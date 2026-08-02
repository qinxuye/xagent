from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .cua_driver import CuaDriverError, CuaDriverMCPClient

_READINESS_CACHE_SECONDS = 10.0


class LocalComputerReadinessIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class LocalComputerWindowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pid: int
    window_id: int
    application: str
    title: str | None = None


class LocalComputerReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    connected: bool
    attached: bool
    application: str = "Local computer"
    title: str | None = None
    windows: list[LocalComputerWindowSummary] = Field(default_factory=list)
    permissions: dict[str, bool] = Field(default_factory=dict)
    issues: list[LocalComputerReadinessIssue] = Field(default_factory=list)
    message: str = ""


@dataclass
class _ReadinessCache:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expires_at: float = 0
    value: LocalComputerReadiness | None = None


_cache = _ReadinessCache()


async def get_local_computer_readiness() -> LocalComputerReadiness:
    """Probe cua-driver and visible application windows with a short cache."""

    now = time.monotonic()
    if _cache.value is not None and _cache.expires_at > now:
        return _cache.value.model_copy(deep=True)
    async with _cache.lock:
        now = time.monotonic()
        if _cache.value is not None and _cache.expires_at > now:
            return _cache.value.model_copy(deep=True)
        value = await _probe_local_computer_readiness()
        _cache.value = value
        _cache.expires_at = time.monotonic() + _READINESS_CACHE_SECONDS
        return value.model_copy(deep=True)


def reset_local_computer_readiness_cache() -> None:
    _cache.value = None
    _cache.expires_at = 0


async def _probe_local_computer_readiness() -> LocalComputerReadiness:
    client = CuaDriverMCPClient()
    try:
        health, windows_result = await asyncio.gather(
            client.call_tool("health_report", {}),
            client.call_tool("list_windows", {"on_screen_only": True}),
        )
    except (CuaDriverError, FileNotFoundError, OSError) as exc:
        issue = LocalComputerReadinessIssue(
            code="driver_unavailable",
            message=f"cua-driver is unavailable on this Xagent host: {exc}",
        )
        return LocalComputerReadiness(
            ready=False,
            connected=False,
            attached=False,
            issues=[issue],
            message=issue.message,
        )
    finally:
        await client.close()

    report = health.structured
    overall = str(report.get("overall") or "").strip().lower()
    connected = overall in {"ok", "degraded"}
    permissions = _health_permissions(report)
    windows = _visible_windows(windows_result.structured.get("windows"))
    window = windows[0] if windows else None
    issues: list[LocalComputerReadinessIssue] = []
    if not connected:
        issues.append(
            LocalComputerReadinessIssue(
                code="driver_unhealthy",
                message=_health_failure_message(report),
            )
        )
    if permissions.get("screen_recording") is False:
        issues.append(
            LocalComputerReadinessIssue(
                code="screen_recording_permission_missing",
                message="cua-driver needs Screen Recording permission.",
            )
        )
    if permissions.get("accessibility") is False:
        issues.append(
            LocalComputerReadinessIssue(
                code="accessibility_permission_missing",
                message="cua-driver needs Accessibility permission.",
            )
        )
    if window is None:
        issues.append(
            LocalComputerReadinessIssue(
                code="window_not_found",
                message="No visible application window is available on the Xagent host.",
            )
        )

    title = _optional_string(window.get("title")) if window is not None else None
    application = (
        _optional_string(window.get("app_name"))
        if window is not None
        else "Local computer"
    ) or "Local computer"
    return LocalComputerReadiness(
        ready=not issues,
        connected=connected,
        attached=window is not None,
        application=application,
        title=title,
        windows=[_window_summary(item) for item in windows],
        permissions=permissions,
        issues=issues,
        message=" ".join(issue.message for issue in issues),
    )


def _health_permissions(report: Mapping[str, Any]) -> dict[str, bool]:
    permissions: dict[str, bool] = {}
    checks = report.get("checks")
    if not isinstance(checks, list):
        return permissions
    names = {
        "tcc_accessibility": "accessibility",
        "ax_capability": "accessibility",
        "tcc_screen_recording": "screen_recording",
        "screen_capture_capability": "screen_recording",
    }
    for check in checks:
        if not isinstance(check, Mapping):
            continue
        permission = names.get(str(check.get("name") or ""))
        status = str(check.get("status") or "").strip().lower()
        if permission is None or status not in {"pass", "fail"}:
            continue
        passed = status == "pass"
        current = permissions.get(permission)
        permissions[permission] = passed if current is None else current and passed
    return permissions


def _health_failure_message(report: Mapping[str, Any]) -> str:
    checks = report.get("checks")
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, Mapping):
                continue
            if str(check.get("status") or "").lower() != "fail":
                continue
            message = _optional_string(check.get("message"))
            hint = _optional_string(check.get("hint"))
            if message and hint:
                return f"cua-driver is unhealthy: {message} {hint}"
            if message:
                return f"cua-driver is unhealthy: {message}"
    return "cua-driver health checks failed on the Xagent host."


def _visible_windows(raw_windows: Any) -> list[Mapping[str, Any]]:
    if not isinstance(raw_windows, list):
        return []
    matches = [
        item
        for item in raw_windows
        if isinstance(item, Mapping)
        and item.get("on_current_space") is not False
        and item.get("is_on_screen") is True
        and _safe_int(item.get("pid")) > 0
        and _safe_int(item.get("window_id")) > 0
        and bool(_optional_string(item.get("app_name")))
    ]
    return sorted(
        matches,
        key=lambda item: _safe_int(item.get("z_index")),
        reverse=True,
    )[:50]


def _window_summary(window: Mapping[str, Any]) -> LocalComputerWindowSummary:
    return LocalComputerWindowSummary(
        pid=_safe_int(window.get("pid")),
        window_id=_safe_int(window.get("window_id")),
        application=_optional_string(window.get("app_name")) or "Application",
        title=_optional_string(window.get("title")),
    )


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_string(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


# Compatibility aliases retained for code and tasks created under the preview
# Local browser name.
NativeBrowserReadinessIssue = LocalComputerReadinessIssue
NativeBrowserReadiness = LocalComputerReadiness
get_native_browser_readiness = get_local_computer_readiness
reset_native_browser_readiness_cache = reset_local_computer_readiness_cache
