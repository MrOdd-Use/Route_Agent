"""Tests for manual class-pool add/remove (渠道2: 手动添加)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from route_agent.router_engine.class_pool import ClassPoolManager
from route_agent.router_engine.constants import POOL_MAX_SIZE
from route_agent.router_engine.schemas import ClassPoolEntry


class _FakeStorage:
    """In-memory storage stub for manual pool tests."""

    def __init__(self) -> None:
        """Initialize in-memory state."""
        self._pool: dict[str, dict[str, ClassPoolEntry]] = {}
        self._stats: set[tuple[str, str]] = set()
        self._defaults: dict[str, str] = {}

    async def get_pool_entries_async(self, agent_class: str) -> list[ClassPoolEntry]:
        """Return pool entries for a class."""
        return list(self._pool.get(agent_class, {}).values())

    async def upsert_pool_entry_async(
        self, agent_class: str, model_id: str, model_release_date: str | None = None
    ) -> None:
        """Insert or update a pool entry."""
        if agent_class not in self._pool:
            self._pool[agent_class] = {}
        self._pool[agent_class][model_id] = ClassPoolEntry(
            agent_class=agent_class,
            model_id=model_id,
            model_release_date=model_release_date,
        )

    async def ensure_stats_row_async(self, agent_class: str, model_id: str) -> None:
        """Record that a stats row was initialized."""
        self._stats.add((agent_class, model_id))

    async def delete_pool_entry_async(self, agent_class: str, model_id: str) -> None:
        """Delete a pool entry."""
        pool = self._pool.get(agent_class, {})
        pool.pop(model_id, None)

    async def list_default_model_ids_async(self, agent_class: str) -> set[str]:
        """Return default model ids for a class."""
        model_id = self._defaults.get(agent_class)
        return {model_id} if model_id else set()

    async def clear_default_async(self, agent_class: str, domain: str) -> None:
        """Clear the default for a class."""
        self._defaults.pop(agent_class, None)


def _resolver(model_id: str):
    """Fake model metadata resolver — returns metadata for known models."""
    known = {
        "openai:gpt-4o": SimpleNamespace(routing={"release_date": "2025-01-15"}),
        "deepseek:deepseek-chat": SimpleNamespace(routing={"release_date": "2025-03-01"}),
        "google:gemini-2.0-flash": SimpleNamespace(routing={"release_date": "2025-02-10"}),
    }
    return known.get(model_id)


def _make_manager(storage: _FakeStorage | None = None) -> tuple[ClassPoolManager, _FakeStorage]:
    """Build a ClassPoolManager with fake storage and resolver."""
    storage = storage or _FakeStorage()
    mgr = ClassPoolManager(storage, model_metadata_resolver=_resolver)
    return mgr, storage


def test_manual_add_to_empty_pool() -> None:
    """Add a model to an empty pool succeeds."""
    mgr, storage = _make_manager()
    result = asyncio.run(mgr.manual_add_to_pool("extraction", "openai:gpt-4o"))
    assert result["status"] == "added"
    assert result["agent_class"] == "extraction"
    assert result["model_id"] == "openai:gpt-4o"
    # Verify entry persisted
    entries = asyncio.run(storage.get_pool_entries_async("extraction"))
    assert len(entries) == 1
    assert entries[0].model_id == "openai:gpt-4o"
    # Verify stats row initialized
    assert ("extraction", "openai:gpt-4o") in storage._stats


def test_manual_add_idempotent() -> None:
    """Adding same model twice returns already_exists."""
    mgr, _ = _make_manager()
    asyncio.run(mgr.manual_add_to_pool("extraction", "openai:gpt-4o"))
    result = asyncio.run(mgr.manual_add_to_pool("extraction", "openai:gpt-4o"))
    assert result["status"] == "already_exists"


def test_manual_add_unknown_model() -> None:
    """Adding a model not in registry returns error."""
    mgr, _ = _make_manager()
    result = asyncio.run(mgr.manual_add_to_pool("extraction", "unknown:model-x"))
    assert result["status"] == "error"
    assert result["reason"] == "model_not_found"


def test_manual_add_pool_full() -> None:
    """Adding to a full pool returns error without auto-eviction."""
    storage = _FakeStorage()
    # Pre-fill pool to POOL_MAX_SIZE
    for i in range(POOL_MAX_SIZE):
        fake_id = f"fake:model-{i}"
        asyncio.run(storage.upsert_pool_entry_async("extraction", fake_id))

    mgr, _ = _make_manager(storage)
    result = asyncio.run(mgr.manual_add_to_pool("extraction", "openai:gpt-4o"))
    assert result["status"] == "error"
    assert result["reason"] == "pool_full"


def test_manual_add_normalizes_class() -> None:
    """Agent class is normalized to lowercase underscore form."""
    mgr, storage = _make_manager()
    result = asyncio.run(mgr.manual_add_to_pool("Agent Coding", "openai:gpt-4o"))
    assert result["status"] == "added"
    assert result["agent_class"] == "agent_coding"


def test_manual_remove_success() -> None:
    """Remove a model that exists in pool."""
    mgr, _ = _make_manager()
    asyncio.run(mgr.manual_add_to_pool("extraction", "openai:gpt-4o"))
    result = asyncio.run(mgr.manual_remove_from_pool("extraction", "openai:gpt-4o"))
    assert result["status"] == "removed"


def test_manual_remove_not_found() -> None:
    """Remove a model not in pool returns not_found."""
    mgr, _ = _make_manager()
    result = asyncio.run(mgr.manual_remove_from_pool("extraction", "openai:gpt-4o"))
    assert result["status"] == "not_found"


def test_manual_remove_clears_default() -> None:
    """Remove a default model also clears the default."""
    storage = _FakeStorage()
    storage._defaults["extraction"] = "openai:gpt-4o"
    mgr, _ = _make_manager(storage)
    asyncio.run(mgr.manual_add_to_pool("extraction", "openai:gpt-4o"))
    result = asyncio.run(mgr.manual_remove_from_pool("extraction", "openai:gpt-4o"))
    assert result["status"] == "removed"
    assert "extraction" not in storage._defaults


def test_manual_add_extracts_release_date() -> None:
    """Manual add extracts release_date from model metadata."""
    mgr, storage = _make_manager()
    asyncio.run(mgr.manual_add_to_pool("general", "openai:gpt-4o"))
    entries = asyncio.run(storage.get_pool_entries_async("general"))
    assert entries[0].model_release_date == "2025-01-15"
