"""Factory helpers for creating provider adapters from environment variables.

Responsibilities:
- Define which providers are expected in one sync run.
- Create adapter instances only when required credentials are configured.
- Keep provider construction logic centralized and easy to audit.
"""

from __future__ import annotations

import logging
import os
import re
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
    RelayProviderAdapter,
)

logger = logging.getLogger(__name__)

# Cloud providers that require API keys.
API_KEY_PROVIDERS: list[str] = ["openai", "deepseek", "google", "anthropic", "groq", "relay"]


def _normalize_relay_group_name(name: str) -> str:
    """Normalize one relay group name into a stable provider suffix."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return normalized


def _relay_groups_from_env() -> list[str]:
    """Return configured relay-group suffixes from RELAY_GROUPS."""
    groups: list[str] = []
    for raw in str(os.getenv("RELAY_GROUPS") or "").split(","):
        group = _normalize_relay_group_name(raw)
        if not group or group in groups:
            continue
        groups.append(group)
    return groups


def _relay_provider_name(group: str) -> str:
    """Build the provider name used for one relay group."""
    normalized = _normalize_relay_group_name(group)
    if not normalized:
        return "relay"
    return f"relay_{normalized}"


def _relay_env_candidates(group: str, suffix: str) -> tuple[str, ...]:
    """Return env-var candidates for one relay config field."""
    normalized = _normalize_relay_group_name(group)
    names: list[str] = []
    if normalized:
        names.append(f"RELAY_{normalized.upper()}_{suffix}")
    names.append(f"RELAY_{suffix}")
    return tuple(names)


def _first_env_value(*names: str) -> str:
    """Return the first non-empty env value from the candidate names."""
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _relay_allowed_models(group: str) -> tuple[str, ...]:
    """Resolve the optional relay allowlist for one group."""
    raw = _first_env_value(*_relay_env_candidates(group, "ALLOWED_MODELS"))
    return tuple(model.strip() for model in raw.split(",") if model.strip())


def expected_providers(include_ollama: bool = False) -> list[str]:
    """Return provider names expected for the current run."""
    providers = list(API_KEY_PROVIDERS)
    for group in _relay_groups_from_env():
        provider = _relay_provider_name(group)
        if provider not in providers:
            providers.append(provider)
    return providers + (["ollama"] if include_ollama else [])


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

    relay_api_key = _first_env_value("RELAY_API_KEY")
    relay_base_url = _first_env_value("RELAY_BASE_URL")
    relay_allowed = _relay_allowed_models("")
    if relay_api_key and relay_base_url:
        adapters.append(
            RelayProviderAdapter(
                api_key=relay_api_key,
                base_url=relay_base_url,
                allowed_models=relay_allowed,
            )
        )
    for group in _relay_groups_from_env():
        provider_name = _relay_provider_name(group)
        if provider_name == "relay":
            continue
        group_api_key = _first_env_value(*_relay_env_candidates(group, "API_KEY"))
        group_base_url = _first_env_value(*_relay_env_candidates(group, "BASE_URL"))
        if not group_api_key or not group_base_url:
            continue
        group_allowed = _relay_allowed_models(group)
        if not group_allowed:
            logger.warning(
                "Relay group %r has no ALLOWED_MODELS configured; all models from this "
                "endpoint will be accepted, including those without pricing/capability data.",
                provider_name,
            )
        adapters.append(
            RelayProviderAdapter(
                api_key=group_api_key,
                base_url=group_base_url,
                allowed_models=group_allowed,
                provider=provider_name,
            )
        )

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

