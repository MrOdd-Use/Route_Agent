"""Core application orchestration for one route decision."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from typing import Any

from route_agent.app.analysis import resolve_task_analysis
from route_agent.app.contracts import RouteAgentRequest, RouteAgentRunOptions
from route_agent.app.monitoring import build_route_monitoring_event, record_route_decision
from route_agent.app.registry import build_registry_context
from route_agent.app.wiring import get_analysis_storage, get_engine
from route_agent.router_engine.schemas import RouteDecision
from route_agent.task_analyzer.schemas import TaskAnalysisResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteAgentExecution:
    """Resolved route-decision execution context used by response builders."""

    request: RouteAgentRequest
    decision: RouteDecision
    analysis_result: TaskAnalysisResult
    report: Any
    local_pool_result: Any
    pool: Any
    record_id: int | None
    used_legacy_fallback: bool
    rate_limiter_status: dict[str, Any]


def _resolve_router_runtime(
    options: RouteAgentRunOptions,
) -> tuple[str | None, str | None, str, str]:
    """Resolve router runtime settings after env files have been loaded."""
    redis_url = options.redis_url or (os.getenv("REDIS_URL") or "").strip() or None
    router_db_path = options.router_db_path or (os.getenv("ROUTER_DB_PATH") or "").strip() or None
    rate_limit_mode = options.rate_limit_mode or (os.getenv("RATE_LIMIT_MODE") or "").strip().lower() or "auto"
    rate_limit_fail_strategy = (
        options.rate_limit_fail_strategy
        or (os.getenv("RATE_LIMIT_FAIL_STRATEGY") or "").strip().lower()
        or "degrade"
    )
    return redis_url, router_db_path, rate_limit_mode, rate_limit_fail_strategy


def _persist_routed_model(
    *,
    analysis_storage: Any,
    record_id: int | None,
    routed_model: str | None,
) -> None:
    """Persist the selected routed model when an analysis record exists."""
    if record_id is None or not routed_model:
        return

    try:
        analysis_storage.update_routed_model(record_id, routed_model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to persist routed_model for record_id=%s: %s", record_id, exc)


def execute_route(request: RouteAgentRequest, options: RouteAgentRunOptions) -> RouteAgentExecution:
    """Execute the end-to-end routing flow for one normalized request."""
    registry = build_registry_context(options)
    analysis = resolve_task_analysis(request)
    analysis_storage = get_analysis_storage(options.analysis_db_path)
    redis_url, router_db_path, rate_limit_mode, rate_limit_fail_strategy = _resolve_router_runtime(options)

    engine = get_engine(
        registry.pool,
        analysis_storage,
        redis_url=redis_url,
        router_db_path=router_db_path,
        rate_limit_mode=rate_limit_mode,
        rate_limit_fail_strategy=rate_limit_fail_strategy,
    )
    decision = engine.route(
        request.to_route_request(
            analysis_result=analysis.result,
            record_id=analysis.record_id,
        )
    )
    rate_limiter_status = engine.rate_limiter_status()

    _persist_routed_model(
        analysis_storage=analysis_storage,
        record_id=analysis.record_id,
        routed_model=decision.primary_model,
    )
    record_route_decision(
        build_route_monitoring_event(
            agent_name=request.agent_name,
            decision=decision,
            analysis_result=analysis.result,
            report=registry.report,
            local_pool_result=registry.local_pool_result,
            rate_limiter_status=rate_limiter_status,
        )
    )

    return RouteAgentExecution(
        request=request,
        decision=decision,
        analysis_result=analysis.result,
        report=registry.report,
        local_pool_result=registry.local_pool_result,
        pool=registry.pool,
        record_id=analysis.record_id,
        used_legacy_fallback=analysis.used_legacy_fallback,
        rate_limiter_status=rate_limiter_status,
    )
