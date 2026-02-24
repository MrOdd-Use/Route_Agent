"""Provider adapter abstraction for model discovery.

This module defines the only contract the registry depends on for provider
integration. Vendor-specific code must stay in adapter implementations so the
registry/service/storage layers remain independent from concrete APIs.
"""

from abc import ABC, abstractmethod

from route_agent.model_registry.schemas import ModelMetadata


class ProviderAdapter(ABC):
    """Base class for all model-provider adapters.

    Implementation rules:
    - `provider` must be a stable identifier (for example: `openai`).
    - `fetch_latest_models` must return normalized `ModelMetadata` objects.
    - Raw secrets should never be returned in clear text.
    """

    provider: str

    @abstractmethod
    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        """Fetch latest models from one provider.

        Args:
            limit: Maximum number of models to return.

        Returns:
            A list of normalized model metadata objects.
        """

