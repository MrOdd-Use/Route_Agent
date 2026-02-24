"""In-memory rate limiter implementation."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

from route_agent.router_engine.constants import ESCALATION_CONC_RATIO, RECENT_LIMITED_TTL_S
from route_agent.router_engine.rate_limiters.base import RateLimiterStatus
from route_agent.router_engine.schemas import ModelUtilization


class InMemoryRateLimiter:
    """Represent `InMemoryRateLimiter`."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self.status = RateLimiterStatus(mode="inmemory", fail_strategy="degrade")
        self._rpm: dict[str, deque[float]] = defaultdict(deque)
        self._rpd: dict[str, deque[float]] = defaultdict(deque)
        self._conc: dict[str, dict[str, int]] = defaultdict(lambda: {"normal": 0, "escalation": 0})
        self._limited_until: dict[str, float] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _limit_value(limits: dict[str, Any], key: str, default: int) -> int:
        """Execute `_limit_value`."""
        try:
            value = int(limits.get(key) or default)
            return max(1, value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _cleanup_window(values: deque[float], window_s: float, now: float) -> None:
        """Execute `_cleanup_window`."""
        while values and (now - values[0]) > window_s:
            values.popleft()

    def _cleanup_limited(self) -> None:
        """Execute `_cleanup_limited`."""
        now = time.monotonic()
        stale = [k for k, v in self._limited_until.items() if now >= v]
        for key in stale:
            self._limited_until.pop(key, None)

    async def is_rate_limited_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_rate_limited_async`."""
        async with self._lock:
            now = time.monotonic()
            rpm_limit = self._limit_value(limits, "max_requests_per_minute", 10)
            rpd_limit = self._limit_value(limits, "max_requests_per_day", 1000)
            conc_limit = self._limit_value(limits, "max_concurrency", 5)

            rpm_q = self._rpm[model_id]
            rpd_q = self._rpd[model_id]
            self._cleanup_window(rpm_q, 60.0, now)
            self._cleanup_window(rpd_q, 86400.0, now)

            conc_state = self._conc[model_id]
            total_conc = conc_state["normal"] + conc_state["escalation"]

            return len(rpm_q) >= rpm_limit or len(rpd_q) >= rpd_limit or total_conc >= conc_limit

    async def record_request_start_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_start_async`."""
        key = "escalation" if traffic_type == "escalation" else "normal"
        async with self._lock:
            now = time.monotonic()
            rpm_q = self._rpm[model_id]
            rpd_q = self._rpd[model_id]
            self._cleanup_window(rpm_q, 60.0, now)
            self._cleanup_window(rpd_q, 86400.0, now)
            rpm_q.append(now)
            rpd_q.append(now)
            self._conc[model_id][key] += 1

    async def record_request_end_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_end_async`."""
        key = "escalation" if traffic_type == "escalation" else "normal"
        async with self._lock:
            state = self._conc[model_id]
            state[key] = max(0, state[key] - 1)

    async def get_utilization_async(self, model_id: str, limits: dict[str, Any]) -> ModelUtilization:
        """Execute `get_utilization_async`."""
        async with self._lock:
            now = time.monotonic()
            rpm_limit = self._limit_value(limits, "max_requests_per_minute", 10)
            conc_limit = self._limit_value(limits, "max_concurrency", 5)

            rpm_q = self._rpm[model_id]
            self._cleanup_window(rpm_q, 60.0, now)
            state = self._conc[model_id]
            normal = state["normal"]
            esc = state["escalation"]
            total = normal + esc
            esc_cap = max(1, int(conc_limit * ESCALATION_CONC_RATIO))

            return ModelUtilization(
                rpm_ratio=min(len(rpm_q) / rpm_limit, 1.0),
                conc_ratio=min(total / conc_limit, 1.0),
                normal_conc_ratio=min(normal / conc_limit, 1.0),
                escalation_conc_ratio=min(esc / conc_limit, 1.0),
                escalation_capped=esc >= esc_cap,
                latency_ratio=0.0,
                is_limited=self.is_recently_limited(model_id),
            )

    async def is_escalation_capped_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_escalation_capped_async`."""
        async with self._lock:
            conc_limit = self._limit_value(limits, "max_concurrency", 5)
            esc_cap = max(1, int(conc_limit * ESCALATION_CONC_RATIO))
            return self._conc[model_id]["escalation"] >= esc_cap

    def mark_limited(self, model_id: str) -> None:
        """Execute `mark_limited`."""
        self._limited_until[model_id] = time.monotonic() + RECENT_LIMITED_TTL_S

    def is_recently_limited(self, model_id: str) -> bool:
        """Execute `is_recently_limited`."""
        self._cleanup_limited()
        until = self._limited_until.get(model_id)
        if until is None:
            return False
        return time.monotonic() < until

