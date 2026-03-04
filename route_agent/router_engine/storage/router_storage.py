
"""SQLite storage layer for router_engine."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from route_agent.router_engine.constants import CLASS_DICT_INITIAL_SET, DEFAULT_DOMAIN_KEY, MIN_TRIALS
from route_agent.router_engine.schemas import (
    ClassModelStats,
    ClassPoolDefault,
    ClassPoolEntry,
    ModelAvailability,
)
from route_agent.router_engine.storage.connection import default_db_path

_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS class_model_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class         TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    success_count       INTEGER NOT NULL DEFAULT 0,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    exec_fail_count     INTEGER NOT NULL DEFAULT 0,
    consecutive_success INTEGER NOT NULL DEFAULT 0,
    consecutive_fail    INTEGER NOT NULL DEFAULT 0,
    success_rate        REAL NOT NULL DEFAULT 0.0,
    bonus_level         INTEGER NOT NULL DEFAULT 0,
    penalty_level       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_class, model_id)
);

CREATE INDEX IF NOT EXISTS idx_class_model_stats_lookup
    ON class_model_stats(agent_class);

CREATE INDEX IF NOT EXISTS idx_stats_pool_candidate
    ON class_model_stats(agent_class, success_count, fail_count);

CREATE TABLE IF NOT EXISTS class_pool (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class         TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    model_release_date  TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_class, model_id)
);

CREATE INDEX IF NOT EXISTS idx_class_pool_lookup
    ON class_pool(agent_class);

CREATE TABLE IF NOT EXISTS class_pool_defaults (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class         TEXT NOT NULL,
    domain              TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    is_locked           INTEGER NOT NULL DEFAULT 0,
    consecutive_success INTEGER NOT NULL DEFAULT 0,
    consecutive_fail    INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_class, domain)
);

CREATE TABLE IF NOT EXISTS class_aliases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alias_class         TEXT NOT NULL,
    canonical_class     TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'seed',
    is_active           INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(alias_class)
);

CREATE INDEX IF NOT EXISTS idx_class_aliases_canonical
    ON class_aliases(canonical_class);

CREATE TABLE IF NOT EXISTS class_review_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_class    TEXT NOT NULL,
    proposed_by         TEXT NOT NULL,
    hit_count           INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending',
    merged_to           TEXT,
    first_seen_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at         TEXT,
    review_note         TEXT,
    UNIQUE(normalized_class)
);

CREATE INDEX IF NOT EXISTS idx_class_review_pending
    ON class_review_queue(status, hit_count, last_seen_at);

CREATE TABLE IF NOT EXISTS class_success_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    agent_class     TEXT NOT NULL,
    class_source    TEXT NOT NULL,
    domain          TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    record_id       INTEGER,
    outcome         TEXT NOT NULL DEFAULT 'success',
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_success_log_class
    ON class_success_log(agent_class, domain);
CREATE INDEX IF NOT EXISTS idx_success_log_agent
    ON class_success_log(agent_name);
CREATE INDEX IF NOT EXISTS idx_success_log_model
    ON class_success_log(agent_class, model_id, created_at);
CREATE INDEX IF NOT EXISTS idx_success_log_request
    ON class_success_log(request_id);

CREATE TABLE IF NOT EXISTS feedback_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    event_ts        TEXT NOT NULL,
    processed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(request_id, model_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_feedback_events_lookup
    ON feedback_events(request_id, model_id);

CREATE TABLE IF NOT EXISTS downgrade_trials (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class             TEXT NOT NULL,
    domain                  TEXT NOT NULL,
    incumbent_model_id      TEXT NOT NULL,
    challenger_model_id     TEXT NOT NULL,
    state                   TEXT NOT NULL DEFAULT 'active',
    sampled_requests        INTEGER NOT NULL DEFAULT 0,
    quality_fail_count      INTEGER NOT NULL DEFAULT 0,
    exec_fail_count         INTEGER NOT NULL DEFAULT 0,
    success_count           INTEGER NOT NULL DEFAULT 0,
    canary_ratio            REAL NOT NULL DEFAULT 0.50,
    expected_savings_ratio  REAL NOT NULL DEFAULT 0.0,
    started_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at                TEXT,
    cooldown_until          TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_downgrade_trials_active
    ON downgrade_trials(agent_class, domain)
 WHERE state = 'active';

CREATE INDEX IF NOT EXISTS idx_downgrade_trials_cooldown
    ON downgrade_trials(agent_class, domain, challenger_model_id, cooldown_until);

CREATE TABLE IF NOT EXISTS model_availability (
    model_id                TEXT PRIMARY KEY,
    status                  TEXT NOT NULL DEFAULT 'available',
    degraded_since          TEXT,
    unable_since            TEXT,
    last_probe_at           TEXT,
    last_probe_success      INTEGER,
    consecutive_exec_fail   INTEGER NOT NULL DEFAULT 0,
    probe_good_feedback     INTEGER NOT NULL DEFAULT 0,
    probe_consecutive_success INTEGER NOT NULL DEFAULT 0,
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _default_db_path() -> Path:
    """Execute `_default_db_path`."""
    return default_db_path()


def _normalize_class_name(value: str) -> str:
    """Execute `_normalize_class_name`."""
    return "_".join((value or "").strip().lower().split())


def _row_to_stats(row: sqlite3.Row) -> ClassModelStats:
    """Execute `_row_to_stats`."""
    return ClassModelStats(
        agent_class=row["agent_class"],
        model_id=row["model_id"],
        success_count=int(row["success_count"]),
        fail_count=int(row["fail_count"]),
        exec_fail_count=int(row["exec_fail_count"]),
        consecutive_success=int(row["consecutive_success"]),
        consecutive_fail=int(row["consecutive_fail"]),
        success_rate=float(row["success_rate"]),
        bonus_level=int(row["bonus_level"]),
        penalty_level=int(row["penalty_level"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_pool_entry(row: sqlite3.Row) -> ClassPoolEntry:
    """Execute `_row_to_pool_entry`."""
    return ClassPoolEntry(
        agent_class=row["agent_class"],
        model_id=row["model_id"],
        model_release_date=row["model_release_date"],
        success_count=int(row["success_count"] or 0),
        fail_count=int(row["fail_count"] or 0),
        success_rate=float(row["success_rate"] or 0.0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_default(row: sqlite3.Row) -> ClassPoolDefault:
    """Execute `_row_to_default`."""
    return ClassPoolDefault(
        agent_class=row["agent_class"],
        domain=row["domain"],
        model_id=row["model_id"],
        is_locked=bool(row["is_locked"]),
        consecutive_success=int(row["consecutive_success"] or 0),
        consecutive_fail=int(row["consecutive_fail"] or 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_availability(row: sqlite3.Row) -> ModelAvailability:
    """Execute `_row_to_availability`."""
    probe_success_raw = row["last_probe_success"]
    probe_success = None if probe_success_raw is None else bool(probe_success_raw)
    return ModelAvailability(
        model_id=row["model_id"],
        status=row["status"],
        degraded_since=row["degraded_since"],
        unable_since=row["unable_since"],
        last_probe_at=row["last_probe_at"],
        last_probe_success=probe_success,
        consecutive_exec_fail=int(row["consecutive_exec_fail"]),
        probe_good_feedback=int(row["probe_good_feedback"]),
        probe_consecutive_success=int(row["probe_consecutive_success"]),
        updated_at=row["updated_at"],
    )


class RouterStorage:
    """SQLite storage with sync + async helpers for router engine."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the instance."""
        self._db_path = db_path or _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()

    @property
    def db_path(self) -> Path:
        """Execute `db_path`."""
        return self._db_path

    def _connect_sync(self) -> sqlite3.Connection:
        """Execute `_connect_sync`."""
        conn = sqlite3.connect(self._db_path, timeout=3.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 3000")
        return conn

    def connect_sync(self) -> sqlite3.Connection:
        """Expose a connection for cold-path transactional operations."""
        return self._connect_sync()

    def _init_db_sync(self) -> None:
        """Execute `_init_db_sync`."""
        with self._connect_sync() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA wal_autocheckpoint = 1000")
            conn.executescript(_CREATE_TABLES_SQL)

    async def _to_thread(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Execute `_to_thread`."""
        return await asyncio.to_thread(fn, *args, **kwargs)

    # ------------------------------------------------------------------
    # Model availability
    # ------------------------------------------------------------------

    def get_availability(self, model_id: str) -> ModelAvailability | None:
        """Execute `get_availability`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                "SELECT * FROM model_availability WHERE model_id = ?",
                (model_id,),
            ).fetchone()
        return _row_to_availability(row) if row else None

    async def get_availability_async(self, model_id: str) -> ModelAvailability | None:
        """Execute `get_availability_async`."""
        return await self._to_thread(self.get_availability, model_id)

    def mark_unable(self, model_id: str) -> None:
        """Execute `mark_unable`."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO model_availability(model_id, status, unable_since, updated_at)
                VALUES (?, 'unable', datetime('now'), datetime('now'))
                ON CONFLICT(model_id) DO UPDATE SET
                    status='unable',
                    unable_since=COALESCE(model_availability.unable_since, datetime('now')),
                    updated_at=datetime('now')
                """,
                (model_id,),
            )

    async def mark_unable_async(self, model_id: str) -> None:
        """Execute `mark_unable_async`."""
        await self._to_thread(self.mark_unable, model_id)

    def mark_available(self, model_id: str) -> None:
        """Execute `mark_available`."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO model_availability(model_id, status, updated_at)
                VALUES (?, 'available', datetime('now'))
                ON CONFLICT(model_id) DO UPDATE SET
                    status='available',
                    degraded_since=NULL,
                    unable_since=NULL,
                    consecutive_exec_fail=0,
                    probe_good_feedback=0,
                    probe_consecutive_success=0,
                    updated_at=datetime('now')
                """,
                (model_id,),
            )

    async def mark_available_async(self, model_id: str) -> None:
        """Execute `mark_available_async`."""
        await self._to_thread(self.mark_available, model_id)

    def report_exec_failure_transition(self, model_id: str, threshold: int = 3) -> str:
        """Increment consecutive_exec_fail; transition to unable when threshold reached.

        Works independently of quality-driven degraded state: if the model is
        currently degraded it still becomes unable once exec failures accumulate.
        """
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO model_availability (model_id, status, consecutive_exec_fail, updated_at)
                VALUES (?, 'available', 1, datetime('now'))
                ON CONFLICT(model_id) DO UPDATE SET
                    consecutive_exec_fail = model_availability.consecutive_exec_fail + 1,
                    updated_at = datetime('now')
                """,
                (model_id,),
            )
            # Check if threshold reached → transition to unable.
            conn.execute(
                """
                UPDATE model_availability
                   SET status = 'unable',
                       unable_since = datetime('now'),
                       consecutive_exec_fail = 0,
                       probe_good_feedback = 0,
                       probe_consecutive_success = 0
                 WHERE model_id = ?
                   AND consecutive_exec_fail >= ?
                   AND status != 'unable'
                """,
                (model_id, threshold),
            )
            row = conn.execute(
                "SELECT status FROM model_availability WHERE model_id = ?",
                (model_id,),
            ).fetchone()
            status = str(row["status"]) if row else "available"
            if status == "unable":
                conn.execute("DELETE FROM class_pool WHERE model_id = ?", (model_id,))
            return status

    async def report_exec_failure_transition_async(self, model_id: str) -> str:
        """Execute `report_exec_failure_transition_async`."""
        return await self._to_thread(self.report_exec_failure_transition, model_id)

    def reset_exec_fail_counter(self, model_id: str) -> None:
        """Reset consecutive_exec_fail to 0 on successful execution."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                UPDATE model_availability
                   SET consecutive_exec_fail = 0,
                       updated_at = datetime('now')
                 WHERE model_id = ?
                   AND consecutive_exec_fail > 0
                """,
                (model_id,),
            )

    async def reset_exec_fail_counter_async(self, model_id: str) -> None:
        """Execute `reset_exec_fail_counter_async`."""
        await self._to_thread(self.reset_exec_fail_counter, model_id)

    def mark_degraded_by_quality(self, model_id: str) -> None:
        """Transition model to degraded state due to quality failures.

        Only applies when status is 'available' or already 'degraded' (refreshes
        degraded_since). Does not override 'unable'.
        """
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO model_availability (model_id, status, degraded_since, updated_at)
                VALUES (?, 'degraded', datetime('now'), datetime('now'))
                ON CONFLICT(model_id) DO UPDATE SET
                    status = CASE
                        WHEN model_availability.status IN ('available', 'degraded')
                        THEN 'degraded'
                        ELSE model_availability.status
                    END,
                    degraded_since = CASE
                        WHEN model_availability.status IN ('available', 'degraded')
                        THEN datetime('now')
                        ELSE model_availability.degraded_since
                    END,
                    probe_good_feedback = 0,
                    probe_consecutive_success = 0,
                    updated_at = datetime('now')
                """,
                (model_id,),
            )

    async def mark_degraded_by_quality_async(self, model_id: str) -> None:
        """Execute `mark_degraded_by_quality_async`."""
        await self._to_thread(self.mark_degraded_by_quality, model_id)

    def update_probe_testing_feedback(
        self, model_id: str, is_good: bool,
    ) -> tuple[int, int] | None:
        """Update probe testing counters for a degraded model in probe-testing phase.

        Returns (probe_good_feedback, probe_consecutive_success) after update,
        or None if the model was reset back to degraded due to quality failure.
        """
        with self._connect_sync() as conn:
            if is_good:
                row = conn.execute(
                    """
                    UPDATE model_availability
                       SET probe_good_feedback = probe_good_feedback + 1,
                           probe_consecutive_success = probe_consecutive_success + 1,
                           updated_at = datetime('now')
                     WHERE model_id = ? AND status = 'degraded'
                 RETURNING probe_good_feedback, probe_consecutive_success
                    """,
                    (model_id,),
                ).fetchone()
                if row is None:
                    return None
                return int(row[0]), int(row[1])
            else:
                # Quality fail during probe testing → reset back to degraded
                conn.execute(
                    """
                    UPDATE model_availability
                       SET degraded_since = datetime('now'),
                           probe_good_feedback = 0,
                           probe_consecutive_success = 0,
                           updated_at = datetime('now')
                     WHERE model_id = ? AND status = 'degraded'
                    """,
                    (model_id,),
                )
                return None

    async def update_probe_testing_feedback_async(
        self, model_id: str, is_good: bool,
    ) -> tuple[int, int] | None:
        """Execute `update_probe_testing_feedback_async`."""
        return await self._to_thread(self.update_probe_testing_feedback, model_id, is_good)

    def list_unable_for_probe(self, interval_s: int) -> list[str]:
        """Execute `list_unable_for_probe`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT model_id
                FROM model_availability
                WHERE status = 'unable'
                  AND (
                    last_probe_at IS NULL
                    OR last_probe_at < datetime('now', ?)
                  )
                """,
                (f"-{int(interval_s)} seconds",),
            ).fetchall()
        return [str(row["model_id"]) for row in rows]

    async def list_unable_for_probe_async(self, interval_s: int) -> list[str]:
        """Execute `list_unable_for_probe_async`."""
        return await self._to_thread(self.list_unable_for_probe, interval_s)

    def update_probe_result(self, model_id: str, success: bool) -> None:
        """Execute `update_probe_result`."""
        with self._connect_sync() as conn:
            if success:
                conn.execute(
                    """
                    INSERT INTO model_availability(
                        model_id, status, degraded_since, unable_since,
                        last_probe_at, last_probe_success, updated_at
                    )
                    VALUES (?, 'available', NULL, NULL, datetime('now'), 1, datetime('now'))
                    ON CONFLICT(model_id) DO UPDATE SET
                        status='available',
                        degraded_since=NULL,
                        unable_since=NULL,
                        consecutive_exec_fail=0,
                        probe_good_feedback=0,
                        probe_consecutive_success=0,
                        last_probe_at=datetime('now'),
                        last_probe_success=1,
                        updated_at=datetime('now')
                    """,
                    (model_id,),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO model_availability(model_id, status, last_probe_at, last_probe_success, updated_at)
                    VALUES (?, 'unable', datetime('now'), 0, datetime('now'))
                    ON CONFLICT(model_id) DO UPDATE SET
                        status='unable',
                        last_probe_at=datetime('now'),
                        last_probe_success=0,
                        updated_at=datetime('now')
                    """,
                    (model_id,),
                )

    async def update_probe_result_async(self, model_id: str, success: bool) -> None:
        """Execute `update_probe_result_async`."""
        await self._to_thread(self.update_probe_result, model_id, success)

    # ------------------------------------------------------------------
    # Class model stats
    # ------------------------------------------------------------------

    def ensure_stats_row(self, agent_class: str, model_id: str) -> None:
        """Execute `ensure_stats_row`."""
        with self._connect_sync() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO class_model_stats(agent_class, model_id) VALUES (?, ?)",
                (agent_class, model_id),
            )

    async def ensure_stats_row_async(self, agent_class: str, model_id: str) -> None:
        """Execute `ensure_stats_row_async`."""
        await self._to_thread(self.ensure_stats_row, agent_class, model_id)

    def get_stats(self, agent_class: str, model_id: str) -> ClassModelStats | None:
        """Execute `get_stats`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                "SELECT * FROM class_model_stats WHERE agent_class = ? AND model_id = ?",
                (agent_class, model_id),
            ).fetchone()
        return _row_to_stats(row) if row else None

    async def get_stats_async(self, agent_class: str, model_id: str) -> ClassModelStats | None:
        """Execute `get_stats_async`."""
        return await self._to_thread(self.get_stats, agent_class, model_id)

    def list_stats_for_class(self, agent_class: str) -> list[ClassModelStats]:
        """Execute `list_stats_for_class`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                "SELECT * FROM class_model_stats WHERE agent_class = ?",
                (agent_class,),
            ).fetchall()
        return [_row_to_stats(row) for row in rows]

    async def list_stats_for_class_async(self, agent_class: str) -> list[ClassModelStats]:
        """Execute `list_stats_for_class_async`."""
        return await self._to_thread(self.list_stats_for_class, agent_class)

    def atomic_increment_success(self, agent_class: str, model_id: str) -> ClassModelStats:
        """Execute `atomic_increment_success`."""
        self.ensure_stats_row(agent_class, model_id)
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                UPDATE class_model_stats
                   SET success_count = success_count + 1,
                       consecutive_success = consecutive_success + 1,
                       consecutive_fail = CASE
                           WHEN consecutive_success + 1 >= 2 THEN 0
                           ELSE consecutive_fail
                       END,
                       success_rate = CAST(success_count + 1 AS REAL)
                                     / MAX(success_count + 1 + fail_count, 1),
                       bonus_level = MAX(bonus_level, (consecutive_success + 1) / 3),
                       penalty_level = CASE
                           WHEN consecutive_success + 1 >= 2 THEN 0
                           ELSE penalty_level
                       END,
                       updated_at = datetime('now')
                 WHERE agent_class = ? AND model_id = ?
             RETURNING *
                """,
                (agent_class, model_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to increment success counter")
        return _row_to_stats(row)

    async def atomic_increment_success_async(self, agent_class: str, model_id: str) -> ClassModelStats:
        """Execute `atomic_increment_success_async`."""
        return await self._to_thread(self.atomic_increment_success, agent_class, model_id)

    def atomic_increment_fail(self, agent_class: str, model_id: str) -> ClassModelStats:
        """Execute `atomic_increment_fail`."""
        self.ensure_stats_row(agent_class, model_id)
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                UPDATE class_model_stats
                   SET fail_count = fail_count + 1,
                       consecutive_fail = consecutive_fail + 1,
                       consecutive_success = 0,
                       bonus_level = CASE
                           WHEN consecutive_fail >= 1 THEN bonus_level / 2
                           ELSE bonus_level
                       END,
                       penalty_level = CASE
                           WHEN consecutive_fail >= 1 AND bonus_level / 2 = 0
                           THEN (consecutive_fail + 1) / 3
                           ELSE 0
                       END,
                       success_rate = CAST(success_count AS REAL)
                                     / MAX(success_count + fail_count + 1, 1),
                       updated_at = datetime('now')
                 WHERE agent_class = ? AND model_id = ?
             RETURNING *
                """,
                (agent_class, model_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to increment fail counter")
        return _row_to_stats(row)

    async def atomic_increment_fail_async(self, agent_class: str, model_id: str) -> ClassModelStats:
        """Execute `atomic_increment_fail_async`."""
        return await self._to_thread(self.atomic_increment_fail, agent_class, model_id)

    def atomic_increment_exec_fail(self, agent_class: str, model_id: str) -> ClassModelStats:
        """Execute `atomic_increment_exec_fail`."""
        self.ensure_stats_row(agent_class, model_id)
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                UPDATE class_model_stats
                   SET exec_fail_count = exec_fail_count + 1,
                       updated_at = datetime('now')
                 WHERE agent_class = ? AND model_id = ?
             RETURNING *
                """,
                (agent_class, model_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to increment exec_fail counter")
        return _row_to_stats(row)

    async def atomic_increment_exec_fail_async(self, agent_class: str, model_id: str) -> ClassModelStats:
        """Execute `atomic_increment_exec_fail_async`."""
        return await self._to_thread(self.atomic_increment_exec_fail, agent_class, model_id)

    # ------------------------------------------------------------------
    # Class pool
    # ------------------------------------------------------------------

    def get_pool_entries(self, agent_class: str) -> list[ClassPoolEntry]:
        """Execute `get_pool_entries`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT
                    cp.agent_class,
                    cp.model_id,
                    cp.model_release_date,
                    COALESCE(cms.success_count, 0) AS success_count,
                    COALESCE(cms.fail_count, 0) AS fail_count,
                    COALESCE(cms.success_rate, 0.0) AS success_rate,
                    cp.created_at,
                    cp.updated_at
                FROM class_pool cp
                LEFT JOIN class_model_stats cms
                  ON cp.agent_class = cms.agent_class
                 AND cp.model_id = cms.model_id
                WHERE cp.agent_class = ?
                ORDER BY
                    CASE WHEN (COALESCE(cms.success_count, 0) + COALESCE(cms.fail_count, 0)) >= ?
                         THEN 0 ELSE 1 END,
                    COALESCE(cms.success_rate, 0.0) DESC,
                    cp.updated_at DESC
                """,
                (agent_class, MIN_TRIALS),
            ).fetchall()
        return [_row_to_pool_entry(row) for row in rows]

    async def get_pool_entries_async(self, agent_class: str) -> list[ClassPoolEntry]:
        """Execute `get_pool_entries_async`."""
        return await self._to_thread(self.get_pool_entries, agent_class)

    def upsert_pool_entry(self, agent_class: str, model_id: str, model_release_date: str | None = None) -> None:
        """Execute `upsert_pool_entry`."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO class_pool(agent_class, model_id, model_release_date)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_class, model_id) DO UPDATE SET
                    model_release_date = COALESCE(excluded.model_release_date, class_pool.model_release_date),
                    updated_at = datetime('now')
                """,
                (agent_class, model_id, model_release_date),
            )

    async def upsert_pool_entry_async(
        self,
        agent_class: str,
        model_id: str,
        model_release_date: str | None = None,
    ) -> None:
        """Execute `upsert_pool_entry_async`."""
        await self._to_thread(self.upsert_pool_entry, agent_class, model_id, model_release_date)

    def delete_pool_entry(self, agent_class: str, model_id: str) -> None:
        """Execute `delete_pool_entry`."""
        with self._connect_sync() as conn:
            conn.execute(
                "DELETE FROM class_pool WHERE agent_class = ? AND model_id = ?",
                (agent_class, model_id),
            )

    async def delete_pool_entry_async(self, agent_class: str, model_id: str) -> None:
        """Execute `delete_pool_entry_async`."""
        await self._to_thread(self.delete_pool_entry, agent_class, model_id)

    def delete_model_from_all_pools(self, model_id: str) -> None:
        """Execute `delete_model_from_all_pools`."""
        with self._connect_sync() as conn:
            conn.execute("DELETE FROM class_pool WHERE model_id = ?", (model_id,))

    async def delete_model_from_all_pools_async(self, model_id: str) -> None:
        """Execute `delete_model_from_all_pools_async`."""
        await self._to_thread(self.delete_model_from_all_pools, model_id)

    def count_pool(self, agent_class: str) -> int:
        """Execute `count_pool`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM class_pool WHERE agent_class = ?",
                (agent_class,),
            ).fetchone()
        return int(row["cnt"] if row else 0)

    async def count_pool_async(self, agent_class: str) -> int:
        """Execute `count_pool_async`."""
        return await self._to_thread(self.count_pool, agent_class)

    def list_pool_classes(self) -> list[str]:
        """Execute `list_pool_classes`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                "SELECT DISTINCT agent_class FROM class_pool ORDER BY agent_class",
            ).fetchall()
        return [str(row["agent_class"]) for row in rows]

    async def list_pool_classes_async(self) -> list[str]:
        """Execute `list_pool_classes_async`."""
        return await self._to_thread(self.list_pool_classes)

    # ------------------------------------------------------------------
    # Class defaults
    # ------------------------------------------------------------------

    def get_default(self, agent_class: str, domain: str) -> ClassPoolDefault | None:
        """Execute `get_default`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                "SELECT * FROM class_pool_defaults WHERE agent_class = ? AND domain = ?",
                (agent_class, domain),
            ).fetchone()
        return _row_to_default(row) if row else None

    async def get_default_async(self, agent_class: str, domain: str) -> ClassPoolDefault | None:
        """Execute `get_default_async`."""
        return await self._to_thread(self.get_default, agent_class, domain)

    def list_default_model_ids(self, agent_class: str) -> set[str]:
        """Execute `list_default_model_ids`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                "SELECT model_id FROM class_pool_defaults WHERE agent_class = ?",
                (agent_class,),
            ).fetchall()
        return {str(row["model_id"]) for row in rows}

    async def list_default_model_ids_async(self, agent_class: str) -> set[str]:
        """Execute `list_default_model_ids_async`."""
        return await self._to_thread(self.list_default_model_ids, agent_class)

    def upsert_default(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        *,
        consecutive_success: int = 0,
        consecutive_fail: int = 0,
        is_locked: bool = False,
    ) -> None:
        """Execute `upsert_default`."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO class_pool_defaults(
                    agent_class, domain, model_id, is_locked,
                    consecutive_success, consecutive_fail
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_class, domain) DO UPDATE SET
                    model_id = excluded.model_id,
                    is_locked = excluded.is_locked,
                    consecutive_success = excluded.consecutive_success,
                    consecutive_fail = excluded.consecutive_fail,
                    updated_at = datetime('now')
                """,
                (
                    agent_class,
                    domain,
                    model_id,
                    int(is_locked),
                    int(consecutive_success),
                    int(consecutive_fail),
                ),
            )

    async def upsert_default_async(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        *,
        consecutive_success: int = 0,
        consecutive_fail: int = 0,
        is_locked: bool = False,
    ) -> None:
        """Execute `upsert_default_async`."""
        await self._to_thread(
            self.upsert_default,
            agent_class,
            domain,
            model_id,
            consecutive_success=consecutive_success,
            consecutive_fail=consecutive_fail,
            is_locked=is_locked,
        )

    def atomic_increment_consecutive_success(self, agent_class: str, domain: str) -> int:
        """Execute `atomic_increment_consecutive_success`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                UPDATE class_pool_defaults
                   SET consecutive_success = consecutive_success + 1,
                       consecutive_fail = 0,
                       updated_at = datetime('now')
                 WHERE agent_class = ? AND domain = ?
             RETURNING consecutive_success
                """,
                (agent_class, domain),
            ).fetchone()
        return int(row["consecutive_success"] if row else 0)

    async def atomic_increment_consecutive_success_async(self, agent_class: str, domain: str) -> int:
        """Execute `atomic_increment_consecutive_success_async`."""
        return await self._to_thread(self.atomic_increment_consecutive_success, agent_class, domain)

    def atomic_increment_consecutive_fail(self, agent_class: str, domain: str) -> int:
        """Execute `atomic_increment_consecutive_fail`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                UPDATE class_pool_defaults
                   SET consecutive_fail = consecutive_fail + 1,
                       consecutive_success = 0,
                       updated_at = datetime('now')
                 WHERE agent_class = ? AND domain = ?
             RETURNING consecutive_fail
                """,
                (agent_class, domain),
            ).fetchone()
        return int(row["consecutive_fail"] if row else 0)

    async def atomic_increment_consecutive_fail_async(self, agent_class: str, domain: str) -> int:
        """Execute `atomic_increment_consecutive_fail_async`."""
        return await self._to_thread(self.atomic_increment_consecutive_fail, agent_class, domain)

    def reset_consecutive(self, agent_class: str, domain: str) -> None:
        """Execute `reset_consecutive`."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                UPDATE class_pool_defaults
                   SET consecutive_success = 0,
                       consecutive_fail = 0,
                       updated_at = datetime('now')
                 WHERE agent_class = ? AND domain = ?
                """,
                (agent_class, domain),
            )

    async def reset_consecutive_async(self, agent_class: str, domain: str) -> None:
        """Execute `reset_consecutive_async`."""
        await self._to_thread(self.reset_consecutive, agent_class, domain)

    def clear_default(self, agent_class: str, domain: str) -> None:
        """Execute `clear_default`."""
        with self._connect_sync() as conn:
            conn.execute(
                "DELETE FROM class_pool_defaults WHERE agent_class = ? AND domain = ?",
                (agent_class, domain),
            )

    async def clear_default_async(self, agent_class: str, domain: str) -> None:
        """Execute `clear_default_async`."""
        await self._to_thread(self.clear_default, agent_class, domain)

    # ------------------------------------------------------------------
    # Class alias / review queue
    # ------------------------------------------------------------------

    def resolve_canonical_class(self, raw_class: str) -> str | None:
        """Execute `resolve_canonical_class`."""
        normalized = _normalize_class_name(raw_class)
        if not normalized:
            return None

        if normalized in CLASS_DICT_INITIAL_SET:
            return normalized

        with self._connect_sync() as conn:
            row = conn.execute(
                """
                SELECT canonical_class
                  FROM class_aliases
                 WHERE alias_class = ? AND is_active = 1
                """,
                (normalized,),
            ).fetchone()
        return str(row["canonical_class"]) if row else None

    async def resolve_canonical_class_async(self, raw_class: str) -> str | None:
        """Execute `resolve_canonical_class_async`."""
        return await self._to_thread(self.resolve_canonical_class, raw_class)

    def upsert_class_alias(self, alias_class: str, canonical_class: str, source: str = "review") -> None:
        """Execute `upsert_class_alias`."""
        normalized_alias = _normalize_class_name(alias_class)
        normalized_canonical = _normalize_class_name(canonical_class)
        if not normalized_alias or not normalized_canonical:
            return

        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO class_aliases(alias_class, canonical_class, source, is_active)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(alias_class) DO UPDATE SET
                    canonical_class = excluded.canonical_class,
                    source = excluded.source,
                    is_active = 1,
                    updated_at = datetime('now')
                """,
                (normalized_alias, normalized_canonical, source),
            )

    async def upsert_class_alias_async(
        self,
        alias_class: str,
        canonical_class: str,
        source: str = "review",
    ) -> None:
        """Execute `upsert_class_alias_async`."""
        await self._to_thread(self.upsert_class_alias, alias_class, canonical_class, source)

    def upsert_class_review_candidate(self, normalized_class: str, proposed_by: str) -> None:
        """Execute `upsert_class_review_candidate`."""
        normalized = _normalize_class_name(normalized_class)
        if not normalized:
            return

        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO class_review_queue(normalized_class, proposed_by, hit_count, status)
                VALUES (?, ?, 1, 'pending')
                ON CONFLICT(normalized_class) DO UPDATE SET
                    hit_count = class_review_queue.hit_count + 1,
                    proposed_by = excluded.proposed_by,
                    last_seen_at = datetime('now')
                """,
                (normalized, proposed_by),
            )

    async def upsert_class_review_candidate_async(self, normalized_class: str, proposed_by: str) -> None:
        """Execute `upsert_class_review_candidate_async`."""
        await self._to_thread(self.upsert_class_review_candidate, normalized_class, proposed_by)

    def list_pending_class_reviews(self, limit: int = 100, min_hits: int = 1) -> list[dict[str, Any]]:
        """Execute `list_pending_class_reviews`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM class_review_queue
                 WHERE status = 'pending'
                   AND hit_count >= ?
                 ORDER BY hit_count DESC, last_seen_at DESC
                 LIMIT ?
                """,
                (max(1, int(min_hits)), max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    async def list_pending_class_reviews_async(
        self,
        limit: int = 100,
        min_hits: int = 1,
    ) -> list[dict[str, Any]]:
        """Execute `list_pending_class_reviews_async`."""
        return await self._to_thread(self.list_pending_class_reviews, limit, min_hits)

    def apply_class_review(
        self,
        review_id: int,
        action: str,
        canonical_class: str | None = None,
        review_note: str | None = None,
    ) -> None:
        """Execute `apply_class_review`."""
        action_normalized = str(action).strip().lower()
        if action_normalized not in {"approved", "merged", "rejected"}:
            raise ValueError(f"unsupported action={action!r}")

        with self._connect_sync() as conn:
            row = conn.execute(
                "SELECT normalized_class FROM class_review_queue WHERE id = ?",
                (review_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"review item id={review_id} not found")

            merged_to = _normalize_class_name(canonical_class or "") if action_normalized == "merged" else None
            conn.execute(
                """
                UPDATE class_review_queue
                   SET status = ?,
                       merged_to = ?,
                       reviewed_at = datetime('now'),
                       review_note = ?
                 WHERE id = ?
                """,
                (action_normalized, merged_to, review_note, review_id),
            )

            if action_normalized == "merged" and merged_to:
                self.upsert_class_alias(str(row["normalized_class"]), merged_to, source="review")

    async def apply_class_review_async(
        self,
        review_id: int,
        action: str,
        canonical_class: str | None = None,
        review_note: str | None = None,
    ) -> None:
        """Execute `apply_class_review_async`."""
        await self._to_thread(self.apply_class_review, review_id, action, canonical_class, review_note)

    def cleanup_old_class_reviews(self, retention_days: int = 90) -> int:
        """Execute `cleanup_old_class_reviews`."""
        with self._connect_sync() as conn:
            cursor = conn.execute(
                """
                DELETE FROM class_review_queue
                 WHERE status != 'pending'
                   AND reviewed_at IS NOT NULL
                   AND reviewed_at < datetime('now', ?)
                """,
                (f"-{max(1, int(retention_days))} days",),
            )
        return int(cursor.rowcount or 0)

    async def cleanup_old_class_reviews_async(self, retention_days: int = 90) -> int:
        """Execute `cleanup_old_class_reviews_async`."""
        return await self._to_thread(self.cleanup_old_class_reviews, retention_days)

    # ------------------------------------------------------------------
    # Logs and events
    # ------------------------------------------------------------------

    def log_outcome(
        self,
        request_id: str,
        agent_name: str,
        agent_class: str,
        class_source: str,
        domain: str,
        model_id: str,
        record_id: int | None,
        outcome: str,
    ) -> None:
        """Execute `log_outcome`."""
        with self._connect_sync() as conn:
            conn.execute(
                """
                INSERT INTO class_success_log(
                    request_id, agent_name, agent_class, class_source,
                    domain, model_id, record_id, outcome
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    agent_name,
                    agent_class,
                    class_source,
                    domain,
                    model_id,
                    record_id,
                    outcome,
                ),
            )

    async def log_outcome_async(
        self,
        request_id: str,
        agent_name: str,
        agent_class: str,
        class_source: str,
        domain: str,
        model_id: str,
        record_id: int | None,
        outcome: str,
    ) -> None:
        """Execute `log_outcome_async`."""
        await self._to_thread(
            self.log_outcome,
            request_id,
            agent_name,
            agent_class,
            class_source,
            domain,
            model_id,
            record_id,
            outcome,
        )

    def query_by_class(self, agent_class: str, domain: str, limit: int = 100) -> list[dict[str, Any]]:
        """Execute `query_by_class`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_success_log
                 WHERE agent_class = ? AND domain = ?
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (agent_class, domain, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def query_by_class_cross_domain(self, agent_class: str, limit: int = 100) -> list[dict[str, Any]]:
        """Execute `query_by_class_cross_domain`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT * FROM class_success_log
                 WHERE agent_class = ?
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (agent_class, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def query_all(self, limit: int = 100) -> list[dict[str, Any]]:
        """Execute `query_all`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                "SELECT * FROM class_success_log ORDER BY id DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def cleanup_old_logs(self, retention_days: int | None = None) -> int:
        """Execute `cleanup_old_logs`."""
        if retention_days is None:
            return 0
        normalized_retention_days = int(retention_days)
        if normalized_retention_days <= 0:
            return 0
        with self._connect_sync() as conn:
            cursor = conn.execute(
                "DELETE FROM class_success_log WHERE created_at < datetime('now', ?)",
                (f"-{normalized_retention_days} days",),
            )
        return int(cursor.rowcount or 0)

    async def cleanup_old_logs_async(self, retention_days: int | None = None) -> int:
        """Execute `cleanup_old_logs_async`."""
        return await self._to_thread(self.cleanup_old_logs, retention_days)

    def try_record_event(self, request_id: str, model_id: str, event_type: str, event_ts: str) -> bool:
        """Execute `try_record_event`."""
        with self._connect_sync() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO feedback_events(request_id, model_id, event_type, event_ts)
                VALUES (?, ?, ?, ?)
                """,
                (request_id, model_id, event_type, event_ts),
            )
        return int(cursor.rowcount or 0) > 0

    async def try_record_event_async(
        self,
        request_id: str,
        model_id: str,
        event_type: str,
        event_ts: str,
    ) -> bool:
        """Execute `try_record_event_async`."""
        return await self._to_thread(self.try_record_event, request_id, model_id, event_type, event_ts)

    def get_events(self, request_id: str, model_id: str) -> list[str]:
        """Execute `get_events`."""
        with self._connect_sync() as conn:
            rows = conn.execute(
                """
                SELECT event_type
                  FROM feedback_events
                 WHERE request_id = ? AND model_id = ?
                 ORDER BY id ASC
                """,
                (request_id, model_id),
            ).fetchall()
        return [str(row["event_type"]) for row in rows]

    async def get_events_async(self, request_id: str, model_id: str) -> list[str]:
        """Execute `get_events_async`."""
        return await self._to_thread(self.get_events, request_id, model_id)

    def cleanup_old_events(self, retention_days: int = 7) -> int:
        """Execute `cleanup_old_events`."""
        with self._connect_sync() as conn:
            cursor = conn.execute(
                "DELETE FROM feedback_events WHERE processed_at < datetime('now', ?)",
                (f"-{max(1, int(retention_days))} days",),
            )
        return int(cursor.rowcount or 0)

    async def cleanup_old_events_async(self, retention_days: int = 7) -> int:
        """Execute `cleanup_old_events_async`."""
        return await self._to_thread(self.cleanup_old_events, retention_days)

    # ------------------------------------------------------------------
    # Downgrade trials
    # ------------------------------------------------------------------

    def get_active_downgrade_trial(self, agent_class: str, domain: str) -> dict[str, Any] | None:
        """Execute `get_active_downgrade_trial`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM downgrade_trials
                 WHERE agent_class = ?
                   AND domain = ?
                   AND state = 'active'
                 LIMIT 1
                """,
                (agent_class, domain),
            ).fetchone()
        return dict(row) if row else None

    async def get_active_downgrade_trial_async(self, agent_class: str, domain: str) -> dict[str, Any] | None:
        """Execute `get_active_downgrade_trial_async`."""
        return await self._to_thread(self.get_active_downgrade_trial, agent_class, domain)

    def is_downgrade_in_cooldown(self, agent_class: str, domain: str, challenger_model_id: str) -> bool:
        """Execute `is_downgrade_in_cooldown`."""
        with self._connect_sync() as conn:
            row = conn.execute(
                """
                SELECT 1
                  FROM downgrade_trials
                 WHERE agent_class = ?
                   AND domain = ?
                   AND challenger_model_id = ?
                   AND state = 'rolled_back'
                   AND cooldown_until IS NOT NULL
                   AND cooldown_until > datetime('now')
                 LIMIT 1
                """,
                (agent_class, domain, challenger_model_id),
            ).fetchone()
        return row is not None

    async def is_downgrade_in_cooldown_async(
        self,
        agent_class: str,
        domain: str,
        challenger_model_id: str,
    ) -> bool:
        """Execute `is_downgrade_in_cooldown_async`."""
        return await self._to_thread(self.is_downgrade_in_cooldown, agent_class, domain, challenger_model_id)

    def start_downgrade_trial(
        self,
        agent_class: str,
        domain: str,
        incumbent_model_id: str,
        challenger_model_id: str,
        expected_savings_ratio: float,
        canary_ratio: float = 0.5,
    ) -> bool:
        """Execute `start_downgrade_trial`."""
        try:
            with self._connect_sync() as conn:
                conn.execute(
                    """
                    INSERT INTO downgrade_trials(
                        agent_class, domain,
                        incumbent_model_id, challenger_model_id,
                        state, sampled_requests,
                        quality_fail_count, exec_fail_count, success_count,
                        canary_ratio, expected_savings_ratio
                    )
                    VALUES (?, ?, ?, ?, 'active', 0, 0, 0, 0, ?, ?)
                    """,
                    (
                        agent_class,
                        domain,
                        incumbent_model_id,
                        challenger_model_id,
                        float(canary_ratio),
                        float(expected_savings_ratio),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    async def start_downgrade_trial_async(
        self,
        agent_class: str,
        domain: str,
        incumbent_model_id: str,
        challenger_model_id: str,
        expected_savings_ratio: float,
        canary_ratio: float = 0.5,
    ) -> bool:
        """Execute `start_downgrade_trial_async`."""
        return await self._to_thread(
            self.start_downgrade_trial,
            agent_class,
            domain,
            incumbent_model_id,
            challenger_model_id,
            expected_savings_ratio,
            canary_ratio,
        )

    def record_downgrade_trial_observation(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> dict[str, Any] | None:
        """Execute `record_downgrade_trial_observation`."""
        with self._connect_sync() as conn:
            trial_row = conn.execute(
                """
                SELECT *
                  FROM downgrade_trials
                 WHERE agent_class = ?
                   AND domain = ?
                   AND state = 'active'
                 LIMIT 1
                """,
                (agent_class, domain),
            ).fetchone()
            if trial_row is None:
                return None

            trial = dict(trial_row)
            if model_id != trial["challenger_model_id"]:
                return trial

            quality_inc = 1 if outcome_type == "quality_fail" else 0
            exec_inc = 1 if outcome_type == "exec_fail" else 0
            success_inc = 1 if outcome_type == "quality_good" else 0

            conn.execute(
                """
                UPDATE downgrade_trials
                   SET sampled_requests = sampled_requests + 1,
                       quality_fail_count = quality_fail_count + ?,
                       exec_fail_count = exec_fail_count + ?,
                       success_count = success_count + ?,
                       updated_at = datetime('now')
                 WHERE id = ?
                """,
                (quality_inc, exec_inc, success_inc, trial["id"]),
            )

            updated = conn.execute(
                "SELECT * FROM downgrade_trials WHERE id = ?",
                (trial["id"],),
            ).fetchone()
            return dict(updated) if updated else None

    async def record_downgrade_trial_observation_async(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> dict[str, Any] | None:
        """Execute `record_downgrade_trial_observation_async`."""
        return await self._to_thread(
            self.record_downgrade_trial_observation,
            agent_class,
            domain,
            model_id,
            outcome_type,
        )

    def finish_downgrade_trial(
        self,
        agent_class: str,
        domain: str,
        result: str,
        cooldown_h: int = 24,
    ) -> None:
        """Execute `finish_downgrade_trial`."""
        normalized = str(result).strip().lower()
        if normalized not in {"promoted", "rolled_back"}:
            raise ValueError(f"unsupported downgrade result={result!r}")

        with self._connect_sync() as conn:
            if normalized == "rolled_back":
                conn.execute(
                    """
                    UPDATE downgrade_trials
                       SET state = 'rolled_back',
                           ended_at = datetime('now'),
                           cooldown_until = datetime('now', ?),
                           updated_at = datetime('now')
                     WHERE agent_class = ? AND domain = ? AND state = 'active'
                    """,
                    (f"+{max(1, int(cooldown_h))} hours", agent_class, domain),
                )
            else:
                conn.execute(
                    """
                    UPDATE downgrade_trials
                       SET state = 'promoted',
                           ended_at = datetime('now'),
                           cooldown_until = NULL,
                           updated_at = datetime('now')
                     WHERE agent_class = ? AND domain = ? AND state = 'active'
                    """,
                    (agent_class, domain),
                )

    async def finish_downgrade_trial_async(
        self,
        agent_class: str,
        domain: str,
        result: str,
        cooldown_h: int = 24,
    ) -> None:
        """Execute `finish_downgrade_trial_async`."""
        await self._to_thread(self.finish_downgrade_trial, agent_class, domain, result, cooldown_h)

    def cleanup_old_downgrade_trials(self, retention_days: int = 30) -> int:
        """Execute `cleanup_old_downgrade_trials`."""
        with self._connect_sync() as conn:
            cursor = conn.execute(
                """
                DELETE FROM downgrade_trials
                 WHERE state != 'active'
                   AND ended_at IS NOT NULL
                   AND ended_at < datetime('now', ?)
                """,
                (f"-{max(1, int(retention_days))} days",),
            )
        return int(cursor.rowcount or 0)

    async def cleanup_old_downgrade_trials_async(self, retention_days: int = 30) -> int:
        """Execute `cleanup_old_downgrade_trials_async`."""
        return await self._to_thread(self.cleanup_old_downgrade_trials, retention_days)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def normalize_domain_key(self, domain: str | None) -> str:
        """Execute `normalize_domain_key`."""
        if not domain:
            return DEFAULT_DOMAIN_KEY
        return domain.strip() if domain.strip() else DEFAULT_DOMAIN_KEY
