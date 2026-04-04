"""
Base executor actor class.

Provides common functionality for all executor actors using xoscar framework.
"""

import time
import traceback
from typing import Any

import xoscar as xo


class BaseExecutorActor(xo.Actor):  # type: ignore[misc]
    """Base executor actor.

    Provides common execution functionality for Python, JavaScript, and command executors.
    All executors inherit from this class to get consistent error handling and result formatting.
    """

    async def __post_create__(self) -> None:
        """Called after actor creation.

        Can be overridden by subclasses for initialization.
        """
        pass

    async def __pre_destroy__(self) -> None:
        """Called before actor destruction.

        Can be overridden by subclasses for cleanup.
        """
        pass

    def _execute_with_tracking(self, func: Any, *args: Any, **kwargs: Any) -> dict:
        """Execute function with time tracking.

        Args:
            func: Function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Execution result dictionary
        """
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            execution_time = time.time() - start_time

            return {
                "success": True,
                "output": result.get("output", ""),
                "error": result.get("error", ""),
                "return_code": result.get("return_code", 0),
                "metadata": result.get("metadata", {}),
                "execution_time": execution_time,
            }
        except Exception as e:
            execution_time = time.time() - start_time
            error_message = f"{type(e).__name__}: {str(e)}"
            error_traceback = traceback.format_exc()

            return {
                "success": False,
                "output": "",
                "error": error_message,
                "return_code": -1,
                "metadata": {"traceback": error_traceback},
                "execution_time": execution_time,
            }
