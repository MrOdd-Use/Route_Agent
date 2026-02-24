"""Top-level package exports.

This module keeps imports lazy so `import route_agent` does not eagerly load
provider/storage stacks and their optional third-party dependencies.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from route_agent.app.service import run_route_agent
    from route_agent.model_registry import (
        LocalPoolReportResult,
        MainModelPool,
        ModelMetadata,
        ModelRegistry,
        ModelRegistryReport,
        PostgresModelRegistryStore,
        ProviderAdapter,
        SkippedProvider,
        SqliteModelRegistryStore,
        StoredSqliteSnapshot,
        create_provider_adapters_from_env,
        fetch_model_registry_report,
        get_model_registry_report_with_local_pool,
        sync_model_registry_to_local_pool,
        sync_model_registry_to_postgres,
    )

__all__ = [
    "MainModelPool",
    "ModelMetadata",
    "SkippedProvider",
    "PostgresModelRegistryStore",
    "SqliteModelRegistryStore",
    "StoredSqliteSnapshot",
    "LocalPoolReportResult",
    "ModelRegistryReport",
    "ModelRegistry",
    "ProviderAdapter",
    "create_provider_adapters_from_env",
    "fetch_model_registry_report",
    "get_model_registry_report_with_local_pool",
    "sync_model_registry_to_local_pool",
    "sync_model_registry_to_postgres",
    "run_route_agent",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "MainModelPool": ("route_agent.model_registry", "MainModelPool"),
    "ModelMetadata": ("route_agent.model_registry", "ModelMetadata"),
    "SkippedProvider": ("route_agent.model_registry", "SkippedProvider"),
    "PostgresModelRegistryStore": ("route_agent.model_registry", "PostgresModelRegistryStore"),
    "SqliteModelRegistryStore": ("route_agent.model_registry", "SqliteModelRegistryStore"),
    "StoredSqliteSnapshot": ("route_agent.model_registry", "StoredSqliteSnapshot"),
    "LocalPoolReportResult": ("route_agent.model_registry", "LocalPoolReportResult"),
    "ModelRegistryReport": ("route_agent.model_registry", "ModelRegistryReport"),
    "ModelRegistry": ("route_agent.model_registry", "ModelRegistry"),
    "ProviderAdapter": ("route_agent.model_registry", "ProviderAdapter"),
    "create_provider_adapters_from_env": ("route_agent.model_registry", "create_provider_adapters_from_env"),
    "fetch_model_registry_report": ("route_agent.model_registry", "fetch_model_registry_report"),
    "get_model_registry_report_with_local_pool": (
        "route_agent.model_registry",
        "get_model_registry_report_with_local_pool",
    ),
    "sync_model_registry_to_local_pool": ("route_agent.model_registry", "sync_model_registry_to_local_pool"),
    "sync_model_registry_to_postgres": ("route_agent.model_registry", "sync_model_registry_to_postgres"),
    "run_route_agent": ("route_agent.app.service", "run_route_agent"),
}


def __getattr__(name: str) -> Any:
    """Lazily resolve public exports on first access."""
    export = _LAZY_EXPORTS.get(name)
    if export is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_path, attr_name = export
    value = getattr(import_module(module_path), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Implement `__dir__`."""
    return sorted(set(globals()) | set(__all__))
