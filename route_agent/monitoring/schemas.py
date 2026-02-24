"""Monitoring data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RouteDecisionEvent:
    source: str
    agent_name: str
    model_used: str | None
    selected_tier: str | None = None
    provider: str | None = None
    routing_reason: str | None = None
    pool_hit: bool | None = None
    pool_class: str | None = None
    analysis_domain: str | None = None
    analysis_complexity: float | None = None
    registry_error_count: int = 0
    skipped_provider_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "agent_name": self.agent_name,
            "model_used": self.model_used,
            "selected_tier": self.selected_tier,
            "provider": self.provider,
            "routing_reason": self.routing_reason,
            "pool_hit": self.pool_hit,
            "pool_class": self.pool_class,
            "analysis_domain": self.analysis_domain,
            "analysis_complexity": self.analysis_complexity,
            "registry_error_count": self.registry_error_count,
            "skipped_provider_count": self.skipped_provider_count,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RouteDecisionEvent":
        return cls(
            source=str(payload.get("source") or "main"),
            agent_name=str(payload.get("agent_name") or "route_agent"),
            model_used=(None if payload.get("model_used") in (None, "") else str(payload.get("model_used"))),
            selected_tier=(None if payload.get("selected_tier") in (None, "") else str(payload.get("selected_tier"))),
            provider=(None if payload.get("provider") in (None, "") else str(payload.get("provider"))),
            routing_reason=(None if payload.get("routing_reason") in (None, "") else str(payload.get("routing_reason"))),
            pool_hit=(None if payload.get("pool_hit") is None else bool(payload.get("pool_hit"))),
            pool_class=(None if payload.get("pool_class") in (None, "") else str(payload.get("pool_class"))),
            analysis_domain=(None if payload.get("analysis_domain") in (None, "") else str(payload.get("analysis_domain"))),
            analysis_complexity=(
                None
                if payload.get("analysis_complexity") is None
                else float(payload.get("analysis_complexity"))
            ),
            registry_error_count=int(payload.get("registry_error_count") or 0),
            skipped_provider_count=int(payload.get("skipped_provider_count") or 0),
            metadata=dict(payload.get("metadata") or {}),
        )
