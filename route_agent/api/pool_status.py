"""Helpers for pool-status API endpoints and dashboard views."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from route_agent.app.monitoring import provider_from_model_id

_UNAVAILABLE_STATUSES = {"down", "unavailable", "error", "offline"}


def _as_text(value: Any) -> str | None:
    """Normalize a loose value to a nullable non-empty string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value: Any) -> float | None:
    """Convert a loose value to float when possible."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _metric_by_model(metrics: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index aggregated execution metrics by model id."""
    indexed: dict[str, dict[str, Any]] = {}
    for item in metrics:
        model_id = _as_text(item.get("model_id"))
        if not model_id:
            continue
        request_count = max(0, int(item.get("request_count") or 0))
        success_count = max(0, int(item.get("success_count") or 0))
        success_rate = (success_count / request_count) if request_count else None
        indexed[model_id] = {
            "provider": _as_text(item.get("provider")),
            "request_count": request_count,
            "success_count": success_count,
            "success_rate": success_rate,
            "avg_latency_ms": _safe_float(item.get("avg_latency_ms")),
            "last_used_at": _as_text(item.get("last_used_at")),
        }
    return indexed


def _latest_history_by_model(history_rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Keep the latest known history row for each model id."""
    latest: dict[str, dict[str, Any]] = {}
    for row in history_rows:
        model_id = _as_text(row.get("model_id"))
        if not model_id or model_id in latest:
            continue
        latest[model_id] = {
            "last_outcome": _as_text(row.get("outcome")),
            "last_outcome_at": _as_text(row.get("created_at")),
        }
    return latest


def _global_card_status(registry_availability: str | None, success_rate: float | None, request_count: int) -> tuple[str, str]:
    """Classify one global-pool card from availability and execution metrics."""
    availability = (registry_availability or "").strip().lower()
    if availability in _UNAVAILABLE_STATUSES:
        return "unavailable", f"Registry availability is '{availability}'."
    if request_count <= 0 or success_rate is None:
        return "idle", "No execution data yet."
    if success_rate >= 0.8:
        return "healthy", f"Execution success rate is {success_rate * 100:.1f}%."
    if success_rate >= 0.5:
        return "warning", f"Execution success rate is {success_rate * 100:.1f}%."
    return "degraded", f"Execution success rate is {success_rate * 100:.1f}%."


def _class_card_status(
    *,
    success_count: int,
    fail_count: int,
    success_rate: float | None,
    last_outcome: str | None,
) -> tuple[str, str]:
    """Classify one class-pool card from class stats and recent outcomes."""
    total = max(0, int(success_count)) + max(0, int(fail_count))
    if total <= 0 or success_rate is None:
        if last_outcome:
            return "idle", f"Latest class outcome is '{last_outcome}', but no stable pool stats exist yet."
        return "idle", "No class-pool stats yet."
    if success_rate >= 0.8:
        return "healthy", f"Class success rate is {success_rate * 100:.1f}% across {total} trials."
    if success_rate >= 0.5:
        reason = f"Class success rate is {success_rate * 100:.1f}% across {total} trials."
        if last_outcome:
            reason += f" Latest outcome: {last_outcome}."
        return "warning", reason
    reason = f"Class success rate is {success_rate * 100:.1f}% across {total} trials."
    if last_outcome:
        reason += f" Latest outcome: {last_outcome}."
    return "degraded", reason


def build_global_pool_status_payload(
    *,
    models: Iterable[Any],
    execution_metrics: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the global-pool status payload used by the dashboard."""
    metrics_by_model = _metric_by_model(execution_metrics)
    cards: list[dict[str, Any]] = []
    available_models = 0

    for model in models:
        model_id = _as_text(getattr(model, "model_id", None))
        if not model_id:
            continue
        display_name = _as_text(getattr(model, "display_name", None)) or model_id
        provider = _as_text(getattr(model, "provider", None)) or provider_from_model_id(model_id) or "unknown"
        status_block = getattr(model, "status", {}) or {}
        registry_availability = _as_text(status_block.get("availability")) if isinstance(status_block, Mapping) else None
        is_available = (registry_availability or "").strip().lower() not in _UNAVAILABLE_STATUSES
        if is_available:
            available_models += 1
        metric = metrics_by_model.get(model_id, {})
        request_count = int(metric.get("request_count") or 0)
        success_rate = _safe_float(metric.get("success_rate"))
        status, status_reason = _global_card_status(registry_availability, success_rate, request_count)
        cards.append(
            {
                "model_id": model_id,
                "display_name": display_name,
                "provider": provider,
                "status": status,
                "status_reason": status_reason,
                "is_default": False,
                "request_count": request_count,
                "success_rate": success_rate,
                "avg_latency_ms": _safe_float(metric.get("avg_latency_ms")),
                "last_used_at": _as_text(metric.get("last_used_at")),
                "registry_availability": registry_availability or "available",
                "is_available": is_available,
                "success_count": int(metric.get("success_count") or 0),
                "fail_count": max(0, request_count - int(metric.get("success_count") or 0)),
                "last_outcome": None,
                "last_outcome_at": None,
                "updated_at": _as_text(metric.get("last_used_at")),
                "model_release_date": None,
            }
        )

    cards.sort(
        key=lambda item: (
            0 if item["is_available"] else 1,
            -int(item["request_count"]),
            str(item["provider"]),
            str(item["model_id"]),
        )
    )
    total_requests = sum(int(card["request_count"]) for card in cards)
    total_success = sum(int(card["success_count"]) for card in cards)
    overall_success_rate = (total_success / total_requests) if total_requests else 0.0
    return {
        "summary": {
            "total_models": len(cards),
            "available_models": available_models,
            "request_count": total_requests,
            "overall_success_rate": overall_success_rate,
        },
        "cards": cards,
    }


def build_class_pool_detail_payload(
    *,
    agent_class: str,
    default_model: str | None,
    pool_entries: Iterable[Mapping[str, Any]],
    default_model_ids: set[str],
    history_rows: Iterable[Mapping[str, Any]],
    model_resolver: Callable[[str], Any | None],
) -> dict[str, Any]:
    """Build the detail payload for one class pool."""
    history_by_model = _latest_history_by_model(history_rows)
    cards: list[dict[str, Any]] = []
    last_updated_at = None

    for entry in pool_entries:
        model_id = _as_text(entry.get("model_id"))
        if not model_id:
            continue
        metadata = model_resolver(model_id)
        display_name = _as_text(getattr(metadata, "display_name", None)) or model_id
        provider = _as_text(getattr(metadata, "provider", None)) or provider_from_model_id(model_id) or "unknown"
        success_count = max(0, int(entry.get("success_count") or 0))
        fail_count = max(0, int(entry.get("fail_count") or 0))
        total_trials = success_count + fail_count
        success_rate = _safe_float(entry.get("success_rate"))
        if success_rate is None and total_trials:
            success_rate = success_count / total_trials
        history = history_by_model.get(model_id, {})
        last_outcome = _as_text(history.get("last_outcome"))
        last_outcome_at = _as_text(history.get("last_outcome_at"))
        status, status_reason = _class_card_status(
            success_count=success_count,
            fail_count=fail_count,
            success_rate=success_rate,
            last_outcome=last_outcome,
        )
        updated_at = _as_text(entry.get("updated_at")) or last_outcome_at or _as_text(entry.get("created_at"))
        if updated_at and (last_updated_at is None or str(updated_at) > str(last_updated_at)):
            last_updated_at = updated_at
        cards.append(
            {
                "model_id": model_id,
                "display_name": display_name,
                "provider": provider,
                "status": status,
                "status_reason": status_reason,
                "is_default": model_id in default_model_ids,
                "request_count": 0,
                "success_rate": success_rate,
                "avg_latency_ms": None,
                "last_used_at": None,
                "registry_availability": None,
                "is_available": metadata is not None,
                "success_count": success_count,
                "fail_count": fail_count,
                "last_outcome": last_outcome,
                "last_outcome_at": last_outcome_at,
                "updated_at": updated_at,
                "model_release_date": _as_text(entry.get("model_release_date")),
            }
        )

    cards.sort(
        key=lambda item: (
            0 if item["is_default"] else 1,
            0 if item["status"] == "healthy" else 1,
            -(item["success_rate"] or 0.0),
            str(item["model_id"]),
        )
    )
    return {
        "agent_class": agent_class,
        "default_model": default_model,
        "model_count": len(cards),
        "last_updated_at": last_updated_at,
        "cards": cards,
    }
