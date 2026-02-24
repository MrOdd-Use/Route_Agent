"""Dependency wiring and singleton builders for app service."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from route_agent.model_registry.pool import MainModelPool
from route_agent.router_engine import RouterEngine
from route_agent.task_analyzer.schemas import TaskAnalysisResult

_engine: RouterEngine | None = None
_engine_key: tuple[Any, ...] | None = None
_engine_lock = threading.Lock()
_analysis_storage: Any | None = None
_analysis_storage_key: str | None = None
_analysis_storage_lock = threading.Lock()


def analyze_task(agent_name: str, task_prompt: str) -> tuple[TaskAnalysisResult, int]:
    """Execute `analyze_task`."""
    from route_agent.task_analyzer import analyze

    return analyze(agent_name=agent_name, task_prompt=task_prompt)


def get_analysis_storage(db_path: str | None) -> Any:
    """Execute `get_analysis_storage`."""
    from route_agent.task_analyzer import AnalysisStorage

    global _analysis_storage  # noqa: PLW0603
    global _analysis_storage_key  # noqa: PLW0603

    normalized_key = str(Path(db_path).resolve()) if db_path else ""
    if _analysis_storage is not None and _analysis_storage_key == normalized_key:
        return _analysis_storage

    with _analysis_storage_lock:
        if _analysis_storage is None or _analysis_storage_key != normalized_key:
            _analysis_storage = AnalysisStorage(Path(db_path) if db_path else None)
            _analysis_storage_key = normalized_key

    return _analysis_storage


def get_engine(
    pool: MainModelPool,
    analysis_storage: Any,
    *,
    redis_url: str | None,
    router_db_path: str | None,
    rate_limit_mode: str,
    rate_limit_fail_strategy: str,
) -> RouterEngine:
    """Execute `get_engine`."""
    global _engine  # noqa: PLW0603
    global _engine_key  # noqa: PLW0603

    normalized_router_db_path = str(Path(router_db_path).resolve()) if router_db_path else ""
    key = (
        id(pool),
        normalized_router_db_path,
        redis_url or "",
        (rate_limit_mode or "auto").strip().lower(),
        (rate_limit_fail_strategy or "degrade").strip().lower(),
        id(analysis_storage),
    )

    if _engine is not None and _engine_key == key:
        return _engine

    with _engine_lock:
        if _engine is None or _engine_key != key:
            _engine = RouterEngine(
                pool,
                analysis_storage,
                redis_url=redis_url,
                router_db_path=Path(router_db_path) if router_db_path else None,
                rate_limit_mode=rate_limit_mode,
                rate_limit_fail_strategy=rate_limit_fail_strategy,
            )
            _engine_key = key

    return _engine
