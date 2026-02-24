"""Schema definitions for normalized model-registry data.

This module contains pure data structures only. No network access and no
database logic should live here. All fields are designed to be JSON-friendly
for API responses and snapshot persistence.
"""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ModelMetadata:
    """Normalized metadata for one model entry.

    Field conventions:
    - `model_id`: globally unique key, usually `provider:model_name`.
    - `api_model_name`: exact model name used in provider API requests.
    - Block fields (`endpoint`, `auth`, `pricing`, `limits`, ...) are kept as
      dictionaries to make schema evolution easier.
    """

    model_id: str
    display_name: str
    provider: str
    api_model_name: str
    endpoint: dict[str, Any] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    pricing: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a deep-converted dictionary representation."""
        return asdict(self)


@dataclass(slots=True, frozen=True)
class SkippedProvider:
    """Information about one provider that was requested but skipped."""

    provider: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-ready dictionary representation."""
        return asdict(self)


@dataclass(slots=True)
class ModelRegistryReport:
    """Aggregated output of one registry extraction run."""

    requested_providers: list[str] = field(default_factory=list)
    configured_providers: list[str] = field(default_factory=list)
    skipped_providers: list[SkippedProvider] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    models: list[ModelMetadata] = field(default_factory=list)
    total_models: int = 0
    alerts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return full report payload in JSON-compatible structure."""
        return {
            "requested_providers": self.requested_providers,
            "configured_providers": self.configured_providers,
            "skipped_providers": [item.to_dict() for item in self.skipped_providers],
            "errors": self.errors,
            "total_models": self.total_models,
            "alerts": self.alerts,
            "models": [model.to_dict() for model in self.models],
        }

