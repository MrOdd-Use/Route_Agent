"""LangChain client: model init, structured output, retry with exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import TYPE_CHECKING, Any

from route_agent.task_analyzer.config import DEFAULT_ANALYZER_MODEL, DEFAULT_MODEL_PROVIDER

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------

_PROVIDER_KEY_MAP: dict[str, str] = {
    "google_genai": "GOOGLE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
}


def _resolve_api_key(provider: str) -> str | None:
    env_var = _PROVIDER_KEY_MAP.get(provider)
    if env_var is None:
        return None
    return os.environ.get(env_var)


# ---------------------------------------------------------------------------
# LLM client creation
# ---------------------------------------------------------------------------

_llm_cache: dict[str, Any] = {}
_llm_cache_lock = threading.Lock()


def get_chat_model(
    model: str = DEFAULT_ANALYZER_MODEL,
    provider: str = DEFAULT_MODEL_PROVIDER,
) -> "BaseChatModel":
    """获取 LangChain chat model 实例 (带客户端复用)。"""
    cache_key = f"{provider}:{model}"
    if cache_key in _llm_cache:
        return _llm_cache[cache_key]

    with _llm_cache_lock:
        if cache_key in _llm_cache:
            return _llm_cache[cache_key]

        api_key = _resolve_api_key(provider)
        kwargs: dict[str, Any] = {"model_provider": provider}
        if api_key is not None:
            kwargs["api_key"] = api_key

        from langchain.chat_models import init_chat_model

        llm = init_chat_model(model, **kwargs)
        _llm_cache[cache_key] = llm
        return llm


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------


async def ainvoke_with_retry(
    chain: Any,
    prompt: Any,
    *,
    max_attempts: int = 2,
    backoff_seconds: float = 1.0,
) -> Any:
    """Invoke chain asynchronously with retry and exponential backoff."""
    from langchain_core.exceptions import OutputParserException

    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if backoff_seconds < 0:
        raise ValueError(f"backoff_seconds must be >= 0, got {backoff_seconds}")

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await chain.ainvoke(prompt)
        except (TimeoutError, ConnectionError, OSError, OutputParserException) as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                wait = backoff_seconds * (2 ** attempt)
                logger.warning(
                    "Attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt + 1, max_attempts, exc, wait,
                )
                await asyncio.sleep(wait)
    if last_exc is None:
        raise RuntimeError("Retry loop exhausted without result or captured exception.")
    raise last_exc
