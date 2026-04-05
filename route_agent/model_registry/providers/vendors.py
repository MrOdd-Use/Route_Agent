"""Unified provider adapters module.

This file centralizes all provider adapters into one module while preserving
independent adapter classes per vendor.

Module layout:
1) Shared helpers
2) Cloud providers:
   - OpenAI
   - DeepSeek
   - Google
   - Anthropic
   - Groq
3) Local provider:
   - Ollama

Design goals:
- Keep external behavior unchanged after file consolidation.
- Keep each adapter's API-specific logic isolated and explicit.
- Reuse shared normalization utilities from `providers.utils`.
"""

from __future__ import annotations

# Detailed notes:
# - Every adapter follows the same workflow:
#   1) call provider model-list endpoint;
#   2) parse response headers for optional rate-limit hints;
#   3) normalize each model into `ModelMetadata`.
# - Adapters should avoid provider SDK lock-in and use plain HTTP for
#   predictable behavior.
# - Partial provider-specific fields are tolerated; missing values are filled
#   by defaults in `providers.utils`.

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from route_agent.model_registry.constants import DEFAULT_REQUEST_TIMEOUT_SECONDS
# Shared registry abstractions and normalization helpers.
from route_agent.model_registry.providers.base import ProviderAdapter
from route_agent.model_registry.providers.utils import (
    build_model_metadata,
    default_limits,
    default_status,
    merge_limits_from_response_headers,
    safe_int,
)
from route_agent.model_registry.schemas import ModelMetadata


# ============================================================================
# Shared Helpers
# ============================================================================

def _parse_iso_datetime_to_timestamp(value: str | None) -> float:
    """Convert ISO-8601 datetime string to sortable timestamp.

    
    -  ISO  `Z` 
    -  float 
    -  0
    """
    if not value:
        return 0.0
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


# ============================================================================
# Cloud Providers
# ============================================================================

@dataclass(slots=True)
class OpenAIProviderAdapter(ProviderAdapter):
    """Fetch latest models from OpenAI `/v1/models`."""

    api_key: str
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "openai"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        # Step 1) Fetch provider model list.
        # Step 2) Parse rate-limit headers once at response level.
        # Step 3) Sort newest first by `created`.
        # Step 4) Normalize to unified `ModelMetadata`.
        """Execute `fetch_latest_models`."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/models",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        limits_from_headers = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("data", [])
        rows.sort(key=lambda x: x.get("created", 0) or 0, reverse=True)

        output: list[ModelMetadata] = []
        for row in rows[:limit]:
            model_name = row.get("id")
            if not model_name:
                continue
            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=model_name,
                    display_name=model_name,
                    endpoint_base_url=self.base_url,
                    api_key=self.api_key,
                    limits=limits_from_headers,
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["api", "cloud"]},
                )
            )
        return output


@dataclass(slots=True)
class DeepSeekProviderAdapter(ProviderAdapter):
    """Fetch latest models from DeepSeek `/v1/models` (OpenAI-compatible)."""

    api_key: str
    base_url: str = "https://api.deepseek.com/v1"
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "deepseek"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        # DeepSeek provides an OpenAI-compatible API surface.
        # Therefore request structure, list payload format, and header parsing
        # can follow the same pattern as OpenAI.
        """Execute `fetch_latest_models`."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/models",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        limits_from_headers = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("data", [])
        rows.sort(key=lambda x: x.get("created", 0) or 0, reverse=True)

        _deepseek_capabilities: dict[str, dict[str, int]] = {
            "deepseek-chat":     {"text": 82, "code": 86, "search": 62, "math": 86, "instruction_following": 82, "creative_writing": 76, "vision": 38},
            "deepseek-reasoner": {"text": 80, "code": 84, "search": 58, "math": 92, "instruction_following": 80, "creative_writing": 70, "vision": 35},
        }

        output: list[ModelMetadata] = []
        for row in rows[:limit]:
            model_name = row.get("id")
            if not model_name:
                continue
            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=model_name,
                    display_name=model_name,
                    endpoint_base_url=self.base_url,
                    api_key=self.api_key,
                    capabilities=_deepseek_capabilities.get(model_name),
                    limits=limits_from_headers,
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["api", "cloud"]},
                )
            )
        return output


