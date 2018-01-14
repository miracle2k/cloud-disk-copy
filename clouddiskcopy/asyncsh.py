import asyncio


async def sh(command, verbose=False):
    """Run command in subprocess (shell)
    
    Note:
        This can be used if you wish to execute e.g. "copy"
        on Windows, which can only be executed in the shell.
    """

    # Create subprocess
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE)

    # Status
    if verbose:
        print('Started:', command, '(pid = ' + str(process.pid) + ')')

    # Wait for the subprocess to finish
    stdout, stderr = await process.communicate()

    # Result
    result = stdout.decode().strip()

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
