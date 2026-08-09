from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from xagent.core.computer.native_browser import NativeBrowserWindow
from xagent.core.computer.native_navigation import (
    MacOSChromiumNavigator,
    NativeBrowserNavigationError,
)


def target() -> NativeBrowserWindow:
    return NativeBrowserWindow(
        pid=83366,
        window_id=46049,
        app_name="Google Chrome",
        title="New Tab",
        x=1512,
        y=30,
        width=2560,
        height=1410,
        z_index=10,
        is_on_screen=True,
        on_current_space=True,
    )


@pytest.mark.asyncio
async def test_macos_chromium_navigate_maps_and_rechecks_exact_window() -> None:
    calls: list[list[str]] = []
    scripts: list[str] = []

    async def runner(script: str, arguments: list[str]) -> Mapping[str, Any]:
        scripts.append(script)
        calls.append(arguments)
        if len(calls) == 1:
            return {
                "bundle_id": "com.google.Chrome",
                "windows": [
                    {
                        "id": 101,
                        "title": "Another Window",
                        "bounds": [1512, 30, 4072, 1440],
                    },
                    {
                        "id": 202,
                        "title": "New Tab",
                        "bounds": [1512, 30, 4072, 1440],
                    },
                ],
            }
        return {
            "bundle_id": "com.google.Chrome",
            "browser_window_id": 202,
            "actual_url": "https://www.zhihu.com/people/qin-xu-ye",
        }

    navigator = MacOSChromiumNavigator(runner=runner, platform="darwin")
    result = await navigator.navigate(
        target(),
        "https://www.zhihu.com/people/qin-xu-ye",
    )

    assert result.route == "macos_chromium_apple_events"
    assert result.browser_window_id == 202
    assert calls == [
        ["83366"],
        [
            "83366",
            "202",
            "New Tab",
            "[1512,30,4072,1440]",
            "https://www.zhihu.com/people/qin-xu-ye",
        ],
    ]
    assert "while (Boolean(tab.loading())" in scripts[1]


@pytest.mark.asyncio
async def test_macos_chromium_navigate_refuses_ambiguous_mapping() -> None:
    call_count = 0

    async def runner(script: str, arguments: list[str]) -> Mapping[str, Any]:
        nonlocal call_count
        del script, arguments
        call_count += 1
        return {
            "bundle_id": "com.google.Chrome",
            "windows": [
                {
                    "id": 101,
                    "title": "New Tab",
                    "bounds": [1512, 30, 4072, 1440],
                },
                {
                    "id": 202,
                    "title": "New Tab",
                    "bounds": [1512, 30, 4072, 1440],
                },
            ],
        }

    navigator = MacOSChromiumNavigator(runner=runner, platform="darwin")
    with pytest.raises(NativeBrowserNavigationError, match="mapping is ambiguous"):
        await navigator.navigate(target(), "https://www.zhihu.com")

    assert call_count == 1


def test_macos_chromium_navigate_rejects_other_platforms_and_apps() -> None:
    linux = MacOSChromiumNavigator(platform="linux")
    assert linux.supports(target()) is False

    non_browser = NativeBrowserWindow(
        **{**target().__dict__, "app_name": "Music"},
    )
    macos = MacOSChromiumNavigator(platform="darwin")
    assert macos.supports(non_browser) is False
