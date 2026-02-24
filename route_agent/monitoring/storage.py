"""SQLite storage for monitoring module."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
from typing import Any

from route_agent.monitoring.schemas import RouteDecisionEvent

_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS monitoring_decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    source                TEXT NOT NULL,
    agent_name            TEXT NOT NULL,
    model_used            TEXT,
    selected_tier         TEXT,
    provider              TEXT,
    routing_reason        TEXT,
    pool_hit              INTEGER,
    pool_class            TEXT,
    analysis_domain       TEXT,
    analysis_complexity   REAL,
    registry_error_count  INTEGER NOT NULL DEFAULT 0,
    skipped_provider_count INTEGER NOT NULL DEFAULT 0,
    event_json            TEXT NOT NULL,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_monitoring_decisions_created
    ON monitoring_decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_monitoring_decisions_source
    ON monitoring_decisions(source, created_at);
"""


class MonitoringStorage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 3000")
        return conn

    def _init_db_sync(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_CREATE_TABLES_SQL)

    async def _to_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)

    def record_decision(self, event: RouteDecisionEvent) -> int:
        payload = event.to_dict()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO monitoring_decisions(
                    source, agent_name, model_used, selected_tier, provider,
                    routing_reason, pool_hit, pool_class,
                    analysis_domain, analysis_complexity,
                    registry_error_count, skipped_provider_count, event_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["source"],
                    payload["agent_name"],
                    payload["model_used"],
                    payload["selected_tier"],
                    payload["provider"],
                    payload["routing_reason"],
                    (None if payload["pool_hit"] is None else int(bool(payload["pool_hit"]))),
                    payload["pool_class"],
                    payload["analysis_domain"],
                    payload["analysis_complexity"],
                    int(payload["registry_error_count"]),
                    int(payload["skipped_provider_count"]),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            conn.execute(
                "DELETE FROM monitoring_decisions WHERE created_at < datetime('now', ?)",
                ("-30 days",),
            )
            return int(cursor.lastrowid)

    async def record_decision_async(self, event: RouteDecisionEvent) -> int:
        return await self._to_thread(self.record_decision, event)

    def get_recent_decisions(
        self,
        *,
        limit: int = 50,
        source: str | None = None,
        since_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: list[Any] = []

        if source:
            where_parts.append("source = ?")
            params.append(source)

        if since_hours is not None:
            where_parts.append("created_at >= datetime('now', ?)")
            params.append(f"-{max(1, int(since_hours))} hours")

        where_sql = ""
        if where_parts:
            where_sql = "WHERE " + " AND ".join(where_parts)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM monitoring_decisions
                {where_sql}
                ORDER BY id DESC
                LIMIT ?
                """,
                (*params, max(1, int(limit))),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["event_json"]))
            payload["id"] = int(row["id"])
            payload["created_at"] = str(row["created_at"])
            result.append(payload)
        return result

    async def get_recent_decisions_async(
        self,
        *,
        limit: int = 50,
        source: str | None = None,
        since_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self._to_thread(
            self.get_recent_decisions,
            limit=limit,
            source=source,
            since_hours=since_hours,
        )

    def get_stats(self, windows: tuple[str, ...] = ("24h", "7d", "all")) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for window in windows:
            where_sql = ""
            params: tuple[Any, ...] = tuple()
            if window == "24h":
                where_sql = "WHERE created_at >= datetime('now', '-24 hours')"
            elif window == "7d":
                where_sql = "WHERE created_at >= datetime('now', '-7 days')"

            with self._connect() as conn:
                total = int(
                    conn.execute(
                        f"SELECT COUNT(1) AS c FROM monitoring_decisions {where_sql}",
                        params,
                    ).fetchone()["c"]
                )
                no_model = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(1) AS c FROM monitoring_decisions
                        {where_sql}{' AND' if where_sql else ' WHERE'} (model_used IS NULL OR model_used = '')
                        """,
                        params,
                    ).fetchone()["c"]
                )
                source_rows = conn.execute(
                    f"""
                    SELECT source, COUNT(1) AS c
                    FROM monitoring_decisions
                    {where_sql}
                    GROUP BY source
                    """,
                    params,
                ).fetchall()
                tier_rows = conn.execute(
                    f"""
                    SELECT selected_tier, COUNT(1) AS c
                    FROM monitoring_decisions
                    {where_sql}
                    GROUP BY selected_tier
                    """,
                    params,
                ).fetchall()

            result[window] = {
                "total_decisions": total,
                "no_model_count": no_model,
                "no_model_rate": (0.0 if total == 0 else no_model / total),
                "source_counts": {str(row["source"]): int(row["c"]) for row in source_rows},
                "tier_counts": {
                    ("unknown" if row["selected_tier"] in (None, "") else str(row["selected_tier"])): int(row["c"])
                    for row in tier_rows
                },
            }
        return result

    async def get_stats_async(self, windows: tuple[str, ...] = ("24h", "7d", "all")) -> dict[str, Any]:
        return await self._to_thread(self.get_stats, windows)

    def cleanup_old_decisions(self, retention_days: int = 30) -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM monitoring_decisions WHERE created_at < datetime('now', ?)",
                (f"-{max(1, int(retention_days))} days",),
            )
        return int(cursor.rowcount or 0)

    async def cleanup_old_decisions_async(self, retention_days: int = 30) -> int:
        return await self._to_thread(self.cleanup_old_decisions, retention_days)
