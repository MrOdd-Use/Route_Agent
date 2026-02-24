"""Shared helpers for provider adapters."""

from __future__ import annotations

# Detailed notes:
# - This module is the normalization hub used by all provider adapters.
# - It defines default schema blocks, static quota/pricing policy, and dynamic
#   pricing fallback behavior.
# - Keep provider-specific API logic out of this file; only generic
#   normalization and policy decisions should stay here.

from datetime import datetime, timezone
import hashlib
import logging
import os
import re
from threading import RLock
from typing import Any, Mapping

from route_agent.model_registry.constants import (
    DEFAULT_MAX_CONCURRENCY,
    DEFAULT_REQUESTS_PER_DAY,
    DEFAULT_REQUESTS_PER_MINUTE,
    DEFAULT_TOKENS_PER_MINUTE,
    PRICING_DEEPSEEK_CHAT_BASE,
    PRICING_DEEPSEEK_REASONER_BASE,
    PRICING_FLASH_BASE,
    PRICING_PRO_BASE,
)
from route_agent.model_registry.pricing import create_dynamic_pricing_resolver
from route_agent.model_registry.schemas import ModelMetadata

logger = logging.getLogger(__name__)


#
_DYNAMIC_PRICING_RESOLVER = None
_DYNAMIC_PRICING_LOCK = RLock()


def utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def key_fingerprint(api_key: str | None) -> str | None:
    """Return short API key fingerprint without leaking the original key."""
    if not api_key:
        return None
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:4]}"


def default_capabilities() -> dict[str, Any]:
    """Default capabilities block aligned with `.md`."""
    return {
        "text": None,
        "code": None,
        "search": None,
        "math": None,
        "instruction_following": None,
        "creative_writing": None,
        "vision": None,
    }


def default_pricing() -> dict[str, Any]:
    """Default pricing block aligned with `.md`."""
    return {
        "currency": "USD",
        "unit": "per_1k_tokens",
        "input": None,
        "output": None,
    }


def default_limits() -> dict[str, Any]:
    """Default limits block aligned with `.md`."""
    return {
        "context_length": None,
        "max_output_tokens": None,
        "timeout_ms": None,
        "max_concurrency": None,
        "max_requests_per_minute": None,
        "max_tokens_per_minute": None,
        "max_requests_per_day": None,
    }


def default_status(availability: str = "unknown") -> dict[str, Any]:
    """Default status block aligned with `.md`."""
    return {
        "availability": availability,
        "latency_ms_p95": None,
        "success_rate_5m": None,
        "last_checked_at": utc_now_iso(),
    }


def default_routing() -> dict[str, Any]:
    """Default routing block aligned with `.md`."""
    return {
        "recommended_for": [],
        "tags": [],
    }


def build_model_metadata(
    provider: str,
    api_model_name: str,
    display_name: str | None,
    endpoint_base_url: str,
    api_key: str | None,
    capabilities: dict[str, Any] | None = None,
    pricing: dict[str, Any] | None = None,
    limits: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
    routing: dict[str, Any] | None = None,
    auth_type: str = "api_key",
) -> ModelMetadata:
    """Build one normalized model metadata object."""
    #  limits
    raw_limits = limits or default_limits()
    normalized_limits = apply_static_quota_policy(
        provider=provider,
        api_model_name=api_model_name,
        limits=raw_limits,
    )
    # 
    raw_pricing = pricing or default_pricing()
    normalized_pricing = apply_pricing_policy(
        provider=provider,
        api_model_name=api_model_name,
        pricing=raw_pricing,
    )

    return ModelMetadata(
        model_id=f"{provider}:{api_model_name}",
        display_name=display_name or api_model_name,
        provider=provider,
        api_model_name=api_model_name,
        endpoint={"base_url": endpoint_base_url},
        auth={
            "auth_configured": bool(api_key),
            "auth_type": auth_type if api_key else None,
            "key_fingerprint": key_fingerprint(api_key),
        },
        capabilities=capabilities or default_capabilities(),
        pricing=normalized_pricing,
        limits=normalized_limits,
        status=status or default_status(availability="healthy"),
        routing=routing or default_routing(),
    )


