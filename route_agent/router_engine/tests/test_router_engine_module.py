"""Router Engine module tests."""

from __future__ import annotations

import route_agent.router_engine as router_engine_module


def test_router_engine_exposes_route_api() -> None:
    assert hasattr(router_engine_module, "route")
