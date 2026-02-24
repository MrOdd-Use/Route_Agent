"""Realtime monitoring dashboard CLI."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from route_agent.monitoring.config import MonitoringConfig
from route_agent.monitoring.service import iter_agent_model_status


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args for realtime monitoring watch command."""
    parser = argparse.ArgumentParser(description="Realtime monitoring dashboard for agent model/status.")
    parser.add_argument(
        "--db-path",
        default="",
        help="Monitoring SQLite path (fallback: ROUTE_AGENT_MONITORING_DB_PATH or default data path).",
    )
    parser.add_argument(
        "--source",
        default="",
        help="Optional source filter (e.g. router_engine_perf_test).",
    )
    parser.add_argument("--limit", type=int, default=200, help="Max execution rows used for each snapshot.")
    parser.add_argument("--since-hours", type=int, default=None, help="Optional lookback window in hours.")
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds.")
    parser.add_argument(
        "--iterations",
        type=int,
        default=0,
        help="Max refresh count. 0 means run until Ctrl+C.",
    )
    parser.add_argument(
        "--only-changes",
        action="store_true",
        help="Only print when snapshot changes.",
    )
    parser.add_argument(
        "--no-clear",
        action="store_true",
        help="Do not clear terminal before each render.",
    )
    return parser.parse_args(argv)


def _build_enabled_config(db_path: str) -> MonitoringConfig:
    """Build a monitoring config that always enables reads for watch mode."""
    env_cfg = MonitoringConfig.from_env()
    resolved_db_path = env_cfg.db_path
    if db_path:
        resolved_db_path = Path(db_path).expanduser().resolve()
    return MonitoringConfig(
        enabled=True,
        db_path=resolved_db_path,
        retention_days=env_cfg.retention_days,
    )


def _clear_terminal() -> None:
    """Clear terminal in a cross-platform friendly way."""
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def _render_frame(frame: dict[str, Any], *, clear_screen: bool) -> None:
    """Print one realtime monitoring frame."""
    if clear_screen:
        _clear_terminal()

    board = str(frame.get("board") or "No execution data.")
    timestamp = str(frame.get("timestamp") or "")
    changed = bool(frame.get("changed"))
    print(board)
    print(f"\nupdated_at_utc={timestamp} changed={changed}")


def run_watch(argv: list[str] | None = None) -> int:
    """Execute realtime watch loop until done or interrupted."""
    args = parse_args(argv)
    config = _build_enabled_config(args.db_path)
    source = args.source.strip() or None
    max_iterations = None if int(args.iterations) <= 0 else int(args.iterations)

    try:
        for frame in iter_agent_model_status(
            config=config,
            limit=max(1, int(args.limit)),
            source=source,
            since_hours=args.since_hours,
            poll_interval_seconds=max(0.05, float(args.interval)),
            max_iterations=max_iterations,
            only_changes=bool(args.only_changes),
        ):
            _render_frame(frame, clear_screen=not bool(args.no_clear))
        return 0
    except KeyboardInterrupt:
        return 130


def main() -> int:
    """Execute `main`."""
    return run_watch()


if __name__ == "__main__":
    raise SystemExit(main())
