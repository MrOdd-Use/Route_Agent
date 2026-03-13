"""Public application service facade for one route decision."""

from __future__ import annotations

from typing import Any

from route_agent.app.contracts import RouteAgentRequest, RouteAgentRunOptions
from route_agent.app.orchestrator import execute_route
from route_agent.app.payloads import build_route_payload


def run_route_agent(
    request: dict[str, Any] | RouteAgentRequest,
    *,
    options: RouteAgentRunOptions | None = None,
    limit: int = 8,
    include_ollama: bool = False,
    min_total_threshold: int = 5,
    load_env_file: bool = True,
    db_backend: str = "sqlite",
    sqlite_path: str | None = None,
    postgres_dsn: str | None = None,
    sync_interval_days: int = 30,
    force_registry_sync: bool = False,
    keep_registry_history: int = 2,
    agent_name: str = "route_agent",
    redis_url: str | None = None,
    router_db_path: str | None = None,
    rate_limit_mode: str | None = None,
    rate_limit_fail_strategy: str | None = None,
    analysis_db_path: str | None = None,
) -> dict[str, Any]:
    """Run one route decision while keeping the public service contract stable."""
    resolved_options = options or RouteAgentRunOptions(
        limit=limit,
        include_ollama=include_ollama,
        min_total_threshold=min_total_threshold,
        load_env_file=load_env_file,
        db_backend=db_backend,
        sqlite_path=sqlite_path,
        postgres_dsn=postgres_dsn,
        sync_interval_days=sync_interval_days,
        force_registry_sync=force_registry_sync,
        keep_registry_history=keep_registry_history,
        default_agent_name=agent_name,
        redis_url=redis_url,
        router_db_path=router_db_path,
        rate_limit_mode=rate_limit_mode or "auto",
        rate_limit_fail_strategy=rate_limit_fail_strategy or "degrade",
        analysis_db_path=analysis_db_path,
    )

    normalized_request = (
        request
        if isinstance(request, RouteAgentRequest)
        else RouteAgentRequest.from_mapping(request, default_agent_name=resolved_options.default_agent_name)
    )
    execution = execute_route(normalized_request, resolved_options)
    return build_route_payload(execution=execution, sync_interval_days=resolved_options.sync_interval_days)
