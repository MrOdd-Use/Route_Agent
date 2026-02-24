"""CLI entrypoints for route agent application service."""

from __future__ import annotations

import argparse
import json

from route_agent.app.service import run_route_agent


def parse_args() -> argparse.Namespace:
    """Execute `parse_args`."""
    parser = argparse.ArgumentParser(description="Run one Route Agent route decision.")
    parser.add_argument("--task", required=True, help="Task text to be routed.")
    parser.add_argument("--agent-name", default="route_agent", help="Agent name for task analysis and routing.")
    parser.add_argument("--request-id", default="", help="Optional request idempotency key (default: UUID4).")
    parser.add_argument("--system-prompt", default="", help="Optional system prompt context for class resolution.")
    parser.add_argument("--max-cost", type=float, default=None, help="Optional max cost constraint.")
    parser.add_argument(
        "--preferred-model",
        default="",
        help="Optional preferred model id, e.g. deepseek:deepseek-chat",
    )
    parser.add_argument("--limit", type=int, default=8, help="Max models to fetch per provider.")
    parser.add_argument(
        "--include-ollama",
        action="store_true",
        help="Include local ollama provider during model registry extraction.",
    )
    parser.add_argument(
        "--db-backend",
        default="sqlite",
        choices=["sqlite", "postgres"],
        help="Local model pool backend. Default is sqlite.",
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
            "Optional PostgreSQL DSN (only used when --db-backend=postgres). "
            "If omitted, uses ROUTE_AGENT_POSTGRES_DSN."
        ),
    )
    parser.add_argument(
        "--sync-interval-days",
        type=int,
        default=30,
        help="How often to refresh model registry from providers (default: 30 days).",
    )
    parser.add_argument(
        "--force-registry-sync",
        action="store_true",
        help="Force one immediate provider refresh and persist a new snapshot.",
    )
    parser.add_argument(
        "--redis-url",
        default="",
        help="Optional Redis URL for router_engine limiter (fallback: REDIS_URL).",
    )
    parser.add_argument(
        "--router-db-path",
        default="",
        help="SQLite path for router_engine state (fallback: ROUTER_DB_PATH or data/router_engine.db).",
    )
    parser.add_argument(
        "--rate-limit",
        dest="rate_limit_mode",
        default="auto",
        choices=["auto", "redis", "inmemory", "off"],
        help="Rate limiter mode used by router_engine (default: auto).",
    )
    parser.add_argument(
        "--rate-limit-fail-strategy",
        default="degrade",
        choices=["degrade", "fail_fast"],
        help="Limiter fallback strategy in auto mode (default: degrade).",
    )
    parser.add_argument(
        "--exclude-models",
        default="",
        help="Comma-separated model ids to exclude from routing candidates.",
    )
    parser.add_argument(
        "--require-provider",
        default="",
        help="Restrict candidates to one provider (e.g. openai, deepseek, google).",
    )
    parser.add_argument(
        "--estimated-input-tokens",
        type=int,
        default=None,
        help="Optional estimated prompt tokens for context-window filtering.",
    )
    return parser.parse_args()


def main() -> int:
    """Execute `main`."""
    args = parse_args()
    exclude_models = [item.strip() for item in (args.exclude_models or "").split(",") if item.strip()]
    payload = run_route_agent(
        {
            "task": args.task,
            "agent_name": args.agent_name,
            "request_id": args.request_id or None,
            "system_prompt": args.system_prompt,
            "constraints": {
                "max_cost": args.max_cost,
                "preferred_model": args.preferred_model,
                "exclude_models": exclude_models,
                "require_provider": args.require_provider or None,
                "estimated_input_tokens": args.estimated_input_tokens,
            },
        },
        limit=args.limit,
        include_ollama=args.include_ollama,
        min_total_threshold=5,
        load_env_file=True,
        db_backend=args.db_backend,
        sqlite_path=args.sqlite_path or None,
        postgres_dsn=args.postgres_dsn or None,
        sync_interval_days=max(1, int(args.sync_interval_days)),
        force_registry_sync=args.force_registry_sync,
        keep_registry_history=2,
        agent_name=args.agent_name,
        redis_url=args.redis_url or None,
        router_db_path=args.router_db_path or None,
        rate_limit_mode=args.rate_limit_mode,
        rate_limit_fail_strategy=args.rate_limit_fail_strategy,
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("model_used") else 1
