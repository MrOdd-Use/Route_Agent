"""SQLite storage for monitoring module."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
import uuid

from route_agent.monitoring.schemas import ExecutionEndEvent, ExecutionStartEvent, RouteDecisionEvent

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
CREATE TABLE IF NOT EXISTS monitoring_executions (
    execution_id          TEXT PRIMARY KEY,
    source                TEXT NOT NULL,
    agent_name            TEXT NOT NULL,
    request_id            TEXT,
    model_used            TEXT,
    provider              TEXT,
    status                TEXT NOT NULL,
    started_at            TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at              TEXT,
    duration_ms           REAL,
    error_message         TEXT,
    metadata_json         TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_monitoring_executions_updated
    ON monitoring_executions(updated_at);
CREATE INDEX IF NOT EXISTS idx_monitoring_executions_source_status
    ON monitoring_executions(source, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_monitoring_executions_agent
    ON monitoring_executions(agent_name, updated_at);
"""


class MonitoringStorage:
    """Represent `MonitoringStorage`."""

    def __init__(self, db_path: Path, retention_days: int = 30) -> None:
        """Initialize the instance."""
        self._db_path = db_path
        self._retention_days = max(1, int(retention_days))
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()

    def _retention_expr(self) -> str:
        """Return SQLite interval expression for configured retention."""
        return f"-{self._retention_days} days"

    def _connect(self) -> sqlite3.Connection:
        """Execute `_connect`."""
        conn = sqlite3.connect(self._db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 3000")
        return conn

    def _init_db_sync(self) -> None:
        """Execute `_init_db_sync`."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_CREATE_TABLES_SQL)

    async def _to_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute `_to_thread`."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    @staticmethod
    def _parse_metadata(raw: Any) -> dict[str, Any]:
        """Best-effort parse JSON metadata object."""
        if raw in (None, ""):
            return {}
        try:
            value = json.loads(str(raw))
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _to_utc(raw: str | None) -> datetime | None:
        """Parse SQLite/ISO datetime string to UTC datetime."""
        if not raw:
            return None

        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

        text = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _compute_duration_ms(cls, started_at: str | None, ended_at: str | None) -> float | None:
        """Compute duration milliseconds from start/end timestamps."""
        start = cls._to_utc(started_at)
        end = cls._to_utc(ended_at)
        if start is None or end is None:
            return None
        duration_ms = (end - start).total_seconds() * 1000.0
        return max(0.0, duration_ms)

    def _cleanup_old_rows(self, conn: sqlite3.Connection) -> None:
        """Delete stale monitoring rows using configured retention."""
        conn.execute(
            "DELETE FROM monitoring_decisions WHERE created_at < datetime('now', ?)",
            (self._retention_expr(),),
        )
        conn.execute(
            "DELETE FROM monitoring_executions WHERE created_at < datetime('now', ?)",
            (self._retention_expr(),),
        )

    def record_decision(self, event: RouteDecisionEvent) -> int:
        """Execute `record_decision`."""
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
            self._cleanup_old_rows(conn)
            return int(cursor.lastrowid)

    async def record_decision_async(self, event: RouteDecisionEvent) -> int:
        """Execute `record_decision_async`."""
        return await self._to_thread(self.record_decision, event)

    def get_recent_decisions(
        self,
        *,
        limit: int = 50,
        source: str | None = None,
        since_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute `get_recent_decisions`."""
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
        """Execute `get_recent_decisions_async`."""
        return await self._to_thread(
            self.get_recent_decisions,
            limit=limit,
            source=source,
            since_hours=since_hours,
        )

    def get_stats(self, windows: tuple[str, ...] = ("24h", "7d", "all")) -> dict[str, Any]:
        """Execute `get_stats`."""
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
        """Execute `get_stats_async`."""
        return await self._to_thread(self.get_stats, windows)

    def start_execution(self, event: ExecutionStartEvent) -> str:
        """Insert or upsert one execution row and return execution id."""
        payload = event.to_dict()
        execution_id = payload["execution_id"] or str(uuid.uuid4())
        started_at = payload["started_at"] or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO monitoring_executions(
                    execution_id, source, agent_name, request_id, model_used, provider,
                    status, started_at, ended_at, duration_ms, error_message, metadata_json, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, datetime('now'))
                ON CONFLICT(execution_id) DO UPDATE SET
                    source = excluded.source,
                    agent_name = excluded.agent_name,
                    request_id = excluded.request_id,
                    model_used = excluded.model_used,
                    provider = excluded.provider,
                    status = excluded.status,
                    started_at = excluded.started_at,
                    ended_at = NULL,
                    duration_ms = NULL,
                    error_message = NULL,
                    metadata_json = excluded.metadata_json,
                    updated_at = datetime('now')
                """,
                (
                    execution_id,
                    payload["source"],
                    payload["agent_name"],
                    payload["request_id"],
                    payload["model_used"],
                    payload["provider"],
                    payload["status"],
                    started_at,
                    json.dumps(payload["metadata"], ensure_ascii=False),
                ),
            )
            self._cleanup_old_rows(conn)
        return execution_id

    async def start_execution_async(self, event: ExecutionStartEvent) -> str:
        """Execute `start_execution_async`."""
        return await self._to_thread(self.start_execution, event)

    def end_execution(self, event: ExecutionEndEvent) -> bool:
        """Finalize one execution row with status and duration."""
        payload = event.to_dict()
        with self._connect() as conn:
            current = conn.execute(
                """
                SELECT started_at, metadata_json
                FROM monitoring_executions
                WHERE execution_id = ?
                """,
                (payload["execution_id"],),
            ).fetchone()
            if current is None:
                return False

            merged_metadata = self._parse_metadata(current["metadata_json"])
            merged_metadata.update(dict(payload["metadata"]))
            ended_at = payload["ended_at"] or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            duration_ms = payload["duration_ms"]
            if duration_ms is None:
                duration_ms = self._compute_duration_ms(str(current["started_at"]), ended_at)

            cursor = conn.execute(
                """
                UPDATE monitoring_executions
                SET status = ?,
                    ended_at = ?,
                    duration_ms = ?,
                    error_message = ?,
                    metadata_json = ?,
                    updated_at = datetime('now')
                WHERE execution_id = ?
                """,
                (
                    payload["status"],
                    ended_at,
                    duration_ms,
                    payload["error_message"],
                    json.dumps(merged_metadata, ensure_ascii=False),
                    payload["execution_id"],
                ),
            )
            self._cleanup_old_rows(conn)
            return bool(cursor.rowcount)

    async def end_execution_async(self, event: ExecutionEndEvent) -> bool:
        """Execute `end_execution_async`."""
        return await self._to_thread(self.end_execution, event)

    def get_recent_executions(
        self,
        *,
        limit: int = 200,
        source: str | None = None,
        status: str | None = None,
        since_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query recent execution rows."""
        where_parts: list[str] = []
        params: list[Any] = []

        if source:
            where_parts.append("source = ?")
            params.append(source)
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if since_hours is not None:
            where_parts.append("updated_at >= datetime('now', ?)")
            params.append(f"-{max(1, int(since_hours))} hours")

        where_sql = ""
        if where_parts:
            where_sql = "WHERE " + " AND ".join(where_parts)

        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    execution_id, source, agent_name, request_id, model_used, provider,
                    status, started_at, ended_at, duration_ms, error_message, metadata_json,
                    created_at, updated_at
                FROM monitoring_executions
                {where_sql}
                ORDER BY datetime(updated_at) DESC, execution_id DESC
                LIMIT ?
                """,
                (*params, max(1, int(limit))),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "execution_id": str(row["execution_id"]),
                    "source": str(row["source"]),
                    "agent_name": str(row["agent_name"]),
                    "request_id": (None if row["request_id"] in (None, "") else str(row["request_id"])),
                    "model_used": (None if row["model_used"] in (None, "") else str(row["model_used"])),
                    "provider": (None if row["provider"] in (None, "") else str(row["provider"])),
                    "status": str(row["status"]),
                    "started_at": str(row["started_at"]),
                    "ended_at": (None if row["ended_at"] in (None, "") else str(row["ended_at"])),
                    "duration_ms": (None if row["duration_ms"] is None else float(row["duration_ms"])),
                    "error_message": (None if row["error_message"] in (None, "") else str(row["error_message"])),
                    "metadata": self._parse_metadata(row["metadata_json"]),
                    "created_at": str(row["created_at"]),
                    "updated_at": str(row["updated_at"]),
                }
            )
        return result

    async def get_recent_executions_async(
        self,
        *,
        limit: int = 200,
        source: str | None = None,
        status: str | None = None,
        since_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute `get_recent_executions_async`."""
        return await self._to_thread(
            self.get_recent_executions,
            limit=limit,
            source=source,
            status=status,
            since_hours=since_hours,
        )

    def cleanup_old_decisions(self, retention_days: int = 30) -> int:
        """Execute `cleanup_old_decisions`."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM monitoring_decisions WHERE created_at < datetime('now', ?)",
                (f"-{max(1, int(retention_days))} days",),
            )
        return int(cursor.rowcount or 0)

    async def cleanup_old_decisions_async(self, retention_days: int = 30) -> int:
        """Execute `cleanup_old_decisions_async`."""
        return await self._to_thread(self.cleanup_old_decisions, retention_days)
