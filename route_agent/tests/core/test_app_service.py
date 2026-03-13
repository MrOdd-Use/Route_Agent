"""Unit tests for the public app-service facade."""

from __future__ import annotations

import pytest

import route_agent.app.service as service_module
from route_agent.app.contracts import RouteAgentRequest, RouteAgentRunOptions


def test_run_route_agent_requires_task() -> None:
    """The public service should reject missing tasks."""
    with pytest.raises(ValueError, match="request.task is required"):
        service_module.run_route_agent({"task": "   "})


def test_run_route_agent_normalizes_request_and_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loose input payloads should be normalized before orchestration runs."""
    captured: dict[str, object] = {}
    fake_execution = object()

    def fake_execute_route(request: RouteAgentRequest, options: RouteAgentRunOptions) -> object:
        """Capture normalized inputs passed into the orchestrator."""
        captured["request"] = request
        captured["options"] = options
        return fake_execution

    def fake_build_route_payload(*, execution: object, sync_interval_days: int) -> dict[str, object]:
        """Return a compact payload for the facade test."""
        return {
            "execution": execution,
            "sync_interval_days": sync_interval_days,
        }

    monkeypatch.setattr(service_module, "execute_route", fake_execute_route)
    monkeypatch.setattr(service_module, "build_route_payload", fake_build_route_payload)

    payload = service_module.run_route_agent(
        {
            "task": "Write a Python function to sort numbers.",
            "system_prompt": "You are a coding expert.",
            "constraints": {
                "max_cost": "0.05",
                "preferred_model": "openai:gpt-smart",
                "exclude_models": ["openai:gpt-legacy"],
                "require_provider": "openai",
                "estimated_input_tokens": "2048",
            },
        },
        limit=11,
        sync_interval_days=14,
        agent_name="coder-agent",
        redis_url="redis://localhost:6379/0",
        rate_limit_mode="inmemory",
        analysis_db_path="data/analysis.db",
    )

    request = captured["request"]
    options = captured["options"]
    assert isinstance(request, RouteAgentRequest)
    assert request.agent_name == "coder-agent"
    assert request.request_id is not None
    assert request.system_prompt == "You are a coding expert."
    assert request.constraints.max_cost == 0.05
    assert request.constraints.preferred_model == "openai:gpt-smart"
    assert request.constraints.exclude_models == ("openai:gpt-legacy",)
    assert request.constraints.require_provider == "openai"
    assert request.constraints.estimated_input_tokens == 2048

    assert isinstance(options, RouteAgentRunOptions)
    assert options.limit == 11
    assert options.sync_interval_days == 14
    assert options.default_agent_name == "coder-agent"
    assert options.redis_url == "redis://localhost:6379/0"
    assert options.rate_limit_mode == "inmemory"
    assert options.analysis_db_path == "data/analysis.db"

    assert payload == {
        "execution": fake_execution,
        "sync_interval_days": 14,
    }


def test_run_route_agent_accepts_prebuilt_request_and_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """Callers may pass normalized request/options objects directly."""
    request = RouteAgentRequest(task="Review this code path.", agent_name="reviewer")
    options = RouteAgentRunOptions(limit=3, sync_interval_days=9, default_agent_name="reviewer")
    captured: dict[str, object] = {}

    def fake_execute_route(route_request: RouteAgentRequest, route_options: RouteAgentRunOptions) -> object:
        """Capture prebuilt objects passed into the orchestrator."""
        captured["request"] = route_request
        captured["options"] = route_options
        return {"ok": True}

    monkeypatch.setattr(service_module, "execute_route", fake_execute_route)
    monkeypatch.setattr(
        service_module,
        "build_route_payload",
        lambda *, execution, sync_interval_days: {
            "execution": execution,
            "sync_interval_days": sync_interval_days,
        },
    )

    payload = service_module.run_route_agent(request, options=options)

    assert captured["request"] is request
    assert captured["options"] is options
    assert payload["execution"] == {"ok": True}
    assert payload["sync_interval_days"] == 9
