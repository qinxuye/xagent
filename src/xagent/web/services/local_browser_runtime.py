from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Any

from ...config import get_local_computer_enabled
from ...core.computer.native_browser import (
    LEGACY_LOCAL_BROWSER_TASK_EXTENSION,
    LOCAL_COMPUTER_TASK_EXTENSION,
    NativeComputerEnvironment,
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

_TARGET_AGENT_CONFIG_KEY = "local_computer_target"


class LocalComputerTaskRuntimeProvider:
    """Bind one task to an application window on the Xagent backend host."""

    def __init__(self, extension_name: str = LOCAL_COMPUTER_TASK_EXTENSION) -> None:
        self.extension_name = extension_name

    def on_task_created(
        self,
        context: TaskRuntimeContext,
        configuration: Mapping[str, Any],
    ) -> None:
        target = _validate_target_configuration(configuration)
        if not get_local_computer_enabled():
            raise TaskRuntimeClientError(
                "Local computer is disabled on this Xagent host.",
                status_code=403,
            )
        if not _task_owner_is_admin(context):
            raise TaskRuntimeClientError(
                "Local computer is restricted to Xagent administrators because "
                "it controls applications on the backend host.",
                status_code=403,
            )
        if target is not None:
            _store_task_target(context, target)

    def build_runtime(
        self,
        context: TaskRuntimeContext,
    ) -> TaskRuntimeContribution | None:
        if not _task_is_bound(context, self.extension_name):
            return None
        if context.workspace is None:
            raise RuntimeError("Local computer requires a task workspace")

        target = _task_target(context)
        environment_factory = partial(
            NativeComputerEnvironment,
            target_pid=target.get("pid") if target else None,
            target_window_id=target.get("window_id") if target else None,
        )
        tool = ComputerTool(
            task_id=str(context.task_id),
            workspace=context.workspace,
            environment_factory=environment_factory,
            environment_label="the selected local application window",
            headless=False,
            environment_instructions=(
                "This task controls one application window on the same host as "
                "Xagent through cua-driver. The selected window may be a browser "
                "or any other supported desktop application and may contain the "
                "user's existing signed-in state. The first screenshot locks the "
                "task to that exact window; never switch windows silently. "
                "Browser-only navigation belongs to the Browser runtime. Actions "
                "use background delivery unless the observation explicitly "
                "recommends foreground delivery. Never ask for credentials."
            ),
        )
        return TaskRuntimeContribution(
            tools=(tool,),
            environment=(
                "Local computer is enabled for this task. Operate the selected "
                "application window with the computer tool. This is the Xagent "
                "backend host, not a browser extension or remote relay."
            ),
            preferred_input_modalities=("image",),
        )

    def public_metadata(
        self,
        context: TaskRuntimeContext,
    ) -> Mapping[str, Any] | None:
        if not _task_is_bound(context, self.extension_name):
            return None
        target = _task_target(context)
        metadata: dict[str, Any] = {
            "kind": "local_computer",
            "enabled": get_local_computer_enabled(),
        }
        if target:
            metadata["target"] = target
        return metadata

    def on_task_deleted(self, context: TaskRuntimeContext) -> None:
        # The task-scoped ComputerTool owns cua-driver and closes it through
        # normal AgentService teardown. The binding itself has no durable state.
        del context


def register_local_computer_runtime() -> None:
    """Register the built-in provider once for this web process."""

    if LOCAL_COMPUTER_TASK_EXTENSION not in registered_task_extensions():
        register_task_extension(
            LOCAL_COMPUTER_TASK_EXTENSION,
            LocalComputerTaskRuntimeProvider(),
        )
    if LEGACY_LOCAL_BROWSER_TASK_EXTENSION not in registered_task_extensions():
        register_task_extension(
            LEGACY_LOCAL_BROWSER_TASK_EXTENSION,
            LocalComputerTaskRuntimeProvider(LEGACY_LOCAL_BROWSER_TASK_EXTENSION),
        )


def unregister_local_computer_runtime() -> None:
    """Remove the built-in provider at the end of the web-app lifespan."""

    unregister_task_extension(LOCAL_COMPUTER_TASK_EXTENSION)
    unregister_task_extension(LEGACY_LOCAL_BROWSER_TASK_EXTENSION)


def _task_is_bound(context: TaskRuntimeContext, extension_name: str) -> bool:
    session = context.session_factory()
    try:
        task = session.query(Task).filter(Task.id == context.task_id).first()
        if task is None or int(task.user_id) != context.user_id:
            return False
        return extension_name in task_extension_bindings_from_agent_config(
            task.agent_config
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


def _validate_target_configuration(
    configuration: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not configuration:
        return None
    allowed = {"pid", "window_id", "application", "title"}
    unexpected = set(configuration) - allowed
    if unexpected:
        raise TaskRuntimeClientError(
            "Local computer configuration contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    try:
        pid = int(configuration["pid"])
        window_id = int(configuration["window_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskRuntimeClientError(
            "Local computer requires integer pid and window_id values."
        ) from exc
    if pid <= 0 or window_id <= 0:
        raise TaskRuntimeClientError(
            "Local computer pid and window_id must be positive."
        )
    target: dict[str, Any] = {"pid": pid, "window_id": window_id}
    for key in ("application", "title"):
        value = str(configuration.get(key) or "").strip()
        if value:
            target[key] = value[:512]
    return target


def _store_task_target(
    context: TaskRuntimeContext,
    target: Mapping[str, Any],
) -> None:
    session = context.session_factory()
    try:
        task = session.query(Task).filter(Task.id == context.task_id).first()
        if task is None or int(task.user_id) != context.user_id:
            raise TaskRuntimeClientError("Local computer task was not found.")
        agent_config = dict(task.agent_config or {})
        agent_config[_TARGET_AGENT_CONFIG_KEY] = dict(target)
        task.agent_config = agent_config
        session.commit()
    finally:
        session.close()


def _task_target(context: TaskRuntimeContext) -> dict[str, Any] | None:
    session = context.session_factory()
    try:
        task = session.query(Task).filter(Task.id == context.task_id).first()
        if task is None or int(task.user_id) != context.user_id:
            return None
        raw = (task.agent_config or {}).get(_TARGET_AGENT_CONFIG_KEY)
        return dict(raw) if isinstance(raw, Mapping) else None
    finally:
        session.close()


# Preview-name compatibility for imports and app lifespan wiring.
LocalBrowserTaskRuntimeProvider = LocalComputerTaskRuntimeProvider
register_local_browser_runtime = register_local_computer_runtime
unregister_local_browser_runtime = unregister_local_computer_runtime
