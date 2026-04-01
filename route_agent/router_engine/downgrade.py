"""Automatic downgrade optimizer for router_engine."""

from __future__ import annotations

import math
import random
from typing import Any, Callable

from route_agent.model_registry.constants import PRICE_UNAVAILABLE_SENTINEL
from route_agent.router_engine.class_pool import ClassPoolManager
from route_agent.router_engine.constants import (
    DOWNGRADE_CANARY_RATIO,
    DOWNGRADE_COOLDOWN_H,
    DOWNGRADE_MIN_SAVINGS_RATIO,
    DOWNGRADE_PROMOTION_MIN_SUCCESS,
    DOWNGRADE_ROLLBACK_EXEC_FAIL,
    DOWNGRADE_ROLLBACK_QUALITY_FAIL,
    DOWNGRADE_SCORE_GAP_MAX,
    DOWNGRADE_SUCCESS_THRESHOLD,
    DOWNGRADE_TRIAL_MIN_SAMPLES,
)
from route_agent.router_engine.health import HealthManager
from route_agent.router_engine.rate_limiters import RateLimiter
from route_agent.router_engine.scorer import compute_cost_score
from route_agent.router_engine.schemas import ModelCandidate, RouteDecision
from route_agent.router_engine.storage import RouterStorage

ModelMetadataResolver = Callable[[str], Any | None]


def _wilson_lower_bound(success_count: int, fail_count: int, z: float = 1.645) -> float:
    """Compute Wilson lower bound for success probability."""
    n = success_count + fail_count
    if n <= 0:
        return 0.0
    p = success_count / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - margin) / denom


def _price_per_1m_from_metadata(metadata: Any | None) -> float:
    """Extract input price per 1M tokens from model metadata."""
    if metadata is None:
        return PRICE_UNAVAILABLE_SENTINEL
    pricing = getattr(metadata, "pricing", None)
    if not isinstance(pricing, dict):
        return PRICE_UNAVAILABLE_SENTINEL

    raw = pricing.get("input")
    try:
        if raw is None:
            return PRICE_UNAVAILABLE_SENTINEL
        price = float(raw)
    except (TypeError, ValueError):
        return PRICE_UNAVAILABLE_SENTINEL

    unit = str(pricing.get("unit") or "").strip().lower()
    if unit == "per_1m_tokens":
        return price
    return price * 1000.0


