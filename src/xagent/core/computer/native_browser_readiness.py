from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ...config import get_native_browser_app_name
from .cua_driver import CuaDriverError, CuaDriverMCPClient

_READINESS_CACHE_SECONDS = 10.0


class NativeBrowserReadinessIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str


class NativeBrowserReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ready: bool
    connected: bool
    attached: bool
    application: str
    title: str | None = None
    permissions: dict[str, bool] = Field(default_factory=dict)
    issues: list[NativeBrowserReadinessIssue] = Field(default_factory=list)
    message: str = ""


@dataclass
class _ReadinessCache:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    expires_at: float = 0
    value: NativeBrowserReadiness | None = None


_cache = _ReadinessCache()


async def get_native_browser_readiness() -> NativeBrowserReadiness:
    """Probe cua-driver and the configured browser with a short polling cache."""

    now = time.monotonic()
    if _cache.value is not None and _cache.expires_at > now:
        return _cache.value.model_copy(deep=True)
    async with _cache.lock:
        now = time.monotonic()
        if _cache.value is not None and _cache.expires_at > now:
            return _cache.value.model_copy(deep=True)
        value = await _probe_native_browser_readiness()
        _cache.value = value
        _cache.expires_at = time.monotonic() + _READINESS_CACHE_SECONDS
        return value.model_copy(deep=True)


def reset_native_browser_readiness_cache() -> None:
    _cache.value = None
    _cache.expires_at = 0


async def _probe_native_browser_readiness() -> NativeBrowserReadiness:
    application = get_native_browser_app_name()
    client = CuaDriverMCPClient()
    try:
        health, windows_result = await asyncio.gather(
            client.call_tool("health_report", {}),
            client.call_tool("list_windows", {"on_screen_only": False}),
        )
    except (CuaDriverError, FileNotFoundError, OSError) as exc:
        issue = NativeBrowserReadinessIssue(
            code="driver_unavailable",
            message=f"cua-driver is unavailable on this Xagent host: {exc}",
        )
        return NativeBrowserReadiness(
            ready=False,
            connected=False,
            attached=False,
            application=application,
            issues=[issue],
            message=issue.message,
        )
    finally:
        await client.close()

    report = health.structured
    overall = str(report.get("overall") or "").strip().lower()
    connected = overall in {"ok", "degraded"}
    permissions = _health_permissions(report)
    window = _select_browser_window(
        windows_result.structured.get("windows"),
        app_name=application,
    )
    issues: list[NativeBrowserReadinessIssue] = []
    if not connected:
        issues.append(
            NativeBrowserReadinessIssue(
                code="driver_unhealthy",
                message=_health_failure_message(report),
            )
        )
    if permissions.get("screen_recording") is False:
        issues.append(
            NativeBrowserReadinessIssue(
                code="screen_recording_permission_missing",
                message="cua-driver needs Screen Recording permission.",
            )
        )
    if permissions.get("accessibility") is False:
        issues.append(
            NativeBrowserReadinessIssue(
                code="accessibility_permission_missing",
                message="cua-driver needs Accessibility permission.",
            )
        )
    if window is None:
        issues.append(
            NativeBrowserReadinessIssue(
                code="browser_not_found",
                message=(
                    f"No visible {application!r} window is on the current desktop "
                    "of the Xagent host."
                ),
            )
        )

    title = _optional_string(window.get("title")) if window is not None else None
    return NativeBrowserReadiness(
        ready=not issues,
        connected=connected,
        attached=window is not None,
        application=application,
        title=title,
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


def _select_browser_window(
    raw_windows: Any,
    *,
    app_name: str,
) -> Mapping[str, Any] | None:
    if not isinstance(raw_windows, list):
        return None
    normalized = app_name.casefold()
    matches = [
        item
        for item in raw_windows
        if isinstance(item, Mapping)
        and str(item.get("app_name") or "").casefold() == normalized
        and item.get("on_current_space") is True
        and item.get("is_on_screen") is True
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: _safe_int(item.get("z_index")))


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _optional_string(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
