"""Tests for PoolVersionManager: version bump, lookup, class_pool callback wiring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from route_agent.federation.server.pool_version import PoolVersionManager
from route_agent.federation.server.storage import FederationStorage


@pytest.fixture()
def storage(tmp_path: Path) -> FederationStorage:
    """Isolated FederationStorage for pool-version tests."""
    return FederationStorage(db_path=tmp_path / "test_pv.db")


@pytest.fixture()
def mgr(storage: FederationStorage) -> PoolVersionManager:
    """PoolVersionManager wired to the isolated storage."""
    return PoolVersionManager(storage)


# ---------------------------------------------------------------------------
# bump_sync / bump_async
# ---------------------------------------------------------------------------


def test_bump_sync_starts_at_version_one(mgr: PoolVersionManager) -> None:
    """First bump creates version 1 with the supplied model_ids."""
    entry = mgr.bump_sync("general", ("m1", "m2"))
    assert entry.version == 1
    assert entry.agent_class == "general"
    assert entry.model_ids == ("m1", "m2")


def test_bump_sync_increments_version(mgr: PoolVersionManager) -> None:
    """Subsequent bump_sync calls increment the version counter."""
    mgr.bump_sync("general", ("m1",))
    entry = mgr.bump_sync("general", ("m1", "m2"))
    assert entry.version == 2


def test_bump_async(mgr: PoolVersionManager) -> None:
    """bump_async creates version 1 for a new class."""
    entry = asyncio.run(mgr.bump_async("scrape", ("s1",)))
    assert entry.version == 1


# ---------------------------------------------------------------------------
# get_async
# ---------------------------------------------------------------------------


def test_get_async_returns_none_for_unknown(mgr: PoolVersionManager) -> None:
    """get_async returns None when no version exists for the class."""
    result = asyncio.run(mgr.get_async("nonexistent"))
    assert result is None


def test_get_async_returns_entry_after_bump(mgr: PoolVersionManager) -> None:
    """get_async returns the entry created by bump_sync."""
    mgr.bump_sync("translation", ("t1", "t2"))
    entry = asyncio.run(mgr.get_async("translation"))
    assert entry is not None
    assert entry.version == 1


# ---------------------------------------------------------------------------
# get_versions_sync / get_versions_async
# ---------------------------------------------------------------------------


def test_get_versions_sync_returns_map(mgr: PoolVersionManager) -> None:
    """get_versions_sync returns a class→version dict, omitting unknown classes."""
    mgr.bump_sync("general", ("m1",))
    mgr.bump_sync("general", ("m1", "m2"))
    mgr.bump_sync("scrape", ("s1",))

    versions = mgr.get_versions_sync(["general", "scrape", "missing"])
    assert versions["general"] == 2
    assert versions["scrape"] == 1
    assert "missing" not in versions


def test_get_versions_async(mgr: PoolVersionManager) -> None:
    """get_versions_async returns the current version for known classes."""
    mgr.bump_sync("summarization", ("m1",))
    versions = asyncio.run(mgr.get_versions_async(["summarization"]))
    assert versions["summarization"] == 1


def test_get_versions_empty_input(mgr: PoolVersionManager) -> None:
    """get_versions_sync returns an empty dict for an empty input list."""
    assert mgr.get_versions_sync([]) == {}


# ---------------------------------------------------------------------------
# pool_change_callback wiring via ClassPoolManager
# ---------------------------------------------------------------------------


def test_class_pool_change_triggers_callback() -> None:
    """ClassPoolManager.set_pool_change_callback should fire on manual_add."""
    from unittest.mock import AsyncMock, MagicMock

    from route_agent.router_engine.class_pool import ClassPoolManager

    fake_storage = MagicMock()
    fake_storage.get_pool_entries_async = AsyncMock(return_value=[])
    fake_storage.get_pool_entries = MagicMock(return_value=[])
    fake_storage.list_default_model_ids_async = AsyncMock(return_value=set())
    fake_storage.upsert_pool_entry_async = AsyncMock()
    fake_storage.ensure_stats_row_async = AsyncMock()

    # resolver returns a dummy metadata object so the model is "found"
    dummy_meta = MagicMock()
    dummy_meta.routing = {}
    dummy_meta.release_date = None
    dummy_meta.model_release_date = None
    resolver = MagicMock(return_value=dummy_meta)

    received: list[tuple[str, list[str]]] = []

    async def callback(agent_class: str, model_ids: list[str]) -> None:
        """Record the callback invocation."""
        received.append((agent_class, model_ids))

    mgr = ClassPoolManager(fake_storage, model_metadata_resolver=resolver)
    mgr.set_pool_change_callback(callback)

    asyncio.run(mgr.manual_add_to_pool("general", "test-model"))

    assert len(received) == 1
    assert received[0][0] == "general"
