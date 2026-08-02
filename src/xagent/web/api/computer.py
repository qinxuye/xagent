from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...config import get_native_browser_app_name, get_native_browser_enabled
from ...core.computer.native_browser_readiness import (
    NativeBrowserReadiness,
    NativeBrowserReadinessIssue,
    get_native_browser_readiness,
)
from ..models.user import User
from .auth import get_current_user

computer_router = APIRouter(prefix="/api/computer", tags=["computer"])


@computer_router.get(
    "/local-browser/readiness",
    response_model=NativeBrowserReadiness,
)
async def get_local_browser_readiness(
    response: Response,
    user: User = Depends(get_current_user),
) -> NativeBrowserReadiness:
    """Return whether this administrator can use the host's local browser."""

    response.headers["Cache-Control"] = "no-store"
    application = get_native_browser_app_name()
    if not get_native_browser_enabled():
        issue = NativeBrowserReadinessIssue(
            code="disabled",
            message="Local browser is disabled on this Xagent host.",
        )
        return NativeBrowserReadiness(
            ready=False,
            connected=False,
            attached=False,
            application=application,
            issues=[issue],
            message=issue.message,
        )
    if not bool(user.is_admin):
        issue = NativeBrowserReadinessIssue(
            code="not_authorized",
            message=(
                "Local browser is restricted to Xagent administrators because "
                "it controls a browser on the backend host."
            ),
        )
        return NativeBrowserReadiness(
            ready=False,
            connected=False,
            attached=False,
            application=application,
            issues=[issue],
            message=issue.message,
        )
    return await get_native_browser_readiness()
