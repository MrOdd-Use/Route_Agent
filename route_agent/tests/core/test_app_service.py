"""Unit tests for route_agent.app.service.run_route_agent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import route_agent.app.legacy_analysis as legacy_module
import route_agent.app.service as main_module
from route_agent.router_engine.schemas import ModelCandidate, RouteDecision
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult


class _FakeSkippedProvider:
    """Represent `_FakeSkippedProvider`."""
    def __init__(self, provider: str, reason: str) -> None:
        """Initialize the instance."""
        self.provider = provider
        self.reason = reason

    def to_dict(self) -> dict[str, str]:
        """Execute `to_dict`."""
        return {"provider": self.provider, "reason": self.reason}


class _FakePool:
    """Represent `_FakePool`."""
    def list_available(self) -> list[SimpleNamespace]:
        """Execute `list_available`."""
        return [SimpleNamespace(model_id="openai:gpt-fast"), SimpleNamespace(model_id="openai:gpt-smart")]

    def summary(self) -> dict[str, object]:
        """Execute `summary`."""
        return {"total_models": 2, "available_models": 2, "providers": ["mock"], "slots": {}}


class _FakeAnalysisStorage:
    """Represent `_FakeAnalysisStorage`."""
    def __init__(self) -> None:
        """Initialize the instance."""
        self.updated: list[tuple[int, str]] = []

    def update_routed_model(self, record_id: int, routed_model: str) -> None:
        """Execute `update_routed_model`."""
        self.updated.append((record_id, routed_model))


class _FakeEngine:
    """Represent `_FakeEngine`."""
    def __init__(self, decision: RouteDecision) -> None:
        """Initialize the instance."""
        self._decision = decision
        self.requests: list[object] = []

    def route(self, request: object) -> RouteDecision:
        """Execute `route`."""
        self.requests.append(request)
        return self._decision

    def rate_limiter_status(self) -> dict[str, object]:
        """Execute `rate_limiter_status`."""
        return {
            "mode": "inmemory",
            "fail_strategy": "degrade",
            "switched_at": None,
            "last_error": None,
        }


def _setup_registry_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakePool:
    """Execute `_setup_registry_mocks`."""
    pool = _FakePool()

    report = SimpleNamespace(
        alerts=["low model count"],
        errors={"openai": "rate limited"},
        skipped_providers=[
            _FakeSkippedProvider("anthropic", "not configured"),
        ],
    )
    local_result = SimpleNamespace(
        report=report,
        source="local_pool_snapshot",
        storage_backend="sqlite",
        sync_due=False,
        sync_performed=False,
        snapshot_version="snap-1",
    )

    def fake_get_model_registry_report_with_local_pool(**_kwargs: object) -> SimpleNamespace:
        """Execute `fake_get_model_registry_report_with_local_pool`."""
        return local_result

    class _FakeMainModelPool:
        """Represent `_FakeMainModelPool`."""

        @staticmethod
        def from_report(*_args: object, **_kwargs: object) -> _FakePool:
            """Execute `from_report`."""
            return pool

    monkeypatch.setattr(
        main_module,
        "get_model_registry_report_with_local_pool",
        fake_get_model_registry_report_with_local_pool,
    )
    monkeypatch.setattr(main_module, "MainModelPool", _FakeMainModelPool)
    return pool


def _sample_analysis() -> TaskAnalysisResult:
    """Execute `_sample_analysis`."""
    return TaskAnalysisResult(
        domain="software_engineering",
        domain_description="Code implementation work.",
        relevant_dimensions=(
            DimensionScore(dimension="code", score=8, reasoning="Complex coding task."),
            DimensionScore(dimension="instruction_following", score=6, reasoning="Needs precise adherence."),
        ),
    )


def _sample_decision(primary_model: str | None = "openai:gpt-smart") -> RouteDecision:
    """Execute `_sample_decision`."""
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


def test_run_route_agent_requires_task() -> None:
    """Test run route agent requires task."""
    with pytest.raises(ValueError, match="request.task is required"):
        main_module.run_route_agent({"task": "   "})


def test_run_route_agent_routes_with_router_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run route agent routes with router engine."""
    _setup_registry_mocks(monkeypatch)
    fake_storage = _FakeAnalysisStorage()
    fake_engine = _FakeEngine(_sample_decision())
    recorded_events: list[dict[str, object]] = []

    monkeypatch.setattr(main_module, "analyze_task", lambda **_kwargs: (_sample_analysis(), 42))
    monkeypatch.setattr(main_module, "get_analysis_storage", lambda _db_path: fake_storage)
    monkeypatch.setattr(main_module, "get_engine", lambda *args, **kwargs: fake_engine)
    monkeypatch.setattr(main_module, "_record_route_decision_monitoring", lambda event: recorded_events.append(event))

    payload = main_module.run_route_agent(
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
        }
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
    assert route_request.analysis.domain == "software_engineering"

    assert payload["model_used"] == "openai:gpt-smart"
    assert payload["routing_reason"] == "class default selected at index=0"
    assert payload["analysis"]["domain"] == "software_engineering"
    assert payload["analysis_record_id"] == 42
    assert payload["analysis_fallback"] is False
    assert payload["default_used"] is True
    assert payload["class_source"] == "llm"
    assert len(payload["candidates"]) == 1
    assert payload["candidates"][0]["model_id"] == "openai:gpt-smart"
    assert fake_storage.updated == [(42, "openai:gpt-smart")]
    assert len(recorded_events) == 1
    assert recorded_events[0]["source"] == "main"
    assert recorded_events[0]["agent_name"] == "route_agent"
    assert recorded_events[0]["model_used"] == "openai:gpt-smart"
    assert recorded_events[0]["provider"] == "openai"
    assert recorded_events[0]["analysis_domain"] == "software_engineering"
    assert recorded_events[0]["registry_error_count"] == 1
    assert recorded_events[0]["skipped_provider_count"] == 1


