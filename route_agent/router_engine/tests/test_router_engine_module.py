"""Router Engine module tests."""

from __future__ import annotations

import route_agent.router_engine as router_engine_module
import pytest


@pytest.mark.xfail(
    reason="router_engine route API is not implemented yet",
    strict=False,
)
def test_router_engine_exposes_route_api() -> None:
    assert hasattr(router_engine_module, "route")
