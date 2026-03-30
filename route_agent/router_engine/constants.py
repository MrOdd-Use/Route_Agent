"""Constants for router_engine module."""

from __future__ import annotations

SCORE_TIER_EPSILON: float = 0.05
MAX_EXPLORE_SLOTS: int = 2
MIN_CANDIDATES_FOR_AUTO: int = 5

DEFAULT_DOMAIN_KEY: str = "__global__"
ENABLE_DOMAIN_DEFAULTS: bool = False
DEFAULT_AGENT_CLASS: str = "general"
ENABLE_CLASS_SIM_FALLBACK: bool = True
CLASS_SIM_THRESHOLD: float = 0.82
CLASS_SIM_MARGIN: float = 0.05
ENABLE_CONTROLLED_CLASS_DICT: bool = True
CLASS_REVIEW_MIN_HITS: int = 3
CLASS_DICT_INITIAL_SET: tuple[str, ...] = (
    "general",
    "scrape",
    "extraction",
    "summarization",
    "classification",
    "rewrite",
    "review",
    "translation",
)

CLASS_DESCRIPTIONS: dict[str, str] = {
    "general": (
        "通用任务类别，作为 LLM 分类失败或向量匹配置信不足时的兜底类别。"
        "涵盖不属于其他特定类别的通用性工作，包括但不限于开放式问答、头脑风暴、"
        "多轮对话、知识检索、指令执行等无法明确归入专用类别的混合型任务。"
        "该池中的模型需要具备广泛的通用能力，而非某一领域的深度专精。"
    ),
    "scrape": (
        "网页抓取与数据采集类任务。核心场景包括：从网页 HTML 中解析目标内容、"
        "调用第三方 API 获取并聚合数据、爬取分页列表或动态渲染页面、"
        "处理反爬机制（验证码、限流、User-Agent 伪装）下的稳定采集。"
        "该类任务对模型的代码生成能力和结构化输出能力要求较高，"
        "需要理解 HTTP 协议、DOM 结构、CSS 选择器及常见爬虫框架的使用模式。"
    ),
    "extraction": (
        "结构化信息抽取类任务。从非结构化或半结构化文本中提取实体、关系、"
        "事件或特定字段，并以预定义 schema 输出结果。典型场景包括："
        "合同关键条款提取、简历字段解析、发票 OCR 后结构化、"
        "学术论文元数据提取、日志中关键事件标记等。"
        "要求模型具备精确的指令遵循能力和稳定的 JSON/结构化输出格式控制，"
        "对幻觉容忍度极低——未在原文中出现的信息不可捏造。"
    ),
    "summarization": (
        "文本摘要与内容概括类任务。将长篇文档、会议记录、新闻报道或多源素材"
        "压缩为关键要点或指定篇幅的精简表述。覆盖抽取式摘要和生成式摘要两种模式，"
        "需兼顾信息保真度与可读性。典型场景包括：长文一句话总结、"
        "多文档跨源综合摘要、会议纪要生成、周报/日报自动生成、"
        "论文 Abstract 改写等。对模型的长上下文理解能力和信息筛选能力要求较高。"
    ),
    "classification": (
        "文本分类与标签标注类任务。将输入内容归入一个或多个预定义类别，"
        "或对文本进行情感、意图、主题等维度的标注。典型场景包括："
        "工单自动分类、用户反馈情感分析、邮件意图识别、"
        "新闻主题标注、内容审核（违规/合规判定）、多标签层级分类等。"
        "要求模型在给定类别体系下具备一致且可复现的判定能力，"
        "对边界案例需要稳定的决策逻辑而非随机输出。"
    ),
    "rewrite": (
        "文本改写与润色类任务。在保持原意不变的前提下，对文本进行风格转换、"
        "语气调整、可读性优化或格式重组。典型场景包括：学术论文降重改写、"
        "营销文案语气调整（正式↔口语）、技术文档面向不同受众的重写、"
        "SEO 优化改写、多版本 A/B 文案生成、错别字和语病修正等。"
        "要求模型在遵循改写约束的同时保持语义忠实，"
        "尤其需要精确控制输出风格而不偏离原始信息。"
    ),
    "review": (
        "审查与评估类任务。对代码、文档、方案或内容进行系统性质量审核，"
        "输出结构化的评审意见和改进建议。典型场景包括：Code Review（逻辑缺陷、"
        "安全漏洞、性能瓶颈、编码规范）、技术方案评审、论文同行评审模拟、"
        "合同条款风险审查、产品 PRD 完整性检查、翻译质量评估等。"
        "要求模型具备深度的领域专业知识和批判性思维能力，"
        "能够识别潜在问题并提供可操作的修改建议，而非泛泛而谈。"
    ),
    "translation": (
        "语言翻译类任务。在不同自然语言之间进行高质量转换，"
        "需兼顾语义准确性、术语一致性和目标语言的地道表达。典型场景包括："
        "技术文档中英互译、法律合同翻译、本地化（UI/UX 文案适配目标市场）、"
        "学术论文摘要翻译、多语言客服话术生成、字幕翻译与时间轴对齐等。"
        "要求模型在专业术语处理上具备领域感知能力，"
        "能根据上下文选择最贴切的译法，并保持全篇术语和风格的一致性。"
    ),
}

