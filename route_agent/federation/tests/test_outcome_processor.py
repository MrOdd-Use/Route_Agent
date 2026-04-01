"""Tests for OutcomeProcessor: stat accumulation, threshold, pool version bump."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from route_agent.federation.server.outcome_processor import OutcomeProcessor
from route_agent.federation.server.pool_version import PoolVersionManager
from route_agent.federation.server.storage import FederationStorage


@pytest.fixture()
def storage(tmp_path: Path) -> FederationStorage:
    """Isolated FederationStorage for outcome-processor tests."""
    return FederationStorage(db_path=tmp_path / "test_op.db")


@pytest.fixture()
def pool_mgr(storage: FederationStorage) -> PoolVersionManager:
    """PoolVersionManager wired to the isolated storage."""
    return PoolVersionManager(storage)


def _make_processor(
    storage: FederationStorage,
    pool_mgr: PoolVersionManager,
    threshold: int = 3,
) -> OutcomeProcessor:
    """Create an OutcomeProcessor with the given significance threshold."""
    return OutcomeProcessor(storage, pool_mgr, significance_threshold=threshold)


# ---------------------------------------------------------------------------
# Stat accumulation
# ---------------------------------------------------------------------------


def test_process_increments_success_count(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """process_async increments success_count and total_count on exec_success."""
    proc = _make_processor(storage, pool_mgr, threshold=100)
    asyncio.run(proc.process_async("m1", "general", "exec_success"))
    asyncio.run(proc.process_async("m1", "general", "exec_success"))

    stats = storage.get_outcome_stats_for_class_sync("general")
    row = next(r for r in stats if r["model_id"] == "m1")
    assert row["success_count"] == 2
    assert row["total_count"] == 2


def test_process_increments_fail_count(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """process_async increments fail_count on exec_fail."""
    proc = _make_processor(storage, pool_mgr, threshold=100)
    asyncio.run(proc.process_async("m1", "general", "exec_fail"))

    stats = storage.get_outcome_stats_for_class_sync("general")
    row = next(r for r in stats if r["model_id"] == "m1")
    assert row["fail_count"] == 1


def test_process_increments_quality_good(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """process_async increments quality_good on quality_good outcome."""
    proc = _make_processor(storage, pool_mgr, threshold=100)
    asyncio.run(proc.process_async("m1", "summarization", "quality_good"))

    stats = storage.get_outcome_stats_for_class_sync("summarization")
    assert stats[0]["quality_good"] == 1


# ---------------------------------------------------------------------------
# Significance threshold + version bump
# ---------------------------------------------------------------------------


def test_pool_version_not_bumped_below_threshold(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """Pool version is not bumped when sample count is below the significance threshold."""
    proc = _make_processor(storage, pool_mgr, threshold=5)
    asyncio.run(proc.process_async("m1", "general", "exec_success"))
    asyncio.run(proc.process_async("m1", "general", "exec_success"))

    entry = asyncio.run(pool_mgr.get_async("general"))
    assert entry is None  # no bump yet


def test_pool_version_bumped_at_threshold(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """Pool version is bumped when sample count hits the threshold."""
    proc = _make_processor(storage, pool_mgr, threshold=3)

    for _ in range(3):
        asyncio.run(proc.process_async("m1", "general", "exec_success"))

    entry = asyncio.run(pool_mgr.get_async("general"))
    assert entry is not None
    assert entry.version >= 1


def test_pool_version_bump_stores_empty_model_ids(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """Version bump does NOT store a ranked model list — ordering is done client-side."""
    proc = _make_processor(storage, pool_mgr, threshold=2)
    asyncio.run(proc.process_async("m1", "general", "exec_success"))
    asyncio.run(proc.process_async("m1", "general", "exec_success"))

    entry = asyncio.run(pool_mgr.get_async("general"))
    assert entry is not None
    assert entry.model_ids == ()  # no server-side ranking


def test_second_threshold_crossing_bumps_again(
    storage: FederationStorage, pool_mgr: PoolVersionManager
) -> None:
    """A second threshold crossing after the first bump increments the version again."""
    proc = _make_processor(storage, pool_mgr, threshold=3)
    for _ in range(3):
        asyncio.run(proc.process_async("m1", "general", "exec_success"))
    first_version = asyncio.run(pool_mgr.get_async("general"))
    assert first_version is not None

    for _ in range(3):
        asyncio.run(proc.process_async("m1", "general", "exec_success"))
    second_version = asyncio.run(pool_mgr.get_async("general"))
    assert second_version is not None
    assert second_version.version > first_version.version
