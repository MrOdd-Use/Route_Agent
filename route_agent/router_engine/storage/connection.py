"""Connection-level helpers for router_engine storage."""

from __future__ import annotations

from pathlib import Path


def default_db_path() -> Path:
    """Return default router-engine SQLite path."""
    return Path(__file__).resolve().parents[3] / "data" / "router_engine.db"

