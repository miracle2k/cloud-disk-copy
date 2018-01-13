# coding: utf-8
import asyncio
import json
from functools import update_wrapper
from .asyncsh import sh
from attrdict import AttrDict
import click


class ResourceCollector:
    """Any resource created is added here, so we can shut them down again..."""
    def __init__(self):
        self.list = []

    def add(self, type, identifer):
        self.list.append({'type': type, 'identifer': identifer})

    def prepare(self, type):
        new = {'type': type}
        self.list.append(new)
        return new

    def complete(self, resource, **data):
        for key, value in data.items():
            resource[key] = value


class Cloud():
    def __init__(self, resources):
        self.resources = resources


class AWS(Cloud):
    async def spin_up_for_disk(self, volume):       
        # Create the instance:
        #
        # Which image?
        # $ aws ec2 describe-images  --region eu-west-2 --owners self amazon --filters "Name=root-device-type,Values=ebs"
        # http://cloud-images.ubuntu.com/locator/ec2/
        instance_resource = self.resources.prepare('instance')
        instance = json.loads(await sh([
            'aws',
            'ec2',
            'run-instances',
            '--image-id', 'ami-fcc4db98',
            '--count', '1',
            '--instance-type', 't2.micro',
            '--region', volume.region, 
            '--key-name', volume.keypair,
            '--output', 'json'
        ]))     
        instance_id = instance['Instances'][0]['InstanceId']
        self.resources.complete(instance_resource, identifier=instance_id)
        

        # Wait for the instance to be up.
        await sh([
            'aws',
            'ec2',
            'wait',
            'instance-status-ok',
            '--region', volume.region,
            '--instance-ids', instance_id
        ])

        # Read data again, now we can get more info
        instance = json.loads(await sh([
            'aws',
            'ec2',
            'describe-instances',
            '--region', volume.region,
            '--instance-ids', instance_id            
        ]))    
        ip = instance['Reservations'][0]['Instances'][0]['PublicIpAddress'] 

        # Attach the target volume
        # This should not be necessary. The volume should be unattached if not in use by any pod.
        # aws ec2 detach-volume --volume-id vol-06e4910397b62d79b --region "eu-west-2"
        await sh([
            'aws', 'ec2', 'attach-volume',
            '--volume-id', volume.id,
            '--instance-id', instance_id,
            '--region', volume.region, 
            '--device', '/dev/sdf'
        ])
        
        # Mount the disk
        # sudo mount /dev/xvdf /mnt/

    async def terminate_vm(self, vm_id, region):
        await sh([
            'aws', 
            'ec2', 
            'terminate-instances', 
            '--region', region, 
            '--instance-ids', vm_id,
        ])



class GoogleCloud(Cloud):

    async def spin_up_for_disk(self, volume):
        # Create an instance
        vmname = f'syncvm-for-{volume.identifier}'
        await sh([
            'gcloud', 'compute', 'instances', 'create', 
            vmname,
            '--image-family', 'ubuntu-1404-lts', 
            '--image-project', 'ubuntu-os-cloud'
        ])

        # Wait - how?

        # Get IP
        await sh([
            'gcloud',
            '--format', 'value(networkInterfaces[0].accessConfigs[0].natIP)',
            'compute', 'instances', 'list',
            '--filter', f'name={vmname}'
        ])

        # Attach the disk
        await sh([
            'gcloud', 'compute', 'instances', 'attach-disk', vmname, 
            '--disk', volume.identifer
        ])

        # sudo mount /dev/disk/by-id/google-persistent-disk-1 /mnt
        # ssh -A ubuntu@35.176.199.160 # 'sudo -E rsync --exclude="/lost+found" -avz michael@35.195.39.175:/mnt/ /mnt'

    async def terminate_vm(self, vm_id):
        await sh([
            'gcloud', 
            'compute', 
            'instances', 
            'stop', 
            vm_id
        ])


def get_impl(cloud_id: str, resources: ResourceCollector):
    klass = {
        'google': GoogleCloud,
        'aws': AWS
    }[cloud_id]
    return klass(resources)


class Volume(AttrDict):
    pass


def coro(f):
    f = asyncio.coroutine(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(f(*args, **kwargs))
    return update_wrapper(wrapper, f)


@click.group()
@click.option('--debug/--no-debug', default=False)
def cli(debug):
    click.echo('Debug mode is %s' % ('on' if debug else 'off'))


@cli.command('mount-disk')
@click.option('--cloud', help='Which cloud?', required=True)
@click.option('--identifier', help='Disk identifier on the cloud', required=True)
@click.option('--region', help='Region the disk is in', required=False)
@click.option('--keypair', help='Keypair to use for the new VM (AWS)', required=False)
@coro
async def mount_disk(cloud, identifier, region=None, keypair=None):
    """Start a VM and mount a disk.
    """
    disk = Volume(
        cloud=cloud,
        identifer=identifier,
        region=region,
        keypair=keypair
    )

    resources = ResourceCollector()
    await get_impl(disk.cloud, resources).spin_up_for_disk(disk)



def main():
    """
    ./copy --to-cloud aws --to-id 34343 --from-cloud google --from-id 
    """

    sourcedisk = Volume(
        cloud='google',
        identifer='gke-production-c46cb51-pvc-9302f1b6-af58-11e6-8e24-42010af00148'
    )

    targetdisk = Volume(
        cloud='aws',
        region='eu-west-2',
        identifier='vol-06e4910397b62d79b',
        keypair='dolores',
    )

    resources = ResourceCollector()
    try:
        get_impl(sourcedisk.cloud, resources).spin_up_for_disk(sourcedisk)
        get_impl(targetdisk.cloud, resources).spin_up_for_disk(sourcedisk)
    finally:
        if resources:
            shutdown(resources)
