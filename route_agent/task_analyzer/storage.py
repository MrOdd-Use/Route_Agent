"""SQLite local storage for task analysis records (async + sync)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS analysis_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name       TEXT    NOT NULL,
    prompt           TEXT    NOT NULL,
    domain           TEXT,
    dimensions       TEXT,
    analyzer_model   TEXT    NOT NULL,
    routed_model     TEXT,
    success          INTEGER NOT NULL,
    token_usage      TEXT,
    response_time_ms INTEGER,
    feedback         TEXT,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_records_agent   ON analysis_records(agent_name);
CREATE INDEX IF NOT EXISTS idx_records_created ON analysis_records(created_at);
"""

_INSERT_SQL = """\
INSERT INTO analysis_records
    (agent_name, prompt, domain, dimensions, analyzer_model, routed_model,
     success, token_usage, response_time_ms)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# ---------------------------------------------------------------------------
# Record dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnalysisRecord:
    """One task analysis record persisted to SQLite."""

    agent_name: str
    prompt: str
    domain: str | None
    dimensions: tuple[dict[str, Any], ...] | None
    analyzer_model: str
    routed_model: str | None
    success: bool
    token_usage: dict[str, int] | None = None
    response_time_ms: int | None = None
    feedback: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record_to_params(record: AnalysisRecord) -> tuple[Any, ...]:
    """Execute `_record_to_params`."""
    return (
        record.agent_name,
        record.prompt,
        record.domain,
        json.dumps(record.dimensions, ensure_ascii=False) if record.dimensions is not None else None,
        record.analyzer_model,
        record.routed_model,
        int(record.success),
        json.dumps(record.token_usage, ensure_ascii=False) if record.token_usage is not None else None,
        record.response_time_ms,
    )


def _default_db_path() -> Path:
    """Execute `_default_db_path`."""
    return Path(__file__).resolve().parents[2] / "data" / "task_analysis.db"


def _normalize_feedback(raw_feedback: str | None) -> dict[str, Any]:
    """Execute `_normalize_feedback`."""
    if not raw_feedback:
        return {"execution": None, "quality": None}

    try:
        payload = json.loads(raw_feedback)
    except json.JSONDecodeError:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    payload.setdefault("execution", None)
    payload.setdefault("quality", None)
    return payload


def _build_execution_feedback(
    current_feedback: dict[str, Any],
    *,
    completed: bool,
    error_type: str | None,
    error_detail: str | None,
) -> dict[str, Any]:
    """Execute `_build_execution_feedback`."""
    merged = dict(current_feedback)
    merged["execution"] = {
        "completed": completed,
        "error_type": error_type,
        "error_detail": error_detail,
    }
    merged.setdefault("quality", None)
    return merged


# ---------------------------------------------------------------------------
# Storage class
# ---------------------------------------------------------------------------


class AnalysisStorage:
    """SQLite storage for analysis records with async + sync interfaces."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize the instance."""
        self._db_path = db_path or _default_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()

    def _init_db_sync(self) -> None:
        """Execute `_init_db_sync`."""
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(_CREATE_TABLES_SQL)

    # -- save ---------------------------------------------------------------

    async def save_async(self, record: AnalysisRecord) -> int:
        """Save record asynchronously and return row id."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(_INSERT_SQL, _record_to_params(record))
            await db.commit()
            return cursor.lastrowid  # type: ignore[return-value]

    def save(self, record: AnalysisRecord) -> int:
        """Save record synchronously and return row id."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(_INSERT_SQL, _record_to_params(record))
            return cursor.lastrowid  # type: ignore[return-value]

    # -- update routed_model ------------------------------------------------

    async def update_routed_model_async(self, record_id: int, routed_model: str) -> None:
        """Execute `update_routed_model_async`."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE analysis_records SET routed_model = ? WHERE id = ?",
                (routed_model, record_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No analysis record found with id={record_id}")
            await db.commit()

    def update_routed_model(self, record_id: int, routed_model: str) -> None:
        """Execute `update_routed_model`."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "UPDATE analysis_records SET routed_model = ? WHERE id = ?",
                (routed_model, record_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"No analysis record found with id={record_id}")

    # -- update execution result (auto) ------------------------------------

    async def update_execution_result_async(
        self,
        record_id: int,
        *,
        completed: bool,
        error_type: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Execute `update_execution_result_async`."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"No analysis record found with id={record_id}")

            feedback = _normalize_feedback(row[0])
            feedback = _build_execution_feedback(
                feedback,
                completed=completed,
                error_type=error_type,
                error_detail=error_detail,
            )

            await db.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )
            await db.commit()

    def update_execution_result(
        self,
        record_id: int,
        *,
        completed: bool,
        error_type: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        """Execute `update_execution_result`."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?",
                (record_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"No analysis record found with id={record_id}")

            feedback = _normalize_feedback(row[0])
            feedback = _build_execution_feedback(
                feedback,
                completed=completed,
                error_type=error_type,
                error_detail=error_detail,
            )

            conn.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )

    # -- update quality review (manual) ------------------------------------

    async def update_quality_review_async(
        self,
        record_id: int,
        *,
        rating: str,
        action: str | None = None,
        note: str | None = None,
    ) -> None:
        """Append feedback.quality while preserving existing feedback.execution."""
        import aiosqlite

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"No analysis record found with id={record_id}")
            feedback = _normalize_feedback(row[0])
            feedback["quality"] = {"rating": rating, "action": action, "note": note}
            await db.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )
            await db.commit()

    def update_quality_review(
        self,
        record_id: int,
        *,
        rating: str,
        action: str | None = None,
        note: str | None = None,
    ) -> None:
        """Execute `update_quality_review`."""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?",
                (record_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"No analysis record found with id={record_id}")
            feedback = _normalize_feedback(row[0])
            feedback["quality"] = {"rating": rating, "action": action, "note": note}
            conn.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )
