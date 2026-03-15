"""Pydantic v2 request/response models for the REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from route_agent.app.contracts import RouteAgentRequest


class RouteConstraintsBody(BaseModel):
    """Request-body constraints for routing."""

    model_config = ConfigDict(frozen=True)

    max_cost: float | None = None
    preferred_model: str | None = None
    exclude_models: list[str] | None = None
    require_provider: str | None = None
    estimated_input_tokens: int | None = None

    def to_mapping(self) -> dict[str, Any]:
        """Return a plain mapping consumed by the application layer."""
        return self.model_dump()


class RouteRequestBody(BaseModel):
    """JSON body for the `/route` endpoint."""

    model_config = ConfigDict(frozen=True)

    task: str = ""
    agent_name: str | None = None
    system_prompt: str | None = None
    request_id: str | None = None
    agent_class: str | None = None
    constraints: RouteConstraintsBody | None = None

    def to_app_request(self, *, default_agent_name: str) -> RouteAgentRequest:
        """Convert the API body into a normalized application request."""
        return RouteAgentRequest.from_mapping(
            {
                "task": self.task,
                "agent_name": self.agent_name,
                "system_prompt": self.system_prompt,
                "request_id": self.request_id,
                "agent_class": self.agent_class,
                "constraints": None if self.constraints is None else self.constraints.to_mapping(),
            },
            default_agent_name=default_agent_name,
        )


class RouteResponse(BaseModel):
    """Response body for the `/route` endpoint."""

    model_config = ConfigDict(frozen=True)

    model_used: str | None = None
    routing_reason: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    alerts: list[str] = Field(default_factory=list)
    start_index: int = 0
    default_used: bool = False
    pool_hit: bool = False
    pool_class: str | None = None
    class_source: str = ""
    analysis_record_id: int | None = None
    analysis_fallback: bool = False
    pool_summary: dict[str, Any] = Field(default_factory=dict)
    registry_alerts: list[str] = Field(default_factory=list)
    registry_errors: dict[str, str] = Field(default_factory=dict)
    skipped_providers: list[dict[str, str]] = Field(default_factory=list)
    rate_limiter: dict[str, Any] = Field(default_factory=dict)
    registry_sync: dict[str, Any] = Field(default_factory=dict)


class ModelsResponse(BaseModel):
    """Response body for the `/models` endpoint."""

    model_config = ConfigDict(frozen=True)

    models: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    pool_summary: dict[str, Any] = Field(default_factory=dict)


class ModelStatusCard(BaseModel):
    """Normalized model status card used by dashboard pool views."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    display_name: str
    provider: str
    status: str
    status_reason: str
    is_default: bool = False
    request_count: int = 0
    success_rate: float | None = None
    avg_latency_ms: float | None = None
    last_used_at: str | None = None
    registry_availability: str | None = None
    is_available: bool = True
    success_count: int = 0
    fail_count: int = 0
    last_outcome: str | None = None
    last_outcome_at: str | None = None
    updated_at: str | None = None
    model_release_date: str | None = None


class GlobalPoolStatusSummary(BaseModel):
    """Summary metrics shown above the global pool card grid."""

    model_config = ConfigDict(frozen=True)

    total_models: int = 0
    available_models: int = 0
    request_count: int = 0
    overall_success_rate: float = 0.0


class GlobalPoolStatusResponse(BaseModel):
    """Response body for the global pool status endpoint."""

    model_config = ConfigDict(frozen=True)

    summary: GlobalPoolStatusSummary = Field(default_factory=GlobalPoolStatusSummary)
    cards: list[ModelStatusCard] = Field(default_factory=list)


class ClassPoolSummaryItem(BaseModel):
    """Directory item for one existing class pool."""

    model_config = ConfigDict(frozen=True)

    agent_class: str
    description: str = ""
    model_count: int = 0
    default_model: str | None = None
    last_updated_at: str | None = None


class ClassPoolListResponse(BaseModel):
    """Response body for the class-pool directory endpoint."""

    model_config = ConfigDict(frozen=True)

    classes: list[ClassPoolSummaryItem] = Field(default_factory=list)
    count: int = 0


class ClassPoolDetailResponse(BaseModel):
    """Response body for one class-pool detail endpoint."""

    model_config = ConfigDict(frozen=True)

    agent_class: str
    description: str = ""
    default_model: str | None = None
    model_count: int = 0
    last_updated_at: str | None = None
    cards: list[ModelStatusCard] = Field(default_factory=list)


class StatsResponse(BaseModel):
    """Response body for the `/stats` endpoint."""

    model_config = ConfigDict(frozen=True)

    windows: dict[str, Any] = Field(default_factory=dict)


class DecisionsResponse(BaseModel):
    """Response body for the `/decisions` endpoint."""

    model_config = ConfigDict(frozen=True)

    decisions: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class ExecutionStartBody(BaseModel):
    """Request body for execution lifecycle start events."""

    model_config = ConfigDict(frozen=True)

    source: str = "api"
    agent_name: str = ""
    execution_id: str | None = None
    request_id: str | None = None
    model_used: str | None = None
    provider: str | None = None
    metadata: dict[str, Any] | None = None


class ExecutionEndBody(BaseModel):
    """Request body for execution lifecycle end events."""

    model_config = ConfigDict(frozen=True)

    execution_id: str
    status: str = "success"
    duration_ms: float | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None


class ExecutionStartResponse(BaseModel):
    """Response body for execution lifecycle start events."""

    model_config = ConfigDict(frozen=True)

    execution_id: str


class ExecutionEndResponse(BaseModel):
    """Response body for execution lifecycle end events."""

    model_config = ConfigDict(frozen=True)

    success: bool


class ExecutionsResponse(BaseModel):
    """Response body for execution lifecycle queries."""

    model_config = ConfigDict(frozen=True)

    executions: list[dict[str, Any]] = Field(default_factory=list)
    count: int = 0


class HealthResponse(BaseModel):
    """Response body for the health endpoint."""

    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    version: str = ""
    monitoring_enabled: bool = False


class AgentStatusResponse(BaseModel):
    """Per-agent model assignment and execution status snapshot."""

    model_config = ConfigDict(frozen=True)

    total_executions: int = 0
    total_agents: int = 0
    active_agent_count: int = 0
    status_counts: dict[str, int] = Field(default_factory=dict)
    model_counts: dict[str, int] = Field(default_factory=dict)
    agents: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Structured API error payload."""

    model_config = ConfigDict(frozen=True)

    error: str
    detail: str | None = None
    request_id: str | None = None
