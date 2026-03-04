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

    def _build_escalation_targets(self) -> list[str]:
        """Build escalation target list ordered by descending strength."""
        targets: list[str] = []
        for idx in range(self._current_index - 1, -1, -1):
            targets.append(self._decision.candidates[idx].model_id)
        if len(targets) < MAX_ESCALATION_ATTEMPTS:
            breakthrough = self._breakthrough_candidate()
            if breakthrough and breakthrough not in {t for t in targets}:
                targets.append(breakthrough)
        return targets[:MAX_ESCALATION_ATTEMPTS]

    def _is_target_over_threshold(self, peak: float, priority: str) -> bool:
        """Check whether peak utilization exceeds the threshold for *priority*."""
        if priority == "normal":
            return peak >= max(RPM_UTIL_HIGH, CONC_UTIL_HIGH)
        if priority == "elevated":
            return peak >= ESCALATION_UTIL_CEILING
        return False

    def _cheapest_per_provider(self) -> dict[str, str]:
        """Return {provider: model_id} mapping with cheapest model per provider."""
        by_provider: dict[str, tuple[float, str]] = {}
        for m in self._available_models:
            price = m.pricing.get("input", float("inf"))
            if m.provider not in by_provider or price < by_provider[m.provider][0]:
                by_provider[m.provider] = (price, m.model_id)
        return {prov: mid for prov, (_, mid) in by_provider.items()}

    def _provider_for_model(self, model_id: str) -> str | None:
        """Return the provider for *model_id*, or ``None`` if unknown."""
        model = next((m for m in self._available_models if m.model_id == model_id), None)
        if model is not None:
            return model.provider
        cand = next((c for c in self._decision.candidates if c.model_id == model_id), None)
        return cand.provider if cand is not None else None

    async def _probe_providers_async(self) -> set[str]:
        """Probe cheapest model per provider, return set of unreachable providers."""
        callback = self._health_manager.probe_callback
        if callback is None:
            return set()
        cheapest = self._cheapest_per_provider()
        tasks = {
            prov: asyncio.create_task(callback(mid))
            for prov, mid in cheapest.items()
        }
        unreachable: set[str] = set()
        for prov, task in tasks.items():
            try:
                result = await task
                if not result:
                    unreachable.add(prov)
            except Exception:
                unreachable.add(prov)
        return unreachable

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
        if base.action not in {"escalate", "escalate_breakthrough"}:
            return base

        last = self._attempts[-1] if self._attempts else None
        is_exec_fail = last is not None and last.failure_type != "quality"

        unreachable_providers: set[str] = set()
        if is_exec_fail:
            unreachable_providers = await self._probe_providers_async()

        candidate_ids = {c.model_id for c in self._decision.candidates}
        targets = self._build_escalation_targets()

        for target in targets:
            provider = self._provider_for_model(target)
            if provider and provider in unreachable_providers:
                continue
            limits = self._limits_for_model(target)
            util = await self._rate_limiter.get_utilization_async(target, limits)
            if util.is_limited:
                continue
            if self._is_target_over_threshold(util.peak_ratio, priority):
                continue
            if await self._rate_limiter.is_escalation_capped_async(target, limits):
                continue
            action = "escalate" if target in candidate_ids else "escalate_breakthrough"
            return replace(base, action=action, next_model=target)

        if is_exec_fail:
            return replace(base, action="alert_escalation_unavailable", next_model=None)

        orig_limits = self._limits_for_model(current_model_id)
        orig_util = await self._rate_limiter.get_utilization_async(current_model_id, orig_limits)
        if not orig_util.is_limited:
            return replace(base, action="retry", next_model=current_model_id)

        if await self._wait_for_model_async(current_model_id):
            return replace(base, action="retry", next_model=current_model_id)

        return replace(base, action="alert_escalation_unavailable", next_model=None)
