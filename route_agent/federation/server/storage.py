"""SQLite persistent storage for federation server.

Tables:
- apps                   — registered applications
- registered_agents      — app-scoped agent → class mappings
- pool_versions          — versioned class pool snapshots
- federation_outcome_stats — per-class per-model outcome accumulator

Lease hot-path state lives in lease_store.py (in-memory), not here.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from typing import Generator

from route_agent.federation.schemas import (
    AppRegistration,
    PoolVersionEntry,
    RegisteredAgent,
)

_CREATE_TABLES_SQL = """\
CREATE TABLE IF NOT EXISTS apps (
    app_id          TEXT PRIMARY KEY,
    app_name        TEXT NOT NULL,
    registered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS registered_agents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id          TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    agent_class     TEXT NOT NULL,
    agent_version   TEXT NOT NULL DEFAULT 'v1',
    registered_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(app_id, agent_name)
);

CREATE INDEX IF NOT EXISTS idx_registered_agents_app
    ON registered_agents(app_id);

CREATE TABLE IF NOT EXISTS pool_versions (
    agent_class     TEXT PRIMARY KEY,
    version         INTEGER NOT NULL DEFAULT 1,
    model_ids_json  TEXT NOT NULL DEFAULT '[]',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS federation_outcome_stats (
    agent_class          TEXT NOT NULL,
    model_id             TEXT NOT NULL,
    success_count        INTEGER NOT NULL DEFAULT 0,
    fail_count           INTEGER NOT NULL DEFAULT 0,
    quality_good         INTEGER NOT NULL DEFAULT 0,
    quality_poor         INTEGER NOT NULL DEFAULT 0,
    total_count          INTEGER NOT NULL DEFAULT 0,
    last_reorder_count   INTEGER NOT NULL DEFAULT 0,
    updated_at           TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY(agent_class, model_id)
);

CREATE INDEX IF NOT EXISTS idx_outcome_stats_class
    ON federation_outcome_stats(agent_class);
"""

_ALLOWED_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_ALLOW_EXTERNAL_DB_PATHS_ENV = "ROUTE_AGENT_ALLOW_EXTERNAL_DB_PATHS"


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return whether `path` is within `root` after resolution."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_federation_db_path(db_path: Path | str) -> Path:
    """Validate and normalize a federation SQLite path."""
    candidate = Path(db_path).expanduser()
    resolved = candidate.resolve(strict=False)
    suffix = resolved.suffix.lower()
    if suffix not in _ALLOWED_SQLITE_SUFFIXES:
        raise ValueError(
            "Federation SQLite paths must use one of the approved suffixes: "
            + ", ".join(sorted(_ALLOWED_SQLITE_SUFFIXES))
        )

    if os.getenv(_ALLOW_EXTERNAL_DB_PATHS_ENV) == "1":
        return resolved

    repo_root = Path(__file__).resolve().parents[4]
    allowed_roots = (
        repo_root.resolve(),
        Path.cwd().resolve(),
        Path(gettempdir()).resolve(),
    )
    if any(_is_relative_to(resolved, root) for root in allowed_roots):
        return resolved

    raise ValueError(
        "Federation SQLite paths must stay within the repository, current workspace, "
        "or system temp directory. "
        f"Set {_ALLOW_EXTERNAL_DB_PATHS_ENV}=1 to allow an external path."
    )


def _default_db_path() -> Path:
    """Return the default SQLite path for federation storage."""
    return validate_federation_db_path(
        Path(__file__).resolve().parents[4] / "data" / "federation.db"
    )


def _parse_dt(value: str) -> datetime:
    """Parse an ISO datetime string to a timezone-aware UTC datetime."""
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(tz=timezone.utc).isoformat()


class FederationStorage:
    """SQLite storage for federation server state."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialise storage, create the parent directory, and run schema migrations."""
        self._db_path = validate_federation_db_path(db_path or _default_db_path())
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db_sync()

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection with WAL mode and a busy timeout."""
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def _transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager that commits on success and rolls back on exception."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db_sync(self) -> None:
        """Create all federation tables if they do not already exist."""
        with self._transaction() as conn:
            conn.executescript(_CREATE_TABLES_SQL)

    # ------------------------------------------------------------------
    # App registration
    # ------------------------------------------------------------------

    def upsert_app_sync(self, app_id: str, app_name: str) -> AppRegistration:
        """Insert or update an app record; preserve registered_at on conflict."""
        now = _now_iso()
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO apps(app_id, app_name, registered_at, last_seen_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(app_id) DO UPDATE SET
                    app_name    = excluded.app_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (app_id, app_name, now, now),
            )
            row = conn.execute(
                "SELECT * FROM apps WHERE app_id = ?", (app_id,)
            ).fetchone()
        return AppRegistration(
            app_id=row["app_id"],
            app_name=row["app_name"],
            registered_at=_parse_dt(row["registered_at"]),
            last_seen_at=_parse_dt(row["last_seen_at"]),
        )

    async def upsert_app_async(self, app_id: str, app_name: str) -> AppRegistration:
        """Async wrapper for upsert_app_sync."""
        return await asyncio.to_thread(self.upsert_app_sync, app_id, app_name)

    def get_app_sync(self, app_id: str) -> AppRegistration | None:
        """Return the AppRegistration for app_id, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM apps WHERE app_id = ?", (app_id,)
            ).fetchone()
        if row is None:
            return None
        return AppRegistration(
            app_id=row["app_id"],
            app_name=row["app_name"],
            registered_at=_parse_dt(row["registered_at"]),
            last_seen_at=_parse_dt(row["last_seen_at"]),
        )

    async def get_app_async(self, app_id: str) -> AppRegistration | None:
        """Async wrapper for get_app_sync."""
        return await asyncio.to_thread(self.get_app_sync, app_id)

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    def upsert_agent_sync(
        self,
        app_id: str,
        agent_name: str,
        agent_class: str,
        agent_version: str = "v1",
    ) -> RegisteredAgent:
        """Insert or update an agent mapping; preserve registered_at on conflict."""
        now = _now_iso()
        with self._transaction() as conn:
            # Preserve original registered_at on conflict
            existing = conn.execute(
                "SELECT registered_at FROM registered_agents WHERE app_id = ? AND agent_name = ?",
                (app_id, agent_name),
            ).fetchone()
            registered_at = existing["registered_at"] if existing else now

            conn.execute(
                """
                INSERT INTO registered_agents
                    (app_id, agent_name, agent_class, agent_version, registered_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(app_id, agent_name) DO UPDATE SET
                    agent_class   = excluded.agent_class,
                    agent_version = excluded.agent_version,
                    updated_at    = excluded.updated_at
                """,
                (app_id, agent_name, agent_class, agent_version, registered_at, now),
            )
        return RegisteredAgent(
            app_id=app_id,
            agent_name=agent_name,
            agent_class=agent_class,
            agent_version=agent_version,
            registered_at=_parse_dt(registered_at),
            updated_at=_parse_dt(now),
        )

    async def upsert_agent_async(
        self,
        app_id: str,
        agent_name: str,
        agent_class: str,
        agent_version: str = "v1",
    ) -> RegisteredAgent:
        """Async wrapper for upsert_agent_sync."""
        return await asyncio.to_thread(
            self.upsert_agent_sync, app_id, agent_name, agent_class, agent_version
        )

    def get_agent_sync(self, app_id: str, agent_name: str) -> RegisteredAgent | None:
        """Return the RegisteredAgent for (app_id, agent_name), or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM registered_agents WHERE app_id = ? AND agent_name = ?",
                (app_id, agent_name),
            ).fetchone()
        if row is None:
            return None
        return RegisteredAgent(
            app_id=row["app_id"],
            agent_name=row["agent_name"],
            agent_class=row["agent_class"],
            agent_version=row["agent_version"],
            registered_at=_parse_dt(row["registered_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    async def get_agent_async(self, app_id: str, agent_name: str) -> RegisteredAgent | None:
        """Async wrapper for get_agent_sync."""
        return await asyncio.to_thread(self.get_agent_sync, app_id, agent_name)

    def list_agents_sync(self, app_id: str) -> list[RegisteredAgent]:
        """Return all registered agents for app_id, ordered by agent_name."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM registered_agents WHERE app_id = ? ORDER BY agent_name",
                (app_id,),
            ).fetchall()
        return [
            RegisteredAgent(
                app_id=row["app_id"],
                agent_name=row["agent_name"],
                agent_class=row["agent_class"],
                agent_version=row["agent_version"],
                registered_at=_parse_dt(row["registered_at"]),
                updated_at=_parse_dt(row["updated_at"]),
            )
            for row in rows
        ]

    async def list_agents_async(self, app_id: str) -> list[RegisteredAgent]:
        """Async wrapper for list_agents_sync."""
        return await asyncio.to_thread(self.list_agents_sync, app_id)

    # ------------------------------------------------------------------
    # Pool versions
    # ------------------------------------------------------------------

    def upsert_pool_version_sync(
        self,
        agent_class: str,
        model_ids: tuple[str, ...],
    ) -> PoolVersionEntry:
        """Insert or increment the pool version for agent_class; return the updated entry."""
        now = _now_iso()
        model_ids_json = json.dumps(list(model_ids))
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO pool_versions(agent_class, version, model_ids_json, updated_at)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(agent_class) DO UPDATE SET
                    version        = version + 1,
                    model_ids_json = excluded.model_ids_json,
                    updated_at     = excluded.updated_at
                """,
                (agent_class, model_ids_json, now),
            )
            row = conn.execute(
                "SELECT * FROM pool_versions WHERE agent_class = ?", (agent_class,)
            ).fetchone()
        return PoolVersionEntry(
            version=row["version"],
            agent_class=row["agent_class"],
            model_ids=tuple(json.loads(row["model_ids_json"])),
            updated_at=_parse_dt(row["updated_at"]),
        )

    async def upsert_pool_version_async(
        self,
        agent_class: str,
        model_ids: tuple[str, ...],
    ) -> PoolVersionEntry:
        """Async wrapper for upsert_pool_version_sync."""
        return await asyncio.to_thread(self.upsert_pool_version_sync, agent_class, model_ids)

    def get_pool_version_sync(self, agent_class: str) -> PoolVersionEntry | None:
        """Return the current PoolVersionEntry for agent_class, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pool_versions WHERE agent_class = ?", (agent_class,)
            ).fetchone()
        if row is None:
            return None
        return PoolVersionEntry(
            version=row["version"],
            agent_class=row["agent_class"],
            model_ids=tuple(json.loads(row["model_ids_json"])),
            updated_at=_parse_dt(row["updated_at"]),
        )

    async def get_pool_version_async(self, agent_class: str) -> PoolVersionEntry | None:
        """Async wrapper for get_pool_version_sync."""
        return await asyncio.to_thread(self.get_pool_version_sync, agent_class)

    def get_pool_versions_sync(self, agent_classes: list[str]) -> dict[str, int]:
        """Return {agent_class: version} for the requested classes."""
        if not agent_classes:
            return {}
        placeholders = ",".join("?" * len(agent_classes))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT agent_class, version FROM pool_versions WHERE agent_class IN ({placeholders})",
                agent_classes,
            ).fetchall()
        return {row["agent_class"]: row["version"] for row in rows}

    async def get_pool_versions_async(self, agent_classes: list[str]) -> dict[str, int]:
        """Async wrapper for get_pool_versions_sync."""
        return await asyncio.to_thread(self.get_pool_versions_sync, agent_classes)

    # ------------------------------------------------------------------
    # Outcome stats (federation cross-app accumulator)
    # ------------------------------------------------------------------

    def increment_outcome_stat_sync(
        self,
        agent_class: str,
        model_id: str,
        outcome_type: str,
    ) -> dict[str, int]:
        """Increment the appropriate counter for (agent_class, model_id) and return updated row."""
        now = _now_iso()
        statements = {
            "exec_success": """
                INSERT INTO federation_outcome_stats(
                    agent_class, model_id, success_count, total_count, updated_at
                )
                VALUES (?, ?, 1, 1, ?)
                ON CONFLICT(agent_class, model_id) DO UPDATE SET
                    success_count = success_count + 1,
                    total_count = total_count + 1,
                    updated_at = excluded.updated_at
                """,
            "exec_fail": """
                INSERT INTO federation_outcome_stats(
                    agent_class, model_id, fail_count, total_count, updated_at
                )
                VALUES (?, ?, 1, 1, ?)
                ON CONFLICT(agent_class, model_id) DO UPDATE SET
                    fail_count = fail_count + 1,
                    total_count = total_count + 1,
                    updated_at = excluded.updated_at
                """,
            "quality_good": """
                INSERT INTO federation_outcome_stats(
                    agent_class, model_id, quality_good, total_count, updated_at
                )
                VALUES (?, ?, 1, 1, ?)
                ON CONFLICT(agent_class, model_id) DO UPDATE SET
                    quality_good = quality_good + 1,
                    total_count = total_count + 1,
                    updated_at = excluded.updated_at
                """,
            "quality_poor": """
                INSERT INTO federation_outcome_stats(
                    agent_class, model_id, quality_poor, total_count, updated_at
                )
                VALUES (?, ?, 1, 1, ?)
                ON CONFLICT(agent_class, model_id) DO UPDATE SET
                    quality_poor = quality_poor + 1,
                    total_count = total_count + 1,
                    updated_at = excluded.updated_at
                """,
        }
        statement = statements.get(outcome_type)
        if statement is None:
            raise ValueError(f"unsupported outcome_type: {outcome_type}")

        with self._transaction() as conn:
            conn.execute(
                statement,
                (agent_class, model_id, now),
            )
            row = conn.execute(
                "SELECT * FROM federation_outcome_stats WHERE agent_class = ? AND model_id = ?",
                (agent_class, model_id),
            ).fetchone()

        return dict(row)

    async def increment_outcome_stat_async(
        self,
        agent_class: str,
        model_id: str,
        outcome_type: str,
    ) -> dict[str, int]:
        """Async wrapper for increment_outcome_stat_sync."""
        return await asyncio.to_thread(
            self.increment_outcome_stat_sync, agent_class, model_id, outcome_type
        )

    def get_outcome_stats_for_class_sync(self, agent_class: str) -> list[dict[str, int]]:
        """Return all outcome stat rows for the given class, ordered by success_count desc."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM federation_outcome_stats
                WHERE agent_class = ?
                ORDER BY success_count DESC
                """,
                (agent_class,),
            ).fetchall()
        return [dict(row) for row in rows]

    async def get_outcome_stats_for_class_async(self, agent_class: str) -> list[dict[str, int]]:
        """Async wrapper for get_outcome_stats_for_class_sync."""
        return await asyncio.to_thread(self.get_outcome_stats_for_class_sync, agent_class)

    def mark_reorder_sync(self, agent_class: str, model_id: str, current_total: int) -> None:
        """Update last_reorder_count to current_total after a reorder decision."""
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE federation_outcome_stats
                SET last_reorder_count = ?, updated_at = ?
                WHERE agent_class = ? AND model_id = ?
                """,
                (current_total, _now_iso(), agent_class, model_id),
            )

    async def mark_reorder_async(self, agent_class: str, model_id: str, current_total: int) -> None:
        """Async wrapper for mark_reorder_sync."""
        await asyncio.to_thread(self.mark_reorder_sync, agent_class, model_id, current_total)
