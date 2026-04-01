"""Tests for Federation API routes (Phase 3).

Uses FastAPI TestClient with isolated in-memory / temp-SQLite dependencies
injected via lru_cache replacement.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from route_agent.federation.api import routes as fed_routes
from route_agent.federation.server.app_registry import AppRegistry
from route_agent.federation.server.lease_limiter import LeaseLimiter
from route_agent.federation.server.lease_store import LeaseStore
from route_agent.federation.server.outcome_processor import OutcomeProcessor
from route_agent.federation.server.pool_version import PoolVersionManager
from route_agent.federation.server.storage import FederationStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_storage(tmp_path: Path) -> FederationStorage:
    """Isolated FederationStorage backed by a temp SQLite file."""
    return FederationStorage(tmp_path / "fed_test.db")


@pytest.fixture()
def pool_mgr(tmp_storage: FederationStorage) -> PoolVersionManager:
    """PoolVersionManager wired to the isolated storage."""
    return PoolVersionManager(tmp_storage)


@pytest.fixture()
def app_registry(tmp_storage: FederationStorage, pool_mgr: PoolVersionManager) -> AppRegistry:
    """AppRegistry wired to the isolated storage and pool manager."""
    return AppRegistry(tmp_storage, pool_mgr)


@pytest.fixture()
def lease_store() -> LeaseStore:
    """Fresh in-memory LeaseStore for each test."""
    return LeaseStore()


@pytest.fixture()
def lease_limiter(lease_store: LeaseStore) -> LeaseLimiter:
    """LeaseLimiter backed by the isolated lease store."""
    return LeaseLimiter(lease_store)


@pytest.fixture()
def outcome_processor(tmp_storage: FederationStorage, pool_mgr: PoolVersionManager) -> OutcomeProcessor:
    """OutcomeProcessor with a low significance threshold for test convenience."""
    return OutcomeProcessor(tmp_storage, pool_mgr, significance_threshold=3)


@pytest.fixture()
def client(
    tmp_storage: FederationStorage,
    app_registry: AppRegistry,
    lease_store: LeaseStore,
    lease_limiter: LeaseLimiter,
    outcome_processor: OutcomeProcessor,
    pool_mgr: PoolVersionManager,
) -> Generator[TestClient, None, None]:
    """TestClient with all federation singletons replaced by isolated fixtures."""
    with (
        patch.object(fed_routes, "_get_storage", return_value=tmp_storage),
        patch.object(fed_routes, "_get_app_registry", return_value=app_registry),
        patch.object(fed_routes, "_get_lease_store", return_value=lease_store),
        patch.object(fed_routes, "_get_lease_limiter", return_value=lease_limiter),
        patch.object(fed_routes, "_get_outcome_processor", return_value=outcome_processor),
        patch.object(fed_routes, "_get_pool_version_mgr", return_value=pool_mgr),
    ):
        from route_agent.api.main import create_app
        from route_agent.api.config import ApiSettings

        settings = ApiSettings()
        app = create_app(settings)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# POST /apps/register
# ---------------------------------------------------------------------------


def test_register_app_success(client: TestClient) -> None:
    """POST /apps/register returns 200 with app_id and pool_versions."""
    resp = client.post(
        f"{PREFIX}/apps/register",
        json={
            "app_id": "test_app",
            "app_name": "Test App",
            "agents": [
                {"agent_name": "planner", "agent_class": "general", "agent_version": "v1"},
                {"agent_name": "fetcher", "agent_class": "scrape", "agent_version": "v1"},
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["app_id"] == "test_app"
    assert "registered_at" in data
    assert "pool_versions" in data
    assert isinstance(data["pool_versions"], dict)


def test_register_app_empty_agents(client: TestClient) -> None:
    """POST /apps/register succeeds with an empty agents list."""
    resp = client.post(
        f"{PREFIX}/apps/register",
        json={"app_id": "empty_app", "app_name": "Empty App", "agents": []},
    )
    assert resp.status_code == 200
    assert resp.json()["app_id"] == "empty_app"


def test_register_app_failure_returns_generic_error(client: TestClient) -> None:
    """POST /apps/register should not leak internal exception text."""
    with patch.object(
        fed_routes,
        "_get_app_registry",
        return_value=type(
            "_BrokenRegistry",
            (),
            {"register_async": staticmethod(_raise_register_failure)},
        )(),
    ):
        resp = client.post(
            f"{PREFIX}/apps/register",
            json={"app_id": "broken_app", "app_name": "Broken App", "agents": []},
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "internal federation service error"


# ---------------------------------------------------------------------------
# POST /concurrency/acquire
# ---------------------------------------------------------------------------


def test_acquire_lease_local_mode(client: TestClient) -> None:
    """POST /concurrency/acquire returns mode='local' when there is no contention."""
    resp = client.post(
        f"{PREFIX}/concurrency/acquire",
        json={
            "app_id": "test_app",
            "agent_name": "planner",
            "agent_class": "general",
            "proposed_model_id": "deepseek-chat",
            "candidate_model_ids": ["deepseek-chat", "gemini-flash"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["granted"] is True
    assert data["mode"] == "local"
    assert data["granted_model_id"] == "deepseek-chat"
    assert "lease_id" in data
    assert data["reason"] is None


def test_acquire_lease_central_override_on_contention(
    client: TestClient,
    lease_store: LeaseStore,
    lease_limiter: LeaseLimiter,
) -> None:
    """POST /concurrency/acquire returns mode='central' and overrides the model on contention."""
    threshold = lease_limiter.threshold_for("free")
    for _ in range(threshold):
        lease_store.acquire(
            app_id="other_app",
            agent_name="agent",
            agent_class="general",
            proposed_model_id="deepseek-chat",
            granted_model_id="deepseek-chat",
            mode="local",
        )

    resp = client.post(
        f"{PREFIX}/concurrency/acquire",
        json={
            "app_id": "test_app",
            "agent_name": "planner",
            "agent_class": "general",
            "proposed_model_id": "deepseek-chat",
            "candidate_model_ids": ["deepseek-chat", "gemini-flash"],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "central"
    assert data["granted_model_id"] == "gemini-flash"
    assert "contention" in data["reason"]


# ---------------------------------------------------------------------------
# POST /concurrency/release
# ---------------------------------------------------------------------------


def test_release_lease(client: TestClient, lease_store: LeaseStore) -> None:
    """POST /concurrency/release returns released=True for an existing lease."""
    lease = lease_store.acquire(
        app_id="test_app",
        agent_name="planner",
        agent_class="general",
        proposed_model_id="deepseek-chat",
        granted_model_id="deepseek-chat",
        mode="local",
    )
    resp = client.post(
        f"{PREFIX}/concurrency/release",
        json={"lease_id": lease.lease_id},
    )
    assert resp.status_code == 200
    assert resp.json()["released"] is True


def test_release_nonexistent_lease(client: TestClient) -> None:
    """POST /concurrency/release returns released=False for an unknown lease_id."""
    resp = client.post(
        f"{PREFIX}/concurrency/release",
        json={"lease_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 200
    assert resp.json()["released"] is False


# ---------------------------------------------------------------------------
# POST /outcomes/report
# ---------------------------------------------------------------------------


def test_report_outcome_accepted(client: TestClient) -> None:
    """POST /outcomes/report returns accepted=True."""
    resp = client.post(
        f"{PREFIX}/outcomes/report",
        json={
            "model_id": "deepseek-chat",
            "agent_class": "general",
            "outcome_type": "exec_success",
            "duration_ms": 1200.0,
            "quality_score": 0.9,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["accepted"] is True


def test_report_outcome_triggers_reorder(client: TestClient) -> None:
    """Reporting outcomes equal to the significance threshold bumps the pool version."""
    for _ in range(3):
        client.post(
            f"{PREFIX}/outcomes/report",
            json={
                "model_id": "model-a",
                "agent_class": "summarization",
                "outcome_type": "exec_success",
            },
        )
    resp = client.get(f"{PREFIX}/pool-version?classes=summarization")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    assert "summarization" in versions
    assert versions["summarization"] >= 1


def test_report_outcome_failure_returns_generic_error(client: TestClient) -> None:
    """POST /outcomes/report should not leak internal exception text."""
    with patch.object(
        fed_routes,
        "_get_outcome_processor",
        return_value=type(
            "_BrokenProcessor",
            (),
            {"process_async": staticmethod(_raise_outcome_failure)},
        )(),
    ):
        resp = client.post(
            f"{PREFIX}/outcomes/report",
            json={
                "model_id": "deepseek-chat",
                "agent_class": "general",
                "outcome_type": "exec_success",
            },
        )

    assert resp.status_code == 500
    assert resp.json()["detail"] == "internal federation service error"


# ---------------------------------------------------------------------------
# GET /pool-version
# ---------------------------------------------------------------------------


def test_pool_version_empty_classes(client: TestClient) -> None:
    """GET /pool-version with no classes param returns an empty versions dict."""
    resp = client.get(f"{PREFIX}/pool-version")
    assert resp.status_code == 200
    assert resp.json()["versions"] == {}


def test_pool_version_specific_classes(client: TestClient, pool_mgr: PoolVersionManager) -> None:
    """GET /pool-version?classes=... returns versions for known classes."""
    import asyncio
    asyncio.run(pool_mgr.bump_async("general", ("model-a", "model-b")))
    resp = client.get(f"{PREFIX}/pool-version?classes=general,scrape")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    assert "general" in versions
    assert versions["general"] >= 1


# ---------------------------------------------------------------------------
# GET /mode
# ---------------------------------------------------------------------------


def test_get_mode_local(client: TestClient) -> None:
    """GET /mode returns a valid ModeDecision with all expected fields."""
    resp = client.get(f"{PREFIX}/mode?agent_class=general&model_id=deepseek-chat")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_class"] == "general"
    assert data["model_id"] == "deepseek-chat"
    assert data["mode"] in ("local", "central")
    assert isinstance(data["active_leases"], int)
    assert isinstance(data["threshold"], int)


def test_get_mode_missing_params(client: TestClient) -> None:
    """GET /mode without model_id returns 422."""
    resp = client.get(f"{PREFIX}/mode?agent_class=general")
    assert resp.status_code == 422


def test_get_mode_empty_params(client: TestClient) -> None:
    """GET /mode with no query params returns 422."""
    resp = client.get(f"{PREFIX}/mode")
    assert resp.status_code == 422


def test_federation_db_path_rejects_invalid_suffix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The FEDERATION_DB_PATH env override should reject non-SQLite paths."""
    monkeypatch.setenv("FEDERATION_DB_PATH", str(tmp_path / "bad.txt"))

    with pytest.raises(ValueError, match="approved suffixes"):
        fed_routes._federation_db_path()


