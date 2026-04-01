"""RouteClient SDK for federated routing."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

import httpx

from route_agent.federation.client.lightweight_analysis import (
    build_lightweight_analysis_for_class,
)
from route_agent.federation.client.local_router import (
    LocalRouter,
    LocalRouterConfig,
)
from route_agent.federation.client.local_store import LocalStore
from route_agent.federation.client.sync import PoolSyncManager
from route_agent.router_engine.schemas import ModelCandidate
from route_agent.task_analyzer.schemas import DimensionScore, TaskAnalysisResult

logger = logging.getLogger(__name__)
_ALLOW_PRIVATE_SERVER_URLS_ENV = "ROUTE_AGENT_ALLOW_PRIVATE_SERVER_URLS"
_REQUEST_TIMEOUT_S = 5.0
_DEFAULT_ESTIMATED_DURATION_S = 30
_INTERNAL_HOST_SUFFIXES = (".internal", ".lan", ".local", ".home", ".corp")
RouteCandidate = ModelCandidate | dict[str, Any]


def _host_is_allowed(hostname: str) -> bool:
    """Return whether the parsed hostname is allowed by default."""
    lowered = hostname.lower().rstrip(".")
    if lowered == "localhost":
        return True

    try:
        address = ip_address(lowered.strip("[]"))
    except ValueError:
        if lowered.endswith(_INTERNAL_HOST_SUFFIXES):
            return False
        return "." in lowered

    if address.is_loopback:
        return True
    return not (
        address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _validate_server_url(server_url: str) -> str:
    """Validate and normalize the SDK's central server URL."""
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("server_url must use http or https")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("server_url must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("server_url must not include embedded credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("server_url must not include a query string or fragment")
    if os.getenv(_ALLOW_PRIVATE_SERVER_URLS_ENV) != "1" and not _host_is_allowed(parsed.hostname):
        raise ValueError(
            "server_url must target a public host by default. "
            f"Set {_ALLOW_PRIVATE_SERVER_URLS_ENV}=1 to allow private or internal hosts."
        )
    return parsed.geturl().rstrip("/")

CANONICAL_CLASSES = {
    "general",
    "scrape",
    "extraction",
    "summarization",
    "classification",
    "rewrite",
    "review",
    "translation",
}


@dataclass(frozen=True)
class RouteResult:
    """Result from RouteClient.route()."""

    model: str | None
    lease_id: str | None
    mode: str  # "local" | "central"
    analysis: TaskAnalysisResult
    candidates: list[RouteCandidate] | tuple[ModelCandidate, ...]
    local_reason: str


class RouteClient:
    """Lightweight SDK for federated routing."""

    def __init__(
        self,
        app_id: str,
        server_url: str,
        local_db_path: str,
        router_db_path: str,
    ) -> None:
        """Initialize route client.

        Args:
            app_id: Application identifier
            server_url: Central server base URL
            local_db_path: Path to local SQLite database
            router_db_path: Path to router engine database
        """
        self._app_id = app_id
        self._server_url = _validate_server_url(server_url)
        self._local_store = LocalStore(local_db_path)
        self._local_router = LocalRouter(
            LocalRouterConfig(router_db_path=router_db_path)
        )
        self._sync_manager = PoolSyncManager(
            local_store=self._local_store,
            server_url=self._server_url,
            app_id=app_id,
            router_db_path=router_db_path,
        )
        self._http = httpx.AsyncClient(base_url=self._server_url, timeout=_REQUEST_TIMEOUT_S)
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Start background sync."""
        await self._sync_manager.start()

    async def stop(self) -> None:
        """Stop background sync and cleanup."""
        await self._drain_background_tasks()
        await self._sync_manager.stop()
        await self._http.aclose()

    async def aclose(self) -> None:
        """Close background resources explicitly."""
        await self.stop()

    async def __aenter__(self) -> "RouteClient":
        """Start the client when used as an async context manager."""
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        """Stop the client when leaving an async context manager."""
        await self.stop()

    async def route(
        self, agent_name: str, task: str, agent_version: str = "v1"
    ) -> RouteResult:
        """Route request for agent.

        Known agents use local mapping + lightweight analysis.
        Unknown agents fall back to central inference chain.
        """
        # 1. App-scoped mapping lookup
        mapping = await self._local_store.get_agent_mapping(
            self._app_id, agent_name
        )

        if mapping is None:
            # Unknown agent: use central inference chain
            result = await self._central_route(agent_name, task)

            # Learn mapping if canonical class
            if result.analysis.task_class in CANONICAL_CLASSES:
                await self._local_store.save_agent_mapping(
                    app_id=self._app_id,
                    agent_name=agent_name,
                    agent_class=result.analysis.task_class,
                    source="learned",
                )

            return result

        # 2. Known agent: build lightweight analysis
        analysis = build_lightweight_analysis_for_class(mapping.agent_class)

        # 3. Local routing with full semantics
        decision = await self._local_router.route_known_agent(
            agent_name=agent_name,
            task=task,
            agent_class=mapping.agent_class,
            analysis=analysis,
        )

        if decision.primary_model is None:
            return RouteResult(
                model=None,
                lease_id=None,
                mode="local",
                analysis=analysis,
                candidates=decision.candidates,
                local_reason=decision.reason,
            )

        lease = await self._acquire_lease(
            agent_name=agent_name,
            agent_version=mapping.agent_version,
            agent_class=mapping.agent_class,
            proposed_model_id=decision.primary_model,
            candidate_model_ids=[c.model_id for c in decision.candidates],
        )

        # 5. Return result with granted model
        return RouteResult(
            model=lease["granted_model_id"],
            lease_id=lease["lease_id"],
            mode=lease["mode"],
            analysis=analysis,
            candidates=decision.candidates,
            local_reason=decision.reason,
        )

    async def release(self, lease_id: str) -> None:
        """Release lease after execution."""
        try:
            response = await self._http.post(
                "/api/v1/concurrency/release",
                json={"lease_id": lease_id},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Lease release failed: %s", exc)

    async def report_outcome(
        self,
        lease_id: str,
        model_id: str,
        agent_class: str,
        outcome_type: str,
        duration_ms: int | None = None,
        quality_score: float | None = None,
    ) -> None:
        """Report execution outcome.

        Dual-write model:
        - Local: synchronous feedback into local pool
        - Central: fire-and-forget aggregation
        """
        try:
            self._local_router.process_outcome(
                lease_id=lease_id,
                model_id=model_id,
                agent_class=agent_class,
                outcome_type=outcome_type,
                duration_ms=duration_ms,
                quality_score=quality_score,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Local outcome processing failed: %s", exc)

        self._track_background_task(
            self._report_outcome_async(
                lease_id=lease_id,
                model_id=model_id,
                agent_class=agent_class,
                outcome_type=outcome_type,
                duration_ms=duration_ms,
                quality_score=quality_score,
            )
        )

    async def _report_outcome_async(
        self,
        lease_id: str,
        model_id: str,
        agent_class: str,
        outcome_type: str,
        duration_ms: int | None,
        quality_score: float | None,
    ) -> None:
        """Fire-and-forget outcome reporting."""
        try:
            response = await self._http.post(
                "/api/v1/outcomes/report",
                json={
                    "lease_id": lease_id,
                    "model_id": model_id,
                    "agent_class": agent_class,
                    "outcome_type": outcome_type,
                    "duration_ms": duration_ms,
                    "quality_score": quality_score,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Outcome report failed: %s", exc)

    async def _central_route(
        self, agent_name: str, task: str
    ) -> RouteResult:
        """Fall back to central inference chain for unknown agents."""
        try:
            response = await self._http.post(
                "/api/v1/route",
                json={"task": task, "agent_name": agent_name},
            )
            response.raise_for_status()
            data = response.json()
            analysis = self._parse_central_analysis(data)

            return RouteResult(
                model=data.get("model_used") or data.get("model"),
                lease_id=None,
                mode="central",
                analysis=analysis,
                candidates=data.get("candidates", []),
                local_reason="central_fallback",
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Central route failed: %s, using default", exc)
            # Fallback to general class
            analysis = build_lightweight_analysis_for_class("general")
            decision = await self._local_router.route_known_agent(
                agent_name=agent_name,
                task=task,
                agent_class="general",
                analysis=analysis,
            )
            return RouteResult(
                model=decision.primary_model,
                lease_id=None,
                mode="local",
                analysis=analysis,
                candidates=decision.candidates,
                local_reason="central_unavailable_fallback",
            )

    def _parse_central_analysis(self, data: dict[str, Any]) -> TaskAnalysisResult:
        """Parse the `/api/v1/route` analysis payload into `TaskAnalysisResult`."""
        payload = data.get("analysis")
        if not isinstance(payload, dict):
            payload = data

        dimensions: list[DimensionScore] = []
        raw_dimensions = payload.get("relevant_dimensions")
        if isinstance(raw_dimensions, list):
            for item in raw_dimensions:
                if not isinstance(item, dict):
                    continue
                dimension = str(item.get("dimension") or "").strip()
                if not dimension:
                    continue
                try:
                    score = int(item.get("score"))
                except (TypeError, ValueError):
                    continue
                reasoning = str(item.get("reasoning") or "").strip() or "central_analysis"
                dimensions.append(
                    DimensionScore(
                        dimension=dimension,
                        score=score,
                        reasoning=reasoning,
                    )
                )

        return TaskAnalysisResult(
            task_class=payload.get("task_class", "general"),
            domain=payload.get("domain", "general"),
            domain_description=payload.get("domain_description", ""),
            relevant_dimensions=tuple(dimensions),
        )

    def _track_background_task(
        self,
        coro: Coroutine[Any, Any, None],
    ) -> None:
        """Create and retain a background task until it finishes."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _drain_background_tasks(self) -> None:
        """Wait briefly for any in-flight background tasks before shutdown."""
        if not self._background_tasks:
            return
        pending = tuple(self._background_tasks)
        await asyncio.gather(*pending, return_exceptions=True)

    async def _acquire_lease(
        self,
        agent_name: str,
        agent_version: str,
        agent_class: str,
        proposed_model_id: str,
        candidate_model_ids: list[str],
    ) -> dict[str, Any]:
        """Acquire lease for proposed model."""
        try:
            response = await self._http.post(
                "/api/v1/concurrency/acquire",
                json={
                    "app_id": self._app_id,
                    "agent_name": agent_name,
                    "agent_version": agent_version,
                    "agent_class": agent_class,
                    "proposed_model_id": proposed_model_id,
                    "candidate_model_ids": candidate_model_ids,
                    "estimated_duration_s": _DEFAULT_ESTIMATED_DURATION_S,
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as exc:
            logger.warning("Lease acquire failed: %s, using local", exc)
            # Fallback to local mode
            return {
                "lease_id": None,
                "granted": True,
                "mode": "local",
                "granted_model_id": proposed_model_id,
                "pool_version": 0,
                "pool_changed": False,
            }
