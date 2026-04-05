"""Tests for the model-registry main pool."""

from __future__ import annotations

from dataclasses import dataclass

from route_agent.model_registry.providers.base import ProviderAdapter
from route_agent.model_registry.providers.factory import (
    create_provider_adapters_from_env,
    expected_providers,
)
from route_agent.model_registry.pool import MainModelPool
from route_agent.model_registry.registry import ModelRegistry
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


@dataclass(slots=True)
class _StaticAdapter(ProviderAdapter):
    """Simple provider double used by registry tests."""

    provider: str
    models: list[ModelMetadata]

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:  # noqa: ARG002
        """Return the configured static model list."""
        return list(self.models)


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


def test_create_provider_adapters_from_env_supports_grouped_relays(monkeypatch) -> None:
    """Grouped relay env vars should create one adapter per configured relay group."""
    monkeypatch.setenv("RELAY_API_KEY", "shared-key")
    monkeypatch.setenv("RELAY_BASE_URL", "https://nexus.itssx.com/api/common")
    monkeypatch.setenv("RELAY_ALLOWED_MODELS", "claude-sonnet-4-6")
    monkeypatch.setenv("RELAY_GROUPS", "cc_glm")
    monkeypatch.setenv("RELAY_CC_GLM_BASE_URL", "https://nexus.itssx.com/api/claude_code/cc_glm")
    monkeypatch.setenv("RELAY_CC_GLM_ALLOWED_MODELS", "glm-4.7")

    adapters = create_provider_adapters_from_env(load_env_file=False)

    providers = [adapter.provider for adapter in adapters if adapter.provider.startswith("relay")]
    assert providers == ["relay", "relay_cc_glm"]
    grouped = next(adapter for adapter in adapters if adapter.provider == "relay_cc_glm")
    assert grouped.api_key == "shared-key"
    assert grouped.base_url == "https://nexus.itssx.com/api/claude_code/cc_glm"
    assert grouped.allowed_models == ("glm-4.7",)
    assert "relay_cc_glm" in expected_providers()


def test_model_registry_preserves_mirrored_relay_models_by_channel() -> None:
    """Relay group mirrors should remain available as distinct execution channels."""
    registry = ModelRegistry()
    registry.register_providers(
        [
            _StaticAdapter(
                provider="relay",
                models=[
                    _model("relay:claude-sonnet-4-6", "Claude Sonnet 4.6", 1.0),
                    _model("relay:glm-5", "GLM 5", 1.0),
                    _model("relay:gpt-5.3-codex", "GPT 5.3 Codex", 1.0),
                ],
            ),
            _StaticAdapter(
                provider="relay_cc_glm",
                models=[
                    _model("relay_cc_glm:claude-sonnet-4-6", "Claude Sonnet 4.6", 1.0),
                    _model("relay_cc_glm:glm-5", "GLM 5", 1.0),
                ],
            ),
            _StaticAdapter(
                provider="relay_codex",
                models=[
                    _model("relay_codex:gpt-5.3-codex", "GPT 5.3 Codex", 1.0),
                ],
            ),
        ]
    )

    report = registry.build_report(
        requested_providers=["relay", "relay_cc_glm", "relay_codex"],
        min_total_threshold=1,
    )

    assert sorted(model.model_id for model in report.models) == [
        "relay:claude-sonnet-4-6",
        "relay:glm-5",
        "relay:gpt-5.3-codex",
        "relay_cc_glm:claude-sonnet-4-6",
        "relay_cc_glm:glm-5",
        "relay_codex:gpt-5.3-codex",
    ]
    assert report.total_models == 6
    assert all("deduplicated" not in alert for alert in report.alerts)
