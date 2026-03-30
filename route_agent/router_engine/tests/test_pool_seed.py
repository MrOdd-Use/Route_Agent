"""Tests for class-pool seed initialization and candidate composition."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.class_pool import ClassPoolManager, _profile_to_dimensions
from route_agent.router_engine.constants import GENERAL_FALLBACK_DIMS, POOL_SEED_SIZE
from route_agent.router_engine.schemas import ClassPoolEntry


class _FakeStorage:
    """In-memory storage stub for seed tests."""

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


# -- Fixtures ------------------------------------------------------------------

_MODELS = [
    ModelMetadata(
        model_id="openai:gpt-4o",
        display_name="GPT-4o",
        provider="openai",
        api_model_name="gpt-4o",
        capabilities={"reasoning": 90, "text": 85, "code": 80, "instruction_following": 88},
        pricing={"input": 2.5, "output": 10.0},
        routing={"release_date": "2025-01-15"},
    ),
    ModelMetadata(
        model_id="anthropic:claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        provider="anthropic",
        api_model_name="claude-sonnet-4-6",
        capabilities={"reasoning": 92, "text": 90, "code": 88, "instruction_following": 91},
        pricing={"input": 3.0, "output": 15.0},
        routing={"release_date": "2025-05-01"},
    ),
    ModelMetadata(
        model_id="deepseek:deepseek-chat",
        display_name="DeepSeek Chat",
        provider="deepseek",
        api_model_name="deepseek-chat",
        capabilities={"reasoning": 75, "text": 70, "code": 85, "instruction_following": 72},
        pricing={"input": 0.27, "output": 1.1},
        routing={"release_date": "2025-03-01"},
    ),
    ModelMetadata(
        model_id="google:gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        provider="google",
        api_model_name="gemini-2.0-flash",
        capabilities={"reasoning": 65, "text": 60, "code": 55, "instruction_following": 62},
        pricing={"input": 0.1, "output": 0.4},
        routing={"release_date": "2025-02-10"},
    ),
    ModelMetadata(
        model_id="groq:llama-3.3-70b",
        display_name="Llama 3.3 70B",
        provider="groq",
        api_model_name="llama-3.3-70b-versatile",
        capabilities={"reasoning": 60, "text": 55, "code": 50, "instruction_following": 58},
        pricing={"input": 0.59, "output": 0.79},
        routing={"release_date": "2025-01-20"},
    ),
]


def _resolver(model_id: str):
    """Fake model metadata resolver."""
    for m in _MODELS:
        if m.model_id == model_id:
            return SimpleNamespace(routing=m.routing)
    return None


def _make_manager(storage: _FakeStorage | None = None) -> tuple[ClassPoolManager, _FakeStorage]:
    """Build a ClassPoolManager with fake storage and resolver."""
    storage = storage or _FakeStorage()
    mgr = ClassPoolManager(storage, model_metadata_resolver=_resolver)
    return mgr, storage


# -- Tests: _profile_to_dimensions --------------------------------------------

def test_profile_to_dimensions_normal() -> None:
    """Non-empty profile converts to DimensionScore tuple."""
    dims = _profile_to_dimensions({"reasoning": 8, "text": 6})
    assert len(dims) == 2
    names = {d.dimension for d in dims}
    assert names == {"reasoning", "text"}
    assert all(d.reasoning == "seed" for d in dims)


def test_profile_to_dimensions_empty_fallback() -> None:
    """Empty profile falls back to GENERAL_FALLBACK_DIMS."""
    dims = _profile_to_dimensions({})
    names = {d.dimension for d in dims}
    assert names == set(GENERAL_FALLBACK_DIMS.keys())


# -- Tests: seed_pool_async ---------------------------------------------------

def test_seed_pool_empty_class() -> None:
    """Empty pool gets seeded with POOL_SEED_SIZE models."""
    mgr, storage = _make_manager()
    seeded = asyncio.run(mgr.seed_pool_async("extraction", _MODELS))
    assert len(seeded) == POOL_SEED_SIZE
    entries = asyncio.run(storage.get_pool_entries_async("extraction"))
    assert len(entries) == POOL_SEED_SIZE


def test_seed_pool_nonempty_skip() -> None:
    """Pool already at POOL_SEED_SIZE is not re-seeded."""
    storage = _FakeStorage()
    for m in _MODELS[:POOL_SEED_SIZE]:
        asyncio.run(storage.upsert_pool_entry_async("extraction", m.model_id))
    mgr, _ = _make_manager(storage)
    seeded = asyncio.run(mgr.seed_pool_async("extraction", _MODELS))
    assert seeded == []


def test_seed_pool_backfill() -> None:
    """Pool with fewer than POOL_SEED_SIZE entries gets backfilled."""
    storage = _FakeStorage()
    asyncio.run(storage.upsert_pool_entry_async("extraction", "openai:gpt-4o"))
    mgr, _ = _make_manager(storage)
    seeded = asyncio.run(mgr.seed_pool_async("extraction", _MODELS))
    assert len(seeded) == POOL_SEED_SIZE - 1
    entries = asyncio.run(storage.get_pool_entries_async("extraction"))
    assert len(entries) == POOL_SEED_SIZE


def test_seed_composite_ranking() -> None:
    """Seeded models should be the highest composite-scored ones."""
    mgr, storage = _make_manager()
    seeded = asyncio.run(mgr.seed_pool_async("coding", _MODELS))
    # anthropic and openai have highest capability scores for coding
    # deepseek has high code score but lower overall
    assert "anthropic:claude-sonnet-4-6" in seeded
    assert "openai:gpt-4o" in seeded
    # The weakest models should NOT be seeded
    assert "groq:llama-3.3-70b" not in seeded


def test_seed_general_class_fallback() -> None:
    """General class (empty profile) uses GENERAL_FALLBACK_DIMS."""
    mgr, storage = _make_manager()
    seeded = asyncio.run(mgr.seed_pool_async("general", _MODELS))
    assert len(seeded) == POOL_SEED_SIZE
    # With reasoning/text/instruction_following focus, anthropic and openai should rank high
    assert "anthropic:claude-sonnet-4-6" in seeded
    assert "openai:gpt-4o" in seeded


def test_seed_excludes_existing_pool_members() -> None:
    """Backfill does not re-add models already in pool."""
    storage = _FakeStorage()
    asyncio.run(storage.upsert_pool_entry_async("extraction", "anthropic:claude-sonnet-4-6"))
    mgr, _ = _make_manager(storage)
    seeded = asyncio.run(mgr.seed_pool_async("extraction", _MODELS))
    assert "anthropic:claude-sonnet-4-6" not in seeded
    entries = asyncio.run(storage.get_pool_entries_async("extraction"))
    ids = {e.model_id for e in entries}
    assert "anthropic:claude-sonnet-4-6" in ids
    assert len(entries) == POOL_SEED_SIZE
