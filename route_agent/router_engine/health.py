"""Health management for router_engine."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from route_agent.router_engine.constants import (
    DEGRADED_COOLDOWN_S,
    DEGRADED_QUALITY_MULTIPLIER,
    FAIL_PENALTY_FACTOR,
    PROBE_CONSECUTIVE_SUCCESS_THRESHOLD,
    PROBE_COOLDOWN_S,
    PROBE_GOOD_FEEDBACK_THRESHOLD,
    QUALITY_FAIL_DEGRADED_THRESHOLD,
    SUCCESS_BONUS_FACTOR,
    UNABLE_PROBE_INTERVAL_S,
)
from route_agent.router_engine.storage import RouterStorage


def _parse_db_datetime(value: str | None) -> datetime | None:
    """Execute `_parse_db_datetime`."""
    if not value:
        return None
    try:
        # SQLite datetime('now') format: YYYY-MM-DD HH:MM:SS
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc)


class HealthManager:
    """Tracks quality/execution driven model health states."""

    def __init__(
        self,
        router_storage: RouterStorage,
        probe_callback: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        """Initialize the instance."""
        self._storage = router_storage
        self._probe_callback = probe_callback

    @property
    def probe_callback(self) -> Callable[[str], Awaitable[bool]] | None:
        """Return the registered probe callback, if any."""
        return self._probe_callback

    # ------------------------------------------------------------------
    # Quality feedback
    # ------------------------------------------------------------------

    async def on_quality_good_async(
        self, agent_class: str, model_id: str,
    ) -> tuple[int, int]:
        """Record a quality-good outcome and handle probe-testing recovery."""
        stats = await self._storage.atomic_increment_success_async(agent_class, model_id)

        # If the model is in probe-testing phase, update probe counters.
        availability = await self._storage.get_availability_async(model_id)
        if availability is not None and availability.status == "degraded":
            degraded_since = _parse_db_datetime(availability.degraded_since)
            now = datetime.now(tz=timezone.utc)
            if degraded_since is not None and now - degraded_since >= timedelta(seconds=DEGRADED_COOLDOWN_S):
                result = await self._storage.update_probe_testing_feedback_async(model_id, is_good=True)
                if result is not None:
                    good_fb, consec_succ = result
                    if (
                        good_fb >= PROBE_GOOD_FEEDBACK_THRESHOLD
                        or consec_succ >= PROBE_CONSECUTIVE_SUCCESS_THRESHOLD
                    ):
                        await self._storage.mark_available_async(model_id)

        return stats.consecutive_success, stats.bonus_level

    async def on_quality_fail_async(
        self, agent_class: str, model_id: str,
    ) -> tuple[int, int, int]:
        """Record a quality-fail outcome and potentially trigger degraded state."""
        stats = await self._storage.atomic_increment_fail_async(agent_class, model_id)

        # If the model is in probe-testing phase, reset back to degraded.
        availability = await self._storage.get_availability_async(model_id)
        if availability is not None and availability.status == "degraded":
            degraded_since = _parse_db_datetime(availability.degraded_since)
            now = datetime.now(tz=timezone.utc)
            if degraded_since is not None and now - degraded_since >= timedelta(seconds=DEGRADED_COOLDOWN_S):
                # In probe-testing phase → reset back to degraded with fresh cooldown.
                await self._storage.update_probe_testing_feedback_async(model_id, is_good=False)
                return stats.consecutive_fail, stats.bonus_level, stats.penalty_level

        # Check whether consecutive quality failures should trigger degraded.
        if stats.consecutive_fail >= QUALITY_FAIL_DEGRADED_THRESHOLD:
            await self._storage.mark_degraded_by_quality_async(model_id)

        return stats.consecutive_fail, stats.bonus_level, stats.penalty_level

    # ------------------------------------------------------------------
    # Health modifier for scoring
    # ------------------------------------------------------------------

    def get_health_modifier(
        self,
        agent_class: str,
        model_id: str,
        cost_score: float,
        *,
        is_escalation: bool = False,
        is_degraded: bool = False,
    ) -> tuple[str, float]:
        """Return (health_label, multiplier) for score adjustment.

        When *is_degraded* is True the model is in quality-degraded cooldown
        and receives a fixed multiplier of DEGRADED_QUALITY_MULTIPLIER (0.70).
        """
        if is_degraded:
            return "quality_degraded", DEGRADED_QUALITY_MULTIPLIER

        stats = self._storage.get_stats(agent_class, model_id)
        if stats is None:
            return "healthy", 1.0

        if stats.bonus_level > 0:
            raw_bonus = SUCCESS_BONUS_FACTOR ** stats.bonus_level
            if is_escalation:
                return "bonus", raw_bonus
            effective = 1.0 + (raw_bonus - 1.0) * (1.0 - max(0.0, min(cost_score, 1.0)))
            return "bonus", max(1.0, effective)

        if stats.penalty_level > 0:
            penalty = FAIL_PENALTY_FACTOR ** stats.penalty_level
            return "penalty", penalty

        return "healthy", 1.0

    async def get_health_modifier_async(
        self,
        agent_class: str,
        model_id: str,
        cost_score: float,
        *,
        is_escalation: bool = False,
        is_degraded: bool = False,
    ) -> tuple[str, float]:
        """Execute `get_health_modifier_async`."""
        return await asyncio.to_thread(
            self.get_health_modifier,
            agent_class,
            model_id,
            cost_score,
            is_escalation=is_escalation,
            is_degraded=is_degraded,
        )

    # ------------------------------------------------------------------
    # Execution success / failure
    # ------------------------------------------------------------------

    async def report_exec_failure_async(self, model_id: str) -> str:
        """Transition model availability state on execution failure."""
        return await self._storage.report_exec_failure_transition_async(model_id)

    async def report_exec_success_async(self, model_id: str) -> None:
        """Reset consecutive exec-fail counter on successful execution."""
        await self._storage.reset_exec_fail_counter_async(model_id)

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    def is_available(self, model_id: str) -> tuple[bool, bool, bool]:
        """Return (selectable, is_degraded, is_probe_testing).

        - unable: not selectable
        - degraded (cooldown not elapsed): selectable with quality_degraded multiplier
        - degraded (cooldown elapsed, probe-testing): selectable with probe_testing penalty
        - available with recent probe success: selectable with probe cooldown
        """
        availability = self._storage.get_availability(model_id)
        if availability is None:
            return True, False, False

        now = datetime.now(tz=timezone.utc)

        if availability.status == "unable":
            return False, False, False

        if availability.status == "degraded":
            degraded_since = _parse_db_datetime(availability.degraded_since)
            if degraded_since is not None:
                elapsed = now - degraded_since
                if elapsed >= timedelta(seconds=DEGRADED_COOLDOWN_S):
                    # Probe-testing phase: cooldown has elapsed.
                    return True, False, True
                # Still in cooldown: apply heavy quality_degraded penalty.
                return True, True, False
            return True, True, False

        # Probe cooldown for models recently recovered from unable.
        last_probe_at = _parse_db_datetime(availability.last_probe_at)
        if availability.last_probe_success and last_probe_at is not None:
            if now - last_probe_at < timedelta(seconds=PROBE_COOLDOWN_S):
                return True, False, True

        return True, False, False

    async def is_available_async(self, model_id: str) -> tuple[bool, bool, bool]:
        """Execute `is_available_async`."""
        return await asyncio.to_thread(self.is_available, model_id)

    # ------------------------------------------------------------------
    # Probe loop for unable models
    # ------------------------------------------------------------------

    async def probe_unable_models_async(self) -> list[str]:
        """Probe unable models and recover successful ones."""
        if self._probe_callback is None:
            return []

        recovered: list[str] = []
        model_ids = await self._storage.list_unable_for_probe_async(UNABLE_PROBE_INTERVAL_S)
        for model_id in model_ids:
            success = False
            try:
                success = bool(await self._probe_callback(model_id))
            except Exception:
                success = False

            await self._storage.update_probe_result_async(model_id, success)
            if success:
                recovered.append(model_id)
        return recovered

    async def probe_loop_async(self) -> None:
        """Execute `probe_loop_async`."""
        while True:
            try:
                await self.probe_unable_models_async()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep loop alive; callers should observe metrics/logs outside this module.
                pass
            await asyncio.sleep(UNABLE_PROBE_INTERVAL_S)
