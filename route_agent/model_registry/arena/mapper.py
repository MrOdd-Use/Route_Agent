"""Arena leaderboard to ModelMetadata capability mapping utilities.

Responsibilities:
- Three-level fallback: live scrape -> SQLite cache -> None
- Rank + ELO hybrid normalization (per category)
- Fuzzy model name matching
- Batch fill of missing capabilities (only None fields)
- Capability scale validation
"""

from __future__ import annotations

import logging
import os
import re
from copy import deepcopy
from typing import Any

from route_agent.model_registry.arena.schemas import ArenaLeaderboard, ArenaModelEntry
from route_agent.model_registry.arena.scraper import ArenaLeaderboardScraper
from route_agent.model_registry.arena.storage import ArenaCacheStorage
from route_agent.model_registry.constants import ARENA_RANK_WEIGHT
from route_agent.model_registry.schemas import ModelMetadata

logger = logging.getLogger(__name__)

# Arena category -> capabilities fields mapping
_CATEGORY_FIELD_MAP: dict[str, list[str]] = {
    "text": ["text", "instruction_following", "creative_writing", "math"],
    "code": ["code"],
    "vision": ["vision"],
    "search": ["search"],
}

# Date suffix pattern for model names like "claude-opus-4-5-20251101"
_DATE_SUFFIX_RE = re.compile(r"-\d{8}$")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def normalize_score(
    rank: int,
    total: int,
    elo: int,
    elo_min: int,
    elo_max: int,
    rank_weight: float = ARENA_RANK_WEIGHT,
) -> int:
    """Hybrid rank + ELO normalization to 0-100 scale.

    - rank_score (70%): percentile from rank position
    - elo_score (30%): relative position within [elo_min, elo_max]
    """
    # Single-entry category should always map to the highest score.
    if total <= 1:
        return 100

    # Rank-based percentile: rank=1 -> 100, rank=total -> ~0
    rank_score = (1 - (rank - 1) / (total - 1)) * 100

    # ELO-based relative position within category
    if elo_max == elo_min:
        elo_score = 50.0
    else:
        elo_score = (elo - elo_min) / (elo_max - elo_min) * 100

    final = rank_weight * rank_score + (1 - rank_weight) * elo_score
    return max(0, min(100, round(final)))


def _compute_category_stats(
    entries: tuple[ArenaModelEntry, ...],
) -> tuple[int, int]:
    """Return (elo_min, elo_max) for a category's entries."""
    if not entries:
        return (0, 0)
    scores = [e.arena_score for e in entries]
    return (min(scores), max(scores))


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------


def fuzzy_match_model(
    registry_name: str,
    arena_names: list[str],
) -> str | None:
    """Match a registry model name to an Arena leaderboard name.

    Strategy (in order):
    1. Exact match
    2. Strip date suffix (-YYYYMMDD) then match
    3. Containment (arena_name in registry_name)
    """
    reg_lower = registry_name.lower()

    # 1. Exact
    for name in arena_names:
        if name == reg_lower:
            return name

    # 2. Strip date suffix
    stripped = _DATE_SUFFIX_RE.sub("", reg_lower)
    for name in arena_names:
        if name == stripped:
            return name
        if _DATE_SUFFIX_RE.sub("", name) == stripped:
            return name

    # 3. Containment (prefer longest match)
    matches = [n for n in arena_names if n in reg_lower or reg_lower in n]
    if matches:
        return max(matches, key=len)

    return None


def _build_arena_index(
    leaderboard: ArenaLeaderboard,
) -> dict[str, dict[str, ArenaModelEntry]]:
    """Build name -> {category -> entry} index for fast lookup."""
    index: dict[str, dict[str, ArenaModelEntry]] = {}
    for category, entries in leaderboard.categories.items():
        for entry in entries:
            index.setdefault(entry.name, {})[category] = entry
    return index


# ---------------------------------------------------------------------------
# Fill logic
# ---------------------------------------------------------------------------


def fill_missing_capabilities(
    capabilities: dict[str, Any],
    arena_scores: dict[str, int] | None,
    fetched_at: str = "",
) -> dict[str, Any]:
    """Fill only None-valued capability fields from Arena scores.

    Already-populated fields are never overwritten.
    Adds _source metadata for traceability.
    """
    updated = deepcopy(capabilities)
    if arena_scores is None:
        return updated

    source: dict[str, str] = dict(updated.get("_source", {}))

    for arena_cat, fields in _CATEGORY_FIELD_MAP.items():
        if arena_cat not in arena_scores:
            continue
        for field_name in fields:
            if field_name in updated and updated[field_name] is None:
                updated[field_name] = arena_scores[arena_cat]
                source[field_name] = f"arena:{fetched_at}"

    if source:
        updated["_source"] = source

    return updated


