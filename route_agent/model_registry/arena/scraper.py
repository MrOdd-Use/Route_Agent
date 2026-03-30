"""Arena leaderboard HTML scraper.

Fetches per-category pages from arena.ai/leaderboard/ and parses rendered
HTML text to extract model rankings, ELO scores, and votes.

Each category has its own URL:
  text   -> https://arena.ai/leaderboard/
  code   -> https://arena.ai/leaderboard/code
  vision -> https://arena.ai/leaderboard/vision
  search -> https://arena.ai/leaderboard/search

Pages are fetched concurrently. Parsing uses a regex that matches the
rendered text pattern:
  "rank ... org model_name org ? license score +spread/-spread votes ..."
"""

from __future__ import annotations

import logging
import re
import time
import httpx
from bs4 import BeautifulSoup

from route_agent.model_registry.arena.schemas import ArenaLeaderboard, ArenaModelEntry
from route_agent.model_registry.constants import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Per-category leaderboard URLs
_CATEGORY_URLS: dict[str, str] = {
    "text": "https://arena.ai/leaderboard/",
    "code": "https://arena.ai/leaderboard/code",
    "vision": "https://arena.ai/leaderboard/vision",
    "search": "https://arena.ai/leaderboard/search",
}

# Two patterns covering all observed page formats:
#
# Pattern A (text/code): model-name [org ? license] score +N/-N votes
#   "claude-opus-4-6 Anthropic ? Proprietary 1549 +11/-11 4,264"
#   "claude-sonnet-4-6 1523 6,391"
#
# Pattern B (search/vision): model-name [org] score±spread votes
#   "claude-opus-4-6-search Anthropic 1254±6 16,183"
_ENTRY_RE_A = re.compile(
    r"([a-z][a-z0-9._-]{3,}(?:-[a-z0-9._-]+)+)"   # model name
    r"(?:\s+[^0-9\s]\S*){0,5}\s+"                   # 0-5 non-numeric tokens (org/license)
    r"(1[0-9]{3})"                                   # ELO score 1000-1999
    r"(?:\s+\+\d+/-\d+)?"                            # optional +N/-N spread
    r"\s+([\d,]+)",                                  # votes
    re.IGNORECASE,
)

_ENTRY_RE_B = re.compile(
    r"([a-z][a-z0-9._-]{3,}(?:-[a-z0-9._-]+)+)"   # model name
    r"(?:\s+[^0-9\s]\S*){0,3}\s+"                   # 0-3 non-numeric tokens
    r"(1[0-9]{3})\u00b1\d+"                          # ELO±spread (no space before ±)
    r"\s+([\d,]+)",                                  # votes
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_category_page(html: str, category: str) -> list[ArenaModelEntry]:
    """Parse one category page into ArenaModelEntry list.

    Tries pattern A (text/code: score +N/-N votes) then pattern B
    (search/vision: score±N votes). Uses whichever yields more results.
    """
    soup = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(" ", strip=True)

    seen: set[str] = set()
    entries: list[ArenaModelEntry] = []

    # Try both patterns; pick the one with more matches
    matches_a = list(_ENTRY_RE_A.finditer(page_text))
    matches_b = list(_ENTRY_RE_B.finditer(page_text))
    matches = matches_a if len(matches_a) >= len(matches_b) else matches_b

    for m in matches:
        name = m.group(1).lower()
        if name in seen:
            continue
        seen.add(name)
        score = int(m.group(2))
        votes = int(m.group(3).replace(",", ""))
        entries.append(
            ArenaModelEntry(
                name=name,
                organization="",
                category=category,
                arena_score=score,
                votes=votes,
                rank=0,
                total_in_category=0,
            )
        )

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


# ---------------------------------------------------------------------------
# Public scraper
# ---------------------------------------------------------------------------


class ArenaLeaderboardScraper:
    """Async scraper for the Arena AI leaderboard.

    Fetches each category page concurrently and parses rendered HTML text.
    Features in-memory cache with configurable TTL and retry on failure.
    """

    def __init__(
        self,
        cache_ttl: int = DEFAULT_CACHE_TTL_SECONDS,
        max_retries: int = 1,
    ) -> None:
        """Initialize the instance."""
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
        """Fetch all category pages sequentially and parse."""
        from datetime import datetime, timezone

        results: dict[str, str] = {}
        for cat, url in _CATEGORY_URLS.items():
            results[cat] = await self._fetch_html(url)

        text_entries = _parse_category_page(results.get("text", ""), "text")
        code_entries = _parse_category_page(results.get("code", ""), "code")
        vision_entries = _parse_category_page(results.get("vision", ""), "vision")
        search_entries = _parse_category_page(results.get("search", ""), "search")

        fetched_at = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )

        return ArenaLeaderboard(
            text=tuple(text_entries),
            code=tuple(code_entries),
            vision=tuple(vision_entries),
            search=tuple(search_entries),
            fetched_at=fetched_at,
        )

    async def _fetch_html(self, url: str) -> str:
        """GET one URL with its own client per request, return empty string on failure."""
        last_err: Exception | None = None
        for attempt in range(1 + self._max_retries):
            try:
                async with httpx.AsyncClient(
                    timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                    follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                ) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    return resp.text
            except (httpx.HTTPError, httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = exc
                logger.warning(
                    "Arena fetch %s attempt %d/%d failed: %s",
                    url,
                    attempt + 1,
                    1 + self._max_retries,
                    exc,
                )
        logger.error("Arena fetch %s failed after retries: %s", url, last_err)
        return ""
