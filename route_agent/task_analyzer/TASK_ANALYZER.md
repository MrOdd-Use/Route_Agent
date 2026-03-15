# Task Analyzer Module — Implementation Plan

## Context

Route Agent 当前的任务分析已经从早期的简单关键词启发式演进为应用层三层链路：先做向量画像匹配，未命中再做 LLM 新类判定，最后才回退到 legacy 关键词启发式。
legacy `_detect_task_type()` 和 `_estimate_complexity()` 仍保留为最终兜底路径。
其中 legacy `task_type` 集合为：
`coding`、`translation`、`scrape`、`extraction`、`summarization`、`classification`、`rewrite`、`review`、`reasoning`、`math`，未命中时回退 `qa`。

**目标**: 说明当前 Task Analyzer 模块的职责边界：提供向量画像匹配、LLM 结构化分析与新类判定、分析记录持久化，并由 `route_agent.app.analysis` 负责把这些能力编排成三层分析链路。

---

## Requirements Summary

| 项目 | 说明 |
|------|------|
| **输入** | agent_name (str) + task_prompt (str) |
| **应用层链路** | `vector profile -> LLM new-class -> legacy` |
| **LLM** | `ANALYZER_CHAIN` via **LangChain**（当前按 `gemini-3-pro` → `deepseek-reasoner` 顺序尝试） |
| **输出** | 任务领域 + 相关维度难度评分 (1-10) + 可选 `task_class` / 新类建议 |
| **评分阶梯** | 1-3 简单, 4-6 中等, 7-8 困难, 9-10 专家级 |
| **维度来源** | **动态**从模型注册表 `default_capabilities()` 提取，不硬编码 |
| **维度输出** | 仅输出相关维度，不相关维度不出现 |
| **API** | Async-First: `analyze_async()` / `analyze_with_fallback()` / `analyze_new_class_async()` + `analyze()` sync 包装 |
| **存储** | SQLite 持久化 LLM 分析记录 (含模型、耗时、token、反馈) |
| **反馈** | 支持自动 (系统检测) + 手动 (人工评价)，数据反哺路由决策 |
| **范围** | 提供任务分析与新类判定信号，不直接负责模型匹配；链路编排由 `route_agent.app.analysis` 完成 |

---

## File Structure

```
route_agent/task_analyzer/
├── __init__.py              # 公共 API 导出: analyze_async, analyze_with_fallback,
│                            #   TaskAnalysisResult, TaskAnalysisError, AnalysisStorage
├── schemas.py               # 数据结构 (frozen dataclasses + Exception)
├── config.py                # 评分阶梯、默认配置 + 维度动态提取 + 类池描述 + 向量画像配置
├── prompt.py                # Prompt 模板 + 动态 Pydantic schema + few-shot 示例 + 新类判定 prompt
├── client.py                # LangChain 客户端 (retry + 客户端复用)
├── analyzer.py              # Async-First 编排 (事件循环兼容) + 新类判定分析
├── profile_analyzer.py      # 向量画像分析器 (Ollama embedding + 余弦相似度匹配)
├── profile_storage.py       # 类池描述 embedding 的 SQLite 缓存
├── storage.py               # SQLite 本地存储 (分析记录持久化, async 兼容)
└── tests/
    ├── test_task_analyzer_module.py
    ├── test_profile_analyzer.py   # 向量画像分析器测试
    ├── test_schemas.py
    ├── test_prompt.py
    ├── test_storage.py
    └── test_analyzer.py

data/
├── task_analysis.db         # SQLite 数据库文件 (自动创建)
└── profile_embeddings.db    # 类池描述 embedding 缓存 (自动创建)
```

**修改的现有文件**:
- `requirements.txt` — 新增 `langchain-google-genai>=2.0.0`, `langchain-deepseek>=0.1.0`, `aiosqlite>=0.20.0`, `nest-asyncio>=1.6.0`
- `route_agent/model_registry/__init__.py` — 导出 `default_capabilities`
- `route_agent/__init__.py` — 添加 task_analyzer 导出
- `route_agent/app/analysis.py` — 集成应用层三层分析链路（向量画像 + LLM 新类判定 + legacy 兜底）

---

## Key Design Decisions

### 1. 维度动态提取 + 配置合并 (config.py)

