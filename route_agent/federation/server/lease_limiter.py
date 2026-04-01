"""Lease-backed mode decision engine.

Determines whether a locally proposed model should run locally ("local") or
be overridden by the central router ("central") based on active-lease counts
and configurable per-tier thresholds.

active_leases(model) < threshold  → mode = "local"
active_leases(model) >= threshold → mode = "central"
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from route_agent.federation.schemas import ModeDecision
from route_agent.federation.server.lease_store import LeaseStore

# Model tier identifiers
TIER_FREE = "free"
TIER_RATE_LIMITED = "rate_limited"
TIER_UNLIMITED = "unlimited"

# Default per-tier thresholds (overridable via env)
_DEFAULT_THRESHOLDS: dict[str, int] = {
    TIER_FREE: 3,
    TIER_RATE_LIMITED: 2,
    TIER_UNLIMITED: 2**31,  # effectively infinite
}


def _load_thresholds() -> dict[str, int]:
    """Load thresholds from env, falling back to defaults."""
    return {
        TIER_FREE: int(os.getenv("FEDERATION_THRESHOLD_FREE", str(_DEFAULT_THRESHOLDS[TIER_FREE]))),
        TIER_RATE_LIMITED: int(
            os.getenv("FEDERATION_THRESHOLD_RATE_LIMITED", str(_DEFAULT_THRESHOLDS[TIER_RATE_LIMITED]))
        ),
        TIER_UNLIMITED: _DEFAULT_THRESHOLDS[TIER_UNLIMITED],
    }


class LeaseLimiter:
    """Contention-based mode decision using active lease counts."""

    def __init__(
        self,
        lease_store: LeaseStore,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        """Initialise with a LeaseStore and optional per-tier threshold overrides."""
        self._lease_store = lease_store
        self._thresholds = thresholds if thresholds is not None else _load_thresholds()

    def threshold_for(self, tier: str) -> int:
        """Return the configured threshold for a model tier."""
        return self._thresholds.get(tier, _DEFAULT_THRESHOLDS[TIER_FREE])

    def decide_mode(
        self,
        agent_class: str,
        model_id: str,
        tier: str = TIER_FREE,
    ) -> ModeDecision:
        """Return a ModeDecision for the (agent_class, model_id) pair.

        Cleans up expired leases before counting to avoid stale state.
        """
        self._lease_store.cleanup_expired()
        threshold = self.threshold_for(tier)
        active = self._lease_store.active_count(model_id)
        mode = "local" if active < threshold else "central"

        return ModeDecision(
            agent_class=agent_class,
            model_id=model_id,
            mode=mode,
            active_leases=active,
            threshold=threshold,
            decided_at=datetime.now(tz=timezone.utc),
        )
