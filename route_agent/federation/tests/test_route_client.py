"""Tests for RouteClient SDK."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from route_agent.federation.client.route_client import RouteClient
from route_agent.router_engine.schemas import (
    ModelCandidate,
    RouteDecision,
)
from route_agent.task_analyzer.schemas import TaskAnalysisResult


@pytest.fixture
def temp_local_db() -> Path:
    """Create temporary local database."""
    with tempfile.NamedTemporaryFile(suffix="_local.db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def temp_router_db() -> Path:
    """Create temporary router database."""
    with tempfile.NamedTemporaryFile(suffix="_router.db", delete=False) as f:
        return Path(f.name)


@pytest.fixture
def mock_http_client() -> AsyncMock:
    """Create mock HTTP client."""
    client = AsyncMock()
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def route_client(
    temp_local_db: Path, temp_router_db: Path, mock_http_client: AsyncMock
) -> RouteClient:
    """Create RouteClient with mocked HTTP."""
    with patch("route_agent.federation.client.route_client.httpx.AsyncClient") as mock_client_cls:
        mock_sync_client = AsyncMock()
        mock_sync_client.aclose = AsyncMock()
        mock_client_cls.side_effect = [mock_sync_client, mock_http_client]
        client = RouteClient(
            app_id="test_app",
            server_url="http://localhost:8000",
            local_db_path=str(temp_local_db),
            router_db_path=str(temp_router_db),
        )
        return client


def test_route_client_rejects_private_server_url_by_default(
    temp_local_db: Path,
    temp_router_db: Path,
) -> None:
    """Private non-localhost server URLs should be rejected by default."""
    with pytest.raises(ValueError, match="public host"):
        RouteClient(
            app_id="test_app",
            server_url="http://169.254.169.254",
            local_db_path=str(temp_local_db),
            router_db_path=str(temp_router_db),
        )


@pytest.mark.asyncio
async def test_route_unknown_agent_central_success(
    route_client: RouteClient, mock_http_client: AsyncMock
) -> None:
    """Test routing unknown agent via central inference."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "model_used": "gpt-4",
        "routing_reason": "central route",
        "analysis": {
            "task_class": "general",
            "domain": "general",
            "domain_description": "General tasks",
            "relevant_dimensions": [
                {
                    "dimension": "reasoning",
                    "score": 8,
                    "reasoning": "Needs multi-step reasoning.",
                },
                {
                    "dimension": "instruction_following",
                    "score": 7,
                    "reasoning": "Needs accurate compliance.",
                },
            ],
        },
        "candidates": [],
    }
    mock_response.raise_for_status = MagicMock()
    mock_http_client.post = AsyncMock(return_value=mock_response)

    result = await route_client.route(
        agent_name="unknown_agent",
        task="Solve this problem",
    )

    assert result.model == "gpt-4"
    assert result.mode == "central"
    assert result.analysis.task_class == "general"
    assert {item.dimension for item in result.analysis.relevant_dimensions} == {
        "instruction_following",
        "reasoning",
    }
    mock_http_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_route_unknown_agent_central_failure_fallback(
    route_client: RouteClient, mock_http_client: AsyncMock
) -> None:
    """Test fallback to local when central unavailable."""
    mock_http_client.post = AsyncMock(
        side_effect=httpx.ConnectError("Network error")
    )

    with patch.object(
        route_client._local_router,
        "route_known_agent",
        return_value=RouteDecision(
            primary_model="local-model",
            candidates=(
                ModelCandidate(
                    model_id="local-model",
                    provider="local",
                    display_name="Local Model",
                    dimension_score=0.8,
                    raw_dimension_score=0.8,
                    cost_score=0.9,
                    health_status="healthy",
                ),
            ),
            start_index=0,
            reason="fallback",
        ),
    ):
        result = await route_client.route(
            agent_name="unknown_agent",
            task="Solve this problem",
        )

    assert result.model == "local-model"
    assert result.mode == "local"
    assert "fallback" in result.local_reason


