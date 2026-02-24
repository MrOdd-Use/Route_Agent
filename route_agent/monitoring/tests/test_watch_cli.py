"""Tests for realtime monitoring watch CLI."""

from __future__ import annotations

from route_agent.monitoring import MonitoringConfig, start_execution
from route_agent.monitoring.watch import parse_args, run_watch


def test_watch_parse_args_defaults() -> None:
    """Test watch CLI argument defaults."""
    args = parse_args([])
    assert args.interval == 1.0
    assert args.limit == 200
    assert args.iterations == 0
    assert args.only_changes is False
    assert args.no_clear is False


def test_watch_run_once(tmp_path, capsys) -> None:
    """Test watch CLI renders one snapshot frame."""
    cfg = MonitoringConfig(enabled=True, db_path=tmp_path / "monitoring.db", retention_days=7)
    execution_id = start_execution(
        {
            "source": "watch_cli_test",
            "agent_name": "watcher",
            "request_id": "watch-001",
            "model_used": "openai:gpt-4.1-mini",
            "provider": "openai",
        },
        config=cfg,
    )
    assert execution_id

    exit_code = run_watch(
        [
            "--db-path",
            str(cfg.db_path),
            "--source",
            "watch_cli_test",
            "--iterations",
            "1",
            "--no-clear",
        ]
    )
    assert exit_code == 0
    rendered = capsys.readouterr().out
    assert "Agent Model/Status Dashboard" in rendered
    assert "watcher" in rendered