原 `constants.py` 和 `dimensions.py` 合并为 `config.py`，内容精简：

```python
from route_agent.model_registry import default_capabilities

# --- 评分阶梯 ---
SCORE_TIERS = {
    "simple": (1, 3),
    "medium": (4, 6),
    "hard": (7, 8),
    "expert": (9, 10),
}

DEFAULT_ANALYZER_MODEL = "gemini-3-pro"
DEFAULT_MODEL_PROVIDER = "google_genai"

# --- 维度动态提取 ---
def get_capability_dimensions() -> tuple[str, ...]:
    """从 model_registry 公共 API 获取维度，不硬编码。"""
    return tuple(default_capabilities().keys())
```

`model_registry/__init__.py` 新增导出:
```python
from route_agent.model_registry.providers.utils import default_capabilities
```

### 2. LangChain 连接 Gemini

**主要方式** — `init_chat_model` 通用工厂:
```python
from langchain.chat_models import init_chat_model
llm = init_chat_model("gemini-3-pro", model_provider="google_genai", api_key=api_key)
```

**备选方式** — 直接导入:
```python
from langchain_google_genai import ChatGoogleGenerativeAI
llm = ChatGoogleGenerativeAI(model="gemini-3-pro", google_api_key=api_key)
```

依赖 `langchain-google-genai` 已通过 `uv pip install` 安装。

### 3. TaskAnalysisError 为 Exception 子类

**不是** frozen dataclass，而是可被 raise 的异常：

```python
class TaskAnalysisError(Exception):
    def __init__(self, error_type: str, message: str, raw_response: str | None = None):
        self.error_type = error_type
        self.raw_response = raw_response
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error_type": self.error_type, "message": str(self), "raw_response": self.raw_response}
```

### 4. Async-First + 事件循环兼容

`analyze_async()` 是核心实现，`analyze()` 包装时处理事件循环冲突：

```python
def analyze(...) -> TaskAnalysisResult:
    try:
        asyncio.get_running_loop()
        # 已在事件循环中 (FastAPI/Jupyter)，用 nest_asyncio
        import nest_asyncio
        nest_asyncio.apply()
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(analyze_async(...))
    except RuntimeError:
        # 无事件循环，正常 asyncio.run
        return asyncio.run(analyze_async(...))
```

### 5. 动态 Pydantic Enum 约束 + Structured Output

`DimensionScore.dimension` 字段动态约束为注册表维度，通过 LangChain `with_structured_output()` 强制 LLM 返回结构化 JSON：

```python
from pydantic import create_model, Field
from typing import Literal

def build_response_schema(dimensions: tuple[str, ...]) -> type[BaseModel]:
    DimLiteral = Literal[dimensions]  # 动态 enum

    DynDimensionScore = create_model(
        "DimensionScore",
        dimension=(DimLiteral, Field(description="能力维度名称")),
        score=(int, Field(ge=1, le=10, description="难度评分 1-10")),
        reasoning=(str, Field(description="评分理由")),
    )

    DynAnalysisResponse = create_model(
        "AnalysisResponse",
        domain=(str, Field(description="任务所属领域")),
        domain_description=(str, Field(description="领域简述")),
        relevant_dimensions=(list[DynDimensionScore], Field(description="相关维度评分")),
    )
    return DynAnalysisResponse

# 在 client.py 中构建 chain:
ResponseSchema = build_response_schema(get_capability_dimensions())
structured_llm = llm.with_structured_output(ResponseSchema)
result = await structured_llm.ainvoke(prompt)  # 直接返回 Pydantic 对象，无需手动解析
```

`with_structured_output()` 会自动将 Pydantic schema 注入 LLM 的 function calling / JSON mode，
解析失败时抛出 `OutputParserException`，由重试机制捕获处理。

### 6. LLM 调用重试机制

简单指数退避重试，仅对瞬时错误重试：

```python
async def ainvoke_with_retry(
    chain,
    prompt: Any,
    *,
    max_attempts: int = 2,
    backoff_seconds: float = 1.0,
) -> Any:
    from langchain_core.exceptions import OutputParserException

    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    if backoff_seconds < 0:
        raise ValueError(f"backoff_seconds must be >= 0, got {backoff_seconds}")

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await chain.ainvoke(prompt)
        except (TimeoutError, ConnectionError, OSError, OutputParserException) as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                await asyncio.sleep(backoff_seconds * (2 ** attempt))
    if last_exc is None:
        raise RuntimeError("Retry loop exhausted without result or captured exception.")
    raise last_exc
```

