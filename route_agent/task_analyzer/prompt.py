"""Prompt template, dynamic Pydantic response schema, and few-shot examples."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any, Literal

from route_agent.task_analyzer.config import get_capability_dimensions


def _schema_suffix(dimensions: tuple[str, ...]) -> str:
    digest = hashlib.sha256("|".join(dimensions).encode("utf-8")).hexdigest()[:8]
    return digest


@lru_cache(maxsize=32)
def _build_response_schema_cached(dimensions: tuple[str, ...]) -> type[Any]:
    from pydantic import Field, create_model

    if not dimensions:
        raise ValueError("No capability dimensions registered; cannot build response schema.")

    dim_literal = Literal[dimensions]  # type: ignore[valid-type]
    suffix = _schema_suffix(dimensions)

    dyn_dimension_score = create_model(
        f"DimensionScore_{suffix}",
        dimension=(dim_literal, Field(description="Capability dimension name")),
        score=(int, Field(ge=1, le=10, description="Difficulty score 1-10")),
        reasoning=(str, Field(description="Reasoning for the score")),
    )

    dyn_analysis_response = create_model(
        f"AnalysisResponse_{suffix}",
        domain=(str, Field(description="Task domain")),
        domain_description=(str, Field(description="Short domain description")),
        relevant_dimensions=(
            list[dyn_dimension_score],  # type: ignore[valid-type]
            Field(description="Only include relevant dimensions."),
        ),
    )
    return dyn_analysis_response


# ---------------------------------------------------------------------------
# Dynamic Pydantic schema
# ---------------------------------------------------------------------------


def build_response_schema(
    dimensions: tuple[str, ...] | None = None,
) -> type[Any]:
    """Build a response schema constrained by registered capability dimensions."""
    resolved_dimensions = tuple(dimensions) if dimensions is not None else get_capability_dimensions()
    return _build_response_schema_cached(resolved_dimensions)


# ---------------------------------------------------------------------------
# Few-shot examples
# ---------------------------------------------------------------------------

_FEW_SHOT_EXAMPLES = """\
示例 1:
Agent: "translator"
Task: "将英文翻译为中文"
分析:
  domain: "translation"
  domain_description: "自然语言翻译"
  relevant_dimensions:
    - dimension: "text", score: 3, reasoning: "基础翻译任务，无专业术语"

示例 2:
Agent: "code_reviewer"
Task: "审查分布式系统的一致性协议实现，检查 Raft 共识算法的正确性"
分析:
  domain: "software_engineering"
  domain_description: "分布式系统代码审查"
  relevant_dimensions:
    - dimension: "code", score: 9, reasoning: "分布式共识算法审查需要专家级编程能力"
    - dimension: "math", score: 7, reasoning: "需要理解形式化证明和一致性模型"
"""


# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
你是一个任务分析专家。给定一个 Agent 名称和它的任务描述，你需要：

1. 判断任务所属的领域 (domain)
2. 从以下能力维度中，选出与该任务相关的维度，并给出难度评分 (1-10)
   可用维度: {dimensions}

评分标准：
- 1-3: 简单，基础知识即可完成
- 4-6: 中等，需要一定专业知识
- 7-8: 困难，需要深度专业能力
- 9-10: 专家级，需要顶尖领域专家

重要规则：
- 仅输出与任务相关的维度，不相关的维度不要出现
- 每个维度必须附带评分理由 (reasoning)
- 评分要客观，参考下面的示例锚定标准

{few_shot}
"""


def build_system_prompt(dimensions: tuple[str, ...] | None = None) -> str:
    """Build complete system prompt."""
    resolved_dimensions = dimensions if dimensions is not None else get_capability_dimensions()
    return _SYSTEM_PROMPT_TEMPLATE.format(
        dimensions=", ".join(resolved_dimensions),
        few_shot=_FEW_SHOT_EXAMPLES,
    )


def build_user_prompt(agent_name: str, task_prompt: str) -> str:
    """Build user prompt."""
    return f'Agent: "{agent_name}"\nTask: "{task_prompt}"\n请分析该任务。'