def validate_capability_scale(capabilities: dict[str, Any]) -> list[str]:
    """Validate all non-None capability values are on [0, 100] scale.

    Returns list of warning messages. Empty list = all good.
    """
    warnings: list[str] = []
    skip_keys = {"_source"}

    scores: dict[str, Any] = {
        k: v for k, v in capabilities.items() if k not in skip_keys and v is not None
    }

    for field_name, value in scores.items():
        if not isinstance(value, (int, float)):
            warnings.append(f"{field_name}: non-numeric type {type(value).__name__}")
            continue
        if value < 0 or value > 100:
            warnings.append(f"{field_name}: value {value} outside [0, 100]")

    # Check for scale mismatch (>10x ratio between min and max)
    numeric = [v for v in scores.values() if isinstance(v, (int, float)) and v > 0]
    if len(numeric) >= 2:
        min_val, max_val = min(numeric), max(numeric)
        if min_val > 0 and max_val / min_val > 10:
            warnings.append(
                f"scale mismatch: min={min_val}, max={max_val}, "
                f"ratio={max_val / min_val:.1f}x"
            )

    return warnings


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------


def batch_fill_arena_capabilities(
    models: list[ModelMetadata],
    leaderboard: ArenaLeaderboard,
) -> list[ModelMetadata]:
    """Fill missing capabilities for all models using Arena data.

    - Builds fuzzy index once
    - Only fills None fields; existing values untouched
    - Validates scale consistency after fill
    - Returns new list (immutable pattern)
    """
    if leaderboard.is_empty:
        return models

    arena_index = _build_arena_index(leaderboard)
    arena_names = list(arena_index.keys())

    # Pre-compute per-category stats for normalization
    cat_stats: dict[str, tuple[int, int]] = {}
    for cat, entries in leaderboard.categories.items():
        cat_stats[cat] = _compute_category_stats(entries)

    result: list[ModelMetadata] = []
    for model in models:
        matched_name = fuzzy_match_model(model.api_model_name, arena_names)
        if matched_name is None:
            result.append(model)
            continue

        entries_by_cat = arena_index[matched_name]
        arena_scores: dict[str, int] = {}

        for cat, entry in entries_by_cat.items():
            elo_min, elo_max = cat_stats.get(cat, (0, 0))
            arena_scores[cat] = normalize_score(
                rank=entry.rank,
                total=entry.total_in_category,
                elo=entry.arena_score,
                elo_min=elo_min,
                elo_max=elo_max,
            )

        new_caps = fill_missing_capabilities(
            model.capabilities,
            arena_scores,
            fetched_at=leaderboard.fetched_at,
        )

        # Validate scale
        warnings = validate_capability_scale(new_caps)
        if warnings:
            logger.warning(
                "Capability scale issues for %s: %s",
                model.model_id,
                warnings,
            )

        # Create new ModelMetadata with updated capabilities (immutable)
        result.append(
            ModelMetadata(
                model_id=model.model_id,
                display_name=model.display_name,
                provider=model.provider,
                api_model_name=model.api_model_name,
                endpoint=model.endpoint,
                auth=model.auth,
                capabilities=new_caps,
                pricing=model.pricing,
                limits=model.limits,
                status=model.status,
                routing=model.routing,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Three-level fallback entry point
# ---------------------------------------------------------------------------

_scraper: ArenaLeaderboardScraper | None = None
_storage: ArenaCacheStorage | None = None


def _get_scraper() -> ArenaLeaderboardScraper:
    """Execute `_get_scraper`."""
    global _scraper
    if _scraper is None:
        _scraper = ArenaLeaderboardScraper()
    return _scraper


def _get_storage() -> ArenaCacheStorage:
    """Execute `_get_storage`."""
    global _storage
    if _storage is None:
        _storage = ArenaCacheStorage()
    return _storage


def is_arena_scoring_enabled() -> bool:
    """Check ENABLE_ARENA_SCORING environment variable."""
    raw = (os.getenv("ENABLE_ARENA_SCORING") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


async def get_leaderboard() -> ArenaLeaderboard | None:
    """Three-level fallback: live scrape -> SQLite cache -> None."""
    # Level 1: live scrape (with in-memory cache)
    try:
        lb = await _get_scraper().get()
        if not lb.is_empty:
            # Persist to SQLite for next restart
            try:
                _get_storage().save(lb)
            except Exception:
                logger.debug("Failed to persist arena cache", exc_info=True)
            return lb
    except Exception:
        logger.warning("Arena live scrape failed", exc_info=True)

    # Level 2: SQLite cache
    try:
        cached = _get_storage().load()
        if cached is not None:
            logger.info("Using cached arena leaderboard (fetched_at=%s)", cached.fetched_at)
            return cached
    except Exception:
        logger.debug("Arena cache load failed", exc_info=True)

    # Level 3: give up
    logger.warning("No arena leaderboard data available")
    return None


def get_leaderboard_sync() -> ArenaLeaderboard | None:
    """Synchronous wrapper around get_leaderboard() for sync call sites."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Already inside an event loop. Run in a separate thread to avoid
        # monkey-patching the global loop with nest_asyncio.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, get_leaderboard())
            return future.result(timeout=30)

    return asyncio.run(get_leaderboard())
