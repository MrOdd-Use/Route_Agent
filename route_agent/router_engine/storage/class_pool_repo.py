"""Class-pool focused repository wrapper."""

from __future__ import annotations

from route_agent.router_engine.storage.router_storage import RouterStorage


class ClassPoolRepository:
    """Thin repository view for class-pool operations."""

    def __init__(self, storage: RouterStorage) -> None:
        self._storage = storage

    async def get_pool_entries_async(self, agent_class: str):
        return await self._storage.get_pool_entries_async(agent_class)

    async def get_default_async(self, agent_class: str, domain: str):
        return await self._storage.get_default_async(agent_class, domain)