def safe_int(value: Any) -> int | None:
    """Convert value to int safely."""
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize response headers to a lowercase string dictionary.

    
    - HTTP header key 
    - requests/httpxheader 
    -  `dict[str, str]` 
    """
    if not headers:
        return {}
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        normalized[str(key).lower()] = str(value)
    return normalized


def _extract_first_int(value: str | None) -> int | None:
    """Extract the first integer from a header value.

    
    - `"5000"` -> 5000
    - `"5000;w=60"` -> 5000
    - `"limit=5000,remaining=4990"` -> 5000
    """
    if not value:
        return None
    match = re.search(r"\d+", value)
    if not match:
        return None
    return safe_int(match.group(0))


def _pick_header_int(
    normalized_headers: Mapping[str, str],
    header_candidates: list[str],
) -> int | None:
    """Pick the first parseable integer from candidate header names."""
    for key in header_candidates:
        value = normalized_headers.get(key.lower())
        parsed = _extract_first_int(value)
        if parsed is not None:
            return parsed
    return None


def merge_limits_from_response_headers(
    base_limits: dict[str, Any] | None,
    response_headers: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge provider limits from response headers into `limits` block.

    
    - `base_limits`  context_length
    - 
      - `max_requests_per_minute`
      - `max_tokens_per_minute`
      - `max_concurrency`
    -  `None`

     header
    - 
      `x-ratelimit-limit-requests`, `ratelimit-limit-requests`,
      `x-ratelimit-requests-limit`, `anthropic-ratelimit-requests-limit`
    - Token 
      `x-ratelimit-limit-tokens`, `ratelimit-limit-tokens`,
      `anthropic-ratelimit-tokens-limit`, `x-ratelimit-limit-tpm`
    - 
      `x-ratelimit-limit-concurrency`, `x-ratelimit-limit-concurrent-requests`,
      `anthropic-ratelimit-concurrent-requests-limit`
    """
    limits = dict(base_limits or default_limits())
    headers = _normalize_headers(response_headers)

    #  + 
    request_limit_headers = [
        "x-ratelimit-limit-requests",
        "ratelimit-limit-requests",
        "x-ratelimit-requests-limit",
        "anthropic-ratelimit-requests-limit",
        "x-ratelimit-limit-requests-1m",
        "x-ratelimit-limit",
    ]
    token_limit_headers = [
        "x-ratelimit-limit-tokens",
        "ratelimit-limit-tokens",
        "anthropic-ratelimit-tokens-limit",
        "x-ratelimit-limit-tokens-1m",
        "x-ratelimit-limit-tpm",
    ]
    concurrency_limit_headers = [
        "x-ratelimit-limit-concurrency",
        "x-ratelimit-limit-concurrent-requests",
        "anthropic-ratelimit-concurrent-requests-limit",
        "x-concurrency-limit",
    ]
    request_limit_day_headers = [
        "x-ratelimit-limit-requests-day",
        "x-ratelimit-limit-requests-1d",
        "ratelimit-limit-requests-day",
        "ratelimit-limit-requests-1d",
    ]

    max_requests_per_minute = _pick_header_int(headers, request_limit_headers)
    max_tokens_per_minute = _pick_header_int(headers, token_limit_headers)
    max_concurrency = _pick_header_int(headers, concurrency_limit_headers)
    max_requests_per_day = _pick_header_int(headers, request_limit_day_headers)

    if max_requests_per_minute is not None:
        limits["max_requests_per_minute"] = max_requests_per_minute
    if max_tokens_per_minute is not None:
        limits["max_tokens_per_minute"] = max_tokens_per_minute
    if max_concurrency is not None:
        limits["max_concurrency"] = max_concurrency
    if max_requests_per_day is not None:
        limits["max_requests_per_day"] = max_requests_per_day

    return limits


def _resolve_quota_profile(provider: str, api_model_name: str) -> str | None:
    """Resolve quota profile (`flash` / `pro`) for one model.

    
    - Gemini: `flash*`  flash `pro*`  pro 
    - GPT: `mini`/`flash`  flash pro
    - DeepSeek: `deepseek-chat`  flash`reason`  pro
    """
    provider_key = provider.lower()
    model_key = api_model_name.lower()

    if provider_key == "google":
        # Gemini exp flash 
        if "exp" in model_key:
            return "flash"
        if "flash" in model_key:
            return "flash"
        if "pro" in model_key:
            return "pro"
        return None

    if provider_key == "openai":
        if "mini" in model_key or "flash" in model_key:
            return "flash"
        return "pro"

    if provider_key == "deepseek":
        if "chat" in model_key:
            return "flash"
        if "reason" in model_key:
            return "pro"
        return None

    return None


def _resolve_pricing_profile(provider: str, api_model_name: str) -> str | None:
    """Resolve pricing profile (`flash` / `pro`) for tiered pricing.

    
    -  flash pro
    -  `_resolve_quota_profile` 
    """
    return _resolve_quota_profile(provider=provider, api_model_name=api_model_name)


