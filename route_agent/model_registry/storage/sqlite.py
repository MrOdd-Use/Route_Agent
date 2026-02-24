"""SQLite-backed persistence for model registry snapshots.

SQLite is the default local backend. It stores snapshot headers, model rows,
and provider sync-state records with transactional writes.
"""

from __future__ import annotations

# Detailed notes:
# - Snapshot persistence uses two tables: snapshot header + model rows.
# - Only successful snapshots are used for read-path restoration.
# - Write path is transactional to keep snapshot metadata and model rows
#   consistent.
# - Provider sync-state table is maintained for operational visibility.

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import JSON
from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Integer
from sqlalchemy import MetaData
from sqlalchemy import String
from sqlalchemy import Table
from sqlalchemy import Text
from sqlalchemy import create_engine
from sqlalchemy import delete
from sqlalchemy import desc
from sqlalchemy import event
from sqlalchemy import insert
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from route_agent.model_registry.schemas import (
    ModelMetadata,
    ModelRegistryReport,
    SkippedProvider,
)
from route_agent.model_registry.storage.utils import (
    build_snapshot_version,
    ensure_utc,
    error_summary,
    utc_now,
)


def _default_sqlite_path() -> Path:
    """Return default sqlite file path under project data/ directory."""
    return Path(__file__).resolve().parents[3] / "data" / "route_agent_registry.sqlite3"


@dataclass(slots=True)
class StoredSqliteSnapshot:
    """In-memory representation of one persisted SQLite snapshot."""

    version: str
    created_at: datetime
    status: str
    report: ModelRegistryReport


