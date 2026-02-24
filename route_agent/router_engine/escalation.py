"""Escalation state machine for router_engine."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.constants import (
    CONC_UTIL_HIGH,
    ESCALATION_UTIL_CEILING,
    RPM_UTIL_HIGH,
)
from route_agent.router_engine.health import HealthManager
from route_agent.router_engine.rate_limiters import RateLimiter
from route_agent.router_engine.schemas import EscalationResult, ExecutionAttempt, RouteDecision


def _approx_raw_capability_score(model: ModelMetadata) -> float:
    """Execute `_approx_raw_capability_score`."""
    values: list[float] = []
    for key, value in model.capabilities.items():
        if key.startswith("_"):
            continue
        try:
            values.append(float(value) / 100.0)
        except (TypeError, ValueError):
            continue
    if not values:
        return 0.0
    return max(values)


class EscalationManager:
    """Track execution attempts and decide retry/escalation actions."""

    def __init__(
        self,
        decision: RouteDecision,
        rate_limiter: RateLimiter,
        health_manager: HealthManager,
        available_models: list[ModelMetadata],
    ) -> None:
        """Initialize the instance."""
        self._decision = decision
        self._rate_limiter = rate_limiter
        self._health_manager = health_manager
        self._available_models = available_models
        self._attempts: list[ExecutionAttempt] = []
        self._current_index = decision.start_index

    def record_attempt(self, attempt: ExecutionAttempt) -> None:
        """Execute `record_attempt`."""
        self._attempts.append(attempt)
        for idx, candidate in enumerate(self._decision.candidates):
            if candidate.model_id == attempt.model_id:
                self._current_index = idx
                break

    def _current_model(self) -> str | None:
        """Execute `_current_model`."""
        if not self._decision.candidates:
            return None
        if self._current_index < 0 or self._current_index >= len(self._decision.candidates):
            return None
        return self._decision.candidates[self._current_index].model_id

    def _quality_fail_count_for_model(self, model_id: str) -> int:
        """Execute `_quality_fail_count_for_model`."""
        count = 0
        for attempt in self._attempts:
            if attempt.model_id == model_id and not attempt.success and attempt.failure_type == "quality":
                count += 1
        return count

    def _breakthrough_candidate(self) -> str | None:
        """Execute `_breakthrough_candidate`."""
        if not self._decision.candidates:
            return None

        top_raw = max(candidate.raw_dimension_score for candidate in self._decision.candidates)
        used_ids = {candidate.model_id for candidate in self._decision.candidates}

        candidates = [
            model
            for model in self._available_models
            if model.model_id not in used_ids and _approx_raw_capability_score(model) > top_raw
        ]
        if not candidates:
            return None

        candidates.sort(key=_approx_raw_capability_score, reverse=True)
        return candidates[0].model_id

    def next_action(self, priority: str = "normal") -> EscalationResult:
        """Execute `next_action`."""
        current_model = self._current_model()
        if current_model is None:
            return EscalationResult(
                action="alert_escalation_unavailable",
                next_model=None,
                previous_attempts=tuple(self._attempts),
                context_for_next=None,
                priority=priority,
            )

        if not self._attempts:
            return EscalationResult(
                action="retry",
                next_model=current_model,
                previous_attempts=tuple(self._attempts),
                context_for_next=None,
                priority=priority,
            )

        last = self._attempts[-1]
        context_for_next = last.output_snippet
        if last.success:
            return EscalationResult(
                action="retry",
                next_model=current_model,
                previous_attempts=tuple(self._attempts),
                context_for_next=context_for_next,
                priority=priority,
            )

        if last.failure_type == "quality":
            quality_fails = self._quality_fail_count_for_model(current_model)
            if quality_fails <= 1:
                return EscalationResult(
                    action="retry",
                    next_model=current_model,
                    previous_attempts=tuple(self._attempts),
                    context_for_next=context_for_next,
                    priority=priority,
                )

        next_index = self._current_index - 1
        if next_index >= 0:
            next_model = self._decision.candidates[next_index].model_id
            return EscalationResult(
                action="escalate",
                next_model=next_model,
                previous_attempts=tuple(self._attempts),
                context_for_next=context_for_next,
                priority=priority,
            )

        breakthrough = self._breakthrough_candidate()
        if breakthrough:
            return EscalationResult(
                action="escalate_breakthrough",
                next_model=breakthrough,
                previous_attempts=tuple(self._attempts),
                context_for_next=context_for_next,
                priority=priority,
            )

        return EscalationResult(
            action="alert_top_failed",
            next_model=None,
            previous_attempts=tuple(self._attempts),
            context_for_next=context_for_next,
            priority=priority,
        )

    def _limits_for_model(self, model_id: str) -> dict[str, Any]:
        """Execute `_limits_for_model`."""
        model = next((m for m in self._available_models if m.model_id == model_id), None)
        return model.limits if model is not None else {}

    async def _find_uncapped_alternative(self, excluded: set[str]) -> str | None:
        """Execute `_find_uncapped_alternative`."""
        for candidate in self._decision.candidates:
            if candidate.model_id in excluded:
                continue
            limits = self._limits_for_model(candidate.model_id)
            util = await self._rate_limiter.get_utilization_async(candidate.model_id, limits)
            if util.is_limited:
                continue
            if await self._rate_limiter.is_escalation_capped_async(candidate.model_id, limits):
                continue
            return candidate.model_id
        return None

    async def escalate_with_overload_check_async(
        self,
        current_model_id: str,
        priority: str,
        decision: RouteDecision | None = None,
    ) -> EscalationResult:
        """Execute `escalate_with_overload_check_async`."""
        _ = decision  # currently unused, kept for API compatibility.
        base = self.next_action(priority=priority)
        if base.action not in {"escalate", "escalate_breakthrough"}:
            return base

        target = base.next_model
        if target is None:
            return replace(base, action="alert_escalation_unavailable")

        limits = self._limits_for_model(target)
        util = await self._rate_limiter.get_utilization_async(target, limits)
        if util.is_limited:
            return replace(base, action="alert_escalation_unavailable", next_model=None)

        peak = max(util.rpm_ratio, util.conc_ratio)
        if priority == "normal":
            if peak >= max(RPM_UTIL_HIGH, CONC_UTIL_HIGH):
                return replace(base, action="retry", next_model=current_model_id)
        elif priority == "elevated":
            if peak >= ESCALATION_UTIL_CEILING:
                return replace(base, action="alert_escalation_unavailable", next_model=None)

        if await self._rate_limiter.is_escalation_capped_async(target, limits):
            alternative = await self._find_uncapped_alternative(excluded={target, current_model_id})
            if alternative is not None:
                return replace(base, next_model=alternative)
            if priority == "normal":
                return replace(base, action="retry", next_model=current_model_id)
            return replace(base, action="alert_escalation_unavailable", next_model=None)

        return base
