"""
Process service using xoscar with sub-pool creation.

Creates a main pool at startup, then creates sub-pools (with one worker) for
each execution request using append_sub_pool, destroys them after completion.
Sub-pools are created on-demand and destroyed when done.
"""

import asyncio
import logging
from typing import Any, Optional

import xoscar as xo

from .base import (
    BaseService,
    ExecutionResult,
    ServiceInfo,
    ServiceStatus,
)

logger = logging.getLogger(__name__)


class ProcessService(BaseService):
    """Process service using xoscar with sub-pool creation.

    Creates a main pool at startup.
    For each execution request, appends a sub-pool with one worker.
    After execution, the sub-pool is killed.
    """

    def __init__(self, address: str = "localhost:12345"):
        super().__init__()
        self._address = address
        self._lock = asyncio.Lock()
        self._active_actors: dict[str, Any] = {}

    @property
    def service_name(self) -> str:
        return "process"

    async def start(self) -> None:
        """Start process service."""
        self._status = ServiceStatus.STARTING
        try:
            # Initialize xoscar router
            from xoscar.backends import router as xo_router

            default_router = xo_router.Router.get_instance_or_empty()
            xo_router.Router.set_instance(default_router)
            logger.info("xoscar router initialized")

            # Create main pool that will hold sub-pools
            self._pool = await xo.create_actor_pool(
                address=self._address,
                n_process=0,  # Main pool doesn't need workers, only sub-pools
            )

            self._status = ServiceStatus.RUNNING
            logger.info(
                f"ProcessService started successfully with main pool at {self._address}"
            )
        except Exception as e:
            self._status = ServiceStatus.ERROR
            logger.error(f"Failed to start ProcessService: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """Stop process service."""
        self._status = ServiceStatus.STOPPING
        try:
            logger.info("Stopping ProcessService")

            # Stop main pool (this will also stop all sub-pools)
            if self._pool:
                await self._pool.stop()
                logger.info("Main pool stopped")

            self._status = ServiceStatus.STOPPED
            logger.info("ProcessService stopped successfully")
        except Exception as e:
            self._status = ServiceStatus.ERROR
            logger.error(f"Failed to stop ProcessService: {e}", exc_info=True)
            raise

    async def health_check(self) -> bool:
        """Health check."""
        return self._status == ServiceStatus.RUNNING

    def get_info(self) -> ServiceInfo:
        """Get service information."""
        return ServiceInfo(
            name=self.service_name,
            status=self._status,
            resource_info={
                "type": "dynamic",
                "address": self._address,
                "active_actors": len(self._active_actors),
            },
            metrics={},
        )

    async def execute_python(
        self,
        code: str,
        workspace: Optional[str] = None,
        timeout: int = 300,
    ) -> ExecutionResult:
        """Execute Python code in a dynamic actor.

        Creates a sub-pool, creates an actor, executes the code,
        then destroys both the actor and sub-pool.
        """
        import uuid

        task_id = f"python_{uuid.uuid4().hex[:8]}"
        sub_pool_address = None
        actor_ref = None

        try:
            # Append a sub-pool for this execution
            # Let xoscar auto-assign port and address
            sub_pool_address = await self._pool.append_sub_pool(
                label=task_id,
            )

            logger.debug(f"Appended sub-pool {sub_pool_address} for Python execution")

            # Create a temporary actor for this execution
            from ..actors.python_executor_actor import PythonExecutorActor

            actor_ref = await xo.create_actor(
                PythonExecutorActor,
                address=sub_pool_address,
            )

            # Track actor
            async with self._lock:
                self._active_actors[task_id] = actor_ref

            logger.debug(f"Created actor {task_id} for Python execution")

            # Get actor ref and call execute
            actor = await xo.actor_ref(actor_ref)

            try:
                result_dict = await asyncio.wait_for(
                    actor.execute(code=code, workspace=workspace, timeout=timeout),
                    timeout=timeout,
                )

                return ExecutionResult.from_dict(result_dict)

            finally:
                # Clean up actor
                try:
                    await xo.destroy_actor(actor_ref)
                    logger.debug(f"Destroyed actor {task_id}")
                except Exception as e:
                    logger.error(f"Failed to destroy actor {task_id}: {e}")
                finally:
                    async with self._lock:
                        self._active_actors.pop(task_id, None)

                # Clean up sub-pool
                if sub_pool_address:
                    try:
                        await self._pool.kill_sub_pool(sub_pool_address)
                        logger.debug(f"Killed sub-pool {sub_pool_address}")
                    except Exception as e:
                        logger.error(f"Failed to kill sub-pool {sub_pool_address}: {e}")

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Execution timed out after {timeout} seconds",
                return_code=-1,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Execution failed: {str(e)}",
                return_code=-1,
            )

    async def execute_tool(
        self,
        tool: Any,
        args: dict,
        timeout: int = 300,
    ) -> ExecutionResult:
        """Execute any tool in a dynamic actor.

        Creates a sub-pool, creates an actor, executes the tool,
        then destroys both the actor and sub-pool.
        """
        import uuid

        task_id = f"tool_{uuid.uuid4().hex[:8]}"
        sub_pool_address = None
        actor_ref = None

        try:
            # Append a sub-pool for this execution
            # Let xoscar auto-assign port and address
            sub_pool_address = await self._pool.append_sub_pool(
                label=task_id,
            )

            logger.debug(f"Appended sub-pool {sub_pool_address} for tool execution")

            # Create a generic tool executor actor
            from ..actors.tool_executor_actor import ToolExecutorActor

            actor_ref = await xo.create_actor(
                ToolExecutorActor,
                address=sub_pool_address,
            )

            # Track actor
            async with self._lock:
                self._active_actors[task_id] = actor_ref

            logger.debug(f"Created actor {task_id} for tool execution")

            # Get actor ref and call execute
            actor = await xo.actor_ref(actor_ref)

            try:
                result_dict = await asyncio.wait_for(
                    actor.execute(tool=tool, args=args, timeout=timeout),
                    timeout=timeout,
                )

                return ExecutionResult.from_dict(result_dict)

            finally:
                # Clean up actor
                try:
                    await xo.destroy_actor(actor_ref)
                    logger.debug(f"Destroyed actor {task_id}")
                except Exception as e:
                    logger.error(f"Failed to destroy actor {task_id}: {e}")
                finally:
                    async with self._lock:
                        self._active_actors.pop(task_id, None)

                # Clean up sub-pool
                if sub_pool_address:
                    try:
                        await self._pool.kill_sub_pool(sub_pool_address)
                        logger.debug(f"Killed sub-pool {sub_pool_address}")
                    except Exception as e:
                        logger.error(f"Failed to kill sub-pool {sub_pool_address}: {e}")

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Execution timed out after {timeout} seconds",
                return_code=-1,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                output="",
                error=f"Execution failed: {str(e)}",
                return_code=-1,
            )
