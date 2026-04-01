"""Local persistent storage for agent mappings and pool snapshots."""

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir

_ALLOWED_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_ALLOW_EXTERNAL_DB_PATHS_ENV = "ROUTE_AGENT_ALLOW_EXTERNAL_DB_PATHS"


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return whether `path` is within `root` after resolution."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_sqlite_path(db_path: str | Path) -> Path:
    """Validate and normalize a client-side SQLite path."""
    candidate = Path(db_path).expanduser()
    resolved = candidate.resolve(strict=False)
    suffix = resolved.suffix.lower()
    if suffix not in _ALLOWED_SQLITE_SUFFIXES:
        raise ValueError(
            "SQLite paths must use one of the approved suffixes: "
            + ", ".join(sorted(_ALLOWED_SQLITE_SUFFIXES))
        )

    if os.getenv(_ALLOW_EXTERNAL_DB_PATHS_ENV) == "1":
        return resolved

    allowed_roots = (
        Path.cwd().resolve(),
        Path(gettempdir()).resolve(),
    )
    if any(_is_relative_to(resolved, root) for root in allowed_roots):
        return resolved

    raise ValueError(
        "SQLite paths must stay within the current workspace or system temp directory. "
        f"Set {_ALLOW_EXTERNAL_DB_PATHS_ENV}=1 to opt in to an external path."
    )


