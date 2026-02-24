"""Class-pool management for router_engine."""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from route_agent.router_engine.constants import (
    CHALLENGER_LEAD_STREAK,
    CLASS_SIM_MARGIN,
    CLASS_SIM_THRESHOLD,
    DEFAULT_AGENT_CLASS,
    DEFAULT_DOMAIN_KEY,
    DEFAULT_PROMOTION_MIN_SUCCESS,
    ENABLE_CLASS_SIM_FALLBACK,
    ENABLE_CONTROLLED_CLASS_DICT,
    FAIL_PENALTY_FACTOR,
    MIN_TRIALS,
    POOL_AGE_EXEMPT_RATE,
    POOL_AGE_EXEMPT_SUCCESS,
    POOL_BONUS_BASE_RATIO,
    POOL_BONUS_FULL_RATIO,
    POOL_ENTRY_CONF_LB_MIN,
    POOL_MAX_SIZE,
    POOL_MODEL_MAX_AGE_DAYS,
    WILSON_Z,
)
from route_agent.router_engine.defaults import DefaultsStore
from route_agent.router_engine.schemas import (
    ClassPoolEntry,
    MatchResult,
    ModelCandidate,
    RouteRequest,
)
from route_agent.router_engine.storage import RouterStorage

ClassMatcher = Callable[..., MatchResult | None]
ModelMetadataResolver = Callable[[str], Any | None]


def _normalize(value: str) -> str:
    return "_".join((value or "").strip().lower().split())


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


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


