"""Tests for app-layer route orchestration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import route_agent.app.orchestrator as orchestrator_module
from route_agent.app.analysis import ResolvedTaskAnalysis
from route_agent.app.contracts import RouteAgentRequest, RouteAgentRunOptions
from route_agent.app.registry import RegistryContext
from route_agent.router_engine.schemas import ModelCandidate, RouteDecision
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult


class _FakePool:
    """Minimal pool object used in orchestration tests."""

    def summary(self) -> dict[str, object]:
        """Return a stable pool summary for assertions."""
        return {"total_models": 2}


class _FakeAnalysisStorage:
    """Minimal analysis storage used in orchestration tests."""

    def __init__(self) -> None:
        """Initialize tracked update calls."""
        self.updated: list[tuple[int, str]] = []

    def update_routed_model(self, record_id: int, routed_model: str) -> None:
        """Track routed-model persistence requests."""
        self.updated.append((record_id, routed_model))


class _FakeEngine:
    """Minimal router engine used in orchestration tests."""

    def __init__(self, decision: RouteDecision) -> None:
        """Initialize the fake engine with a fixed decision."""
        self._decision = decision
        self.requests: list[object] = []

    def route(self, request: object) -> RouteDecision:
        """Record the request and return the preconfigured decision."""
        self.requests.append(request)
        return self._decision

    def rate_limiter_status(self) -> dict[str, object]:
        """Return a stable rate-limiter status."""
        return {
            "mode": "inmemory",
            "fail_strategy": "degrade",
            "switched_at": None,
            "last_error": None,
        }


def _sample_analysis() -> TaskAnalysisResult:
    """Build a representative analyzer result for app-layer tests."""
    return TaskAnalysisResult(
        domain="software_engineering",
        domain_description="Code implementation work.",
        relevant_dimensions=(
            DimensionScore(dimension="code", score=8, reasoning="Complex coding task."),
            DimensionScore(dimension="instruction_following", score=6, reasoning="Needs precise adherence."),
        ),
    )


def _sample_decision(primary_model: str | None = "openai:gpt-smart") -> RouteDecision:
    """Build a representative routing decision for app-layer tests."""
    candidates: tuple[ModelCandidate, ...]
    if primary_model is None:
        candidates = ()
    else:
        candidates = (
            ModelCandidate(
                model_id=primary_model,
                provider="openai",
                display_name="GPT Smart",
                dimension_score=0.86,
                raw_dimension_score=0.84,
                cost_score=0.22,
                health_status="healthy",
                success_bonus=1.0,
                fail_penalty=1.0,
                rank=0,
            ),
        )
    return RouteDecision(
        primary_model=primary_model,
        candidates=candidates,
        start_index=0,
        reason="class default selected at index=0",
        alerts=tuple(),
        default_used=True if primary_model else False,
        pool_hit=False,
        pool_class=None,
        class_source="llm",
    )


def _registry_context() -> RegistryContext:
    """Build a representative registry context for orchestration tests."""
    report = SimpleNamespace(
        alerts=["low model count"],
        errors={"openai": "rate limited"},
        skipped_providers=[],
    )
    local_pool_result = SimpleNamespace(
        report=report,
        source="local_pool_snapshot",
        storage_backend="sqlite",
        sync_due=False,
        sync_performed=False,
        snapshot_version="snap-1",
    )
    return RegistryContext(report=report, local_pool_result=local_pool_result, pool=_FakePool())


def test_execute_route_routes_and_records_monitoring(monkeypatch: pytest.MonkeyPatch) -> None:
    """The orchestrator should assemble the route request and persist side effects."""
    fake_storage = _FakeAnalysisStorage()
    fake_engine = _FakeEngine(_sample_decision())
    recorded_events: list[dict[str, object]] = []

    monkeypatch.setattr(orchestrator_module, "build_registry_context", lambda _options: _registry_context())
    monkeypatch.setattr(
        orchestrator_module,
        "resolve_task_analysis",
        lambda _request: ResolvedTaskAnalysis(result=_sample_analysis(), record_id=42, used_legacy_fallback=False),
    )
    monkeypatch.setattr(orchestrator_module, "get_analysis_storage", lambda _db_path: fake_storage)
    monkeypatch.setattr(orchestrator_module, "get_engine", lambda *_args, **_kwargs: fake_engine)
    monkeypatch.setattr(
        orchestrator_module,
        "build_route_monitoring_event",
        lambda **kwargs: {
            "agent_name": kwargs["agent_name"],
            "model_used": kwargs["decision"].primary_model,
            "provider": "openai",
        },
    )
    monkeypatch.setattr(orchestrator_module, "record_route_decision", lambda event: recorded_events.append(event))

    execution = orchestrator_module.execute_route(
        RouteAgentRequest.from_mapping(
            {
                "task": "Write a Python function to sort numbers.",
                "request_id": "req-123",
                "system_prompt": "You are a coding expert.",
                "constraints": {
                    "max_cost": "0.05",
                    "preferred_model": "openai:gpt-smart",
                    "exclude_models": ["openai:gpt-legacy"],
                    "require_provider": "openai",
                    "estimated_input_tokens": "2048",
                },
            },
            default_agent_name="route_agent",
        ),
        RouteAgentRunOptions(
            default_agent_name="route_agent",
            redis_url="redis://localhost:6379/0",
            rate_limit_mode="inmemory",
        ),
    )

    assert len(fake_engine.requests) == 1
    route_request = fake_engine.requests[0]
    assert route_request.request_id == "req-123"
    assert route_request.system_prompt == "You are a coding expert."
    assert route_request.record_id == 42
    assert route_request.constraints.max_cost == 0.05
    assert route_request.constraints.preferred_model == "openai:gpt-smart"
    assert route_request.constraints.exclude_models == ("openai:gpt-legacy",)
    assert route_request.constraints.require_provider == "openai"
    assert route_request.constraints.estimated_input_tokens == 2048

    assert execution.decision.primary_model == "openai:gpt-smart"
    assert execution.record_id == 42
    assert execution.used_legacy_fallback is False
    assert fake_storage.updated == [(42, "openai:gpt-smart")]
    assert recorded_events == [
        {
            "agent_name": "route_agent",
            "model_used": "openai:gpt-smart",
            "provider": "openai",
        },
    ]


def test_execute_route_skips_routed_model_update_when_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """No routed model means the analysis record should not be updated."""
    fake_storage = _FakeAnalysisStorage()
    fake_engine = _FakeEngine(_sample_decision(primary_model=None))
    recorded_events: list[dict[str, object]] = []

    monkeypatch.setattr(orchestrator_module, "build_registry_context", lambda _options: _registry_context())
    monkeypatch.setattr(
        orchestrator_module,
        "resolve_task_analysis",
        lambda _request: ResolvedTaskAnalysis(result=_sample_analysis(), record_id=7, used_legacy_fallback=True),
    )
    monkeypatch.setattr(orchestrator_module, "get_analysis_storage", lambda _db_path: fake_storage)
    monkeypatch.setattr(orchestrator_module, "get_engine", lambda *_args, **_kwargs: fake_engine)
    monkeypatch.setattr(orchestrator_module, "build_route_monitoring_event", lambda **_kwargs: {"model_used": None})
    monkeypatch.setattr(orchestrator_module, "record_route_decision", lambda event: recorded_events.append(event))

    execution = orchestrator_module.execute_route(
        RouteAgentRequest(task="Please scrape this website and parse html."),
        RouteAgentRunOptions(default_agent_name="route_agent"),
    )

    assert execution.decision.primary_model is None
    assert execution.used_legacy_fallback is True
    assert fake_storage.updated == []
    assert recorded_events == [{"model_used": None}]
