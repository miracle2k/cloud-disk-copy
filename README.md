Idea: Help migrate data from disks between clouds (AWS, Google).

How it works:

- Specify two disks.
- Alternatively, reference Kubernetes volumes.
- The script spins up to machines in the source and target clouds.
- The source disk may be shared, the target disk needs to be exclusively given to target VM.
- Kubernetes pods depending on the disks must be scaled down temporarily.
- Rsync the data between them.
- Finally, automatically scale up the pods we scaled down.


VERY MUCH A WORK IN PROGRESS.

Alternative: have a service running on both clusters, supporting mounting disk and syncing arbitary sets of disks.

To run on my machine, I currently hae to do:

- pyenv shell 2.7.14 3.6.3
- python3.6 cli.py


Process: Sync one Kubernetes Persistent Volume to another
=========================================================

Single Command:

$ python3 cli.py sync --kubectl-context-source gce --kubectl-context-target k5 --deployments-source NS/FOO --deployments-target NS/BAR --kubernetes-pv-target pvc-e102b24f-27e3-11e8-909c-069be05e237c --cloud-source google --identifier-source FOO --region-source europe-west1 --keypair-target foobar --region-source europe-west1

Or:

$ python3 cli.py sync --kubectl-context-source gce --kubectl-context-target k5 --deployments-source NS/FOO --deployments-target NS/BAR --kubernetes-pv-target pvc-e12899e9-26c0-11e8-909c-069be05e237c --kubernetes-pv-source pvc-77403b9f-d5e1-11e6-b958-42010af000bd --region-source europe-west1 --keypair-target foobar


Individual Steps:

1. Pick your your volume:

	$ kubectl --context source get pv

2. Scale down the deployments that use this volume (or otherwise make sure no pod is using it):

	$ kubectl --context source-cluster scale --replicas=0 deployment/foobar
	$ kubectl --context target scale --replicas=0 deployment/foobar

3. Mount both disks:

	$ ./cli.py mount-disk --kubectl-context=source-cluster --kubernetes-pv $PV
	$ ./cli.py mount-disk --kubectl-context=target-cluster --kubernetes-pv $PV

4. Sync:

    $ ssh-agent
    $ ssh-add
    $ ssh -A ubuntu@TARGETIP  'sudo -E rsync -e "ssh -o StrictHostKeyChecking=no" --exclude="/lost+found" -avz root@SOURCEIP:/mnt/ /mnt'

5. Terminate the instances:

	$ python3 cli.py terminate-vm --cloud google --vm ....
	$ python3 cli.py terminate-vm --cloud aws --vm ....

6. Scale backup the deployments:

	$ kubectl --context source-cluster scale --replicas=1 deployment/foobar
	$ kubectl --context target scale --replicas=1 deployment/foobar


TODO
====

Automatically scale down the deployments that use the disks in question
-----------------------------------------------------------------------

- kubectl get pods --all-namespaces --output json
- Find the claim name
- Find the deployment matching the pod
- Scale that deployment