### 7. SQLite 本地存储

每次分析结果持久化到 SQLite，记录完整上下文，用于审计和反哺路由。

**数据库路径**: `{project_root}/data/task_analysis.db` (自动创建目录和文件)

**表结构** `analysis_records`:

```sql
CREATE TABLE IF NOT EXISTS analysis_records (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name       TEXT    NOT NULL,
    prompt           TEXT    NOT NULL,
    domain           TEXT,
    dimensions       TEXT,                -- JSON: [{"dimension": "code", "score": 9, "reasoning": "..."}]
    analyzer_model   TEXT    NOT NULL,    -- 执行分析的 LLM (如 gemini-3-pro)
    routed_model     TEXT,                -- 最终被路由到的执行模型 (分析阶段为 NULL, 路由后回填)
    success          INTEGER NOT NULL,    -- 0=失败, 1=成功
    token_usage      TEXT,                -- JSON: {"input": 150, "output": 80}
    response_time_ms INTEGER,             -- 分析耗时 (毫秒)
    feedback         TEXT,                -- JSON: 统一承载错误信息 + 执行反馈
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_records_agent ON analysis_records(agent_name);
CREATE INDEX IF NOT EXISTS idx_records_created ON analysis_records(created_at);
```

#### 7.1 双模型字段

| 字段 | 说明 | 写入时机 |
|------|------|----------|
| `analyzer_model` | 执行本次 LLM 分析尝试的模型（如 `gemini-3-pro` / `deepseek-reasoner`） | `save()` 时写入 |
| `routed_model` | 最终执行任务的模型 | 路由完成后通过 `update_routed_model()` 回填 |

#### 7.2 性能追踪字段

| 字段 | 说明 |
|------|------|
| `token_usage` | JSON: `{"input": 150, "output": 80}` — 分析器 LLM 的 token 消耗 |
| `response_time_ms` | 分析器 LLM 调用耗时 (毫秒) |

#### 7.3 Feedback 机制

`feedback` 字段统一承载所有执行后信息，分两个阶段写入：

**阶段 1: 执行结果 (自动)** — 路由模型执行完成后，系统自动记录是否成功：

```json
{
    "execution": {
        "completed": false,
        "error_type": "network_error",
        "error_detail": "Connection refused to model endpoint"
    },
    "quality": null
}
```

| error_type | 说明 |
|------------|------|
| `network_error` | 网络连接问题 |
| `token_limit` | token 超出模型限制 |
| `timeout` | 响应超时 |
| `format_error` | 输出不符合预期格式 |
| `rate_limit` | 触发速率限制 |
| `null` | 执行成功，无错误 |

**阶段 2: 质量评价 (人工)** — 仅在执行成功后，人工评估输出质量：

```json
{
    "execution": {
        "completed": true,
        "error_type": null,
        "error_detail": null
    },
    "quality": {
        "rating": "poor",
        "action": "upgrade",
        "note": "输出不够完整，需要更高阶模型"
    }
}
```

| 子字段 | 类型 | 说明 |
|--------|------|------|
| `quality.rating` | str | `"good"` / `"acceptable"` / `"poor"` |
| `quality.action` | str \| None | `"upgrade"` (升阶) / `"downgrade"` (降阶) / `null` (无需调整) |
| `quality.note` | str \| None | 补充说明 |

**写入时序**:
1. `save()` — 分析完成，feedback 为 `null`
2. `update_routed_model()` — 路由完成，回填 routed_model
3. `update_execution_result()` — 执行完成，写入 `feedback.execution`
4. `update_quality_review()` (可选) — 人工评价，追加 `feedback.quality` (内部自动 merge)

#### 7.4 Async 兼容

Storage 使用 `aiosqlite` 提供异步接口，避免阻塞事件循环：

