"""FastAPI dependency injection callables."""

from __future__ import annotations

import functools
import logging

from route_agent.api.config import ApiSettings
from route_agent.app.registry import build_registry_context
from route_agent.model_registry import MainModelPool

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    """Return cached API settings loaded from the environment."""
    return ApiSettings()


def get_model_pool(settings: ApiSettings | None = None) -> MainModelPool | None:
    """Build model pool from registry. Returns `None` on failure."""
    resolved = settings or get_api_settings()
    try:
        return build_registry_context(resolved.to_run_options()).pool
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to build model pool: %s", exc)
        return None
