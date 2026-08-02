from __future__ import annotations

from typing import Any

import pytest

from xagent.core.computer.cua_driver import CuaDriverResult
from xagent.core.computer.native_browser import NativeBrowserEnvironment
from xagent.core.computer.schema import (
    COMPUTER_FRAME_ID_METADATA_KEY,
    COMPUTER_SESSION_ID_METADATA_KEY,
    ComputerAction,
    ComputerActionBatch,
    ComputerActionType,
    ComputerTarget,
)
from xagent.core.context_ref import ContextReference


class FakeObservationStore:
    async def save_screenshot(self, **kwargs: Any) -> ContextReference:
        return ContextReference(
            file_ref={
                "file_id": "native-shot",
                "filename": "native.png",
                "mime_type": kwargs["mime_type"],
            },
            metadata={
                COMPUTER_SESSION_ID_METADATA_KEY: kwargs["session_id"],
                COMPUTER_FRAME_ID_METADATA_KEY: kwargs["frame_id"],
            },
        )


class FakeCuaDriver:
    def __init__(
        self,
        *,
        windows: list[dict[str, Any]] | None = None,
        escalation: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.windows = windows if windows is not None else self._default_windows()
        self.escalation = escalation

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> CuaDriverResult:
        payload = dict(arguments or {})
        self.calls.append((name, payload))
        if name == "list_windows":
            return CuaDriverResult(structured={"windows": self.windows})
        if name == "get_window_state":
            structured: dict[str, Any] = {
                "window_id": payload["window_id"],
                "pid": payload["pid"],
                "url": "https://example.com/inbox",
                "element_count": 2,
                "screenshot_width": 1200,
                "screenshot_height": 800,
                "elements": [
                    {
                        "element_index": 4,
                        "element_token": "snapshot-1:4",
                        "role": "AXButton",
                        "label": "Continue",
                        "frame": {"x": 200, "y": 300, "w": 200, "h": 80},
                    },
                    {
                        "element_index": 5,
                        "element_token": "snapshot-1:5",
                        "role": "AXSecureTextField",
                        "label": "Password",
                        "value": "do-not-leak",
                        "frame": {"x": 300, "y": 450, "w": 300, "h": 50},
                    },
                ],
            }
            if self.escalation is not None:
                structured["escalation"] = self.escalation
            return CuaDriverResult(
                structured=structured,
                image_bytes=b"native-png",
                image_mime_type="image/png",
            )
        if name == "health_report":
            return CuaDriverResult(structured={"schema_version": "1", "overall": "ok"})
        return CuaDriverResult(
            structured={"effect": "confirmed", "verified": True},
            text=f"{name} completed",
        )

    async def close(self) -> None:
        self.closed = True

    @staticmethod
    def _default_windows() -> list[dict[str, Any]]:
        return [
            {
                "window_id": 10,
                "pid": 100,
                "app_name": "Google Chrome",
                "title": "Background",
                "bounds": {"x": 10, "y": 10, "width": 900, "height": 700},
                "z_index": 1,
                "is_on_screen": True,
                "on_current_space": False,
            },
            {
                "window_id": 20,
                "pid": 200,
                "app_name": "Google Chrome",
                "title": "Inbox",
                "bounds": {"x": 100, "y": 200, "width": 1000, "height": 800},
                "z_index": 9,
                "is_on_screen": True,
                "on_current_space": True,
            },
        ]


def make_environment(driver: FakeCuaDriver) -> NativeBrowserEnvironment:
    return NativeBrowserEnvironment(
        session_id="task-1",
        workspace=object(),
        driver=driver,
        observation_store=FakeObservationStore(),  # type: ignore[arg-type]
    )


def batch(frame_id: str, action: ComputerAction) -> ComputerActionBatch:
    return ComputerActionBatch(
        session_id="task-1",
        expected_frame_id=frame_id,
        actions=[action],
    )


def test_native_browser_requires_explicit_enablement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XAGENT_NATIVE_BROWSER_ENABLED", raising=False)

    with pytest.raises(RuntimeError, match="Native browser access is disabled"):
        NativeBrowserEnvironment(session_id="task-1", workspace=object())


@pytest.mark.asyncio
async def test_native_browser_binds_frontmost_window_and_redacts_password() -> None:
    driver = FakeCuaDriver()
    environment = make_environment(driver)

    observation = await environment.observe()

    assert observation.title == "Inbox"
    assert observation.active_url == "https://example.com/inbox"
    assert observation.viewport.width == 1200
    assert observation.metadata["pid"] == 200
    assert observation.metadata["window_id"] == 20
    assert "move" not in observation.metadata["supported_actions"]
    assert observation.elements[0].element_id == "snapshot-1:4"
    assert observation.elements[1].label == "Sensitive input"
    assert observation.elements[1].text is None
    assert "do-not-leak" not in observation.model_dump_json()
    assert [name for name, _payload in driver.calls[:3]] == [
        "start_session",
        "list_windows",
        "get_window_state",
    ]


@pytest.mark.asyncio
async def test_native_browser_click_uses_bound_window_and_element_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("xagent.core.computer.native_browser.asyncio.sleep", no_sleep)
    driver = FakeCuaDriver()
    environment = make_environment(driver)
    first = await environment.observe()

    second = await environment.execute(
        batch(
            first.frame_id,
            ComputerAction(
                type=ComputerActionType.CLICK,
                target=ComputerTarget(element_id="snapshot-1:4"),
            ),
        )
    )

    name, payload = driver.calls[3]
    assert name == "click"
    assert payload["pid"] == 200
    assert payload["window_id"] == 20
    assert payload["element_token"] == "snapshot-1:4"
    assert payload["delivery_mode"] == "background"
    assert second.metadata["last_action_result"]["effect"] == "confirmed"
    assert [name for name, _payload in driver.calls].count("list_windows") == 1


@pytest.mark.asyncio
async def test_native_browser_requires_driver_proof_for_foreground_delivery() -> None:
    driver = FakeCuaDriver()
    environment = make_environment(driver)
    first = await environment.observe()

    with pytest.raises(ValueError, match="escalation recommendation"):
        await environment.execute(
            batch(
                first.frame_id,
                ComputerAction(
                    type=ComputerActionType.CLICK,
                    target=ComputerTarget(element_id="snapshot-1:4"),
                    metadata={"delivery_mode": "foreground"},
                ),
            )
        )


@pytest.mark.asyncio
async def test_native_browser_allows_driver_recommended_foreground_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("xagent.core.computer.native_browser.asyncio.sleep", no_sleep)
    driver = FakeCuaDriver(
        escalation={"recommended": "foreground", "reason": "background did not land"}
    )
    environment = make_environment(driver)
    first = await environment.observe()

    await environment.execute(
        batch(
            first.frame_id,
            ComputerAction(
                type=ComputerActionType.CLICK,
                target=ComputerTarget(element_id="snapshot-1:4"),
                metadata={"delivery_mode": "foreground"},
            ),
        )
    )

    assert driver.calls[3][1]["delivery_mode"] == "foreground"


@pytest.mark.asyncio
async def test_native_browser_defensively_rejects_incomplete_drag() -> None:
    driver = FakeCuaDriver()
    environment = make_environment(driver)
    first = await environment.observe()
    invalid_drag = ComputerAction.model_construct(
        type=ComputerActionType.DRAG,
        target=None,
        url=None,
        text=None,
        keys=[],
        delta_x=0,
        delta_y=0,
        start=None,
        end=None,
        duration_ms=0,
        metadata={},
    )

    with pytest.raises(ValueError, match="drag requires start and end points"):
        await environment.execute(batch(first.frame_id, invalid_drag))


@pytest.mark.asyncio
async def test_native_browser_navigation_and_teardown_use_driver() -> None:
    driver = FakeCuaDriver()
    environment = NativeBrowserEnvironment(
        session_id="task-1",
        workspace=object(),
        driver=driver,
        observation_store=FakeObservationStore(),  # type: ignore[arg-type]
        navigation_allowlist=["example.com"],
    )
    first = await environment.observe()

    await environment.execute(
        batch(
            first.frame_id,
            ComputerAction(
                type=ComputerActionType.NAVIGATE,
                url="https://example.com/account",
            ),
        )
    )
    await environment.close()

    action_calls = driver.calls[3:6]
    assert [name for name, _payload in action_calls] == [
        "hotkey",
        "type_text",
        "press_key",
    ]
    assert all(payload["delivery_mode"] == "foreground" for _, payload in action_calls)
    assert ("end_session", {"session": "task-1"}) in driver.calls
    assert driver.closed is True
    assert environment.closed is True


@pytest.mark.asyncio
async def test_native_browser_refuses_missing_or_hidden_browser() -> None:
    driver = FakeCuaDriver(windows=[])
    with pytest.raises(RuntimeError, match="No local 'Google Chrome' window"):
        await make_environment(driver).observe()

    hidden = FakeCuaDriver(
        windows=[
            {
                "window_id": 10,
                "pid": 100,
                "app_name": "Google Chrome",
                "title": "Hidden",
                "bounds": {"x": 10, "y": 10, "width": 900, "height": 700},
                "z_index": 1,
                "is_on_screen": False,
                "on_current_space": False,
            }
        ]
    )
    with pytest.raises(RuntimeError, match="No visible 'Google Chrome' window"):
        await make_environment(hidden).observe()
