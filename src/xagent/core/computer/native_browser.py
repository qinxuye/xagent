from __future__ import annotations

import asyncio
import math
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from ...config import (
    get_browser_cua_driver_max_elements,
    get_native_browser_app_name,
    get_native_browser_enabled,
)
from .cua_driver import (
    CuaDriverClientProtocol,
    CuaDriverError,
    CuaDriverMCPClient,
    CuaDriverResult,
)
from .environment import ComputerEnvironment, ComputerTargetNotFoundError
from .input_platform import (
    ComputerInputPlatform,
    computer_input_metadata,
    host_computer_input_platform,
)
from .schema import (
    ELEMENT_EXTRACTION_FAILED_KEY,
    ELEMENT_EXTRACTION_INCOMPLETE_KEY,
    ELEMENTS_TRUNCATED_KEY,
    MAX_OBSERVATION_ELEMENTS,
    ComputerAction,
    ComputerActionBatch,
    ComputerActionType,
    ComputerElement,
    ComputerElementSource,
    ComputerEnvironmentType,
    ComputerObservation,
    NormalizedPoint,
    NormalizedRect,
    Viewport,
)
from .store import ObservationStore

_SUPPORTED_ACTIONS = tuple(
    action for action in ComputerActionType if action is not ComputerActionType.MOVE
)
_ACTION_RESULT_FIELDS = (
    "path",
    "effect",
    "verified",
    "escalation",
    "status",
    "code",
)

CuaDriverClientFactory = Callable[[], CuaDriverClientProtocol]
LOCAL_BROWSER_TASK_EXTENSION = "local_browser"


@dataclass(frozen=True)
class NativeBrowserWindow:
    pid: int
    window_id: int
    app_name: str
    title: str | None
    x: float
    y: float
    width: float
    height: float
    z_index: int
    is_on_screen: bool
    on_current_space: bool