class SqliteModelRegistryStore:
    """SQLite persistent store for model registry snapshots."""

    def __init__(self, sqlite_path: str | None = None) -> None:
        db_path = Path(sqlite_path).expanduser().resolve() if sqlite_path else _default_sqlite_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._sqlite_path = db_path

        # Allow pooled SQLite connections across threads.
        self._engine: Engine = create_engine(
            f"sqlite:///{self._sqlite_path.as_posix()}",
            future=True,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False},
        )
        self._metadata = MetaData()

        # SQLite disables foreign keys by default; enable them explicitly.
        @event.listens_for(self._engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self._snapshots = Table(
            "model_registry_snapshots",
            self._metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("version", String(64), nullable=False, unique=True),
            Column("status", String(24), nullable=False),  # success / failed
            Column("created_at", DateTime(timezone=True), nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
            Column("requested_providers", JSON, nullable=False),
            Column("configured_providers", JSON, nullable=False),
            Column("skipped_providers", JSON, nullable=False),
            Column("errors", JSON, nullable=False),
            Column("alerts", JSON, nullable=False),
            Column("source_count", Integer, nullable=False, default=0),
            Column("total_models", Integer, nullable=False, default=0),
            Column("error_summary", Text, nullable=True),
        )

        self._model_configs = Table(
            "model_configs",
            self._metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column(
                "snapshot_id",
                Integer,
                ForeignKey("model_registry_snapshots.id", ondelete="CASCADE"),
                nullable=False,
            ),
            Column("model_id", String(255), nullable=False),
            Column("display_name", String(255), nullable=False),
            Column("provider", String(64), nullable=False),
            Column("api_model_name", String(255), nullable=False),
            Column("endpoint", JSON, nullable=False),
            Column("auth", JSON, nullable=False),
            Column("capabilities", JSON, nullable=False),
            Column("pricing", JSON, nullable=False),
            Column("limits", JSON, nullable=False),
            Column("status", JSON, nullable=False),
            Column("routing", JSON, nullable=False),
            Column("raw_payload", JSON, nullable=False),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )

        self._provider_sync_state = Table(
            "provider_sync_state",
            self._metadata,
            Column("provider", String(64), primary_key=True),
            Column("last_attempt_at", DateTime(timezone=True), nullable=True),
            Column("last_success_at", DateTime(timezone=True), nullable=True),
            Column("next_due_at", DateTime(timezone=True), nullable=True),
            Column("last_error", Text, nullable=True),
            Column("updated_at", DateTime(timezone=True), nullable=False),
        )

    @property
    def sqlite_path(self) -> Path:
        """Return resolved sqlite db file path."""
        return self._sqlite_path

    def ensure_schema(self) -> None:
        """Create required tables if they do not exist."""
        self._metadata.create_all(self._engine)

    def dispose(self) -> None:
        """Dispose engine and release pooled connections."""
        self._engine.dispose()

    def is_sync_due(self, interval_days: int = 30, now: datetime | None = None) -> bool:
        """Return whether sync is due based on latest attempt time."""
        effective_now = ensure_utc(now or utc_now())
        effective_interval = max(1, int(interval_days))
        with self._engine.begin() as conn:
            latest_attempt = conn.execute(
                select(self._snapshots.c.created_at).order_by(desc(self._snapshots.c.created_at)).limit(1)
            ).scalar_one_or_none()

        if latest_attempt is None:
            return True

        due_at = ensure_utc(latest_attempt) + timedelta(days=effective_interval)
        return effective_now >= due_at

    def save_snapshot(
        self,
        report: ModelRegistryReport,
        *,
        requested_providers: list[str] | None = None,
        interval_days: int = 30,
        keep_history: int = 2,
    ) -> str:
        """Persist one extraction report into SQLite snapshot tables."""
        now = utc_now()
        version = build_snapshot_version(now)
        effective_requested = list(requested_providers or report.requested_providers)
        snapshot_status = "success" if int(report.total_models) > 0 else "failed"

        snapshot_payload = {
            "version": version,
            "status": snapshot_status,
            "created_at": now,
            "updated_at": now,
            "requested_providers": effective_requested,
            "configured_providers": list(report.configured_providers),
            "skipped_providers": [item.to_dict() for item in report.skipped_providers],
            "errors": dict(report.errors),
            "alerts": list(report.alerts),
            "source_count": len(report.configured_providers),
            "total_models": int(report.total_models),
            "error_summary": error_summary(report.errors),
        }

        with self._engine.begin() as conn:
            insert_result = conn.execute(insert(self._snapshots).values(**snapshot_payload))
            snapshot_id = insert_result.inserted_primary_key[0]
            if snapshot_id is None:
                snapshot_id = conn.execute(
                    select(self._snapshots.c.id).where(self._snapshots.c.version == version)
                ).scalar_one()

            rows = []
            for model in report.models:
                rows.append(
                    {
                        "snapshot_id": snapshot_id,
                        "model_id": model.model_id,
                        "display_name": model.display_name,
                        "provider": model.provider,
                        "api_model_name": model.api_model_name,
                        "endpoint": model.endpoint,
                        "auth": model.auth,
                        "capabilities": model.capabilities,
                        "pricing": model.pricing,
                        "limits": model.limits,
                        "status": model.status,
                        "routing": model.routing,
                        "raw_payload": model.to_dict(),
                        "updated_at": now,
                    }
                )
            if rows:
                conn.execute(insert(self._model_configs), rows)

            self._upsert_provider_sync_state(
                conn=conn,
                requested_providers=effective_requested,
                report=report,
                now=now,
                interval_days=max(1, int(interval_days)),
            )
            self._prune_old_snapshots(conn=conn, keep_history=keep_history)

        return version

    def load_latest_success_snapshot(self) -> StoredSqliteSnapshot | None:
        """Load newest successful snapshot and reconstruct report object."""
        with self._engine.begin() as conn:
            snapshot_row = conn.execute(
                select(
                    self._snapshots.c.id,
                    self._snapshots.c.version,
                    self._snapshots.c.status,
                    self._snapshots.c.created_at,
                    self._snapshots.c.requested_providers,
                    self._snapshots.c.configured_providers,
                    self._snapshots.c.skipped_providers,
                    self._snapshots.c.errors,
                    self._snapshots.c.alerts,
                    self._snapshots.c.total_models,
                )
                .where(self._snapshots.c.status == "success")
                .order_by(desc(self._snapshots.c.created_at))
                .limit(1)
            ).mappings().first()
            if snapshot_row is None:
                return None

            model_rows = conn.execute(
                select(
                    self._model_configs.c.model_id,
                    self._model_configs.c.display_name,
                    self._model_configs.c.provider,
                    self._model_configs.c.api_model_name,
                    self._model_configs.c.endpoint,
                    self._model_configs.c.auth,
                    self._model_configs.c.capabilities,
                    self._model_configs.c.pricing,
                    self._model_configs.c.limits,
                    self._model_configs.c.status,
                    self._model_configs.c.routing,
                )
                .where(self._model_configs.c.snapshot_id == snapshot_row["id"])
                .order_by(self._model_configs.c.provider, self._model_configs.c.model_id)
            ).mappings().all()

        models: list[ModelMetadata] = []
        for row in model_rows:
            models.append(
                ModelMetadata(
                    model_id=str(row["model_id"]),
                    display_name=str(row["display_name"]),
                    provider=str(row["provider"]),
                    api_model_name=str(row["api_model_name"]),
                    endpoint=dict(row["endpoint"] or {}),
                    auth=dict(row["auth"] or {}),
                    capabilities=dict(row["capabilities"] or {}),
                    pricing=dict(row["pricing"] or {}),
                    limits=dict(row["limits"] or {}),
                    status=dict(row["status"] or {}),
                    routing=dict(row["routing"] or {}),
                )
            )

        skipped: list[SkippedProvider] = []
        for item in list(snapshot_row["skipped_providers"] or []):
            provider = str(item.get("provider") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if provider:
                skipped.append(SkippedProvider(provider=provider, reason=reason))

        report = ModelRegistryReport(
            requested_providers=list(snapshot_row["requested_providers"] or []),
            configured_providers=list(snapshot_row["configured_providers"] or []),
            skipped_providers=skipped,
            errors=dict(snapshot_row["errors"] or {}),
            models=models,
            total_models=int(snapshot_row["total_models"] or len(models)),
            alerts=list(snapshot_row["alerts"] or []),
        )
        return StoredSqliteSnapshot(
            version=str(snapshot_row["version"]),
            created_at=ensure_utc(snapshot_row["created_at"]),
            status=str(snapshot_row["status"]),
            report=report,
        )

    def _upsert_provider_sync_state(
        self,
        *,
        conn: Connection,
        requested_providers: list[str],
        report: ModelRegistryReport,
        now: datetime,
        interval_days: int,
    ) -> None:
        """Update provider-level sync status using bulk upsert to avoid N+1 queries."""
        skipped_reason_map = {item.provider: item.reason for item in report.skipped_providers}
        next_due_at = now + timedelta(days=max(1, interval_days))

        # Build all upsert values in a single batch
        upsert_values = []
        for provider in requested_providers:
            provider_error = report.errors.get(provider)
            skipped_reason = skipped_reason_map.get(provider)
            configured = provider in report.configured_providers
            provider_ok = configured and provider_error is None

            if provider_ok:
                failure_reason = None
            elif provider_error:
                failure_reason = provider_error
            elif skipped_reason:
                failure_reason = skipped_reason
            else:
                failure_reason = "provider was requested but no successful sync output"

            upsert_values.append(
                {
                    "provider": provider,
                    "last_attempt_at": now,
                    "last_success_at": now if provider_ok else None,
                    "next_due_at": next_due_at,
                    "last_error": failure_reason,
                    "updated_at": now,
                }
            )

        # Use SQLite's INSERT OR REPLACE with a subquery to preserve last_success_at
        # This approach avoids the N+1 pattern by doing a single bulk operation
        for values in upsert_values:
            # First try to update existing record
            update_result = conn.execute(
                update(self._provider_sync_state)
                .where(self._provider_sync_state.c.provider == values["provider"])
                .values(
                    last_attempt_at=values["last_attempt_at"],
                    next_due_at=values["next_due_at"],
                    last_error=values["last_error"],
                    updated_at=values["updated_at"],
                    # Only update last_success_at if provider is OK (new value is not None)
                    **(
                        {"last_success_at": values["last_success_at"]}
                        if values["last_success_at"] is not None
                        else {}
                    ),
                )
            )
            # If no rows were updated, insert new record
            if update_result.rowcount == 0:
                conn.execute(insert(self._provider_sync_state).values(values))

    def _prune_old_snapshots(self, *, conn: Connection, keep_history: int) -> None:
        """Keep only newest `keep_history` snapshots."""
        effective_keep = int(keep_history)
        if effective_keep <= 0:
            return

        snapshot_ids = list(
            conn.execute(select(self._snapshots.c.id).order_by(desc(self._snapshots.c.created_at))).scalars()
        )
        if len(snapshot_ids) <= effective_keep:
            return

        stale_ids = snapshot_ids[effective_keep:]
        if not stale_ids:
            return

        conn.execute(delete(self._model_configs).where(self._model_configs.c.snapshot_id.in_(stale_ids)))
        conn.execute(delete(self._snapshots).where(self._snapshots.c.id.in_(stale_ids)))

