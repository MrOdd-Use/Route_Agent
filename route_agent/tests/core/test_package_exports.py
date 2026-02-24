"""Tests for package-level lazy import behavior."""

from __future__ import annotations

import importlib
import sys


def _drop_modules(*module_names: str) -> None:
    for name in module_names:
        sys.modules.pop(name, None)


def test_route_agent_top_level_import_is_lazy() -> None:
    _drop_modules(
        "route_agent",
        "route_agent.model_registry",
        "route_agent.model_registry.service",
    )

    importlib.import_module("route_agent")

    assert "route_agent.model_registry.service" not in sys.modules


def test_model_registry_import_is_lazy() -> None:
    _drop_modules(
        "route_agent.model_registry",
        "route_agent.model_registry.service",
        "route_agent.model_registry.providers.factory",
    )

    importlib.import_module("route_agent.model_registry")

    assert "route_agent.model_registry.service" not in sys.modules
    assert "route_agent.model_registry.providers.factory" not in sys.modules
