"""Base rate-limiter protocols and status types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from route_agent.router_engine.schemas import ModelUtilization


class RateLimiter(Protocol):
    async def is_rate_limited_async(self, model_id: str, limits: dict[str, Any]) -> bool: ...

    async def record_request_start_async(self, model_id: str, traffic_type: str = "normal") -> None: ...

    async def record_request_end_async(self, model_id: str, traffic_type: str = "normal") -> None: ...

    async def get_utilization_async(self, model_id: str, limits: dict[str, Any]) -> ModelUtilization: ...

    async def is_escalation_capped_async(self, model_id: str, limits: dict[str, Any]) -> bool: ...

    def mark_limited(self, model_id: str) -> None: ...

    def is_recently_limited(self, model_id: str) -> bool: ...


@dataclass(slots=True)
class RateLimiterStatus:
    mode: str
    fail_strategy: str
    switched_at: str | None = None
    last_error: str | None = None


class NoOpRateLimiter:
    def __init__(self) -> None:
        self.status = RateLimiterStatus(mode="off", fail_strategy="off")

    async def is_rate_limited_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        return False

    async def record_request_start_async(self, model_id: str, traffic_type: str = "normal") -> None:
        return None

    async def record_request_end_async(self, model_id: str, traffic_type: str = "normal") -> None:
        return None

    async def get_utilization_async(self, model_id: str, limits: dict[str, Any]) -> ModelUtilization:
        return ModelUtilization()

    async def is_escalation_capped_async(self, model_id: str, limits: dict[str, Any]) -> bool:
        return False

    def mark_limited(self, model_id: str) -> None:
        return None

    def is_recently_limited(self, model_id: str) -> bool:
        return False

