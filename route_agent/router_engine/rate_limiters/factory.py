"""Factory for selecting rate-limiter implementation."""

from __future__ import annotations

from datetime import datetime, timezone

from route_agent.router_engine.rate_limiters.base import NoOpRateLimiter, RateLimiter, RateLimiterStatus
from route_agent.router_engine.rate_limiters.inmemory import InMemoryRateLimiter
from route_agent.router_engine.rate_limiters.redis import RedisRateLimiter


def _status_time() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def create_rate_limiter(
    redis_url: str | None,
    *,
    mode: str = "auto",
    fail_strategy: str = "degrade",
) -> RateLimiter:
    normalized_mode = (mode or "auto").strip().lower()
    normalized_strategy = (fail_strategy or "degrade").strip().lower()

    if normalized_mode == "off":
        return NoOpRateLimiter()

    if normalized_mode == "inmemory":
        limiter = InMemoryRateLimiter()
        limiter.status = RateLimiterStatus(mode="inmemory", fail_strategy=normalized_strategy)
        return limiter

    if normalized_mode == "redis":
        if not redis_url:
            raise RuntimeError("rate_limit_mode=redis requires redis_url")
        limiter = RedisRateLimiter(redis_url)
        limiter.status = RateLimiterStatus(mode="redis", fail_strategy=normalized_strategy)
        return limiter

    if redis_url:
        try:
            limiter = RedisRateLimiter(redis_url)
            limiter.status = RateLimiterStatus(mode="redis", fail_strategy=normalized_strategy)
            return limiter
        except Exception as exc:
            if normalized_strategy == "fail_fast":
                raise RuntimeError(f"redis unavailable in auto mode: {exc}") from exc

            limiter = InMemoryRateLimiter()
            limiter.status = RateLimiterStatus(
                mode="inmemory",
                fail_strategy=normalized_strategy,
                switched_at=_status_time(),
                last_error=str(exc),
            )
            return limiter

    if normalized_strategy == "fail_fast":
        raise RuntimeError("redis_url is required when RATE_LIMIT_FAIL_STRATEGY=fail_fast")

    limiter = InMemoryRateLimiter()
    limiter.status = RateLimiterStatus(
        mode="inmemory",
        fail_strategy=normalized_strategy,
        switched_at=_status_time(),
        last_error="redis_url not configured",
    )
    return limiter

