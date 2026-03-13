"""Tests for app-layer task-analysis resolution."""

from __future__ import annotations

import pytest

import route_agent.app.analysis as analysis_module
import route_agent.app.legacy_analysis as legacy_module
from route_agent.app.contracts import RouteAgentRequest
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult


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


def test_resolve_task_analysis_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Successful analyzer calls should pass through unchanged."""
    monkeypatch.setattr(analysis_module, "analyze_task", lambda **_kwargs: (_sample_analysis(), 42))

    resolved = analysis_module.resolve_task_analysis(
        RouteAgentRequest(task="Write a Python function to sort numbers.", agent_name="coder"),
    )

    assert resolved.result.domain == "software_engineering"
    assert resolved.record_id == 42
    assert resolved.used_legacy_fallback is False


def test_resolve_task_analysis_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Analyzer failures should downgrade to the legacy heuristic path."""
    monkeypatch.setattr(
        analysis_module,
        "analyze_task",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("analyzer failed")),
    )

    resolved = analysis_module.resolve_task_analysis(
        RouteAgentRequest(task="Please scrape this website and parse html."),
    )

    assert resolved.record_id is None
    assert resolved.used_legacy_fallback is True
    assert resolved.result.domain == "scrape"
    assert resolved.result.relevant_dimensions


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
    """Legacy task-type detection should keep supporting the expanded categories."""
    assert legacy_module.detect_task_type(task) == expected
