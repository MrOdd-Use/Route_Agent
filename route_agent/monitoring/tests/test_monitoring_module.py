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
