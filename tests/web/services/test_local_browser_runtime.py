from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from xagent.core.computer.native_browser import (
    LEGACY_LOCAL_BROWSER_TASK_EXTENSION,
    LOCAL_COMPUTER_TASK_EXTENSION,
)
from xagent.core.task_runtime import (
    TaskRuntimeClientError,
    TaskRuntimeContext,
    TaskRuntimeContribution,
    merge_task_runtime_contributions,
)
from xagent.core.tools.adapters.vibe.browser_tools import (
    _has_local_computer_runtime,
    create_browser_tools,
)
from xagent.web.models.task import Task
from xagent.web.models.user import User
from xagent.web.services.local_browser_runtime import (
    LocalComputerTaskRuntimeProvider,
    register_local_computer_runtime,
    unregister_local_computer_runtime,
)
from xagent.web.services.task_runtime import (
    agent_config_with_task_extension_bindings,
    registered_task_extensions,
    unregister_task_extension,
)


class FakeSession:
    def __init__(self, *, task: Any, user: Any) -> None:
        self.task = task
        self.user = user
        self.model: Any = None
        self.closed = False
        self.committed = False

    def query(self, model: Any) -> "FakeSession":
        self.model = model
        return self

    def filter(self, *_args: Any) -> "FakeSession":
        return self

    def first(self) -> Any:
        if self.model is Task:
            return self.task
        if self.model is User:
            return self.user
        raise AssertionError(f"unexpected query model: {self.model}")

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        self.committed = True


def make_context(
    *,
    bound: bool,
    admin: bool,
    workspace: Any = object(),
    extension: str = LOCAL_COMPUTER_TASK_EXTENSION,
):
    agent_config = (
        agent_config_with_task_extension_bindings({}, [extension]) if bound else {}
    )
    sessions: list[FakeSession] = []
    task = SimpleNamespace(id=7, user_id=3, agent_config=agent_config)
    user = SimpleNamespace(id=3, is_admin=admin)

    def session_factory() -> FakeSession:
        session = FakeSession(task=task, user=user)
        sessions.append(session)
        return session

    return (
        TaskRuntimeContext(
            task_id=7,
            user_id=3,
            source="internal",
            session_factory=session_factory,
            workspace=workspace,
        ),
        sessions,
    )


def test_local_computer_registration_is_explicit_and_lifespan_scoped() -> None:
    unregister_task_extension(LOCAL_COMPUTER_TASK_EXTENSION)
    unregister_task_extension(LEGACY_LOCAL_BROWSER_TASK_EXTENSION)
    try:
        register_local_computer_runtime()
        register_local_computer_runtime()
        assert registered_task_extensions().count(LOCAL_COMPUTER_TASK_EXTENSION) == 1
        assert (
            registered_task_extensions().count(LEGACY_LOCAL_BROWSER_TASK_EXTENSION) == 1
        )

        unregister_local_computer_runtime()
        assert LOCAL_COMPUTER_TASK_EXTENSION not in registered_task_extensions()
        assert LEGACY_LOCAL_BROWSER_TASK_EXTENSION not in registered_task_extensions()
    finally:
        unregister_task_extension(LOCAL_COMPUTER_TASK_EXTENSION)
        unregister_task_extension(LEGACY_LOCAL_BROWSER_TASK_EXTENSION)


def test_local_computer_create_requires_enablement_admin_and_valid_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalComputerTaskRuntimeProvider()
    context, sessions = make_context(bound=True, admin=True)
    monkeypatch.delenv("XAGENT_NATIVE_BROWSER_ENABLED", raising=False)
    monkeypatch.delenv("XAGENT_LOCAL_COMPUTER_ENABLED", raising=False)

    with pytest.raises(TaskRuntimeClientError, match="disabled") as disabled:
        provider.on_task_created(context, {})
    assert disabled.value.status_code == 403

    monkeypatch.setenv("XAGENT_NATIVE_BROWSER_ENABLED", "true")
    non_admin, _ = make_context(bound=True, admin=False)
    with pytest.raises(TaskRuntimeClientError, match="administrators"):
        provider.on_task_created(non_admin, {})

    provider.on_task_created(context, {})
    assert sessions[-1].closed is True

    provider.on_task_created(
        context,
        {
            "pid": 100,
            "window_id": 20,
            "application": "Music",
            "title": "Songs",
        },
    )
    assert sessions[-1].committed is True


def test_local_computer_contributes_standard_computer_tool_only_when_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAGENT_NATIVE_BROWSER_ENABLED", "true")
    provider = LocalComputerTaskRuntimeProvider()
    unbound, _ = make_context(bound=False, admin=True)
    assert provider.build_runtime(unbound) is None

    bound, sessions = make_context(bound=True, admin=True)
    contribution = provider.build_runtime(bound)

    assert isinstance(contribution, TaskRuntimeContribution)
    assert [tool.name for tool in contribution.tools] == ["computer"]
    assert contribution.preferred_input_modalities == ("image",)
    assert "not a browser extension or remote relay" in (contribution.environment or "")
    assert sessions[-1].closed is True


@pytest.mark.asyncio
async def test_local_computer_binding_suppresses_colliding_playwright_family() -> None:
    contribution = merge_task_runtime_contributions(
        {
            LOCAL_COMPUTER_TASK_EXTENSION: TaskRuntimeContribution(
                tools=(SimpleNamespace(name="computer"),)
            )
        }
    )
    config = SimpleNamespace(
        get_browser_tools_enabled=lambda: True,
        get_task_runtime_contribution=lambda: contribution,
    )

    assert await create_browser_tools(config) == []


def test_unbound_local_computer_provider_does_not_suppress_playwright() -> None:
    contribution = merge_task_runtime_contributions(
        {LOCAL_COMPUTER_TASK_EXTENSION: None}
    )

    assert _has_local_computer_runtime(contribution) is False


def test_local_computer_public_metadata_is_bound_task_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAGENT_NATIVE_BROWSER_ENABLED", "true")
    provider = LocalComputerTaskRuntimeProvider()
    unbound, _ = make_context(bound=False, admin=True)
    bound, _ = make_context(bound=True, admin=True)

    assert provider.public_metadata(unbound) is None
    assert provider.public_metadata(bound) == {
        "kind": "local_computer",
        "enabled": True,
    }


def test_legacy_local_browser_binding_still_builds_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAGENT_LOCAL_COMPUTER_ENABLED", "true")
    provider = LocalComputerTaskRuntimeProvider(LEGACY_LOCAL_BROWSER_TASK_EXTENSION)
    context, _ = make_context(
        bound=True,
        admin=True,
        extension=LEGACY_LOCAL_BROWSER_TASK_EXTENSION,
    )
    assert provider.build_runtime(context) is not None
