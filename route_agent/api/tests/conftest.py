"""Shared fixtures for API tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from route_agent.router_engine.schemas import ModelCandidate, RouteDecision
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult


class FakeSkippedProvider:
    """Represent `FakeSkippedProvider`."""

    def __init__(self, provider: str, reason: str) -> None:
        """Initialize the instance."""
        self.provider = provider
        self.reason = reason

    def to_dict(self) -> dict[str, str]:
        """Execute `to_dict`."""
        return {"provider": self.provider, "reason": self.reason}


class FakePool:
    """Represent `FakePool`."""

    def list_available(self, provider: str | None = None) -> list[SimpleNamespace]:
        """Execute `list_available`."""
        models = [
            SimpleNamespace(model_id="deepseek:deepseek-chat", provider="deepseek", display_name="DeepSeek Chat"),
            SimpleNamespace(model_id="openai:gpt-4o", provider="openai", display_name="GPT-4o"),
        ]
        if provider is not None:
            return [m for m in models if m.provider == provider]
        return models

    def summary(self) -> dict[str, object]:
        """Execute `summary`."""
        return {
            "total_models": 2,
            "available_models": 2,
            "providers": ["deepseek", "openai"],
        }


class FakeAnalysisStorage:
    """Represent `FakeAnalysisStorage`."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.updated: list[tuple[int, str]] = []

    def update_routed_model(self, record_id: int, routed_model: str) -> None:
        """Execute `update_routed_model`."""
        self.updated.append((record_id, routed_model))


class FakeEngine:
    """Represent `FakeEngine`."""

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


def sample_analysis() -> TaskAnalysisResult:
    """Execute `sample_analysis`."""
    return TaskAnalysisResult(
        domain="software_engineering",
        domain_description="Code implementation work.",
        relevant_dimensions=(
            DimensionScore(dimension="code", score=8, reasoning="Complex coding task."),
            DimensionScore(dimension="instruction_following", score=6, reasoning="Needs precise adherence."),
        ),
    )


def sample_decision(primary_model: str | None = "deepseek:deepseek-chat") -> RouteDecision:
    """Execute `sample_decision`."""
    candidates: tuple[ModelCandidate, ...]
    if primary_model is None:
        candidates = ()
    else:
        candidates = (
            ModelCandidate(
                model_id=primary_model,
                provider=primary_model.split(":")[0] if ":" in primary_model else "unknown",
                display_name="DeepSeek Chat",
                dimension_score=0.86,
                raw_dimension_score=0.84,
                cost_score=0.22,
                health_status="healthy",
                rank=0,
            ),
        )
    return RouteDecision(
        primary_model=primary_model,
        candidates=candidates,
        start_index=0,
        reason="class default selected at index=0",
        alerts=(),
        default_used=True if primary_model else False,
        pool_hit=False,
        pool_class=None,
        class_source="llm",
    )


def fake_registry_report() -> SimpleNamespace:
    """Execute `fake_registry_report`."""
    report = SimpleNamespace(
        alerts=["low model count"],
        errors={},
        skipped_providers=[
            FakeSkippedProvider("anthropic", "not configured"),
        ],
    )
    return SimpleNamespace(
        report=report,
        source="local_pool_snapshot",
        storage_backend="sqlite",
        sync_due=False,
        sync_performed=False,
        snapshot_version="snap-1",
    )
