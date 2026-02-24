"""Monitoring config helpers."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _as_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _default_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "route_agent_monitoring.db"


@dataclass(frozen=True)
class MonitoringConfig:
    enabled: bool = False
    db_path: Path = _default_db_path()
    retention_days: int = 30

    @classmethod
    def from_env(cls) -> "MonitoringConfig":
        enabled = _as_bool(os.getenv("ROUTE_AGENT_MONITORING_ENABLED"), False)
        db_path_raw = os.getenv("ROUTE_AGENT_MONITORING_DB_PATH")
        db_path = Path(db_path_raw).expanduser().resolve() if db_path_raw else _default_db_path()
        retention_raw = os.getenv("ROUTE_AGENT_MONITORING_RETENTION_DAYS")
        try:
            retention_days = max(1, int(retention_raw)) if retention_raw is not None else 30
        except ValueError:
            retention_days = 30
        return cls(enabled=enabled, db_path=db_path, retention_days=retention_days)
