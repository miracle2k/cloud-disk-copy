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

    def add(self, type, identifier):
        self.list.append({'type': type, 'identifier': identifier})

    def prepare(self, type):
        new = {'type': type}
        self.list.append(new)
        return new

    def complete(self, resource, **data):
        for key, value in data.items():
            resource[key] = value


async def get_volume_from_kubernetes_disk(kubernetes_pv: str, context: str = None):
    cmd = ['kubectl']
    if context:
        cmd.extend(['--context', context])
    cmd.extend(['get', 'pv', '-o', 'json', kubernetes_pv])
    pv = json.loads(await sh(cmd))
    spec = pv['spec']

    if 'awsElasticBlockStore' in spec:
        parts = spec['awsElasticBlockStore']['volumeID'].split('/')
        pd_name = parts[-1]
        region = parts[-2]
        cloud = 'aws'

    if 'gcePersistentDisk' in spec:
        pd_name = spec['gcePersistentDisk']['pdName']
        cloud = 'google'

    return Volume(
        cloud=cloud,
        identifier=pd_name,
    )


class Cloud():
    def __init__(self, resources):
        self.resources = resources


class AWS(Cloud):
    async def spin_up_for_disk(self, volume, read_only=False):       
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
            '--volume-id', volume.identifier,
            '--instance-id', instance_id,
            '--region', volume.region, 
            '--device', '/dev/sdf'
        ])
        
        # Mount the disk
        await sh([
            'ssh', f'ubuntu@{ip}', 
            '--', 'sudo', 'mount', '/dev/xvdf', '/mnt'
        ])

        return VMInstance(ip=ip, username='ubuntu')   

    async def terminate_vm(self, vm_id, region):
        await sh([
            'aws', 
            'ec2', 
            'terminate-instances', 
            '--region', region, 
            '--instance-ids', vm_id,
        ])


def action():
    pass
    """
    if log is not hidden:
        log immediately
        ...done
    """


class GoogleCloud(Cloud):

    async def spin_up_for_disk(self, volume, read_only=False):
        # Create an instance
        # Name is max 61 characters
        with action("Create a new instance."):
            vmname = f'syncvm-for-{volume.identifier}'[:60]
            await sh([
                'gcloud', 'compute', 'instances', 'create', 
                vmname,
                '--image-family', 'ubuntu-1404-lts', 
                '--image-project', 'ubuntu-os-cloud'
            ])

        # Wait until the instance is running
        # Is this necessary?
        print("Wait for instance to start.")
        while True:
            status = await sh([
                'gcloud',
                '--format', 'value(status)',
                'compute', 'instances', 'list',
                '--filter', f'name={vmname}'
            ])
            if status == 'RUNNING':
                break

        # Get IP
        ip = await sh([
            'gcloud',
            '--format', 'value(networkInterfaces[0].accessConfigs[0].natIP)',
            'compute', 'instances', 'list',
            '--filter', f'name={vmname}'
        ])

        print("Wait for SSH to become available.")
        while True:
            try:
                status = await sh([
                    'nc',
                    '-w', '1',
                    '-v', ip, '22'
                ])
            except ValueError:
                await asyncio.sleep(3.0)
            else:
                break

        # Attach the disk
        print("Attach the disk to the instance.")
        cmd = [
            'gcloud', 'compute', 'instances', 'attach-disk', vmname, 
            '--disk', volume.identifier
        ]
        if read_only:
            cmd.extend(['--mode', 'ro'])
        await sh(cmd)

        # Mount the disk
        print("Mount the volume instead the VM.")
        await sh([
            'gcloud', 'compute', 'ssh', vmname, 
            '--ssh-key-file', '/Users/michael/.ssh/id_rsa', 
            '--', 'sudo', 'mount', '/dev/disk/by-id/google-persistent-disk-1', '/mnt'
        ])  

        return VMInstance(ip=ip, username='')


    async def terminate_vm(self, vm_id):
        await sh([
            'gcloud', 
            'compute', 
            'instances', 
            'stop', 
            vm_id
        ])


def get_impl(cloud_id: str, resources: ResourceCollector):
    try:
        klass = {
            'google': GoogleCloud,
            'aws': AWS
        }[cloud_id]
    except KeyError:
        raise click.UsageError(f'"{cloud_id}" is not a supported cloud provider.')
    return klass(resources)


class Volume(AttrDict):
    pass


class VMInstance(AttrDict):

    def ident(self):
        return f'{self.user}@{self.ip}'


def coro(f):
    f = asyncio.coroutine(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(f(*args, **kwargs))
    return update_wrapper(wrapper, f)


def require_value(value: str, error_message: str):
    if not value:
        raise click.UsageError(error_message)


@click.group()
@click.option('--debug/--no-debug', default=False)
def cli(debug):
    click.echo('Debug mode is %s' % ('on' if debug else 'off'))


@cli.command('mount-disk')
@click.option('--cloud', help='Which cloud?', required=False)
@click.option('--identifier', help='Disk identifier on the cloud', required=False)
@click.option('--region', help='Region the disk is in', required=False)
@click.option('--keypair', help='Keypair to use for the new VM (AWS)', required=False)
@click.option('--kubernetes-pv', help='Kubernetes Persistent Volume', required=False)
@click.option('--kubectl-context', help='Use the kubernetes cluster behind this kubectl context.', required=False)
@coro
async def mount_disk(cloud=None, identifier=None, region=None, keypair=None, kubernetes_pv=None,
    kubectl_context=None):
    """Start a VM and mount a disk.    
    """
    if kubernetes_pv:
        disk = await get_volume_from_kubernetes_disk(kubernetes_pv, context=kubectl_context)
        if region:
            disk.region = region
        if keypair:
            disk.keypair = keypair

    else:
        require_value(cloud, "You need so specify a cloud provider, using --cloud")
        disk = Volume(
            cloud=cloud,
            identifier=identifier,
            region=region,
            keypair=keypair
        )

    resources = ResourceCollector()
    vm = await get_impl(disk.cloud, resources).spin_up_for_disk(disk, read_only=True)

    print(vm.ident)


def main():
    """
    # ssh -A ubuntu@35.176.199.160 # 'sudo -E rsync --exclude="/lost+found" -avz michael@35.195.39.175:/mnt/ /mnt'
    """

    sourcedisk = Volume(
        cloud='google',
        identifier='gke-production-c46cb51-pvc-9302f1b6-af58-11e6-8e24-42010af00148'
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
