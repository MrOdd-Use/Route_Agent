"""Monitoring service APIs."""

from __future__ import annotations

import logging
from typing import Any

from route_agent.monitoring.config import MonitoringConfig
from route_agent.monitoring.schemas import RouteDecisionEvent
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

    db_key = str(config.db_path)
    if _storage is not None and _storage_key == db_key:
        return _storage

    _storage = MonitoringStorage(config.db_path)
    _storage_key = db_key
    return _storage


def _coerce_event(event: RouteDecisionEvent | dict[str, Any]) -> RouteDecisionEvent:
    """Execute `_coerce_event`."""
    if isinstance(event, RouteDecisionEvent):
        return event
    return RouteDecisionEvent.from_dict(event)


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
        row_id = storage.record_decision(_coerce_event(event))
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
        row_id = await storage.record_decision_async(_coerce_event(event))
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
