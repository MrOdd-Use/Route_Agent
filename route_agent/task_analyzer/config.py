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

TASK_CLASS_DESCRIPTIONS: dict[str, str] = {
    "general": (
        "通用任务类别，涵盖不属于其他特定类别的通用性工作，包括开放式问答、"
        "头脑风暴、多轮对话、知识检索、指令执行等混合型任务"
    ),
    "scrape": (
        "网页抓取与数据采集，从网页 HTML 解析目标内容、调用第三方 API 获取数据、"
        "爬取分页列表或动态渲染页面，对代码生成和结构化输出能力要求较高"
    ),
    "extraction": (
        "结构化信息抽取，从非结构化或半结构化文本中提取实体、关系、事件或特定字段，"
        "如合同条款提取、简历解析、发票结构化，要求精确指令遵循和稳定的格式输出"
    ),
    "summarization": (
        "文本摘要与内容概括，将长篇文档、会议记录、多源素材压缩为关键要点，"
        "覆盖抽取式和生成式摘要，对长上下文理解和信息筛选能力要求较高"
    ),
    "classification": (
        "文本分类与标签标注，将输入内容归入预定义类别或进行情感、意图、主题标注，"
        "如工单分类、情感分析、内容审核，要求一致且可复现的判定能力"
    ),
    "rewrite": (
        "文本改写与润色，在保持原意不变的前提下进行风格转换、语气调整、可读性优化，"
        "如学术降重、文案语气调整、SEO 改写，要求语义忠实且风格可控"
    ),
    "review": (
        "审查与评估，对代码、文档、方案或内容进行系统性质量审核并输出结构化评审意见，"
        "如 Code Review、技术方案评审、合同风险审查，要求深度专业知识和批判性思维"
    ),
    "translation": (
        "语言翻译，在不同自然语言之间进行高质量转换，兼顾语义准确性、术语一致性和地道表达，"
        "如技术文档互译、本地化适配、字幕翻译，要求领域术语感知和全篇风格一致"
    ),
}

# ---------------------------------------------------------------------------
# 向量画像分析器配置（本地 Ollama embedding）
# ---------------------------------------------------------------------------
import os

PROFILE_EMBEDDING_MODEL: str = os.environ.get("PROFILE_EMBEDDING_MODEL", "nomic-embed-text")
PROFILE_EMBEDDING_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
PROFILE_MATCH_THRESHOLD: float = 0.6
PROFILE_STORAGE_DB_PATH: str | None = os.environ.get("PROFILE_STORAGE_DB_PATH")

# ---------------------------------------------------------------------------
# LLM 分析器优先级链 (向量匹配未命中时按顺序尝试)
# ---------------------------------------------------------------------------
ANALYZER_CHAIN: list[dict[str, str]] = [
    {"model": "gemini-3-pro", "provider": "google_genai"},
    {"model": "deepseek-reasoner", "provider": "deepseek"},
]

# LLM 判定无反馈时的超时秒数（超时后 LLM 自行分配）
NEW_CLASS_FEEDBACK_TIMEOUT_S: int = int(os.environ.get("NEW_CLASS_FEEDBACK_TIMEOUT_S", "0"))

# ---------------------------------------------------------------------------
# 维度动态提取
# ---------------------------------------------------------------------------

def get_capability_dimensions() -> tuple[str, ...]:
    """从 model_registry 公共 API 获取能力维度，不硬编码。"""
    from route_agent.model_registry import default_capabilities

    caps: dict[str, Any] = default_capabilities()
    return tuple(caps.keys())
