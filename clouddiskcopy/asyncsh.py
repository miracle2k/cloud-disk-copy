import asyncio


async def sh(command, verbose=True, capture=True):
    """Run command in subprocess (shell)
    
    Note:
        This can be used if you wish to execute e.g. "copy"
        on Windows, which can only be executed in the shell.
    """

    # Create subprocess
    kwargs = {}
    if capture:
        kwargs['stdout'] = asyncio.subprocess.PIPE
    process = await asyncio.create_subprocess_exec(
        *command,
        **kwargs)

    # Status
    if verbose:
        print('Started:', command, '(pid = ' + str(process.pid) + ')')

    # Wait for the subprocess to finish
    stdout, stderr = await process.communicate()

    # Result
    if capture:
        result = stdout.decode().strip()
    else:
        result = None

    # Progress
    if process.returncode == 0:
        if verbose:
            print('Done:', command, '(pid = ' + str(process.pid) + ')')
    else:
        if verbose:
            print('Failed:', command, '(pid = ' + str(process.pid) + ')')
        raise ValueError('Failed', result)

    # Return stdout
    return result
