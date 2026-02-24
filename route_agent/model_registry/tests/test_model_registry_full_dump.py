"""Unified Model Registry test script.

This script validates model-registry extraction and local-pool persistence.
By default (sqlite backend), the final terminal output shows SQLite DB state
instead of dumping the full JSON payload to stdout.

Key behaviors:
1) If a provider API key is missing, print a clear warning and skip it.
2) Continue extracting models from other providers.
3) If total extracted models < 5, print a warning alert.
4) Show concise model lines and SQLite DB snapshot/model preview.
5) Support optional full payload export via --output-json.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

from dotenv import load_dotenv

# Ensure the project root is importable when this script is run directly.
ROOT_DIR = Path(__file__).resolve().parents[3]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from route_agent.model_registry import (
    get_model_registry_report_with_local_pool,
)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for model registry testing."""
    parser = argparse.ArgumentParser(
        description="Fetch model registry data and inspect sqlite DB persistence."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=8,
        help="Maximum number of models to fetch per provider.",
    )
    parser.add_argument(
        "--include-ollama",
        action="store_true",
        help="Include local ollama provider (does not require cloud API key).",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional output file path for full JSON payload.",
    )
    parser.add_argument(
        "--db-backend",
        default="sqlite",
        choices=["sqlite", "postgres"],
        help="Local model pool backend. Default sqlite.",
    )
    parser.add_argument(
        "--sqlite-path",
        default="",
        help=(
            "SQLite DB file path for local model pool. "
            "If omitted, uses ROUTE_AGENT_SQLITE_PATH or data/route_agent_registry.sqlite3."
        ),
    )
    parser.add_argument(
        "--postgres-dsn",
        default="",
        help=(
            "Optional PostgreSQL DSN (used only when --db-backend=postgres)."
        ),
    )
    parser.add_argument(
        "--sync-interval-days",
        type=int,
        default=30,
        help="Refresh interval days in local-pool mode (default: 30).",
    )
    parser.add_argument(
        "--force-sync",
        action="store_true",
        help="Force one immediate provider refresh in local-pool mode.",
    )
    parser.add_argument(
        "--db-preview-limit",
        type=int,
        default=20,
        help="Maximum number of model rows to preview from SQLite DB.",
    )
    return parser.parse_args()


def _resolve_sqlite_path(raw_sqlite_path: str) -> Path:
    """Resolve sqlite path using same fallback order as service layer."""
    from route_agent.model_registry.storage.sqlite import _default_sqlite_path
    effective = raw_sqlite_path.strip() or os.getenv("ROUTE_AGENT_SQLITE_PATH")
    return Path(effective).expanduser().resolve() if effective else _default_sqlite_path()


def _print_sqlite_db_view(sqlite_path: Path, preview_limit: int) -> None:
    """Print a concise DB view for sqlite-backed local pool."""
    if not sqlite_path.exists():
        print(f"[WARN] SQLite DB file not found: {sqlite_path}")
        return

    print(f"[INFO] SQLite DB file: {sqlite_path}")
    print(f"[INFO] SQLite DB size: {sqlite_path.stat().st_size} bytes")

    try:
        with sqlite3.connect(sqlite_path) as conn:
            conn.row_factory = sqlite3.Row
            table_rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()

            if not table_rows:
                print("[WARN] No tables found in SQLite DB.")
                return

            for row in table_rows:
                table_name = str(row["name"])
                count_row = conn.execute(
                    f'SELECT COUNT(1) AS row_count FROM "{table_name}"'
                ).fetchone()
                row_count = int(count_row["row_count"]) if count_row else 0
                print(f"[DB TABLE] {table_name}: rows={row_count}")

            latest_snapshot = conn.execute(
                """
                SELECT id, version, status, created_at, total_models
                FROM model_registry_snapshots
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            if latest_snapshot is None:
                print("[WARN] model_registry_snapshots has no records.")
                return

            snapshot_id = int(latest_snapshot["id"])
            print(
                "[DB SNAPSHOT] "
                f"id={snapshot_id} "
                f"version={latest_snapshot['version']} "
                f"status={latest_snapshot['status']} "
                f"created_at={latest_snapshot['created_at']} "
                f"total_models={latest_snapshot['total_models']}"
            )

            preview_rows = conn.execute(
                """
                SELECT provider, model_id, api_model_name
                FROM model_configs
                WHERE snapshot_id = ?
                ORDER BY provider, model_id
                LIMIT ?
                """,
                (snapshot_id, max(1, int(preview_limit))),
            ).fetchall()
            for idx, row in enumerate(preview_rows, start=1):
                print(
                    "[DB MODEL "
                    f"{idx}] provider={row['provider']} "
                    f"model_id={row['model_id']} "
                    f"api_model_name={row['api_model_name']}"
                )

            if len(preview_rows) == 0:
                print("[WARN] No model rows found for latest snapshot.")
    except sqlite3.Error as exc:
        print(f"[WARN] Failed to inspect SQLite DB: {exc}")


def main() -> int:
    """Entry point for model registry full-dump test."""
    # Explicitly load `.env` as requested.
    load_dotenv()
    args = _parse_args()
    # Fetch from local pool with fallback refresh behavior.
    local_result = get_model_registry_report_with_local_pool(
        limit=args.limit,
        include_ollama=args.include_ollama,
        min_total_threshold=5,
        load_env_file=True,
        db_backend=args.db_backend,
        sqlite_path=args.sqlite_path or None,
        postgres_dsn=args.postgres_dsn,
        sync_interval_days=max(1, int(args.sync_interval_days)),
        keep_history=2,
        force_sync=args.force_sync,
    )
    report = local_result.report
    print(
        (
            "[INFO] local_pool source="
            f"{local_result.source}, sync_due={local_result.sync_due}, "
            f"sync_performed={local_result.sync_performed}, "
            f"snapshot_version={local_result.snapshot_version}, "
            f"storage_backend={local_result.storage_backend}"
        )
    )

    # Convert report object to serializable dict payload.
    payload: dict[str, Any] = report.to_dict()

    # Print skip warnings explicitly for operator visibility.
    for skipped in payload.get("skipped_providers", []):
        provider = skipped.get("provider")
        reason = skipped.get("reason")
        print(f"[WARN] Skip provider '{provider}': {reason}.")

    for alert in payload["alerts"]:
        print(f"[ALERT] {alert}")

    # Print concise model list first for quick checking in terminal.
    models = payload.get("models", [])
    print(f"[INFO] fetched_models={len(models)}")
    for idx, model in enumerate(models, start=1):
        print(
            "[MODEL "
            f"{idx}] provider={model.get('provider')} "
            f"model_id={model.get('model_id')} "
            f"api_model_name={model.get('api_model_name')}"
        )

    if local_result.storage_backend == "sqlite":
        sqlite_db_path = _resolve_sqlite_path(args.sqlite_path)
        _print_sqlite_db_view(
            sqlite_path=sqlite_db_path,
            preview_limit=args.db_preview_limit,
        )
    else:
        print(
            "[INFO] SQLite DB inspection skipped because "
            f"storage_backend={local_result.storage_backend}."
        )

    # Optionally persist payload to file for later analysis.
    if args.output_json:
        output_path = Path(args.output_json).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"[INFO] Full output written to: {output_path}")

    # Return non-zero only when no models were extracted at all.
    return 1 if int(payload["total_models"]) == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())

