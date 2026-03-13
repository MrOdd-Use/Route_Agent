"""Tests for dashboard HTML pages and agent-status endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from route_agent.api.main import create_app

API_PREFIX = "/api/v1"


@pytest.fixture()
def app():
    """Create a fresh app instance for each test."""
    return create_app()


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


@pytest.mark.asyncio
async def test_dashboard_returns_html(app) -> None:
    """Dashboard endpoint should return an HTML page."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_dashboard_has_three_views(app) -> None:
    """Dashboard HTML should contain global pool, class pools, and agents views."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    assert 'id="tab-global"' in html
    assert 'id="tab-classes"' in html
    assert 'id="tab-agents"' in html
    assert 'id="view-global"' in html
    assert 'id="view-classes"' in html
    assert 'id="view-agents"' in html


@pytest.mark.asyncio
async def test_dashboard_has_card_grids(app) -> None:
    """Dashboard HTML should expose the global and class card containers."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    assert 'id="global-card-grid"' in html
    assert 'id="class-card-grid"' in html
    assert "/pool-status/global" in html
    assert "/pool-status/classes" in html


@pytest.mark.asyncio
async def test_dashboard_keeps_agent_assignment_table(app) -> None:
    """Dashboard should keep the agent assignment table columns."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    for col in ("Agent", "Request ID", "Model", "Status"):
        assert col in html, f"Missing column: {col}"


@pytest.mark.asyncio
async def test_dashboard_has_empty_states_and_refresh(app) -> None:
    """Dashboard should include empty states and auto-refresh behavior."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard")
    html = resp.text
    assert 'id="global-empty"' in html
    assert 'id="class-empty"' in html
    assert 'id="agent-empty"' in html
    assert "setInterval(refresh, REFRESH_MS)" in html


@pytest.mark.asyncio
async def test_class_pool_dashboard_returns_html(app) -> None:
    """Class-pool detail dashboard should return HTML."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard/class-pools/coder")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_class_pool_dashboard_has_back_link_and_cards(app) -> None:
    """Class-pool detail page should include a back link and card grid."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/dashboard/class-pools/coder")
    html = resp.text
    assert "Back to Dashboard" in html
    assert 'id="detail-card-grid"' in html
    assert "/pool-status/classes/" in html
    assert "setInterval(refresh, REFRESH_MS)" in html