@dataclass(slots=True)
class GoogleProviderAdapter(ProviderAdapter):
    """Fetch latest models from Google Generative Language API."""

    api_key: str
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "google"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        # Google uses query-parameter auth (`key=...`) for model listing.
        """Execute `fetch_latest_models`."""
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/models",
            params={"key": self.api_key},
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        header_limits = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("models", [])
        # Google model list does not always use one stable timestamp field.
        # Prefer `updateTime`, then fallback to `version`.
        rows.sort(
            key=lambda x: _parse_iso_datetime_to_timestamp(x.get("updateTime"))
            or _parse_iso_datetime_to_timestamp(x.get("version")),
            reverse=True,
        )

        output: list[ModelMetadata] = []
        for row in rows[:limit]:
            full_name = row.get("name", "")
            if not full_name:
                continue
            # API returns names as "models/<model-name>"; normalize to short id.
            api_model_name = full_name.split("/", 1)[-1]
            display_name = row.get("displayName") or api_model_name

            # Populate per-model static limits when available from payload.
            limits = default_limits()
            limits["context_length"] = safe_int(row.get("inputTokenLimit"))
            limits["max_output_tokens"] = safe_int(row.get("outputTokenLimit"))
            # Then merge dynamic rate-limit fields extracted from headers.
            limits = merge_limits_from_response_headers(
                base_limits=limits,
                response_headers=resp.headers,
            )
            # Fallback to response-level header values if model-level fields
            # remain unavailable after merge.
            if limits["max_requests_per_minute"] is None:
                limits["max_requests_per_minute"] = header_limits["max_requests_per_minute"]
            if limits["max_tokens_per_minute"] is None:
                limits["max_tokens_per_minute"] = header_limits["max_tokens_per_minute"]
            if limits["max_concurrency"] is None:
                limits["max_concurrency"] = header_limits["max_concurrency"]

            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=api_model_name,
                    display_name=display_name,
                    endpoint_base_url=self.base_url,
                    api_key=self.api_key,
                    limits=limits,
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["api", "cloud"]},
                )
            )
        return output


@dataclass(slots=True)
class AnthropicProviderAdapter(ProviderAdapter):
    """Fetch latest models from Anthropic `/v1/models`."""

    api_key: str
    base_url: str = "https://api.anthropic.com/v1"
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "anthropic"
    api_version: str = "2023-06-01"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        # Anthropic requires both API key and API version headers.
        """Execute `fetch_latest_models`."""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.api_version,
        }
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/models",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        limits_from_headers = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("data", [])
        rows.sort(
            key=lambda x: _parse_iso_datetime_to_timestamp(x.get("created_at")),
            reverse=True,
        )

        output: list[ModelMetadata] = []
        for row in rows[:limit]:
            model_name = row.get("id")
            if not model_name:
                continue
            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=model_name,
                    display_name=row.get("display_name") or model_name,
                    endpoint_base_url=self.base_url,
                    api_key=self.api_key,
                    limits=limits_from_headers,
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["api", "cloud"]},
                )
            )
        return output


