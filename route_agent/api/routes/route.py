"""POST /route and POST /suggest endpoints."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException

from route_agent.api.dependencies import get_api_settings
from route_agent.api.schemas import (
    RouteRequestBody,
    RouteResponse,
    SuggestRequestBody,
    SuggestResponse,
)
from route_agent.app.payloads import build_empty_task_payload
from route_agent.app.service import run_route_agent

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_empty_task(task: str | None) -> bool:
    """Return `True` when the request task is empty or whitespace-only."""
    return not (task or "").strip()


def _resolve_empty_task_model() -> str:
    """Resolve the fallback model used when the task is empty."""
    return (os.getenv("FAST_LLM") or "").strip() or "deepseek:deepseek-chat"


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


def _estimate_confidence(analysis: dict[str, Any]) -> float:
    """Compute a confidence proxy from analysis dimension scores."""
    dims = analysis.get("relevant_dimensions") or []
    if not dims:
        return 0.0
    total = sum(float(d.get("score", 0)) for d in dims)
    return max(0.0, min(total / (10.0 * len(dims)), 1.0))


@router.post("/suggest", response_model=SuggestResponse)
async def suggest_model(body: SuggestRequestBody) -> SuggestResponse:
    """Suggest a model without execution."""
    if _is_empty_task(body.task):
        fast_llm = _resolve_empty_task_model()
        logger.warning("empty task in suggest from agent=%s, using fallback model=%s", body.agent_name, fast_llm)
        return SuggestResponse(
            suggested_model=fast_llm,
            confidence=0.0,
            reason="empty_task_fallback",
            analysis={"domain": "unknown"},
        )

    settings = get_api_settings()
    route_body = RouteRequestBody(
        task=body.task,
        agent_name=body.agent_name,
        request_id=body.request_id,
    )
    try:
        payload = await _call_service(route_body)
    except Exception as exc:
        logger.exception("suggest_model failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    analysis = payload.get("analysis", {})
    return SuggestResponse(
        suggested_model=payload.get("model_used"),
        confidence=_estimate_confidence(analysis),
        reason=payload.get("routing_reason", ""),
        analysis=analysis,
    )
