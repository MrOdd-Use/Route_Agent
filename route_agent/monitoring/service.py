"""Monitoring service APIs."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
import json
import time
from typing import Any

from route_agent.monitoring.config import MonitoringConfig
from route_agent.monitoring.schemas import ExecutionEndEvent, ExecutionStartEvent, RouteDecisionEvent
from route_agent.monitoring.storage import MonitoringStorage

logger = logging.getLogger(__name__)

_storage: MonitoringStorage | None = None
_storage_key: str | None = None


def _resolve_config(config: MonitoringConfig | None) -> MonitoringConfig:
    """Execute `_resolve_config`."""
    return config or MonitoringConfig.from_env()


def _get_storage(config: MonitoringConfig) -> MonitoringStorage:
    """Execute `_get_storage`."""
    global _storage  # noqa: PLW0603
    global _storage_key  # noqa: PLW0603

    db_key = f"{config.db_path}|retention={int(config.retention_days)}"
    if _storage is not None and _storage_key == db_key:
        return _storage

    _storage = MonitoringStorage(config.db_path, retention_days=config.retention_days)
    _storage_key = db_key
    return _storage


def _coerce_decision_event(event: RouteDecisionEvent | dict[str, Any]) -> RouteDecisionEvent:
    """Execute `_coerce_decision_event`."""
    if isinstance(event, RouteDecisionEvent):
        return event
    return RouteDecisionEvent.from_dict(event)


def _coerce_start_event(event: ExecutionStartEvent | dict[str, Any]) -> ExecutionStartEvent:
    """Execute `_coerce_start_event`."""
    if isinstance(event, ExecutionStartEvent):
        return event
    return ExecutionStartEvent.from_dict(event)


def _coerce_end_event(event: ExecutionEndEvent | dict[str, Any]) -> ExecutionEndEvent:
    """Execute `_coerce_end_event`."""
    if isinstance(event, ExecutionEndEvent):
        return event
    return ExecutionEndEvent.from_dict(event)


def _build_agent_model_status(executions: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact per-agent model/status snapshot from recent execution rows."""
    latest_by_agent: dict[str, dict[str, Any]] = {}
    latest_status_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()

    for row in executions:
        agent_name = str(row.get("agent_name") or "unknown")
        if agent_name in latest_by_agent:
            continue

        model_used = row.get("model_used")
        model_key = str(model_used) if model_used else "unassigned"
        status_key = str(row.get("status") or "unknown")

        latest_by_agent[agent_name] = {
            "agent_name": agent_name,
            "execution_id": row.get("execution_id"),
            "request_id": row.get("request_id"),
            "model_used": model_used,
            "provider": row.get("provider"),
            "status": status_key,
            "updated_at": row.get("updated_at"),
        }
        latest_status_counts[status_key] += 1
        model_counts[model_key] += 1

    agents = sorted(latest_by_agent.values(), key=lambda item: str(item["agent_name"]))
    return {
        "total_executions": len(executions),
        "total_agents": len(agents),
        "active_agent_count": int(latest_status_counts.get("running", 0)),
        "status_counts": dict(latest_status_counts),
        "model_counts": dict(model_counts),
        "agents": agents,
    }


def _render_agent_status_board(snapshot: dict[str, Any]) -> str:
    """Render agent/model/status snapshot as plain-text table."""
    agents = list(snapshot.get("agents") or [])
    if not agents:
        return "No execution data."

    headers = ("agent_name", "model_used", "status", "provider", "request_id", "updated_at")
    rows: list[tuple[str, ...]] = []
    for agent in agents:
        rows.append(
            (
                str(agent.get("agent_name") or ""),
                str(agent.get("model_used") or "unassigned"),
                str(agent.get("status") or "unknown"),
                str(agent.get("provider") or ""),
                str(agent.get("request_id") or ""),
                str(agent.get("updated_at") or ""),
            )
        )

    col_widths: list[int] = []
    for idx, header in enumerate(headers):
        content_width = max(len(row[idx]) for row in rows) if rows else 0
        col_widths.append(max(len(header), content_width))

    def _format_row(parts: tuple[str, ...]) -> str:
        """Format one table row with aligned columns."""
        cells = [parts[idx].ljust(col_widths[idx]) for idx in range(len(parts))]
        return "| " + " | ".join(cells) + " |"

    separator = "+-" + "-+-".join("-" * width for width in col_widths) + "-+"
    lines = [
        "Agent Model/Status Dashboard",
        f"total_agents={snapshot.get('total_agents', 0)} "
        f"active_agents={snapshot.get('active_agent_count', 0)}",
        separator,
        _format_row(headers),
        separator,
    ]
    for row in rows:
        lines.append(_format_row(row))
    lines.append(separator)
    return "\n".join(lines)


