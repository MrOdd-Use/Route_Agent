"""Core model selection algorithm for router_engine."""

from __future__ import annotations

import logging
import math
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.class_pool import ClassPoolManager
from route_agent.router_engine.constants import (
    CEILING_SLOTS,
    COLD_START_INDEX_BY_COMPLEXITY,
    CONC_UTIL_HIGH,
    CONC_UTIL_LOW,
    CONTEXT_LIMIT_BUFFER_RATIO,
    DEGRADED_QUALITY_MULTIPLIER,
    DEFAULT_SKIP_POWER,
    EXPLORE_AVG_TRIALS_THRESHOLD,
    EXPLORE_POOL_RICH_THRESHOLD,
    EXPLORE_SLOTS_MAX,
    EXPLORE_SLOTS_MIN,
    MAX_EXPLORE_SLOTS,
    MAX_SAME_PROVIDER_IN_CANDIDATES,
    MIN_CANDIDATES_FOR_AUTO,
    NEW_MODEL_BONUS,
    NEW_MODEL_LOOKBACK_DAYS,
    PROBE_TESTING_PENALTY,
    RPM_UTIL_HIGH,
    RPM_UTIL_LOW,
    SCORE_TIER_EPSILON,
)
from route_agent.router_engine.health import HealthManager
from route_agent.router_engine.rate_limiters import RateLimiter
from route_agent.router_engine.scorer import (
    compute_cost_score,
    compute_dimension_score,
    compute_effective_price_per_1m,
    compute_overload_penalty,
    compute_wilson_bonus,
)
from route_agent.router_engine.schemas import ModelCandidate, RouteDecision, RouteRequest


