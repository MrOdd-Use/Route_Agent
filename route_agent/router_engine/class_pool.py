"""Class-pool management for router_engine."""

from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.constants import (
    CHALLENGER_LEAD_STREAK,
    CLASS_DIMENSION_PROFILES,
    CLASS_SIM_MARGIN,
    CLASS_SIM_THRESHOLD,
    DEFAULT_AGENT_CLASS,
    DEFAULT_DOMAIN_KEY,
    DEFAULT_PROMOTION_MIN_SUCCESS,
    ENABLE_CLASS_SIM_FALLBACK,
    ENABLE_CONTROLLED_CLASS_DICT,
    GENERAL_FALLBACK_DIMS,
    POOL_AGE_EXEMPT_RATE,
    POOL_AGE_EXEMPT_SUCCESS,
    POOL_ENTRY_CONF_LB_MIN,
    POOL_MAX_SIZE,
    POOL_MODEL_MAX_AGE_DAYS,
    POOL_SEED_SIZE,
    SEED_COST_WEIGHT,
    SEED_DIM_WEIGHT,
    WILSON_Z,
)
from route_agent.router_engine.defaults import DefaultsStore
from route_agent.router_engine.schemas import (
    ClassPoolEntry,
    MatchResult,
    RouteRequest,
)
from route_agent.router_engine.storage import RouterStorage
from route_agent.task_analyzer.schemas import DimensionScore

ClassMatcher = Callable[..., MatchResult | None]
ModelMetadataResolver = Callable[[str], Any | None]
PoolChangeCallback = Callable[[str, list[str]], Awaitable[None]]

logger = logging.getLogger(__name__)


def _normalize(value: str) -> str:
    """Execute `_normalize`."""
    return "_".join((value or "").strip().lower().split())


def _parse_date(value: str | None) -> datetime | None:
    """Execute `_parse_date`."""
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
    """Execute `_wilson_lower_bound`."""
    n = success_count + fail_count
    if n <= 0:
        return 0.0
    p = success_count / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (center - margin) / denom


def profile_to_dimensions(profile: dict[str, int]) -> tuple[DimensionScore, ...]:
    """将 CLASS_DIMENSION_PROFILES 条目转为 DimensionScore 元组。

    federation SDK 的轻量分析构建器复用此函数；空 profile 回退到 GENERAL_FALLBACK_DIMS。
    """
    source = profile if profile else GENERAL_FALLBACK_DIMS
    return tuple(
        DimensionScore(dimension=dim, score=score, reasoning="seed")
        for dim, score in source.items()
    )


# ---------------------------------------------------------------------------
# Federation-weighted scoring helper
# ---------------------------------------------------------------------------