```python
import aiosqlite
import sqlite3
import json
from pathlib import Path
from dataclasses import dataclass

@dataclass(frozen=True)
class AnalysisRecord:
    agent_name: str
    prompt: str
    domain: str | None
    dimensions: list[dict] | None
    analyzer_model: str
    routed_model: str | None
    success: bool
    token_usage: dict | None = None       # {"input": 150, "output": 80}
    response_time_ms: int | None = None
    feedback: dict | None = None          # {"execution": {...}, "quality": {...}}

class AnalysisStorage:
    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path(__file__).resolve().parents[2] / "data" / "task_analysis.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._init_db_sync()  # 首次建表用同步 (仅执行一次)

    def _init_db_sync(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(CREATE_TABLES_SQL)

    async def save_async(self, record: AnalysisRecord) -> int:
        """异步保存记录，返回 row id。"""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(INSERT_SQL, _record_to_params(record))
            await db.commit()
            return cursor.lastrowid

    def save(self, record: AnalysisRecord) -> int:
        """同步保存 (非事件循环环境使用)。"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(INSERT_SQL, _record_to_params(record))
            return cursor.lastrowid

    async def update_routed_model_async(self, record_id: int, routed_model: str) -> None:
        """路由完成后回填实际执行模型。"""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE analysis_records SET routed_model = ? WHERE id = ?",
                (routed_model, record_id),
            )
            await db.commit()

    async def update_execution_result_async(
        self, record_id: int, *, completed: bool,
        error_type: str | None = None, error_detail: str | None = None,
    ) -> None:
        """执行完成后写入 feedback.execution，并保留已有 quality。"""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"No analysis record found with id={record_id}")
            feedback = json.loads(row[0]) if row and row[0] else {"quality": None}
            feedback["execution"] = {
                "completed": completed,
                "error_type": error_type,
                "error_detail": error_detail,
            }
            await db.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )
            await db.commit()

    async def update_quality_review_async(
        self, record_id: int, *, rating: str,
        action: str | None = None, note: str | None = None,
    ) -> None:
        """人工评价后追加 feedback.quality (自动 merge 已有 execution)。"""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?", (record_id,),
            )
            row = await cursor.fetchone()
            feedback = json.loads(row[0]) if row and row[0] else {"execution": None}
            feedback["quality"] = {"rating": rating, "action": action, "note": note}
            await db.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )
            await db.commit()

    def update_execution_result(
        self, record_id: int, *, completed: bool,
        error_type: str | None = None, error_detail: str | None = None,
    ) -> None:
        """同步版: 写入 feedback.execution，并保留已有 quality。"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?",
                (record_id,),
            )
            row = cursor.fetchone()
            if row is None:
                raise ValueError(f"No analysis record found with id={record_id}")
            feedback = json.loads(row[0]) if row and row[0] else {"quality": None}
            feedback["execution"] = {
                "completed": completed,
                "error_type": error_type,
                "error_detail": error_detail,
            }
            conn.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )

    def update_quality_review(
        self, record_id: int, *, rating: str,
        action: str | None = None, note: str | None = None,
    ) -> None:
        """同步版: 追加 feedback.quality。"""
        with sqlite3.connect(self._db_path) as conn:
            cursor = conn.execute(
                "SELECT feedback FROM analysis_records WHERE id = ?", (record_id,),
            )
            row = cursor.fetchone()
            feedback = json.loads(row[0]) if row and row[0] else {"execution": None}
            feedback["quality"] = {"rating": rating, "action": action, "note": note}
            conn.execute(
                "UPDATE analysis_records SET feedback = ? WHERE id = ?",
                (json.dumps(feedback, ensure_ascii=False), record_id),
            )
```

#### 7.5 在 analyzer.py 中集成

```python
storage = AnalysisStorage()

start = time.perf_counter()
try:
    result, usage = await _do_analysis(agent_name, task_prompt, analyzer_model)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    record_id = await storage.save_async(AnalysisRecord(
        agent_name=agent_name, prompt=task_prompt,
        domain=result.domain,
        dimensions=[d.to_dict() for d in result.relevant_dimensions],
        analyzer_model=analyzer_model, routed_model=None,
        success=True,
        token_usage=usage, response_time_ms=elapsed_ms,
    ))
    return result, record_id  # record_id 供后续回填 routed_model 和 feedback
except TaskAnalysisError as exc:
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    await storage.save_async(AnalysisRecord(
        agent_name=agent_name, prompt=task_prompt,
        domain=None, dimensions=None,
        analyzer_model=analyzer_model, routed_model=None,
        success=False,
        token_usage=None, response_time_ms=elapsed_ms,
    ))
    raise

# --- 后续由调用方分阶段回填 ---
# 阶段 1: 路由完成后
await storage.update_routed_model_async(record_id, "deepseek-chat")

# 阶段 2: 执行完成后 (自动)
await storage.update_execution_result_async(record_id, completed=True)
# 或执行失败时:
await storage.update_execution_result_async(
    record_id, completed=False,
    error_type="token_limit", error_detail="Input exceeds 8192 token limit",
)

# 阶段 3: 人工评价后 (可选, 仅执行成功时)
await storage.update_quality_review_async(
    record_id, rating="poor", action="upgrade", note="推理深度不足",
)
```

