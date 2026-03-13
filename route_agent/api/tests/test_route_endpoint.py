"""Tests for POST /route endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

import route_agent.api.routes.route as route_module
from route_agent.api.main import create_app

API_PREFIX = "/api/v1"


def _sample_route_payload(primary_model: str = "deepseek:deepseek-chat") -> dict[str, object]:
    """Build a representative route-service payload for API tests."""
    return {
        "result": None,
        "model_used": primary_model,
        "cost": None,
        "routing_reason": "class default selected at index=0",
        "analysis": {
            "domain": "software_engineering",
            "domain_description": "Code implementation work.",
            "relevant_dimensions": [
                {"dimension": "code", "score": 8, "reasoning": "Complex coding task."},
                {
                    "dimension": "instruction_following",
                    "score": 6,
                    "reasoning": "Needs precise adherence.",
                },
            ],
        },
        "candidates": [{"model_id": primary_model}],
        "start_index": 0,
        "alerts": [],
        "default_used": True,
        "pool_hit": False,
        "pool_class": None,
        "class_source": "llm",
        "analysis_record_id": 42,
        "analysis_fallback": False,
        "pool_summary": {"total_models": 2},
        "registry_alerts": [],
        "registry_errors": {},
        "skipped_providers": [],
        "rate_limiter": {"mode": "inmemory"},
        "registry_sync": {"source": "local_pool_snapshot"},
    }


def _patch_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    primary_model: str = "deepseek:deepseek-chat",
) -> dict[str, object]:
    """Patch the app-service facade used by the API layer."""
    captured: dict[str, object] = {}

    def fake_run_route_agent(request, *, options=None, **_kwargs):
        """Capture inputs passed from the API layer to the app-service facade."""
        captured["request"] = request
        captured["options"] = options
        return _sample_route_payload(primary_model)

    monkeypatch.setattr(route_module, "run_route_agent", fake_run_route_agent)
    return captured


@pytest.fixture()
def app():
    """Create the FastAPI app used in endpoint tests."""
    return create_app()


@pytest.mark.asyncio
async def test_post_route_returns_model(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Agent calls with agent_name + system_prompt + task should return a model."""
    captured = _patch_service(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"{API_PREFIX}/route",
            json={
                "agent_name": "coder-agent",
                "system_prompt": "You are a coding expert.",
                "task": "Write a Python sorting algorithm",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_used"] == "deepseek:deepseek-chat"
    assert data["routing_reason"]
    assert isinstance(data["candidates"], list)
    assert data["analysis"]["domain"] == "software_engineering"

    request = captured["request"]
    options = captured["options"]
    assert request.agent_name == "coder-agent"
    assert request.system_prompt == "You are a coding expert."
    assert request.task == "Write a Python sorting algorithm"
    assert options.default_agent_name == "route_agent"


@pytest.mark.asyncio
async def test_post_route_with_constraints(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Agent constraints should be forwarded through the API adapter."""
    captured = _patch_service(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"{API_PREFIX}/route",
            json={
                "agent_name": "my-agent",
                "task": "translate text",
                "constraints": {
                    "max_cost": 0.05,
                    "require_provider": "deepseek",
                },
            },
        )
    assert resp.status_code == 200
    request = captured["request"]
    assert request.constraints.max_cost == 0.05
    assert request.constraints.require_provider == "deepseek"


@pytest.mark.asyncio
async def test_post_route_empty_task_fallback(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Empty task should use the built-in fallback model without calling the service."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"{API_PREFIX}/route",
            json={
                "agent_name": "test-agent",
                "task": "",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_used"] == "deepseek:deepseek-chat"
    assert "empty_task_fallback" in data["routing_reason"]


@pytest.mark.asyncio
async def test_post_route_blank_task_fallback(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Whitespace-only task should also trigger the built-in fallback."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"{API_PREFIX}/route",
            json={
                "task": "   ",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "deepseek:deepseek-chat"


@pytest.mark.asyncio
async def test_post_route_empty_task_ignores_removed_model_env(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Removed model-slot env vars should not affect the empty-task fallback."""
    monkeypatch.setenv("FAST_LLM", "openai:gpt-override")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"{API_PREFIX}/route",
            json={
                "task": "",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["model_used"] == "deepseek:deepseek-chat"


@pytest.mark.asyncio
async def test_post_route_surfaces_service_validation_error(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Service-side validation errors should become HTTP 422 responses."""
    monkeypatch.setattr(
        route_module,
        "run_route_agent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("request.task is required")),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"{API_PREFIX}/route",
            json={
                "task": "needs validation",
            },
        )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "request.task is required"
