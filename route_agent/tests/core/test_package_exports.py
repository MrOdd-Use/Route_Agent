"""Tests for package-level lazy import behavior."""

from __future__ import annotations

import importlib
import sys


def _drop_modules(*module_names: str) -> None:
    """Execute `_drop_modules`."""
    for name in module_names:
        sys.modules.pop(name, None)


def test_route_agent_lazy_exports_are_consistent() -> None:
    """Test route agent lazy export table is aligned with public exports."""
    import route_agent

    assert set(route_agent.__all__) == set(route_agent._LAZY_EXPORTS.keys())


def test_model_registry_lazy_exports_are_consistent() -> None:
    """Test model registry lazy export table is aligned with public exports."""
    import route_agent.model_registry as model_registry

    assert set(model_registry.__all__) == set(model_registry._LAZY_EXPORTS.keys())


def test_route_agent_top_level_import_is_lazy() -> None:
    """Test route agent top level import is lazy."""
    _drop_modules(
        "route_agent",
        "route_agent.model_registry",
        "route_agent.model_registry.service",
    )

    importlib.import_module("route_agent")

    assert "route_agent.model_registry.service" not in sys.modules


def test_model_registry_import_is_lazy() -> None:
    """Test model registry import is lazy."""
    _drop_modules(
        "route_agent.model_registry",
        "route_agent.model_registry.service",
        "route_agent.model_registry.providers.factory",
    )

    importlib.import_module("route_agent.model_registry")

    assert "route_agent.model_registry.service" not in sys.modules
    assert "route_agent.model_registry.providers.factory" not in sys.modules
