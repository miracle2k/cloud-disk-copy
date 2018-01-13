Idea: Help migrate data from disks between clouds (AWS, Google).

How it works:

- Specify two disks.
- Alternatively, reference Kubernetes volumes.
- The script spins up to machines in the source and target clouds.
- The source disk may be shared, the target disk needs to be exclusively given to target VM.
- Kubernetes pods depending on the disks may be scaled down temporarily.
- Rsync the data between them.
- Finally, automatically scale up the pods we scaled down.


VERY MUCH A WORK IN PROGRESS.

Alternative: have a service running on both clusters, supporting mounting disk and syncing arbitary sets of disks.