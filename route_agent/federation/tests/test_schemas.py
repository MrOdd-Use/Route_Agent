"""Tests for federation data schemas."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from route_agent.federation.schemas import (
    AgentDeclaration,
    AgentMappingEntry,
    AppRegisterRequest,
    AppRegisterResponse,
    AppRegistration,
    ConcurrencyLease,
    LocalPoolSnapshot,
    ModeDecision,
    PoolVersionEntry,
    RegisteredAgent,
)

_NOW = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_app_registration_is_frozen() -> None:
    """AppRegistration must reject mutation."""
    reg = AppRegistration(app_id="app1", app_name="App One", registered_at=_NOW, last_seen_at=_NOW)
    with pytest.raises((FrozenInstanceError, TypeError)):
        reg.app_name = "changed"  # type: ignore[misc]


def test_registered_agent_is_frozen() -> None:
    """RegisteredAgent must reject mutation."""
    agent = RegisteredAgent(
        app_id="app1", agent_name="summarizer", agent_class="summarization",
        registered_at=_NOW, updated_at=_NOW,
    )
    with pytest.raises((FrozenInstanceError, TypeError)):
        agent.agent_class = "general"  # type: ignore[misc]


def test_concurrency_lease_is_frozen() -> None:
    """ConcurrencyLease must reject mutation."""
    lease = ConcurrencyLease(
        lease_id="lease-1", app_id="app1", agent_name="a", agent_class="general",
        proposed_model_id="m1", granted_model_id="m1", mode="local", acquired_at=_NOW,
    )
    with pytest.raises((FrozenInstanceError, TypeError)):
        lease.mode = "central"  # type: ignore[misc]


def test_pool_version_entry_is_frozen() -> None:
    """PoolVersionEntry must reject mutation."""
    entry = PoolVersionEntry(version=1, agent_class="general", model_ids=("m1",), updated_at=_NOW)
    with pytest.raises((FrozenInstanceError, TypeError)):
        entry.version = 2  # type: ignore[misc]


def test_local_pool_snapshot_is_frozen() -> None:
    """LocalPoolSnapshot must reject mutation."""
    snap = LocalPoolSnapshot(
        agent_class="summarization", ordered_model_ids=("m1", "m2"),
        pool_version=3, synced_at=_NOW,
    )
    with pytest.raises((FrozenInstanceError, TypeError)):
        snap.pool_version = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------


def test_registered_agent_default_version() -> None:
    """RegisteredAgent defaults agent_version to 'v1'."""
    agent = RegisteredAgent(
        app_id="app1", agent_name="a", agent_class="general",
        registered_at=_NOW, updated_at=_NOW,
    )
    assert agent.agent_version == "v1"


def test_concurrency_lease_default_ttl() -> None:
    """ConcurrencyLease defaults ttl_seconds to 300 and released_at to None."""
    lease = ConcurrencyLease(
        lease_id="l1", app_id="app1", agent_name="a", agent_class="general",
        proposed_model_id="m1", granted_model_id="m1", mode="local", acquired_at=_NOW,
    )
    assert lease.ttl_seconds == 300
    assert lease.released_at is None


def test_agent_mapping_entry_default_source() -> None:
    """AgentMappingEntry defaults source to 'declared' and version to 'v1'."""
    entry = AgentMappingEntry(
        app_id="app1", agent_name="a", agent_class="general",
        created_at=_NOW, updated_at=_NOW,
    )
    assert entry.source == "declared"
    assert entry.agent_version == "v1"


def test_local_pool_snapshot_default_model_id() -> None:
    """LocalPoolSnapshot defaults default_model_id to None."""
    snap = LocalPoolSnapshot(
        agent_class="general", ordered_model_ids=(), pool_version=1, synced_at=_NOW
    )
    assert snap.default_model_id is None


# ---------------------------------------------------------------------------
# API containers
# ---------------------------------------------------------------------------


def test_app_register_request_empty_agents() -> None:
    """AppRegisterRequest defaults agents to an empty tuple."""
    req = AppRegisterRequest(app_id="app1", app_name="App One")
    assert req.agents == ()


def test_app_register_request_with_agents() -> None:
    """AppRegisterRequest preserves all declared agents."""
    req = AppRegisterRequest(
        app_id="app1",
        app_name="App One",
        agents=(
            AgentDeclaration(agent_name="planner", agent_class="general"),
            AgentDeclaration(agent_name="scraper", agent_class="scrape", agent_version="v2"),
        ),
    )
    assert len(req.agents) == 2
    assert req.agents[1].agent_version == "v2"


def test_app_register_response_pool_versions() -> None:
    """AppRegisterResponse carries pool version map."""
    resp = AppRegisterResponse(
        app_id="app1",
        registered_at=_NOW,
        pool_versions={"general": 5, "scrape": 3},
    )
    assert resp.pool_versions["general"] == 5


def test_mode_decision_fields() -> None:
    """ModeDecision exposes all expected fields."""
    decision = ModeDecision(
        agent_class="summarization", model_id="deepseek-chat",
        mode="local", active_leases=2, threshold=5, decided_at=_NOW,
    )
    assert decision.mode == "local"
    assert decision.active_leases < decision.threshold
