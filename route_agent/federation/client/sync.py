"""Pool version sync — fetches federation scores for local weighted ranking."""

from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path
from urllib.parse import quote

import httpx

from route_agent.federation.client.local_store import (
    LocalStore,
    validate_sqlite_path,
)
from route_agent.router_engine.storage import RouterStorage

logger = logging.getLogger(__name__)
_MIN_SYNC_INTERVAL_S = 5
_REQUEST_TIMEOUT_S = 5.0


class PoolSyncManager:
    """Manages periodic federation score sync with central server."""

    def __init__(
        self,
        local_store: LocalStore,
        server_url: str,
        app_id: str,
        router_db_path: str,
        sync_interval_s: int = 60,
        alpha_floor: float = 0.3,
    ) -> None:
        """Initialize the sync manager."""
        self._local_store = local_store
        self._server_url = server_url
        self._app_id = app_id
        self._sync_interval_s = max(_MIN_SYNC_INTERVAL_S, int(sync_interval_s))
        self._alpha_floor = alpha_floor
        self._http = httpx.AsyncClient(base_url=server_url, timeout=_REQUEST_TIMEOUT_S)
        self._router_storage = RouterStorage(validate_sqlite_path(router_db_path))
        self._running = False
        self._task: asyncio.Task[None] | None = None
        # Track last-seen remote versions to detect score changes
        self._known_versions: dict[str, int] = {}

    async def start(self) -> None:
        """Start the background sync loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._sync_loop())

    async def stop(self) -> None:
        """Stop the background sync loop and close HTTP resources."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._http.aclose()

    async def _sync_loop(self) -> None:
        """Run the periodic sync loop until `stop()` is called."""
        while self._running:
            try:
                await self._check_and_sync()
            except Exception as exc:  # noqa: BLE001
                logger.warning("Pool sync failed: %s", exc)
            await asyncio.sleep(self._sync_interval_s)

    async def _check_and_sync(self) -> None:
        """Check pool versions and pull fresh federation scores when changed."""
        mappings = await self._local_store.list_agent_mappings(self._app_id)
        snapshots = await self._local_store.list_pool_snapshots()
        snapshot_by_class = {s.agent_class: s for s in snapshots}
        tracked_classes = sorted(
            {m.agent_class for m in mappings} | set(snapshot_by_class)
        )

        if not tracked_classes:
            return

        response = await self._http.get(
            "/api/v1/pool-version",
            params={"classes": ",".join(tracked_classes)},
        )
        response.raise_for_status()
        remote_versions: dict[str, int] = {
            k: int(v) for k, v in response.json().get("versions", {}).items()
        }

        for agent_class in tracked_classes:
            remote_version = remote_versions.get(agent_class, 0)
            if remote_version <= 0:
                continue
            known_version = self._known_versions.get(agent_class, 0)
            if remote_version == known_version:
                continue

            await self._fetch_and_store_scores(agent_class)
            self._known_versions[agent_class] = remote_version
            logger.info(
                "federation scores refreshed | class=%s version=%d",
                agent_class,
                remote_version,
            )

    async def _fetch_and_store_scores(self, agent_class: str) -> None:
        """Fetch federation scores from central server and persist locally."""
        path = f"/api/v1/federation-scores/{quote(agent_class, safe='')}"
        response = await self._http.get(path)
        if response.status_code == 404:
            logger.info("No federation scores for class=%s", agent_class)
            return
        response.raise_for_status()

        data = response.json()
        scores: list[dict] = data.get("scores") or []
        await self._local_store.save_federation_scores(agent_class, scores)

    def _estimate_local_sample_count(self, agent_class: str) -> int:
        """Estimate local sample count from recent class history rows."""
        try:
            return len(self._router_storage.query_by_class_cross_domain(agent_class, limit=500))
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Failed to estimate local sample count for class=%s: %s",
                agent_class,
                exc,
            )
            return 0

    def calculate_alpha(self, local_sample_count: int) -> float:
        """Calculate the federation weight from local experience.

        Returns 1.0 (trust federation fully) when local_sample_count==0,
        decaying toward alpha_floor as local samples accumulate.
        """
        if local_sample_count == 0:
            return 1.0
        decay = math.exp(-local_sample_count / 100.0)
        return self._alpha_floor + (1.0 - self._alpha_floor) * decay