class NativeBrowserEnvironment(ComputerEnvironment):
    """Control one local browser window through cua-driver's native MCP tools.

    The first observation binds the task to one concrete ``(pid, window_id)``.
    That binding is intentionally sticky: if the window closes, the environment
    fails instead of silently taking over another browser window.
    """

    def __init__(
        self,
        *,
        session_id: str,
        workspace: Any,
        driver: CuaDriverClientProtocol | None = None,
        driver_factory: CuaDriverClientFactory | None = None,
        observation_store: ObservationStore | None = None,
        browser_app_name: str | None = None,
        navigation_allowlist: Sequence[str] | None = None,
        navigation_denylist: Sequence[str] | None = None,
        max_elements: int | None = None,
        headless: bool = False,
    ) -> None:
        del headless
        super().__init__(session_id)
        if driver is not None and driver_factory is not None:
            raise ValueError("provide either driver or driver_factory, not both")
        if driver is None and not get_native_browser_enabled():
            raise RuntimeError(
                "Native browser access is disabled. Set "
                "XAGENT_NATIVE_BROWSER_ENABLED=true only on a trusted "
                "interactive Xagent host."
            )
        self.workspace = workspace
        self.observation_store = observation_store or ObservationStore(workspace)
        self.browser_app_name = (
            browser_app_name or get_native_browser_app_name()
        ).strip()
        if not self.browser_app_name:
            raise ValueError("native browser application name must not be empty")
        self.navigation_allowlist = _normalize_host_patterns(navigation_allowlist)
        self.navigation_denylist = _normalize_host_patterns(navigation_denylist)
        self.max_elements = (
            get_browser_cua_driver_max_elements()
            if max_elements is None
            else max_elements
        )
        if self.max_elements <= 0:
            raise ValueError("native browser max_elements must be positive")
        self._driver = driver
        self._driver_factory = driver_factory or CuaDriverMCPClient
        self._target: NativeBrowserWindow | None = None
        self._session_started = False
        self._last_action_result: dict[str, Any] | None = None

    async def _close(self) -> None:
        driver = self._driver
        self._driver = None
        if driver is None:
            return
        if self._session_started:
            try:
                await driver.call_tool("end_session", {"session": self.session_id})
            except Exception:
                # Closing stdin still tears down process-owned driver state.
                pass
        await driver.close()
        self._session_started = False

    async def health_report(self) -> dict[str, Any]:
        """Return cua-driver's stable structured diagnostics contract."""

        result = await self._get_driver().call_tool("health_report", {})
        return result.structured

    async def _observe(self) -> ComputerObservation:
        await self._ensure_session()
        if self._target is None:
            self._target = await self._select_target()
        return await self._capture_observation()

    async def _execute(self, batch: ComputerActionBatch) -> ComputerObservation:
        if len(batch.actions) != 1:
            raise ValueError("native browser executes exactly one action per frame")
        action = batch.actions[0]
        supported_actions = (
            self.current_observation.metadata.get("supported_actions")
            if self.current_observation is not None
            else None
        )
        if self.current_observation is not None and (
            not isinstance(supported_actions, list)
            or action.type.value not in supported_actions
        ):
            raise ValueError(
                f"{action.type.value} is not supported by the native browser runtime"
            )
        await self._execute_action(action)
        if action.type not in {ComputerActionType.SCREENSHOT, ComputerActionType.WAIT}:
            await asyncio.sleep(0.25)
        return await self._capture_observation()

    async def _ensure_session(self) -> None:
        if self._session_started:
            return
        await self._get_driver().call_tool(
            "start_session",
            {
                "session": self.session_id,
                "capture_scope": "window",
            },
        )
        self._session_started = True

    def _get_driver(self) -> CuaDriverClientProtocol:
        if self._driver is None:
            self._driver = self._driver_factory()
        return self._driver

    async def _select_target(self) -> NativeBrowserWindow:
        result = await self._get_driver().call_tool(
            "list_windows",
            {"on_screen_only": False},
        )
        raw_windows = result.structured.get("windows")
        if not isinstance(raw_windows, list):
            raise CuaDriverError("cua-driver list_windows returned no window list")
        windows = [
            parsed
            for raw in raw_windows
            if isinstance(raw, Mapping)
            and (parsed := self._parse_window(raw)) is not None
        ]
        app_name = self.browser_app_name.casefold()
        matches = [
            window for window in windows if window.app_name.casefold() == app_name
        ]
        if not matches:
            raise ComputerTargetNotFoundError(
                f"No local {self.browser_app_name!r} window is running. Open the "
                "browser on the Xagent host, then request a fresh screenshot."
            )
        visible_matches = [
            window
            for window in matches
            if window.on_current_space and window.is_on_screen
        ]
        if not visible_matches:
            raise ComputerTargetNotFoundError(
                f"No visible {self.browser_app_name!r} window is on the current "
                "desktop. Restore and show the intended window, then request a "
                "fresh screenshot."
            )
        return max(visible_matches, key=lambda window: window.z_index)

    @staticmethod
    def _parse_window(raw: Mapping[str, Any]) -> NativeBrowserWindow | None:
        bounds = raw.get("bounds")
        if not isinstance(bounds, Mapping):
            return None
        try:
            width = float(bounds["width"])
            height = float(bounds["height"])
            if width <= 0 or height <= 0:
                return None
            return NativeBrowserWindow(
                pid=int(raw["pid"]),
                window_id=int(raw["window_id"]),
                app_name=str(raw["app_name"]),
                title=(
                    str(raw["title"]).strip()
                    if raw.get("title") is not None and str(raw["title"]).strip()
                    else None
                ),
                x=float(bounds["x"]),
                y=float(bounds["y"]),
                width=width,
                height=height,
                z_index=int(raw.get("z_index") or 0),
                is_on_screen=raw.get("is_on_screen") is True,
                on_current_space=raw.get("on_current_space") is True,
            )
        except (KeyError, TypeError, ValueError):
            return None

    async def _capture_observation(self) -> ComputerObservation:
        target = self._require_target()
        requested_max_elements = min(self.max_elements, MAX_OBSERVATION_ELEMENTS)
        result = await self._get_driver().call_tool(
            "get_window_state",
            {
                "session": self.session_id,
                "pid": target.pid,
                "window_id": target.window_id,
                "include_screenshot": True,
                "max_elements": requested_max_elements,
            },
        )
        if not result.image_bytes:
            raise CuaDriverError(
                "cua-driver could not capture the bound browser window. Check "
                "Screen Recording permission with `cua-driver health_report`."
            )
        mime_type = result.image_mime_type or str(
            result.structured.get("screenshot_mime_type") or "image/png"
        )
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise CuaDriverError(
                f"cua-driver returned unsupported screenshot type {mime_type!r}"
            )
        width, height = self._screenshot_size(result)
        viewport = Viewport(width=width, height=height, device_pixel_ratio=1.0)
        frame_id = f"frame-{uuid4().hex}"
        screenshot = await self.observation_store.save_screenshot(
            session_id=self.session_id,
            frame_id=frame_id,
            image_bytes=result.image_bytes,
            mime_type=mime_type,
            viewport=viewport,
            text_fallback=(f"Current local {target.app_name} window screenshot."),
            metadata={
                "browser_runtime_kind": "native_browser",
                "pid": target.pid,
                "window_id": target.window_id,
            },
        )
        raw_elements = result.structured.get("elements")
        elements = self._build_elements(
            raw_elements if isinstance(raw_elements, list) else [],
            target=target,
        )
        raw_element_count = self._optional_int(result.structured.get("element_count"))
        metadata: dict[str, Any] = {
            "browser_runtime_kind": "native_browser",
            "native_driver": "cua-driver",
            "application": target.app_name,
            "pid": target.pid,
            "window_id": target.window_id,
            "user_takeover_available": True,
            "delivery_mode": "background",
            **computer_input_metadata(host_computer_input_platform()),
            "supported_actions": [action.value for action in _SUPPORTED_ACTIONS],
        }
        if result.structured.get("degraded") is True:
            metadata[ELEMENT_EXTRACTION_FAILED_KEY] = True
            metadata["driver_degraded_reason"] = str(
                result.structured.get("degraded_reason") or ""
            )
        if raw_element_count is not None and raw_element_count > len(elements):
            metadata[ELEMENT_EXTRACTION_INCOMPLETE_KEY] = True
        if isinstance(raw_elements, list) and len(elements) < len(raw_elements):
            metadata[ELEMENT_EXTRACTION_INCOMPLETE_KEY] = True
        if (
            raw_element_count is not None and raw_element_count > requested_max_elements
        ) or (
            isinstance(raw_elements, list)
            and len(raw_elements) >= requested_max_elements
        ):
            metadata[ELEMENT_EXTRACTION_INCOMPLETE_KEY] = True
            metadata[ELEMENTS_TRUNCATED_KEY] = True
        escalation = result.structured.get("escalation")
        if isinstance(escalation, Mapping):
            metadata["driver_escalation"] = dict(escalation)
        if self._last_action_result is not None:
            metadata["last_action_result"] = self._last_action_result
            self._last_action_result = None
        return ComputerObservation(
            session_id=self.session_id,
            frame_id=frame_id,
            environment=ComputerEnvironmentType.BROWSER,
            viewport=viewport,
            screenshot=screenshot,
            elements=elements,
            active_url=_optional_active_url(result.structured),
            title=target.title,
            metadata=metadata,
        )

    def _screenshot_size(self, result: CuaDriverResult) -> tuple[int, int]:
        width = self._optional_int(result.structured.get("screenshot_width"))
        height = self._optional_int(result.structured.get("screenshot_height"))
        if width and height:
            return width, height
        image = result.image_bytes or b""
        if image.startswith(b"\x89PNG\r\n\x1a\n") and len(image) >= 24:
            parsed_width, parsed_height = struct.unpack(">II", image[16:24])
            if parsed_width > 0 and parsed_height > 0:
                return parsed_width, parsed_height
        raise CuaDriverError("cua-driver screenshot dimensions are missing")

    def _build_elements(
        self,
        raw_elements: list[Any],
        *,
        target: NativeBrowserWindow,
    ) -> list[ComputerElement]:
        elements: list[ComputerElement] = []
        for raw in raw_elements[:MAX_OBSERVATION_ELEMENTS]:
            if not isinstance(raw, Mapping):
                continue
            frame = raw.get("frame")
            if not isinstance(frame, Mapping):
                continue
            bounds = self._normalize_element_bounds(frame, target=target)
            if bounds is None:
                continue
            token = str(raw.get("element_token") or "").strip()
            index = self._optional_int(raw.get("element_index"))
            if not token and index is None:
                continue
            element_id = token or f"cua-index:{index}"
            role = str(raw.get("role") or "").strip() or None
            label = str(raw.get("label") or "").strip() or None
            sensitive = self._is_sensitive_element(
                raw,
                role=role,
                label=label,
            )
            value = str(raw.get("value") or "").strip() or None
            metadata = {
                "element_index": index,
                "element_token": token or None,
                "depth": self._optional_int(raw.get("depth")),
                "parent_index": self._optional_int(raw.get("parent_index")),
                "enabled": raw.get("enabled"),
                "selected": raw.get("selected"),
                "sensitive": sensitive,
            }
            return_metadata = {
                key: value for key, value in metadata.items() if value is not None
            }
            elements.append(
                ComputerElement(
                    element_id=element_id,
                    source=ComputerElementSource.ACCESSIBILITY,
                    bounds=bounds,
                    label="Sensitive input" if sensitive else label,
                    role=role,
                    text=None if sensitive else value,
                    metadata=return_metadata,
                )
            )
        return elements

    @staticmethod
    def _is_sensitive_element(
        raw: Mapping[str, Any],
        *,
        role: str | None,
        label: str | None,
    ) -> bool:
        role_text = " ".join(
            str(value or "").casefold()
            for value in (role, raw.get("subrole"), raw.get("input_type"))
        )
        if any(marker in role_text for marker in ("secure", "password")):
            return True
        if any(
            raw.get(flag) is True for flag in ("sensitive", "protected", "is_password")
        ):
            return True
        is_text_input = any(
            marker in role_text for marker in ("text", "input", "field", "edit")
        )
        label_text = (label or "").casefold()
        return is_text_input and any(
            marker in label_text
            for marker in ("password", "passcode", "security code", "verification code")
        )

    @staticmethod
    def _normalize_element_bounds(
        raw: Mapping[str, Any],
        *,
        target: NativeBrowserWindow,
    ) -> NormalizedRect | None:
        try:
            x = float(raw["x"])
            y = float(raw["y"])
            width = float(raw["w"])
            height = float(raw["h"])
        except (KeyError, TypeError, ValueError):
            return None
        if width <= 0 or height <= 0:
            return None

        # AX frames are normally screen-relative. Some platform backends emit
        # window-local frames, so accept that shape when subtracting the native
        # window origin would put the entire element outside the screenshot.
        local_x = x - target.x
        local_y = y - target.y
        if (
            (
                local_x + width <= 0
                or local_y + height <= 0
                or local_x >= target.width
                or local_y >= target.height
            )
            and 0 <= x < target.width
            and 0 <= y < target.height
        ):
            local_x = x
            local_y = y

        left = max(0.0, local_x)
        top = max(0.0, local_y)
        right = min(target.width, local_x + width)
        bottom = min(target.height, local_y + height)
        if right <= left or bottom <= top:
            return None
        return NormalizedRect(
            x=left / target.width,
            y=top / target.height,
            width=(right - left) / target.width,
            height=(bottom - top) / target.height,
        )

    async def _execute_action(self, action: ComputerAction) -> None:
        if action.type is ComputerActionType.SCREENSHOT:
            return
        if action.type is ComputerActionType.WAIT:
            duration_ms = action.duration_ms or 1_000
            await asyncio.sleep(duration_ms / 1_000)
            self._last_action_result = {
                "effect": "confirmed",
                "verified": True,
            }
            return
        if action.type is ComputerActionType.NAVIGATE:
            await self._navigate(action.url or "")
            return

        target = self._require_target()
        common: dict[str, Any] = {
            "session": self.session_id,
            "pid": target.pid,
            "window_id": target.window_id,
            "delivery_mode": self._delivery_mode(action),
        }
        if action.type in {
            ComputerActionType.CLICK,
            ComputerActionType.DOUBLE_CLICK,
        }:
            arguments = {**common, **self._action_target_arguments(action)}
            tool_name = (
                "double_click"
                if action.type is ComputerActionType.DOUBLE_CLICK
                else "click"
            )
            await self._call_action(tool_name, arguments)
            return
        if action.type is ComputerActionType.TYPE:
            arguments = {**common, "text": action.text or ""}
            await self._call_action("type_text", arguments)
            return
        if action.type is ComputerActionType.REPLACE_TEXT:
            x, y = self._action_point_pixels(action)
            modifier = self._primary_driver_modifier()
            await self._call_action(
                "hotkey",
                {
                    **common,
                    "keys": [modifier, "a"],
                    "x": x,
                    "y": y,
                },
                remember=False,
            )
            await self._call_action(
                "type_text",
                {
                    **common,
                    "text": action.text or "",
                },
            )
            return
        if action.type is ComputerActionType.KEYPRESS:
            keys = [self._driver_key(key) for key in action.keys]
            if len(keys) == 1:
                arguments = {**common, "key": keys[0]}
                tool_name = "press_key"
            else:
                arguments = {**common, "keys": keys}
                tool_name = "hotkey"
            await self._call_action(tool_name, arguments)
            return
        if action.type is ComputerActionType.SCROLL:
            horizontal = abs(action.delta_x) > abs(action.delta_y)
            delta = action.delta_x if horizontal else action.delta_y
            direction = (
                ("right" if delta > 0 else "left")
                if horizontal
                else ("down" if delta > 0 else "up")
            )
            arguments = {
                **common,
                "direction": direction,
                "amount": max(1, min(20, math.ceil(abs(delta) * 10))),
                "by": "line",
            }
            if action.target is not None:
                arguments.update(self._action_target_arguments(action))
            await self._call_action("scroll", arguments)
            return
        if action.type is ComputerActionType.DRAG:
            if action.start is None or action.end is None:
                raise ValueError("drag requires start and end points")
            from_x, from_y = self._point_pixels(action.start)
            to_x, to_y = self._point_pixels(action.end)
            await self._call_action(
                "drag",
                {
                    **common,
                    "from_x": from_x,
                    "from_y": from_y,
                    "to_x": to_x,
                    "to_y": to_y,
                    "duration_ms": action.duration_ms or 500,
                    "steps": max(
                        1,
                        min(50, (action.duration_ms or 500) // 25),
                    ),
                },
            )
            return
        raise ValueError(f"unsupported native browser action: {action.type.value}")

    async def _navigate(self, raw_url: str) -> None:
        url = self._validate_navigation_url(raw_url)
        target = self._require_target()
        common = {
            "session": self.session_id,
            "pid": target.pid,
            "window_id": target.window_id,
            "delivery_mode": "foreground",
        }
        await self._call_action(
            "hotkey",
            {**common, "keys": [self._primary_driver_modifier(), "l"]},
            remember=False,
        )
        await self._call_action(
            "type_text",
            {**common, "text": url},
            remember=False,
        )
        await self._call_action(
            "press_key",
            {**common, "key": "return"},
        )

    async def _call_action(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        remember: bool = True,
    ) -> CuaDriverResult:
        result = await self._get_driver().call_tool(name, arguments)
        status = str(result.structured.get("status") or "").strip().lower()
        if status in {"refused", "failed", "error"}:
            refusal = result.structured.get("refusal")
            detail = (
                refusal.get("message")
                if isinstance(refusal, Mapping)
                else result.structured.get("message")
            )
            raise CuaDriverError(
                str(detail or result.text or f"cua-driver {name} was {status}")
            )
        if remember:
            metadata = {
                field: result.structured[field]
                for field in _ACTION_RESULT_FIELDS
                if field in result.structured
            }
            if result.text:
                metadata["summary"] = result.text[:500]
            self._last_action_result = metadata or {
                "effect": "unverifiable",
                "verified": False,
            }
        return result

    def _action_target_arguments(self, action: ComputerAction) -> dict[str, Any]:
        target = action.target
        if target is None:
            return {}
        if target.element_id is not None:
            element = self._find_element(target.element_id)
            token = str(element.metadata.get("element_token") or "").strip()
            if token:
                return {"element_token": token}
            index = element.metadata.get("element_index")
            if isinstance(index, int):
                return {"element_index": index}
        x, y = self._action_point_pixels(action)
        return {"x": x, "y": y}

    def _action_point_pixels(self, action: ComputerAction) -> tuple[float, float]:
        target = action.target
        if target is None:
            raise ValueError(f"{action.type.value} requires a target")
        if target.point is not None:
            return self._point_pixels(target.point)
        element = self._find_element(target.element_id or "")
        return self._point_pixels(
            NormalizedPoint(
                x=element.bounds.x + element.bounds.width / 2,
                y=element.bounds.y + element.bounds.height / 2,
            )
        )

    def _point_pixels(self, point: NormalizedPoint) -> tuple[float, float]:
        observation = self.current_observation
        if observation is None:
            raise RuntimeError("native browser action requires a current observation")
        return (
            point.x * observation.viewport.width,
            point.y * observation.viewport.height,
        )

    def _find_element(self, element_id: str) -> ComputerElement:
        observation = self.current_observation
        if observation is None:
            raise RuntimeError("element target requires a current observation")
        element = next(
            (item for item in observation.elements if item.element_id == element_id),
            None,
        )
        if element is None:
            raise ComputerTargetNotFoundError(
                f"element {element_id!r} is not present in frame "
                f"{observation.frame_id!r}"
            )
        return element

    def _require_target(self) -> NativeBrowserWindow:
        if self._target is None:
            raise RuntimeError("native browser window has not been selected")
        return self._target

    def _validate_navigation_url(self, raw_url: str) -> str:
        url = raw_url.strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
            raise ValueError("native browser navigation requires an absolute HTTP URL")
        reason = _navigation_block_reason(
            url,
            allowlist=self.navigation_allowlist,
            denylist=self.navigation_denylist,
        )
        if reason is not None:
            raise ValueError(reason)
        return url

    def _delivery_mode(self, action: ComputerAction) -> str:
        requested = str(action.metadata.get("delivery_mode") or "").strip().lower()
        if requested != "foreground":
            return "background"
        observation = self.current_observation
        metadata = observation.metadata if observation is not None else {}
        last_result = metadata.get("last_action_result")
        escalations = [
            last_result.get("escalation") if isinstance(last_result, Mapping) else None,
            metadata.get("driver_escalation"),
        ]
        for escalation in escalations:
            if (
                isinstance(escalation, Mapping)
                and str(escalation.get("recommended") or "").strip().lower()
                == "foreground"
            ):
                return "foreground"
        raise ValueError(
            "foreground delivery requires a cua-driver escalation recommendation "
            "from the current observation"
        )

    @staticmethod
    def _driver_key(key: str) -> str:
        normalized = key.strip().lower()
        aliases = {
            "meta": "cmd",
            "command": "cmd",
            "alt": "option",
            "enter": "return",
            "esc": "escape",
            "arrowup": "up",
            "arrowdown": "down",
            "arrowleft": "left",
            "arrowright": "right",
            "page_up": "pageup",
            "page_down": "pagedown",
        }
        return aliases.get(normalized, normalized)

    @staticmethod
    def _primary_driver_modifier() -> str:
        if host_computer_input_platform() is ComputerInputPlatform.MACOS:
            return "cmd"
        return "ctrl"

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


def _normalize_host_patterns(patterns: Sequence[str] | None) -> tuple[str, ...]:
    if not patterns:
        return ()
    return tuple(
        dict.fromkeys(
            normalized
            for pattern in patterns
            if (normalized := str(pattern).strip().lower().lstrip("."))
        )
    )


def _host_matches(host: str, patterns: Sequence[str]) -> bool:
    candidate = host.strip().lower().rstrip(".")
    return any(
        candidate == pattern or candidate.endswith(f".{pattern}")
        for pattern in patterns
    )


def _navigation_block_reason(
    raw_url: str,
    *,
    allowlist: Sequence[str],
    denylist: Sequence[str],
) -> str | None:
    host = urlsplit(raw_url).hostname
    if host is None:
        return "Native browser navigation requires a network host."
    if _host_matches(host, denylist):
        return f"Navigation to {host} is blocked by the configured policy."
    if allowlist and not _host_matches(host, allowlist):
        return f"Navigation to {host} is outside the configured allowlist."
    return None


def _optional_active_url(structured: Mapping[str, Any]) -> str | None:
    for key in ("active_url", "url"):
        value = structured.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized.startswith(("http://", "https://", "about:")):
            return normalized
    return None