def _snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """Build deterministic fingerprint for one monitoring snapshot."""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)


def record_decision(
    event: RouteDecisionEvent | dict[str, Any],
    *,
    config: MonitoringConfig | None = None,
    alert_callback: Any | None = None,
) -> int:
    """Execute `record_decision`."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return 0

    try:
        storage = _get_storage(cfg)
        row_id = storage.record_decision(_coerce_decision_event(event))
        if alert_callback is not None:
            try:
                alert_callback({"rule_key": "recorded", "status": "ok", "id": row_id})
            except Exception:  # noqa: BLE001
                logger.warning("monitoring alert_callback failed", exc_info=True)
        return row_id
    except Exception:  # noqa: BLE001
        logger.warning("monitoring record_decision failed", exc_info=True)
        return 0


async def record_decision_async(
    event: RouteDecisionEvent | dict[str, Any],
    *,
    config: MonitoringConfig | None = None,
    alert_callback: Any | None = None,
) -> int:
    """Execute `record_decision_async`."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return 0

    try:
        storage = _get_storage(cfg)
        row_id = await storage.record_decision_async(_coerce_decision_event(event))
        if alert_callback is not None:
            try:
                alert_callback({"rule_key": "recorded", "status": "ok", "id": row_id})
            except Exception:  # noqa: BLE001
                logger.warning("monitoring alert_callback failed", exc_info=True)
        return row_id
    except Exception:  # noqa: BLE001
        logger.warning("monitoring record_decision_async failed", exc_info=True)
        return 0


