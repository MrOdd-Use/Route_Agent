"""CLI subcommands for manual class-pool management (渠道2: 手动添加)."""

from __future__ import annotations

import argparse
import json
import os

from route_agent.app.contracts import RouteAgentRunOptions
from route_agent.app.registry import build_registry_context
from route_agent.app.wiring import get_analysis_storage, get_engine


def _default_options() -> RouteAgentRunOptions:
    """Build minimal run options from environment for pool management."""
    return RouteAgentRunOptions(
        load_env_file=True,
        db_backend=os.getenv("ROUTE_AGENT_DB_BACKEND", "sqlite"),
        sqlite_path=os.getenv("ROUTE_AGENT_SQLITE_PATH"),
        router_db_path=os.getenv("ROUTER_DB_PATH"),
        redis_url=os.getenv("REDIS_URL"),
    )


def _build_engine(options: RouteAgentRunOptions):
    """Build a RouterEngine for pool operations."""
    registry = build_registry_context(options)
    analysis_storage = get_analysis_storage(options.analysis_db_path)

    redis_url = options.redis_url or (os.getenv("REDIS_URL") or "").strip() or None
    router_db_path = options.router_db_path or (os.getenv("ROUTER_DB_PATH") or "").strip() or None
    rate_limit_mode = options.rate_limit_mode or "auto"
    rate_limit_fail_strategy = options.rate_limit_fail_strategy or "degrade"

    return get_engine(
        registry.pool,
        analysis_storage,
        redis_url=redis_url,
        router_db_path=router_db_path,
        rate_limit_mode=rate_limit_mode,
        rate_limit_fail_strategy=rate_limit_fail_strategy,
    )


def _cmd_add(args: argparse.Namespace) -> int:
    """Handle `pool add` subcommand."""
    options = _default_options()
    engine = _build_engine(options)
    result = engine.add_model_to_pool(args.agent_class, args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") != "error" else 1


def _cmd_remove(args: argparse.Namespace) -> int:
    """Handle `pool remove` subcommand."""
    options = _default_options()
    engine = _build_engine(options)
    result = engine.remove_model_from_pool(args.agent_class, args.model)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") != "error" else 1


def _cmd_list(args: argparse.Namespace) -> int:
    """Handle `pool list` subcommand."""
    import asyncio

    options = _default_options()
    engine = _build_engine(options)

    if args.agent_class:
        result = asyncio.run(engine.inspect_pool_async(args.agent_class))
    else:
        result = asyncio.run(engine.list_pools_async())

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def build_pool_parser() -> argparse.ArgumentParser:
    """Build the argument parser for pool subcommands."""
    parser = argparse.ArgumentParser(
        prog="python -m route_agent pool",
        description="Manual class-pool management (渠道2: 手动添加/移除模型).",
    )
    sub = parser.add_subparsers(dest="pool_action", required=True)

    # pool add
    add_parser = sub.add_parser("add", help="Manually add a model to a class pool.")
    add_parser.add_argument("--class", dest="agent_class", required=True, help="Target agent class (e.g. extraction).")
    add_parser.add_argument("--model", required=True, help="Model id (e.g. openai:gpt-4o).")

    # pool remove
    rm_parser = sub.add_parser("remove", help="Remove a model from a class pool.")
    rm_parser.add_argument("--class", dest="agent_class", required=True, help="Target agent class.")
    rm_parser.add_argument("--model", required=True, help="Model id to remove.")

    # pool list
    list_parser = sub.add_parser("list", help="List class pools or inspect a specific pool.")
    list_parser.add_argument("--class", dest="agent_class", default="", help="Optional class to inspect.")

    return parser


def pool_main(argv: list[str] | None = None) -> int:
    """Entry point for pool subcommands."""
    parser = build_pool_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "add": _cmd_add,
        "remove": _cmd_remove,
        "list": _cmd_list,
    }
    handler = dispatch.get(args.pool_action)
    if handler is None:
        parser.print_help()
        return 1
    return handler(args)