class ClassPoolManager:
    """Maintains per-agent-class model pools and defaults."""

    def __init__(
        self,
        router_storage: RouterStorage,
        model_metadata_resolver: ModelMetadataResolver | None = None,
        class_matcher: ClassMatcher | None = None,
    ) -> None:
        """Initialize the instance."""
        self._storage = router_storage
        self._resolver = model_metadata_resolver
        self._class_matcher = class_matcher
        self._defaults_store = DefaultsStore(router_storage, model_metadata_resolver)
        self._pool_change_callback: PoolChangeCallback | None = None

    def set_pool_change_callback(self, callback: PoolChangeCallback) -> None:
        """Register a callback invoked with (agent_class, model_ids) on pool membership change.

        Used by the federation PoolVersionManager to bump the pool version when
        the class pool is modified via the add/remove channels.
        """
        self._pool_change_callback = callback

    async def _emit_pool_change(self, agent_class: str) -> None:
        """Fetch current pool entries and invoke the callback if registered."""
        if self._pool_change_callback is None:
            return
        entries = await self._storage.get_pool_entries_async(agent_class)
        model_ids = [e.model_id for e in entries]
        try:
            await self._pool_change_callback(agent_class, model_ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pool_change_callback error for class=%s: %s", agent_class, exc
            )

    def _domain_key(self, domain: str) -> str:
        """Execute `_domain_key`."""
        return DEFAULT_DOMAIN_KEY if not domain.strip() else domain.strip()

    async def resolve_class_async(self, request: RouteRequest) -> tuple[str, str]:
        """Execute `resolve_class_async`."""
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
        """Execute `resolve_class`."""
        return asyncio.run(self.resolve_class_async(request))

    async def get_pool_entries_async(self, agent_class: str) -> list[ClassPoolEntry]:
        """Execute `get_pool_entries_async`."""
        return await self._storage.get_pool_entries_async(_normalize(agent_class))

    def get_pool_entries(self, agent_class: str) -> list[ClassPoolEntry]:
        """Execute `get_pool_entries`."""
        return self._storage.get_pool_entries(_normalize(agent_class))

    async def record_outcome(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> None:
        """Execute `record_outcome`."""
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
        """Execute `try_add_to_pool`."""
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

        await self._emit_pool_change(agent_class)
        return True

    async def evict_check(self, agent_class: str) -> list[str]:
        """Execute `evict_check`."""
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
        """Execute `get_default`."""
        default = await self._defaults_store.lookup_default_async(_normalize(agent_class), self._domain_key(domain))
        if default is None:
            return None

        if self._resolver is not None and self._resolver(default.model_id) is None:
            await self._storage.clear_default_async(_normalize(agent_class), self._domain_key(domain))
            return None

        return default.model_id

    async def set_user_override(self, agent_class: str, domain: str, model_id: str) -> None:
        """Execute `set_user_override`."""
        await self._defaults_store.set_user_override_async(_normalize(agent_class), self._domain_key(domain), model_id)

    async def manual_add_to_pool(self, agent_class: str, model_id: str) -> dict[str, str]:
        """手动添加渠道 — 跳过统计门槛，直接入池。

        与 try_add_to_pool（自动统计渠道）并行，两者都通向同一个 class_pool 表。
        """
        normalized_class = _normalize(agent_class)

        if self._resolver is not None and self._resolver(model_id) is None:
            return {"status": "error", "reason": "model_not_found", "model_id": model_id}

        entries = await self._storage.get_pool_entries_async(normalized_class)
        existing_ids = {entry.model_id for entry in entries}
        if model_id in existing_ids:
            return {"status": "already_exists", "agent_class": normalized_class, "model_id": model_id}

        if len(existing_ids) >= POOL_MAX_SIZE:
            return {"status": "error", "reason": "pool_full", "agent_class": normalized_class}

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

        await self._storage.upsert_pool_entry_async(normalized_class, model_id, release_date)
        await self._storage.ensure_stats_row_async(normalized_class, model_id)
        await self._emit_pool_change(normalized_class)

        return {"status": "added", "agent_class": normalized_class, "model_id": model_id}

    async def manual_remove_from_pool(self, agent_class: str, model_id: str) -> dict[str, str]:
        """手动从类池移除模型。"""
        normalized_class = _normalize(agent_class)

        entries = await self._storage.get_pool_entries_async(normalized_class)
        existing_ids = {entry.model_id for entry in entries}
        if model_id not in existing_ids:
            return {"status": "not_found", "agent_class": normalized_class, "model_id": model_id}

        default_ids = await self._storage.list_default_model_ids_async(normalized_class)
        if model_id in default_ids:
            await self._storage.clear_default_async(normalized_class, self._domain_key(""))

        await self._storage.delete_pool_entry_async(normalized_class, model_id)
        await self._emit_pool_change(normalized_class)
        return {"status": "removed", "agent_class": normalized_class, "model_id": model_id}

    async def get_federation_scores_map(
        self,
        agent_class: str,
        local_store: "Any | None",
    ) -> dict[str, tuple[int, int]]:
        """Return {model_id: (success_count, fail_count)} from local federation cache.

        Returns empty dict when local_store is None or no scores cached.
        """
        if local_store is None:
            return {}
        try:
            entries = await local_store.get_federation_scores(agent_class)
            return {e.model_id: (e.success_count, e.fail_count) for e in entries}
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_federation_scores_map failed class=%s: %s", agent_class, exc)
            return {}

    async def seed_pool_async(
        self,
        agent_class: str,
        available_models: list[ModelMetadata],
        local_store: "Any | None" = None,
        local_sample_count: int = 0,
    ) -> list[str]:
        """为类池种子化/补位至 POOL_SEED_SIZE 个模型，返回新入池的 model_id 列表。

        当 local_store 不为 None 时，将联邦分数融入播种排名：
          alpha = 1.0（无本地样本时完全信任联邦）→ 随本地样本积累衰减至 0.3
          seed_score = (1 - alpha) × static_score + alpha × fed_success_rate
        无联邦数据或联邦不可用时退化为原有静态排名。
        """
        from route_agent.router_engine.scorer import compute_cost_score, compute_dimension_score

        normalized = _normalize(agent_class)
        entries = await self._storage.get_pool_entries_async(normalized)
        current_count = len(entries)
        if current_count >= POOL_SEED_SIZE:
            return []

        existing_ids = {entry.model_id for entry in entries}
        slots = POOL_SEED_SIZE - current_count

        profile = CLASS_DIMENSION_PROFILES.get(normalized, {})
        dimensions = profile_to_dimensions(profile)

        # 联邦分数：{model_id: success_rate}，无数据时为空 dict
        fed_scores: dict[str, float] = {}
        if local_store is not None:
            fed_map = await self.get_federation_scores_map(normalized, local_store)
            for model_id, (succ, fail) in fed_map.items():
                total = succ + fail
                if total > 0:
                    fed_scores[model_id] = succ / total

        # alpha 衰减：0 本地样本时 = 1.0（全信联邦），随样本增加衰减至 alpha_floor=0.3
        _alpha_floor = 0.3
        alpha = (
            _alpha_floor + (1.0 - _alpha_floor) * math.exp(-local_sample_count / 100.0)
            if local_sample_count > 0
            else 1.0
        )
        use_federation = bool(fed_scores)

        scored: list[tuple[float, str]] = []
        for model in available_models:
            if model.model_id in existing_ids:
                continue
            dim_score = compute_dimension_score(dimensions, model.capabilities)
            cost_score = compute_cost_score(model.pricing, dimensions)
            # Federation success rate is empirical capability evidence; blend it into
            # dim_score so cost still penalizes and composite stays on a consistent scale.
            if use_federation and model.model_id in fed_scores:
                effective_dim = (1.0 - alpha) * dim_score + alpha * fed_scores[model.model_id]
            else:
                effective_dim = dim_score
            composite = SEED_DIM_WEIGHT * effective_dim - SEED_COST_WEIGHT * cost_score

            scored.append((composite, model.model_id))

        scored.sort(key=lambda item: item[0], reverse=True)

        seeded: list[str] = []
        for _score, model_id in scored:
            if len(seeded) >= slots:
                break
            result = await self.manual_add_to_pool(normalized, model_id)
            if result.get("status") in {"added", "already_exists"}:
                seeded.append(model_id)

        return seeded
