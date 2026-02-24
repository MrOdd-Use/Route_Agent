"""Thread-safe in-memory model registry.

The registry is responsible for:
- storing normalized model metadata;
- coordinating provider adapters;
- producing one aggregated extraction report.

Provider-specific API logic is intentionally delegated to adapter classes.
"""

from __future__ import annotations

from threading import RLock

from route_agent.model_registry.providers.base import ProviderAdapter
from route_agent.model_registry.schemas import (
    ModelMetadata,
    ModelRegistryReport,
    SkippedProvider,
)


class ModelRegistry:
    """Central in-memory registry for models and providers."""

    def __init__(self) -> None:
        # model_id -> model metadata
        """Initialize the instance."""
        self._models: dict[str, ModelMetadata] = {}
        # provider_name -> provider adapter
        self._adapters: dict[str, ProviderAdapter] = {}
        self._lock = RLock()

    def register_provider(self, adapter: ProviderAdapter) -> None:
        """Register or replace one provider adapter."""
        with self._lock:
            self._adapters[adapter.provider] = adapter

    def register_providers(self, adapters: list[ProviderAdapter]) -> None:
        """Register multiple adapters."""
        for adapter in adapters:
            self.register_provider(adapter)

    def list_providers(self) -> list[str]:
        """Return currently registered provider names."""
        with self._lock:
            return list(self._adapters.keys())

    def add_model(self, model: ModelMetadata) -> None:
        """Insert or replace one model entry."""
        with self._lock:
            self._models[model.model_id] = model

    def add_many(self, models: list[ModelMetadata]) -> None:
        """Insert or replace many model entries."""
        with self._lock:
            for model in models:
                self._models[model.model_id] = model

    def get_model(self, model_id: str) -> ModelMetadata | None:
        """Get one model by exact ID."""
        with self._lock:
            return self._models.get(model_id)

    def list_models(self, provider: str | None = None) -> list[ModelMetadata]:
        """List all models, optionally filtered by provider."""
        with self._lock:
            if provider is None:
                return list(self._models.values())
            return [m for m in self._models.values() if m.provider == provider]

    def clear(self) -> None:
        """Clear all in-memory model entries."""
        with self._lock:
            self._models.clear()

    def refresh_provider(self, provider: str, limit: int = 8) -> list[ModelMetadata]:
        """Refresh one provider and merge results into registry state.

        Locking strategy:
        - grab adapter reference under lock;
        - perform network call outside lock;
        - write results back under lock via `add_many`.
        """
        with self._lock:
            adapter = self._adapters.get(provider)
        if adapter is None:
            raise KeyError(f"Provider '{provider}' is not registered")

        # Adapter call may do network I/O; keep it outside lock to reduce contention.
        models = adapter.fetch_latest_models(limit=limit)
        self.add_many(models)
        return models

    def refresh_all(self, limit: int = 8) -> dict[str, list[ModelMetadata]]:
        """Refresh all registered providers and return grouped results."""
        with self._lock:
            providers = list(self._adapters.keys())

        output: dict[str, list[ModelMetadata]] = {}
        for provider in providers:
            output[provider] = self.refresh_provider(provider=provider, limit=limit)
        return output

    def build_report(
        self,
        requested_providers: list[str],
        limit: int = 8,
        min_total_threshold: int = 5,
    ) -> ModelRegistryReport:
        """Build one report from requested providers.

        Behavior:
        - providers requested but not configured are recorded in `skipped_providers`;
        - provider refresh failures are recorded in `errors`;
        - failures do not stop processing other providers.
        """
        report = ModelRegistryReport()
        report.requested_providers = requested_providers

        # Snapshot configured providers once to make report fields consistent.
        configured = self.list_providers()
        report.configured_providers = configured

        for provider in requested_providers:
            if provider not in configured:
                reason = "provider is not configured (possibly missing API key or not enabled)"
                report.skipped_providers.append(
                    SkippedProvider(provider=provider, reason=reason)
                )

        for provider in configured:
            try:
                models = self.refresh_provider(provider=provider, limit=limit)
                report.models.extend(models)
            except Exception as exc:  # noqa: BLE001
                # Continue collecting from other providers; aggregate failures in report.
                report.errors[provider] = str(exc)

        report.total_models = len(report.models)
        if report.total_models < min_total_threshold:
            report.alerts.append(
                (
                    f"Total extracted models is {report.total_models}, which is below "
                    f"threshold {min_total_threshold}. Please check API keys and "
                    "provider connectivity."
                )
            )

        return report

