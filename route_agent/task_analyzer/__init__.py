"""Task Analyzer — intelligent LLM-based task analysis engine.

Public API:
    analyze_async        — async core analysis with fallback chain
    analyze_with_fallback — async, tries all analyzers in priority order
    analyze              — sync wrapper (event-loop compatible)
    TaskAnalysisResult   — analysis output dataclass
    TaskAnalysisError    — raisable exception
    AnalysisStorage      — SQLite persistence layer
"""

from route_agent.task_analyzer.analyzer import (
    analyze,
    analyze_async,
    analyze_with_fallback,
)
from route_agent.task_analyzer.schemas import TaskAnalysisError, TaskAnalysisResult
from route_agent.task_analyzer.storage import AnalysisStorage

__all__ = [
    "analyze",
    "analyze_async",
    "analyze_with_fallback",
    "TaskAnalysisResult",
    "TaskAnalysisError",
    "AnalysisStorage",
]
