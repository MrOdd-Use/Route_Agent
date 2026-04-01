"""Pydantic request/response models for the Federation API.

Kept in a separate file to avoid circular imports between
route_agent.api (which imports main.py) and
route_agent.federation.api.routes (which is mounted by main.py).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AgentDeclarationBody(BaseModel):
    """Single agent declaration inside an app registration request."""

    model_config = ConfigDict(frozen=True)

    agent_name: str
    agent_class: str
    agent_version: str = "v1"


class FederationAppRegisterRequest(BaseModel):
    """Request body for POST /api/v1/apps/register."""

    model_config = ConfigDict(frozen=True)

    app_id: str
    app_name: str
    agents: list[AgentDeclarationBody] = Field(default_factory=list)


class FederationAppRegisterResponse(BaseModel):
    """Response body for POST /api/v1/apps/register."""

    model_config = ConfigDict(frozen=True)

    app_id: str
    registered_at: str
    pool_versions: dict[str, int] = Field(default_factory=dict)


class AcquireLeaseRequest(BaseModel):
    """Request body for POST /api/v1/concurrency/acquire."""

    model_config = ConfigDict(frozen=True)

    app_id: str
    agent_name: str
    agent_version: str = "v1"
    agent_class: str
    proposed_model_id: str
    candidate_model_ids: list[str] = Field(default_factory=list)
    estimated_duration_s: int | None = None


class AcquireLeaseResponse(BaseModel):
    """Response body for POST /api/v1/concurrency/acquire."""

    model_config = ConfigDict(frozen=True)

    lease_id: str
    granted: bool
    mode: str
    granted_model_id: str
    pool_version: int
    pool_changed: bool
    reason: str | None = None


class ReleaseLeaseRequest(BaseModel):
    """Request body for POST /api/v1/concurrency/release."""

    model_config = ConfigDict(frozen=True)

    lease_id: str


class ReleaseLeaseResponse(BaseModel):
    """Response body for POST /api/v1/concurrency/release."""

    model_config = ConfigDict(frozen=True)

    released: bool


class ReportOutcomeRequest(BaseModel):
    """Request body for POST /api/v1/outcomes/report."""

    model_config = ConfigDict(frozen=True)

    lease_id: str | None = None
    request_id: str | None = None
    model_id: str
    agent_class: str
    outcome_type: str
    duration_ms: float | None = None
    quality_score: float | None = None


class ReportOutcomeResponse(BaseModel):
    """Response body for POST /api/v1/outcomes/report."""

    model_config = ConfigDict(frozen=True)

    accepted: bool


class PoolVersionQueryResponse(BaseModel):
    """Response body for GET /api/v1/pool-version."""

    model_config = ConfigDict(frozen=True)

    versions: dict[str, int] = Field(default_factory=dict)


class ModeQueryResponse(BaseModel):
    """Response body for GET /api/v1/mode."""

    model_config = ConfigDict(frozen=True)

    agent_class: str
    model_id: str
    mode: str
    active_leases: int
    threshold: int


class FederationModelScore(BaseModel):
    """Per-model aggregated score entry."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    success_count: int = 0
    fail_count: int = 0
    total_count: int = 0
    success_rate: float = 0.0


class FederationScoresResponse(BaseModel):
    """Response body for GET /api/v1/federation-scores/{agent_class}."""

    model_config = ConfigDict(frozen=True)

    agent_class: str
    scores: list[FederationModelScore] = Field(default_factory=list)
