"""Default-model management for class pools (internal)."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable

from route_agent.model_registry.constants import PRICE_UNAVAILABLE_SENTINEL
from route_agent.router_engine.constants import (
    CHALLENGER_LEAD_STREAK,
    DEFAULT_DOMAIN_KEY,
    DEFAULT_PROMOTION_MIN_SUCCESS,
    ENABLE_DOMAIN_DEFAULTS,
    QUALITY_FAIL_REVOKE,
    SCORE_TIER_EPSILON,
    WILSON_Z,
)
from route_agent.router_engine.schemas import ClassModelStats, ClassPoolDefault
from route_agent.router_engine.storage import RouterStorage

ModelMetadataResolver = Callable[[str], Any | None]


def _normalized_domain(domain: str) -> str:
    if not ENABLE_DOMAIN_DEFAULTS:
        return DEFAULT_DOMAIN_KEY
    domain_clean = (domain or "").strip()
    return domain_clean or DEFAULT_DOMAIN_KEY


def _extract_price(metadata: Any | None) -> float:
    if metadata is None:
        return PRICE_UNAVAILABLE_SENTINEL

    pricing: dict[str, Any] | None = getattr(metadata, "pricing", None)
    if not isinstance(pricing, dict):
        return PRICE_UNAVAILABLE_SENTINEL

    raw = pricing.get("input")
    try:
        price = float(raw)
    except (TypeError, ValueError):
        return PRICE_UNAVAILABLE_SENTINEL

    unit = str(pricing.get("unit") or "").strip().lower()
    if unit != "per_1m_tokens":
        price *= 1000.0
    return price


def _extract_release_date(metadata: Any | None) -> str | None:
    if metadata is None:
        return None

    # Routing metadata may carry release date under routing.release_date.
    routing = getattr(metadata, "routing", None)
    if isinstance(routing, dict):
        value = routing.get("release_date")
        if isinstance(value, str) and value.strip():
            return value.strip()

    for attr in ("model_release_date", "release_date"):
        value = getattr(metadata, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _release_recency_sort_key(release_date: str | None) -> float:
    """Lower value means higher priority (newer release first)."""

    if not release_date:
        return math.inf

    value = release_date.strip()
    if not value:
        return math.inf

    try:
        return -float(date.fromisoformat(value[:10]).toordinal())
    except ValueError:
        return 0.0


def _wilson_lower_bound(success_count: int, fail_count: int, z: float = WILSON_Z) -> float:
    n = success_count + fail_count
    if n <= 0:
        return 0.0

    p = success_count / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - margin) / denom


class DefaultsStore:
    """Encapsulates class_pool_defaults read/write and promotion logic."""

    def __init__(
        self,
        router_storage: RouterStorage,
        model_metadata_resolver: ModelMetadataResolver | None = None,
    ) -> None:
        self._storage = router_storage
        self._model_metadata_resolver = model_metadata_resolver

    async def lookup_default_async(self, agent_class: str, domain: str) -> ClassPoolDefault | None:
        return await self._storage.get_default_async(agent_class, _normalized_domain(domain))

    async def record_success_async(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
    ) -> ClassPoolDefault | None:
        key_domain = _normalized_domain(domain)
        current = await self._storage.get_default_async(agent_class, key_domain)
        if current is None or current.model_id != model_id:
            return None

        await self._storage.atomic_increment_consecutive_success_async(agent_class, key_domain)
        return await self._storage.get_default_async(agent_class, key_domain)

    async def record_fail_async(self, agent_class: str, domain: str, model_id: str) -> bool:
        key_domain = _normalized_domain(domain)
        current = await self._storage.get_default_async(agent_class, key_domain)
        if current is None or current.model_id != model_id:
            return False

        fail_count = await self._storage.atomic_increment_consecutive_fail_async(agent_class, key_domain)
        if fail_count >= QUALITY_FAIL_REVOKE:
            await self._storage.clear_default_async(agent_class, key_domain)
            return True
        return False

    async def evaluate_and_promote_default_async(
        self,
        agent_class: str,
        domain: str,
        min_success: int = DEFAULT_PROMOTION_MIN_SUCCESS,
    ) -> ClassPoolDefault | None:
        key_domain = _normalized_domain(domain)
        current_default = await self._storage.get_default_async(agent_class, key_domain)
        if current_default is not None and current_default.is_locked:
            return current_default

        stats_items = await self._storage.list_stats_for_class_async(agent_class)
        candidates: list[tuple[ClassModelStats, float, float, str | None]] = []
        for stats in stats_items:
            if stats.success_count < int(min_success):
                continue

            availability = await self._storage.get_availability_async(stats.model_id)
            if availability is not None and availability.status == "unable":
                continue

            wlb = _wilson_lower_bound(stats.success_count, stats.fail_count)
            metadata = self._model_metadata_resolver(stats.model_id) if self._model_metadata_resolver else None
            price = _extract_price(metadata)
            release_date = _extract_release_date(metadata)
            candidates.append((stats, wlb, price, release_date))

        if not candidates:
            return current_default

        def sort_key(item: tuple[ClassModelStats, float, float, str | None]) -> tuple[float, float, float]:
            _, wlb, price, release_date = item
            # Primary: higher wlb, secondary: lower price, tertiary: newer release date.
            return (-wlb, price, _release_recency_sort_key(release_date))

        # First pass ranking by comparator (wlb desc, price asc, release recency).
        ranked = sorted(candidates, key=sort_key)
        best_stats, best_wlb, _, _ = ranked[0]

        if current_default is None:
            await self._storage.upsert_default_async(
                agent_class,
                key_domain,
                best_stats.model_id,
                consecutive_success=0,
                consecutive_fail=0,
                is_locked=False,
            )
            return await self._storage.get_default_async(agent_class, key_domain)

        incumbent = next((item for item in candidates if item[0].model_id == current_default.model_id), None)
        if incumbent is None:
            await self._storage.upsert_default_async(
                agent_class,
                key_domain,
                best_stats.model_id,
                consecutive_success=0,
                consecutive_fail=0,
                is_locked=False,
            )
            return await self._storage.get_default_async(agent_class, key_domain)

        _, inc_wlb, inc_price, inc_release = incumbent

        if best_stats.model_id == current_default.model_id:
            return current_default

        if best_stats.consecutive_success < CHALLENGER_LEAD_STREAK:
            return current_default

        if best_wlb <= inc_wlb:
            return current_default

        if abs(best_wlb - inc_wlb) < SCORE_TIER_EPSILON:
            _, _, best_price, best_release = ranked[0]
            if best_price > inc_price:
                return current_default
            if best_price == inc_price:
                if _release_recency_sort_key(best_release) >= _release_recency_sort_key(inc_release):
                    return current_default

        await self._storage.upsert_default_async(
            agent_class,
            key_domain,
            best_stats.model_id,
            consecutive_success=0,
            consecutive_fail=0,
            is_locked=False,
        )
        return await self._storage.get_default_async(agent_class, key_domain)

    async def set_user_override_async(self, agent_class: str, domain: str, model_id: str) -> None:
        key_domain = _normalized_domain(domain)
        await self._storage.upsert_default_async(
            agent_class,
            key_domain,
            model_id,
            consecutive_success=0,
            consecutive_fail=0,
            is_locked=True,
        )