def _is_dynamic_pricing_enabled() -> bool:
    """Return whether dynamic pricing is enabled.

    
    - `ENABLE_DYNAMIC_PRICING=1/true/yes/on`
    - 
    """
    raw = (os.getenv("ENABLE_DYNAMIC_PRICING") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _get_dynamic_pricing_resolver():
    """Get lazily initialized dynamic pricing resolver singleton."""
    global _DYNAMIC_PRICING_RESOLVER
    if _DYNAMIC_PRICING_RESOLVER is not None:
        return _DYNAMIC_PRICING_RESOLVER
    with _DYNAMIC_PRICING_LOCK:
        if _DYNAMIC_PRICING_RESOLVER is None:
            _DYNAMIC_PRICING_RESOLVER = create_dynamic_pricing_resolver()
    return _DYNAMIC_PRICING_RESOLVER


def apply_static_pricing_policy(
    provider: str,
    api_model_name: str,
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply static tier pricing policy requested by user.

    USD / 1M tokens
    - GPT 
      - flash: 0.20
      - pro:   0.30
    - DeepSeek -0.10
    - Geminigoogle +0.05

    
    - openai flash/pro:   0.20 / 0.30
    - deepseek flash/pro: 0.10 / 0.20
    - google flash/pro:   0.25 / 0.35

    
    - `unit`  `per_1m_tokens`
    - `input`  `output`  token 
    -  openai/deepseek/google  provider 
    """
    merged_pricing = dict(pricing or default_pricing())
    provider_key = provider.lower()
    if provider_key not in {"openai", "deepseek", "google"}:
        return merged_pricing

    profile = _resolve_pricing_profile(provider=provider, api_model_name=api_model_name)
    if profile not in {"flash", "pro"}:
        # 
        return merged_pricing

    base_price_per_1m = PRICING_FLASH_BASE if profile == "flash" else PRICING_PRO_BASE
    provider_delta = 0.0
    if provider_key == "deepseek":
        provider_delta = PRICING_DEEPSEEK_CHAT_BASE - PRICING_FLASH_BASE
    elif provider_key == "google":
        provider_delta = 0.05

    #  round  0.199999999 
    final_price = round(max(0.0, base_price_per_1m + provider_delta), 3)
    merged_pricing["currency"] = "USD"
    merged_pricing["unit"] = "per_1m_tokens"
    merged_pricing["input"] = final_price
    merged_pricing["output"] = final_price
    merged_pricing["tier"] = profile
    return merged_pricing


def apply_pricing_policy(
    provider: str,
    api_model_name: str,
    pricing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply pricing policy with dynamic-first and static fallback behavior.

    
    1) 
    2) flash/pro
    3)  input/output `source=dynamic`
        `source=static`
    """
    static_pricing = apply_static_pricing_policy(
        provider=provider,
        api_model_name=api_model_name,
        pricing=pricing,
    )
    static_pricing["source"] = "static"
    static_pricing["updated_at"] = None
    static_pricing["source_url"] = None

    if not _is_dynamic_pricing_enabled():
        return static_pricing

    profile = _resolve_pricing_profile(provider=provider, api_model_name=api_model_name)
    if profile not in {"flash", "pro"}:
        return static_pricing

    try:
        resolver = _get_dynamic_pricing_resolver()
        dynamic_price = resolver.get_price(provider=provider, profile=profile)
        # 
        if dynamic_price is None or not (0.0 < float(dynamic_price) <= 100.0):
            return static_pricing

        meta = resolver.get_price_meta(provider=provider, profile=profile)
        merged = dict(static_pricing)
        merged["currency"] = "USD"
        merged["unit"] = "per_1m_tokens"
        merged["input"] = round(float(dynamic_price), 4)
        merged["output"] = round(float(dynamic_price), 4)
        merged["tier"] = profile
        merged["source"] = "dynamic"
        merged["updated_at"] = meta.get("updated_at")
        merged["source_url"] = meta.get("source_url")
        return merged
    except Exception:
        # Dynamic pricing failed; fall back to static pricing.
        logger.debug("Dynamic pricing lookup failed, using static pricing", exc_info=True)
        return static_pricing


def apply_static_quota_policy(
    provider: str,
    api_model_name: str,
    limits: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply static quota policy requested by user.

    
    - TPM=250000
    - flash RPM=10, RPD=60, max_concurrency=5
    - pro RPM=5, RPD=30, max_concurrency=3
    -  pro max_concurrency=3

    
    -  header  RPM/RPD/TPM/
      
    """
    merged_limits = dict(limits or default_limits())
    profile = _resolve_quota_profile(provider=provider, api_model_name=api_model_name)

    # Default TPM
    merged_limits["max_tokens_per_minute"] = DEFAULT_TOKENS_PER_MINUTE

    if profile == "flash":
        merged_limits["max_requests_per_minute"] = DEFAULT_REQUESTS_PER_MINUTE
        merged_limits["max_requests_per_day"] = DEFAULT_REQUESTS_PER_DAY
        merged_limits["max_concurrency"] = DEFAULT_MAX_CONCURRENCY
    elif profile == "pro":
        merged_limits["max_requests_per_minute"] = DEFAULT_REQUESTS_PER_MINUTE // 2
        merged_limits["max_requests_per_day"] = DEFAULT_REQUESTS_PER_DAY // 2
        merged_limits["max_concurrency"] = DEFAULT_MAX_CONCURRENCY - 2
    else:
        # Non-flash/pro tiers get conservative default concurrency
        merged_limits["max_concurrency"] = DEFAULT_MAX_CONCURRENCY - 2
    return merged_limits
