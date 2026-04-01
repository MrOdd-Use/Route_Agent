"""Tests for LeaseStore: acquire, release, TTL expiry, active counts."""

from __future__ import annotations

import time

import pytest

from route_agent.federation.server.lease_store import LeaseStore


@pytest.fixture()
def store() -> LeaseStore:
    """Fresh in-memory LeaseStore for each test."""
    return LeaseStore()


def _acquire(store: LeaseStore, model_id: str = "m1", ttl: int = 300) -> str:
    """Helper: acquire a lease and return its ID."""
    lease = store.acquire(
        app_id="app1", agent_name="agent", agent_class="general",
        proposed_model_id=model_id, granted_model_id=model_id, mode="local",
        ttl_seconds=ttl,
    )
    return lease.lease_id


# ---------------------------------------------------------------------------
# acquire / release / get
# ---------------------------------------------------------------------------


def test_acquire_returns_lease(store: LeaseStore) -> None:
    """acquire() returns a ConcurrencyLease with the expected fields."""
    lease = store.acquire(
        app_id="are", agent_name="summarizer", agent_class="summarization",
        proposed_model_id="deepseek-chat", granted_model_id="deepseek-chat",
        mode="local", ttl_seconds=300,
    )
    assert lease.lease_id
    assert lease.granted_model_id == "deepseek-chat"
    assert lease.mode == "local"
    assert lease.ttl_seconds == 300


def test_get_returns_lease_while_active(store: LeaseStore) -> None:
    """get() returns the lease while it is still active."""
    lid = _acquire(store)
    result = store.get(lid)
    assert result is not None
    assert result.lease_id == lid


def test_get_returns_none_for_unknown(store: LeaseStore) -> None:
    """get() returns None for an unrecognised lease_id."""
    assert store.get("nonexistent-id") is None


def test_release_removes_lease(store: LeaseStore) -> None:
    """release() removes the lease and returns True."""
    lid = _acquire(store)
    assert store.release(lid) is True
    assert store.get(lid) is None


def test_release_returns_false_for_unknown(store: LeaseStore) -> None:
    """release() returns False when the lease_id does not exist."""
    assert store.release("no-such-lease") is False


# ---------------------------------------------------------------------------
# active_count
# ---------------------------------------------------------------------------


def test_active_count_zero_initially(store: LeaseStore) -> None:
    """active_count() is zero for a model with no leases."""
    assert store.active_count("m1") == 0


def test_active_count_increments(store: LeaseStore) -> None:
    """active_count() reflects the number of active leases per model."""
    _acquire(store, "m1")
    _acquire(store, "m1")
    _acquire(store, "m2")
    assert store.active_count("m1") == 2
    assert store.active_count("m2") == 1


def test_active_count_decrements_on_release(store: LeaseStore) -> None:
    """active_count() decrements after releasing a lease."""
    lid = _acquire(store, "m1")
    assert store.active_count("m1") == 1
    store.release(lid)
    assert store.active_count("m1") == 0


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


def test_expired_lease_not_returned_by_get(store: LeaseStore) -> None:
    """get() returns None for a lease whose TTL has elapsed."""
    lid = _acquire(store, ttl=0)
    time.sleep(0.01)
    assert store.get(lid) is None


def test_expired_leases_not_counted(store: LeaseStore) -> None:
    """active_count() excludes leases past their TTL."""
    _acquire(store, "m1", ttl=0)
    time.sleep(0.01)
    assert store.active_count("m1") == 0


def test_cleanup_expired_removes_stale(store: LeaseStore) -> None:
    """cleanup_expired() removes expired leases and returns the count removed."""
    _acquire(store, ttl=0)
    _acquire(store, ttl=300)
    time.sleep(0.01)
    removed = store.cleanup_expired()
    assert removed == 1
    assert len(store.snapshot()) == 1


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_active_leases(store: LeaseStore) -> None:
    """snapshot() returns all non-expired leases."""
    _acquire(store, "m1")
    _acquire(store, "m2")
    snaps = store.snapshot()
    assert len(snaps) == 2
    models = {s.granted_model_id for s in snaps}
    assert models == {"m1", "m2"}
