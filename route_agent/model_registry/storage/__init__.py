"""Storage backends for local model-registry snapshots.

Two backends are exposed:
- SQLite: default local persistence with zero external infrastructure.
- PostgreSQL: centralized/shared persistence option.
"""

from route_agent.model_registry.storage.postgres import (
    PostgresModelRegistryStore,
    StoredRegistrySnapshot,
)
from route_agent.model_registry.storage.sqlite import (
    SqliteModelRegistryStore,
    StoredSqliteSnapshot,
)

__all__ = [
    "StoredSqliteSnapshot",
    "SqliteModelRegistryStore",
    "StoredRegistrySnapshot",
    "PostgresModelRegistryStore",
]

