"""Pool version manager: tracks versioned class pool snapshots for federation sync."""

from __future__ import annotations

from route_agent.federation.schemas import PoolVersionEntry
from route_agent.federation.server.storage import FederationStorage


class PoolVersionManager:
    """Manages pool version bumps and version lookups for federation clients.

    Wraps FederationStorage pool-version methods and provides a single point
    for version-bump events triggered by class_pool changes or outcome_processor
    reorder decisions.
    """

    def __init__(self, storage: FederationStorage) -> None:
        """Initialise with a FederationStorage backend."""
        self._storage = storage

    async def bump_async(
        self,
        agent_class: str,
        model_ids: tuple[str, ...],
    ) -> PoolVersionEntry:
        """Bump the version for agent_class and store the new ordered model list."""
        return await self._storage.upsert_pool_version_async(agent_class, model_ids)

    def bump_sync(
        self,
        agent_class: str,
        model_ids: tuple[str, ...],
    ) -> PoolVersionEntry:
        """Sync wrapper for bump_async."""
        return self._storage.upsert_pool_version_sync(agent_class, model_ids)

    async def get_async(self, agent_class: str) -> PoolVersionEntry | None:
        """Return the current pool version entry for a class, or None."""
        return await self._storage.get_pool_version_async(agent_class)

    async def get_versions_async(self, agent_classes: list[str]) -> dict[str, int]:
        """Return {agent_class: version} for the requested classes."""
        return await self._storage.get_pool_versions_async(agent_classes)

    def get_versions_sync(self, agent_classes: list[str]) -> dict[str, int]:
        """Return {agent_class: version} for the requested classes (sync)."""
        return self._storage.get_pool_versions_sync(agent_classes)
