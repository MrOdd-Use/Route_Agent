"""Canonical request and runtime option models for the application layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
import uuid

from route_agent.router_engine import RouteConstraints, RouteRequest
from route_agent.task_analyzer.schemas import TaskAnalysisResult


def _as_float(value: Any) -> float | None:
    """Convert a loose value into a float when possible."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    """Convert a loose value into an integer when possible."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    """Normalize a loose value into a non-empty string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_model_list(value: Any) -> tuple[str, ...]:
    """Normalize model identifiers into a tuple of non-empty strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(item for item in (_as_text(part) for part in value) if item)
    return ()


@dataclass(frozen=True)
class RouteAgentConstraints:
    """Normalized routing constraints accepted by the application layer."""

    max_cost: float | None = None
    preferred_model: str | None = None
    exclude_models: tuple[str, ...] = ()
    require_provider: str | None = None
    estimated_input_tokens: int | None = None

    def __post_init__(self) -> None:
        """Normalize loose constructor values into stable constraint fields."""
        object.__setattr__(self, "max_cost", _as_float(self.max_cost))
        object.__setattr__(self, "preferred_model", _as_text(self.preferred_model))
        object.__setattr__(self, "exclude_models", _as_model_list(self.exclude_models))
        object.__setattr__(self, "require_provider", _as_text(self.require_provider))
        object.__setattr__(self, "estimated_input_tokens", _as_int(self.estimated_input_tokens))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> RouteAgentConstraints:
        """Build normalized constraints from a loose mapping payload."""
        if payload is None:
            return cls()
        if isinstance(payload, cls):
            return payload

        return cls(
            max_cost=_as_float(payload.get("max_cost")),
            preferred_model=_as_text(payload.get("preferred_model")),
            exclude_models=_as_model_list(payload.get("exclude_models")),
            require_provider=_as_text(payload.get("require_provider")),
            estimated_input_tokens=_as_int(payload.get("estimated_input_tokens")),
        )

    def to_route_constraints(self) -> RouteConstraints:
        """Translate normalized constraints into router-engine constraints."""
        return RouteConstraints(
            max_cost=self.max_cost,
            preferred_model=self.preferred_model,
            exclude_models=self.exclude_models,
            require_provider=self.require_provider,
            estimated_input_tokens=self.estimated_input_tokens,
        )


@dataclass(frozen=True)
class RouteAgentRequest:
    """Normalized application request for one route decision."""

    task: str
    agent_name: str = "route_agent"
    request_id: str | None = None
    system_prompt: str = ""
    agent_class: str | None = None
    constraints: RouteAgentConstraints = field(default_factory=RouteAgentConstraints)

    def __post_init__(self) -> None:
        """Normalize request fields and ensure required values are present."""
        task = str(self.task or "").strip()
        if not task:
            raise ValueError("request.task is required")

        constraints = (
            self.constraints
            if isinstance(self.constraints, RouteAgentConstraints)
            else RouteAgentConstraints.from_mapping(self.constraints)
        )
        object.__setattr__(self, "task", task)
        object.__setattr__(self, "agent_name", _as_text(self.agent_name) or "route_agent")
        object.__setattr__(self, "request_id", _as_text(self.request_id) or str(uuid.uuid4()))
        object.__setattr__(self, "system_prompt", _as_text(self.system_prompt) or "")
        object.__setattr__(self, "agent_class", _as_text(self.agent_class))
        object.__setattr__(self, "constraints", constraints)

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any],
        *,
        default_agent_name: str = "route_agent",
    ) -> RouteAgentRequest:
        """Build a normalized request from CLI/API style payloads."""
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise TypeError("request must be a mapping or RouteAgentRequest")

        constraints_payload = payload.get("constraints")
        if isinstance(constraints_payload, RouteAgentConstraints):
            constraints = constraints_payload
        elif isinstance(constraints_payload, Mapping) or constraints_payload is None:
            constraints = RouteAgentConstraints.from_mapping(constraints_payload)
        else:
            constraints = RouteAgentConstraints()

        return cls(
            task=str(payload.get("task") or ""),
            agent_name=_as_text(payload.get("agent_name")) or _as_text(default_agent_name) or "route_agent",
            request_id=_as_text(payload.get("request_id")),
            system_prompt=_as_text(payload.get("system_prompt")) or "",
            agent_class=_as_text(payload.get("agent_class")),
            constraints=constraints,
        )

    def to_route_request(
        self,
        *,
        analysis_result: TaskAnalysisResult,
        record_id: int | None,
    ) -> RouteRequest:
        """Translate the normalized request into a router-engine request."""
        return RouteRequest(
            agent_name=self.agent_name,
            agent_class=self.agent_class,
            request_id=self.request_id,
            system_prompt=self.system_prompt,
            task_prompt=self.task,
            analysis=analysis_result,
            record_id=record_id,
            constraints=self.constraints.to_route_constraints(),
        )


@dataclass(frozen=True)
class RouteAgentRunOptions:
    """Execution options shared by CLI, API, and direct service calls."""

    limit: int = 8
    include_ollama: bool = False
    min_total_threshold: int = 5
    load_env_file: bool = True
    db_backend: str = "sqlite"
    sqlite_path: str | None = None
    postgres_dsn: str | None = None
    sync_interval_days: int = 30
    force_registry_sync: bool = False
    keep_registry_history: int = 2
    default_agent_name: str = "route_agent"
    redis_url: str | None = None
    router_db_path: str | None = None
    rate_limit_mode: str = "auto"
    rate_limit_fail_strategy: str = "degrade"
    analysis_db_path: str | None = None

    def __post_init__(self) -> None:
        """Normalize numeric and string options after construction."""
        object.__setattr__(self, "limit", max(1, int(self.limit)))
        object.__setattr__(self, "min_total_threshold", max(1, int(self.min_total_threshold)))
        object.__setattr__(self, "sync_interval_days", max(1, int(self.sync_interval_days)))
        object.__setattr__(self, "keep_registry_history", max(1, int(self.keep_registry_history)))
        object.__setattr__(self, "db_backend", (_as_text(self.db_backend) or "sqlite").lower())
        object.__setattr__(self, "default_agent_name", _as_text(self.default_agent_name) or "route_agent")
        object.__setattr__(self, "sqlite_path", _as_text(self.sqlite_path))
        object.__setattr__(self, "postgres_dsn", _as_text(self.postgres_dsn))
        object.__setattr__(self, "redis_url", _as_text(self.redis_url))
        object.__setattr__(self, "router_db_path", _as_text(self.router_db_path))
        object.__setattr__(self, "rate_limit_mode", (_as_text(self.rate_limit_mode) or "auto").lower())
        object.__setattr__(
            self,
            "rate_limit_fail_strategy",
            (_as_text(self.rate_limit_fail_strategy) or "degrade").lower(),
        )
        object.__setattr__(self, "analysis_db_path", _as_text(self.analysis_db_path))

    def model_registry_kwargs(self) -> dict[str, Any]:
        """Return the normalized kwargs used by model-registry services."""
        return {
            "limit": self.limit,
            "include_ollama": self.include_ollama,
            "min_total_threshold": self.min_total_threshold,
            "load_env_file": self.load_env_file,
            "db_backend": self.db_backend,
            "sqlite_path": self.sqlite_path,
            "postgres_dsn": self.postgres_dsn,
            "sync_interval_days": self.sync_interval_days,
            "keep_history": self.keep_registry_history,
            "force_sync": self.force_registry_sync,
        }
