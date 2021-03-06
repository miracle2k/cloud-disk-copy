# coding: utf-8
import asyncio
import json
from typing import Dict
from contextlib import contextmanager
from collections import defaultdict
from functools import update_wrapper
from .asyncsh import sh
from .utils import composed
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

    def __iter__(self):
        return iter(self.list)

    def complete(self, resource, **data):
        for key, value in data.items():
            resource[key] = value


async def get_volume_from_kubernetes_disk(kubernetes_pv: str, context: str = None, region: str = None):
    cmd = ['kubectl']
    if context:
        cmd.extend(['--context', context])
    cmd.extend(['get', 'pv', '-o', 'json', kubernetes_pv])
    pv = json.loads(await sh(cmd))
    spec = pv['spec']

    if 'awsElasticBlockStore' in spec:
        parts = spec['awsElasticBlockStore']['volumeID'].split('/')
        pd_name = parts[-1]
        if not region:
            region = parts[-2][:-1] # eu-west-2a to eu-west-2
        cloud = 'aws'

    if 'gcePersistentDisk' in spec:
        pd_name = spec['gcePersistentDisk']['pdName']
        cloud = 'google'

    volume = Volume(
        cloud=cloud,
        identifier=pd_name,
        region=region
    )
    require_volume_complete(volume)
    return volume


class Cloud():
    def __init__(self, resources):
        self.resources = resources


