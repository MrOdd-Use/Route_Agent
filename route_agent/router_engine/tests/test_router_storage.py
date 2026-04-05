"""Tests for router storage local retention behavior."""

from __future__ import annotations

import sqlite3

from route_agent.router_engine.storage import RouterStorage


def test_cleanup_old_logs_is_noop_by_default_for_success_log_retention(tmp_path) -> None:
    """Test class_success_log keeps rows when cleanup is called without retention_days."""
    storage = RouterStorage(tmp_path / "router_engine.db")
    storage.log_outcome(
        request_id="req-1",
        agent_name="route_agent",
        agent_class="general",
        class_source="default",
        domain="coding",
        model_id="openai:gpt-smart",
        record_id=None,
        outcome="success",
    )

    deleted = storage.cleanup_old_logs()

    assert deleted == 0
    assert len(storage.query_all(limit=10)) == 1


def test_cleanup_old_logs_deletes_old_rows_when_retention_days_is_explicit(tmp_path) -> None:
    """Test cleanup removes old rows only when retention_days is explicitly provided."""
    storage = RouterStorage(tmp_path / "router_engine.db")
    storage.log_outcome(
        request_id="req-2",
        agent_name="route_agent",
        agent_class="general",
        class_source="default",
        domain="coding",
        model_id="openai:gpt-smart",
        record_id=None,
        outcome="success",
    )

    with sqlite3.connect(storage.db_path) as conn:
        conn.execute("UPDATE class_success_log SET created_at = datetime('now', '-40 days')")

    deleted = storage.cleanup_old_logs(retention_days=30)

    assert deleted == 1
    assert storage.query_all(limit=10) == []


def test_router_storage_backfills_legacy_model_availability_columns(tmp_path) -> None:
    """Test legacy `model_availability` tables are upgraded with new health columns."""
    db_path = tmp_path / "router_engine.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE model_availability (
                model_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'available',
                degraded_since TEXT,
                unable_since TEXT,
                last_probe_at TEXT,
                last_probe_success INTEGER,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            INSERT INTO model_availability(
                model_id,
                status,
                degraded_since,
                unable_since,
                last_probe_at,
                last_probe_success,
                updated_at
            ) VALUES (
                'deepseek:deepseek-chat',
                'available',
                NULL,
                NULL,
                '2026-04-05 12:00:00',
                1,
                '2026-04-05 12:00:01'
            );
            """
        )

    storage = RouterStorage(db_path)
    availability = storage.get_availability("deepseek:deepseek-chat")

    with sqlite3.connect(db_path) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(model_availability)").fetchall()]

    assert columns == [
        "model_id",
        "status",
        "degraded_since",
        "unable_since",
        "last_probe_at",
        "last_probe_success",
        "updated_at",
        "consecutive_exec_fail",
        "probe_good_feedback",
        "probe_consecutive_success",
    ]
    assert availability is not None
    assert availability.consecutive_exec_fail == 0
    assert availability.probe_good_feedback == 0
    assert availability.probe_consecutive_success == 0
