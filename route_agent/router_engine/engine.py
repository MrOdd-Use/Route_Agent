"""Router engine orchestrator."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from route_agent.model_registry.pool import MainModelPool
from route_agent.router_engine.class_pool import ClassPoolManager
from route_agent.router_engine.constants import DEFAULT_DOMAIN_KEY, ENABLE_DOMAIN_DEFAULTS
from route_agent.router_engine.downgrade import DowngradeOptimizer
from route_agent.router_engine.escalation import EscalationManager
from route_agent.router_engine.health import HealthManager
from route_agent.router_engine.rate_limiters import RateLimiter, create_rate_limiter
from route_agent.router_engine.schemas import RouteDecision, RouteRequest
from route_agent.router_engine.selector import ModelSelector
from route_agent.router_engine.storage import RouterStorage
from route_agent.task_analyzer.storage import AnalysisStorage


class RouterEngine:
    """Main router-engine facade."""

    def __init__(
        self,
        pool: MainModelPool,
        analysis_storage: AnalysisStorage,
        redis_url: str | None = None,
        router_db_path: str | Path | None = None,
        rate_limit_mode: str = "auto",
        rate_limit_fail_strategy: str = "degrade",
    ) -> None:
        """Initialize the instance."""
        self._pool = pool
        self._analysis_storage = analysis_storage
        self._router_storage = RouterStorage(Path(router_db_path) if router_db_path else None)
        self._health_manager = HealthManager(self._router_storage)
        self._rate_limiter: RateLimiter = create_rate_limiter(
            redis_url,
            mode=rate_limit_mode,
            fail_strategy=rate_limit_fail_strategy,
        )
        self._class_pool_mgr = ClassPoolManager(self._router_storage, model_metadata_resolver=self._pool.get)
        self._selector = ModelSelector(self._health_manager, self._rate_limiter, self._class_pool_mgr)
        self._downgrade_optimizer = DowngradeOptimizer(
            self._class_pool_mgr,
            self._health_manager,
            self._rate_limiter,
            self._router_storage,
            model_metadata_resolver=self._pool.get,
        )
        self._probe_task: asyncio.Task[None] | None = None

    @property
    def rate_limiter(self) -> RateLimiter:
        """Execute `rate_limiter`."""
        return self._rate_limiter

    def _domain_key(self, request: RouteRequest) -> str:
        """Execute `_domain_key`."""
        if ENABLE_DOMAIN_DEFAULTS:
            domain = request.analysis.domain.strip()
            return domain or DEFAULT_DOMAIN_KEY
        return DEFAULT_DOMAIN_KEY

    async def _ensure_probe_task(self) -> None:
        """Execute `_ensure_probe_task`."""
        if self._probe_task is not None and not self._probe_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._probe_task = loop.create_task(self._health_manager.probe_loop_async())

    async def route_async(self, request: RouteRequest) -> RouteDecision:
        """Execute `route_async`."""
        await self._ensure_probe_task()

        available = self._pool.list_available(provider=request.constraints.require_provider)
        decision = await self._selector.select_async(available, request)

        agent_class = decision.pool_class
        if not agent_class:
            agent_class, _source = await self._class_pool_mgr.resolve_class_async(request)

        decision = await self._downgrade_optimizer.choose_trial_model_async(
            agent_class,
            self._domain_key(request),
            decision,
        )
        return decision

    def route(self, request: RouteRequest) -> RouteDecision:
        """Execute `route`."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.route_async(request)).result()

        return asyncio.run(self.route_async(request))

    def create_escalation_manager(self, decision: RouteDecision) -> EscalationManager:
        """Execute `create_escalation_manager`."""
        return EscalationManager(
            decision,
            self._rate_limiter,
            self._health_manager,
            self._pool.list_available(),
        )

    async def report_quality_async(
        self,
        request_id: str,
        record_id: int | None,
        model_id: str,
        agent_name: str,
        agent_class: str,
        domain: str,
        rating: str,
        action: str | None = None,
        note: str | None = None,
        event_ts: str | None = None,
        class_source: str = "default",
    ) -> None:
        """Execute `report_quality_async`."""
        normalized_rating = (rating or "").strip().lower()
        event_type = "quality_good" if normalized_rating in {"good", "fair"} else "quality_poor"

        existing = await self._router_storage.get_events_async(request_id, model_id)
        if "exec_fail" in existing:
            return
        if "exec_success" not in existing:
            return

        timestamp = event_ts or datetime.now(tz=timezone.utc).isoformat()
        is_new = await self._router_storage.try_record_event_async(request_id, model_id, event_type, timestamp)
        if not is_new:
            return

        if record_id is not None:
            await self._analysis_storage.update_quality_review_async(
                record_id,
                rating=normalized_rating,
                action=action,
                note=note,
            )

        if normalized_rating in {"good", "fair"}:
            await self._health_manager.on_quality_good_async(agent_class, model_id)
            await self._class_pool_mgr.record_outcome(agent_class, domain, model_id, "success")
            await self._router_storage.log_outcome_async(
                request_id,
                agent_name,
                agent_class,
                class_source,
                domain,
                model_id,
                record_id,
                "success",
            )
            outcome = await self._downgrade_optimizer.record_downgrade_result_async(
                agent_class,
                domain,
                model_id,
                "quality_good",
            )
            if outcome == "promote":
                await self._downgrade_optimizer.finalize_downgrade_trial_async(agent_class, domain, "promote")
                await self._router_storage.log_outcome_async(
                    request_id,
                    agent_name,
                    agent_class,
                    class_source,
                    domain,
                    model_id,
                    record_id,
                    "downgrade_promote",
                )
                return

            if outcome == "continue":
                started = await self._downgrade_optimizer.maybe_start_downgrade_trial_async(
                    agent_class,
                    domain,
                    model_id,
                )
                if started is not None:
                    await self._router_storage.log_outcome_async(
                        request_id,
                        agent_name,
                        agent_class,
                        class_source,
                        domain,
                        started,
                        record_id,
                        "downgrade_start",
                    )
            return

        await self._health_manager.on_quality_fail_async(agent_class, model_id)
        await self._class_pool_mgr.record_outcome(agent_class, domain, model_id, "quality_fail")
        await self._router_storage.log_outcome_async(
            request_id,
            agent_name,
            agent_class,
            class_source,
            domain,
            model_id,
            record_id,
            "quality_fail",
        )
        outcome = await self._downgrade_optimizer.record_downgrade_result_async(
            agent_class,
            domain,
            model_id,
            "quality_fail",
        )
        if outcome == "rollback":
            await self._downgrade_optimizer.finalize_downgrade_trial_async(agent_class, domain, "rollback")
            await self._router_storage.log_outcome_async(
                request_id,
                agent_name,
                agent_class,
                class_source,
                domain,
                model_id,
                record_id,
                "downgrade_rollback",
            )

    async def report_execution_async(
        self,
        request_id: str,
        record_id: int | None,
        model_id: str,
        agent_name: str,
        agent_class: str,
        domain: str,
        completed: bool,
        error_type: str | None = None,
        error_detail: str | None = None,
        event_ts: str | None = None,
        class_source: str = "default",
    ) -> None:
        """Execute `report_execution_async`."""
        event_type = "exec_success" if completed else "exec_fail"
        timestamp = event_ts or datetime.now(tz=timezone.utc).isoformat()

        is_new = await self._router_storage.try_record_event_async(request_id, model_id, event_type, timestamp)
        if not is_new:
            return

        if record_id is not None:
            await self._analysis_storage.update_execution_result_async(
                record_id,
                completed=completed,
                error_type=error_type,
                error_detail=error_detail,
            )

        if completed:
            await self._health_manager.report_exec_success_async(model_id)
            return

        await self._health_manager.report_exec_failure_async(model_id)
        await self._class_pool_mgr.record_outcome(agent_class, domain, model_id, "exec_fail")
        await self._router_storage.log_outcome_async(
            request_id,
            agent_name,
            agent_class,
            class_source,
            domain,
            model_id,
            record_id,
            "exec_fail",
        )

        outcome = await self._downgrade_optimizer.record_downgrade_result_async(
            agent_class,
            domain,
            model_id,
            "exec_fail",
        )
        if outcome == "rollback":
            await self._downgrade_optimizer.finalize_downgrade_trial_async(agent_class, domain, "rollback")
            await self._router_storage.log_outcome_async(
                request_id,
                agent_name,
                agent_class,
                class_source,
                domain,
                model_id,
                record_id,
                "downgrade_rollback",
            )

    async def list_pools_async(self) -> list[dict[str, Any]]:
        """Execute `list_pools_async`."""
        result: list[dict[str, Any]] = []
        for agent_class in await self._router_storage.list_pool_classes_async():
            entries = await self._router_storage.get_pool_entries_async(agent_class)
            default_model = await self._class_pool_mgr.get_default(agent_class, DEFAULT_DOMAIN_KEY)
            result.append(
                {
                    "agent_class": agent_class,
                    "model_count": len(entries),
                    "default_model": default_model,
                }
            )
        return result

    async def inspect_pool_async(self, agent_class: str) -> list[dict[str, Any]]:
        """Execute `inspect_pool_async`."""
        entries = await self._router_storage.get_pool_entries_async(agent_class)
        return [
            {
                "agent_class": entry.agent_class,
                "model_id": entry.model_id,
                "model_release_date": entry.model_release_date,
                "success_count": entry.success_count,
                "fail_count": entry.fail_count,
                "success_rate": entry.success_rate,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
            }
            for entry in entries
        ]

    def rate_limiter_status(self) -> dict[str, Any]:
        """Execute `rate_limiter_status`."""
        status = getattr(self._rate_limiter, "status", None)
        if status is None:
            return {"mode": "unknown", "fail_strategy": "unknown", "switched_at": None, "last_error": None}
        return {
            "mode": status.mode,
            "fail_strategy": status.fail_strategy,
            "switched_at": status.switched_at,
            "last_error": status.last_error,
        }
