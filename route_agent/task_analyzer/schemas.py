"""Task Analyzer data structures and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DimensionScore:
    """单个能力维度的难度评分。"""

    dimension: str
    score: int  # 1-10
    reasoning: str

    def __post_init__(self) -> None:
        if not 1 <= self.score <= 10:
            raise ValueError(f"score must be between 1 and 10, got {self.score}")

    def to_dict(self) -> dict[str, Any]:
        return {"dimension": self.dimension, "score": self.score, "reasoning": self.reasoning}


@dataclass(frozen=True)
class TaskAnalysisResult:
    """LLM 分析器返回的完整结果。"""

    domain: str
    domain_description: str
    relevant_dimensions: tuple[DimensionScore, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "domain_description": self.domain_description,
            "relevant_dimensions": [d.to_dict() for d in self.relevant_dimensions],
        }


class TaskAnalysisError(Exception):
    """任务分析过程中的错误，可被 raise。"""

    def __init__(
        self,
        error_type: str,
        message: str,
        raw_response: str | None = None,
    ) -> None:
        self.error_type = error_type
        self.raw_response = raw_response
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "message": str(self),
            "raw_response": self.raw_response,
        }