class ClassPoolManager:
    """Maintains per-agent-class model pools and defaults."""

    def __init__(
        self,
        router_storage: RouterStorage,
        model_metadata_resolver: ModelMetadataResolver | None = None,
        class_matcher: ClassMatcher | None = None,
    ) -> None:
        self._storage = router_storage
        self._resolver = model_metadata_resolver
        self._class_matcher = class_matcher
        self._defaults_store = DefaultsStore(router_storage, model_metadata_resolver)

    def _domain_key(self, domain: str) -> str:
        return DEFAULT_DOMAIN_KEY if not domain.strip() else domain.strip()

    async def resolve_class_async(self, request: RouteRequest) -> tuple[str, str]:
        if request.agent_class:
            return _normalize(request.agent_class), "override"

        task_class = getattr(request.analysis, "task_class", None)
        if isinstance(task_class, str) and task_class.strip():
            if ENABLE_CONTROLLED_CLASS_DICT:
                canonical = await self._storage.resolve_canonical_class_async(task_class)
                if canonical is not None:
                    return canonical, "llm"
                await self._storage.upsert_class_review_candidate_async(task_class, proposed_by="llm")
            else:
                return _normalize(task_class), "llm"

        if ENABLE_CLASS_SIM_FALLBACK and self._class_matcher is not None:
            text = f"{request.agent_name}\n{request.system_prompt or ''}"
            hit = self._class_matcher(text, threshold=CLASS_SIM_THRESHOLD, margin=CLASS_SIM_MARGIN)
            if hit is not None:
                if ENABLE_CONTROLLED_CLASS_DICT:
                    canonical = await self._storage.resolve_canonical_class_async(hit.class_name)
                    if canonical is not None:
                        return canonical, "vector"
                    await self._storage.upsert_class_review_candidate_async(hit.class_name, proposed_by="vector")
                else:
                    return _normalize(hit.class_name), "vector"

        return DEFAULT_AGENT_CLASS, "default"

    def resolve_class(self, request: RouteRequest) -> tuple[str, str]:
        return asyncio.run(self.resolve_class_async(request))

    async def get_pool_entries_async(self, agent_class: str) -> list[ClassPoolEntry]:
        return await self._storage.get_pool_entries_async(_normalize(agent_class))

    def get_pool_entries(self, agent_class: str) -> list[ClassPoolEntry]:
        return self._storage.get_pool_entries(_normalize(agent_class))

    def apply_pool_bonus(
        self,
        candidates: list[ModelCandidate],
        pool_entries: list[ClassPoolEntry],
    ) -> list[ModelCandidate]:
        pool_by_id = {entry.model_id: entry for entry in pool_entries}
        updated: list[ModelCandidate] = []
        for candidate in candidates:
            pool_entry = pool_by_id.get(candidate.model_id)
            if pool_entry is None:
                updated.append(candidate)
                continue

            trials = max(0, pool_entry.success_count + pool_entry.fail_count)
            ratio = min(trials / float(MIN_TRIALS), 1.0)
            pct = POOL_BONUS_BASE_RATIO + (POOL_BONUS_FULL_RATIO - POOL_BONUS_BASE_RATIO) * ratio
            boosted = candidate.dimension_score * (1.0 + pct)
            updated.append(
                replace(
                    candidate,
                    dimension_score=boosted,
                    is_pool=True,
                )
            )
        return updated

    async def record_outcome(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> None:
        normalized_class = _normalize(agent_class)
        key_domain = self._domain_key(domain)

        if outcome_type == "success":
            await self.try_add_to_pool(normalized_class, model_id)
            await self.evict_check(normalized_class)
            await self._defaults_store.record_success_async(normalized_class, key_domain, model_id)
            await self._defaults_store.evaluate_and_promote_default_async(
                normalized_class,
                key_domain,
                min_success=DEFAULT_PROMOTION_MIN_SUCCESS,
            )
            return

        if outcome_type == "quality_fail":
            await self.evict_check(normalized_class)
            await self._defaults_store.record_fail_async(normalized_class, key_domain, model_id)
            return

        if outcome_type == "exec_fail":
            await self._storage.atomic_increment_exec_fail_async(normalized_class, model_id)

    async def try_add_to_pool(self, agent_class: str, model_id: str) -> bool:
        stats = await self._storage.get_stats_async(agent_class, model_id)
        if stats is None:
            return False

        lb = _wilson_lower_bound(stats.success_count, stats.fail_count)
        if lb < POOL_ENTRY_CONF_LB_MIN:
            return False

        release_date: str | None = None
        if self._resolver is not None:
            metadata = self._resolver(model_id)
            routing = getattr(metadata, "routing", None)
            if isinstance(routing, dict):
                value = routing.get("release_date")
                if isinstance(value, str) and value.strip():
                    release_date = value.strip()
            for attr in ("release_date", "model_release_date"):
                if release_date:
                    break
                value = getattr(metadata, attr, None)
                if isinstance(value, str) and value.strip():
                    release_date = value.strip()

        # Cold path: serialize pool-size check + eviction + insert.
        with self._storage.connect_sync() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cnt_row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM class_pool WHERE agent_class = ?",
                (agent_class,),
            ).fetchone()
            current_count = int(cnt_row["cnt"] if cnt_row else 0)

            if current_count >= POOL_MAX_SIZE:
                victim = conn.execute(
                    """
                    SELECT cp.model_id
                      FROM class_pool cp
                      JOIN class_model_stats cms
                        ON cp.agent_class = cms.agent_class AND cp.model_id = cms.model_id
                      LEFT JOIN class_pool_defaults cpd
                        ON cp.agent_class = cpd.agent_class AND cp.model_id = cpd.model_id
                     WHERE cp.agent_class = ?
                       AND cpd.model_id IS NULL
                       AND NOT (cms.success_count >= ? AND cms.success_rate >= ?)
                     ORDER BY cms.success_rate ASC
                     LIMIT 1
                    """,
                    (agent_class, POOL_AGE_EXEMPT_SUCCESS, POOL_AGE_EXEMPT_RATE),
                ).fetchone()
                if victim is not None:
                    conn.execute(
                        "DELETE FROM class_pool WHERE agent_class = ? AND model_id = ?",
                        (agent_class, victim["model_id"]),
                    )
                else:
                    conn.execute("ROLLBACK")
                    return False

            conn.execute(
                """
                INSERT OR IGNORE INTO class_pool(agent_class, model_id, model_release_date)
                VALUES (?, ?, ?)
                """,
                (agent_class, model_id, release_date),
            )
            conn.execute("COMMIT")

        return True

    async def evict_check(self, agent_class: str) -> list[str]:
        removed: list[str] = []
        entries = await self._storage.get_pool_entries_async(agent_class)
        default_ids = await self._storage.list_default_model_ids_async(agent_class)
        now = datetime.now(tz=timezone.utc)

        for entry in entries:
            availability = await self._storage.get_availability_async(entry.model_id)
            if availability is not None and availability.status == "unable":
                await self._storage.delete_pool_entry_async(agent_class, entry.model_id)
                removed.append(entry.model_id)
                continue

            release_dt = _parse_date(entry.model_release_date)
            if release_dt is None:
                continue

            age_days = (now - release_dt).days
            if age_days <= POOL_MODEL_MAX_AGE_DAYS:
                continue

            if entry.model_id in default_ids:
                continue

            exempt = (
                entry.success_count >= POOL_AGE_EXEMPT_SUCCESS
                and entry.success_rate >= POOL_AGE_EXEMPT_RATE
            )
            if exempt:
                continue

            await self._storage.delete_pool_entry_async(agent_class, entry.model_id)
            removed.append(entry.model_id)

        return removed

    async def get_default(self, agent_class: str, domain: str) -> str | None:
        default = await self._defaults_store.lookup_default_async(_normalize(agent_class), self._domain_key(domain))
        if default is None:
            return None

        if self._resolver is not None and self._resolver(default.model_id) is None:
            await self._storage.clear_default_async(_normalize(agent_class), self._domain_key(domain))
            return None

        return default.model_id

    async def set_user_override(self, agent_class: str, domain: str, model_id: str) -> None:
        await self._defaults_store.set_user_override_async(_normalize(agent_class), self._domain_key(domain), model_id)
