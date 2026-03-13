"""Monitoring-side event helpers for route decisions."""

from __future__ import annotations

import logging
from typing import Any

from route_agent.router_engine.schemas import RouteDecision
from route_agent.task_analyzer.schemas import TaskAnalysisResult

logger = logging.getLogger(__name__)


def provider_from_model_id(model_id: str | None) -> str | None:
    """Extract the provider prefix from a `provider:model_name` model id."""
    if not model_id:
        return None
    raw = str(model_id).strip()
    if ":" not in raw:
        return None
    provider = raw.split(":", 1)[0].strip()
    return provider or None


def estimate_analysis_complexity(analysis_result: TaskAnalysisResult) -> float | None:
    """Estimate a normalized complexity score from analysis dimensions."""
    dimensions = analysis_result.relevant_dimensions
    if not dimensions:
        return None
    value = sum(float(item.score) for item in dimensions) / (10.0 * len(dimensions))
    return max(0.0, min(value, 1.0))


def build_route_monitoring_event(
    *,
    agent_name: str,
    decision: RouteDecision,
    analysis_result: TaskAnalysisResult,
    report: Any,
    local_pool_result: Any,
    rate_limiter_status: dict[str, Any],
) -> dict[str, Any]:
    """Build the monitoring event emitted for one route decision."""
    return {
        "source": "main",
        "agent_name": agent_name,
        "model_used": decision.primary_model,
        "selected_tier": None,
        "provider": provider_from_model_id(decision.primary_model),
        "routing_reason": decision.reason,
        "pool_hit": decision.pool_hit,
        "pool_class": decision.pool_class,
        "analysis_domain": analysis_result.domain,
        "analysis_complexity": estimate_analysis_complexity(analysis_result),
        "registry_error_count": len(getattr(report, "errors", {}) or {}),
        "skipped_provider_count": len(getattr(report, "skipped_providers", []) or []),
        "metadata": {
            "class_source": decision.class_source,
            "start_index": decision.start_index,
            "alerts": list(decision.alerts),
            "default_used": decision.default_used,
            "sync_source": getattr(local_pool_result, "source", None),
            "sync_performed": bool(getattr(local_pool_result, "sync_performed", False)),
            "snapshot_version": getattr(local_pool_result, "snapshot_version", None),
            "rate_limiter_mode": rate_limiter_status.get("mode"),
            "rate_limiter_fail_strategy": rate_limiter_status.get("fail_strategy"),
        },
    }


def record_route_decision(event: dict[str, Any]) -> None:
    """Persist a route-monitoring event in best-effort mode."""
    try:
        from route_agent.monitoring import record_decision

        record_decision(event)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to record monitoring decision event: %s", exc)
