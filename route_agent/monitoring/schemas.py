"""Monitoring data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_RUNNING_STATUS = "running"
_EXECUTION_STATUSES = frozenset({"running", "success", "failed", "timeout", "cancelled"})


def _as_optional_text(value: Any) -> str | None:
    """Normalize a value to a nullable non-empty string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_execution_status(value: Any, *, default: str) -> str:
    """Normalize execution status with a safe fallback."""
    raw = (_as_optional_text(value) or default).lower()
    return raw if raw in _EXECUTION_STATUSES else default


@dataclass(frozen=True)
class RouteDecisionEvent:
    """Represent `RouteDecisionEvent`."""
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
        """Execute `to_dict`."""
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
        """Execute `from_dict`."""
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


@dataclass(frozen=True)
class ExecutionStartEvent:
    """Represent an execution-start event for one agent."""

    source: str
    agent_name: str
    execution_id: str | None = None
    request_id: str | None = None
    model_used: str | None = None
    provider: str | None = None
    status: str = _RUNNING_STATUS
    started_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Execute `to_dict`."""
        return {
            "source": self.source,
            "agent_name": self.agent_name,
            "execution_id": self.execution_id,
            "request_id": self.request_id,
            "model_used": self.model_used,
            "provider": self.provider,
            "status": _coerce_execution_status(self.status, default=_RUNNING_STATUS),
            "started_at": self.started_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionStartEvent":
        """Execute `from_dict`."""
        return cls(
            source=str(payload.get("source") or "test"),
            agent_name=str(payload.get("agent_name") or "route_agent"),
            execution_id=_as_optional_text(payload.get("execution_id")),
            request_id=_as_optional_text(payload.get("request_id")),
            model_used=_as_optional_text(payload.get("model_used")),
            provider=_as_optional_text(payload.get("provider")),
            status=_coerce_execution_status(payload.get("status"), default=_RUNNING_STATUS),
            started_at=_as_optional_text(payload.get("started_at")),
            metadata=dict(payload.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ExecutionEndEvent:
    """Represent an execution-end event for one agent."""

    execution_id: str
    status: str = "success"
    ended_at: str | None = None
    duration_ms: float | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Execute `to_dict`."""
        return {
            "execution_id": self.execution_id,
            "status": _coerce_execution_status(self.status, default="success"),
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExecutionEndEvent":
        """Execute `from_dict`."""
        execution_id = _as_optional_text(payload.get("execution_id"))
        if not execution_id:
            raise ValueError("execution_id is required for ExecutionEndEvent")

        duration_ms: float | None
        try:
            duration_ms = (
                None if payload.get("duration_ms") in (None, "") else float(payload.get("duration_ms"))
            )
        except (TypeError, ValueError):
            duration_ms = None

        return cls(
            execution_id=execution_id,
            status=_coerce_execution_status(payload.get("status"), default="success"),
            ended_at=_as_optional_text(payload.get("ended_at")),
            duration_ms=duration_ms,
            error_message=_as_optional_text(payload.get("error_message")),
            metadata=dict(payload.get("metadata") or {}),
        )
