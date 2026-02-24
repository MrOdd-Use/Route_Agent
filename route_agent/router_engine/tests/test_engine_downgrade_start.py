"""Tests for downgrade-trial start integration in RouterEngine."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from route_agent.router_engine.engine import RouterEngine


class _FakePool:
    """Minimal pool implementation required by RouterEngine."""

    def get(self, _model_id: str) -> SimpleNamespace | None:
        """Execute `get`."""
        return None

    def list_available(self, provider: str | None = None) -> list[SimpleNamespace]:
        """Execute `list_available`."""
        _ = provider
        return [
            SimpleNamespace(
                model_id="openai:gpt-smart",
                provider="openai",
                display_name="gpt-smart",
                capabilities={},
                pricing={"input": 1.0, "output": 1.0, "unit": "per_1m_tokens"},
                limits={},
                routing={},
            )
        ]


class _FakeAnalysisStorage:
    """Minimal analysis storage for RouterEngine constructor."""

    async def update_quality_review_async(
        self,
        record_id: int,
        rating: str,
        action: str | None,
        note: str | None,
    ) -> None:
        """Execute `update_quality_review_async`."""
        _ = (record_id, rating, action, note)

    async def update_execution_result_async(
        self,
        record_id: int,
        completed: bool,
        error_type: str | None,
        error_detail: str | None,
    ) -> None:
        """Execute `update_execution_result_async`."""
        _ = (record_id, completed, error_type, error_detail)


def test_report_quality_records_downgrade_start_when_trial_begins(tmp_path, monkeypatch) -> None:
    """Test quality-good flow records `downgrade_start` when trial starts."""
    engine = RouterEngine(
        _FakePool(),
        _FakeAnalysisStorage(),
        router_db_path=tmp_path / "router_engine.db",
    )

    async def _record_continue(
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> str:
        """Execute `_record_continue`."""
        _ = (agent_class, domain, model_id, outcome_type)
        return "continue"

    async def _start_trial(agent_class: str, domain: str, model_id: str) -> str | None:
        """Execute `_start_trial`."""
        _ = (agent_class, domain, model_id)
        return "openai:gpt-cheap"

    monkeypatch.setattr(
        engine._downgrade_optimizer,  # noqa: SLF001
        "record_downgrade_result_async",
        _record_continue,
    )
    monkeypatch.setattr(
        engine._downgrade_optimizer,  # noqa: SLF001
        "maybe_start_downgrade_trial_async",
        _start_trial,
    )

    asyncio.run(
        engine.report_execution_async(
            request_id="req-1",
            record_id=None,
            model_id="openai:gpt-smart",
            agent_name="route_agent",
            agent_class="general",
            domain="coding",
            completed=True,
        )
    )
    asyncio.run(
        engine.report_quality_async(
            request_id="req-1",
            record_id=None,
            model_id="openai:gpt-smart",
            agent_name="route_agent",
            agent_class="general",
            domain="coding",
            rating="good",
        )
    )

    logs = engine._router_storage.query_all(limit=10)  # noqa: SLF001
    outcomes = [str(row["outcome"]) for row in logs]
    assert "success" in outcomes
    assert "downgrade_start" in outcomes

    downgrade_rows = [row for row in logs if row["outcome"] == "downgrade_start"]
    assert downgrade_rows
    assert downgrade_rows[0]["model_id"] == "openai:gpt-cheap"


def test_report_quality_does_not_start_new_trial_after_promote(tmp_path, monkeypatch) -> None:
    """Test quality-good flow does not start trial when downgrade was promoted."""
    engine = RouterEngine(
        _FakePool(),
        _FakeAnalysisStorage(),
        router_db_path=tmp_path / "router_engine.db",
    )

    async def _record_promote(
        agent_class: str,
        domain: str,
        model_id: str,
        outcome_type: str,
    ) -> str:
        """Execute `_record_promote`."""
        _ = (agent_class, domain, model_id, outcome_type)
        return "promote"

    async def _fail_if_called(agent_class: str, domain: str, model_id: str) -> str | None:
        """Execute `_fail_if_called`."""
        _ = (agent_class, domain, model_id)
        raise AssertionError("maybe_start_downgrade_trial_async should not be called after promote")

    monkeypatch.setattr(
        engine._downgrade_optimizer,  # noqa: SLF001
        "record_downgrade_result_async",
        _record_promote,
    )
    monkeypatch.setattr(
        engine._downgrade_optimizer,  # noqa: SLF001
        "maybe_start_downgrade_trial_async",
        _fail_if_called,
    )

    asyncio.run(
        engine.report_execution_async(
            request_id="req-2",
            record_id=None,
            model_id="openai:gpt-smart",
            agent_name="route_agent",
            agent_class="general",
            domain="coding",
            completed=True,
        )
    )
    asyncio.run(
        engine.report_quality_async(
            request_id="req-2",
            record_id=None,
            model_id="openai:gpt-smart",
            agent_name="route_agent",
            agent_class="general",
            domain="coding",
            rating="good",
        )
    )