def test_run_route_agent_falls_back_to_legacy_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test run route agent falls back to legacy analysis."""
    _setup_registry_mocks(monkeypatch)
    fake_storage = _FakeAnalysisStorage()
    fake_engine = _FakeEngine(_sample_decision(primary_model=None))

    def _raise_analyzer_failure(**_kwargs: object) -> tuple[TaskAnalysisResult, int]:
        """Execute `_raise_analyzer_failure`."""
        raise RuntimeError("analyzer failed")

    monkeypatch.setattr(main_module, "analyze_task", _raise_analyzer_failure)
    monkeypatch.setattr(main_module, "get_analysis_storage", lambda _db_path: fake_storage)
    monkeypatch.setattr(main_module, "get_engine", lambda *args, **kwargs: fake_engine)

    payload = main_module.run_route_agent({"task": "Please scrape this website and parse html."})

    assert payload["model_used"] is None
    assert payload["analysis_fallback"] is True
    assert payload["analysis_record_id"] is None
    assert payload["analysis"]["domain"] == "scrape"
    assert payload["analysis"]["relevant_dimensions"]
    assert "registry_errors={'openai': 'rate limited'}" in payload["routing_reason"]
    assert fake_storage.updated == []
    assert payload["registry_sync"]["source"] == "local_pool_snapshot"


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("Please scrape this website and parse html.", "scrape"),
        ("Extract entities into structured json output.", "extraction"),
        ("Summarize this report into a brief.", "summarization"),
        ("Classify the sentiment label of this review.", "classification"),
        ("Rewrite this paragraph to be clearer.", "rewrite"),
        ("Audit this architecture and give assessment.", "review"),
    ],
)
def test_legacy_detect_task_type_new_categories(task: str, expected: str) -> None:
    """Test legacy detect task type new categories."""
    assert legacy_module.detect_task_type(task) == expected
