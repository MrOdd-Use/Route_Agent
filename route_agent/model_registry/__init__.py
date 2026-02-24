"""Public exports for the model registry package.

This module is the stable public API surface for the `model_registry` package.
External callers should import from here instead of deep internal paths.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

# Detailed package notes:
# - This module is the stable import boundary for callers.
# - Internal files may evolve, but symbols re-exported here should remain
#   backward-compatible as much as possible.
# - New public capabilities should be exported here first, then referenced by
#   external code to reduce coupling to internal file paths.

if TYPE_CHECKING:
    from route_agent.model_registry.pool import MainModelPool
    from route_agent.model_registry.providers.base import ProviderAdapter
    from route_agent.model_registry.providers.factory import create_provider_adapters_from_env
    from route_agent.model_registry.providers.utils import default_capabilities
    from route_agent.model_registry.registry import ModelRegistry
    from route_agent.model_registry.schemas import (
        ModelMetadata,
        ModelRegistryReport,
        SkippedProvider,
    )
    from route_agent.model_registry.service import (
        LocalPoolReportResult,
        fetch_model_registry_report,
        get_model_registry_report_with_local_pool,
        sync_model_registry_to_local_pool,
        sync_model_registry_to_postgres,
    )
    from route_agent.model_registry.storage.postgres import (
        PostgresModelRegistryStore,
        StoredRegistrySnapshot,
    )
    from route_agent.model_registry.storage.sqlite import (
        SqliteModelRegistryStore,
        StoredSqliteSnapshot,
    )

__all__ = [
    # Core schema types.
    "ModelMetadata",
    "SkippedProvider",
    "ModelRegistryReport",
    # Core runtime components.
    "ModelRegistry",
    "ProviderAdapter",
    "MainModelPool",
    # Storage snapshots and stores.
    "StoredRegistrySnapshot",
    "StoredSqliteSnapshot",
    "SqliteModelRegistryStore",
    "PostgresModelRegistryStore",
    # Service-layer orchestration APIs.
    "LocalPoolReportResult",
    "create_provider_adapters_from_env",
    "fetch_model_registry_report",
    "get_model_registry_report_with_local_pool",
    "sync_model_registry_to_local_pool",
    "sync_model_registry_to_postgres",
    # Utility helpers.
    "default_capabilities",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "ModelMetadata": ("route_agent.model_registry.schemas", "ModelMetadata"),
    "SkippedProvider": ("route_agent.model_registry.schemas", "SkippedProvider"),
    "ModelRegistryReport": ("route_agent.model_registry.schemas", "ModelRegistryReport"),
    "ModelRegistry": ("route_agent.model_registry.registry", "ModelRegistry"),
    "ProviderAdapter": ("route_agent.model_registry.providers.base", "ProviderAdapter"),
    "MainModelPool": ("route_agent.model_registry.pool", "MainModelPool"),
    "StoredRegistrySnapshot": ("route_agent.model_registry.storage.postgres", "StoredRegistrySnapshot"),
    "StoredSqliteSnapshot": ("route_agent.model_registry.storage.sqlite", "StoredSqliteSnapshot"),
    "SqliteModelRegistryStore": ("route_agent.model_registry.storage.sqlite", "SqliteModelRegistryStore"),
    "PostgresModelRegistryStore": (
        "route_agent.model_registry.storage.postgres",
        "PostgresModelRegistryStore",
    ),
    "LocalPoolReportResult": ("route_agent.model_registry.service", "LocalPoolReportResult"),
    "create_provider_adapters_from_env": (
        "route_agent.model_registry.providers.factory",
        "create_provider_adapters_from_env",
    ),
    "fetch_model_registry_report": ("route_agent.model_registry.service", "fetch_model_registry_report"),
    "get_model_registry_report_with_local_pool": (
        "route_agent.model_registry.service",
        "get_model_registry_report_with_local_pool",
    ),
    "sync_model_registry_to_local_pool": (
        "route_agent.model_registry.service",
        "sync_model_registry_to_local_pool",
    ),
    "sync_model_registry_to_postgres": (
        "route_agent.model_registry.service",
        "sync_model_registry_to_postgres",
    ),
    "default_capabilities": ("route_agent.model_registry.providers.utils", "default_capabilities"),
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
