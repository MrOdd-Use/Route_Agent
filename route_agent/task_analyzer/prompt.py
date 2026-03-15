"""Prompt template, dynamic Pydantic response schema, and few-shot examples."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any, Literal

from route_agent.task_analyzer.config import TASK_CLASS_DESCRIPTIONS, TASK_CLASSES, get_capability_dimensions


def _schema_suffix(dimensions: tuple[str, ...]) -> str:
    """Execute `_schema_suffix`."""
    digest = hashlib.sha256("|".join(dimensions).encode("utf-8")).hexdigest()[:8]
    return digest


@lru_cache(maxsize=32)
def _build_response_schema_cached(dimensions: tuple[str, ...]) -> type[Any]:
    """Execute `_build_response_schema_cached`."""
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
        task_class=(
            str | None,
            Field(default=None, description="Agent task class chosen from the predefined set; null if none matches."),
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
  task_class: "translation"
  relevant_dimensions:
    - dimension: "text", score: 3, reasoning: "基础翻译任务，无专业术语"

示例 2:
Agent: "code_reviewer"
Task: "审查分布式系统的一致性协议实现，检查 Raft 共识算法的正确性"
分析:
  domain: "software_engineering"
  domain_description: "分布式系统代码审查"
  task_class: "review"
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
3. 判断任务的所属类别 (task_class)，从以下预定义集合中选择一个最匹配的：
   可用类别: {task_classes}
   若均不匹配，输出 null

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
        task_classes="; ".join(
            f"{cls} — {TASK_CLASS_DESCRIPTIONS[cls]}" if cls in TASK_CLASS_DESCRIPTIONS else cls
            for cls in TASK_CLASSES
        ),
        few_shot=_FEW_SHOT_EXAMPLES,
    )


def build_user_prompt(agent_name: str, task_prompt: str) -> str:
    """Build user prompt."""
    return f'Agent: "{agent_name}"\nTask: "{task_prompt}"\n请分析该任务。'


# ---------------------------------------------------------------------------
# 新类判定 prompt（向量匹配未命中时，LLM 判断是否需要新建类池）
# ---------------------------------------------------------------------------

_NEW_CLASS_SYSTEM_PROMPT_TEMPLATE = """\
你是一个任务分类专家。当前系统已有以下预定义任务类别：
{task_classes}

给定一个任务描述，你需要：

1. 判断该任务是否属于以上某个现有类别
2. 从以下能力维度中，选出与该任务相关的维度，并给出难度评分 (1-10)
   可用维度: {dimensions}
3. 如果任务明显不属于任何现有类别，建议一个新类别名称和描述

评分标准：
- 1-3: 简单，基础知识即可完成
- 4-6: 中等，需要一定专业知识
- 7-8: 困难，需要深度专业能力
- 9-10: 专家级，需要顶尖领域专家

重要规则：
- 优先归入现有类别，只有确实不匹配时才建议新类别
- 仅输出与任务相关的维度
- 如果建议新类别，suggested_new_class 填写类别名称（英文小写下划线），suggested_new_class_description 填写描述
- 如果归入现有类别，suggested_new_class 和 suggested_new_class_description 均填 null
"""


@lru_cache(maxsize=32)
def _build_new_class_response_schema_cached(dimensions: tuple[str, ...]) -> type[Any]:
    """构建带 suggested_new_class 字段的动态响应 schema。"""
    from pydantic import Field, create_model

    if not dimensions:
        raise ValueError("No capability dimensions registered.")

    dim_literal = Literal[dimensions]  # type: ignore[valid-type]
    suffix = _schema_suffix(dimensions)

    dyn_dimension_score = create_model(
        f"NewClassDimensionScore_{suffix}",
        dimension=(dim_literal, Field(description="Capability dimension name")),
        score=(int, Field(ge=1, le=10, description="Difficulty score 1-10")),
        reasoning=(str, Field(description="Reasoning for the score")),
    )

    dyn_response = create_model(
        f"NewClassAnalysisResponse_{suffix}",
        domain=(str, Field(description="Task domain")),
        domain_description=(str, Field(description="Short domain description")),
        relevant_dimensions=(
            list[dyn_dimension_score],  # type: ignore[valid-type]
            Field(description="Only include relevant dimensions."),
        ),
        task_class=(
            str | None,
            Field(default=None, description="Existing task class if matches; null if suggesting new class."),
        ),
        suggested_new_class=(
            str | None,
            Field(default=None, description="Suggested new class name (lowercase, underscores); null if existing class matches."),
        ),
        suggested_new_class_description=(
            str | None,
            Field(default=None, description="Description for the suggested new class; null if not suggesting."),
        ),
    )
    return dyn_response


def build_new_class_response_schema(
    dimensions: tuple[str, ...] | None = None,
) -> type[Any]:
    """Build response schema with new-class suggestion fields."""
    resolved = tuple(dimensions) if dimensions is not None else get_capability_dimensions()
    return _build_new_class_response_schema_cached(resolved)


def build_new_class_system_prompt(dimensions: tuple[str, ...] | None = None) -> str:
    """Build system prompt for new-class determination."""
    resolved_dimensions = dimensions if dimensions is not None else get_capability_dimensions()
    return _NEW_CLASS_SYSTEM_PROMPT_TEMPLATE.format(
        dimensions=", ".join(resolved_dimensions),
        task_classes="; ".join(
            f"{cls} — {TASK_CLASS_DESCRIPTIONS[cls]}" if cls in TASK_CLASS_DESCRIPTIONS else cls
            for cls in TASK_CLASSES
        ),
    )