def _utc_now_iso() -> str:
    """Return the current timezone-aware UTC timestamp as ISO 8601 text."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AgentMappingEntry:
    """Persistent app-scoped agent mapping. No TTL."""

    app_id: str
    agent_name: str
    agent_class: str
    agent_version: str = "v1"
    source: str = "declared"  # "declared" | "learned" | "manual"
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class LocalPoolSnapshot:
    """Local ordered replica of class pool. No TTL."""

    agent_class: str
    ordered_model_ids: tuple[str, ...]
    default_model_id: str | None
    pool_version: int
    synced_at: datetime | None = None


@dataclass(frozen=True)
class FederationScoreEntry:
    """Cached federation score for one model in one class."""

    agent_class: str
    model_id: str
    success_count: int = 0
    fail_count: int = 0
    total_count: int = 0
    success_rate: float = 0.0
    synced_at: datetime | None = None


class LocalStore:
    """SQLite-backed local storage for agent mappings and pool snapshots."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize local store with SQLite database."""
        self._db_path = validate_sqlite_path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_mappings (
                    app_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    agent_class TEXT NOT NULL,
                    agent_version TEXT NOT NULL DEFAULT 'v1',
                    source TEXT NOT NULL DEFAULT 'declared',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (app_id, agent_name)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pool_snapshots (
                    agent_class TEXT PRIMARY KEY,
                    ordered_model_ids TEXT NOT NULL,
                    default_model_id TEXT,
                    pool_version INTEGER NOT NULL,
                    synced_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS federation_scores (
                    agent_class TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    fail_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    success_rate REAL NOT NULL DEFAULT 0.0,
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (agent_class, model_id)
                )
            """)
            conn.commit()
        finally:
            conn.close()

    async def get_agent_mapping(
        self, app_id: str, agent_name: str
    ) -> AgentMappingEntry | None:
        """Retrieve agent mapping by app_id and agent_name."""

        def _query() -> AgentMappingEntry | None:
            """Execute the blocking SQLite lookup for one agent mapping."""
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT app_id, agent_name, agent_class, agent_version,
                           source, created_at, updated_at
                    FROM agent_mappings
                    WHERE app_id = ? AND agent_name = ?
                    """,
                    (app_id, agent_name),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return AgentMappingEntry(
                    app_id=row[0],
                    agent_name=row[1],
                    agent_class=row[2],
                    agent_version=row[3],
                    source=row[4],
                    created_at=datetime.fromisoformat(row[5]),
                    updated_at=datetime.fromisoformat(row[6]),
                )
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def save_agent_mapping(
        self,
        app_id: str,
        agent_name: str,
        agent_class: str,
        agent_version: str = "v1",
        source: str = "declared",
    ) -> None:
        """Save or update agent mapping."""

        def _upsert() -> None:
            """Execute the blocking SQLite upsert for one agent mapping."""
            conn = sqlite3.connect(self._db_path)
            try:
                now = _utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO agent_mappings
                        (app_id, agent_name, agent_class, agent_version, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(app_id, agent_name) DO UPDATE SET
                        agent_class = excluded.agent_class,
                        agent_version = excluded.agent_version,
                        source = excluded.source,
                        updated_at = excluded.updated_at
                    """,
                    (app_id, agent_name, agent_class, agent_version, source, now, now),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_upsert)

    async def list_agent_mappings(self, app_id: str) -> list[AgentMappingEntry]:
        """Return all agent mappings stored for one application."""

        def _query() -> list[AgentMappingEntry]:
            """Execute the blocking SQLite lookup for one app's mappings."""
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT app_id, agent_name, agent_class, agent_version,
                           source, created_at, updated_at
                    FROM agent_mappings
                    WHERE app_id = ?
                    ORDER BY agent_name
                    """,
                    (app_id,),
                )
                rows = cursor.fetchall()
                return [
                    AgentMappingEntry(
                        app_id=row[0],
                        agent_name=row[1],
                        agent_class=row[2],
                        agent_version=row[3],
                        source=row[4],
                        created_at=datetime.fromisoformat(row[5]),
                        updated_at=datetime.fromisoformat(row[6]),
                    )
                    for row in rows
                ]
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def get_pool_snapshot(
        self, agent_class: str
    ) -> LocalPoolSnapshot | None:
        """Retrieve pool snapshot for a class."""

        def _query() -> LocalPoolSnapshot | None:
            """Execute the blocking SQLite lookup for one pool snapshot."""
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT agent_class, ordered_model_ids, default_model_id,
                           pool_version, synced_at
                    FROM pool_snapshots
                    WHERE agent_class = ?
                    """,
                    (agent_class,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return LocalPoolSnapshot(
                    agent_class=row[0],
                    ordered_model_ids=tuple(filter(None, row[1].split(","))),
                    default_model_id=row[2] if row[2] else None,
                    pool_version=row[3],
                    synced_at=datetime.fromisoformat(row[4]),
                )
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def save_pool_snapshot(
        self,
        agent_class: str,
        ordered_model_ids: list[str] | tuple[str, ...],
        default_model_id: str | None,
        pool_version: int,
    ) -> None:
        """Save or update pool snapshot."""

        def _upsert() -> None:
            """Execute the blocking SQLite upsert for one pool snapshot."""
            conn = sqlite3.connect(self._db_path)
            try:
                now = _utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO pool_snapshots
                        (agent_class, ordered_model_ids, default_model_id, pool_version, synced_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(agent_class) DO UPDATE SET
                        ordered_model_ids = excluded.ordered_model_ids,
                        default_model_id = excluded.default_model_id,
                        pool_version = excluded.pool_version,
                        synced_at = excluded.synced_at
                    """,
                    (agent_class, ",".join(ordered_model_ids), default_model_id, pool_version, now),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_upsert)

    async def list_pool_snapshots(self) -> list[LocalPoolSnapshot]:
        """Return all persisted pool snapshots ordered by class name."""

        def _query() -> list[LocalPoolSnapshot]:
            """Execute the blocking SQLite lookup for all pool snapshots."""
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT agent_class, ordered_model_ids, default_model_id,
                           pool_version, synced_at
                    FROM pool_snapshots
                    ORDER BY agent_class
                    """
                )
                rows = cursor.fetchall()
                return [
                    LocalPoolSnapshot(
                        agent_class=row[0],
                        ordered_model_ids=tuple(filter(None, row[1].split(","))),
                        default_model_id=row[2] if row[2] else None,
                        pool_version=row[3],
                        synced_at=datetime.fromisoformat(row[4]),
                    )
                    for row in rows
                ]
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    # ------------------------------------------------------------------
    # Federation scores
    # ------------------------------------------------------------------

    async def save_federation_scores(
        self,
        agent_class: str,
        scores: list[dict],
    ) -> None:
        """Replace all federation scores for one class with fresh data."""

        def _upsert() -> None:
            """Delete existing scores for the class and insert the new batch."""
            conn = sqlite3.connect(self._db_path)
            try:
                now = _utc_now_iso()
                conn.execute(
                    "DELETE FROM federation_scores WHERE agent_class = ?",
                    (agent_class,),
                )
                for s in scores:
                    conn.execute(
                        """
                        INSERT INTO federation_scores
                            (agent_class, model_id, success_count, fail_count,
                             total_count, success_rate, synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            agent_class,
                            s["model_id"],
                            int(s.get("success_count") or 0),
                            int(s.get("fail_count") or 0),
                            int(s.get("total_count") or 0),
                            float(s.get("success_rate") or 0.0),
                            now,
                        ),
                    )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_upsert)

    async def get_federation_scores(
        self,
        agent_class: str,
    ) -> list[FederationScoreEntry]:
        """Return cached federation scores for one class."""

        def _query() -> list[FederationScoreEntry]:
            """Query all federation score rows for the given agent class."""
            conn = sqlite3.connect(self._db_path)
            try:
                cursor = conn.execute(
                    """
                    SELECT agent_class, model_id, success_count, fail_count,
                           total_count, success_rate, synced_at
                    FROM federation_scores
                    WHERE agent_class = ?
                    ORDER BY success_rate DESC
                    """,
                    (agent_class,),
                )
                return [
                    FederationScoreEntry(
                        agent_class=row[0],
                        model_id=row[1],
                        success_count=row[2],
                        fail_count=row[3],
                        total_count=row[4],
                        success_rate=row[5],
                        synced_at=datetime.fromisoformat(row[6]),
                    )
                    for row in cursor.fetchall()
                ]
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

