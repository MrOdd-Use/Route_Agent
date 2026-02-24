"""Automatic downgrade optimizer for router_engine."""

from __future__ import annotations

import random

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
from route_agent.router_engine.schemas import ModelCandidate, RouteDecision
from route_agent.router_engine.storage import RouterStorage


class DowngradeOptimizer:
    """Handle downgrade trial lifecycle and canary routing."""

    def __init__(
        self,
        class_pool_mgr: ClassPoolManager,
        health: HealthManager,
        rate_limiter: RateLimiter,
        router_storage: RouterStorage,
    ) -> None:
        self._class_pool_mgr = class_pool_mgr
        self._health = health
        self._rate_limiter = rate_limiter
        self._storage = router_storage

    async def should_try_downgrade_async(
        self,
        agent_class: str,
        domain: str,
        current_model_id: str,
        next_cheaper: ModelCandidate,
    ) -> bool:
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
