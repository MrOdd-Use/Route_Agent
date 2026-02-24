"""Unit tests for route_agent.main.run_route_agent."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import route_agent.main as main_module


class _FakeSkippedProvider:
    def __init__(self, provider: str, reason: str) -> None:
        self.provider = provider
        self.reason = reason

    def to_dict(self) -> dict[str, str]:
        return {"provider": self.provider, "reason": self.reason}


class _FakePool:
    def __init__(self, *, with_model: bool = True) -> None:
        self.with_model = with_model
        self.calls: list[tuple[str, float | None, str | None]] = []

    def pick_tier(
        self,
        tier: str,
        *,
        max_cost: float | None = None,
        preferred_model: str | None = None,
    ) -> SimpleNamespace:
        self.calls.append((tier, max_cost, preferred_model))
        model = SimpleNamespace(model_id=f"mock:{tier}") if self.with_model else None
        return SimpleNamespace(
            tier=tier,
            model=model,
            reason=f"picked:{tier}",
        )

    def summary(self) -> dict[str, object]:
        return {"total_models": 2, "available_models": 2, "providers": ["mock"], "slots": {}}


def _setup_mocks(
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_model: bool = True,
) -> _FakePool:
    pool = _FakePool(with_model=with_model)

    report = SimpleNamespace(
        alerts=["low model count"],
        errors={"openai": "rate limited"},
        skipped_providers=[
            _FakeSkippedProvider("anthropic", "not configured"),
        ],
    )
    local_result = SimpleNamespace(
        report=report,
        source="local_pool_snapshot",
        storage_backend="sqlite",
        sync_due=False,
        sync_performed=False,
        snapshot_version="snap-1",
    )

    def fake_get_model_registry_report_with_local_pool(**_kwargs: object) -> SimpleNamespace:
        return local_result

    class _FakeMainModelPool:
        @staticmethod
        def from_report(*_args: object, **_kwargs: object) -> _FakePool:
            return pool

    monkeypatch.setattr(
        main_module,
        "get_model_registry_report_with_local_pool",
        fake_get_model_registry_report_with_local_pool,
    )
    monkeypatch.setattr(main_module, "MainModelPool", _FakeMainModelPool)
    return pool


def test_run_route_agent_requires_task() -> None:
    with pytest.raises(ValueError, match="request.task is required"):
        main_module.run_route_agent({"task": "   "})


def test_run_route_agent_passes_constraints_to_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _setup_mocks(monkeypatch, with_model=True)
    payload = main_module.run_route_agent(
        {
            "task": "Write a Python function to sort numbers.",
            "constraints": {"max_cost": "0.05", "preferred_model": "openai:gpt-test"},
        }
    )

    assert pool.calls == [("smart", 0.05, "openai:gpt-test")]
    assert payload["selected_tier"] == "smart"
    assert payload["model_used"] == "mock:smart"


def test_run_route_agent_uses_fast_tier_for_very_low_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _setup_mocks(monkeypatch, with_model=True)
    payload = main_module.run_route_agent(
        {
            "task": "General QA task",
            "constraints": {"max_cost": 0.01},
        }
    )

    assert pool.calls == [("fast", 0.01, None)]
    assert payload["selected_tier"] == "fast"
    assert payload["model_used"] == "mock:fast"


def test_run_route_agent_appends_registry_errors_when_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _setup_mocks(monkeypatch, with_model=False)
    payload = main_module.run_route_agent({"task": "Quick question"})

    assert payload["model_used"] is None
    assert "registry_errors={'openai': 'rate limited'}" in payload["routing_reason"]
    assert payload["registry_sync"]["source"] == "local_pool_snapshot"


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("Please scrape this website and parse html.", "scrape"),
        ("Extract entities into structured json output.", "extraction"),
        ("Summarize this report into a brief.", "summarization"),
        ("Classify the sentiment label of this review.", "classification"),
        ("Rewrite this paragraph to be clearer.", "rewrite"),
        ("Audit this architecture and give assessment.", "review"),
    ],
)
def test_detect_task_type_new_categories(task: str, expected: str) -> None:
    assert main_module._detect_task_type(task) == expected
