"""POST /route endpoint."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from route_agent.api.dependencies import get_api_settings
from route_agent.api.schemas import (
    RouteRequestBody,
    RouteResponse,
)
from route_agent.app.payloads import build_empty_task_payload
from route_agent.app.service import run_route_agent

logger = logging.getLogger(__name__)

router = APIRouter()
EMPTY_TASK_FALLBACK_MODEL = "deepseek:deepseek-chat"


def _is_empty_task(task: str | None) -> bool:
    """Return `True` when the request task is empty or whitespace-only."""
    return not (task or "").strip()


def _resolve_empty_task_model() -> str:
    """Return the fixed fallback model used when the task is empty."""
    return EMPTY_TASK_FALLBACK_MODEL


async def _call_service(body: RouteRequestBody) -> dict[str, Any]:
    """Convert API request bodies into app requests and invoke the service."""
    settings = get_api_settings()
    request = body.to_app_request(default_agent_name=settings.agent_name)
    return await asyncio.to_thread(run_route_agent, request, options=settings.to_run_options())


@router.post("/route", response_model=RouteResponse)
async def route_task(body: RouteRequestBody) -> RouteResponse:
    """Route a task and return the full routing decision."""
    if _is_empty_task(body.task):
        fast_llm = _resolve_empty_task_model()
        logger.warning("empty task received from agent=%s, using fallback model=%s", body.agent_name, fast_llm)
        return RouteResponse.model_validate(build_empty_task_payload(fast_llm))

    try:
        payload = await _call_service(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("route_task failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return RouteResponse.model_validate(payload)