def _model_release_date(model: ModelMetadata) -> str | None:
    """Execute `_model_release_date`."""
    routing = model.routing if isinstance(model.routing, dict) else {}
    value = routing.get("release_date")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_date(value: str | None) -> datetime | None:
    """Execute `_parse_date`."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _provider_diverse_limit(
    candidates: list[ModelCandidate],
    limit: int,
    max_per_provider: int,
) -> list[ModelCandidate]:
    """Execute `_provider_diverse_limit`."""
    selected: list[ModelCandidate] = []
    provider_count: dict[str, int] = {}
    overflow: list[ModelCandidate] = []

    for candidate in candidates:
        count = provider_count.get(candidate.provider, 0)
        if count < max_per_provider and len(selected) < limit:
            selected.append(candidate)
            provider_count[candidate.provider] = count + 1
        else:
            overflow.append(candidate)

    if len(selected) < limit:
        for candidate in overflow:
            if len(selected) >= limit:
                break
            selected.append(candidate)

    return selected


def _adaptive_explore_slots(pool_size: int, avg_trials: float) -> int:
    """Execute `_adaptive_explore_slots`."""
    pool_rich = pool_size >= EXPLORE_POOL_RICH_THRESHOLD and avg_trials >= EXPLORE_AVG_TRIALS_THRESHOLD
    pool_thin = pool_size < 3 or avg_trials < EXPLORE_AVG_TRIALS_THRESHOLD

    if pool_rich:
        return EXPLORE_SLOTS_MIN
    if pool_thin:
        return EXPLORE_SLOTS_MAX
    return MAX_EXPLORE_SLOTS


def _cold_start_index(request: RouteRequest, candidate_count: int) -> int:
    """Execute `_cold_start_index`."""
    if candidate_count <= 0:
        return 0

    dimensions = request.analysis.relevant_dimensions
    if not dimensions:
        return max(0, min(2, candidate_count - 1))

    max_dim_score = max(dim.score for dim in dimensions)
    for _label, threshold, index in COLD_START_INDEX_BY_COMPLEXITY:
        if max_dim_score >= threshold:
            return max(0, min(index, candidate_count - 1))
    return max(0, min(2, candidate_count - 1))


class ModelSelector:
    """Select route candidates from available models."""

    def __init__(
        self,
        health: HealthManager,
        rate_limiter: RateLimiter,
        class_pool_mgr: ClassPoolManager,
        local_store: Any | None = None,
    ) -> None:
        """Initialize the instance."""
        self._health = health
        self._rate_limiter = rate_limiter
        self._class_pool_mgr = class_pool_mgr
        self._local_store = local_store

    def _check_skip(self, ratio: float, low: float, high: float) -> bool:
        """Execute `_check_skip`."""
        if ratio <= low:
            return False
        if ratio >= high:
            return True

        normalized = (ratio - low) / (high - low)
        probability = normalized**DEFAULT_SKIP_POWER
        # Deterministic proxy for probabilistic skip (keeps tests deterministic).
        return probability >= 0.5

    def _should_skip_default(self, util: Any) -> bool:
        """Execute `_should_skip_default`."""
        if util.is_limited:
            return True
        return self._check_skip(util.rpm_ratio, RPM_UTIL_LOW, RPM_UTIL_HIGH) or self._check_skip(
            util.conc_ratio,
            CONC_UTIL_LOW,
            CONC_UTIL_HIGH,
        )

    async def select_async(self, available_models: list[ModelMetadata], request: RouteRequest) -> RouteDecision:
        """Execute `select_async`."""
        agent_class, class_source = await self._class_pool_mgr.resolve_class_async(request)
        pool_entries = await self._class_pool_mgr.get_pool_entries_async(agent_class)
        logger.info(
            "SELECT_START | class=%s source=%s available=%d pool_entries=%d domain=%s",
            agent_class, class_source, len(available_models),
            len(pool_entries), request.analysis.domain,
        )

        # Lazy seed: 空池时用 top-N composite 模型种子化
        if not pool_entries and available_models:
            seeded = await self._class_pool_mgr.seed_pool_async(agent_class, available_models)
            if seeded:
                pool_entries = await self._class_pool_mgr.get_pool_entries_async(agent_class)

        default_model_id = await self._class_pool_mgr.get_default(agent_class, request.analysis.domain)
        pool_by_id = {entry.model_id: entry for entry in pool_entries}

        # Fetch raw federation counts {model_id: (success, fail)} from local cache
        fed_counts: dict[str, tuple[int, int]] = await self._class_pool_mgr.get_federation_scores_map(
            agent_class, self._local_store
        )
        # alpha: trust of local data vs federation. 0 = full federation, 1 = full local.
        # Grows as local feedback accumulates; per-model, averaged across pool.
        local_sample_count = sum(
            (e.success_count + e.fail_count) for e in pool_entries
        )
        _alpha = 1.0 - math.exp(-local_sample_count / 50.0)

        excluded = set(request.constraints.exclude_models)
        candidate_rows: list[ModelCandidate] = []
        degraded_flags: dict[str, bool] = {}
        probe_testing_flags: dict[str, bool] = {}

        for model in available_models:
            selectable, is_degraded, is_probe_testing = await self._health.is_available_async(model.model_id)
            if not selectable:
                continue

            rate_limited = await self._rate_limiter.is_rate_limited_async(model.model_id, model.limits)
            if rate_limited:
                continue

            if model.model_id in excluded:
                continue

            if request.constraints.require_provider and model.provider != request.constraints.require_provider:
                continue

            effective_price = compute_effective_price_per_1m(model.pricing, request.analysis.relevant_dimensions)
            if request.constraints.max_cost is not None and effective_price > request.constraints.max_cost:
                continue

            estimated_tokens = request.constraints.estimated_input_tokens
            if estimated_tokens is not None:
                max_context = model.limits.get("context_length")
                try:
                    max_context_value = int(max_context) if max_context is not None else 0
                except (TypeError, ValueError):
                    max_context_value = 0
                if max_context_value > 0:
                    if estimated_tokens > int(max_context_value * CONTEXT_LIMIT_BUFFER_RATIO):
                        continue

            raw_dimension = compute_dimension_score(request.analysis.relevant_dimensions, model.capabilities)
            cost_score = compute_cost_score(model.pricing, request.analysis.relevant_dimensions)

            candidate_rows.append(
                ModelCandidate(
                    model_id=model.model_id,
                    provider=model.provider,
                    display_name=model.display_name,
                    dimension_score=raw_dimension,
                    raw_dimension_score=raw_dimension,
                    cost_score=cost_score,
                    health_status="healthy",
                    rate_limited=False,
                    is_default=(model.model_id == default_model_id),
                    is_pool=(model.model_id in pool_by_id),
                    is_explore=False,
                    rank=0,
                )
            )
            degraded_flags[model.model_id] = is_degraded
            probe_testing_flags[model.model_id] = is_probe_testing

        logger.info(
            "SELECT_FILTER | passed=%d excluded=%d (from %d available)",
            len(candidate_rows), len(excluded), len(available_models),
        )

        if not candidate_rows:
            return RouteDecision(
                primary_model=None,
                candidates=tuple(),
                start_index=0,
                reason="no candidate models available after filters",
                alerts=("0 个候选模型可用",),
                default_used=False,
                pool_hit=bool(pool_entries),
                pool_class=agent_class if pool_entries else None,
                class_source=class_source,
            )

        candidates = candidate_rows

        limits_by_id = {m.model_id: m.limits for m in available_models}

        adjusted: list[ModelCandidate] = []
        now = datetime.now(tz=timezone.utc)
        for candidate in candidates:
            is_degraded = degraded_flags.get(candidate.model_id, False)

            util = await self._rate_limiter.get_utilization_async(
                candidate.model_id, limits_by_id.get(candidate.model_id, {})
            )

            # Compute blended_bonus from local + federation Wilson lower bounds.
            # local_bonus uses pool stats; fed_bonus uses federation cache counts.
            # alpha: 0 = full federation trust (no local data), 1 = full local trust.
            pool_entry = pool_by_id.get(candidate.model_id)
            local_s = pool_entry.success_count if pool_entry else 0
            local_f = pool_entry.fail_count if pool_entry else 0
            local_bonus = compute_wilson_bonus(local_s, local_f)

            fed_pair = fed_counts.get(candidate.model_id)
            if fed_pair is not None:
                fed_bonus = compute_wilson_bonus(fed_pair[0], fed_pair[1])
                effective_alpha = _alpha
            else:
                fed_bonus = local_bonus  # no federation data → treat as local only
                effective_alpha = 1.0

            blended_bonus = effective_alpha * local_bonus + (1.0 - effective_alpha) * fed_bonus

            health_multiplier = compute_overload_penalty(util)
            if is_degraded:
                health_multiplier *= DEGRADED_QUALITY_MULTIPLIER

            # final_score = dimension_score × blended_bonus × health_multiplier + cost_score
            score = candidate.dimension_score * blended_bonus * health_multiplier + candidate.cost_score

            if probe_testing_flags.get(candidate.model_id, False):
                score -= PROBE_TESTING_PENALTY

            if not candidate.is_pool:
                model = next((m for m in available_models if m.model_id == candidate.model_id), None)
                release_date = _model_release_date(model) if model is not None else None
                release_dt = _parse_date(release_date)
                if release_dt is not None and now - release_dt <= timedelta(days=NEW_MODEL_LOOKBACK_DAYS):
                    score += NEW_MODEL_BONUS

            health_status = "quality_degraded" if is_degraded else "healthy"
            adjusted.append(
                replace(
                    candidate,
                    dimension_score=score,
                    health_status=health_status,
                )
            )

        sorted_by_cost = sorted(adjusted, key=lambda item: item.cost_score)
        ranked = sorted(sorted_by_cost, key=lambda item: item.dimension_score, reverse=True)

        # Fine-grained tier tie-break on near-equal scores.
        for idx in range(1, len(ranked)):
            prev = ranked[idx - 1]
            curr = ranked[idx]
            if abs(prev.dimension_score - curr.dimension_score) < SCORE_TIER_EPSILON and curr.cost_score < prev.cost_score:
                ranked[idx - 1], ranked[idx] = ranked[idx], ranked[idx - 1]

        final_candidates: list[ModelCandidate]

        if pool_entries:
            pool_ids = {entry.model_id for entry in pool_entries}
            pool_candidates = [item for item in ranked if item.model_id in pool_ids]
            non_pool_candidates = [item for item in ranked if item.model_id not in pool_ids]

            avg_trials = 0.0
            if pool_entries:
                avg_trials = sum((entry.success_count + entry.fail_count) for entry in pool_entries) / len(pool_entries)
            explore_slots = _adaptive_explore_slots(len(pool_entries), avg_trials)

            explore_candidates = _provider_diverse_limit(
                non_pool_candidates,
                limit=explore_slots,
                max_per_provider=MAX_SAME_PROVIDER_IN_CANDIDATES,
            )

            ceiling_model = max(ranked, key=lambda item: item.raw_dimension_score)
            selected_ids: set[str] = set()
            final_candidates = []

            # 1) 池模型优先（最多 3 个，按 composite 排序）
            for item in pool_candidates[:3]:
                final_candidates.append(item)
                selected_ids.add(item.model_id)

            # 2) ceiling 模型（与池重叠则不额外占位）
            if CEILING_SLOTS > 0 and ceiling_model.model_id not in selected_ids:
                final_candidates.append(ceiling_model)
                selected_ids.add(ceiling_model.model_id)

            # 3) explore 补位
            for item in explore_candidates:
                if len(final_candidates) >= 5:
                    break
                if item.model_id in selected_ids:
                    continue
                final_candidates.append(replace(item, is_explore=True))
                selected_ids.add(item.model_id)
        else:
            raw_candidates = ranked[: max(10, len(ranked))]
            final_candidates = _provider_diverse_limit(
                raw_candidates,
                limit=len(ranked),
                max_per_provider=MAX_SAME_PROVIDER_IN_CANDIDATES,
            )

        if not final_candidates:
            return RouteDecision(
                primary_model=None,
                candidates=tuple(),
                start_index=0,
                reason="no candidate models after top-k composition",
                alerts=("0 个候选模型可用",),
                default_used=False,
                pool_hit=bool(pool_entries),
                pool_class=agent_class if pool_entries else None,
                class_source=class_source,
            )

        final_candidates = [replace(item, rank=index) for index, item in enumerate(final_candidates)]
        candidate_ids = [item.model_id for item in final_candidates]

        start_index: int
        reason: str

        preferred = request.constraints.preferred_model
        if preferred and preferred in candidate_ids:
            start_index = candidate_ids.index(preferred)
            reason = f"preferred model selected at index={start_index}"
        elif default_model_id and default_model_id in candidate_ids:
            util = await self._rate_limiter.get_utilization_async(default_model_id, next(
                (m.limits for m in available_models if m.model_id == default_model_id),
                {},
            ))
            if self._should_skip_default(util):
                default_idx = candidate_ids.index(default_model_id)
                start_index = min(default_idx + 1, len(final_candidates) - 1)
                reason = f"default overloaded, shifted to cheaper neighbor at index={start_index}"
            else:
                start_index = candidate_ids.index(default_model_id)
                reason = f"class default selected at index={start_index}"
        elif pool_entries:
            # 池内加权随机：composite 差距 < SCORE_TIER_EPSILON 的模型之间均匀随机
            pool_ids_set = {entry.model_id for entry in pool_entries}
            pool_in_final = [
                (i, c) for i, c in enumerate(final_candidates)
                if c.model_id in pool_ids_set
            ]
            if pool_in_final:
                best_pool_score = pool_in_final[0][1].dimension_score
                tier_indices = [
                    i for i, c in pool_in_final
                    if abs(c.dimension_score - best_pool_score) < SCORE_TIER_EPSILON
                ]
                start_index = random.choice(tier_indices)
                reason = f"pool weighted-random start at index={start_index} (tier size={len(tier_indices)})"
            else:
                start_index = 0
                reason = "pool models not in final candidates, fallback to index=0"
        else:
            start_index = _cold_start_index(request, len(final_candidates))
            reason = f"cold start selection index={start_index}"

        if len(final_candidates) < 5 and not preferred and not default_model_id and not pool_entries:
            start_index = max(0, len(final_candidates) - 2)

        alerts: list[str] = []
        if len(final_candidates) < MIN_CANDIDATES_FOR_AUTO:
            alerts.append(f"仅 {len(final_candidates)} 个候选模型可用")

        primary_model = final_candidates[start_index].model_id

        logger.info(
            "SELECT_RESULT | primary=%s start_index=%d reason=%s "
            "final_candidates=%d default=%s pool_hit=%s",
            primary_model, start_index, reason,
            len(final_candidates), default_model_id, bool(pool_entries),
        )
        for c in final_candidates:
            logger.debug(
                "  FINAL #%d | %s dim=%.4f cost=%.4f pool=%s default=%s explore=%s health=%s",
                c.rank, c.model_id, c.dimension_score, c.cost_score,
                c.is_pool, c.is_default, c.is_explore, c.health_status,
            )

        return RouteDecision(
            primary_model=primary_model,
            candidates=tuple(final_candidates),
            start_index=start_index,
            reason=reason,
            alerts=tuple(alerts),
            default_used=(default_model_id == primary_model),
            pool_hit=bool(pool_entries),
            pool_class=agent_class if pool_entries else None,
            class_source=class_source,
        )