class AWS(Cloud):

    async def spin_up_for_disk(self, volume, read_only=False, opts=None):
        if not opts:
            opts = AttrDict()

        require_value(opts.keypair, "AWS needs a keypair to be specified, you may want to use the name you have for ~/.ssh/id_rsa.pub on AWS")

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
            '--key-name', opts.keypair,
            '--output', 'json'
        ]))     
        instance_id = instance['Instances'][0]['InstanceId']
        self.resources.complete(instance_resource, identifier=instance_id, region=opts.region)
        
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
        await asyncio.sleep(10)

        await sh([
            'ssh', f'ubuntu@{ip}', '-o', 'StrictHostKeyChecking=no',
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


@contextmanager
def action(text):
    print(text)
    print('----------------------------')
    yield
    print()
    print()
    print()
    print()
    """
    if log is not hidden:
        log immediately
        ...done
    """


class GoogleCloud(Cloud):

    async def spin_up_for_disk(self, volume, read_only=False, opts=None):
        # Create an instance
        # Name is max 61 characters
        with action("Create a new instance."):
            instance_resource = self.resources.prepare('instance')
            vmname = f'syncvm-for-{volume.identifier}'[:60]
            await sh([
                'gcloud', 'compute', 'instances', 'create', 
                vmname,
                '--image-family', 'ubuntu-1404-lts', 
                '--image-project', 'ubuntu-os-cloud'
            ])
            self.resources.complete(instance_resource, identifier=vmname)

            # Wait until the instance is running
            # Is this necessary?
            with action("Wait for instance to start."):
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

            with action("Wait for SSH to become available."):
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
        with action("Attach the disk to the instance."):
            cmd = [
                'gcloud', 'compute', 'instances', 'attach-disk', vmname, 
                '--disk', volume.identifier
            ]
            if read_only:
                cmd.extend(['--mode', 'ro'])
            await sh(cmd)

        # Mount the disk
        with action("Mount the volume inside the VM."):
            await sh([
                'gcloud', 'compute', 'ssh', vmname, 
                '--ssh-key-file', '/Users/michael/.ssh/id_rsa', 
                '--', 'sudo', 'mount', '/dev/disk/by-id/google-persistent-disk-1', '/mnt'
            ])  

        return VMInstance(ip=ip, username='')


    async def terminate_vm(self, vm_id, region):
        with action("Terminating %s" % vm_id):
            await sh([
                'gcloud', 
                'compute', 
                'instances', 
                'delete', 
                vm_id
            ])


async def scale_down_deployment(deployment, context=None):    
    cmd = ['kubectl']
    if context:
        cmd.extend(['--context', context])
    cmd.extend(['scale', '--replicas=0', '-o=name'])

    parts = deployment.split('/', 1)
    namespace = None
    if len(parts) > 1:
        namespace, deployment_name = parts
    else:
        deployment_name = parts[0]

    if namespace:
        cmd.extend(['--namespace', namespace])
    cmd.extend(['deployment/%s' % deployment_name])
    await sh(cmd)


def get_impl(cloud_id: str, resources: ResourceCollector, opts: Dict = None):
    try:
        klass = {
            'google': GoogleCloud,
            'aws': AWS
        }[cloud_id]
    except KeyError:
        raise click.UsageError(f'"{cloud_id}" is not a supported cloud provider.')
    return klass(resources, **(opts or {}))


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


def require_volume_complete(disk: Volume):
    require_value(disk.region, 'All disks require a region to be specified. For Google, this needs to be done manually.')    


@click.group()
@click.option('--debug/--no-debug', default=False)
def cli(debug):
    click.echo('Debug mode is %s' % ('on' if debug else 'off'))


async def get_disk_from_cli_arguments(kubernetes_pv, cloud, identifier, region, kubectl_context):
    if kubernetes_pv:
        return await get_volume_from_kubernetes_disk(
            kubernetes_pv, context=kubectl_context, region=region)
    else:
        require_value(cloud, "You need so specify a cloud provider, using --cloud")
        disk = Volume(
            cloud=cloud,
            identifier=identifier,
            region=region
        )
        require_volume_complete(disk)
    return disk


def make_disk_options(suffix=''):
    return composed(
        click.option(f'--cloud{suffix}', help='Which cloud?', required=False),
        click.option(f'--identifier{suffix}', help='Disk identifier on the cloud', required=False),
        click.option(f'--region{suffix}', help='Region the disk is in', required=False),
        click.option(f'--keypair{suffix}', help='Keypair to use for the new VM (AWS)', required=False),
        click.option(f'--kubernetes-pv{suffix}', help='Kubernetes Persistent Volume', required=False),
        click.option(f'--kubectl-context{suffix}', help='Use the kubernetes cluster behind this kubectl context.', required=False),
    )


@cli.command('mount-disk')
@make_disk_options()
@coro
async def mount_disk(cloud=None, identifier=None, region=None, keypair=None, kubernetes_pv=None,
    kubectl_context=None):
    """Start a VM and mount a disk.    
    """
    disk = await get_disk_from_cli_arguments(kubernetes_pv, cloud, identifier, region, kubectl_context)

    resources = ResourceCollector()
    cloud_opts = {
        'keypair': keypair
    }
    vm = await get_impl(disk.cloud, resources).spin_up_for_disk(disk, read_only=True, opts=cloud_opts)

    print(vm.ident())


@cli.command('terminate-vm')
@click.option('--cloud', help='Which cloud?', required=True)
@click.option('--vm', help='VM identifier on the cloud', required=True)
@click.option('--region', help='Region the disk is in', required=False)
@coro
async def terminate_vm(cloud, vm, region=None):
    resources = ResourceCollector()

    cloud_api = get_impl(cloud, resources)
    await cloud_api.terminate_vm(vm, region=region)
    print('Terminated %s' % vm)


async def sync(source_vm: VMInstance, target_vm: VMInstance):
    """Do the sync between two VM instances.
    """

    await sh(['ssh-agent'])
    await sh(['ssh-add'])
    await sh([
        'ssh',
        '-o', 'StrictHostKeyChecking=no',
        '-A',
        'ubuntu@%s' % target_vm.ip,
        'sudo -E rsync -e "ssh -o StrictHostKeyChecking=no" --exclude="/lost+found" -avz root@%s:/mnt/ /mnt' %  source_vm.ip,
    ], capture=False)


@cli.command('sync')
@make_disk_options(suffix='-source')
@make_disk_options(suffix='-target')
@click.option('--deployments-source', help='Source deployments to scale down', multiple=True)
@click.option('--deployments-target', help='Target deployments to scale down', multiple=True)
@coro
async def main(    
    deployments_source,
    kubernetes_pv_source,
    cloud_source,
    region_source,
    keypair_source,
    identifier_source,

    deployments_target,
    kubernetes_pv_target,
    cloud_target,
    region_target,
    keypair_target,
    identifier_target,
    
    kubectl_context_source=None,
    kubectl_context_target=None
):

    with action('Scaling down deployments'):
        for deployment in deployments_source:
            with action('Scaling down source deployment: %s' % deployment):
                await scale_down_deployment(deployment, context=kubectl_context_source)
        for deployment in deployments_target:
            with action('Scaling down target deployment: %s' % deployment):
                await scale_down_deployment(deployment, context=kubectl_context_target)

    resources = defaultdict(lambda: ResourceCollector())
    try:
        disk_source = await get_disk_from_cli_arguments(kubernetes_pv_source, cloud_source, identifier_source, region_source, kubectl_context_source)
        disk_target = await get_disk_from_cli_arguments(kubernetes_pv_target, cloud_target, identifier_target, region_target, kubectl_context_target)        
        
        source_cloud = get_impl(disk_source.cloud, resources[disk_source.cloud])
        source_cloud_opts = AttrDict({
            'keypair': keypair_source
        })
        vm_source = await source_cloud.spin_up_for_disk(disk_source, read_only=True, opts=source_cloud_opts)

        target_cloud_opts = AttrDict({
            'keypair': keypair_target
        })
        target_cloud = get_impl(disk_target.cloud, resources[disk_target.cloud])
        vm_target = await target_cloud.spin_up_for_disk(disk_target, read_only=False, opts=target_cloud_opts)

        with action('Now syncing the files'):
            await sync(vm_source, vm_target)
    finally:
        with action('Terminating all resources'):
            for cloud, collector in resources.items():
                cloud_api = get_impl(cloud, resources[cloud])
                for resource in collector:                    
                    with action('Terminating %s' % resource['identifier']):
                        try:
                            await cloud_api.terminate_vm(resource['identifier'], region=resource.get('region'))
                        except Exception as e:
                            print(e)

    