"""SQLite persistent cache for Arena leaderboard data.

Stores scraped leaderboard entries so the system can survive restarts
without re-fetching from arena.ai every time.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from route_agent.model_registry.arena.schemas import ArenaLeaderboard, ArenaModelEntry
from route_agent.model_registry.constants import (
    ARENA_CACHE_DB_PATH,
    DEFAULT_CACHE_TTL_SECONDS,
)

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS arena_leaderboard_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category      TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    organization  TEXT    NOT NULL DEFAULT '',
    arena_score   INTEGER NOT NULL,
    votes         INTEGER NOT NULL DEFAULT 0,
    rank          INTEGER NOT NULL,
    total_in_cat  INTEGER NOT NULL DEFAULT 0,
    fetched_at    TEXT    NOT NULL
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_arena_cache_category
    ON arena_leaderboard_cache (category);
"""


class ArenaCacheStorage:
    """SQLite-backed cache for Arena leaderboard data."""

    def __init__(
        self,
        db_path: str | None = None,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        self._db_path = db_path or os.getenv("ARENA_CACHE_DB_PATH", ARENA_CACHE_DB_PATH)
        self._ttl = ttl_seconds
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create database and table if they don't exist."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_CREATE_TABLE_SQL + _CREATE_INDEX_SQL)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=10)

    def save(self, leaderboard: ArenaLeaderboard) -> None:
        """Persist leaderboard (full replace)."""
        if leaderboard.is_empty:
            logger.debug("Skipping save of empty leaderboard")
            return

        rows: list[tuple[Any, ...]] = []
        for category, entries in leaderboard.categories.items():
            for entry in entries:
                rows.append((
                    entry.category,
                    entry.name,
                    entry.organization,
                    entry.arena_score,
                    entry.votes,
                    entry.rank,
                    entry.total_in_category,
                    leaderboard.fetched_at,
                ))

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute("DELETE FROM arena_leaderboard_cache")
                conn.executemany(
                    "INSERT INTO arena_leaderboard_cache "
                    "(category, name, organization, arena_score, votes, rank, total_in_cat, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info("Saved %d arena entries to cache", len(rows))

    def load(self) -> ArenaLeaderboard | None:
        """Load cached leaderboard. Returns None if cache is expired or empty."""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "SELECT category, name, organization, arena_score, votes, "
                    "rank, total_in_cat, fetched_at "
                    "FROM arena_leaderboard_cache "
                    "ORDER BY category, rank"
                )
                rows = cursor.fetchall()
        except sqlite3.OperationalError:
            logger.debug("Arena cache table not found")
            return None

        if not rows:
            return None

        # Check TTL using fetched_at from first row
        fetched_at = rows[0][7]
        if self._is_expired(fetched_at):
            logger.debug("Arena cache expired (fetched_at=%s)", fetched_at)
            return None

        # Group by category
        buckets: dict[str, list[ArenaModelEntry]] = {
            "text": [], "code": [], "vision": [], "search": [],
        }
        for row in rows:
            cat = row[0]
            if cat not in buckets:
                continue
            buckets[cat].append(
                ArenaModelEntry(
                    category=cat,
                    name=row[1],
                    organization=row[2],
                    arena_score=row[3],
                    votes=row[4],
                    rank=row[5],
                    total_in_category=row[6],
                )
            )

        return ArenaLeaderboard(
            text=tuple(buckets["text"]),
            code=tuple(buckets["code"]),
            vision=tuple(buckets["vision"]),
            search=tuple(buckets["search"]),
            fetched_at=fetched_at,
        )

    def _is_expired(self, fetched_at: str) -> bool:
        """Check if a fetched_at ISO timestamp is older than TTL."""
        try:
            ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age > self._ttl
        except (ValueError, TypeError):
            return True
