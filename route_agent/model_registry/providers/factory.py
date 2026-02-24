"""Factory helpers for creating provider adapters from environment variables.

Responsibilities:
- Define which providers are expected in one sync run.
- Create adapter instances only when required credentials are configured.
- Keep provider construction logic centralized and easy to audit.
"""

from __future__ import annotations

import os
from typing import Iterable

from dotenv import load_dotenv

from route_agent.model_registry.providers.base import ProviderAdapter
from route_agent.model_registry.providers.vendors import (
    AnthropicProviderAdapter,
    DeepSeekProviderAdapter,
    GoogleProviderAdapter,
    GroqProviderAdapter,
    OllamaProviderAdapter,
    OpenAIProviderAdapter,
)

# Cloud providers that require API keys.
API_KEY_PROVIDERS: list[str] = ["openai", "deepseek", "google", "anthropic", "groq"]


def expected_providers(include_ollama: bool = False) -> list[str]:
    """Return provider names expected for the current run."""
    return API_KEY_PROVIDERS + (["ollama"] if include_ollama else [])


def create_provider_adapters_from_env(
    load_env_file: bool = True,
    include_ollama: bool = False,
) -> list[ProviderAdapter]:
    """Create adapters from process environment.

    Behavior:
    - Reads `.env` when `load_env_file=True`.
    - Creates cloud adapters only when the corresponding API key exists.
    - Adds Ollama adapter only when `include_ollama=True`.
    """
    if load_env_file:
        load_dotenv()

    adapters: list[ProviderAdapter] = []
    openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    deepseek_api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    google_api_key = (os.getenv("GOOGLE_API_KEY") or "").strip()
    anthropic_api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    groq_api_key = (os.getenv("GROQ_API_KEY") or "").strip()

    if openai_api_key:
        adapters.append(OpenAIProviderAdapter(api_key=openai_api_key))
    if deepseek_api_key:
        adapters.append(DeepSeekProviderAdapter(api_key=deepseek_api_key))
    if google_api_key:
        adapters.append(GoogleProviderAdapter(api_key=google_api_key))
    if anthropic_api_key:
        adapters.append(AnthropicProviderAdapter(api_key=anthropic_api_key))
    if groq_api_key:
        adapters.append(GroqProviderAdapter(api_key=groq_api_key))

    if include_ollama:
        ollama_base_url = (
            (os.getenv("OLLAMA_BASE_URL") or "").strip()
            or (os.getenv("OLLAMA_HOST") or "").strip()
            or "http://localhost:11434"
        )
        adapters.append(OllamaProviderAdapter(base_url=ollama_base_url))

    return adapters


def provider_names(adapters: Iterable[ProviderAdapter]) -> list[str]:
    """Return provider names for logs/diagnostics."""
    return [adapter.provider for adapter in adapters]

