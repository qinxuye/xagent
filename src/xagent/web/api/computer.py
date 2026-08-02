from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ...config import get_local_computer_enabled
from ...core.computer.native_browser_readiness import (
    LocalComputerReadiness,
    LocalComputerReadinessIssue,
    get_local_computer_readiness,
)
from ..models.user import User
from .auth import get_current_user

computer_router = APIRouter(prefix="/api/computer", tags=["computer"])


@computer_router.get(
    "/local-computer/readiness",
    response_model=LocalComputerReadiness,
)
@computer_router.get(
    "/local-browser/readiness",
    response_model=LocalComputerReadiness,
    include_in_schema=False,
)
async def get_local_computer_readiness_endpoint(
    response: Response,
    user: User = Depends(get_current_user),
) -> LocalComputerReadiness:
    """Return whether this administrator can control a local host window."""

    response.headers["Cache-Control"] = "no-store"
    if not get_local_computer_enabled():
        issue = LocalComputerReadinessIssue(
            code="disabled",
            message="Local computer is disabled on this Xagent host.",
        )
        return LocalComputerReadiness(
            ready=False,
            connected=False,
            attached=False,
            issues=[issue],
            message=issue.message,
        )
    if not bool(user.is_admin):
        issue = LocalComputerReadinessIssue(
            code="not_authorized",
            message=(
                "Local computer is restricted to Xagent administrators because "
                "it controls applications on the backend host."
            ),
        )
        return LocalComputerReadiness(
            ready=False,
            connected=False,
            attached=False,
            issues=[issue],
            message=issue.message,
        )
    return await get_local_computer_readiness()


# Compatibility for tests and integrations using the preview function name.
get_local_browser_readiness = get_local_computer_readiness_endpoint
