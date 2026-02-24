"""Service-level orchestration for model-registry extraction and local pool use.

This layer composes:
- provider factory + in-memory registry (`fetch_model_registry_report`);
- optional local persistence with due-based refresh and fallback behavior
  (`get_model_registry_report_with_local_pool`).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import os

from dotenv import load_dotenv

from route_agent.model_registry.providers.factory import (
    create_provider_adapters_from_env,
    expected_providers,
)
from route_agent.model_registry.registry import ModelRegistry
from route_agent.model_registry.schemas import ModelRegistryReport
from route_agent.model_registry.storage.postgres import PostgresModelRegistryStore
from route_agent.model_registry.storage.sqlite import SqliteModelRegistryStore

logger = logging.getLogger(__name__)


def _enrich_with_arena(report: ModelRegistryReport) -> ModelRegistryReport:
    """Optionally enrich model capabilities with Arena leaderboard data.

    Controlled by ENABLE_ARENA_SCORING env var. Only fills None-valued
    capability fields; existing values are preserved.
    """
    from route_agent.model_registry.arena import (
        batch_fill_arena_capabilities,
        get_leaderboard_sync,
        is_arena_scoring_enabled,
    )

    if not is_arena_scoring_enabled():
        return report

    if not report.models:
        return report

    try:
        leaderboard = get_leaderboard_sync()
        if leaderboard is None:
            return report

        enriched_models = batch_fill_arena_capabilities(report.models, leaderboard)
        enriched_report = _clone_report(report)
        enriched_report.models = enriched_models
        logger.info("Arena scoring applied to %d models", len(enriched_models))
        return enriched_report
    except Exception:
        logger.warning("Arena scoring failed, returning original report", exc_info=True)

    return report


def fetch_model_registry_report(
    limit: int = 8,
    include_ollama: bool = False,
    min_total_threshold: int = 5,
    load_env_file: bool = True,
) -> ModelRegistryReport:
    """Fetch models directly from configured providers and return one report."""
    if load_env_file:
        load_dotenv()

    requested = expected_providers(include_ollama=include_ollama)
    adapters = create_provider_adapters_from_env(
        load_env_file=False,
        include_ollama=include_ollama,
    )

    registry = ModelRegistry()
    registry.register_providers(adapters)
    report = registry.build_report(
        requested_providers=requested,
        limit=limit,
        min_total_threshold=min_total_threshold,
    )

    return _enrich_with_arena(report)


@dataclass(slots=True)
class LocalPoolReportResult:
    """Result container for local-pool-aware report retrieval."""

    report: ModelRegistryReport
    source: str
    sync_due: bool
    sync_performed: bool
    snapshot_version: str | None = None
    storage_backend: str = "none"


def _clone_report(report: ModelRegistryReport) -> ModelRegistryReport:
    """Clone a report so fallback alerts can be appended safely."""
    return ModelRegistryReport(
        requested_providers=list(report.requested_providers),
        configured_providers=list(report.configured_providers),
        skipped_providers=list(report.skipped_providers),
        errors=dict(report.errors),
        models=list(report.models),
        total_models=int(report.total_models),
        alerts=list(report.alerts),
    )


def get_model_registry_report_with_local_pool(
    *,
    limit: int = 8,
    include_ollama: bool = False,
    min_total_threshold: int = 5,
    load_env_file: bool = True,
    db_backend: str = "sqlite",
    sqlite_path: str | None = None,
    postgres_dsn: str | None = None,
    sync_interval_days: int = 30,
    keep_history: int = 2,
    force_sync: bool = False,
) -> LocalPoolReportResult:
    """Retrieve report from local pool and refresh providers only when needed.

    Backend selection:
    - `sqlite` (default): local file-based storage.
    - `postgres`: selected only when DSN is provided.
    - otherwise fallback to direct provider fetch without persistence.
    """
    selected_backend = (db_backend or "sqlite").strip().lower()
    store = None

    if selected_backend == "postgres":
        dsn = (postgres_dsn or os.getenv("ROUTE_AGENT_POSTGRES_DSN") or "").strip()
        if dsn:
            store = PostgresModelRegistryStore(dsn)
        else:
            selected_backend = "none"
    elif selected_backend == "sqlite":
        resolved_sqlite_path = sqlite_path or os.getenv("ROUTE_AGENT_SQLITE_PATH")
        store = SqliteModelRegistryStore(resolved_sqlite_path)
    else:
        selected_backend = "none"

    if store is None:
        live_report = fetch_model_registry_report(
            limit=limit,
            include_ollama=include_ollama,
            min_total_threshold=min_total_threshold,
            load_env_file=load_env_file,
        )
        return LocalPoolReportResult(
            report=live_report,
            source="live_provider_fetch_without_local_store",
            sync_due=True,
            sync_performed=True,
            snapshot_version=None,
            storage_backend="none",
        )

    store.ensure_schema()
    try:
        cached_snapshot = store.load_latest_success_snapshot()
        sync_due = force_sync or store.is_sync_due(interval_days=sync_interval_days)

        if (not sync_due) and cached_snapshot is not None:
            return LocalPoolReportResult(
                report=_enrich_with_arena(cached_snapshot.report),
                source="local_pool_snapshot",
                sync_due=False,
                sync_performed=False,
                snapshot_version=cached_snapshot.version,
                storage_backend=selected_backend,
            )

        live_report = fetch_model_registry_report(
            limit=limit,
            include_ollama=include_ollama,
            min_total_threshold=min_total_threshold,
            load_env_file=load_env_file,
        )
        snapshot_version = store.save_snapshot(
            live_report,
            requested_providers=expected_providers(include_ollama=include_ollama),
            interval_days=sync_interval_days,
            keep_history=keep_history,
        )

        if int(live_report.total_models) > 0:
            return LocalPoolReportResult(
                report=live_report,
                source="provider_refresh_and_persisted",
                sync_due=sync_due,
                sync_performed=True,
                snapshot_version=snapshot_version,
                storage_backend=selected_backend,
            )

        if cached_snapshot is not None:
            fallback_report = _clone_report(cached_snapshot.report)
            fallback_report.alerts.append(
                (
                    "latest provider refresh returned zero models; "
                    "fallback to previous successful local snapshot"
                )
            )
            return LocalPoolReportResult(
                report=_enrich_with_arena(fallback_report),
                source="local_pool_fallback_after_failed_refresh",
                sync_due=sync_due,
                sync_performed=True,
                snapshot_version=cached_snapshot.version,
                storage_backend=selected_backend,
            )

        return LocalPoolReportResult(
            report=live_report,
            source="provider_refresh_failed_no_cached_snapshot",
            sync_due=sync_due,
            sync_performed=True,
            snapshot_version=snapshot_version,
            storage_backend=selected_backend,
        )
    finally:
        store.dispose()


def sync_model_registry_to_local_pool(
    *,
    limit: int = 8,
    include_ollama: bool = False,
    min_total_threshold: int = 5,
    load_env_file: bool = True,
    db_backend: str = "sqlite",
    sqlite_path: str | None = None,
    postgres_dsn: str | None = None,
    sync_interval_days: int = 30,
    keep_history: int = 2,
    force_sync: bool = True,
) -> LocalPoolReportResult:
    """Explicit sync entry point (default force sync)."""
    return get_model_registry_report_with_local_pool(
        limit=limit,
        include_ollama=include_ollama,
        min_total_threshold=min_total_threshold,
        load_env_file=load_env_file,
        db_backend=db_backend,
        sqlite_path=sqlite_path,
        postgres_dsn=postgres_dsn,
        sync_interval_days=sync_interval_days,
        keep_history=keep_history,
        force_sync=force_sync,
    )


def sync_model_registry_to_postgres(
    *,
    limit: int = 8,
    include_ollama: bool = False,
    min_total_threshold: int = 5,
    load_env_file: bool = True,
    postgres_dsn: str | None = None,
    sync_interval_days: int = 30,
    keep_history: int = 2,
    force_sync: bool = True,
) -> LocalPoolReportResult:
    """Backward-compatible alias for PostgreSQL sync."""
    return sync_model_registry_to_local_pool(
        limit=limit,
        include_ollama=include_ollama,
        min_total_threshold=min_total_threshold,
        load_env_file=load_env_file,
        db_backend="postgres",
        sqlite_path=None,
        postgres_dsn=postgres_dsn,
        sync_interval_days=sync_interval_days,
        keep_history=keep_history,
        force_sync=force_sync,
    )

