"""Tests for Arena SQLite cache storage."""

from datetime import datetime, timedelta, timezone

import pytest

from route_agent.model_registry.arena.schemas import ArenaLeaderboard, ArenaModelEntry
from route_agent.model_registry.arena.storage import ArenaCacheStorage


def _make_leaderboard(fetched_at: str | None = None) -> ArenaLeaderboard:
    timestamp = fetched_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return ArenaLeaderboard(
        text=(
            ArenaModelEntry(
                name="claude-opus-4-6", organization="anthropic",
                category="text", arena_score=1506, votes=4745,
                rank=1, total_in_category=2,
            ),
            ArenaModelEntry(
                name="gemini-3-pro", organization="google",
                category="text", arena_score=1486, votes=36354,
                rank=2, total_in_category=2,
            ),
        ),
        code=(
            ArenaModelEntry(
                name="claude-opus-4-6", organization="anthropic",
                category="code", arena_score=1561, votes=2364,
                rank=1, total_in_category=1,
            ),
        ),
        fetched_at=timestamp,
    )


@pytest.fixture
def storage(tmp_path):
    db_path = str(tmp_path / "test_arena_cache.sqlite3")
    return ArenaCacheStorage(db_path=db_path, ttl_seconds=3600)


class TestArenaCacheStorage:
    def test_save_and_load(self, storage):
        lb = _make_leaderboard()
        storage.save(lb)
        loaded = storage.load()

        assert loaded is not None
        assert len(loaded.text) == 2
        assert len(loaded.code) == 1
        assert loaded.text[0].name == "claude-opus-4-6"
        assert loaded.text[0].arena_score == 1506
        assert loaded.code[0].arena_score == 1561

    def test_load_empty_db(self, storage):
        assert storage.load() is None

    def test_save_replaces_previous(self, storage):
        lb1 = _make_leaderboard()
        storage.save(lb1)

        lb2 = ArenaLeaderboard(
            text=(
                ArenaModelEntry(
                    name="new-model", organization="test",
                    category="text", arena_score=1600, votes=100,
                    rank=1, total_in_category=1,
                ),
            ),
            fetched_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        storage.save(lb2)

        loaded = storage.load()
        assert loaded is not None
        assert len(loaded.text) == 1
        assert loaded.text[0].name == "new-model"

    def test_skip_save_empty_leaderboard(self, storage):
        storage.save(ArenaLeaderboard())
        assert storage.load() is None

    def test_expired_cache_returns_none(self, tmp_path):
        db_path = str(tmp_path / "expired.sqlite3")
        storage = ArenaCacheStorage(db_path=db_path, ttl_seconds=3600)
        old_timestamp = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat().replace("+00:00", "Z")
        storage.save(_make_leaderboard(fetched_at=old_timestamp))
        assert storage.load() is None
