"""Outcome processor: aggregates cross-app execution outcomes and signals score changes.

Flow:
  Execution completes
    → client calls POST /api/v1/outcomes/report (fire-and-forget)
    → OutcomeProcessor.process_async() accumulates stats in federation_outcome_stats
    → When per-model sample count since last signal >= significance_threshold:
        - Bump pool_version so clients know new scores are available
        - Clients fetch scores via GET /federation-scores/{class} and rank locally
"""

from __future__ import annotations

import logging
import os

from route_agent.federation.server.pool_version import PoolVersionManager
from route_agent.federation.server.storage import FederationStorage

logger = logging.getLogger(__name__)

_DEFAULT_SIGNIFICANCE_THRESHOLD = 20


def _load_significance_threshold() -> int:
    """Load the significance threshold from env, falling back to the default."""
    try:
        return int(os.getenv("FEDERATION_SIGNIFICANCE_THRESHOLD", str(_DEFAULT_SIGNIFICANCE_THRESHOLD)))
    except (TypeError, ValueError):
        return _DEFAULT_SIGNIFICANCE_THRESHOLD


class OutcomeProcessor:
    """Aggregates federated outcomes and bumps pool version when scores change significantly."""

    def __init__(
        self,
        storage: FederationStorage,
        pool_version_mgr: PoolVersionManager,
        significance_threshold: int | None = None,
    ) -> None:
        """Initialise with storage, pool version manager, and optional threshold override."""
        self._storage = storage
        self._pool_version_mgr = pool_version_mgr
        self._threshold = (
            significance_threshold
            if significance_threshold is not None
            else _load_significance_threshold()
        )

    async def process_async(
        self,
        model_id: str,
        agent_class: str,
        outcome_type: str,
        duration_ms: float | None = None,  # noqa: ARG002 — reserved for future latency scoring
        quality_score: float | None = None,  # noqa: ARG002 — reserved for future quality scoring
    ) -> None:
        """Increment outcome stat and bump version when change is significant."""
        row = await self._storage.increment_outcome_stat_async(agent_class, model_id, outcome_type)

        total = int(row.get("total_count") or 0)
        last_reorder = int(row.get("last_reorder_count") or 0)
        since_last = total - last_reorder

        if since_last >= self._threshold:
            await self._bump_score_version_async(agent_class, model_id, total)

    async def _bump_score_version_async(
        self,
        agent_class: str,
        trigger_model_id: str,
        current_total: int,
    ) -> None:
        """Bump pool version to signal clients that federation scores have changed.

        Does NOT compute or store rankings — clients fetch scores via the
        /federation-scores endpoint and rank locally using weighted merge.
        """
        # Mark reorder count to prevent repeated triggers
        await self._storage.mark_reorder_async(agent_class, trigger_model_id, current_total)

        entry = await self._pool_version_mgr.bump_async(agent_class, ())
        logger.info(
            "federation score version bump | class=%s new_version=%d trigger=%s",
            agent_class, entry.version, trigger_model_id,
        )
