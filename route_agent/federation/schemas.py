"""Federation data schemas: central-side and client-side data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# ---------------------------------------------------------------------------
# Central-side schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppRegistration:
    """Persistent record of a registered application."""

    app_id: str
    app_name: str
    registered_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class RegisteredAgent:
    """Persistent app-scoped agent → class mapping (central side)."""

    app_id: str
    agent_name: str
    agent_class: str
    registered_at: datetime
    updated_at: datetime
    agent_version: str = "v1"


@dataclass(frozen=True)
class ConcurrencyLease:
    """Active lease record tracking a model currently in use by an agent execution."""

    lease_id: str
    app_id: str
    agent_name: str
    agent_class: str
    proposed_model_id: str
    granted_model_id: str
    mode: str               # "local" | "central"
    acquired_at: datetime
    ttl_seconds: int = 300
    released_at: datetime | None = None


@dataclass(frozen=True)
class PoolVersionEntry:
    """Versioned snapshot of a class pool's ordered model list."""

    version: int
    agent_class: str
    model_ids: tuple[str, ...]
    updated_at: datetime


@dataclass(frozen=True)
class ModeDecision:
    """Result of a per-model contention check for a given class."""

    agent_class: str
    model_id: str
    mode: str               # "local" | "central"
    active_leases: int
    threshold: int
    decided_at: datetime


# ---------------------------------------------------------------------------
# Client-side (SDK) schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentMappingEntry:
    """Persistent app-scoped agent mapping stored in the client local store. No TTL."""

    app_id: str
    agent_name: str
    agent_class: str
    created_at: datetime
    updated_at: datetime
    agent_version: str = "v1"
    source: str = "declared"  # "declared" | "learned" | "manual"


@dataclass(frozen=True)
class LocalPoolSnapshot:
    """Local ordered replica of a class pool. No TTL."""

    agent_class: str
    ordered_model_ids: tuple[str, ...]
    pool_version: int
    synced_at: datetime
    default_model_id: str | None = None


# ---------------------------------------------------------------------------
# API request / response containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentDeclaration:
    """Single agent entry in an app registration request."""

    agent_name: str
    agent_class: str
    agent_version: str = "v1"


@dataclass(frozen=True)
class AppRegisterRequest:
    """Payload for POST /api/v1/apps/register."""

    app_id: str
    app_name: str
    agents: tuple[AgentDeclaration, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AppRegisterResponse:
    """Response for POST /api/v1/apps/register."""

    app_id: str
    registered_at: datetime
    pool_versions: dict[str, int] = field(default_factory=dict)
