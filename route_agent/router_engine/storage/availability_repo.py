"""Availability-focused repository wrapper."""

from __future__ import annotations

from route_agent.router_engine.storage.router_storage import RouterStorage


class AvailabilityRepository:
    """Thin repository view for model availability operations."""

    def __init__(self, storage: RouterStorage) -> None:
        self._storage = storage

    async def get_async(self, model_id: str):
        return await self._storage.get_availability_async(model_id)

    async def mark_available_async(self, model_id: str) -> None:
        await self._storage.mark_available_async(model_id)

    async def mark_unable_async(self, model_id: str) -> None:
        await self._storage.mark_unable_async(model_id)

