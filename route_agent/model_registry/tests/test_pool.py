"""Tests for the model-registry main pool."""

from __future__ import annotations

from route_agent.model_registry.pool import MainModelPool
from route_agent.model_registry.schemas import ModelMetadata, ModelRegistryReport


def _model(model_id: str, display_name: str, price: float, *, availability: str | None = None) -> ModelMetadata:
    """Build a minimal model entry for main-pool tests."""
    provider, api_model_name = model_id.split(":", maxsplit=1)
    status = {} if availability is None else {"availability": availability}
    return ModelMetadata(
        model_id=model_id,
        display_name=display_name,
        provider=provider,
        api_model_name=api_model_name,
        pricing={"input": price},
        status=status,
    )


def test_main_model_pool_from_report_keeps_available_models_only() -> None:
    """The main pool should filter unavailable models and expose summary counts."""
    report = ModelRegistryReport(
        models=[
            _model("deepseek:deepseek-chat", "DeepSeek Chat", 0.1),
            _model("openai:gpt-5", "GPT-5", 1.0, availability="offline"),
        ],
        total_models=2,
    )

    pool = MainModelPool.from_report(report)

    assert [model.model_id for model in pool.list_available()] == ["deepseek:deepseek-chat"]
    assert pool.summary() == {
        "total_models": 2,
        "available_models": 1,
        "providers": ["deepseek", "openai"],
    }


def test_main_model_pool_get_returns_exact_model_by_id() -> None:
    """The main pool should support direct model lookup without tier indirection."""
    pool = MainModelPool(
        [
            _model("deepseek:deepseek-chat", "DeepSeek Chat", 0.1),
            _model("openai:gpt-5", "GPT-5", 1.0),
        ]
    )

    model = pool.get("openai:gpt-5")

    assert model is not None
    assert model.model_id == "openai:gpt-5"
    assert model.display_name == "GPT-5"
