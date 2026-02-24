"""Application service entrypoint for one route decision."""

from __future__ import annotations

import logging
import os
from typing import Any
import uuid

from route_agent.app.legacy_analysis import build_legacy_analysis, detect_task_type, estimate_complexity
from route_agent.app.payloads import build_route_payload
from route_agent.app.wiring import analyze_task, get_analysis_storage, get_engine
from route_agent.model_registry import MainModelPool, get_model_registry_report_with_local_pool
from route_agent.router_engine import RouteConstraints, RouteRequest

logger = logging.getLogger(__name__)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_model_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(item for item in (_as_text(part) for part in value) if item)
    return ()


def run_route_agent(
    request: dict[str, Any],
    *,
    limit: int = 8,
    include_ollama: bool = False,
    min_total_threshold: int = 5,
    load_env_file: bool = True,
    db_backend: str = "sqlite",
    sqlite_path: str | None = None,
    postgres_dsn: str | None = None,
    sync_interval_days: int = 30,
    force_registry_sync: bool = False,
    keep_registry_history: int = 2,
    agent_name: str = "route_agent",
    redis_url: str | None = None,
    router_db_path: str | None = None,
    rate_limit_mode: str | None = None,
    rate_limit_fail_strategy: str | None = None,
    analysis_db_path: str | None = None,
) -> dict[str, Any]:
    task = str(request.get("task") or "").strip()
    if not task:
        raise ValueError("request.task is required")
    system_prompt = str(request.get("system_prompt") or "")

    constraints = request.get("constraints") or {}
    if not isinstance(constraints, dict):
        constraints = {}
    request_id = _as_text(request.get("request_id")) or str(uuid.uuid4())
    resolved_agent_name = _as_text(request.get("agent_name")) or _as_text(agent_name) or "route_agent"

    local_pool_result = get_model_registry_report_with_local_pool(
        limit=limit,
        include_ollama=include_ollama,
        min_total_threshold=min_total_threshold,
        load_env_file=load_env_file,
        db_backend=db_backend,
        sqlite_path=sqlite_path,
        postgres_dsn=postgres_dsn,
        sync_interval_days=sync_interval_days,
        keep_history=keep_registry_history,
        force_sync=force_registry_sync,
    )
    report = local_pool_result.report
    pool = MainModelPool.from_report(
        report,
        fast_model=(os.getenv("FAST_LLM") or "").strip() or None,
        smart_model=(os.getenv("SMART_LLM") or "").strip() or None,
        strategic_model=(os.getenv("STRATEGIC_LLM") or "").strip() or None,
    )

    max_cost = _as_float(constraints.get("max_cost"))
    preferred_model = _as_text(constraints.get("preferred_model"))
    exclude_models = _as_model_list(constraints.get("exclude_models"))
    require_provider = _as_text(constraints.get("require_provider"))
    estimated_input_tokens = _as_int(constraints.get("estimated_input_tokens"))

    used_legacy_fallback = False
    record_id: int | None
    try:
        analysis_result, record_id = analyze_task(agent_name=resolved_agent_name, task_prompt=task)
    except Exception as exc:  # noqa: BLE001
        used_legacy_fallback = True
        logger.warning("task_analyzer failed, fallback to legacy analysis: %s", exc)
        task_type = detect_task_type(task)
        complexity = estimate_complexity(task, task_type)
        analysis_result = build_legacy_analysis(task_type, complexity)
        record_id = None

    analysis_storage = get_analysis_storage(analysis_db_path)
    resolved_redis_url = _as_text(redis_url) or _as_text(os.getenv("REDIS_URL"))
    resolved_router_db_path = _as_text(router_db_path) or _as_text(os.getenv("ROUTER_DB_PATH"))
    resolved_rate_limit_mode = _as_text(rate_limit_mode) or _as_text(os.getenv("RATE_LIMIT_MODE")) or "auto"
    resolved_rate_limit_fail_strategy = (
        _as_text(rate_limit_fail_strategy)
        or _as_text(os.getenv("RATE_LIMIT_FAIL_STRATEGY"))
        or "degrade"
    )

    route_request = RouteRequest(
        agent_name=resolved_agent_name,
        agent_class=_as_text(request.get("agent_class")),
        request_id=request_id,
        system_prompt=system_prompt,
        task_prompt=task,
        analysis=analysis_result,
        record_id=record_id,
        constraints=RouteConstraints(
            max_cost=max_cost,
            preferred_model=preferred_model,
            exclude_models=exclude_models,
            require_provider=require_provider,
            estimated_input_tokens=estimated_input_tokens,
        ),
    )

    engine = get_engine(
        pool,
        analysis_storage,
        redis_url=resolved_redis_url,
        router_db_path=resolved_router_db_path,
        rate_limit_mode=resolved_rate_limit_mode,
        rate_limit_fail_strategy=resolved_rate_limit_fail_strategy,
    )
    decision = engine.route(route_request)

    if record_id is not None and decision.primary_model:
        try:
            analysis_storage.update_routed_model(record_id, decision.primary_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to persist routed_model for record_id=%s: %s", record_id, exc)

    return build_route_payload(
        decision=decision,
        analysis_result=analysis_result,
        report=report,
        local_pool_result=local_pool_result,
        pool=pool,
        record_id=record_id,
        used_legacy_fallback=used_legacy_fallback,
        sync_interval_days=sync_interval_days,
        rate_limiter_status=engine.rate_limiter_status(),
    )