# 每个类池的维度画像：维度名 → 需求分数 (1-10)，不需要的维度不列出（等效为 0）。
# 向量画像分析器命中类池后，用此画像生成 TaskAnalysisResult 的 relevant_dimensions。
CLASS_DIMENSION_PROFILES: dict[str, dict[str, int]] = {
    "general": {},
    "scrape": {
        "code": 6,
        "search": 5,
        "instruction_following": 4,
    },
    "extraction": {
        "instruction_following": 8,
        "text": 6,
        "code": 3,
    },
    "summarization": {
        "text": 8,
        "creative_writing": 3,
    },
    "classification": {
        "instruction_following": 7,
        "text": 5,
    },
    "rewrite": {
        "creative_writing": 7,
        "text": 6,
        "instruction_following": 4,
    },
    "review": {
        "code": 8,
        "text": 5,
        "math": 4,
    },
    "translation": {
        "text": 8,
        "creative_writing": 5,
    },
}

RATE_LIMIT_FAIL_STRATEGY_DEFAULT: str = "degrade"

SUCCESS_BONUS_STREAK: int = 3
SUCCESS_BONUS_FACTOR: float = 1.20
FAIL_PENALTY_STREAK: int = 3
FAIL_PENALTY_FACTOR: float = 0.95

UNABLE_PROBE_INTERVAL_S: int = 3600
PROBE_COOLDOWN_S: int = 300

EXEC_FAIL_UNABLE_THRESHOLD: int = 3
QUALITY_FAIL_DEGRADED_THRESHOLD: int = 5
DEGRADED_QUALITY_MULTIPLIER: float = 0.70
DEGRADED_COOLDOWN_S: int = 86400
PROBE_TESTING_PENALTY: float = 0.25
PROBE_GOOD_FEEDBACK_THRESHOLD: int = 2
PROBE_CONSECUTIVE_SUCCESS_THRESHOLD: int = 10
QUALITY_FAIL_RESET_STREAK: int = 2

DEFAULT_PROMOTION_MIN_SUCCESS: int = 20
DOWNGRADE_PROMOTION_MIN_SUCCESS: int = 15
DOWNGRADE_SUCCESS_THRESHOLD: int = 10
DOWNGRADE_SCORE_GAP_MAX: float = 0.10
DOWNGRADE_TRIAL_MIN_SAMPLES: int = 5
DOWNGRADE_ROLLBACK_QUALITY_FAIL: int = 2
DOWNGRADE_ROLLBACK_EXEC_FAIL: int = 1
DOWNGRADE_CANARY_RATIO: float = 0.50
DOWNGRADE_COOLDOWN_H: int = 24
DOWNGRADE_MIN_SAVINGS_RATIO: float = 0.10

PRICE_CAP: float = 10.0
COST_ALPHA: float = 3.0

REDIS_PREFIX_RPM: str = "route_agent:rpm:"
REDIS_PREFIX_RPD: str = "route_agent:rpd:"
REDIS_PREFIX_CONC: str = "route_agent:conc:"

POOL_MAX_SIZE: int = 10
POOL_BONUS_BASE_RATIO: float = 0.06
POOL_BONUS_FULL_RATIO: float = 0.10
POOL_ENTRY_CONF_LB_MIN: float = 0.25
POOL_MODEL_MAX_AGE_DAYS: int = 180
POOL_AGE_EXEMPT_SUCCESS: int = 10
POOL_AGE_EXEMPT_RATE: float = 0.80
MIN_TRIALS: int = 10

EXEC_FAIL_RETRY: int = 2
EXEC_FAIL_UNAVAILABLE: int = 1
QUALITY_FAIL_REVOKE: int = 3

RPM_UTIL_LOW: float = 0.70
RPM_UTIL_HIGH: float = 0.90
CONC_UTIL_LOW: float = 0.60
CONC_UTIL_HIGH: float = 0.85
DEFAULT_SKIP_POWER: float = 2.0

FALLBACK_MIN_HEADROOM: float = 0.05
BETA_PRIOR: float = 2.0
WILSON_Z: float = 1.645
CHALLENGER_LEAD_STREAK: int = 3

UTIL_CACHE_TTL_MS: int = 150
RECENT_LIMITED_TTL_S: float = 5.0
ESCALATION_CONC_RATIO: float = 0.30
ESCALATION_UTIL_CEILING: float = 0.95
ESCALATION_WAIT_BASE_DELAY: float = 0.5
ESCALATION_WAIT_MAX_ATTEMPTS: int = 3
ESCALATION_WAIT_MAX_TOTAL: float = 7.0
ESCALATION_MAX_WAITING: int = 50
MAX_ESCALATION_ATTEMPTS: int = 3

CEILING_SLOTS: int = 1
MAX_SAME_PROVIDER_IN_CANDIDATES: int = 999
COLD_START_INDEX_BY_COMPLEXITY: tuple[tuple[str, int, int], ...] = (
    ("expert", 8, 0),
    ("hard", 5, 1),
    ("medium", 3, 2),
    ("easy", 0, 3),
)
CONTEXT_LIMIT_BUFFER_RATIO: float = 0.90
OVERLOAD_PENALTY_MIN: float = 0.20

EXPLORE_SLOTS_MIN: int = 1
EXPLORE_SLOTS_MAX: int = 3
EXPLORE_POOL_RICH_THRESHOLD: int = 5
EXPLORE_AVG_TRIALS_THRESHOLD: float = 5.0
NEW_MODEL_LOOKBACK_DAYS: int = 30
NEW_MODEL_BONUS: float = 0.04

# ── Pool seed ──────────────────────────────────────────────
POOL_SEED_SIZE: int = 3
SEED_DIM_WEIGHT: float = 0.7
SEED_COST_WEIGHT: float = 0.3
GENERAL_FALLBACK_DIMS: dict[str, int] = {"reasoning": 7, "text": 6, "instruction_following": 5}
