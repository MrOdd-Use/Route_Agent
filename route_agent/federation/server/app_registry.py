"""AppRegistry: facade for app + agent registration at the central server.

Combines FederationStorage (persistence) and PoolVersionManager (version lookups)
to implement the POST /api/v1/apps/register flow.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from route_agent.federation.schemas import (
    AppRegisterRequest,
    AppRegisterResponse,
    RegisteredAgent,
)
from route_agent.federation.server.pool_version import PoolVersionManager
from route_agent.federation.server.storage import FederationStorage

logger = logging.getLogger(__name__)


class AppRegistry:
    """Central app + declared-agent registration service."""

    def __init__(
        self,
        storage: FederationStorage,
        pool_version_mgr: PoolVersionManager,
    ) -> None:
        """Initialise with a FederationStorage and a PoolVersionManager."""
        self._storage = storage
        self._pool_version_mgr = pool_version_mgr

    async def register_async(self, request: AppRegisterRequest) -> AppRegisterResponse:
        """Upsert app + all declared agents; return pool versions for declared classes."""
        await self._storage.upsert_app_async(request.app_id, request.app_name)

        agent_classes: set[str] = set()
        for decl in request.agents:
            await self._storage.upsert_agent_async(
                request.app_id,
                decl.agent_name,
                decl.agent_class,
                decl.agent_version,
            )
            agent_classes.add(decl.agent_class)
            logger.debug(
                "federation register | app=%s agent=%s class=%s version=%s",
                request.app_id, decl.agent_name, decl.agent_class, decl.agent_version,
            )

        pool_versions = await self._pool_version_mgr.get_versions_async(list(agent_classes))

        app = await self._storage.get_app_async(request.app_id)
        registered_at = app.registered_at if app else datetime.now(tz=timezone.utc)

        logger.info(
            "federation app registered | app_id=%s agents=%d classes=%s",
            request.app_id, len(request.agents), sorted(agent_classes),
        )
        return AppRegisterResponse(
            app_id=request.app_id,
            registered_at=registered_at,
            pool_versions=pool_versions,
        )

    async def get_agent_mapping_async(
        self,
        app_id: str,
        agent_name: str,
    ) -> RegisteredAgent | None:
        """Return the persisted agent mapping for (app_id, agent_name), or None."""
        return await self._storage.get_agent_async(app_id, agent_name)

    async def list_agents_async(self, app_id: str) -> list[RegisteredAgent]:
        """Return all registered agents for an app."""
        return await self._storage.list_agents_async(app_id)
