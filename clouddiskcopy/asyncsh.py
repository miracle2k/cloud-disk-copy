import asyncio


async def sh(command):
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
    print('Started:', command, '(pid = ' + str(process.pid) + ')')

    # Wait for the subprocess to finish
    stdout, stderr = await process.communicate()

    # Progress
    if process.returncode == 0:
        print('Done:', command, '(pid = ' + str(process.pid) + ')')
    else:
        print('Failed:', command, '(pid = ' + str(process.pid) + ')')

    # Result
    result = stdout.decode().strip()

    # Return stdout
    return result
