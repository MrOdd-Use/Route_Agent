"""Model-registry and pool preparation helpers for the application layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from route_agent.app.contracts import RouteAgentRunOptions
from route_agent.model_registry import MainModelPool, get_model_registry_report_with_local_pool


@dataclass(frozen=True)
class RegistryContext:
    """Prepared registry snapshot/report and derived model pool."""

    report: Any
    local_pool_result: Any
    pool: MainModelPool


def build_main_model_pool(report: Any) -> MainModelPool:
    """Build the main model pool from a registry report."""
    return MainModelPool.from_report(report)


def build_registry_context(options: RouteAgentRunOptions) -> RegistryContext:
    """Load registry data and construct the in-memory model pool."""
    local_pool_result = get_model_registry_report_with_local_pool(**options.model_registry_kwargs())
    return RegistryContext(
        report=local_pool_result.report,
        local_pool_result=local_pool_result,
        pool=build_main_model_pool(local_pool_result.report),
    )
