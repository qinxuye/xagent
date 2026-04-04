"""
Process-isolated tool wrapper.

Execute tool's run_json_sync/async methods in isolated processes using xoscar.
xoscar automatically handles serialization - no need for manual pickle/json encoding.
"""

import asyncio
import logging
from typing import Any, Mapping

from pydantic import BaseModel

from .....execution.service.manager import get_process_service
from ..base import AbstractBaseTool, ToolMetadata

logger = logging.getLogger(__name__)


class ProcessIsolatedToolWrapper(AbstractBaseTool):
    """Process-isolated tool wrapper.

    Wrap any AbstractBaseTool to execute in isolated processes using xoscar.
    xoscar automatically serializes the tool instance and arguments.
    """

    def __init__(
        self,
        target_tool: AbstractBaseTool,
        timeout: int = 300,
    ):
        """Initialize process-isolated tool wrapper.

        Args:
            target_tool: Target tool to wrap
            timeout: Execution timeout in seconds (default: 300)
        """
        self._target = target_tool
        self._timeout = timeout

        # Proxy target tool attributes
        self._visibility = getattr(target_tool, "_visibility", None)
        self._allow_users = getattr(target_tool, "_allow_users", None)

    @property
    def is_isolated(self) -> bool:
        """Marker for process-isolated."""
        return True

    @property
    def name(self) -> str:
        return self._target.name

    @property
    def description(self) -> str:
        return self._target.description

    @property
    def tags(self) -> list[str]:
        return self._target.tags

    @property
    def metadata(self) -> ToolMetadata:
        return self._target.metadata

    def args_type(self) -> type[BaseModel]:
        return self._target.args_type()

    def return_type(self) -> type[BaseModel]:
        return self._target.return_type()

    def state_type(self) -> type[BaseModel] | None:
        return self._target.state_type()

    def run_json_sync(self, args: Mapping[str, Any]) -> Any:
        """Synchronous execution (calls async version via asyncio.run)"""
        return asyncio.run(self.run_json_async(args))

    async def run_json_async(self, args: Mapping[str, Any]) -> Any:
        """Execute tool asynchronously in isolated process.

        Args:
            args: Tool arguments

        Returns:
            Tool execution result
        """
        process_service = get_process_service()
        if not process_service:
            # ProcessService not available, fall back to direct execution
            logger.warning(
                f"ProcessService not available for {self._target.name}, "
                "falling back to direct execution"
            )
            return await self._target.run_json_async(args)

        try:
            # xoscar automatically serializes tool and args
            result = await process_service.execute_tool(
                tool=self._target,  # Tool instance - xoscar serializes it
                args=dict(args),  # Arguments - xoscar serializes them
                timeout=self._timeout,
            )

            if not result.success:
                raise RuntimeError(
                    f"Process-isolated execution failed for {self._target.name}: "
                    f"{result.error}"
                )

            return result.output

        except Exception as e:
            logger.error(
                f"Error executing tool {self._target.name} in isolated process: {e}",
                exc_info=True,
            )
            raise


def create_process_isolated_tool(
    tool: AbstractBaseTool,
    timeout: int = 300,
) -> ProcessIsolatedToolWrapper:
    """Create process-isolated tool instance.

    Args:
        tool: Tool to wrap
        timeout: Execution timeout in seconds

    Returns:
        Process-isolated tool wrapper
    """
    wrapper = ProcessIsolatedToolWrapper(
        target_tool=tool,
        timeout=timeout,
    )

    return wrapper