def get_recent_decisions(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 50,
    source: str | None = None,
    since_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Execute `get_recent_decisions`."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return []

    try:
        storage = _get_storage(cfg)
        return storage.get_recent_decisions(limit=limit, source=source, since_hours=since_hours)
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_recent_decisions failed", exc_info=True)
        return []


async def get_recent_decisions_async(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 50,
    source: str | None = None,
    since_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Execute `get_recent_decisions_async`."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return []

    try:
        storage = _get_storage(cfg)
        return await storage.get_recent_decisions_async(limit=limit, source=source, since_hours=since_hours)
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_recent_decisions_async failed", exc_info=True)
        return []


def get_stats(
    *,
    config: MonitoringConfig | None = None,
    windows: tuple[str, ...] = ("24h", "7d", "all"),
) -> dict[str, Any]:
    """Execute `get_stats`."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {window: {"total_decisions": 0, "no_model_count": 0, "no_model_rate": 0.0} for window in windows}

    try:
        storage = _get_storage(cfg)
        return storage.get_stats(windows=windows)
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_stats failed", exc_info=True)
        return {window: {"total_decisions": 0, "no_model_count": 0, "no_model_rate": 0.0} for window in windows}


async def get_stats_async(
    *,
    config: MonitoringConfig | None = None,
    windows: tuple[str, ...] = ("24h", "7d", "all"),
) -> dict[str, Any]:
    """Execute `get_stats_async`."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return {window: {"total_decisions": 0, "no_model_count": 0, "no_model_rate": 0.0} for window in windows}

    try:
        storage = _get_storage(cfg)
        return await storage.get_stats_async(windows=windows)
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_stats_async failed", exc_info=True)
        return {window: {"total_decisions": 0, "no_model_count": 0, "no_model_rate": 0.0} for window in windows}


def start_execution(
    event: ExecutionStartEvent | dict[str, Any],
    *,
    config: MonitoringConfig | None = None,
) -> str:
    """Record one execution start event and return execution id."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return ""

    try:
        storage = _get_storage(cfg)
        return storage.start_execution(_coerce_start_event(event))
    except Exception:  # noqa: BLE001
        logger.warning("monitoring start_execution failed", exc_info=True)
        return ""


async def start_execution_async(
    event: ExecutionStartEvent | dict[str, Any],
    *,
    config: MonitoringConfig | None = None,
) -> str:
    """Record one execution start event asynchronously."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return ""

    try:
        storage = _get_storage(cfg)
        return await storage.start_execution_async(_coerce_start_event(event))
    except Exception:  # noqa: BLE001
        logger.warning("monitoring start_execution_async failed", exc_info=True)
        return ""


def end_execution(
    event: ExecutionEndEvent | dict[str, Any],
    *,
    config: MonitoringConfig | None = None,
) -> bool:
    """Record one execution end event."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return False

    try:
        storage = _get_storage(cfg)
        return storage.end_execution(_coerce_end_event(event))
    except Exception:  # noqa: BLE001
        logger.warning("monitoring end_execution failed", exc_info=True)
        return False


async def end_execution_async(
    event: ExecutionEndEvent | dict[str, Any],
    *,
    config: MonitoringConfig | None = None,
) -> bool:
    """Record one execution end event asynchronously."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return False

    try:
        storage = _get_storage(cfg)
        return await storage.end_execution_async(_coerce_end_event(event))
    except Exception:  # noqa: BLE001
        logger.warning("monitoring end_execution_async failed", exc_info=True)
        return False


def get_recent_executions(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    status: str | None = None,
    since_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Query recent execution rows."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return []

    try:
        storage = _get_storage(cfg)
        return storage.get_recent_executions(
            limit=limit,
            source=source,
            status=status,
            since_hours=since_hours,
        )
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_recent_executions failed", exc_info=True)
        return []


async def get_recent_executions_async(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    status: str | None = None,
    since_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Query recent execution rows asynchronously."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return []

    try:
        storage = _get_storage(cfg)
        return await storage.get_recent_executions_async(
            limit=limit,
            source=source,
            status=status,
            since_hours=since_hours,
        )
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_recent_executions_async failed", exc_info=True)
        return []


def get_model_execution_metrics(
    *,
    config: MonitoringConfig | None = None,
    source: str | None = None,
    since_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Aggregate execution metrics by model."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return []

    try:
        storage = _get_storage(cfg)
        return storage.get_model_execution_metrics(
            source=source,
            since_hours=since_hours,
        )
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_model_execution_metrics failed", exc_info=True)
        return []


async def get_model_execution_metrics_async(
    *,
    config: MonitoringConfig | None = None,
    source: str | None = None,
    since_hours: int | None = None,
) -> list[dict[str, Any]]:
    """Aggregate execution metrics by model asynchronously."""
    cfg = _resolve_config(config)
    if not cfg.enabled:
        return []

    try:
        storage = _get_storage(cfg)
        return await storage.get_model_execution_metrics_async(
            source=source,
            since_hours=since_hours,
        )
    except Exception:  # noqa: BLE001
        logger.warning("monitoring get_model_execution_metrics_async failed", exc_info=True)
        return []


def get_agent_model_status(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    since_hours: int | None = None,
) -> dict[str, Any]:
    """Build a per-agent assigned-model and execution-status snapshot."""
    executions = get_recent_executions(
        config=config,
        limit=limit,
        source=source,
        since_hours=since_hours,
    )
    return _build_agent_model_status(executions)


async def get_agent_model_status_async(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    since_hours: int | None = None,
) -> dict[str, Any]:
    """Build a per-agent assigned-model and execution-status snapshot asynchronously."""
    executions = await get_recent_executions_async(
        config=config,
        limit=limit,
        source=source,
        since_hours=since_hours,
    )
    return _build_agent_model_status(executions)


def render_agent_model_status(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    since_hours: int | None = None,
) -> str:
    """Render per-agent model/status snapshot into plain text."""
    snapshot = get_agent_model_status(
        config=config,
        limit=limit,
        source=source,
        since_hours=since_hours,
    )
    return _render_agent_status_board(snapshot)


async def render_agent_model_status_async(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    since_hours: int | None = None,
) -> str:
    """Render per-agent model/status snapshot into plain text asynchronously."""
    snapshot = await get_agent_model_status_async(
        config=config,
        limit=limit,
        source=source,
        since_hours=since_hours,
    )
    return _render_agent_status_board(snapshot)


def iter_agent_model_status(
    *,
    config: MonitoringConfig | None = None,
    limit: int = 200,
    source: str | None = None,
    since_hours: int | None = None,
    poll_interval_seconds: float = 1.0,
    max_iterations: int | None = None,
    only_changes: bool = False,
    sleep_fn: Callable[[float], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield realtime agent-model-status frames by polling monitoring storage."""
    normalized_interval = max(0.05, float(poll_interval_seconds))
    normalized_iterations = (
        None
        if max_iterations is None
        else max(1, int(max_iterations))
    )
    resolved_sleep = sleep_fn or time.sleep
    previous_fingerprint: str | None = None
    iteration = 0

    while normalized_iterations is None or iteration < normalized_iterations:
        snapshot = get_agent_model_status(
            config=config,
            limit=limit,
            source=source,
            since_hours=since_hours,
        )
        fingerprint = _snapshot_fingerprint(snapshot)
        changed = fingerprint != previous_fingerprint
        if changed or not only_changes:
            yield {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "changed": changed,
                "snapshot": snapshot,
                "board": _render_agent_status_board(snapshot),
            }
            previous_fingerprint = fingerprint

        iteration += 1
        if normalized_iterations is not None and iteration >= normalized_iterations:
            break
        resolved_sleep(normalized_interval)
