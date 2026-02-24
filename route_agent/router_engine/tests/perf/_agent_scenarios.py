"""Agent scenario builders for overlapping-batch performance tests."""

from __future__ import annotations

from dataclasses import dataclass
import random

from route_agent.router_engine.schemas import RouteConstraints
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult

TIME_SCALE = 0.4
BATCH_COUNT = 5
BATCH_SIZE = 4
TOTAL_AGENTS = BATCH_COUNT * BATCH_SIZE
SEED = 20260224
INTERVAL_MIN = 3.0 * TIME_SCALE
INTERVAL_MAX = 5.0 * TIME_SCALE

_ROLE_NAMES: tuple[str, ...] = (
    "report_writer",
    "review_agent",
    "risk_analyst",
    "data_extractor",
    "summarizer",
    "translator",
    "compliance_checker",
    "qa_agent",
    "insight_generator",
    "planner",
    "research_assistant",
    "fact_checker",
    "code_reviewer",
    "ops_analyst",
    "support_triage",
    "proposal_writer",
    "incident_reporter",
    "policy_rewriter",
    "classification_agent",
    "audit_agent",
)


@dataclass(frozen=True)
class AgentScenario:
    """One synthetic agent execution scenario."""

    role_name: str
    agent_class: str
    request_id: str
    batch_index: int
    complexity_tier: str
    duration_seconds: float
    analysis: TaskAnalysisResult
    constraints: RouteConstraints


def _domain_for_role(role_name: str) -> str:
    if "code" in role_name:
        return "coding"
    if "extract" in role_name or "classification" in role_name:
        return "extraction"
    if "translate" in role_name:
        return "translation"
    if "review" in role_name or "audit" in role_name or "compliance" in role_name:
        return "review"
    if "summarizer" in role_name or "writer" in role_name or "rewriter" in role_name:
        return "summarization"
    return "reasoning"


def _class_for_role(role_name: str) -> str:
    if "writer" in role_name or "summarizer" in role_name or "rewriter" in role_name:
        return "writing"
    if "review" in role_name or "audit" in role_name or "compliance" in role_name:
        return "review"
    if "extract" in role_name or "classification" in role_name:
        return "extraction"
    if "ops" in role_name or "incident" in role_name or "support" in role_name:
        return "operations"
    if "code" in role_name:
        return "coding"
    return "analysis"


def _dimensions_for_tier(role_name: str, tier: str, rng: random.Random) -> tuple[DimensionScore, ...]:
    domain = _domain_for_role(role_name)
    if tier == "simple":
        dims = (
            ("instruction_following", rng.randint(2, 4)),
            ("text", rng.randint(2, 4)),
        )
    elif tier == "medium":
        lead = "code" if domain == "coding" else ("math" if domain == "reasoning" else "text")
        dims = (
            (lead, rng.randint(5, 7)),
            ("instruction_following", rng.randint(5, 7)),
            ("text", rng.randint(5, 7)),
        )
    else:
        dims = (
            ("reasoning", rng.randint(8, 10)),
            ("code", rng.randint(8, 10)),
            ("math", rng.randint(8, 10)),
            ("instruction_following", rng.randint(8, 10)),
        )

    return tuple(
        DimensionScore(
            dimension=name,
            score=score,
            reasoning=f"synthetic {tier} tier scenario for {role_name}",
        )
        for name, score in dims
    )


def _sample_duration_seconds(tier: str, rng: random.Random) -> float:
    if tier == "simple":
        low, high = 1.0, 3.0
    elif tier == "medium":
        low, high = 5.0, 10.0
    else:
        low, high = 30.0, 40.0
    return rng.uniform(low * TIME_SCALE, high * TIME_SCALE)


def build_batch_start_offsets(
    *,
    seed: int = SEED,
    batch_count: int = BATCH_COUNT,
) -> tuple[float, ...]:
    """Build cumulative start offsets using start-to-start intervals."""
    rng = random.Random(seed + 7)
    offsets: list[float] = [0.0]
    for _ in range(1, batch_count):
        offsets.append(offsets[-1] + rng.uniform(INTERVAL_MIN, INTERVAL_MAX))
    return tuple(offsets)


def build_agent_scenarios(
    provider_models: dict[str, tuple[str, ...]],
    *,
    seed: int = SEED,
) -> tuple[AgentScenario, ...]:
    """Build 20 agents with fixed-seed roles, tiers, and constraints."""
    if len(_ROLE_NAMES) != TOTAL_AGENTS:
        raise ValueError(f"Expected {TOTAL_AGENTS} role names, got {len(_ROLE_NAMES)}")
    if not provider_models:
        raise ValueError("provider_models must not be empty")

    rng = random.Random(seed)
    providers = sorted(provider_models.keys())
    tiers = ["simple"] * 8 + ["medium"] * 8 + ["complex"] * 4
    rng.shuffle(tiers)

    scenarios: list[AgentScenario] = []
    for idx, role_name in enumerate(_ROLE_NAMES):
        batch_index = idx // BATCH_SIZE
        tier = tiers[idx]
        provider = providers[idx % len(providers)]
        model_choices = provider_models[provider]
        preferred = model_choices[batch_index % len(model_choices)]

        domain = _domain_for_role(role_name)
        analysis = TaskAnalysisResult(
            domain=domain,
            domain_description=f"synthetic domain for {role_name}",
            relevant_dimensions=_dimensions_for_tier(role_name, tier, rng),
        )

        if tier == "simple":
            estimated_tokens = rng.randint(800, 2000)
        elif tier == "medium":
            estimated_tokens = rng.randint(3000, 7000)
        else:
            estimated_tokens = rng.randint(8000, 14000)

        scenarios.append(
            AgentScenario(
                role_name=role_name,
                agent_class=_class_for_role(role_name),
                request_id=f"perf-req-{idx:02d}",
                batch_index=batch_index,
                complexity_tier=tier,
                duration_seconds=_sample_duration_seconds(tier, rng),
                analysis=analysis,
                constraints=RouteConstraints(
                    max_cost=10.0,
                    preferred_model=preferred,
                    exclude_models=(),
                    require_provider=provider,
                    estimated_input_tokens=estimated_tokens,
                ),
            )
        )

    return tuple(scenarios)

