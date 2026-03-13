"""Tests for app-layer registry helpers."""

from __future__ import annotations

from route_agent.app.registry import build_main_model_pool
from route_agent.model_registry.schemas import ModelMetadata, ModelRegistryReport


def test_build_main_model_pool_ignores_removed_model_slot_env(monkeypatch) -> None:
    """Removed model-slot env vars should not affect the registry pool."""
    monkeypatch.setenv("FAST_LLM", "openai:gpt-override")
    monkeypatch.setenv("SMART_LLM", "openai:gpt-override")
    monkeypatch.setenv("STRATEGIC_LLM", "openai:gpt-override")
    report = ModelRegistryReport(
        models=[
            ModelMetadata(
                model_id="deepseek:deepseek-chat",
                display_name="DeepSeek Chat",
                provider="deepseek",
                api_model_name="deepseek-chat",
                pricing={"input": 0.1},
            ),
            ModelMetadata(
                model_id="openai:gpt-4.1",
                display_name="GPT-4.1",
                provider="openai",
                api_model_name="gpt-4.1",
                pricing={"input": 1.0},
            ),
        ],
        total_models=2,
    )

    pool = build_main_model_pool(report)

    assert [model.model_id for model in pool.list_available()] == [
        "deepseek:deepseek-chat",
        "openai:gpt-4.1",
    ]
    assert pool.summary() == {
        "total_models": 2,
        "available_models": 2,
        "providers": ["deepseek", "openai"],
    }
