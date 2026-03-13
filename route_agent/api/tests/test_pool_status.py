"""Tests for pool-status helpers and API endpoints."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

import route_agent.api.routes.pool_status as pool_status_route
import route_agent.monitoring as monitoring_module
from route_agent.api.main import create_app
from route_agent.api.pool_status import build_class_pool_detail_payload, build_global_pool_status_payload

API_PREFIX = "/api/v1"


def _model(model_id: str, provider: str, display_name: str, availability: str | None = None) -> SimpleNamespace:
    """Build a minimal model-like object for pool-status tests."""
    return SimpleNamespace(
        model_id=model_id,
        provider=provider,
        display_name=display_name,
        status={} if availability is None else {"availability": availability},
    )


@pytest.fixture()
def app():
    """Create the FastAPI app used in pool-status tests."""
    return create_app()


def test_build_global_pool_status_payload_covers_status_rules() -> None:
    """Global pool status helper should cover unavailable, idle, healthy, warning, and degraded."""
    payload = build_global_pool_status_payload(
        models=[
            _model("deepseek:down", "deepseek", "Down model", "offline"),
            _model("deepseek:idle", "deepseek", "Idle model"),
            _model("deepseek:healthy", "deepseek", "Healthy model"),
            _model("deepseek:warning", "deepseek", "Warning model"),
            _model("deepseek:degraded", "deepseek", "Degraded model"),
        ],
        execution_metrics=[
            {"model_id": "deepseek:healthy", "provider": "deepseek", "request_count": 10, "success_count": 9},
            {"model_id": "deepseek:warning", "provider": "deepseek", "request_count": 10, "success_count": 5},
            {"model_id": "deepseek:degraded", "provider": "deepseek", "request_count": 8, "success_count": 2},
        ],
    )

    cards = {card["model_id"]: card for card in payload["cards"]}
    assert cards["deepseek:down"]["status"] == "unavailable"
    assert cards["deepseek:idle"]["status"] == "idle"
    assert cards["deepseek:healthy"]["status"] == "healthy"
    assert cards["deepseek:warning"]["status"] == "warning"
    assert cards["deepseek:degraded"]["status"] == "degraded"


def test_build_class_pool_detail_payload_falls_back_when_registry_model_missing() -> None:
    """Class-pool detail helper should keep rendering cards for models missing from the registry pool."""
    payload = build_class_pool_detail_payload(
        agent_class="coder",
        default_model="custom:model-x",
        pool_entries=[
            {
                "model_id": "custom:model-x",
                "success_count": 3,
                "fail_count": 1,
                "success_rate": 0.75,
                "updated_at": "2026-03-13 10:45:31",
                "model_release_date": "2025-01-01",
            }
        ],
        default_model_ids={"custom:model-x"},
        history_rows=[
            {
                "model_id": "custom:model-x",
                "outcome": "success",
                "created_at": "2026-03-13 10:45:30",
            }
        ],
        model_resolver=lambda _model_id: None,
    )

    assert payload["default_model"] == "custom:model-x"
    assert payload["cards"][0]["model_id"] == "custom:model-x"
    assert payload["cards"][0]["display_name"] == "custom:model-x"
    assert payload["cards"][0]["provider"] == "custom"
    assert payload["cards"][0]["is_default"] is True


@pytest.mark.asyncio
async def test_pool_status_global_endpoint_returns_cards(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """GET /pool-status/global should return summary plus model cards."""
    fake_pool = SimpleNamespace(
        get=lambda _model_id: None,
    )
    fake_context = SimpleNamespace(
        report=SimpleNamespace(models=[_model("deepseek:deepseek-chat", "deepseek", "DeepSeek Chat")]),
        pool=fake_pool,
    )

    async def fake_metrics() -> list[dict[str, object]]:
        """Return one healthy execution aggregate for the fake global pool."""
        return [{"model_id": "deepseek:deepseek-chat", "provider": "deepseek", "request_count": 4, "success_count": 4}]

    monkeypatch.setattr(pool_status_route, "get_registry_context", lambda: fake_context)
    monkeypatch.setattr(monitoring_module, "get_model_execution_metrics_async", fake_metrics)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/pool-status/global")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_models"] == 1
    assert data["cards"][0]["model_id"] == "deepseek:deepseek-chat"
    assert data["cards"][0]["status"] == "healthy"


@pytest.mark.asyncio
async def test_pool_status_class_list_endpoint_returns_existing_pools(
    monkeypatch: pytest.MonkeyPatch,
    app,
) -> None:
    """GET /pool-status/classes should return existing class pools."""
    fake_context = SimpleNamespace(pool=SimpleNamespace())

    class _FakeEngine:
        """Minimal router-engine stub for class directory tests."""

        async def list_pools_async(self) -> list[dict[str, object]]:
            """Return one existing class-pool summary row."""
            return [
                {
                    "agent_class": "coder",
                    "model_count": 2,
                    "default_model": "deepseek:deepseek-chat",
                    "last_updated_at": "2026-03-13 10:45:31",
                }
            ]

    monkeypatch.setattr(pool_status_route, "get_registry_context", lambda: fake_context)
    monkeypatch.setattr(pool_status_route, "get_api_settings", lambda: None)
    monkeypatch.setattr(pool_status_route, "build_router_engine", lambda _pool, _settings=None: _FakeEngine())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/pool-status/classes")

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["classes"][0]["agent_class"] == "coder"


@pytest.mark.asyncio
async def test_pool_status_class_detail_endpoint_returns_cards(monkeypatch: pytest.MonkeyPatch, app) -> None:
    """GET /pool-status/classes/{agent_class} should return class-pool detail cards."""
    registry_models = {
        "deepseek:deepseek-chat": _model("deepseek:deepseek-chat", "deepseek", "DeepSeek Chat"),
    }
    fake_context = SimpleNamespace(
        pool=SimpleNamespace(get=lambda model_id: registry_models.get(model_id)),
    )

    class _FakeEngine:
        """Router-engine stub returning one class-pool detail payload."""

        async def inspect_pool_async(self, agent_class: str) -> list[dict[str, object]]:
            """Return one class-pool entry for the requested class."""
            assert agent_class == "coder"
            return [
                {
                    "agent_class": "coder",
                    "model_id": "deepseek:deepseek-chat",
                    "success_count": 3,
                    "fail_count": 1,
                    "success_rate": 0.75,
                    "created_at": "2026-03-13 10:45:00",
                    "updated_at": "2026-03-13 10:45:31",
                    "model_release_date": "2025-01-01",
                }
            ]

        async def list_default_model_ids_async(self, agent_class: str) -> set[str]:
            """Return the default-model set for the requested class."""
            assert agent_class == "coder"
            return {"deepseek:deepseek-chat"}

        async def list_pools_async(self) -> list[dict[str, object]]:
            """Return summary metadata for the fake class pool."""
            return [{"agent_class": "coder", "default_model": "deepseek:deepseek-chat", "last_updated_at": "2026-03-13 10:45:31"}]

        async def query_class_history_async(self, agent_class: str, limit: int = 500) -> list[dict[str, object]]:
            """Return one recent success history row for the requested class."""
            assert agent_class == "coder"
            assert limit == 500
            return [{"model_id": "deepseek:deepseek-chat", "outcome": "success", "created_at": "2026-03-13 10:45:30"}]

    monkeypatch.setattr(pool_status_route, "get_registry_context", lambda: fake_context)
    monkeypatch.setattr(pool_status_route, "get_api_settings", lambda: None)
    monkeypatch.setattr(pool_status_route, "build_router_engine", lambda _pool, _settings=None: _FakeEngine())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/pool-status/classes/coder")

    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_class"] == "coder"
    assert data["cards"][0]["model_id"] == "deepseek:deepseek-chat"
    assert data["cards"][0]["is_default"] is True


@pytest.mark.asyncio
async def test_pool_status_class_detail_returns_404_for_missing_class(
    monkeypatch: pytest.MonkeyPatch,
    app,
) -> None:
    """Unknown class pools should return HTTP 404."""
    fake_context = SimpleNamespace(pool=SimpleNamespace(get=lambda _model_id: None))

    class _FakeEngine:
        """Router-engine stub returning no pool entries."""

        async def inspect_pool_async(self, _agent_class: str) -> list[dict[str, object]]:
            """Return an empty class-pool listing."""
            return []

    monkeypatch.setattr(pool_status_route, "get_registry_context", lambda: fake_context)
    monkeypatch.setattr(pool_status_route, "get_api_settings", lambda: None)
    monkeypatch.setattr(pool_status_route, "build_router_engine", lambda _pool, _settings=None: _FakeEngine())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"{API_PREFIX}/pool-status/classes/missing")

    assert resp.status_code == 404
