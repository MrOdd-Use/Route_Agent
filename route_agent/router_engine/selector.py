"""Core model selection algorithm for router_engine."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.class_pool import ClassPoolManager
from route_agent.router_engine.constants import (
    CEILING_SLOTS,
    COLD_START_INDEX_BY_COMPLEXITY,
    CONC_UTIL_HIGH,
    CONC_UTIL_LOW,
    CONTEXT_LIMIT_BUFFER_RATIO,
    DEFAULT_SKIP_POWER,
    DEGRADED_PENALTY,
    EXPLORE_AVG_TRIALS_THRESHOLD,
    EXPLORE_POOL_RICH_THRESHOLD,
    EXPLORE_SLOTS_MAX,
    EXPLORE_SLOTS_MIN,
    MAX_EXPLORE_SLOTS,
    MAX_SAME_PROVIDER_IN_CANDIDATES,
    MIN_CANDIDATES_FOR_AUTO,
    NEW_MODEL_BONUS,
    NEW_MODEL_LOOKBACK_DAYS,
    PROBE_COOLDOWN_PENALTY,
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
    ) -> None:
        """Initialize the instance."""
        self._health = health
        self._rate_limiter = rate_limiter
        self._class_pool_mgr = class_pool_mgr

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
        default_model_id = await self._class_pool_mgr.get_default(agent_class, request.analysis.domain)
        pool_by_id = {entry.model_id: entry for entry in pool_entries}

        excluded = set(request.constraints.exclude_models)
        candidate_rows: list[ModelCandidate] = []
        degraded_flags: dict[str, bool] = {}
        cooldown_flags: dict[str, bool] = {}

        for model in available_models:
            selectable, is_degraded, is_probe_cooldown = await self._health.is_available_async(model.model_id)
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
                max_context = model.limits.get("max_context_tokens")
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
                    success_bonus=1.0,
                    fail_penalty=1.0,
                    rate_limited=False,
                    is_default=(model.model_id == default_model_id),
                    is_pool=(model.model_id in pool_by_id),
                    is_explore=False,
                    rank=0,
                )
            )
            degraded_flags[model.model_id] = is_degraded
            cooldown_flags[model.model_id] = is_probe_cooldown

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

        candidates = self._class_pool_mgr.apply_pool_bonus(candidate_rows, pool_entries)

        adjusted: list[ModelCandidate] = []
        now = datetime.now(tz=timezone.utc)
        for candidate in candidates:
            health_status, multiplier = await self._health.get_health_modifier_async(
                agent_class,
                candidate.model_id,
                candidate.cost_score,
            )

            score = candidate.dimension_score * multiplier
            if degraded_flags.get(candidate.model_id, False):
                score -= DEGRADED_PENALTY
            if cooldown_flags.get(candidate.model_id, False):
                score -= PROBE_COOLDOWN_PENALTY

            if not candidate.is_pool:
                model = next((m for m in available_models if m.model_id == candidate.model_id), None)
                release_date = _model_release_date(model) if model is not None else None
                release_dt = _parse_date(release_date)
                if release_dt is not None and now - release_dt <= timedelta(days=NEW_MODEL_LOOKBACK_DAYS):
                    score += NEW_MODEL_BONUS

            adjusted.append(
                replace(
                    candidate,
                    dimension_score=score,
                    health_status=health_status,
                    success_bonus=multiplier if multiplier > 1.0 else 1.0,
                    fail_penalty=multiplier if multiplier < 1.0 else 1.0,
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

            if CEILING_SLOTS > 0:
                final_candidates.append(ceiling_model)
                selected_ids.add(ceiling_model.model_id)

            for item in pool_candidates:
                if len(final_candidates) >= 5:
                    break
                if item.model_id in selected_ids:
                    continue
                final_candidates.append(item)
                selected_ids.add(item.model_id)

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
                limit=5,
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
                start_index = 0
                reason = "default overloaded, switched to best available candidate"
            else:
                start_index = candidate_ids.index(default_model_id)
                reason = f"class default selected at index={start_index}"
        elif pool_entries:
            start_index = 0
            reason = "pool hit without default, pick top ranked pool candidate"
        else:
            start_index = _cold_start_index(request, len(final_candidates))
            reason = f"cold start selection index={start_index}"

        if len(final_candidates) < 5 and not preferred and not default_model_id and not pool_entries:
            start_index = max(0, len(final_candidates) - 2)

        alerts: list[str] = []
        if len(final_candidates) < MIN_CANDIDATES_FOR_AUTO:
            alerts.append(f"仅 {len(final_candidates)} 个候选模型可用")

        primary_model = final_candidates[start_index].model_id

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
