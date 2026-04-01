"""Tests for FederationStorage SQLite persistence."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from route_agent.federation.server.storage import FederationStorage


@pytest.fixture()
def storage(tmp_path: Path) -> FederationStorage:
    """Isolated FederationStorage backed by a temp SQLite file."""
    return FederationStorage(db_path=tmp_path / "test_federation.db")


# ---------------------------------------------------------------------------
# App registration
# ---------------------------------------------------------------------------


def test_upsert_app_creates_record(storage: FederationStorage) -> None:
    """upsert_app_sync persists a new app record."""
    reg = storage.upsert_app_sync("are", "Auto Research Engine")

    assert reg.app_id == "are"
    assert reg.app_name == "Auto Research Engine"
    assert reg.registered_at is not None
    assert reg.last_seen_at is not None


def test_upsert_app_preserves_registered_at_on_update(storage: FederationStorage) -> None:
    """Re-upserting an app preserves its original registered_at timestamp."""
    first = storage.upsert_app_sync("are", "Auto Research Engine")
    second = storage.upsert_app_sync("are", "Auto Research Engine v2")

    assert second.registered_at == first.registered_at
    assert second.app_name == "Auto Research Engine v2"


def test_get_app_returns_none_for_unknown(storage: FederationStorage) -> None:
    """get_app_sync returns None for an unregistered app_id."""
    assert storage.get_app_sync("nonexistent") is None


def test_get_app_returns_registered(storage: FederationStorage) -> None:
    """get_app_sync returns the record after upsert."""
    storage.upsert_app_sync("app1", "App One")
    reg = storage.get_app_sync("app1")

    assert reg is not None
    assert reg.app_id == "app1"


def test_upsert_app_async(storage: FederationStorage) -> None:
    """upsert_app_async works correctly via asyncio.run."""
    reg = asyncio.run(storage.upsert_app_async("async_app", "Async App"))
    assert reg.app_id == "async_app"


# ---------------------------------------------------------------------------
# Agent registration
# ---------------------------------------------------------------------------


def test_upsert_agent_creates_record(storage: FederationStorage) -> None:
    """upsert_agent_sync persists a new agent mapping."""
    storage.upsert_app_sync("are", "ARE")
    agent = storage.upsert_agent_sync("are", "report_summarizer", "summarization")

    assert agent.app_id == "are"
    assert agent.agent_name == "report_summarizer"
    assert agent.agent_class == "summarization"
    assert agent.agent_version == "v1"


def test_upsert_agent_updates_class(storage: FederationStorage) -> None:
    """Re-upserting an agent updates its class and version, preserving registered_at."""
    storage.upsert_app_sync("are", "ARE")
    first = storage.upsert_agent_sync("are", "planner", "general")
    second = storage.upsert_agent_sync("are", "planner", "review", "v2")

    assert second.agent_class == "review"
    assert second.agent_version == "v2"
    assert second.registered_at == first.registered_at


def test_get_agent_returns_none_for_unknown(storage: FederationStorage) -> None:
    """get_agent_sync returns None for an unregistered agent name."""
    assert storage.get_agent_sync("are", "unknown_agent") is None


def test_list_agents_returns_all_for_app(storage: FederationStorage) -> None:
    """list_agents_sync returns only agents scoped to the given app_id."""
    storage.upsert_app_sync("are", "ARE")
    storage.upsert_agent_sync("are", "planner", "general")
    storage.upsert_agent_sync("are", "scraper", "scrape")
    storage.upsert_agent_sync("other_app", "unrelated", "general")

    agents = storage.list_agents_sync("are")
    names = {a.agent_name for a in agents}

    assert names == {"planner", "scraper"}


def test_list_agents_empty_for_new_app(storage: FederationStorage) -> None:
    """list_agents_sync returns an empty list for an unknown app."""
    assert storage.list_agents_sync("nonexistent") == []


def test_upsert_agent_async(storage: FederationStorage) -> None:
    """upsert_agent_async works correctly via asyncio.run."""
    asyncio.run(storage.upsert_app_async("app1", "App"))
    agent = asyncio.run(storage.upsert_agent_async("app1", "extractor", "extraction"))
    assert agent.agent_class == "extraction"


# ---------------------------------------------------------------------------
# Pool versions
# ---------------------------------------------------------------------------


def test_upsert_pool_version_starts_at_one(storage: FederationStorage) -> None:
    """First pool version upsert creates version 1."""
    entry = storage.upsert_pool_version_sync("general", ("m1", "m2", "m3"))

    assert entry.version == 1
    assert entry.agent_class == "general"
    assert entry.model_ids == ("m1", "m2", "m3")


def test_upsert_pool_version_increments_on_update(storage: FederationStorage) -> None:
    """Subsequent pool version upserts increment the version counter."""
    storage.upsert_pool_version_sync("general", ("m1",))
    second = storage.upsert_pool_version_sync("general", ("m1", "m2"))

    assert second.version == 2
    assert second.model_ids == ("m1", "m2")


def test_get_pool_version_returns_none_for_unknown(storage: FederationStorage) -> None:
    """get_pool_version_sync returns None for a class with no record."""
    assert storage.get_pool_version_sync("nonexistent_class") is None


def test_get_pool_versions_returns_map(storage: FederationStorage) -> None:
    """get_pool_versions_sync returns a class→version dict, omitting unknown classes."""
    storage.upsert_pool_version_sync("general", ("m1",))
    storage.upsert_pool_version_sync("scrape", ("m2",))
    storage.upsert_pool_version_sync("scrape", ("m2", "m3"))

    versions = storage.get_pool_versions_sync(["general", "scrape", "missing"])

    assert versions["general"] == 1
    assert versions["scrape"] == 2
    assert "missing" not in versions


def test_get_pool_versions_empty_input(storage: FederationStorage) -> None:
    """get_pool_versions_sync returns an empty dict for an empty input list."""
    assert storage.get_pool_versions_sync([]) == {}


def test_pool_version_async(storage: FederationStorage) -> None:
    """Async pool version upsert and lookup work correctly."""
    asyncio.run(storage.upsert_pool_version_async("translation", ("m1",)))
    entry = asyncio.run(storage.get_pool_version_async("translation"))

    assert entry is not None
    assert entry.version == 1
    assert entry.model_ids == ("m1",)


def test_increment_outcome_stat_rejects_unknown_type(storage: FederationStorage) -> None:
    """Unknown outcome types should be rejected instead of falling through silently."""
    with pytest.raises(ValueError, match="unsupported outcome_type"):
        storage.increment_outcome_stat_sync("general", "m1", "mystery")


def test_storage_rejects_non_sqlite_path(tmp_path: Path) -> None:
    """FederationStorage should reject non-SQLite file suffixes."""
    with pytest.raises(ValueError, match="approved suffixes"):
        FederationStorage(tmp_path / "federation.txt")
