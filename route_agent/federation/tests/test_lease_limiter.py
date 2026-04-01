"""Tests for LeaseLimiter: mode decision based on active lease counts."""

from __future__ import annotations

from route_agent.federation.server.lease_limiter import (
    TIER_FREE,
    TIER_RATE_LIMITED,
    TIER_UNLIMITED,
    LeaseLimiter,
)
from route_agent.federation.server.lease_store import LeaseStore


def _make_limiter(thresholds: dict[str, int] | None = None) -> tuple[LeaseLimiter, LeaseStore]:
    """Create a LeaseLimiter with an isolated LeaseStore and optional thresholds."""
    store = LeaseStore()
    limiter = LeaseLimiter(store, thresholds=thresholds or {TIER_FREE: 3, TIER_RATE_LIMITED: 2})
    return limiter, store


def _add_lease(store: LeaseStore, model_id: str) -> str:
    """Helper: acquire a lease for model_id and return its ID."""
    return store.acquire(
        app_id="app1", agent_name="a", agent_class="general",
        proposed_model_id=model_id, granted_model_id=model_id,
        mode="local",
    ).lease_id


# ---------------------------------------------------------------------------
# threshold_for
# ---------------------------------------------------------------------------


def test_threshold_for_free_tier() -> None:
    """threshold_for returns the configured value for TIER_FREE."""
    limiter, _ = _make_limiter({TIER_FREE: 5})
    assert limiter.threshold_for(TIER_FREE) == 5


def test_threshold_for_unknown_tier_uses_free_default() -> None:
    """threshold_for falls back to the free-tier default for unknown tiers."""
    limiter, _ = _make_limiter()
    assert limiter.threshold_for("some_unknown_tier") == 3


# ---------------------------------------------------------------------------
# decide_mode: local
# ---------------------------------------------------------------------------


def test_decide_mode_local_when_no_leases() -> None:
    """decide_mode returns 'local' when there are no active leases."""
    limiter, _ = _make_limiter()
    decision = limiter.decide_mode("general", "m1")
    assert decision.mode == "local"
    assert decision.active_leases == 0


def test_decide_mode_local_below_threshold() -> None:
    """decide_mode returns 'local' when active leases are below the threshold."""
    limiter, store = _make_limiter({TIER_FREE: 3})
    _add_lease(store, "m1")
    _add_lease(store, "m1")
    decision = limiter.decide_mode("general", "m1", TIER_FREE)
    assert decision.mode == "local"
    assert decision.active_leases == 2
    assert decision.threshold == 3


# ---------------------------------------------------------------------------
# decide_mode: central
# ---------------------------------------------------------------------------


def test_decide_mode_central_at_threshold() -> None:
    """decide_mode returns 'central' when active leases reach the threshold."""
    limiter, store = _make_limiter({TIER_FREE: 3})
    for _ in range(3):
        _add_lease(store, "m1")
    decision = limiter.decide_mode("general", "m1", TIER_FREE)
    assert decision.mode == "central"
    assert decision.active_leases == 3


def test_decide_mode_central_rate_limited_tier() -> None:
    """decide_mode returns 'central' for rate-limited models at their threshold."""
    limiter, store = _make_limiter({TIER_RATE_LIMITED: 2})
    _add_lease(store, "m1")
    _add_lease(store, "m1")
    decision = limiter.decide_mode("summarization", "m1", TIER_RATE_LIMITED)
    assert decision.mode == "central"


# ---------------------------------------------------------------------------
# decide_mode: fields
# ---------------------------------------------------------------------------


def test_decide_mode_decision_fields() -> None:
    """decide_mode returns a ModeDecision with all expected fields populated."""
    limiter, _ = _make_limiter()
    decision = limiter.decide_mode("scrape", "deepseek-chat")
    assert decision.agent_class == "scrape"
    assert decision.model_id == "deepseek-chat"
    assert decision.decided_at is not None


def test_decide_mode_unlimited_always_local() -> None:
    """decide_mode always returns 'local' for unlimited-tier models."""
    limiter, store = _make_limiter({TIER_UNLIMITED: 2**31})
    for _ in range(100):
        _add_lease(store, "local-m")
    decision = limiter.decide_mode("general", "local-m", TIER_UNLIMITED)
    assert decision.mode == "local"
