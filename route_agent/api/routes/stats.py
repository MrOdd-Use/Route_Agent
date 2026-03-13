"""GET /stats endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from route_agent.api.schemas import StatsResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/stats", response_model=StatsResponse)
async def get_stats(windows: str = Query(default="24h,7d,all")) -> StatsResponse:
    """Return routing statistics from the monitoring service."""
    try:
        from route_agent.monitoring import get_stats_async

        result = await get_stats_async(windows=tuple(w.strip() for w in windows.split(",")))
        return StatsResponse(windows=result)
    except Exception as exc:
        logger.warning("get_stats failed: %s", exc)
        return StatsResponse(windows={})
