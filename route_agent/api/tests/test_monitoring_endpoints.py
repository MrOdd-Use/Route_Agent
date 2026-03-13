"""Tests for monitoring endpoints (stats, decisions, executions)."""

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
async def test_get_stats(app) -> None:
    """Test stats endpoint returns windowed data."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_get_decisions(app) -> None:
    """Test decisions endpoint returns list."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/decisions")
    assert resp.status_code == 200
    data = resp.json()
    assert "decisions" in data
    assert isinstance(data["decisions"], list)


@pytest.mark.asyncio
async def test_get_executions(app) -> None:
    """Test executions endpoint returns list."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/executions")
    assert resp.status_code == 200
    data = resp.json()
    assert "executions" in data
    assert isinstance(data["executions"], list)
