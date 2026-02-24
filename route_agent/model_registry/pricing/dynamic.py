"""Dynamic pricing resolver for model registry.

This module performs best-effort dynamic price extraction from provider pricing
pages, caches results in-process, and exposes a small query API used by
normalization helpers. Static pricing must remain available as fallback.
"""

from __future__ import annotations

# Detailed notes:
# - This resolver is best-effort by design: it may fail when provider pages
#   change markup, and callers must always have a static fallback.
# - Cache is process-local and guarded by a lock for thread safety.
# - Extracted prices are intentionally validated with conservative bounds to
#   reduce false positives from unrelated numbers in HTML.

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
import re
from threading import RLock
from typing import Any

import requests

from route_agent.model_registry.constants import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_PRICING_SNIPPET_RADIUS,
)


# Match explicit currency amounts (for example: `$0.25`).
PRICE_PATTERN = re.compile(r"\$\s*(\d+(?:\.\d+)?)")


def _utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format with `Z` suffix."""
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class PriceValue:
    """Container for one resolved price point."""

    value: float
    updated_at: str
    source_url: str


class DynamicPricingResolver:
    """Resolve tier pricing dynamically from provider pricing pages.

    Internal structure:
    - `_prices[provider][profile] -> PriceValue`
    - supported providers: `openai`, `deepseek`, `google`
    - supported profiles: `flash`, `pro`
    """

    def __init__(
        self,
        timeout_seconds: int = 10,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        """Initialize resolver with timeout and cache TTL.

        Args:
            timeout_seconds: timeout for one page request.
            cache_ttl_seconds: in-memory cache TTL (default 6 hours).
        """
        self.timeout_seconds = timeout_seconds
        self.cache_ttl_seconds = cache_ttl_seconds if cache_ttl_seconds is not None else DEFAULT_CACHE_TTL_SECONDS
        self._lock = RLock()
        self._prices: dict[str, dict[str, PriceValue]] = {}
        self._last_refresh_at: datetime | None = None
        self._last_errors: dict[str, str] = {}

    def get_price(self, provider: str, profile: str) -> float | None:
        """Get dynamic price for provider/profile, refreshing cache when needed."""
        self._refresh_if_needed()
        with self._lock:
            provider_prices = self._prices.get(provider.lower(), {})
            price_obj = provider_prices.get(profile.lower())
            return price_obj.value if price_obj else None

    def get_price_meta(self, provider: str, profile: str) -> dict[str, Any]:
        """Get metadata for one dynamic price point."""
        self._refresh_if_needed()
        with self._lock:
            provider_prices = self._prices.get(provider.lower(), {})
            price_obj = provider_prices.get(profile.lower())
            if not price_obj:
                return {"updated_at": None, "source_url": None, "error": self._last_errors.get(provider.lower())}
            return {
                "updated_at": price_obj.updated_at,
                "source_url": price_obj.source_url,
                "error": None,
            }

    def _refresh_if_needed(self) -> None:
        """Refresh cache only when empty or expired."""
        with self._lock:
            if self._last_refresh_at is not None:
                age = _utc_now() - self._last_refresh_at
                if age <= timedelta(seconds=self.cache_ttl_seconds):
                    return
        self.refresh()

    def refresh(self) -> None:
        """Force refresh all supported provider pricing snapshots."""
        refreshed_prices: dict[str, dict[str, PriceValue]] = {}
        errors: dict[str, str] = {}

        # Fetch provider pages independently so one failure does not block others.
        for provider in ["openai", "deepseek", "google"]:
            try:
                provider_prices = self._fetch_provider_prices(provider=provider)
                if provider_prices:
                    refreshed_prices[provider] = provider_prices
            except Exception as exc:  # noqa: BLE001
                errors[provider] = str(exc)

        with self._lock:
            # Only replace cache when new data exists; avoid wiping prior values.
            if refreshed_prices:
                self._prices = refreshed_prices
            self._last_errors = errors
            self._last_refresh_at = _utc_now()

    def _fetch_provider_prices(self, provider: str) -> dict[str, PriceValue]:
        """Fetch one provider pricing page and extract flash/pro prices."""
        provider_key = provider.lower()
        if provider_key == "openai":
            return self._fetch_openai_prices()
        if provider_key == "deepseek":
            return self._fetch_deepseek_prices()
        if provider_key == "google":
            return self._fetch_google_prices()
        return {}

    def _fetch_openai_prices(self) -> dict[str, PriceValue]:
        """Fetch OpenAI prices from official API pricing page."""
        url = (os.getenv("OPENAI_PRICING_URL") or "").strip() or "https://openai.com/api/pricing/"
        text = self._fetch_page_text(url=url)

        # OpenAI profile mapping:
        # - flash: keywords near `mini` / `flash`
        # - pro:   keywords near `gpt-5` / `pro`
        flash = self._extract_price_near_keywords(
            text=text,
            keyword_sets=[["mini"], ["flash"]],
        )
        pro = self._extract_price_near_keywords(
            text=text,
            keyword_sets=[["pro"], ["gpt-5"]],
        )

        return self._build_provider_price_map(url=url, flash=flash, pro=pro)

    def _fetch_deepseek_prices(self) -> dict[str, PriceValue]:
        """Fetch DeepSeek prices from official docs pricing page."""
        url = (os.getenv("DEEPSEEK_PRICING_URL") or "").strip() or "https://api-docs.deepseek.com/quick_start/pricing"
        text = self._fetch_page_text(url=url)

        # DeepSeek profile mapping:
        # - flash: chat
        # - pro: reasoner
        flash = self._extract_price_near_keywords(
            text=text,
            keyword_sets=[["deepseek-chat"], ["chat"]],
        )
        pro = self._extract_price_near_keywords(
            text=text,
            keyword_sets=[["deepseek-reasoner"], ["reasoner"]],
        )
        return self._build_provider_price_map(url=url, flash=flash, pro=pro)

    def _fetch_google_prices(self) -> dict[str, PriceValue]:
        """Fetch Google Gemini prices from official Gemini pricing page."""
        url = (os.getenv("GOOGLE_PRICING_URL") or "").strip() or "https://ai.google.dev/gemini-api/docs/pricing"
        text = self._fetch_page_text(url=url)

        # Google profile mapping:
        # - flash: `flash`
        # - pro:   `pro`
        flash = self._extract_price_near_keywords(
            text=text,
            keyword_sets=[["flash"]],
        )
        pro = self._extract_price_near_keywords(
            text=text,
            keyword_sets=[["pro"]],
        )
        return self._build_provider_price_map(url=url, flash=flash, pro=pro)

    def _fetch_page_text(self, url: str) -> str:
        """Fetch remote page text with strict timeout and status check."""
        response = requests.get(
            url,
            timeout=self.timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; RouteAgentPricingBot/1.0; +https://example.local)"
                )
            },
        )
        response.raise_for_status()
        return response.text

    def _extract_price_near_keywords(
        self,
        text: str,
        keyword_sets: list[list[str]],
        radius: int | None = None,
    ) -> float | None:
        """Extract first price whose nearby snippet matches one keyword set.

        Args:
            text: page content.
            keyword_sets: list of keyword groups; any full group match is valid.
            radius: snippet window around a candidate price.

        Returns:
            Parsed price if matched, otherwise `None`.
        """
        effective_radius = radius if radius is not None else DEFAULT_PRICING_SNIPPET_RADIUS
        lowered = text.lower()
        for match in PRICE_PATTERN.finditer(lowered):
            start = max(0, match.start() - effective_radius)
            end = min(len(lowered), match.end() + effective_radius)
            snippet = lowered[start:end]

            # Accept a match when all keywords in one set are present nearby.
            for keywords in keyword_sets:
                if all(keyword in snippet for keyword in keywords):
                    try:
                        candidate = round(float(match.group(1)), 4)
                        # Keep only plausible per-1M-token price values.
                        if 0.0 < candidate <= 100.0:
                            return candidate
                    except ValueError:
                        continue
        return None

    def _build_provider_price_map(
        self,
        url: str,
        flash: float | None,
        pro: float | None,
    ) -> dict[str, PriceValue]:
        """Build provider price map for flash/pro profiles."""
        out: dict[str, PriceValue] = {}
        now = _utc_now_iso()
        if flash is not None:
            out["flash"] = PriceValue(value=flash, updated_at=now, source_url=url)
        if pro is not None:
            out["pro"] = PriceValue(value=pro, updated_at=now, source_url=url)
        return out


def create_dynamic_pricing_resolver() -> DynamicPricingResolver:
    """Create resolver from environment configuration.

    Optional environment variables:
    - `DYNAMIC_PRICING_TIMEOUT_SECONDS`
    - `DYNAMIC_PRICING_CACHE_TTL_SECONDS`
    """
    timeout = int((os.getenv("DYNAMIC_PRICING_TIMEOUT_SECONDS") or "10").strip())
    ttl_str = os.getenv("DYNAMIC_PRICING_CACHE_TTL_SECONDS")
    ttl = int(ttl_str.strip()) if ttl_str else DEFAULT_CACHE_TTL_SECONDS
    return DynamicPricingResolver(timeout_seconds=timeout, cache_ttl_seconds=ttl)

