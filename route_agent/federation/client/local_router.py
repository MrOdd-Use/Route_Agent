"""Local router adapter for known-agent routing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from route_agent.app.analysis import ResolvedTaskAnalysis
from route_agent.app.contracts import RouteAgentRequest, RouteAgentRunOptions
from route_agent.app.orchestrator import _resolve_router_runtime, execute_route_with_analysis
from route_agent.app.registry import build_registry_context
from route_agent.app.wiring import get_analysis_storage, get_engine
from route_agent.federation.client.local_store import validate_sqlite_path
from route_agent.router_engine import RouterEngine
from route_agent.router_engine.schemas import RouteDecision
from route_agent.task_analyzer.schemas import TaskAnalysisResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LocalRouterConfig:
    """Configuration for local router."""

    router_db_path: str


class LocalRouter:
    """Adapter for local routing with known agent classes."""

    def __init__(self, config: LocalRouterConfig) -> None:
        """Initialize local router with config."""
        self._config = config
        self._router_db_path = str(validate_sqlite_path(config.router_db_path))
        self._feedback_pool = ThreadPoolExecutor(max_workers=1)

    def close(self) -> None:
        """Release resources."""
        self._feedback_pool.shutdown(wait=False)

    def _build_options(self) -> RouteAgentRunOptions:
        """Build run options shared by local routing and feedback paths."""
        return RouteAgentRunOptions(router_db_path=self._router_db_path)

    def _get_engine(self) -> RouterEngine:
        """Build or reuse the local router engine for federation feedback."""
        options = self._build_options()
        registry = build_registry_context(options)
        analysis_storage = get_analysis_storage(options.analysis_db_path)
        redis_url, router_db_path, rate_limit_mode, rate_limit_fail_strategy = _resolve_router_runtime(
            options
        )
        return get_engine(
            registry.pool,
            analysis_storage,
            redis_url=redis_url,
            router_db_path=router_db_path,
            rate_limit_mode=rate_limit_mode,
            rate_limit_fail_strategy=rate_limit_fail_strategy,
        )

    def _run_feedback(self, coro: Coroutine[Any, Any, object]) -> None:
        """Run an async feedback coroutine from sync code, even inside an event loop."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            self._feedback_pool.submit(asyncio.run, coro).result()
            return

        asyncio.run(coro)

    async def route_known_agent(
        self,
        agent_name: str,
        task: str,
        agent_class: str,
        analysis: TaskAnalysisResult,
    ) -> RouteDecision:
        """Route a known agent using the lightweight known-class analysis path."""
        request = RouteAgentRequest(task=task, agent_name=agent_name, agent_class=agent_class)
        options = self._build_options()
        resolved_analysis = ResolvedTaskAnalysis(
            result=analysis,
            record_id=None,
            used_legacy_fallback=False,
        )
        execution = execute_route_with_analysis(
            request=request,
            analysis=resolved_analysis,
            options=options,
        )
        return execution.decision

    def process_outcome(
        self,
        lease_id: str,
        model_id: str,
        agent_class: str,
        outcome_type: str,
        duration_ms: int | None = None,
        quality_score: float | None = None,
    ) -> None:
        """Process one outcome immediately into the local router-engine feedback loop."""
        del duration_ms, quality_score

        engine = self._get_engine()
        common = dict(
            request_id=lease_id,
            record_id=None,
            model_id=model_id,
            agent_name="federation_client",
            agent_class=agent_class,
            domain=agent_class,
            class_source="federation_client",
        )

        if outcome_type == "exec_success":
            self._run_feedback(engine.report_execution_async(completed=True, **common))
            return

        if outcome_type == "exec_fail":
            self._run_feedback(engine.report_execution_async(completed=False, **common))
            return

        if outcome_type == "quality_good":
            self._run_feedback(engine.report_quality_async(rating="good", **common))
            return

        if outcome_type == "quality_poor":
            self._run_feedback(engine.report_quality_async(rating="poor", **common))
            return

        logger.warning("Unsupported federation local outcome_type=%s", outcome_type)
