"""Tests for default promotion tie-break behavior."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from route_agent.router_engine.defaults import DefaultsStore
from route_agent.router_engine.schemas import ClassModelStats, ClassPoolDefault


class _FakeStorage:
    def __init__(
        self,
        stats_items: list[ClassModelStats],
        current_default: ClassPoolDefault | None = None,
    ) -> None:
        self._stats_items = stats_items
        self._default = current_default

    async def get_default_async(self, _agent_class: str, _domain: str) -> ClassPoolDefault | None:
        return self._default

    async def list_stats_for_class_async(self, _agent_class: str) -> list[ClassModelStats]:
        return list(self._stats_items)

    async def get_availability_async(self, _model_id: str) -> None:
        return None

    async def upsert_default_async(
        self,
        agent_class: str,
        domain: str,
        model_id: str,
        *,
        consecutive_success: int = 0,
        consecutive_fail: int = 0,
        is_locked: bool = False,
    ) -> None:
        self._default = ClassPoolDefault(
            agent_class=agent_class,
            domain=domain,
            model_id=model_id,
            is_locked=is_locked,
            consecutive_success=consecutive_success,
            consecutive_fail=consecutive_fail,
        )


def _stats(model_id: str) -> ClassModelStats:
    return ClassModelStats(
        agent_class="general",
        model_id=model_id,
        success_count=30,
        fail_count=2,
        consecutive_success=5,
    )


def test_promote_prefers_newer_release_when_wlb_and_price_tied() -> None:
    storage = _FakeStorage(stats_items=[_stats("model-old"), _stats("model-new")])
    metadata = {
        "model-old": SimpleNamespace(
            pricing={"input": "1.0", "unit": "per_1m_tokens"},
            routing={"release_date": "2024-01-01"},
        ),
        "model-new": SimpleNamespace(
            pricing={"input": "1.0", "unit": "per_1m_tokens"},
            routing={"release_date": "2025-05-20"},
        ),
    }
    store = DefaultsStore(storage, model_metadata_resolver=lambda model_id: metadata[model_id])

    promoted = asyncio.run(store.evaluate_and_promote_default_async("general", "coding", min_success=1))

    assert promoted is not None
    assert promoted.model_id == "model-new"


def test_promote_prefers_known_release_over_missing_release_when_tied() -> None:
    storage = _FakeStorage(stats_items=[_stats("model-missing"), _stats("model-dated")])
    metadata = {
        "model-missing": SimpleNamespace(
            pricing={"input": "0.8", "unit": "per_1m_tokens"},
            routing={},
        ),
        "model-dated": SimpleNamespace(
            pricing={"input": "0.8", "unit": "per_1m_tokens"},
            routing={"release_date": "2025-01-15"},
        ),
    }
    store = DefaultsStore(storage, model_metadata_resolver=lambda model_id: metadata[model_id])

    promoted = asyncio.run(store.evaluate_and_promote_default_async("general", "coding", min_success=1))

    assert promoted is not None
    assert promoted.model_id == "model-dated"
