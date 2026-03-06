"""Escalation state machine for router_engine."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.constants import (
    CONC_UTIL_HIGH,
    ESCALATION_UTIL_CEILING,
    ESCALATION_WAIT_BASE_DELAY,
    ESCALATION_WAIT_MAX_ATTEMPTS,
    ESCALATION_WAIT_MAX_TOTAL,
    MAX_ESCALATION_ATTEMPTS,
    RPM_UTIL_HIGH,
)
from route_agent.router_engine.health import HealthManager
from route_agent.router_engine.rate_limiters import RateLimiter
from route_agent.router_engine.schemas import EscalationResult, ExecutionAttempt, RouteDecision


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

    def _build_escalation_targets(self) -> list[str]:
        """Build escalation target list ordered by descending strength."""
        targets: list[str] = []
        for idx in range(self._current_index - 1, -1, -1):
            targets.append(self._decision.candidates[idx].model_id)
        return targets[:MAX_ESCALATION_ATTEMPTS]

    def _is_target_over_threshold(self, peak: float, priority: str) -> bool:
        """Check whether peak utilization exceeds the threshold for *priority*."""
        if priority == "normal":
            return peak >= max(RPM_UTIL_HIGH, CONC_UTIL_HIGH)
        if priority == "elevated":
            return peak >= ESCALATION_UTIL_CEILING
        return False

    async def _wait_for_model_async(self, model_id: str) -> bool:
        """Wait with exponential back-off until *model_id* is no longer limited.

        Returns ``True`` if the model became available within the budget.
        """
        limits = self._limits_for_model(model_id)
        delay = ESCALATION_WAIT_BASE_DELAY
        total = 0.0
        for _ in range(ESCALATION_WAIT_MAX_ATTEMPTS):
            if total + delay > ESCALATION_WAIT_MAX_TOTAL:
                delay = max(ESCALATION_WAIT_MAX_TOTAL - total, 0)
                if delay <= 0:
                    break
            await asyncio.sleep(delay)
            total += delay
            util = await self._rate_limiter.get_utilization_async(model_id, limits)
            if not util.is_limited:
                return True
            delay *= 2
        return False

    async def escalate_with_overload_check_async(
        self,
        current_model_id: str,
        priority: str,
        decision: RouteDecision | None = None,
    ) -> EscalationResult:
        """Execute `escalate_with_overload_check_async`."""
        _ = decision  # currently unused, kept for API compatibility.
        base = self.next_action(priority=priority)
        if base.action != "escalate":
            return base

        targets = self._build_escalation_targets()

        for target in targets:
            selectable, _, _ = self._health_manager.is_available(target)
            if not selectable:
                continue
            limits = self._limits_for_model(target)
            util = await self._rate_limiter.get_utilization_async(target, limits)
            if util.is_limited:
                continue
            if self._is_target_over_threshold(util.peak_ratio, priority):
                continue
            if await self._rate_limiter.is_escalation_capped_async(target, limits):
                continue
            return replace(base, next_model=target)

        return replace(base, action="alert_escalation_unavailable", next_model=None)