async def _raise_register_failure(_request: object) -> object:
    """Raise a synthetic registry failure for API error-handling tests."""
    raise RuntimeError("leaky register error")


async def _raise_outcome_failure(**_kwargs: object) -> None:
    """Raise a synthetic outcome-processing failure for API error-handling tests."""
    raise RuntimeError("leaky outcome error")


# ---------------------------------------------------------------------------
# GET /federation-scores/{agent_class}
# ---------------------------------------------------------------------------


def test_federation_scores_empty_class(client: TestClient) -> None:
    """GET /federation-scores/{agent_class} returns empty scores for unknown class."""
    resp = client.get(f"{PREFIX}/federation-scores/nonexistent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_class"] == "nonexistent"
    assert data["scores"] == []


def test_federation_scores_after_outcomes(client: TestClient) -> None:
    """Scores appear after outcomes are reported above the significance threshold."""
    for _ in range(3):
        client.post(
            f"{PREFIX}/outcomes/report",
            json={"model_id": "model-x", "agent_class": "coding", "outcome_type": "exec_success"},
        )

    resp = client.get(f"{PREFIX}/federation-scores/coding")
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_class"] == "coding"
    scores_by_id = {s["model_id"]: s for s in data["scores"]}
    assert "model-x" in scores_by_id
    assert scores_by_id["model-x"]["success_count"] == 3
    assert scores_by_id["model-x"]["success_rate"] == pytest.approx(1.0)


def test_federation_scores_version_bumped_after_threshold(client: TestClient) -> None:
    """pool-version is bumped after threshold outcomes; scores are then fetchable."""
    for _ in range(3):
        client.post(
            f"{PREFIX}/outcomes/report",
            json={"model_id": "model-y", "agent_class": "translation", "outcome_type": "exec_success"},
        )

    ver_resp = client.get(f"{PREFIX}/pool-version?classes=translation")
    assert ver_resp.status_code == 200
    assert ver_resp.json()["versions"].get("translation", 0) >= 1

    score_resp = client.get(f"{PREFIX}/federation-scores/translation")
    assert score_resp.status_code == 200
    assert any(s["model_id"] == "model-y" for s in score_resp.json()["scores"])
