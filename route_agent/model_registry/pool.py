"""Main model pool built from model registry report.

This module converts a flat `ModelRegistryReport.models` list into an in-memory
index of available models for routing, lookup, and lightweight diagnostics.
"""

from __future__ import annotations

from typing import Any

from route_agent.model_registry.schemas import ModelMetadata, ModelRegistryReport


def _is_model_available(model: ModelMetadata) -> bool:
    """Treat unknown status as available; only explicit bad states are filtered."""
    availability = str(model.status.get("availability") or "").strip().lower()
    if not availability:
        return True
    return availability not in {"down", "unavailable", "error", "offline"}


class MainModelPool:
    """In-memory pool with model lookup and availability filtering."""

    def __init__(
        self,
        models: list[ModelMetadata],
    ) -> None:
        # Fast lookup by model_id for direct preference resolution.
        """Initialize the instance."""
        self._models_by_id: dict[str, ModelMetadata] = {m.model_id: m for m in models}
        # Grouped view is kept for potential provider-aware introspection.
        self._models_by_provider: dict[str, list[ModelMetadata]] = {}
        for model in models:
            self._models_by_provider.setdefault(model.provider, []).append(model)

        # Availability filtering is done once at construction time.
        self._available_models = [m for m in models if _is_model_available(m)]

    @classmethod
    def from_report(cls, report: ModelRegistryReport) -> "MainModelPool":
        """Convenience constructor from registry report."""
        return cls(report.models)

    def get(self, model_id: str) -> ModelMetadata | None:
        """Get one model by exact model_id."""
        return self._models_by_id.get(model_id)

    def list_available(self, provider: str | None = None) -> list[ModelMetadata]:
        """List available models, optionally filtered by provider."""
        if provider is None:
            return list(self._available_models)
        return [m for m in self._available_models if m.provider == provider]

    def summary(self) -> dict[str, Any]:
        """Return lightweight pool diagnostics for logging/API responses."""
        return {
            "total_models": len(self._models_by_id),
            "available_models": len(self._available_models),
            "providers": sorted(self._models_by_provider.keys()),
        }
