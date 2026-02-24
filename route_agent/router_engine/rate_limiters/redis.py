"""Redis-backed rate limiter implementation."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from route_agent.router_engine.constants import (
    ESCALATION_CONC_RATIO,
    RECENT_LIMITED_TTL_S,
    REDIS_PREFIX_CONC,
    REDIS_PREFIX_RPD,
    REDIS_PREFIX_RPM,
    UTIL_CACHE_TTL_MS,
)
from route_agent.router_engine.rate_limiters.base import RateLimiterStatus
from route_agent.router_engine.schemas import ModelUtilization


class RedisRateLimiter:
    """Represent `RedisRateLimiter`."""
    def __init__(self, redis_url: str) -> None:
        """Initialize the instance."""
        try:
            from redis.asyncio import Redis
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("redis package is required for RedisRateLimiter") from exc

        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self.status = RateLimiterStatus(mode="redis", fail_strategy="degrade")
        self._util_cache: dict[str, tuple[float, ModelUtilization]] = {}
        self._util_cache_lock = asyncio.Lock()
        self._limited_until: dict[str, float] = {}

    @staticmethod
    def _limit_value(limits: dict[str, Any], key: str, default: int) -> int:
        """Execute `_limit_value`."""
        try:
            value = int(limits.get(key) or default)
            return max(1, value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any) -> int:
        """Execute `_safe_int`."""
        try:
            if value is None:
                return 0
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _key_rpm(model_id: str) -> str:
        """Execute `_key_rpm`."""
        return f"{REDIS_PREFIX_RPM}{model_id}"

    @staticmethod
    def _key_rpd(model_id: str) -> str:
        """Execute `_key_rpd`."""
        return f"{REDIS_PREFIX_RPD}{model_id}"

    @staticmethod
    def _key_conc(model_id: str, traffic_type: str) -> str:
        """Execute `_key_conc`."""
        suffix = "esc" if traffic_type == "escalation" else "normal"
        return f"{REDIS_PREFIX_CONC}{suffix}:{model_id}"

    def _cleanup_limited(self) -> None:
        """Execute `_cleanup_limited`."""
        now = time.monotonic()
        stale = [key for key, value in self._limited_until.items() if now >= value]
        for key in stale:
            self._limited_until.pop(key, None)

    async def is_rate_limited_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_rate_limited_async`."""
        now = time.time()
        rpm_limit = self._limit_value(limits, "max_requests_per_minute", 10)
        rpd_limit = self._limit_value(limits, "max_requests_per_day", 1000)
        conc_limit = self._limit_value(limits, "max_concurrency", 5)

        rpm_key = self._key_rpm(model_id)
        rpd_key = self._key_rpd(model_id)
        conc_n_key = self._key_conc(model_id, "normal")
        conc_e_key = self._key_conc(model_id, "escalation")

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rpm_key, 0, now - 60.0)
        pipe.zcard(rpm_key)
        pipe.zremrangebyscore(rpd_key, 0, now - 86400.0)
        pipe.zcard(rpd_key)
        pipe.get(conc_n_key)
        pipe.get(conc_e_key)
        result = await pipe.execute()

        rpm_count = self._safe_int(result[1])
        rpd_count = self._safe_int(result[3])
        conc_total = self._safe_int(result[4]) + self._safe_int(result[5])

        return rpm_count >= rpm_limit or rpd_count >= rpd_limit or conc_total >= conc_limit

    async def record_request_start_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_start_async`."""
        now = time.time()
        score = float(now)
        member = f"{score}:{time.monotonic()}"

        rpm_key = self._key_rpm(model_id)
        rpd_key = self._key_rpd(model_id)
        conc_key = self._key_conc(model_id, traffic_type)

        pipe = self._redis.pipeline()
        pipe.zadd(rpm_key, {member: score})
        pipe.expire(rpm_key, 120)
        pipe.zadd(rpd_key, {member: score})
        pipe.expire(rpd_key, 90000)
        pipe.incr(conc_key)
        pipe.expire(conc_key, 300)
        await pipe.execute()

    async def record_request_end_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_end_async`."""
        conc_key = self._key_conc(model_id, traffic_type)
        await self._redis.decr(conc_key)

    async def get_utilization_async(self, model_id: str, limits: dict[str, Any]) -> ModelUtilization:
        """Execute `get_utilization_async`."""
        now_mono = time.monotonic()
        async with self._util_cache_lock:
            cached = self._util_cache.get(model_id)
            if cached is not None and now_mono - cached[0] <= (UTIL_CACHE_TTL_MS / 1000.0):
                return cached[1]

        now = time.time()
        rpm_limit = self._limit_value(limits, "max_requests_per_minute", 10)
        conc_limit = self._limit_value(limits, "max_concurrency", 5)

        rpm_key = self._key_rpm(model_id)
        conc_n_key = self._key_conc(model_id, "normal")
        conc_e_key = self._key_conc(model_id, "escalation")

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rpm_key, 0, now - 60.0)
        pipe.zcard(rpm_key)
        pipe.get(conc_n_key)
        pipe.get(conc_e_key)
        result = await pipe.execute()

        rpm_count = self._safe_int(result[1])
        normal = self._safe_int(result[2])
        esc = self._safe_int(result[3])
        total = normal + esc

        esc_cap = max(1, int(conc_limit * ESCALATION_CONC_RATIO))
        util = ModelUtilization(
            rpm_ratio=min(rpm_count / rpm_limit, 1.0),
            conc_ratio=min(total / conc_limit, 1.0),
            normal_conc_ratio=min(normal / conc_limit, 1.0),
            escalation_conc_ratio=min(esc / conc_limit, 1.0),
            escalation_capped=esc >= esc_cap,
            latency_ratio=0.0,
            is_limited=self.is_recently_limited(model_id),
        )

        async with self._util_cache_lock:
            self._util_cache[model_id] = (now_mono, util)

        return util

    async def is_escalation_capped_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_escalation_capped_async`."""
        conc_limit = self._limit_value(limits, "max_concurrency", 5)
        esc_cap = max(1, int(conc_limit * ESCALATION_CONC_RATIO))
        esc = self._safe_int(await self._redis.get(self._key_conc(model_id, "escalation")))
        return esc >= esc_cap

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