class DowngradeOptimizer:
    """Handle downgrade trial lifecycle and canary routing."""

    def __init__(
        self,
        class_pool_mgr: ClassPoolManager,
        health: HealthManager,
        rate_limiter: RateLimiter,
        router_storage: RouterStorage,
        model_metadata_resolver: ModelMetadataResolver | None = None,
    ) -> None:
        """Initialize the instance."""
        self._class_pool_mgr = class_pool_mgr
        self._health = health
        self._rate_limiter = rate_limiter
        self._storage = router_storage
        self._model_metadata_resolver = model_metadata_resolver

    def _resolve_metadata(self, model_id: str) -> Any | None:
        """Resolve model metadata with optional resolver."""
        if self._model_metadata_resolver is None:
            return None
        return self._model_metadata_resolver(model_id)

    async def _select_cheaper_candidate_async(
        self,
        agent_class: str,
        current_model_id: str,
    ) -> tuple[ModelCandidate, float] | None:
        """Select one cheaper challenger candidate and expected savings ratio."""
        current_stats = await self._storage.get_stats_async(agent_class, current_model_id)
        current_metadata = self._resolve_metadata(current_model_id)
        current_price = _price_per_1m_from_metadata(current_metadata)
        if current_stats is None or current_price >= PRICE_UNAVAILABLE_SENTINEL:
            return None

        current_wlb = _wilson_lower_bound(current_stats.success_count, current_stats.fail_count)
        stats_items = await self._storage.list_stats_for_class_async(agent_class)

        best: tuple[ModelCandidate, float, float] | None = None
        for stats in stats_items:
            if stats.model_id == current_model_id:
                continue

            availability = await self._storage.get_availability_async(stats.model_id)
            if availability is not None and availability.status == "unable":
                continue

            metadata = self._resolve_metadata(stats.model_id)
            if metadata is None:
                continue

            challenger_price = _price_per_1m_from_metadata(metadata)
            if challenger_price >= PRICE_UNAVAILABLE_SENTINEL:
                continue
            if challenger_price >= current_price:
                continue

            challenger_wlb = _wilson_lower_bound(stats.success_count, stats.fail_count)
            ratio_score = 1.0 if current_wlb <= 0 else min(1.0, challenger_wlb / current_wlb)
            expected_savings = max(0.0, (current_price - challenger_price) / max(current_price, 1e-9))

            pricing = getattr(metadata, "pricing", {})
            provider = str(getattr(metadata, "provider", "") or "")
            display_name = str(getattr(metadata, "display_name", stats.model_id) or stats.model_id)
            candidate = ModelCandidate(
                model_id=stats.model_id,
                provider=provider,
                display_name=display_name,
                dimension_score=ratio_score,
                raw_dimension_score=ratio_score,
                cost_score=compute_cost_score(pricing, tuple()),
                health_status="healthy",
            )

            # Prioritize larger savings; tie-break by relative quality score.
            rank_key = (expected_savings, ratio_score)
            if best is None or rank_key > (best[2], best[1]):
                best = (candidate, ratio_score, expected_savings)

        if best is None:
            return None
        selected_candidate, _quality_ratio, savings = best
        return selected_candidate, savings

    async def maybe_start_downgrade_trial_async(
        self,
        agent_class: str,
        domain: str,
        current_model_id: str,
    ) -> str | None:
        """Try to create a downgrade trial and return challenger model id when started."""
        active = await self._storage.get_active_downgrade_trial_async(agent_class, domain)
        if active is not None:
            return None

        selection = await self._select_cheaper_candidate_async(agent_class, current_model_id)
        if selection is None:
            return None

        challenger, expected_savings_ratio = selection
        if expected_savings_ratio < DOWNGRADE_MIN_SAVINGS_RATIO:
            return None

        should_try = await self.should_try_downgrade_async(
            agent_class,
            domain,
            current_model_id,
            challenger,
        )
        if not should_try:
            return None

        started = await self.start_downgrade_trial_async(
            agent_class,
            domain,
            current_model_id,
            challenger.model_id,
            expected_savings_ratio,
        )
        if not started:
            return None
        return challenger.model_id

    async def should_try_downgrade_async(
        self,
        agent_class: str,
        domain: str,
        current_model_id: str,
        next_cheaper: ModelCandidate,
    ) -> bool:
        """Execute `should_try_downgrade_async`."""
        stats = await self._storage.get_stats_async(agent_class, current_model_id)
        if stats is None:
            return False

        if stats.consecutive_success < DOWNGRADE_SUCCESS_THRESHOLD:
            return False

        current_default = await self._class_pool_mgr.get_default(agent_class, domain)
        if current_default is None or current_default != current_model_id:
            return False

        score_gap = max(0.0, 1.0 - next_cheaper.dimension_score)
        if score_gap > DOWNGRADE_SCORE_GAP_MAX:
            return False

        if next_cheaper.health_status == "unable":
            return False

        limits = {}
        util = await self._rate_limiter.get_utilization_async(next_cheaper.model_id, limits)
        if util.is_limited:
            return False

        current_effective = max(1e-9, next_cheaper.cost_score + DOWNGRADE_MIN_SAVINGS_RATIO)
        expected_savings_ratio = max(0.0, (current_effective - next_cheaper.cost_score) / current_effective)
        if expected_savings_ratio < DOWNGRADE_MIN_SAVINGS_RATIO:
            return False

        in_cooldown = await self._storage.is_downgrade_in_cooldown_async(
            agent_class,
            domain,
            next_cheaper.model_id,
        )
        return not in_cooldown

    async def start_downgrade_trial_async(
        self,
        agent_class: str,
        domain: str,
        incumbent_model_id: str,
        challenger_model_id: str,
        expected_savings_ratio: float,
    ) -> bool:
        """Execute `start_downgrade_trial_async`."""
        return await self._storage.start_downgrade_trial_async(
            agent_class,
            domain,
            incumbent_model_id,
            challenger_model_id,
            expected_savings_ratio,
            canary_ratio=DOWNGRADE_CANARY_RATIO,
        )

    async def choose_trial_model_async(
        self,
        agent_class: str,
        domain: str,
        decision: RouteDecision,
    ) -> RouteDecision:
        """Execute `choose_trial_model_async`."""
        trial = await self._storage.get_active_downgrade_trial_async(agent_class, domain)
        if trial is None:
            return decision

        challenger = str(trial["challenger_model_id"])
        ratio = float(trial.get("canary_ratio") or DOWNGRADE_CANARY_RATIO)
        if random.random() > ratio:
            return decision

        for idx, candidate in enumerate(decision.candidates):
            if candidate.model_id == challenger:
                return RouteDecision(
                    primary_model=challenger,
                    candidates=decision.candidates,
                    start_index=idx,
                    reason=f"downgrade trial canary switched to challenger index={idx}",
                    alerts=decision.alerts,
                    default_used=decision.default_used,
                    pool_hit=decision.pool_hit,
                    pool_class=decision.pool_class,
                    class_source=decision.class_source,
                )

        return decision

    async def record_downgrade_result_async(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> str:
        """Execute `record_downgrade_result_async`."""
        trial = await self._storage.record_downgrade_trial_observation_async(
            agent_class,
            domain,
            model_id,
            outcome_type,
        )
        if trial is None:
            return "continue"

        quality_fail_count = int(trial.get("quality_fail_count") or 0)
        exec_fail_count = int(trial.get("exec_fail_count") or 0)
        sampled_requests = int(trial.get("sampled_requests") or 0)

        if quality_fail_count >= DOWNGRADE_ROLLBACK_QUALITY_FAIL:
            return "rollback"
        if exec_fail_count >= DOWNGRADE_ROLLBACK_EXEC_FAIL:
            return "rollback"

        if sampled_requests < DOWNGRADE_TRIAL_MIN_SAMPLES:
            return "continue"

        challenger = str(trial.get("challenger_model_id") or "")
        stats = await self._storage.get_stats_async(agent_class, challenger)
        if stats is None or stats.success_count < DOWNGRADE_PROMOTION_MIN_SUCCESS:
            return "continue"

        promoted = await self._class_pool_mgr._defaults_store.evaluate_and_promote_default_async(  # noqa: SLF001
            agent_class,
            domain,
            min_success=DOWNGRADE_PROMOTION_MIN_SUCCESS,
        )
        if promoted is not None and promoted.model_id == challenger:
            return "promote"

        return "continue"

    async def finalize_downgrade_trial_async(self, agent_class: str, domain: str, result: str) -> None:
        """Execute `finalize_downgrade_trial_async`."""
        if result == "promote":
            await self._storage.finish_downgrade_trial_async(agent_class, domain, "promoted")
            return

        if result == "rollback":
            await self._storage.finish_downgrade_trial_async(
                agent_class,
                domain,
                "rolled_back",
                cooldown_h=DOWNGRADE_COOLDOWN_H,
            )
