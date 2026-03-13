"""Integration tests that call real LLM providers via .env API keys.

These tests require valid API keys in .env and network access.
Run with: uv run pytest route_agent/api/tests/test_integration_real.py -v
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from route_agent.api.main import create_app

API_PREFIX = "/api/v1"


@pytest.fixture()
def app():
    """Execute `app`."""
    return create_app()


@pytest.mark.asyncio
async def test_real_route_returns_model(app) -> None:
    """Full round-trip: agent_name + system_prompt + task -> real model selection."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=60.0,
    ) as client:
        resp = await client.post(f"{API_PREFIX}/route", json={
            "agent_name": "integration-test-agent",
            "system_prompt": "You are a helpful coding assistant.",
            "task": "Write a Python function that calculates the Fibonacci sequence",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_used"] is not None, f"Expected a model, got None. Full response: {data}"
    assert ":" in data["model_used"], "model_used should be in provider:model format"
    assert len(data["candidates"]) > 0
    assert data["analysis"]["domain"]
    assert data["routing_reason"]


@pytest.mark.asyncio
async def test_real_route_with_constraints(app) -> None:
    """Route with require_provider constraint."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=60.0,
    ) as client:
        resp = await client.post(f"{API_PREFIX}/route", json={
            "agent_name": "constrained-agent",
            "task": "Summarize the benefits of microservices architecture",
            "constraints": {
                "require_provider": "deepseek",
            },
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["model_used"] is not None
    assert data["model_used"].startswith("deepseek:")




@pytest.mark.asyncio
async def test_real_models_list(app) -> None:
    """Models endpoint returns real registered models."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        timeout=60.0,
    ) as client:
        resp = await client.get(f"{API_PREFIX}/models")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 0
    assert len(data["models"]) > 0
    first_model = data["models"][0]
    assert "model_id" in first_model
    assert "provider" in first_model
