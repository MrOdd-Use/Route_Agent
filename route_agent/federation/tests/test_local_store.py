"""Tests for LocalStore."""

import asyncio
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from route_agent.federation.client.local_store import (
    AgentMappingEntry,
    LocalPoolSnapshot,
    LocalStore,
)


@pytest.fixture
def temp_db() -> Path:
    """Create temporary database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def store(temp_db: Path) -> LocalStore:
    """Create LocalStore instance."""
    return LocalStore(temp_db)


@pytest.mark.asyncio
async def test_agent_mapping_not_found(store: LocalStore) -> None:
    """Test agent mapping lookup returns None when not found."""
    result = await store.get_agent_mapping("app1", "agent1")
    assert result is None


@pytest.mark.asyncio
async def test_save_and_get_agent_mapping(store: LocalStore) -> None:
    """Test saving and retrieving agent mapping."""
    await store.save_agent_mapping(
        app_id="app1",
        agent_name="agent1",
        agent_class="general",
        agent_version="v1",
        source="declared",
    )

    result = await store.get_agent_mapping("app1", "agent1")
    assert result is not None
    assert result.app_id == "app1"
    assert result.agent_name == "agent1"
    assert result.agent_class == "general"
    assert result.agent_version == "v1"
    assert result.source == "declared"


@pytest.mark.asyncio
async def test_update_agent_mapping(store: LocalStore) -> None:
    """Test updating existing agent mapping."""
    await store.save_agent_mapping(
        app_id="app1",
        agent_name="agent1",
        agent_class="general",
        source="declared",
    )

    await store.save_agent_mapping(
        app_id="app1",
        agent_name="agent1",
        agent_class="summarization",
        source="learned",
    )

    result = await store.get_agent_mapping("app1", "agent1")
    assert result is not None
    assert result.agent_class == "summarization"
    assert result.source == "learned"


@pytest.mark.asyncio
async def test_pool_snapshot_not_found(store: LocalStore) -> None:
    """Test pool snapshot lookup returns None when not found."""
    result = await store.get_pool_snapshot("general")
    assert result is None


@pytest.mark.asyncio
async def test_save_and_get_pool_snapshot(store: LocalStore) -> None:
    """Test saving and retrieving pool snapshot."""
    await store.save_pool_snapshot(
        agent_class="general",
        ordered_model_ids=["gpt-4", "claude-3-opus", "gemini-pro"],
        default_model_id="gpt-4",
        pool_version=42,
    )

    result = await store.get_pool_snapshot("general")
    assert result is not None
    assert result.agent_class == "general"
    assert result.ordered_model_ids == ("gpt-4", "claude-3-opus", "gemini-pro")
    assert result.default_model_id == "gpt-4"
    assert result.pool_version == 42


@pytest.mark.asyncio
async def test_update_pool_snapshot(store: LocalStore) -> None:
    """Test updating existing pool snapshot."""
    await store.save_pool_snapshot(
        agent_class="general",
        ordered_model_ids=["gpt-4"],
        default_model_id="gpt-4",
        pool_version=1,
    )

    await store.save_pool_snapshot(
        agent_class="general",
        ordered_model_ids=["claude-3-opus", "gpt-4"],
        default_model_id="claude-3-opus",
        pool_version=2,
    )

    result = await store.get_pool_snapshot("general")
    assert result is not None
    assert result.ordered_model_ids == ("claude-3-opus", "gpt-4")
    assert result.default_model_id == "claude-3-opus"
    assert result.pool_version == 2


@pytest.mark.asyncio
async def test_multiple_apps_isolated(store: LocalStore) -> None:
    """Test agent mappings are isolated by app_id."""
    await store.save_agent_mapping(
        app_id="app1",
        agent_name="agent1",
        agent_class="general",
    )

    await store.save_agent_mapping(
        app_id="app2",
        agent_name="agent1",
        agent_class="summarization",
    )

    result1 = await store.get_agent_mapping("app1", "agent1")
    result2 = await store.get_agent_mapping("app2", "agent1")

    assert result1 is not None
    assert result1.agent_class == "general"
    assert result2 is not None
    assert result2.agent_class == "summarization"


@pytest.mark.asyncio
async def test_list_agent_mappings_returns_app_rows(store: LocalStore) -> None:
    """Test list_agent_mappings returns all rows for one app only."""
    await store.save_agent_mapping(
        app_id="app1",
        agent_name="agent1",
        agent_class="general",
    )
    await store.save_agent_mapping(
        app_id="app1",
        agent_name="agent2",
        agent_class="summarization",
    )
    await store.save_agent_mapping(
        app_id="app2",
        agent_name="agent1",
        agent_class="review",
    )

    mappings = await store.list_agent_mappings("app1")

    assert [mapping.agent_name for mapping in mappings] == ["agent1", "agent2"]
    assert {mapping.agent_class for mapping in mappings} == {"general", "summarization"}


@pytest.mark.asyncio
async def test_list_pool_snapshots_returns_all_snapshots(store: LocalStore) -> None:
    """Test list_pool_snapshots returns persisted snapshots for every class."""
    await store.save_pool_snapshot(
        agent_class="general",
        ordered_model_ids=["gpt-4", "claude-3-opus"],
        default_model_id="gpt-4",
        pool_version=2,
    )
    await store.save_pool_snapshot(
        agent_class="summarization",
        ordered_model_ids=["gemini-pro"],
        default_model_id="gemini-pro",
        pool_version=5,
    )

    snapshots = await store.list_pool_snapshots()

    assert [snapshot.agent_class for snapshot in snapshots] == ["general", "summarization"]
    assert snapshots[0].ordered_model_ids == ("gpt-4", "claude-3-opus")
    assert snapshots[1].pool_version == 5


def test_local_store_rejects_non_sqlite_path(tmp_path: Path) -> None:
    """Only approved SQLite file suffixes should be accepted."""
    with pytest.raises(ValueError, match="approved suffixes"):
        LocalStore(tmp_path / "store.txt")


# ---------------------------------------------------------------------------
# Federation scores
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_and_get_federation_scores(store: LocalStore) -> None:
    """save_federation_scores persists and get_federation_scores retrieves entries."""
    scores = [
        {"model_id": "model-a", "success_count": 80, "fail_count": 10, "total_count": 90, "success_rate": 0.889},
        {"model_id": "model-b", "success_count": 50, "fail_count": 5,  "total_count": 55, "success_rate": 0.909},
    ]
    await store.save_federation_scores("general", scores)

    entries = await store.get_federation_scores("general")
    assert len(entries) == 2
    # returned in success_rate DESC order
    assert entries[0].model_id == "model-b"
    assert entries[0].success_rate == pytest.approx(0.909)
    assert entries[1].model_id == "model-a"


@pytest.mark.asyncio
async def test_get_federation_scores_empty(store: LocalStore) -> None:
    """get_federation_scores returns empty list for unknown class."""
    entries = await store.get_federation_scores("nonexistent")
    assert entries == []


@pytest.mark.asyncio
async def test_save_federation_scores_replaces_previous(store: LocalStore) -> None:
    """A second save_federation_scores call replaces all previous scores for that class."""
    await store.save_federation_scores("general", [
        {"model_id": "old-model", "success_count": 10, "fail_count": 0, "total_count": 10, "success_rate": 1.0},
    ])
    await store.save_federation_scores("general", [
        {"model_id": "new-model", "success_count": 5, "fail_count": 1, "total_count": 6, "success_rate": 0.833},
    ])

    entries = await store.get_federation_scores("general")
    assert len(entries) == 1
    assert entries[0].model_id == "new-model"


@pytest.mark.asyncio
async def test_federation_scores_isolated_by_class(store: LocalStore) -> None:
    """Federation scores for different classes do not interfere."""
    await store.save_federation_scores("general", [
        {"model_id": "m1", "success_count": 10, "fail_count": 0, "total_count": 10, "success_rate": 1.0},
    ])
    await store.save_federation_scores("summarization", [
        {"model_id": "m2", "success_count": 5, "fail_count": 2, "total_count": 7, "success_rate": 0.714},
    ])

    general = await store.get_federation_scores("general")
    summarization = await store.get_federation_scores("summarization")
    assert len(general) == 1 and general[0].model_id == "m1"
    assert len(summarization) == 1 and summarization[0].model_id == "m2"
