"""Monitoring endpoints: decisions, executions."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from route_agent.api.schemas import (
    DecisionsResponse,
    ExecutionEndBody,
    ExecutionEndResponse,
    ExecutionStartBody,
    ExecutionStartResponse,
    ExecutionsResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/decisions", response_model=DecisionsResponse)
async def get_decisions(
    limit: int = Query(default=50, ge=1, le=500),
    source: str | None = Query(default=None),
    since_hours: int | None = Query(default=None, ge=1),
) -> DecisionsResponse:
    """Return recent routing decisions."""
    try:
        from route_agent.monitoring import get_recent_decisions_async

        items = await get_recent_decisions_async(limit=limit, source=source, since_hours=since_hours)
        return DecisionsResponse(decisions=items, count=len(items))
    except Exception as exc:
        logger.warning("get_decisions failed: %s", exc)
        return DecisionsResponse(decisions=[], count=0)


@router.post("/executions/start", response_model=ExecutionStartResponse)
async def start_execution(body: ExecutionStartBody) -> ExecutionStartResponse:
    """Start execution tracking."""
    try:
        from route_agent.monitoring import start_execution_async

        execution_id = await start_execution_async(body.model_dump())
        return ExecutionStartResponse(execution_id=execution_id)
    except Exception as exc:
        logger.exception("start_execution failed")
        return ExecutionStartResponse(execution_id="")


@router.post("/executions/end", response_model=ExecutionEndResponse)
async def end_execution(body: ExecutionEndBody) -> ExecutionEndResponse:
    """End execution tracking."""
    try:
        from route_agent.monitoring import end_execution_async

        success = await end_execution_async(body.model_dump())
        return ExecutionEndResponse(success=success)
    except Exception as exc:
        logger.exception("end_execution failed")
        return ExecutionEndResponse(success=False)


@router.get("/executions", response_model=ExecutionsResponse)
async def get_executions(
    limit: int = Query(default=200, ge=1, le=1000),
    source: str | None = Query(default=None),
    status: str | None = Query(default=None),
    since_hours: int | None = Query(default=None, ge=1),
) -> ExecutionsResponse:
    """Return recent executions."""
    try:
        from route_agent.monitoring import get_recent_executions_async

        items = await get_recent_executions_async(
            limit=limit, source=source, status=status, since_hours=since_hours,
        )
        return ExecutionsResponse(executions=items, count=len(items))
    except Exception as exc:
        logger.warning("get_executions failed: %s", exc)
        return ExecutionsResponse(executions=[], count=0)