#### 7.6 聚合查询

Storage 模块仅负责读写。Feedback 聚合分析（如"某类任务升阶频率"）放在**单独模块**中处理，
为 router_engine 的路由决策提供数据支撑。该模块不在 task_analyzer 范围内。

### 8. Few-Shot 示例锚定评分

Prompt 中包含 2-3 个示例，锚定评分标准一致性：

```
示例 1:
Agent: "translator"
Task: "将英文翻译为中文"
分析: domain="translation", relevant_dimensions=[{dimension: "text", score: 3, reasoning: "基础翻译任务"}]

示例 2:
Agent: "code_reviewer"
Task: "审查分布式系统的一致性协议实现，检查 Raft 共识算法的正确性"
分析: domain="software_engineering", relevant_dimensions=[
  {dimension: "code", score: 9, reasoning: "分布式共识算法审查需要专家级编程能力"},
  {dimension: "math", score: 7, reasoning: "需要理解形式化证明和一致性模型"}
]
```

### 9. 分析链路与降级语义

当前系统把“任务分析容错”拆成两层：

```
应用层 (`route_agent.app.analysis.resolve_task_analysis`)
① 向量画像匹配 (ProfileAnalyzer)
   ↓ 全部低于阈值 / 异常
② LLM 新类判定 (analyze_new_class_async)
   ↓ 全部 LLM 尝试失败
③ Legacy 关键词启发式兜底
```

```
task_analyzer 模块内部 (`ANALYZER_CHAIN`)
gemini-3-pro / google_genai
  ↓ 失败
deepseek-reasoner / deepseek
```

第一层是应用层编排，决定整个请求最终走哪条分析路径。
第二层是 LLM 新类判定内部的模型级 fallback：单次调用先做 retry，某个分析模型失败后继续尝试下一个分析模型；只有整个 LLM 新类判定阶段都失败，应用层才会进入 legacy 兜底。

**config.py 中配置分析器链**:

```python
# LLM 分析器优先级链 (向量匹配未命中时按顺序尝试)
ANALYZER_CHAIN: list[dict[str, str]] = [
    {"model": "gemini-3-pro", "provider": "google_genai"},
    {"model": "deepseek-reasoner", "provider": "deepseek"},
]
```

**analyzer.py 中实现 LLM 级 fallback**:

```python
async def analyze_new_class_async(agent_name: str, task_prompt: str) -> NewClassAnalysisResult:
    """向量匹配未命中时，用 LLM 判定是否需要新类池。"""
    last_exc: TaskAnalysisError | None = None

    for cfg in ANALYZER_CHAIN:
        try:
            result, token_usage = await _do_new_class_analysis(
                agent_name, task_prompt, cfg["model"], cfg["provider"],
            )
            await storage.save_async(...)
            return result
        except TaskAnalysisError as exc:
            last_exc = exc
            logger.warning("New-class analyzer %s (%s) failed: %s; trying next.", ...)
            continue

    raise TaskAnalysisError(
        error_type="all_analyzers_failed",
        message=f"All new-class analyzers failed. Last error: {last_exc}",
    )
```

**Storage 记录**: 成功进入 LLM 分析路径时，`analysis_records` 会写入实际命中的 `analyzer_model`
（如 `"gemini-3-pro"` / `"deepseek-reasoner"`）。向量画像直接命中和应用层 legacy 兜底当前不生成独立的 analyzer 记录，因此这两条路径上的 `record_id` 为 `None`。

---

### 10. 向量画像分析器 (profile_analyzer.py)

