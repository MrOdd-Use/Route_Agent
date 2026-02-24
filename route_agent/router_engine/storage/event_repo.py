"""Feedback/event focused repository wrapper."""

from __future__ import annotations

from route_agent.router_engine.storage.router_storage import RouterStorage


class EventRepository:
    """Thin repository view for feedback events."""

    def __init__(self, storage: RouterStorage) -> None:
        self._storage = storage

    async def try_record_event_async(self, request_id: str, model_id: str, event_type: str, event_ts: str) -> bool:
        return await self._storage.try_record_event_async(request_id, model_id, event_type, event_ts)

    async def get_events_async(self, request_id: str, model_id: str) -> list[str]:
        return await self._storage.get_events_async(request_id, model_id)

