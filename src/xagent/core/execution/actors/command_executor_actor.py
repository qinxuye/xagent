"""Shell command executor actor."""

import asyncio
import shlex
from typing import Optional

from .base_executor_actor import BaseExecutorActor


class CommandExecutorActor(BaseExecutorActor):
    """Execute shell commands in an isolated process."""

    async def execute(
        self,
        command: str,
        workspace: Optional[str] = None,
        timeout: int = 300,
    ) -> dict:
        async def _execute() -> dict:
            argv = shlex.split(command)
            if not argv:
                return {
                    "output": "",
                    "error": "Command cannot be empty",
                    "return_code": 1,
                }

            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return {
                    "output": "",
                    "error": f"Execution timed out after {timeout} seconds",
                    "return_code": -1,
                }

            return {
                "output": stdout.decode(errors="replace"),
                "error": stderr.decode(errors="replace"),
                "return_code": process.returncode or 0,
                "metadata": {},
            }

        return await self._execute_async_with_tracking(_execute)
