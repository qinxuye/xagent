from __future__ import annotations

from typing import Any

import pytest

from xagent.core.computer import native_browser_readiness as readiness
from xagent.core.computer.cua_driver import CuaDriverError, CuaDriverResult


class FakeClient:
    def __init__(self, responses: dict[str, CuaDriverResult | Exception]) -> None:
        self.responses = responses
        self.closed = False

    async def call_tool(
        self,
        name: str,
        _arguments: dict[str, Any],
    ) -> CuaDriverResult:
        response = self.responses[name]
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    readiness.reset_native_browser_readiness_cache()


@pytest.mark.asyncio
async def test_native_browser_readiness_combines_health_and_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        {
            "health_report": CuaDriverResult(
                structured={
                    "overall": "ok",
                    "checks": [
                        {"name": "tcc_accessibility", "status": "pass"},
                        {"name": "tcc_screen_recording", "status": "pass"},
                    ],
                }
            ),
            "list_windows": CuaDriverResult(
                structured={
                    "windows": [
                        {
                            "app_name": "Google Chrome",
                            "title": "Inbox",
                            "on_current_space": True,
                            "is_on_screen": True,
                            "z_index": 4,
                        }
                    ]
                }
            ),
        }
    )
    monkeypatch.setattr(readiness, "CuaDriverMCPClient", lambda: client)

    result = await readiness.get_native_browser_readiness()

    assert result.ready is True
    assert result.connected is True
    assert result.attached is True
    assert result.title == "Inbox"
    assert result.permissions == {
        "accessibility": True,
        "screen_recording": True,
    }
    assert client.closed is True


@pytest.mark.asyncio
async def test_native_browser_readiness_reports_driver_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        {
            "health_report": CuaDriverError("not installed"),
            "list_windows": CuaDriverResult(structured={"windows": []}),
        }
    )
    monkeypatch.setattr(readiness, "CuaDriverMCPClient", lambda: client)

    result = await readiness.get_native_browser_readiness()

    assert result.ready is False
    assert result.connected is False
    assert [issue.code for issue in result.issues] == ["driver_unavailable"]
    assert client.closed is True


@pytest.mark.asyncio
async def test_native_browser_readiness_reports_permissions_and_missing_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient(
        {
            "health_report": CuaDriverResult(
                structured={
                    "overall": "degraded",
                    "checks": [
                        {"name": "tcc_accessibility", "status": "fail"},
                        {"name": "tcc_screen_recording", "status": "fail"},
                    ],
                }
            ),
            "list_windows": CuaDriverResult(structured={"windows": []}),
        }
    )
    monkeypatch.setattr(readiness, "CuaDriverMCPClient", lambda: client)

    result = await readiness.get_native_browser_readiness()

    assert result.connected is True
    assert result.attached is False
    assert [issue.code for issue in result.issues] == [
        "screen_recording_permission_missing",
        "accessibility_permission_missing",
        "browser_not_found",
    ]
