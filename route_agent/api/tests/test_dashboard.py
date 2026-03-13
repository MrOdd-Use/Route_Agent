"""Tests for monitoring dashboard UI and agent-status endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from route_agent.api.main import create_app

API_PREFIX = "/api/v1"


@pytest.fixture()
def app():
    """Create a fresh app instance for each test."""
    return create_app()


# ---------------------------------------------------------------------------
# GET /agent-status — JSON endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_status_returns_200(app) -> None:
    """Agent status endpoint should return 200 with expected structure."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/agent-status")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_agents" in data
    assert "agents" in data
    assert isinstance(data["agents"], list)
    assert "status_counts" in data
    assert "model_counts" in data


@pytest.mark.asyncio
async def test_agent_status_empty_shows_zero(app) -> None:
    """With no execution data, agent-status should show zero agents."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/agent-status")
    data = resp.json()
    assert data["total_agents"] == 0
    assert data["agents"] == []


@pytest.mark.asyncio
async def test_agent_status_with_query_params(app) -> None:
    """Agent status endpoint should accept limit, source, since_hours."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(
            f"{API_PREFIX}/agent-status",
            params={"limit": 10, "source": "api", "since_hours": 1},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "agents" in data


# ---------------------------------------------------------------------------
# GET /dashboard — HTML page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_returns_html(app) -> None:
    """Dashboard endpoint should return an HTML page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_dashboard_has_two_views(app) -> None:
    """Dashboard HTML should contain both Model Status and Agent Assignment views."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    # Tab buttons for switching
    assert 'id="tab-models"' in html
    assert 'id="tab-agents"' in html
    # View containers
    assert 'id="view-models"' in html
    assert 'id="view-agents"' in html


@pytest.mark.asyncio
async def test_dashboard_model_view_has_metrics(app) -> None:
    """Model Status view should display key metric columns."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    # Column headers for model metrics
    for col in ("Model", "Provider", "Success Rate", "Avg Latency"):
        assert col in html, f"Missing column: {col}"


@pytest.mark.asyncio
async def test_dashboard_agent_view_has_columns(app) -> None:
    """Agent Assignment view should display agent_id, request_id, SP columns."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    for col in ("Agent", "Request ID", "Model", "Status"):
        assert col in html, f"Missing column: {col}"


@pytest.mark.asyncio
async def test_dashboard_empty_state_message(app) -> None:
    """When no agents are active, dashboard should show empty state message."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    assert "no-agent-msg" in html or "无 agent 调用" in html.lower() or "no agent" in html.lower()


@pytest.mark.asyncio
async def test_dashboard_auto_refresh(app) -> None:
    """Dashboard should include auto-refresh logic."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    # Should have setInterval or setTimeout for polling
    assert "setInterval" in html or "setTimeout" in html


@pytest.mark.asyncio
async def test_dashboard_tab_switching_js(app) -> None:
    """Dashboard should include JavaScript for tab switching."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    assert "switchTab" in html or "switchView" in html
