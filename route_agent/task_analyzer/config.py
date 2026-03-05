"""Task Analyzer configuration: scoring tiers, defaults, dimension extraction, analyzer chain."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 评分阶梯
# ---------------------------------------------------------------------------
SCORE_TIERS: dict[str, tuple[int, int]] = {
    "simple": (1, 3),
    "medium": (4, 6),
    "hard": (7, 8),
    "expert": (9, 10),
}

# ---------------------------------------------------------------------------
# 分析器默认配置
# ---------------------------------------------------------------------------
DEFAULT_ANALYZER_MODEL = "gemini-3-pro"
DEFAULT_MODEL_PROVIDER = "google_genai"

# ---------------------------------------------------------------------------
# 合法 task_class 集合 (与 router_engine.constants.CLASS_DICT_INITIAL_SET 初始值一致，
# 独立定义以避免循环依赖)
# ---------------------------------------------------------------------------
TASK_CLASSES: tuple[str, ...] = (
    "general",
    "scrape",
    "extraction",
    "summarization",
    "classification",
    "rewrite",
    "review",
    "translation",
)

# ---------------------------------------------------------------------------
# 分析器优先级链 (按顺序尝试)
# ---------------------------------------------------------------------------
ANALYZER_CHAIN: list[dict[str, str]] = [
    {"model": "gemini-3-pro", "provider": "google_genai"},
    {"model": "deepseek-reasoner", "provider": "deepseek"},
]

# ---------------------------------------------------------------------------
# 维度动态提取
# ---------------------------------------------------------------------------

def get_capability_dimensions() -> tuple[str, ...]:
    """从 model_registry 公共 API 获取能力维度，不硬编码。"""
    from route_agent.model_registry import default_capabilities

    caps: dict[str, Any] = default_capabilities()
    return tuple(caps.keys())
