"""Tests for GET /health endpoint."""

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
async def test_health_returns_ok(app) -> None:
    """Test health endpoint returns ok status."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
