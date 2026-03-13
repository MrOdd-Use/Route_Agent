"""FastAPI dependency injection callables."""

from __future__ import annotations

import functools
import logging

from dotenv import load_dotenv

from route_agent.api.config import ApiSettings
from route_agent.app.orchestrator import _resolve_router_runtime
from route_agent.app.registry import RegistryContext, build_registry_context
from route_agent.app.wiring import get_analysis_storage, get_engine
from route_agent.model_registry import MainModelPool
from route_agent.router_engine import RouterEngine

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Return cached API settings loaded from the environment."""
    load_dotenv()
    return ApiSettings()


def get_registry_context(settings: ApiSettings | None = None) -> RegistryContext | None:
    """Build registry context for API requests. Returns `None` on failure."""
    resolved = settings or get_api_settings()
    try:
        return build_registry_context(resolved.to_run_options())
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to build registry context: %s", exc)
        return None


def get_model_pool(settings: ApiSettings | None = None) -> MainModelPool | None:
    """Build model pool from registry. Returns `None` on failure."""
    context = get_registry_context(settings)
    return None if context is None else context.pool


def build_router_engine(pool: MainModelPool, settings: ApiSettings | None = None) -> RouterEngine:
    """Create or reuse the router engine for inspection-style API routes."""
    resolved = settings or get_api_settings()
    options = resolved.to_run_options()
    analysis_storage = get_analysis_storage(options.analysis_db_path)
    redis_url, router_db_path, rate_limit_mode, rate_limit_fail_strategy = _resolve_router_runtime(options)
    return get_engine(
        pool,
        analysis_storage,
        redis_url=redis_url,
        router_db_path=router_db_path,
        rate_limit_mode=rate_limit_mode,
        rate_limit_fail_strategy=rate_limit_fail_strategy,
    )
