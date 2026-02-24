"""Base rate-limiter protocols and status types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from route_agent.router_engine.schemas import ModelUtilization


class RateLimiter(Protocol):
    """Represent `RateLimiter`."""
    async def is_rate_limited_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_rate_limited_async`."""
        ...

    async def record_request_start_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_start_async`."""
        ...

    async def record_request_end_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_end_async`."""
        ...

    async def get_utilization_async(self, model_id: str, limits: dict[str, Any]) -> ModelUtilization:
        """Execute `get_utilization_async`."""
        ...

    async def is_escalation_capped_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_escalation_capped_async`."""
        ...

    def mark_limited(self, model_id: str) -> None:
        """Execute `mark_limited`."""
        ...

    def is_recently_limited(self, model_id: str) -> bool:
        """Execute `is_recently_limited`."""
        ...


@dataclass(slots=True)
class RateLimiterStatus:
    """Represent `RateLimiterStatus`."""
    mode: str
    fail_strategy: str
    switched_at: str | None = None
    last_error: str | None = None


class NoOpRateLimiter:
    """Represent `NoOpRateLimiter`."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self.status = RateLimiterStatus(mode="off", fail_strategy="off")

    async def is_rate_limited_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_rate_limited_async`."""
        return False

    async def record_request_start_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_start_async`."""
        return None

    async def record_request_end_async(self, model_id: str, traffic_type: str = "normal") -> None:
        """Execute `record_request_end_async`."""
        return None

    async def get_utilization_async(self, model_id: str, limits: dict[str, Any]) -> ModelUtilization:
        """Execute `get_utilization_async`."""
        return ModelUtilization()

    async def is_escalation_capped_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        """Execute `is_escalation_capped_async`."""
        return False

    def mark_limited(self, model_id: str) -> None:
        """Execute `mark_limited`."""
        return None

    def is_recently_limited(self, model_id: str) -> bool:
        """Execute `is_recently_limited`."""
        return False