在 LLM 分析器之前新增向量画像匹配层，以零 LLM 调用完成大多数已知类别的快速分类。

**三级分析链路** (`app/analysis.py` → `resolve_task_analysis`):

```
① 向量画像分析器 (ProfileAnalyzer)
   ↓ 全部低于阈值 / 异常
② LLM 新类判定 (analyze_new_class_async)
   ↓ 失败
③ Legacy 关键词启发式兜底
```

#### 10.1 向量匹配原理

1. 从 `router_engine/constants.py` 的 `CLASS_DESCRIPTIONS` 获取每个类池的自然语言描述
2. 使用本地 Ollama embedding 模型 (`nomic-embed-text`) 将描述文本转为向量
3. 将任务输入文本同样转为向量
4. 计算余弦相似度，取最高匹配
5. 若最高相似度 ≥ 阈值 (`PROFILE_MATCH_THRESHOLD`, 默认 0.6)，命中该类池

命中后，从 `CLASS_DIMENSION_PROFILES` 取出预定义的维度分数，直接组装 `TaskAnalysisResult`，
无需 LLM 调用。

#### 10.2 embedding 缓存 (profile_storage.py)

类池描述的 embedding 存储在 `data/profile_embeddings.db`：

```sql
CREATE TABLE IF NOT EXISTS class_profile_embeddings (
    class_name       TEXT NOT NULL,
    description_hash TEXT NOT NULL,
    embedding        TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (class_name)
);
```

- 使用描述文本的 SHA-256 哈希前 16 位作为 `description_hash`
- 描述变更时自动失效并重新计算
- 首次启动或描述变更时才会调用 Ollama embedding

#### 10.3 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PROFILE_EMBEDDING_MODEL` | `nomic-embed-text` | Ollama embedding 模型 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 服务地址 |
| `PROFILE_MATCH_THRESHOLD` | `0.6` | 余弦相似度命中阈值 |
| `PROFILE_STORAGE_DB_PATH` | `data/profile_embeddings.db` | embedding 缓存数据库路径 |

### 11. LLM 新类判定 (analyzer.py → analyze_new_class_async)

向量匹配未命中时，调用 LLM 判定任务是否属于现有类别，或建议新建类池。

**输出结构** `NewClassAnalysisResult`:

```python
@dataclass(frozen=True)
class NewClassAnalysisResult:
    analysis: TaskAnalysisResult           # 常规分析结果
    suggested_new_class: str | None        # 建议的新类名 (英文小写下划线)
    suggested_new_class_description: str | None  # 新类描述
```

- 如果归入现有类别：`suggested_new_class` 和 `suggested_new_class_description` 均为 `None`
- 如果建议新类：写入 `router_engine` 的审核队列，并记录日志

**Prompt 设计** (`prompt.py → build_new_class_system_prompt`):
- 列出所有现有类别及描述
- 要求 LLM 优先归入现有类别
- 仅确实不匹配时才建议新类
- 响应 schema 包含 `suggested_new_class` 和 `suggested_new_class_description` 字段

### 12. 类池描述与维度画像

#### 12.1 TASK_CLASS_DESCRIPTIONS (config.py)

每个任务类别的自然语言描述，用于 LLM prompt 中列出类别含义：

| 类别 | 描述摘要 |
|------|----------|
| `general` | 通用任务：开放式问答、头脑风暴、多轮对话等 |
| `scrape` | 网页抓取与数据采集 |
| `extraction` | 结构化信息抽取 |
| `summarization` | 文本摘要与内容概括 |
| `classification` | 文本分类与标签标注 |
| `rewrite` | 文本改写与润色 |
| `review` | 审查与评估 |
| `translation` | 语言翻译 |

#### 12.2 CLASS_DIMENSION_PROFILES (router_engine/constants.py)

每个类池的预定义维度分数，向量画像命中后直接使用：

| 类别 | 维度分数 |
|------|----------|
| `general` | (空) |
| `scrape` | code=6, search=5, instruction_following=4 |
| `extraction` | instruction_following=8, text=6, code=3 |
| `summarization` | text=8, creative_writing=3 |
| `classification` | instruction_following=7, text=5 |
| `rewrite` | creative_writing=7, text=6, instruction_following=4 |
| `review` | code=8, text=5, math=4 |
| `translation` | text=8, creative_writing=5 |

---