@dataclass(slots=True)
class GroqProviderAdapter(ProviderAdapter):
    """Fetch latest models from Groq `/openai/v1/models`."""

    api_key: str
    base_url: str = "https://api.groq.com/openai/v1"
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "groq"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        # Groq model-list endpoint follows OpenAI-compatible shape.
        """Execute `fetch_latest_models`."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/models",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        limits_from_headers = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("data", [])
        rows.sort(key=lambda x: x.get("created", 0) or 0, reverse=True)

        output: list[ModelMetadata] = []
        for row in rows[:limit]:
            model_name = row.get("id")
            if not model_name:
                continue
            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=model_name,
                    display_name=model_name,
                    endpoint_base_url=self.base_url,
                    api_key=self.api_key,
                    limits=limits_from_headers,
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["api", "cloud"]},
                )
            )
        return output


# ============================================================================
# Local Provider
# ============================================================================

@dataclass(slots=True)
class OllamaProviderAdapter(ProviderAdapter):
    """Fetch latest models from local Ollama `/api/tags`."""

    base_url: str = "http://localhost:11434"
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "ollama"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:
        """Execute `fetch_latest_models`."""
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/api/tags",
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        # Ollama usually does not expose cloud-style rate-limit headers.
        # Still parse them for compatibility with proxy/gateway deployments.
        header_limits = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("models", [])
        rows.sort(
            key=lambda x: _parse_iso_datetime_to_timestamp(x.get("modified_at")),
            reverse=True,
        )

        output: list[ModelMetadata] = []
        for row in rows[:limit]:
            model_name = row.get("model") or row.get("name")
            if not model_name:
                continue

            limits = default_limits()
            details = row.get("details", {})
            # Context length may be absent for some local model manifests.
            limits["context_length"] = details.get("context_length")
            limits = merge_limits_from_response_headers(
                base_limits=limits,
                response_headers=resp.headers,
            )
            # Fallback to response-level header limits when fields are missing.
            if limits["max_requests_per_minute"] is None:
                limits["max_requests_per_minute"] = header_limits["max_requests_per_minute"]
            if limits["max_tokens_per_minute"] is None:
                limits["max_tokens_per_minute"] = header_limits["max_tokens_per_minute"]
            if limits["max_concurrency"] is None:
                limits["max_concurrency"] = header_limits["max_concurrency"]

            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=model_name,
                    display_name=row.get("name") or model_name,
                    endpoint_base_url=self.base_url,
                    api_key=None,
                    limits=limits,
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["local", "on_device"]},
                    auth_type="none",
                )
            )
        return output


@dataclass(slots=True)
class RelayProviderAdapter(ProviderAdapter):
    """Fetch models from an OpenAI-compatible relay, optionally filtered by allowlist."""

    api_key: str
    base_url: str = "https://nexusacc.itssx.com/api/claude_code/mixedcc_openclaw"
    allowed_models: tuple[str, ...] = ()
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS
    provider: str = "relay"

    def fetch_latest_models(self, limit: int = 8) -> list[ModelMetadata]:  # noqa: ARG002
        """Fetch and filter models from relay /models endpoint."""
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/models",
            headers=headers,
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        limits_from_headers = merge_limits_from_response_headers(
            base_limits=default_limits(),
            response_headers=resp.headers,
        )

        payload = resp.json()
        rows: list[dict[str, Any]] = payload.get("data", [])

        _pricing: dict[str, float] = {
            "claude-haiku-4-5": 0.5,
            "claude-haiku-4-5-20251001": 0.5,
            "claude-sonnet-4-5": 2.0,
            "claude-sonnet-4-5-20250929": 2.0,
            "claude-sonnet-4-6": 3.0,
            "gemini-3-flash-preview": 0.6,
            "gemini-3-pro-preview": 1.0,
            "gemini-3.1-pro-preview": 1.0,
            "glm-4.7": 0.3,
            "gpt-5.2": 0.5,
            "gpt-5.2-codex": 0.5,
            "gpt-5.3-codex": 0.8,
            "gpt-5.4": 1.0,
            "gpt-5.4-mini": 0.8,
            "gpt-5.4-nano": 0.8,
            "deepseek-v3.2": 0.5,
            "kimi-k2-thinking": 0.6,
            "kimi-k2.5": 0.6,
        }

        _capabilities: dict[str, dict[str, int]] = {
            "claude-haiku-4-5":           {"text": 78, "code": 78, "search": 60, "math": 68, "instruction_following": 80, "creative_writing": 72, "vision": 60},
            "claude-haiku-4-5-20251001":  {"text": 78, "code": 78, "search": 60, "math": 68, "instruction_following": 80, "creative_writing": 72, "vision": 60},
            "claude-sonnet-4-5":          {"text": 88, "code": 90, "search": 68, "math": 80, "instruction_following": 88, "creative_writing": 83, "vision": 73},
            "claude-sonnet-4-5-20250929": {"text": 88, "code": 90, "search": 68, "math": 80, "instruction_following": 88, "creative_writing": 83, "vision": 73},
            "claude-sonnet-4-6":          {"text": 90, "code": 92, "search": 70, "math": 82, "instruction_following": 90, "creative_writing": 85, "vision": 75},
            "gemini-3-flash-preview":     {"text": 80, "code": 76, "search": 72, "math": 78, "instruction_following": 80, "creative_writing": 74, "vision": 82},
            "gemini-3-pro-preview":       {"text": 87, "code": 83, "search": 78, "math": 86, "instruction_following": 85, "creative_writing": 80, "vision": 88},
            "gemini-3.1-pro-preview":     {"text": 89, "code": 85, "search": 80, "math": 88, "instruction_following": 87, "creative_writing": 82, "vision": 90},
            "glm-4.7":                    {"text": 76, "code": 74, "search": 60, "math": 72, "instruction_following": 76, "creative_writing": 70, "vision": 55},
            "gpt-5.2":                    {"text": 80, "code": 82, "search": 65, "math": 78, "instruction_following": 82, "creative_writing": 75, "vision": 70},
            "gpt-5.2-codex":              {"text": 78, "code": 88, "search": 62, "math": 80, "instruction_following": 80, "creative_writing": 70, "vision": 58},
            "gpt-5.3-codex":              {"text": 80, "code": 90, "search": 65, "math": 82, "instruction_following": 82, "creative_writing": 72, "vision": 60},
            "gpt-5.4":                    {"text": 88, "code": 88, "search": 75, "math": 88, "instruction_following": 88, "creative_writing": 84, "vision": 82},
            "gpt-5.4-mini":               {"text": 82, "code": 84, "search": 70, "math": 82, "instruction_following": 84, "creative_writing": 78, "vision": 75},
            "gpt-5.4-nano":               {"text": 74, "code": 76, "search": 62, "math": 72, "instruction_following": 76, "creative_writing": 68, "vision": 65},
            "deepseek-v3.2":              {"text": 84, "code": 88, "search": 65, "math": 88, "instruction_following": 84, "creative_writing": 78, "vision": 40},
            "kimi-k2-thinking":           {"text": 82, "code": 86, "search": 68, "math": 90, "instruction_following": 82, "creative_writing": 76, "vision": 50},
            "kimi-k2.5":                  {"text": 80, "code": 84, "search": 66, "math": 86, "instruction_following": 80, "creative_writing": 74, "vision": 48},
        }

        output: list[ModelMetadata] = []
        for row in rows:
            model_name = row.get("id") or ""
            if self.allowed_models and model_name not in self.allowed_models:
                continue
            price = _pricing.get(model_name, 0.5)
            output.append(
                build_model_metadata(
                    provider=self.provider,
                    api_model_name=model_name,
                    display_name=row.get("name") or model_name,
                    endpoint_base_url=self.base_url,
                    api_key=self.api_key,
                    capabilities=_capabilities.get(model_name),
                    limits={**limits_from_headers, "context_length": 200000},
                    status=default_status("healthy"),
                    routing={"recommended_for": [], "tags": ["api", "cloud", "relay"], "release_date": "2025-03-29"},
                    pricing={"currency": "USD", "unit": "per_1m_tokens", "input": price, "output": price},
                )
            )
        return output
