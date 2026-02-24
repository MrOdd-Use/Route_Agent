"""Arena leaderboard HTML scraper.

Fetches https://arena.ai/zh/leaderboard/ and parses embedded Next.js data
plus rendered HTML tables to extract model rankings, ELO scores, and votes.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx
from bs4 import BeautifulSoup

from route_agent.model_registry.arena.schemas import ArenaLeaderboard, ArenaModelEntry
from route_agent.model_registry.constants import (
    ARENA_LEADERBOARD_URL,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Category labels as they appear on the Arena page (Chinese locale).
_CATEGORY_LABELS: dict[str, str] = {
    "text": "text",
    "code": "code",
    "vision": "vision",
    "search": "search",
}


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

_NEXT_PUSH_RE = re.compile(r"self\.__next_f\.push\(\[.*?,\s*\"(.*?)\"\]\)", re.DOTALL)


def _extract_next_data(html: str) -> list[dict[str, Any]]:
    """Extract JSON objects from self.__next_f.push() calls."""
    results: list[dict[str, Any]] = []
    for match in _NEXT_PUSH_RE.finditer(html):
        raw = match.group(1)
        # Unescape common JS string escapes
        raw = raw.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        # Try to find JSON objects within the payload
        for json_match in re.finditer(r"\{[^{}]{20,}\}", raw):
            try:
                obj = json.loads(json_match.group())
                results.append(obj)
            except (json.JSONDecodeError, ValueError):
                continue
    return results


def _parse_table_rows(html: str) -> list[dict[str, str]]:
    """Parse leaderboard table rows from rendered HTML using BeautifulSoup."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict[str, str]] = []

    # Arena uses table or div-based rows; try both patterns.
    for tr in soup.select("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) >= 3:
            texts = [c.get_text(strip=True) for c in cells]
            rows.append({"cells": texts, "raw": tr.get_text(" ", strip=True)})

    # Fallback: look for repeated div patterns with score-like numbers.
    if not rows:
        for div in soup.select("div"):
            text = div.get_text(" ", strip=True)
            # Match patterns like "1 claude-opus-4-6 1506 4,745"
            if re.search(r"\d{4,}", text) and re.search(r"[a-z]", text, re.I):
                rows.append({"cells": text.split(), "raw": text})

    return rows


def _parse_entries_from_text(
    raw_text: str,
    category: str,
) -> list[ArenaModelEntry]:
    """Parse model entries from raw page text using regex patterns.

    This is the most robust fallback: scan the full page text for patterns
    like "model-name  1506  4,745" that appear in the rendered leaderboard.
    """
    entries: list[ArenaModelEntry] = []
    # Pattern: model_name  score  votes (with optional commas in votes)
    pattern = re.compile(
        r"([a-z][a-z0-9._-]+(?:-[a-z0-9._-]+)+)"  # model name (hyphenated)
        r"[^0-9]+"  # skip non-digit chars (organization, whitespace, etc.)
        r"(\d{3,4})"  # arena score (3-4 digits)
        r"\s+"
        r"([\d,]+)",  # votes (with commas)
        re.IGNORECASE,
    )
    seen: set[str] = set()
    for m in pattern.finditer(raw_text):
        name = m.group(1).lower()
        if name in seen:
            continue
        seen.add(name)
        score = int(m.group(2))
        votes = int(m.group(3).replace(",", ""))
        entries.append(
            ArenaModelEntry(
                name=name,
                organization="",  # filled later if available
                category=category,
                arena_score=score,
                votes=votes,
                rank=0,  # assigned after sorting
                total_in_category=0,
            )
        )

    # Sort by score descending and assign ranks
    entries.sort(key=lambda e: e.arena_score, reverse=True)
    total = len(entries)
    ranked: list[ArenaModelEntry] = []
    for i, entry in enumerate(entries):
        ranked.append(
            ArenaModelEntry(
                name=entry.name,
                organization=entry.organization,
                category=entry.category,
                arena_score=entry.arena_score,
                votes=entry.votes,
                rank=i + 1,
                total_in_category=total,
            )
        )
    return ranked


def _enrich_organizations(
    entries: list[ArenaModelEntry],
    next_data: list[dict[str, Any]],
) -> list[ArenaModelEntry]:
    """Fill in organization field from Next.js embedded data."""
    org_map: dict[str, str] = {}
    for obj in next_data:
        name = obj.get("publicName") or obj.get("displayName") or ""
        org = obj.get("organization", "")
        if name and org:
            org_map[name.lower()] = org.lower()

    enriched: list[ArenaModelEntry] = []
    for entry in entries:
        org = org_map.get(entry.name, entry.organization)
        if org != entry.organization:
            enriched.append(
                ArenaModelEntry(
                    name=entry.name,
                    organization=org,
                    category=entry.category,
                    arena_score=entry.arena_score,
                    votes=entry.votes,
                    rank=entry.rank,
                    total_in_category=entry.total_in_category,
                )
            )
        else:
            enriched.append(entry)
    return enriched


# ---------------------------------------------------------------------------
# Public scraper
# ---------------------------------------------------------------------------


class ArenaLeaderboardScraper:
    """Async scraper for the Arena AI leaderboard page.

    Features:
    - In-memory cache with configurable TTL
    - Retry on transient failures
    - Two-layer parsing: Next.js embedded JSON + HTML text regex
    """

    def __init__(
        self,
        url: str = ARENA_LEADERBOARD_URL,
        cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
        max_retries: int = 1,
    ) -> None:
        self._url = url
        self._cache_ttl = cache_ttl
        self._max_retries = max_retries
        self._cache: ArenaLeaderboard | None = None
        self._cache_ts: float = 0.0

    async def get(self) -> ArenaLeaderboard:
        """Return leaderboard, using memory cache if fresh."""
        now = time.monotonic()
        if self._cache is not None and (now - self._cache_ts) < self._cache_ttl:
            return self._cache

        result = await self.fetch()
        self._cache = result
        self._cache_ts = time.monotonic()
        return result

    async def fetch(self) -> ArenaLeaderboard:
        """Fetch and parse the leaderboard page (no cache)."""
        html = await self._fetch_html()
        if not html:
            return ArenaLeaderboard()
        return self._parse(html)

    async def _fetch_html(self) -> str:
        """GET the leaderboard page with retries."""
        last_err: Exception | None = None
        for attempt in range(1 + self._max_retries):
            try:
                async with httpx.AsyncClient(
                    timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(
                        self._url,
                        headers={"User-Agent": _USER_AGENT},
                    )
                    resp.raise_for_status()
                    return resp.text
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_err = exc
                logger.warning(
                    "Arena fetch attempt %d/%d failed: %s",
                    attempt + 1,
                    1 + self._max_retries,
                    exc,
                )
        logger.error("Arena fetch failed after retries: %s", last_err)
        return ""

    def _parse(self, html: str) -> ArenaLeaderboard:
        """Parse HTML into ArenaLeaderboard."""
        from datetime import datetime, timezone

        next_data = _extract_next_data(html)

        # Extract plain text for regex-based score parsing
        soup = BeautifulSoup(html, "lxml")
        page_text = soup.get_text(" ", strip=True)

        # Parse each category.
        # The page renders separate sections/tabs for each category.
        # We parse the full text and rely on model-name patterns.
        all_entries = _parse_entries_from_text(page_text, category="text")
        all_entries = _enrich_organizations(all_entries, next_data)

        # Categorize: use Next.js rankByModality data if available
        modality_map = self._build_modality_map(next_data)

        text_entries: list[ArenaModelEntry] = []
        code_entries: list[ArenaModelEntry] = []
        vision_entries: list[ArenaModelEntry] = []
        search_entries: list[ArenaModelEntry] = []

        for entry in all_entries:
            modalities = modality_map.get(entry.name, set())
            # Assign to categories based on modality data
            if "code" in modalities:
                code_entries.append(
                    ArenaModelEntry(
                        name=entry.name,
                        organization=entry.organization,
                        category="code",
                        arena_score=entry.arena_score,
                        votes=entry.votes,
                        rank=entry.rank,
                        total_in_category=entry.total_in_category,
                    )
                )
            if "vision" in modalities:
                vision_entries.append(
                    ArenaModelEntry(
                        name=entry.name,
                        organization=entry.organization,
                        category="vision",
                        arena_score=entry.arena_score,
                        votes=entry.votes,
                        rank=entry.rank,
                        total_in_category=entry.total_in_category,
                    )
                )
            if "search" in modalities:
                search_entries.append(
                    ArenaModelEntry(
                        name=entry.name,
                        organization=entry.organization,
                        category="search",
                        arena_score=entry.arena_score,
                        votes=entry.votes,
                        rank=entry.rank,
                        total_in_category=entry.total_in_category,
                    )
                )
            # All models go into text by default
            text_entries.append(entry)

        fetched_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

        return ArenaLeaderboard(
            text=tuple(text_entries),
            code=tuple(self._rerank(code_entries, "code")),
            vision=tuple(self._rerank(vision_entries, "vision")),
            search=tuple(self._rerank(search_entries, "search")),
            fetched_at=fetched_at,
        )

    @staticmethod
    def _build_modality_map(
        next_data: list[dict[str, Any]],
    ) -> dict[str, set[str]]:
        """Build model_name -> set of modalities from Next.js data."""
        result: dict[str, set[str]] = {}
        for obj in next_data:
            name = (obj.get("publicName") or obj.get("displayName") or "").lower()
            rank_by = obj.get("rankByModality", {})
            if not name or not rank_by:
                continue
            modalities: set[str] = set()
            for modality, rank_val in rank_by.items():
                # 9007199254740991 is JS Number.MAX_SAFE_INTEGER (= not ranked)
                if isinstance(rank_val, (int, float)) and rank_val < 9007199254740991:
                    modalities.add(modality.lower())
            result[name] = modalities
        return result

    @staticmethod
    def _rerank(
        entries: list[ArenaModelEntry],
        category: str,
    ) -> list[ArenaModelEntry]:
        """Re-sort and re-assign ranks within a category."""
        entries.sort(key=lambda e: e.arena_score, reverse=True)
        total = len(entries)
        return [
            ArenaModelEntry(
                name=e.name,
                organization=e.organization,
                category=category,
                arena_score=e.arena_score,
                votes=e.votes,
                rank=i + 1,
                total_in_category=total,
            )
            for i, e in enumerate(entries)
        ]
