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


Process: Sync one Kubernetes Persistent Volume to another
=========================================================

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
    $ ssh -A ubuntu@35.177.57.84  'sudo -E rsync -e "ssh -o StrictHostKeyChecking=no" --exclude="/lost+found" -avz michael@35.195.204.200:/mnt/ /mnt'

5. Terminate the instances:

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