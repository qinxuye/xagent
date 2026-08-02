from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...config import (
    get_native_browser_app_name,
    get_native_browser_enabled,
)
from ...core.computer.native_browser import (
    LOCAL_BROWSER_TASK_EXTENSION,
    NativeBrowserEnvironment,
)
from ...core.task_runtime import (
    TaskRuntimeClientError,
    TaskRuntimeContext,
    TaskRuntimeContribution,
)
from ...core.tools.adapters.vibe.computer import ComputerTool
from ..models.task import Task
from ..models.user import User
from .task_runtime import (
    register_task_extension,
    registered_task_extensions,
    task_extension_bindings_from_agent_config,
    unregister_task_extension,
)


class LocalBrowserTaskRuntimeProvider:
    """Bind one task to a browser window on the Xagent backend host."""

    def on_task_created(
        self,
        context: TaskRuntimeContext,
        configuration: Mapping[str, Any],
    ) -> None:
        if configuration:
            raise TaskRuntimeClientError(
                "Local browser does not accept per-task configuration."
            )
        if not get_native_browser_enabled():
            raise TaskRuntimeClientError(
                "Local browser is disabled on this Xagent host.",
                status_code=403,
            )
        if not _task_owner_is_admin(context):
            raise TaskRuntimeClientError(
                "Local browser is restricted to Xagent administrators because "
                "it controls a browser on the backend host.",
                status_code=403,
            )

    def build_runtime(
        self,
        context: TaskRuntimeContext,
    ) -> TaskRuntimeContribution | None:
        if not _task_is_bound(context):
            return None
        if context.workspace is None:
            raise RuntimeError("Local browser requires a task workspace")

        application = get_native_browser_app_name()
        tool = ComputerTool(
            task_id=str(context.task_id),
            workspace=context.workspace,
            environment_factory=NativeBrowserEnvironment,
            headless=False,
            environment_instructions=(
                f"This task controls one visible {application} window on the "
                "same host as Xagent through cua-driver. It uses that browser's "
                "existing profile and signed-in sessions. The first screenshot "
                "locks the task to one concrete window; never switch to another "
                "window if it closes. Actions use background delivery unless the "
                "current observation explicitly recommends foreground delivery. "
                "Never ask the user to reveal credentials."
            ),
        )
        return TaskRuntimeContribution(
            tools=(tool,),
            environment=(
                f"Local browser is enabled for this task. Operate the visible "
                f"{application} window with the computer tool. This is the "
                "backend host browser, not a Chrome extension or remote relay."
            ),
            preferred_input_modalities=("image",),
        )

    def public_metadata(
        self,
        context: TaskRuntimeContext,
    ) -> Mapping[str, Any] | None:
        if not _task_is_bound(context):
            return None
        return {
            "kind": "local_browser",
            "application": get_native_browser_app_name(),
            "enabled": get_native_browser_enabled(),
        }

    def on_task_deleted(self, context: TaskRuntimeContext) -> None:
        # The task-scoped ComputerTool owns cua-driver and closes it through
        # normal AgentService teardown. The binding itself has no durable state.
        del context


def register_local_browser_runtime() -> None:
    """Register the built-in provider once for this web process."""

    if LOCAL_BROWSER_TASK_EXTENSION not in registered_task_extensions():
        register_task_extension(
            LOCAL_BROWSER_TASK_EXTENSION,
            LocalBrowserTaskRuntimeProvider(),
        )


def unregister_local_browser_runtime() -> None:
    """Remove the built-in provider at the end of the web-app lifespan."""

    unregister_task_extension(LOCAL_BROWSER_TASK_EXTENSION)


def _task_is_bound(context: TaskRuntimeContext) -> bool:
    session = context.session_factory()
    try:
        task = session.query(Task).filter(Task.id == context.task_id).first()
        if task is None or int(task.user_id) != context.user_id:
            return False
        return (
            LOCAL_BROWSER_TASK_EXTENSION
            in task_extension_bindings_from_agent_config(task.agent_config)
        )
    finally:
        session.close()


def _task_owner_is_admin(context: TaskRuntimeContext) -> bool:
    session = context.session_factory()
    try:
        user = session.query(User).filter(User.id == context.user_id).first()
        return bool(user is not None and user.is_admin)
    finally:
        session.close()
