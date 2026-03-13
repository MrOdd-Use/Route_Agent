"""Task-analysis resolution and legacy fallback handling for the application layer."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from route_agent.app.contracts import RouteAgentRequest
from route_agent.app.legacy_analysis import build_legacy_analysis, detect_task_type, estimate_complexity
from route_agent.app.wiring import analyze_task
from route_agent.task_analyzer.schemas import TaskAnalysisResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedTaskAnalysis:
    """Task-analysis result plus fallback metadata."""

    result: TaskAnalysisResult
    record_id: int | None
    used_legacy_fallback: bool = False


def resolve_task_analysis(request: RouteAgentRequest) -> ResolvedTaskAnalysis:
    """Resolve task analysis, falling back to legacy heuristics when needed."""
    try:
        result, record_id = analyze_task(agent_name=request.agent_name, task_prompt=request.task)
        return ResolvedTaskAnalysis(result=result, record_id=record_id, used_legacy_fallback=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("task_analyzer failed, fallback to legacy analysis: %s", exc)
        task_type = detect_task_type(request.task)
        complexity = estimate_complexity(request.task, task_type)
        return ResolvedTaskAnalysis(
            result=build_legacy_analysis(task_type, complexity),
            record_id=None,
            used_legacy_fallback=True,
        )
