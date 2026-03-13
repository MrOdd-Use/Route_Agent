"""Tests for GET /health endpoint."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

import route_agent.api.dependencies as dependencies_module
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


@pytest.mark.asyncio
async def test_health_loads_monitoring_flag_from_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """API settings should load `.env` so monitoring flags affect health output."""
    monkeypatch.delenv("ROUTE_AGENT_MONITORING_ENABLED", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ROUTE_AGENT_MONITORING_ENABLED=true\n", encoding="utf-8")
    dependencies_module.get_api_settings.cache_clear()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(f"{API_PREFIX}/health")
    finally:
        dependencies_module.get_api_settings.cache_clear()

    assert resp.status_code == 200
    assert resp.json()["monitoring_enabled"] is True
