"""Top-level Route Agent execution entry."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from route_agent.model_registry import MainModelPool, get_model_registry_report_with_local_pool


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _detect_task_type(task: str) -> str:
    text = task.lower()
    keyword_map = {
        "coding": [
            "code",
            "coding",
            "python",
            "bug",
            "debug",
            "function",
            "script",
            "sql",
            "programming",
        ],
        "translation": ["translate", "translation", "localize"],
        "scrape": ["scrape", "crawler", "crawl", "xpath", "selector", "html"],
        "extraction": ["extract", "extraction", "entity", "schema", "structured", "json"],
        "summarization": ["summarize", "summary", "tldr", "tl;dr", "brief"],
        "classification": ["classify", "classification", "label", "sentiment", "categorize"],
        "rewrite": ["rewrite", "paraphrase", "polish", "rephrase"],
        "review": ["review", "audit", "critique", "evaluate", "assessment"],
        "reasoning": ["reason", "prove", "analyze", "analysis", "deduce"],
        "math": ["math", "equation", "calculate", "formula", "algebra"],
    }
    for task_type, keywords in keyword_map.items():
        if any(keyword in text for keyword in keywords):
            return task_type
    return "qa"


# Characters beyond which a task is considered maximum-length for complexity scoring.
_COMPLEXITY_LENGTH_CEILING = 800


def _estimate_complexity(task: str, task_type: str) -> float:
    length_score = min(len(task) / _COMPLEXITY_LENGTH_CEILING, 1.0)
    base = 0.2 + 0.5 * length_score
    if task_type in {"coding", "review", "reasoning", "math"}:
        base += 0.2
    return max(0.0, min(base, 1.0))


def _choose_tier(analysis: dict[str, Any], max_cost: float | None) -> str:
    task_type = analysis["task_type"]
    complexity = analysis["complexity"]

    if max_cost is not None and max_cost <= 0.01:
        return "fast"
    if complexity >= 0.75:
        return "strategic"
    if task_type in {"coding", "review", "reasoning", "math"} or complexity >= 0.5:
        return "smart"
    return "fast"


def run_route_agent(
    request: dict[str, Any],
    *,
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
) -> dict[str, Any]:
    """Run one route decision using model registry + main model pool.

    Current scope:
    - Build model pool from registry (default SQLite local pool).
    - Perform lightweight heuristic task analysis.
    - Return route decision payload (without invoking model inference).
    """
    task = str(request.get("task") or "").strip()
    if not task:
        raise ValueError("request.task is required")

    constraints = request.get("constraints") or {}
    if not isinstance(constraints, dict):
        constraints = {}

    # Obtain model registry report:
    # - Default SQLite mode reads local snapshots and refreshes when due/forced.
    # - PostgreSQL backend can be selected explicitly.
    # - If local backend is unavailable, service falls back to live provider fetch.
    local_pool_result = get_model_registry_report_with_local_pool(
        limit=limit,
        include_ollama=include_ollama,
        min_total_threshold=min_total_threshold,
        load_env_file=load_env_file,
        db_backend=db_backend,
        sqlite_path=sqlite_path,
        postgres_dsn=postgres_dsn,
        sync_interval_days=sync_interval_days,
        keep_history=keep_registry_history,
        force_sync=force_registry_sync,
    )
    report = local_pool_result.report
    pool = MainModelPool.from_report(
        report,
        fast_model=(os.getenv("FAST_LLM") or "").strip() or None,
        smart_model=(os.getenv("SMART_LLM") or "").strip() or None,
        strategic_model=(os.getenv("STRATEGIC_LLM") or "").strip() or None,
    )

    task_type = _detect_task_type(task)
    complexity = _estimate_complexity(task, task_type)
    analysis = {"task_type": task_type, "complexity": complexity}

    max_cost = _as_float(constraints.get("max_cost"))
    preferred_model = str(constraints.get("preferred_model") or "").strip() or None
    tier = _choose_tier(analysis, max_cost)
    selection = pool.pick_tier(
        tier,
        max_cost=max_cost,
        preferred_model=preferred_model,
    )

    routing_reason = selection.reason
    if selection.model is None:
        routing_reason = f"{routing_reason}; registry_errors={report.errors}"

    return {
        "result": None,
        "model_used": selection.model.model_id if selection.model else None,
        "cost": None,
        "routing_reason": routing_reason,
        "analysis": analysis,
        "selected_tier": selection.tier,
        "pool_summary": pool.summary(),
        "registry_alerts": list(report.alerts),
        "registry_errors": dict(report.errors),
        "skipped_providers": [item.to_dict() for item in report.skipped_providers],
        "registry_sync": {
            "source": local_pool_result.source,
            "storage_backend": local_pool_result.storage_backend,
            "sync_due": local_pool_result.sync_due,
            "sync_performed": local_pool_result.sync_performed,
            "snapshot_version": local_pool_result.snapshot_version,
            "sync_interval_days": sync_interval_days,
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one Route Agent route decision.")
    parser.add_argument("--task", required=True, help="Task text to be routed.")
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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = run_route_agent(
        {
            "task": args.task,
            "constraints": {
                "max_cost": args.max_cost,
                "preferred_model": args.preferred_model,
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
    )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if payload.get("model_used") else 1


if __name__ == "__main__":
    raise SystemExit(main())
