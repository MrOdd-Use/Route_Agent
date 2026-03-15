"""Task-analysis resolution: 向量画像 → LLM 新类判定 → legacy 兜底。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from route_agent.app.contracts import RouteAgentRequest
from route_agent.app.legacy_analysis import build_legacy_analysis, detect_task_type, estimate_complexity
from route_agent.task_analyzer.schemas import TaskAnalysisResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedTaskAnalysis:
    """Task-analysis result plus fallback metadata."""

    result: TaskAnalysisResult
    record_id: int | None
    used_legacy_fallback: bool = False
    used_profile_match: bool = False
    suggested_new_class: str | None = None
    suggested_new_class_description: str | None = None
    profile_scores: dict[str, float] = field(default_factory=dict)


def _try_profile_analysis(request: RouteAgentRequest) -> ResolvedTaskAnalysis | None:
    """① 向量画像分析器：任务 input 与类池描述做 embedding 相似度匹配。"""
    try:
        from route_agent.task_analyzer.profile_analyzer import ProfileAnalyzer

        analyzer = ProfileAnalyzer()
        hit = analyzer.match(request.task)

        if hit is None:
            logger.info("向量画像匹配：全部低于阈值，进入 LLM 新类判定")
            return None

        result = analyzer.analyze(request.task)
        if result is None:
            return None

        logger.info(
            "向量画像匹配命中: class=%s, similarity=%.4f",
            hit.class_name,
            hit.similarity,
        )
        return ResolvedTaskAnalysis(
            result=result,
            record_id=None,
            used_profile_match=True,
            profile_scores=hit.all_scores,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量画像分析器异常，跳过: %s", exc)
        return None


def _try_new_class_analysis(request: RouteAgentRequest) -> ResolvedTaskAnalysis | None:
    """② LLM 新类判定：向量匹配未命中时，LLM 判断是否需要新类池。"""
    try:
        import asyncio

        from route_agent.task_analyzer.analyzer import analyze_new_class_async

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            nc_result = asyncio.run(
                analyze_new_class_async(agent_name=request.agent_name, task_prompt=request.task)
            )
        else:
            try:
                import nest_asyncio
                nest_asyncio.apply()
            except ModuleNotFoundError as exc:
                raise RuntimeError(
                    "analyze called inside active event loop without nest_asyncio"
                ) from exc
            loop = asyncio.get_event_loop()
            nc_result = loop.run_until_complete(
                analyze_new_class_async(agent_name=request.agent_name, task_prompt=request.task)
            )

        if nc_result.suggested_new_class:
            # 建议新类池 → 写入审核队列 + 日志
            logger.warning(
                "LLM 建议新建类池: suggested_class=%s, description=%s, "
                "assigned_class=%s, task=%.100s",
                nc_result.suggested_new_class,
                nc_result.suggested_new_class_description,
                nc_result.analysis.task_class,
                request.task,
            )
            _write_new_class_to_review_queue(
                nc_result.suggested_new_class,
                nc_result.suggested_new_class_description,
            )
        else:
            logger.info(
                "LLM 新类判定：归入现有类池 class=%s",
                nc_result.analysis.task_class,
            )

        return ResolvedTaskAnalysis(
            result=nc_result.analysis,
            record_id=None,
            suggested_new_class=nc_result.suggested_new_class,
            suggested_new_class_description=nc_result.suggested_new_class_description,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 新类判定失败，进入 legacy 兜底: %s", exc)
        return None


def _write_new_class_to_review_queue(class_name: str, description: str | None) -> None:
    """将 LLM 建议的新类写入 router_engine 的审核队列。"""
    try:
        from route_agent.router_engine.storage import RouterStorage

        storage = RouterStorage()
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(
                storage.upsert_class_review_candidate_async(class_name, proposed_by="llm_new_class")
            )
        else:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                storage.upsert_class_review_candidate_async(class_name, proposed_by="llm_new_class")
            )
        logger.info("新类 '%s' 已写入审核队列 (description=%s)", class_name, description)
    except Exception as exc:  # noqa: BLE001
        logger.warning("写入审核队列失败: %s", exc)


def resolve_task_analysis(request: RouteAgentRequest) -> ResolvedTaskAnalysis:
    """三级分析链路：向量画像 → LLM 新类判定 → legacy 兜底。"""

    # ① 向量画像分析器
    profile_result = _try_profile_analysis(request)
    if profile_result is not None:
        return profile_result

    # ② LLM 新类判定
    llm_result = _try_new_class_analysis(request)
    if llm_result is not None:
        return llm_result

    # ③ Legacy 关键词启发式兜底
    logger.warning("全部分析器失败，fallback 到 legacy 关键词启发式")
    task_type = detect_task_type(request.task)
    complexity = estimate_complexity(request.task, task_type)
    return ResolvedTaskAnalysis(
        result=build_legacy_analysis(task_type, complexity),
        record_id=None,
        used_legacy_fallback=True,
    )
