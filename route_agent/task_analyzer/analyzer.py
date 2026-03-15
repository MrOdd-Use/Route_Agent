"""Async-first task analysis orchestration."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from route_agent.task_analyzer.client import ainvoke_with_retry, get_chat_model
from route_agent.task_analyzer.config import ANALYZER_CHAIN
from route_agent.task_analyzer.prompt import (
    build_new_class_response_schema,
    build_new_class_system_prompt,
    build_response_schema,
    build_system_prompt,
    build_user_prompt,
)
from route_agent.task_analyzer.schemas import (
    DimensionScore,
    TaskAnalysisError,
    TaskAnalysisResult,
)
from route_agent.task_analyzer.storage import AnalysisRecord, AnalysisStorage

logger = logging.getLogger(__name__)

# Module-level storage instance (lazy init, thread-safe)
_storage: AnalysisStorage | None = None
_storage_lock = threading.Lock()


def _get_storage() -> AnalysisStorage:
    """Execute `_get_storage`."""
    global _storage  # noqa: PLW0603
    if _storage is None:
        with _storage_lock:
            if _storage is None:
                _storage = AnalysisStorage()
    return _storage


def _extract_token_usage(ai_message: Any) -> dict[str, int] | None:
    """Extract normalized token usage from heterogeneous provider metadata."""
    if ai_message is None:
        return None

    usage: dict[str, Any] | None = None

    response_meta = getattr(ai_message, "response_metadata", None)
    if isinstance(response_meta, dict):
        raw_usage = response_meta.get("token_usage")
        if isinstance(raw_usage, dict):
            usage = raw_usage

    if usage is None:
        usage_meta = getattr(ai_message, "usage_metadata", None)
        if isinstance(usage_meta, dict):
            usage = usage_meta

    if usage is None:
        return None

    # Common naming variants used by providers/LangChain adapters.
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    return {"input": int(input_tokens or 0), "output": int(output_tokens or 0)}


# ---------------------------------------------------------------------------
# Core async analysis
# ---------------------------------------------------------------------------


async def _do_analysis(
    agent_name: str,
    task_prompt: str,
    model: str,
    provider: str,
) -> tuple[TaskAnalysisResult, dict[str, int] | None]:
    """Run one analyzer attempt and return (result, token_usage)."""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_chat_model(model, provider)
        response_schema = build_response_schema()
        structured_llm = llm.with_structured_output(response_schema, include_raw=True)

        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(agent_name, task_prompt)
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    except Exception as exc:
        raise TaskAnalysisError(
            error_type="llm_setup_failed",
            message=f"Failed to prepare analyzer ({provider}/{model}): {exc}",
        ) from exc

    try:
        raw_output = await ainvoke_with_retry(structured_llm, messages)
    except Exception as exc:
        raise TaskAnalysisError(
            error_type="llm_invocation_failed",
            message=f"LLM invocation failed ({provider}/{model}): {exc}",
        ) from exc

    if raw_output is None:
        raise TaskAnalysisError(
            error_type="empty_response",
            message=f"LLM returned an empty response ({provider}/{model}).",
        )

    # include_raw=True returns {"raw": AIMessage, "parsed": Pydantic, "parsing_error": ...}
    parsed = raw_output.get("parsed") if isinstance(raw_output, dict) else raw_output
    ai_message = raw_output.get("raw") if isinstance(raw_output, dict) else None

    if parsed is None:
        parsing_err = raw_output.get("parsing_error") if isinstance(raw_output, dict) else None
        raise TaskAnalysisError(
            error_type="parse_error",
            message=f"Structured output parsing failed: {parsing_err}",
        )

    try:
        dims = tuple(
            DimensionScore(
                dimension=d.dimension,
                score=d.score,
                reasoning=d.reasoning,
            )
            for d in parsed.relevant_dimensions
        )
        raw_task_class = getattr(parsed, "task_class", None)
        result = TaskAnalysisResult(
            domain=parsed.domain,
            domain_description=parsed.domain_description,
            relevant_dimensions=dims,
            task_class=raw_task_class if isinstance(raw_task_class, str) and raw_task_class.strip() else None,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise TaskAnalysisError(
            error_type="parse_error",
            message=f"Failed to parse analyzer response: {exc}",
            raw_response=str(parsed),
        ) from exc

    return result, _extract_token_usage(ai_message)


# ---------------------------------------------------------------------------
# Public API: analyze_async
# ---------------------------------------------------------------------------


async def analyze_async(
    agent_name: str,
    task_prompt: str,
    model: str,
    provider: str,
) -> tuple[TaskAnalysisResult, int]:
    """Analyze one task and persist record, returning (result, record_id)."""
    storage = _get_storage()

    start = time.perf_counter()
    try:
        result, token_usage = await _do_analysis(agent_name, task_prompt, model, provider)
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        record_id = await storage.save_async(
            AnalysisRecord(
                agent_name=agent_name,
                prompt=task_prompt,
                domain=result.domain,
                dimensions=tuple(d.to_dict() for d in result.relevant_dimensions),
                analyzer_model=model,
                routed_model=None,
                success=True,
                token_usage=token_usage,
                response_time_ms=elapsed_ms,
            )
        )
        return result, record_id
    except TaskAnalysisError:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        await storage.save_async(
            AnalysisRecord(
                agent_name=agent_name,
                prompt=task_prompt,
                domain=None,
                dimensions=None,
                analyzer_model=model,
                routed_model=None,
                success=False,
                token_usage=None,
                response_time_ms=elapsed_ms,
            )
        )
        raise


# ---------------------------------------------------------------------------
# 新类判定分析（向量匹配未命中时调用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewClassAnalysisResult:
    """LLM 新类判定结果。"""

    analysis: TaskAnalysisResult
    suggested_new_class: str | None = None
    suggested_new_class_description: str | None = None


async def _do_new_class_analysis(
    agent_name: str,
    task_prompt: str,
    model: str,
    provider: str,
) -> tuple[NewClassAnalysisResult, dict[str, int] | None]:
    """调用 LLM 进行新类判定分析。"""
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = get_chat_model(model, provider)
        response_schema = build_new_class_response_schema()
        structured_llm = llm.with_structured_output(response_schema, include_raw=True)

        system_prompt = build_new_class_system_prompt()
        user_prompt = build_user_prompt(agent_name, task_prompt)
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    except Exception as exc:
        raise TaskAnalysisError(
            error_type="llm_setup_failed",
            message=f"Failed to prepare new-class analyzer ({provider}/{model}): {exc}",
        ) from exc

    try:
        raw_output = await ainvoke_with_retry(structured_llm, messages)
    except Exception as exc:
        raise TaskAnalysisError(
            error_type="llm_invocation_failed",
            message=f"New-class LLM invocation failed ({provider}/{model}): {exc}",
        ) from exc

    parsed = raw_output.get("parsed") if isinstance(raw_output, dict) else raw_output
    ai_message = raw_output.get("raw") if isinstance(raw_output, dict) else None

    if parsed is None:
        parsing_err = raw_output.get("parsing_error") if isinstance(raw_output, dict) else None
        raise TaskAnalysisError(
            error_type="parse_error",
            message=f"New-class structured output parsing failed: {parsing_err}",
        )

    try:
        dims = tuple(
            DimensionScore(
                dimension=d.dimension,
                score=d.score,
                reasoning=d.reasoning,
            )
            for d in parsed.relevant_dimensions
        )
        raw_task_class = getattr(parsed, "task_class", None)
        suggested_new = getattr(parsed, "suggested_new_class", None)
        suggested_desc = getattr(parsed, "suggested_new_class_description", None)

        analysis = TaskAnalysisResult(
            domain=parsed.domain,
            domain_description=parsed.domain_description,
            relevant_dimensions=dims,
            task_class=raw_task_class if isinstance(raw_task_class, str) and raw_task_class.strip() else None,
        )
        result = NewClassAnalysisResult(
            analysis=analysis,
            suggested_new_class=suggested_new if isinstance(suggested_new, str) and suggested_new.strip() else None,
            suggested_new_class_description=suggested_desc if isinstance(suggested_desc, str) and suggested_desc.strip() else None,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise TaskAnalysisError(
            error_type="parse_error",
            message=f"Failed to parse new-class response: {exc}",
            raw_response=str(parsed),
        ) from exc

    return result, _extract_token_usage(ai_message)


async def analyze_new_class_async(
    agent_name: str,
    task_prompt: str,
) -> NewClassAnalysisResult:
    """向量匹配未命中时，用 LLM 判定是否需要新类池。按 ANALYZER_CHAIN 顺序尝试。"""
    last_exc: TaskAnalysisError | None = None
    storage = _get_storage()

    for cfg in ANALYZER_CHAIN:
        start = time.perf_counter()
        try:
            result, token_usage = await _do_new_class_analysis(
                agent_name, task_prompt, cfg["model"], cfg["provider"],
            )
            elapsed_ms = int((time.perf_counter() - start) * 1000)

            # 记录分析日志
            await storage.save_async(
                AnalysisRecord(
                    agent_name=agent_name,
                    prompt=task_prompt,
                    domain=result.analysis.domain,
                    dimensions=tuple(d.to_dict() for d in result.analysis.relevant_dimensions),
                    analyzer_model=cfg["model"],
                    routed_model=None,
                    success=True,
                    token_usage=token_usage,
                    response_time_ms=elapsed_ms,
                )
            )

            if result.suggested_new_class:
                logger.warning(
                    "LLM 建议新类池: class=%s, description=%s, task=%s",
                    result.suggested_new_class,
                    result.suggested_new_class_description,
                    task_prompt[:100],
                )

            return result
        except TaskAnalysisError as exc:
            last_exc = exc
            logger.warning(
                "New-class analyzer %s (%s) failed: %s; trying next.",
                cfg["model"], cfg["provider"], exc,
            )
            continue

    raise TaskAnalysisError(
        error_type="all_analyzers_failed",
        message=f"All new-class analyzers failed. Last error: {last_exc}",
    )


# ---------------------------------------------------------------------------
# Public API: analyze_with_fallback
# ---------------------------------------------------------------------------


async def analyze_with_fallback(
    agent_name: str,
    task_prompt: str,
) -> tuple[TaskAnalysisResult, int]:
    """Try analyzers in priority order and raise if all fail."""
    last_exc: TaskAnalysisError | None = None

    for cfg in ANALYZER_CHAIN:
        try:
            return await analyze_async(
                agent_name=agent_name,
                task_prompt=task_prompt,
                model=cfg["model"],
                provider=cfg["provider"],
            )
        except TaskAnalysisError as exc:
            last_exc = exc
            logger.warning(
                "Analyzer %s (%s) unavailable: %s; trying next.",
                cfg["model"],
                cfg["provider"],
                exc,
            )
            continue

    raise TaskAnalysisError(
        error_type="all_analyzers_failed",
        message=f"All analyzers failed. Last error: {last_exc}",
    )


# ---------------------------------------------------------------------------
# Public API: analyze (sync wrapper)
# ---------------------------------------------------------------------------


def analyze(
    agent_name: str,
    task_prompt: str,
) -> tuple[TaskAnalysisResult, int]:
    """Sync wrapper that remains compatible with existing event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(analyze_with_fallback(agent_name, task_prompt))

    try:
        import nest_asyncio
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "analyze() was called inside an active event loop but nest_asyncio is not installed."
        ) from exc

    nest_asyncio.apply()
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(analyze_with_fallback(agent_name, task_prompt))
