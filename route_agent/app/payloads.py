"""Payload builders for route responses."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from route_agent.router_engine.schemas import RouteDecision
from route_agent.task_analyzer.schemas import TaskAnalysisResult


def build_route_payload(
    *,
    decision: RouteDecision,
    analysis_result: TaskAnalysisResult,
    report: Any,
    local_pool_result: Any,
    pool: Any,
    record_id: int | None,
    used_legacy_fallback: bool,
    sync_interval_days: int,
    rate_limiter_status: dict[str, Any],
) -> dict[str, Any]:
    """Execute `build_route_payload`."""
    routing_reason = decision.reason
    if decision.primary_model is None:
        routing_reason = f"{routing_reason}; registry_errors={report.errors}"

    return {
        "result": None,
        "model_used": decision.primary_model,
        "cost": None,
        "routing_reason": routing_reason,
        "analysis": analysis_result.to_dict(),
        "candidates": [asdict(candidate) for candidate in decision.candidates],
        "start_index": decision.start_index,
        "alerts": list(decision.alerts),
        "default_used": decision.default_used,
        "pool_hit": decision.pool_hit,
        "pool_class": decision.pool_class,
        "class_source": decision.class_source,
        "selected_tier": None,
        "pool_summary": pool.summary(),
        "registry_alerts": list(report.alerts),
        "registry_errors": dict(report.errors),
        "skipped_providers": [item.to_dict() for item in report.skipped_providers],
        "analysis_record_id": record_id,
        "analysis_fallback": used_legacy_fallback,
        "rate_limiter": rate_limiter_status,
        "registry_sync": {
            "source": local_pool_result.source,
            "storage_backend": local_pool_result.storage_backend,
            "sync_due": local_pool_result.sync_due,
            "sync_performed": local_pool_result.sync_performed,
            "snapshot_version": local_pool_result.snapshot_version,
            "sync_interval_days": sync_interval_days,
        },
    }
