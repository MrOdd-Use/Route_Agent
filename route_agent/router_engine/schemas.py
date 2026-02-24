"""Data schemas for router_engine module."""

from __future__ import annotations

from dataclasses import dataclass, field

from route_agent.task_analyzer.schemas import TaskAnalysisResult


@dataclass(frozen=True)
class RouteConstraints:
    """Represent `RouteConstraints`."""
    max_cost: float | None = None
    preferred_model: str | None = None
    exclude_models: tuple[str, ...] = field(default_factory=tuple)
    require_provider: str | None = None
    estimated_input_tokens: int | None = None


@dataclass(frozen=True)
class RouteRequest:
    """Represent `RouteRequest`."""
    agent_name: str
    request_id: str
    task_prompt: str
    analysis: TaskAnalysisResult
    constraints: RouteConstraints = field(default_factory=RouteConstraints)
    agent_class: str | None = None
    system_prompt: str | None = None
    record_id: int | None = None


@dataclass(frozen=True)
class ModelCandidate:
    """Represent `ModelCandidate`."""
    model_id: str
    provider: str
    display_name: str
    dimension_score: float
    raw_dimension_score: float
    cost_score: float
    health_status: str
    success_bonus: float
    fail_penalty: float
    rate_limited: bool = False
    is_default: bool = False
    is_pool: bool = False
    is_explore: bool = False
    rank: int = 0


@dataclass(frozen=True)
class RouteDecision:
    """Represent `RouteDecision`."""
    primary_model: str | None
    candidates: tuple[ModelCandidate, ...]
    start_index: int
    reason: str
    alerts: tuple[str, ...] = field(default_factory=tuple)
    default_used: bool = False
    pool_hit: bool = False
    pool_class: str | None = None
    class_source: str = "default"


@dataclass(frozen=True)
class ExecutionAttempt:
    """Represent `ExecutionAttempt`."""
    model_id: str
    attempt_number: int
    success: bool
    failure_type: str | None = None
    error_detail: str | None = None
    output_snippet: str | None = None


@dataclass(frozen=True)
class EscalationResult:
    """Represent `EscalationResult`."""
    action: str
    next_model: str | None
    previous_attempts: tuple[ExecutionAttempt, ...] = field(default_factory=tuple)
    context_for_next: str | None = None
    priority: str = "normal"


@dataclass(frozen=True)
class ModelUtilization:
    """Represent `ModelUtilization`."""
    rpm_ratio: float = 0.0
    conc_ratio: float = 0.0
    normal_conc_ratio: float = 0.0
    escalation_conc_ratio: float = 0.0
    escalation_capped: bool = False
    latency_ratio: float = 0.0
    is_limited: bool = False

    @property
    def peak_ratio(self) -> float:
        """Execute `peak_ratio`."""
        return max(self.rpm_ratio, self.conc_ratio)


@dataclass(frozen=True)
class ModelAvailability:
    """Represent `ModelAvailability`."""
    model_id: str
    status: str
    degraded_since: str | None = None
    unable_since: str | None = None
    last_probe_at: str | None = None
    last_probe_success: bool | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ClassModelStats:
    """Represent `ClassModelStats`."""
    agent_class: str
    model_id: str
    success_count: int = 0
    fail_count: int = 0
    exec_fail_count: int = 0
    consecutive_success: int = 0
    consecutive_fail: int = 0
    success_rate: float = 0.0
    bonus_level: int = 0
    penalty_level: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ClassPoolEntry:
    """Represent `ClassPoolEntry`."""
    agent_class: str
    model_id: str
    model_release_date: str | None = None
    success_count: int = 0
    fail_count: int = 0
    success_rate: float = 0.0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ClassPoolDefault:
    """Represent `ClassPoolDefault`."""
    agent_class: str
    domain: str
    model_id: str
    is_locked: bool = False
    consecutive_success: int = 0
    consecutive_fail: int = 0
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ClassAlias:
    """Represent `ClassAlias`."""
    alias_class: str
    canonical_class: str
    source: str = "seed"
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class ClassReviewItem:
    """Represent `ClassReviewItem`."""
    normalized_class: str
    proposed_by: str
    hit_count: int = 1
    status: str = "pending"
    merged_to: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


@dataclass(frozen=True)
class MatchResult:
    """Represent `MatchResult`."""
    class_name: str
    top1_score: float
    top2_score: float
    margin: float
