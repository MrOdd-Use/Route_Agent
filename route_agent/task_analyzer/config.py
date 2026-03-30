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
DEFAULT_ANALYZER_MODEL = "deepseek-chat"
DEFAULT_MODEL_PROVIDER = "deepseek"

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
        "General-purpose tasks: open-ended Q&A, brainstorming, multi-turn conversation, "
        "knowledge retrieval, research, information lookup, answering questions, explaining concepts, "
        "giving advice, planning, writing reports, analysis, and mixed instruction-following tasks. "
        "Examples: 'What is X?', 'Help me plan Y', 'Search for latest news about Z', "
        "'Find information on topic', 'Investigate and summarize findings'."
    ),
    "scrape": (
        "Web scraping, data collection, and online search: crawling websites, parsing HTML, extracting data from web pages, "
        "searching for latest news, finding articles, retrieving online information, calling APIs to fetch data, "
        "scraping product listings, prices, news articles, paginated results, "
        "dynamic JavaScript-rendered pages. Requires code generation and structured output. "
        "Examples: 'Scrape product prices from this URL', 'Crawl all links on this page', "
        "'Search for latest news about X', 'Find recent articles on topic Y', "
        "'Fetch data from API endpoint', 'Extract table data from HTML'."
    ),
    "extraction": (
        "Structured information extraction from unstructured or semi-structured text: "
        "named entity recognition, relation extraction, event detection, pulling specific fields "
        "from documents such as contract clauses, resume parsing, invoice line items, form fields. "
        "Examples: 'Extract all dates and names from this document', 'Parse the invoice and return JSON', "
        "'Pull out key terms from this contract', 'Identify entities in this text'."
    ),
    "summarization": (
        "Text summarization and content condensation: compressing long documents, articles, meeting notes, "
        "research papers, or multi-source materials into concise key points or executive summaries. "
        "Covers extractive and abstractive summarization, TL;DR generation, bullet-point summaries. "
        "Examples: 'Summarize this article in 3 sentences', 'Give me a TL;DR of this report', "
        "'Condense this meeting transcript into action items', 'Write an abstract for this paper'."
    ),
    "classification": (
        "Text classification and labeling: assigning input content to predefined categories, "
        "sentiment analysis (positive/negative/neutral), intent detection, topic tagging, "
        "content moderation, spam detection, ticket routing, emotion recognition. "
        "Examples: 'Is this review positive or negative?', 'Classify this support ticket', "
        "'Detect the intent of this user message', 'Label this text as spam or not spam'."
    ),
    "rewrite": (
        "Text rewriting, editing, and polishing: style transfer, tone adjustment, paraphrasing, "
        "readability improvement, grammar correction, formality adjustment, SEO optimization, "
        "academic paraphrasing, simplifying complex text, making text more concise or more detailed. "
        "Examples: 'Rewrite this in a formal tone', 'Paraphrase this paragraph', "
        "'Make this email more professional', 'Simplify this technical explanation'."
    ),
    "review": (
        "Review and evaluation: systematic quality inspection and critique of code, documents, "
        "proposals, essays, or designs with structured feedback and improvement suggestions. "
        "Code review, pull request review, technical design review, contract risk assessment, "
        "proofreading, fact-checking, peer review. "
        "Examples: 'Review this code for bugs', 'Check this essay for issues', "
        "'Find problems in this implementation', 'Evaluate this technical proposal'."
    ),
    "translation": (
        "Language translation: converting text between natural languages with semantic accuracy, "
        "terminology consistency, and idiomatic expression. Technical document translation, "
        "localization, subtitle translation, multilingual content adaptation. "
        "Examples: 'Translate this English text to Chinese', 'Convert this document to French', "
        "'Localize this UI string for Japanese users', 'Translate this subtitle file'."
    ),
}

# ---------------------------------------------------------------------------
# 向量画像分析器配置（本地 Ollama embedding）
# ---------------------------------------------------------------------------
import os

PROFILE_EMBEDDING_MODEL: str = os.environ.get("PROFILE_EMBEDDING_MODEL", "nomic-embed-text")
PROFILE_EMBEDDING_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
PROFILE_MATCH_THRESHOLD: float = 0.5
PROFILE_STORAGE_DB_PATH: str | None = os.environ.get("PROFILE_STORAGE_DB_PATH")

# ---------------------------------------------------------------------------
# LLM 分析器优先级链 (向量匹配未命中时按顺序尝试)
# ---------------------------------------------------------------------------
ANALYZER_CHAIN: list[dict[str, str]] = [
    {"model": "deepseek-chat", "provider": "deepseek"},
    {"model": "gemini-2.0-flash", "provider": "google_genai"},
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
