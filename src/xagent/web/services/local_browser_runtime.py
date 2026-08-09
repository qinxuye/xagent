from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Any

from ...config import get_native_browser_app_name, get_native_browser_enabled
from ...core.computer.native_browser import (
    LOCAL_BROWSER_TASK_EXTENSION,
    NativeBrowserEnvironment,
)
from ...core.computer.schema import ComputerPerceptionMode
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

_TARGET_AGENT_CONFIG_KEY = "local_browser_target"


class LocalBrowserTaskRuntimeProvider:
    """Bind one task to a configured browser window on the backend host."""

    def __init__(self, extension_name: str = LOCAL_BROWSER_TASK_EXTENSION) -> None:
        self.extension_name = extension_name

    def on_task_created(
        self,
        context: TaskRuntimeContext,
        configuration: Mapping[str, Any],
    ) -> None:
        if not get_native_browser_enabled():
            raise TaskRuntimeClientError(
                "Local browser is disabled on this Xagent host.",
                status_code=403,
            )
        if not _task_owner_is_admin(context):
            raise TaskRuntimeClientError(
                "Local browser is restricted to Xagent administrators because "
                "it controls a signed-in browser on the backend host.",
                status_code=403,
            )
        target = _validate_target_configuration(configuration)
        _store_task_target(context, target)

    def build_runtime(
        self,
        context: TaskRuntimeContext,
    ) -> TaskRuntimeContribution | None:
        if not _task_is_bound(context, self.extension_name):
            return None
        if context.workspace is None:
            raise RuntimeError("Local browser requires a task workspace")

        target = _task_target(context)
        if target is None:
            raise RuntimeError("Local browser task has no selected browser window")
        perception_mode = target.get(
            "perception_mode", ComputerPerceptionMode.AUTO.value
        )
        environment_factory = partial(
            NativeBrowserEnvironment,
            target_pid=target["pid"],
            target_window_id=target["window_id"],
            browser_app_name=get_native_browser_app_name(),
            perception_mode=perception_mode,
        )
        tool = ComputerTool(
            task_id=str(context.task_id),
            workspace=context.workspace,
            environment_factory=environment_factory,
            environment_label="the selected local browser window",
            perception_mode=perception_mode,
            headless=False,
            environment_instructions=(
                "This task controls one configured browser window on the same "
                "host as Xagent through cua-driver. The window may contain the "
                "user's existing signed-in state. The first screenshot locks "
                "the task to that exact window; never switch windows silently. "
                "Control is delivered through native OS automation. It never "
                "opens a Chrome debugging connection. "
                "For browser URL changes, use the atomic navigate action when the "
                "observation explicitly lists it as supported. Never simulate "
                "navigation with address-bar clicks, typing, or key presses. "
                "Treat each observation's supported_actions as authoritative. If "
                "an action is unavailable, do not retry or work around that "
                "capability through another tool. "
                "Actions use background delivery unless the observation explicitly "
                "recommends foreground delivery. Never ask for credentials."
            ),
        )
        return TaskRuntimeContribution(
            tools=(tool,),
            environment=(
                "Local browser is enabled for this task. Operate the selected "
                "browser window with the computer tool. This is the Xagent "
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
            "kind": "local_browser",
            "enabled": get_native_browser_enabled(),
            "perception_mode": (
                target.get("perception_mode", ComputerPerceptionMode.AUTO.value)
                if target
                else ComputerPerceptionMode.AUTO.value
            ),
            "control_transport": "native_accessibility",
        }
        if target:
            metadata["target"] = target
        return metadata

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
) -> dict[str, Any]:
    if not configuration:
        raise TaskRuntimeClientError(
            "Local browser requires an explicitly selected browser window."
        )
    allowed = {"pid", "window_id", "application", "title", "perception_mode"}
    unexpected = set(configuration) - allowed
    if unexpected:
        raise TaskRuntimeClientError(
            "Local browser configuration contains unsupported fields: "
            + ", ".join(sorted(unexpected))
        )
    try:
        pid = int(configuration["pid"])
        window_id = int(configuration["window_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TaskRuntimeClientError(
            "Local browser requires integer pid and window_id values."
        ) from exc
    if pid <= 0 or window_id <= 0:
        raise TaskRuntimeClientError(
            "Local browser pid and window_id must be positive."
        )
    application = str(configuration.get("application") or "").strip()
    browser_app_name = get_native_browser_app_name()
    if application.casefold() != browser_app_name.casefold():
        raise TaskRuntimeClientError(
            f"Local browser only accepts {browser_app_name} windows."
        )
    target: dict[str, Any] = {
        "pid": pid,
        "window_id": window_id,
        "application": browser_app_name,
    }
    title = str(configuration.get("title") or "").strip()
    if title:
        target["title"] = title[:512]
    try:
        target["perception_mode"] = ComputerPerceptionMode(
            configuration.get("perception_mode", ComputerPerceptionMode.AUTO.value)
        ).value
    except ValueError as exc:
        raise TaskRuntimeClientError(
            "Local browser perception_mode must be auto, vision, or semantic."
        ) from exc
    return target


def _store_task_target(
    context: TaskRuntimeContext,
    target: Mapping[str, Any],
) -> None:
    session = context.session_factory()
    try:
        task = session.query(Task).filter(Task.id == context.task_id).first()
        if task is None or int(task.user_id) != context.user_id:
            raise TaskRuntimeClientError("Local browser task was not found.")
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