@pytest.mark.asyncio
async def test_route_known_agent_local_path(
    route_client: RouteClient, mock_http_client: AsyncMock
) -> None:
    """Test routing known agent via local path."""
    # Save agent mapping
    await route_client._local_store.save_agent_mapping(
        app_id="test_app",
        agent_name="known_agent",
        agent_class="summarization",
        source="declared",
    )

    # Mock local router
    with patch.object(
        route_client._local_router,
        "route_known_agent",
        return_value=RouteDecision(
            primary_model="deepseek-chat",
            candidates=(
                ModelCandidate(
                    model_id="deepseek-chat",
                    provider="deepseek",
                    display_name="DeepSeek Chat",
                    dimension_score=0.9,
                    raw_dimension_score=0.9,
                    cost_score=0.95,
                    health_status="healthy",
                ),
            ),
            start_index=0,
            reason="class_pool_primary",
        ),
    ):
        # Mock lease acquisition
        mock_lease_response = MagicMock()
        mock_lease_response.json.return_value = {
            "lease_id": "lease-123",
            "granted": True,
            "mode": "local",
            "granted_model_id": "deepseek-chat",
            "pool_version": 21,
            "pool_changed": False,
        }
        mock_lease_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_lease_response)

        result = await route_client.route(
            agent_name="known_agent",
            task="Summarize this document",
        )

    assert result.model == "deepseek-chat"
    assert result.lease_id == "lease-123"
    assert result.mode == "local"
    assert result.analysis.task_class == "summarization"
    assert {item.dimension for item in result.analysis.relevant_dimensions} == {
        "creative_writing",
        "text",
    }


@pytest.mark.asyncio
async def test_route_known_agent_central_override(
    route_client: RouteClient, mock_http_client: AsyncMock
) -> None:
    """Test central override due to contention."""
    await route_client._local_store.save_agent_mapping(
        app_id="test_app",
        agent_name="known_agent",
        agent_class="summarization",
        source="declared",
    )

    with patch.object(
        route_client._local_router,
        "route_known_agent",
        return_value=RouteDecision(
            primary_model="deepseek-chat",
            candidates=(
                ModelCandidate(
                    model_id="deepseek-chat",
                    provider="deepseek",
                    display_name="DeepSeek Chat",
                    dimension_score=0.9,
                    raw_dimension_score=0.9,
                    cost_score=0.95,
                    health_status="healthy",
                ),
                ModelCandidate(
                    model_id="gemini-2.0-flash",
                    provider="google",
                    display_name="Gemini 2.0 Flash",
                    dimension_score=0.85,
                    raw_dimension_score=0.85,
                    cost_score=0.9,
                    health_status="healthy",
                ),
            ),
            start_index=0,
            reason="class_pool_primary",
        ),
    ):
        mock_lease_response = MagicMock()
        mock_lease_response.json.return_value = {
            "lease_id": "lease-456",
            "granted": True,
            "mode": "central",
            "granted_model_id": "gemini-2.0-flash",
            "reason": "deepseek-chat contention",
            "pool_version": 22,
            "pool_changed": True,
        }
        mock_lease_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_lease_response)

        result = await route_client.route(
            agent_name="known_agent",
            task="Summarize this document",
        )

    assert result.model == "gemini-2.0-flash"
    assert result.mode == "central"
    assert result.lease_id == "lease-456"


@pytest.mark.asyncio
async def test_release_lease(
    route_client: RouteClient, mock_http_client: AsyncMock
) -> None:
    """Test lease release."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_http_client.post = AsyncMock(return_value=mock_response)

    await route_client.release("lease-123")

    mock_http_client.post.assert_called_once_with(
        "/api/v1/concurrency/release",
        json={"lease_id": "lease-123"},
    )


@pytest.mark.asyncio
async def test_report_outcome_dual_write(
    route_client: RouteClient, mock_http_client: AsyncMock
) -> None:
    """Test outcome reporting with dual-write model."""
    with patch.object(
        route_client._local_router, "process_outcome"
    ) as mock_local:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post = AsyncMock(return_value=mock_response)

        await route_client.report_outcome(
            lease_id="lease-123",
            model_id="gpt-4",
            agent_class="general",
            outcome_type="exec_success",
            duration_ms=1500,
            quality_score=0.9,
        )

        # Local processing should be called synchronously
        mock_local.assert_called_once_with(
            lease_id="lease-123",
            model_id="gpt-4",
            agent_class="general",
            outcome_type="exec_success",
            duration_ms=1500,
            quality_score=0.9,
        )

        # Central reporting is fire-and-forget, give it time
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_stop_waits_for_background_outcome_tasks(
    route_client: RouteClient,
) -> None:
    """stop() should wait for tracked fire-and-forget outcome tasks."""
    finished = asyncio.Event()

    async def _fake_report(**_kwargs: object) -> None:
        """Simulate one deferred outcome-report request."""
        await asyncio.sleep(0.01)
        finished.set()

    with patch.object(route_client._local_router, "process_outcome"), patch.object(
        route_client,
        "_report_outcome_async",
        side_effect=_fake_report,
    ):
        await route_client.report_outcome(
            lease_id="lease-123",
            model_id="gpt-4",
            agent_class="general",
            outcome_type="exec_success",
        )
        await route_client.stop()

    assert finished.is_set() is True
