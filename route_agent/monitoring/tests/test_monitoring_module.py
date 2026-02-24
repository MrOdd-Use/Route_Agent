"""Monitoring module tests."""

from __future__ import annotations

import route_agent.monitoring as monitoring_module
import pytest


@pytest.mark.xfail(
    reason="monitoring stats APIs are not implemented yet",
    strict=False,
)
def test_monitoring_exposes_stats_interfaces() -> None:
    assert hasattr(monitoring_module, "get_stats")
    assert hasattr(monitoring_module, "get_recent_decisions")
