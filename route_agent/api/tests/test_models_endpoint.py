"""Tests for GET /models endpoint."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

import route_agent.app.service as service_module
from route_agent.api.main import create_app
from route_agent.api.tests.conftest import (
    FakeAnalysisStorage,
    FakeEngine,
    FakePool,
    fake_registry_report,
    sample_decision,
)

API_PREFIX = "/api/v1"


@pytest.fixture()
def app():
    """Execute `app`."""
    return create_app()


@pytest.mark.asyncio
async def test_get_models_returns_list(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Test models endpoint returns a list."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert isinstance(data["models"], list)


@pytest.mark.asyncio
async def test_get_models_filter_by_provider(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """Test models endpoint with provider filter."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/models?provider=deepseek")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["models"], list)
