"""Tests for app-layer task-analysis resolution (三级分析链路)。"""

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
        task_class="review",
    )


def test_resolve_via_profile_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """① 向量画像匹配成功时应直接返回，不走 LLM。"""
    profile_result = analysis_module.ResolvedTaskAnalysis(
        result=_sample_analysis(),
        record_id=None,
        used_profile_match=True,
    )
    monkeypatch.setattr(analysis_module, "_try_profile_analysis", lambda _req: profile_result)

    resolved = analysis_module.resolve_task_analysis(
        RouteAgentRequest(task="审查代码", agent_name="reviewer"),
    )

    assert resolved.used_profile_match is True
    assert resolved.used_legacy_fallback is False
    assert resolved.result.domain == "software_engineering"


def test_resolve_via_llm_new_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """② 向量未命中 → LLM 新类判定成功。"""
    monkeypatch.setattr(analysis_module, "_try_profile_analysis", lambda _req: None)

    llm_result = analysis_module.ResolvedTaskAnalysis(
        result=_sample_analysis(),
        record_id=None,
        suggested_new_class="code_generation",
        suggested_new_class_description="代码生成类任务",
    )
    monkeypatch.setattr(analysis_module, "_try_new_class_analysis", lambda _req: llm_result)

    resolved = analysis_module.resolve_task_analysis(
        RouteAgentRequest(task="生成一个 React 组件", agent_name="generator"),
    )

    assert resolved.suggested_new_class == "code_generation"
    assert resolved.used_legacy_fallback is False


def test_resolve_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """③ 向量 + LLM 全部失败 → legacy 兜底。"""
    monkeypatch.setattr(analysis_module, "_try_profile_analysis", lambda _req: None)
    monkeypatch.setattr(analysis_module, "_try_new_class_analysis", lambda _req: None)

    resolved = analysis_module.resolve_task_analysis(
        RouteAgentRequest(task="Please scrape this website and parse html."),
    )

    assert resolved.record_id is None
    assert resolved.used_legacy_fallback is True
    assert resolved.result.domain == "scrape"
    assert resolved.result.relevant_dimensions


def test_build_lightweight_analysis_for_known_class() -> None:
    """已知类应返回正确的 task_class、domain 和非空 relevant_dimensions。"""
    result = analysis_module.build_lightweight_analysis_for_class("summarization")

    assert result.task_class == "summarization"
    assert result.domain == "summarization"
    assert result.domain_description != ""
    dim_names = {d.dimension for d in result.relevant_dimensions}
    assert dim_names == {"text", "creative_writing"}


def test_build_lightweight_analysis_general_uses_fallback_dims() -> None:
    """general 类的 profile 为空，应回退到 GENERAL_FALLBACK_DIMS。"""
    result = analysis_module.build_lightweight_analysis_for_class("general")

    assert result.task_class == "general"
    assert len(result.relevant_dimensions) > 0
    dim_names = {d.dimension for d in result.relevant_dimensions}
    assert dim_names == {"reasoning", "text", "instruction_following"}


def test_build_lightweight_analysis_unknown_class_has_no_description() -> None:
    """未知类应仍能构建结果，description 为空，dimensions 回退到 GENERAL_FALLBACK_DIMS。"""
    result = analysis_module.build_lightweight_analysis_for_class("unknown_custom_class")

    assert result.task_class == "unknown_custom_class"
    assert result.domain == "unknown_custom_class"
    assert result.domain_description == ""
    assert len(result.relevant_dimensions) > 0


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
