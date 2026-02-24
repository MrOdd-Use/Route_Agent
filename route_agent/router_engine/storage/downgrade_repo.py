"""Downgrade trial focused repository wrapper."""

from __future__ import annotations

from route_agent.router_engine.storage.router_storage import RouterStorage


class DowngradeRepository:
    """Thin repository view for downgrade trials."""

    def __init__(self, storage: RouterStorage) -> None:
        """Initialize the instance."""
        self._storage = storage

    async def get_active_trial_async(self, agent_class: str, domain: str):
        """Execute `get_active_trial_async`."""
        return await self._storage.get_active_downgrade_trial_async(agent_class, domain)

    async def record_observation_async(self, agent_class: str, domain: str, model_id: str, outcome_type: str):
        """Execute `record_observation_async`."""
        return await self._storage.record_downgrade_trial_observation_async(
            agent_class,
            domain,
            model_id,
            outcome_type,
        )

