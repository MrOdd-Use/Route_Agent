"""Health check endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter

from route_agent.api.schemas import HealthResponse

router = APIRouter()


def _get_version() -> str:
    """Execute `_get_version`."""
    try:
        from importlib.metadata import version
        return version("route_agent")
    except Exception:
        return "unknown"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Execute `health`."""
    monitoring_enabled = os.getenv("ROUTE_AGENT_MONITORING_ENABLED", "false").lower() in ("1", "true", "yes")
    return HealthResponse(
        status="ok",
        version=_get_version(),
        monitoring_enabled=monitoring_enabled,
    )
