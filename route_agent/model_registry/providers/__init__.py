"""Public exports for model-provider adapter components.

This package exposes:
- the adapter interface (`ProviderAdapter`);
- adapter factory helpers;
- vendor adapter implementations.
"""

from route_agent.model_registry.providers.base import ProviderAdapter
from route_agent.model_registry.providers.factory import (
    API_KEY_PROVIDERS,
    create_provider_adapters_from_env,
    expected_providers,
    provider_names,
)
from route_agent.model_registry.providers.vendors import (
    AnthropicProviderAdapter,
    DeepSeekProviderAdapter,
    GoogleProviderAdapter,
    GroqProviderAdapter,
    OllamaProviderAdapter,
    OpenAIProviderAdapter,
)

__all__ = [
    "ProviderAdapter",
    "OpenAIProviderAdapter",
    "DeepSeekProviderAdapter",
    "GoogleProviderAdapter",
    "AnthropicProviderAdapter",
    "GroqProviderAdapter",
    "OllamaProviderAdapter",
    "API_KEY_PROVIDERS",
    "create_provider_adapters_from_env",
    "expected_providers",
    "provider_names",
]

