"""Monitoring module tests."""

from __future__ import annotations

import asyncio

import route_agent.monitoring as monitoring_module


def _config(tmp_path) -> monitoring_module.MonitoringConfig:
    """Execute `_config`."""
    return monitoring_module.MonitoringConfig(
        enabled=True,
        db_path=tmp_path / "monitoring.db",
        retention_days=30,
    )


def test_monitoring_exposes_stats_interfaces() -> None:
    """Test monitoring exposes stats interfaces."""
    assert hasattr(monitoring_module, "get_stats")
    assert hasattr(monitoring_module, "get_recent_decisions")
    assert hasattr(monitoring_module, "record_decision")
    assert hasattr(monitoring_module, "start_execution")
    assert hasattr(monitoring_module, "end_execution")
    assert hasattr(monitoring_module, "get_agent_model_status")
    assert hasattr(monitoring_module, "render_agent_model_status")
    assert hasattr(monitoring_module, "iter_agent_model_status")


def test_monitoring_sync_flow(tmp_path) -> None:
    """Test monitoring sync flow."""
    cfg = _config(tmp_path)
    event = monitoring_module.RouteDecisionEvent(
        source="main",
        agent_name="route_agent",
        model_used="openai:gpt-smart",
        selected_tier="smart",
        provider="openai",
        registry_error_count=0,
        skipped_provider_count=0,
    )

    row_id = monitoring_module.record_decision(event, config=cfg)
    assert row_id > 0

    recent = monitoring_module.get_recent_decisions(config=cfg, limit=10)
    assert recent
    assert recent[0]["model_used"] == "openai:gpt-smart"

    stats = monitoring_module.get_stats(config=cfg)
    assert stats["all"]["total_decisions"] >= 1
    assert "source_counts" in stats["all"]


def test_monitoring_async_flow(tmp_path) -> None:
    """Test monitoring async flow."""
    cfg = _config(tmp_path)
    event = {
        "source": "router_engine",
        "agent_name": "route_agent",
        "model_used": None,
        "selected_tier": None,
        "provider": None,
        "registry_error_count": 1,
    }

    row_id = asyncio.run(monitoring_module.record_decision_async(event, config=cfg))
    assert row_id > 0

    recent = asyncio.run(monitoring_module.get_recent_decisions_async(config=cfg, limit=5))
    assert recent and recent[0]["source"] == "router_engine"

    stats = asyncio.run(monitoring_module.get_stats_async(config=cfg))
    assert stats["all"]["no_model_count"] >= 1


def test_monitoring_execution_tracking_and_visualization(tmp_path) -> None:
    """Test execution tracking and per-agent visualization output."""
    cfg = _config(tmp_path)
    execution_id = monitoring_module.start_execution(
        {
            "source": "router_engine_test",
            "agent_name": "review_agent",
            "request_id": "req-001",
            "model_used": "openai:gpt-4.1",
            "provider": "openai",
            "metadata": {"suite": "perf"},
        },
        config=cfg,
    )
    assert execution_id

    snapshot = monitoring_module.get_agent_model_status(config=cfg, source="router_engine_test", limit=20)
    assert snapshot["total_agents"] == 1
    assert snapshot["active_agent_count"] == 1
    assert snapshot["status_counts"]["running"] == 1
    assert snapshot["model_counts"]["openai:gpt-4.1"] == 1
    assert snapshot["agents"][0]["agent_name"] == "review_agent"

    board = monitoring_module.render_agent_model_status(config=cfg, source="router_engine_test", limit=20)
    assert "Agent Model/Status Dashboard" in board
    assert "review_agent" in board
    assert "running" in board
    assert "openai:gpt-4.1" in board

    updated = monitoring_module.end_execution(
        {
            "execution_id": execution_id,
            "status": "success",
            "metadata": {"result": "ok"},
        },
        config=cfg,
    )
    assert updated is True

    recent = monitoring_module.get_recent_executions(config=cfg, source="router_engine_test", limit=5)
    assert recent
    assert recent[0]["execution_id"] == execution_id
    assert recent[0]["status"] == "success"
    assert recent[0]["duration_ms"] is None or recent[0]["duration_ms"] >= 0.0


def test_monitoring_execution_async_tracking(tmp_path) -> None:
    """Test async execution start/end and dashboard rendering."""
    cfg = _config(tmp_path)
    execution_id = asyncio.run(
        monitoring_module.start_execution_async(
            {
                "source": "router_engine_test",
                "agent_name": "qa_agent",
                "request_id": "req-async-001",
                "model_used": "deepseek:deepseek-chat",
                "provider": "deepseek",
            },
            config=cfg,
        )
    )
    assert execution_id

    updated = asyncio.run(
        monitoring_module.end_execution_async(
            {
                "execution_id": execution_id,
                "status": "failed",
                "error_message": "synthetic timeout",
            },
            config=cfg,
        )
    )
    assert updated is True

    snapshot = asyncio.run(
        monitoring_module.get_agent_model_status_async(config=cfg, source="router_engine_test", limit=20)
    )
    assert snapshot["total_agents"] == 1
    assert snapshot["status_counts"]["failed"] == 1
    assert snapshot["agents"][0]["model_used"] == "deepseek:deepseek-chat"

    board = asyncio.run(monitoring_module.render_agent_model_status_async(config=cfg, source="router_engine_test"))
    assert "qa_agent" in board
    assert "failed" in board


def test_monitoring_realtime_iteration(tmp_path) -> None:
    """Test realtime iteration yields dashboard frames and change filtering."""
    cfg = _config(tmp_path)
    execution_id = monitoring_module.start_execution(
        {
            "source": "realtime_test",
            "agent_name": "planner",
            "request_id": "req-real-001",
            "model_used": "google:gemini-2.5-pro",
            "provider": "google",
            "status": "running",
        },
        config=cfg,
    )
    assert execution_id

    frames = list(
        monitoring_module.iter_agent_model_status(
            config=cfg,
            source="realtime_test",
            limit=20,
            poll_interval_seconds=0.01,
            max_iterations=2,
            only_changes=False,
            sleep_fn=lambda _seconds: None,
        )
    )
    assert len(frames) == 2
    assert frames[0]["snapshot"]["total_agents"] == 1
    assert "Agent Model/Status Dashboard" in frames[0]["board"]

    changed_only_frames = list(
        monitoring_module.iter_agent_model_status(
            config=cfg,
            source="realtime_test",
            limit=20,
            poll_interval_seconds=0.01,
            max_iterations=3,
            only_changes=True,
            sleep_fn=lambda _seconds: None,
        )
    )
    assert len(changed_only_frames) == 1
    assert changed_only_frames[0]["changed"] is True

    updated = monitoring_module.end_execution(
        {"execution_id": execution_id, "status": "success"},
        config=cfg,
    )
    assert updated is True

    post_update_frames = list(
        monitoring_module.iter_agent_model_status(
            config=cfg,
            source="realtime_test",
            limit=20,
            poll_interval_seconds=0.01,
            max_iterations=2,
            only_changes=True,
            sleep_fn=lambda _seconds: None,
        )
    )
    assert len(post_update_frames) == 1
    assert post_update_frames[0]["snapshot"]["status_counts"]["success"] == 1
