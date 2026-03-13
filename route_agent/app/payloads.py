"""Payload builders for application responses."""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from route_agent.app.orchestrator import RouteAgentExecution


def build_route_payload(
    *,
    execution: RouteAgentExecution,
    sync_interval_days: int,
) -> dict[str, Any]:
    """Build the unified payload returned by CLI and API flows."""
    decision = execution.decision
    report = execution.report
    routing_reason = decision.reason
    if decision.primary_model is None:
        routing_reason = f"{routing_reason}; registry_errors={report.errors}"

    return {
        "result": None,
        "model_used": decision.primary_model,
        "cost": None,
        "routing_reason": routing_reason,
        "analysis": execution.analysis_result.to_dict(),
        "candidates": [asdict(candidate) for candidate in decision.candidates],
        "start_index": decision.start_index,
        "alerts": list(decision.alerts),
        "default_used": decision.default_used,
        "pool_hit": decision.pool_hit,
        "pool_class": decision.pool_class,
        "class_source": decision.class_source,
        "pool_summary": execution.pool.summary(),
        "registry_alerts": list(report.alerts),
        "registry_errors": dict(report.errors),
        "skipped_providers": [item.to_dict() for item in report.skipped_providers],
        "analysis_record_id": execution.record_id,
        "analysis_fallback": execution.used_legacy_fallback,
        "rate_limiter": execution.rate_limiter_status,
        "registry_sync": {
            "source": execution.local_pool_result.source,
            "storage_backend": execution.local_pool_result.storage_backend,
            "sync_due": execution.local_pool_result.sync_due,
            "sync_performed": execution.local_pool_result.sync_performed,
            "snapshot_version": execution.local_pool_result.snapshot_version,
            "sync_interval_days": sync_interval_days,
        },
    }


def build_empty_task_payload(fast_llm: str) -> dict[str, Any]:
    """Build the minimal fallback payload used for empty tasks."""
    return {
        "result": None,
        "model_used": fast_llm,
        "cost": None,
        "routing_reason": "empty_task_fallback",
        "analysis": {
            "domain": "unknown",
            "domain_description": "No task provided",
            "relevant_dimensions": [],
            "task_class": None,
        },
        "candidates": [],
        "start_index": 0,
        "alerts": ["empty task: using fallback model"],
        "default_used": True,
        "pool_hit": False,
        "pool_class": None,
        "class_source": "fallback",
        "pool_summary": {},
        "registry_alerts": [],
        "registry_errors": {},
        "skipped_providers": [],
        "analysis_record_id": None,
        "analysis_fallback": True,
        "rate_limiter": {},
        "registry_sync": {},
    }
