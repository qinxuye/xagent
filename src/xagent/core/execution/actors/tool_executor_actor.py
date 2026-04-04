"""
Generic tool executor actor.

Executes any tool in isolated process by receiving the tool instance
and calling its run_json_async method. xoscar handles serialization automatically.
"""

import asyncio
from typing import Any

from .base_executor_actor import BaseExecutorActor


class ToolExecutorActor(BaseExecutorActor):
    """Generic tool executor actor.

    Executes any AbstractBaseTool in isolated process.
    xoscar automatically serializes the tool instance and arguments.
    """

    async def execute(
        self,
        tool: Any,  # AbstractBaseTool instance - xoscar serializes it
        args: dict,  # Tool arguments - xoscar serializes it
        timeout: int = 300,
    ) -> dict:
        """Execute tool in isolated process.

        Args:
            tool: Tool instance (serialized and sent by xoscar)
            args: Tool arguments (serialized and sent by xoscar)
            timeout: Execution timeout in seconds

        Returns:
            Execution result dictionary
        """

        def _execute() -> dict:
            """Internal execution function."""
            try:
                # Always try sync method first, then async if sync fails
                try:
                    if hasattr(tool, "run_json_sync"):
                        result = tool.run_json_sync(args)
                    else:
                        # Fall back to async method
                        result = asyncio.run(tool.run_json_async(args))
                except AttributeError:
                    # No sync method, use async
                    result = asyncio.run(tool.run_json_async(args))

                return {
                    "output": result,
                    "error": "",
                    "return_code": 0,
                    "metadata": {},
                }

            except Exception as e:
                import traceback

                error_message = f"{type(e).__name__}: {str(e)}"
                error_traceback = traceback.format_exc()
                return {
                    "output": None,
                    "error": f"{error_message}\n{error_traceback}",
                    "return_code": 1,
                    "metadata": {"exception_type": type(e).__name__},
                }

        return self._execute_with_tracking(_execute)

        return self._execute_with_tracking(_execute)
