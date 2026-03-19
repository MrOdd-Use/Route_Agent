# Router Engine — 实现计划

## 1. 概述

将应用编排层（`app/service.py`）中的路由逻辑提取为独立的 `router_engine/` 模块。新模块实现：

- 基于维度匹配 + 健康度 + 成本的分层过滤排序
- Top-5 选择（Pool 优先 + 最多 2 个探索槽）
- 升阶/降阶状态机
- Redis 滑动窗口限流（RPM/RPD/并发）
- 基于成功案例的默认模型学习
- Agent 类别模型池（Class Pool）：按 agent_class 共享模型池
  - **当前阶段（demo）**：默认模型按 `agent_class` 管理（`domain` 固定为 `__global__`）
  - **未来阶段**：切换到 `(agent_class, domain)` 粒度管理默认模型

**文档分层说明：**
- §2-9：**主规范**（实现必须遵守）— 文件结构、集成点、数据结构、常量、DDL、模块设计、实现阶段、app/service.py 接入
- §10-13：**配置与验证** — 依赖、验证方式、Arena 集成、风险降级
- §14：**附录**（推导与深度分析）— Class Pool 详细设计、高并发风险分析、运维 runbook
- 当 §7 与 §14 描述同一机制时，以 §7 为准（§14 提供背景推导和边界场景分析）

## 2. 文件结构

```
route_agent/router_engine/
    __init__.py              # 公共 API 导出
    schemas.py               # 所有 frozen dataclass
    constants.py             # 阈值、默认值、Redis key 前缀、类池描述与维度画像
    scorer.py                # 维度匹配打分 + 成本打分
    health.py                # 连续成功奖励（成本反比加权）+ 可用性状态（Unable）+ 探测
    rate_limiters/           # 限流子包（base/inmemory/redis/factory）
    selector.py              # 分层过滤 → 排序 → Top-5 选择
    escalation.py            # 重试 + 升阶状态机
    class_pool.py            # Agent 类别模型池：池管理 + 默认模型 + 淘汰
    defaults.py              # 默认模型查询（读写 class_pool_defaults 表）
    downgrade.py             # 自动降阶优化器
    storage/                 # 存储子包（router_storage + 各类 repository）
    engine.py                # RouterEngine 主类：编排所有组件
    tests/
        __init__.py
        test_defaults.py
        test_engine_downgrade_start.py
        test_manual_pool.py
        test_router_engine_module.py
        test_router_storage.py
        perf/                # 性能测试
            __init__.py
            _agent_scenarios.py
            _perf_metrics.py
            test_batch_concurrency_allocation_perf.py
```

## 3. 与现有代码的集成点

### 3.1 从 model_registry 读取

| 类/字段 | 位置 | 用途 |
|---------|------|------|
| `ModelMetadata` | `model_registry/schemas.py` | 模型元数据（非 frozen，有 `slots=True`） |
| `ModelMetadata.model_id` | `str` | 主键，格式 `"provider:model_name"` |
| `ModelMetadata.capabilities` | `dict[str, Any]` | `{"text": 0-100, "code": 0-100, "math": 0-100, "vision": 0-100, "search": 0-100, "instruction_following": 0-100, "creative_writing": 0-100, "_source": {...}}`，值可能为 `None`（Arena 未覆盖时） |
| `ModelMetadata.pricing` | `dict[str, Any]` | `{"currency": "USD", "unit": "per_1k_tokens", "input": float, "output": float}` |
| `ModelMetadata.limits` | `dict[str, Any]` | `{"max_requests_per_minute": int, "max_requests_per_day": int, ...}` |
| `ModelMetadata.status` | `dict[str, Any]` | `{"availability": str, ...}` |
| `ModelMetadata.provider` | `str` | 提供商名称 |
| `MainModelPool` | `model_registry/pool.py` | 模型池，提供 `list_available()` 和 `get(model_id)` |
| `PRICE_UNAVAILABLE_SENTINEL` | `model_registry/constants.py` | `1e12`，表示价格不可用 |

### 3.2 从 task_analyzer 接收

| 类/字段 | 位置 | 用途 |
|---------|------|------|
| `TaskAnalysisResult` | `task_analyzer/schemas.py` | frozen dataclass |
| `.domain` | `str` | 任务领域 |
| `.domain_description` | `str` | 领域描述 |
| `.relevant_dimensions` | `tuple[DimensionScore, ...]` | 任务维度评分 |
| `.task_class` | `str | None` | **Phase 7.5 新增字段** — LLM 生成的任务类别（如 `"scrape"`, `"extraction"`, `"summarization"`, `"classification"`, `"rewrite"`, `"review"`, `"translation"`）；字段缺失/为空时进入向量匹配兜底 |
| `DimensionScore.dimension` | `str` | 维度名（如 `"reasoning"`, `"coding"`, `"math"`） |
| `DimensionScore.score` | `int` | 1-10 |

### 3.3 feedback 字段结构

`analysis_records.feedback` 是 JSON 文本，结构为：
```json
{
  "execution": {"completed": bool, "error_type": str|null, "error_detail": str|null},
  "quality": {"rating": str, "action": str|null, "note": str|null}
}
```

健康度聚合需要读取 `execution.completed` 和 `quality.rating` 来判断 poor/good。

## 4. 数据结构设计 (`schemas.py`)

所有 dataclass 使用 `frozen=True`。

### 4.1 输入

```
RouteRequest
  ├── agent_name: str
  ├── agent_class: str | None          # 可选 override（默认不传；仅调试/人工强制指定时使用）
  ├── request_id: str                   # 请求级唯一标识（幂等/状态机主键，建议 UUID）
  ├── system_prompt: str | None         # Agent 系统提示词（用于 class 向量兜底匹配）
  ├── task_prompt: str
  ├── analysis: TaskAnalysisResult    # 来自 task_analyzer
  ├── record_id: int | None             # analysis_records.id（仅分析记录关联字段；可为 None）
  └── constraints: RouteConstraints
        ├── max_cost: float | None       # 预算上限，单位 USD/1M tokens（按 effective_price_per_1m 判定）
        ├── preferred_model: str | None
        ├── exclude_models: tuple[str, ...]
        ├── require_provider: str | None
        └── estimated_input_tokens: int | None  # 任务预估输入 token 数（用于上下文长度过滤；调用方估算，可取 len(task_prompt) 的近似值）
```

### 4.2 打分

```
ModelCandidate
  ├── model_id: str
  ├── provider: str
  ├── display_name: str
  ├── dimension_score: float          # 0.0-1.0（含 pool bonus 和健康修正后）
  ├── raw_dimension_score: float      # 0.0-1.0（Step 4 原始分，不含 bonus/penalty，用于升阶穿透和天花板槽位）
  ├── cost_score: float               # 0.0-1.0（越低越便宜）
  ├── health_status: str              # "healthy" | "bonus" | "penalty"（unable 已在 Step 1 过滤）
  ├── success_bonus: float            # effective_multiplier（1.0 = 无奖励，成本反比加权）
  ├── fail_penalty: float             # effective_penalty（1.0 = 无惩罚，0.95^penalty_level）
  ├── rate_limited: bool
  ├── is_default: bool
  ├── is_pool: bool                   # 是否来自 Class Pool
  ├── is_explore: bool                # 是否为探索槽模型（非池）
  └── rank: int                       # 0-indexed
```

### 4.3 输出

```
RouteDecision
  ├── primary_model: str | None       # 首发模型 model_id
  ├── candidates: tuple[ModelCandidate, ...]
  ├── start_index: int                # 0-indexed
  ├── reason: str
  ├── alerts: tuple[str, ...]
  ├── default_used: bool
  ├── pool_hit: bool                  # 新增，是否命中 Class Pool
  ├── pool_class: str | None          # 新增，命中的 agent_class
  └── class_source: str               # 新增，"override" | "llm" | "vector" | "default"
```

### 4.4 升阶

```
ExecutionAttempt
  ├── model_id: str
  ├── attempt_number: int
  ├── success: bool
  ├── failure_type: str | None        # "quality" | "deployment"
  ├── error_detail: str | None
  └── output_snippet: str | None

EscalationResult
  ├── action: str                     # "retry" | "escalate" | "escalate_breakthrough" | "alert_top_failed" | "alert_escalation_unavailable"
  ├── next_model: str | None
  ├── previous_attempts: tuple[ExecutionAttempt, ...]
  ├── context_for_next: str | None
  └── priority: str                   # "normal" | "elevated" | "forced"（默认 "normal"）
```

### 4.5 健康 & Class Pool & 模型统计 & 利用率

```
ModelUtilization                        # 模型实时利用率快照（per model_id）
  ├── rpm_ratio: float                  # 过去 60s 滑动窗口请求数 / max_rpm, [0.0, 1.0]
  ├── conc_ratio: float                 # 总并发比 (normal + esc) / max_conc, [0.0, 1.0]
  ├── normal_conc_ratio: float          # 正常流量并发比
  ├── escalation_conc_ratio: float      # 升阶流量并发比
  ├── escalation_capped: bool           # 升阶并发 >= escalation_cap
  ├── latency_ratio: float              # v2 预留: p95_latency / expected_latency, 默认 0.0
  └── is_limited: bool                  # 近期收到 429（进程内 TTL 标记）

  @property peak_ratio -> float:        # max(rpm_ratio, conc_ratio)（计算属性）

ModelAvailability                       # 模型可用性（per model_id，全局）
  ├── model_id: str
  ├── status: str                      # "available" | "degraded" | "unable"
  ├── degraded_since: str | None       # 标记 degraded 的时间
  ├── unable_since: str | None         # 标记 unable 的时间
  ├── last_probe_at: str | None        # 最近一次探测时间
  ├── last_probe_success: bool | None  # 最近一次探测结果
  └── updated_at: str | None

ClassModelStats                        # 模型实时统计，per (agent_class, model_id)，从首次使用开始追踪
  ├── agent_class: str
  ├── model_id: str
  ├── success_count: int              # 效果成功次数（累计）
  ├── fail_count: int                 # 效果失败次数（累计，poor）
  ├── exec_fail_count: int            # 执行失败次数（不影响 success_rate）
  ├── consecutive_success: int        # 连续效果成功（用于进池/默认晋升/降阶判定）
  ├── consecutive_fail: int           # 连续效果失败（用于默认撤销）
  ├── success_rate: float             # 冗余字段，每次 UPDATE 时重算
  ├── bonus_level: int                # 当前奖励等级，consecutive_success // 3
  ├── penalty_level: int              # 当前惩罚等级，consecutive_fail // 3
  ├── created_at: str | None
  └── updated_at: str | None

ClassPoolEntry                         # 子池成员，达标模型的子集（JOIN class_model_stats 填充统计字段）
  ├── agent_class: str
  ├── model_id: str
  ├── model_release_date: str | None
  ├── success_count: int               # 来自 class_model_stats，用于 apply_pool_bonus 计算 trials
  ├── fail_count: int                  # 来自 class_model_stats，用于 apply_pool_bonus 计算 trials
  ├── success_rate: float              # 来自 class_model_stats，用于池内排序
  ├── created_at: str | None
  └── updated_at: str | None

ClassPoolDefault
  ├── agent_class: str
  ├── domain: str
  ├── model_id: str
  ├── is_locked: bool                 # 用户手动锁定
  ├── consecutive_success: int        # 当前默认模型连续成功次数（demo 阶段 domain 固定 __global__）
  ├── consecutive_fail: int           # 当前默认模型连续失败次数（demo 阶段 domain 固定 __global__）
  ├── created_at: str | None
  └── updated_at: str | None

ClassAlias
  ├── alias_class: str                # 非标准别名（归一化后）
  ├── canonical_class: str            # 标准类别（受控字典）
  ├── source: str                     # "seed" | "review"
  ├── is_active: bool
  ├── created_at: str | None
  └── updated_at: str | None

ClassReviewItem
  ├── normalized_class: str           # 未命中字典的候选类别（归一化后）
  ├── proposed_by: str                # "llm" | "vector"
  ├── hit_count: int                  # 累计命中次数
  ├── status: str                     # "pending" | "approved" | "merged" | "rejected"
  ├── merged_to: str | None           # status=merged 时指向 canonical_class
  ├── first_seen_at: str | None
  ├── last_seen_at: str | None
  ├── reviewed_at: str | None
  └── review_note: str | None
```

## 5. 常量 (`constants.py`)

| 常量 | 值 | 说明 |
|------|-----|------|
| `SCORE_TIER_EPSILON` | `0.05` | 分差在此范围内视为同分段，按成本排序 |
| `MAX_EXPLORE_SLOTS` | `2` | Top-5 中非池探索模型的最大数量 |
| `MIN_CANDIDATES_FOR_AUTO` | `5` | 低于此数量需要告警 |
| `DEFAULT_DOMAIN_KEY` | `"__global__"` | demo 阶段默认模型 domain 固定值（按 class 生效） |
| `ENABLE_DOMAIN_DEFAULTS` | `false` | 是否启用 `(class, domain)` 默认模型（未来切换开关） |
| `DEFAULT_AGENT_CLASS` | `"general"` | agent_class 兜底类别（LLM 分类失败且向量匹配置信不足时使用） |
| `ENABLE_CLASS_SIM_FALLBACK` | `true` | 是否启用向量相似度 class 兜底匹配（LLM 分类缺失或未命中字典时触发；可作为紧急回退开关） |
| `CLASS_SIM_THRESHOLD` | `0.82` | 向量相似度命中阈值（top-1 必须 >= 阈值） |
| `CLASS_SIM_MARGIN` | `0.05` | top-1 与 top-2 最小差值（避免近似类误判） |
| `ENABLE_CONTROLLED_CLASS_DICT` | `true` | 是否启用受控 class 字典（防止 LLM 自由造类导致碎片化） |
| `CLASS_REVIEW_MIN_HITS` | `3` | 未命中字典类别累计命中达到 N 次后进入人工审核重点队列 |
| `CLASS_DICT_INITIAL_SET` | `("general","scrape","extraction","summarization","classification","rewrite","review","translation")` | 初始 canonical class 集合 |
| `CLASS_DESCRIPTIONS` | `dict[str, str]` | 每个类池的自然语言描述（中文），用于向量画像 embedding 匹配和 API 返回 |
| `CLASS_DIMENSION_PROFILES` | `dict[str, dict[str, int]]` | 每个类池的预定义维度分数（维度名→分数），向量画像命中后直接使用 |
| `RATE_LIMIT_FAIL_STRATEGY_DEFAULT` | `"degrade"` | `mode=auto` 下 Redis 不可用时策略：`degrade`（降级 InMemory）或 `fail_fast`（直接报错） |
| `SUCCESS_BONUS_STREAK` | `3` | 每连续成功 N 次（同 class）→ bonus_level + 1 |
| `SUCCESS_BONUS_FACTOR` | `1.20` | 奖励系数，raw_bonus = factor^bonus_level |
| `FAIL_PENALTY_STREAK` | `3` | 每连续失败 N 次（同 class）→ penalty_level + 1 |
| `FAIL_PENALTY_FACTOR` | `0.95` | 惩罚系数，effective_penalty = factor^penalty_level |
| `UNABLE_PROBE_INTERVAL_S` | `3600` | Unable 模型探测间隔（秒，1小时） |
| `PROBE_COOLDOWN_S` | `300` | 探测成功后冷启动降权窗口（秒，5分钟） |
| `PROBE_COOLDOWN_PENALTY` | `0.02` | 冷启动期间固定降权值（加法，比 DEGRADED_PENALTY 更轻） |
| `DEGRADED_WINDOW_S` | `300` | Degraded 自动恢复窗口（秒，5分钟内无新失败→恢复） |
| `DEGRADED_PENALTY` | `0.05` | Degraded 模型的固定降权值（加法，约半档 pool bonus） |
| `DEFAULT_PROMOTION_MIN_SUCCESS` | `20` | 默认模型候选最小成功次数（按 `class_model_stats.success_count`） |
| `DOWNGRADE_PROMOTION_MIN_SUCCESS` | `15` | 降阶候选晋升默认的最小成功次数（便宜候选放宽门槛） |
| `DOWNGRADE_SUCCESS_THRESHOLD` | `10` | 连续成功10次后尝试降阶 |
| `DOWNGRADE_SCORE_GAP_MAX` | `0.10` | 降阶候选与当前模型分差上限 |
| `DOWNGRADE_TRIAL_MIN_SAMPLES` | `5` | 降阶试用最小样本数（未达样本数不允许转正） |
| `DOWNGRADE_ROLLBACK_QUALITY_FAIL` | `2` | 降阶试用期质量失败达到 N 次立即回滚 |
| `DOWNGRADE_ROLLBACK_EXEC_FAIL` | `1` | 降阶试用期执行失败达到 N 次立即回滚 |
| `DOWNGRADE_CANARY_RATIO` | `0.50` | 降阶试用流量占比（50% 小流量） |
| `DOWNGRADE_COOLDOWN_H` | `24` | 降阶回滚后冷却时长（小时） |
| `DOWNGRADE_MIN_SAVINGS_RATIO` | `0.10` | 降阶最小预期节省比例（低于该值不发起试用） |
| `PRICE_CAP` | `10.0` | 成本归一化价格上限（$/1M tokens）。注意：`ModelMetadata.pricing` 单位是 `per_1k_tokens`，scorer 内部需先转换为 `per_1M_tokens`（× 1000）再与 PRICE_CAP 比较 |
| `COST_ALPHA` | `3.0` | 指数归一化陡峭系数，越大贵模型惩罚越重 |
| `REDIS_PREFIX_RPM` | `"route_agent:rpm:"` | Redis RPM key 前缀 |
| `REDIS_PREFIX_RPD` | `"route_agent:rpd:"` | Redis RPD key 前缀 |
| `REDIS_PREFIX_CONC` | `"route_agent:conc:"` | Redis 并发 key 前缀（后接 `normal:` 或 `esc:` + model_id） |
| `POOL_MAX_SIZE` | `10` | 每个 agent_class 池的最大模型数 |
| `POOL_BONUS_BASE_RATIO` | `0.06` | 池内模型基础加成比例（6%，刚进池） |
| `POOL_BONUS_FULL_RATIO` | `0.10` | 池内模型转正加成比例（10%，达到 MIN_TRIALS 后） |
| `POOL_ENTRY_CONF_LB_MIN` | `0.25` | 进池最低置信下界（Wilson lower bound，低吞吐 demo 放宽） |
| `POOL_MODEL_MAX_AGE_DAYS` | `180` | 模型发布超过此天数触发淘汰 |
| `POOL_AGE_EXEMPT_SUCCESS` | `10` | 老模型豁免：最低成功次数 |
| `POOL_AGE_EXEMPT_RATE` | `0.80` | 老模型豁免：最低成功率 |
| `MIN_TRIALS` | `10` | 池内转正门槛（达到后获得满额加成） |
| `EXEC_FAIL_RETRY` | `2` | 单次请求内执行失败重试次数（共 3 次尝试） |
| `EXEC_FAIL_UNAVAILABLE` | `1` | 单次请求 3 次尝试全失败 → 触发 degraded 状态机（available→degraded→unable） |
| `QUALITY_FAIL_REVOKE` | `3` | 连续效果失败 N 次 → 撤销默认 |
| `RPM_UTIL_LOW` | `0.70` | RPM 维度：开始概率跳过的利用率阈值 |
| `RPM_UTIL_HIGH` | `0.90` | RPM 维度：必定跳过的利用率阈值 |
| `CONC_UTIL_LOW` | `0.60` | 并发维度：开始概率跳过的利用率阈值（比 RPM 更敏感） |
| `CONC_UTIL_HIGH` | `0.85` | 并发维度：必定跳过的利用率阈值 |
| `DEFAULT_SKIP_POWER` | `2.0` | 跳过概率曲线指数（1.0=线性, 2.0=二次） |
| `FALLBACK_MIN_HEADROOM` | `0.05` | 加权分散时最低权重系数 |
| `BETA_PRIOR` | `2.0` | Beta 平滑先验 Beta(α,β), α=β=BETA_PRIOR |
| `WILSON_Z` | `1.645` | Wilson 置信下界 z 值（单侧 95%），用于默认模型仲裁 |
| `CHALLENGER_LEAD_STREAK` | `3` | Challenger 连续成功达到 N 次后，才允许替换默认（防抖） |
| `UTIL_CACHE_TTL_MS` | `150` | 利用率本地缓存 TTL（毫秒） |
| `RECENT_LIMITED_TTL_S` | `5.0` | 429 标记本地 TTL（秒） |
| `ESCALATION_CONC_RATIO` | `0.30` | 升阶流量最大并发占比 |
| `ESCALATION_UTIL_CEILING` | `0.95` | elevated 优先级的过载拦截阈值 |
| `ESCALATION_WAIT_BASE_DELAY` | `0.5` | v2 预留：升阶等待首次基础延迟（秒） |
| `ESCALATION_WAIT_MAX_ATTEMPTS` | `3` | v2 预留：升阶等待最大重试次数 |
| `ESCALATION_WAIT_MAX_TOTAL` | `7.0` | v2 预留：升阶等待最大总时间（秒） |
| `ESCALATION_MAX_WAITING` | `50` | v2 预留：全局最大同时等待升阶的请求数 |
| `CEILING_SLOTS` | `1` | Top-5 中为原始能力最强模型保留的槽位数（保证升阶天花板） |
| `MAX_SAME_PROVIDER_IN_CANDIDATES` | `3` | Top-N 中同一提供商最多占用的槽位数（防止单点故障；overflow 按原排序填充剩余槽位） |
| `COLD_START_INDEX_BY_COMPLEXITY` | `((expert,8,0),(hard,5,1),(medium,3,2),(easy,0,3))` | 冷启动复杂度阈值表 — `(标签, 最低max维度分, start_index)`；dimensions 为空时兜底 index=2 |
| `CONTEXT_LIMIT_BUFFER_RATIO` | `0.90` | 上下文长度安全缓冲系数：`estimated_input_tokens` 不得超过模型 `max_context_tokens` 的 90%，防止边界截断；`limits` 中无此字段或值为 `None`/`0` 时跳过过滤 |
| `EXPLORE_SLOTS_MIN` | `1` | 自适应探索槽下限：池充足（size≥5 且 avg_trials≥5）时使用，减少无效探索 |
| `EXPLORE_SLOTS_MAX` | `3` | 自适应探索槽上限：池稀少（size<3 或 avg_trials<5）时使用，加速新模型发现 |
| `EXPLORE_POOL_RICH_THRESHOLD` | `5` | 探索槽自适应：池成员数达到此值视为"池充足" |
| `EXPLORE_AVG_TRIALS_THRESHOLD` | `5.0` | 探索槽自适应：池内模型平均试用次数（success+fail）达到此值视为"数据充足" |
| `NEW_MODEL_LOOKBACK_DAYS` | `30` | 新模型判定窗口：`model_release_date` 在此天数内的非池模型可获得探索加成 |
| `NEW_MODEL_BONUS` | `0.04` | 新模型探索加成（加法，仅对非池模型生效）；低于 `POOL_BONUS_FULL_RATIO`（0.10），不与 pool bonus 叠加 |

### 5.1 时间语义约定（必须遵守）

- **墙钟时间（UTC）**：用于持久化与审计字段，如 `created_at` / `updated_at` / `event_ts` / `cooldown_until`，统一使用 UTC（SQLite `datetime('now')` 或应用层 UTC 时间）。
- **单调时钟（monotonic）**：仅用于进程内 TTL/超时/等待控制，如 `_limited_until`、本地缓存 TTL、等待超时。
- **禁止跨语义比较**：不得将 `time.monotonic()` 值与数据库墙钟时间戳直接比较；也不得用墙钟时间驱动进程内 TTL 判断。
- **event_ts 语义**：`event_ts` 作为审计记录字段，不作为主状态仲裁依据；幂等与互斥以 `feedback_events` 状态机约束为准。

## 6. 数据库变更

### 6.1 新数据库文件

路径: `data/router_engine.db`（独立于现有的 `task_analysis.db` 和 `route_agent_registry.sqlite3`）

### 6.2 新表 DDL

```sql
-- 模型实时统计：per (agent_class, model_id)，从首次使用开始追踪
-- 热路径：每次请求结果出来后原子 UPDATE ... RETURNING
CREATE TABLE IF NOT EXISTS class_model_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class         TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    success_count       INTEGER NOT NULL DEFAULT 0,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    exec_fail_count     INTEGER NOT NULL DEFAULT 0,
    consecutive_success INTEGER NOT NULL DEFAULT 0,
    consecutive_fail    INTEGER NOT NULL DEFAULT 0,
    success_rate        REAL NOT NULL DEFAULT 0.0,   -- 冗余字段，每次 UPDATE 时重算
    bonus_level         INTEGER NOT NULL DEFAULT 0,   -- 奖励等级，consecutive_success // 3
    penalty_level       INTEGER NOT NULL DEFAULT 0,   -- 惩罚等级，consecutive_fail // 3
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_class, model_id)
);

CREATE INDEX idx_class_model_stats_lookup
    ON class_model_stats(agent_class);

-- 部分索引：加速定期进池扫描（Wilson 置信下界需要 success/fail 样本）
CREATE INDEX idx_stats_pool_candidate
    ON class_model_stats(agent_class, success_count, fail_count);

-- 子池：已达标模型的子集，类似 MainModelPool 的 per-class 子池
-- 冷路径：定期批量扫描 class_model_stats 后入池/淘汰
CREATE TABLE IF NOT EXISTS class_pool (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class         TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    model_release_date  TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_class, model_id)
);

CREATE INDEX idx_class_pool_lookup
    ON class_pool(agent_class);

-- 默认模型表：结构按 (agent_class, domain) 设计，支持未来垂域分化
-- demo 阶段统一写 domain='__global__'，等价于按 agent_class 管理默认
CREATE TABLE IF NOT EXISTS class_pool_defaults (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class         TEXT NOT NULL,
    domain              TEXT NOT NULL,
    model_id            TEXT NOT NULL,
    is_locked           INTEGER NOT NULL DEFAULT 0,
    consecutive_success INTEGER NOT NULL DEFAULT 0,   -- 当前默认模型连续成功次数（demo 阶段 domain 固定 __global__）
    consecutive_fail    INTEGER NOT NULL DEFAULT 0,    -- 当前默认模型连续失败次数（demo 阶段 domain 固定 __global__）
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_class, domain)
);

-- 受控 class 字典：alias -> canonical 映射
-- 仅用于 class 解析治理，不参与默认模型 key（默认模型 key 仍是 agent_class + domain）
CREATE TABLE IF NOT EXISTS class_aliases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    alias_class         TEXT NOT NULL,   -- 归一化后别名
    canonical_class     TEXT NOT NULL,   -- 归一化后标准类别
    source              TEXT NOT NULL DEFAULT 'seed',  -- "seed" | "review"
    is_active           INTEGER NOT NULL DEFAULT 1,    -- 0/1
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(alias_class)
);

CREATE INDEX idx_class_aliases_canonical
    ON class_aliases(canonical_class);

-- 人工审核队列：记录未命中字典的新类别候选
CREATE TABLE IF NOT EXISTS class_review_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    normalized_class    TEXT NOT NULL,   -- 归一化候选类名
    proposed_by         TEXT NOT NULL,   -- "llm" | "vector"
    hit_count           INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'pending',  -- "pending" | "approved" | "merged" | "rejected"
    merged_to           TEXT,            -- status="merged" 时的 canonical_class
    first_seen_at       TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at        TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at         TEXT,
    review_note         TEXT,
    UNIQUE(normalized_class)
);

CREATE INDEX idx_class_review_pending
    ON class_review_queue(status, hit_count, last_seen_at);

-- 调用日志：每次请求都写，保留 agent_name 用于追踪和三级查询
-- 保留策略：demo 阶段默认不自动清理；需要时手动按时间清理
CREATE TABLE IF NOT EXISTS class_success_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    agent_name      TEXT NOT NULL,
    agent_class     TEXT NOT NULL,
    class_source    TEXT NOT NULL, -- "override" | "llm" | "vector" | "default"
    domain          TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    record_id       INTEGER,  -- 可选：analysis_records.id 关联
    outcome         TEXT NOT NULL DEFAULT 'success', -- success | quality_fail | exec_fail | downgrade_start | downgrade_promote | downgrade_rollback
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_success_log_class
    ON class_success_log(agent_class, domain);
CREATE INDEX idx_success_log_agent
    ON class_success_log(agent_name);
CREATE INDEX idx_success_log_model
    ON class_success_log(agent_class, model_id, created_at);
CREATE INDEX idx_success_log_request
    ON class_success_log(request_id);

-- 反馈事件幂等表：防止重复上报和乱序污染
-- 每个 (request_id, model_id, event_type) 只允许处理一次
-- event_type 遵循单请求状态机约束（见 §7.10）
CREATE TABLE IF NOT EXISTS feedback_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id      TEXT NOT NULL,
    model_id        TEXT NOT NULL,
    event_type      TEXT NOT NULL,  -- "exec_success" | "exec_fail" | "quality_good" | "quality_poor"
    event_ts        TEXT NOT NULL,  -- 调用方传入的事件发生时间（ISO 8601）
    processed_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(request_id, model_id, event_type)
);

CREATE INDEX idx_feedback_events_lookup
    ON feedback_events(request_id, model_id);

-- 降阶试用状态：per (agent_class, domain) 追踪 challenger 小流量试用与回滚
CREATE TABLE IF NOT EXISTS downgrade_trials (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_class             TEXT NOT NULL,
    domain                  TEXT NOT NULL,
    incumbent_model_id      TEXT NOT NULL,
    challenger_model_id     TEXT NOT NULL,
    state                   TEXT NOT NULL DEFAULT 'active',  -- "active" | "promoted" | "rolled_back"
    sampled_requests        INTEGER NOT NULL DEFAULT 0,
    quality_fail_count      INTEGER NOT NULL DEFAULT 0,
    exec_fail_count         INTEGER NOT NULL DEFAULT 0,
    success_count           INTEGER NOT NULL DEFAULT 0,
    canary_ratio            REAL NOT NULL DEFAULT 0.50,
    expected_savings_ratio  REAL NOT NULL DEFAULT 0.0,
    started_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at                TEXT,
    cooldown_until          TEXT
);

-- 同一 (agent_class, domain) 同时只允许一个 active 试用
CREATE UNIQUE INDEX idx_downgrade_trials_active
    ON downgrade_trials(agent_class, domain)
 WHERE state = 'active';

CREATE INDEX idx_downgrade_trials_cooldown
    ON downgrade_trials(agent_class, domain, challenger_model_id, cooldown_until);

-- 模型可用性：per model_id（全局，非 per agent_class）
-- 关注模型是否可被调用（API 超时/限流/500 等部署层面问题）
-- 连续成功奖励（bonus_level）和连续失败惩罚（penalty_level）在 class_model_stats 中按 (agent_class, model_id) 双轨追踪
CREATE TABLE IF NOT EXISTS model_availability (
    model_id            TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'available',  -- "available" | "degraded" | "unable"
    degraded_since      TEXT,                               -- 标记 degraded 的时间
    unable_since        TEXT,                               -- 标记 unable 的时间
    last_probe_at       TEXT,                               -- 最近一次探测时间
    last_probe_success  INTEGER,                            -- 0/1
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### 6.3 现有表修改

**无 DDL 修改，无新增查询方法。**

健康状态改为事件驱动（连续成功触发奖励，执行失败触发 Unable），不再需要定期聚合 `analysis_records`。

## 7. 各模块详细设计

### 7.1 `scorer.py` — 维度匹配 + 成本打分

**函数 1: `compute_dimension_score(relevant_dimensions, model_capabilities) -> float`**

- 输入: `relevant_dimensions: tuple[DimensionScore, ...]`, `model_capabilities: dict[str, Any]`
- 逻辑:
  1. 遍历每个 `DimensionScore`，以 `dim.score`（1-10）为权重
  2. 从 `model_capabilities` 取对应维度值；`None` 时用中性分 `50`（0-100 量级）
  3. 跳过 `_source` 等元信息字段
  4. 归一化到 [0, 1]: `cap_value / 100`（Arena 评分已在 0-100 范围）
  5. 加权平均: `sum(weight * normalized_value) / sum(weight)`
- 输出: `float` in [0.0, 1.0]
- 边界: `relevant_dimensions` 为空时返回 `0.0`
- 注意: capabilities 值来源于 Arena 排行榜（rank+ELO 混合归一化），`_source` 字段记录数据来源

**函数 2: `compute_effective_price_per_1m(pricing, relevant_dimensions) -> float`**

- 输入: `pricing: dict[str, Any]`（来自 `ModelMetadata.pricing`），`relevant_dimensions: tuple[DimensionScore, ...]`
- 逻辑:
  1. 估算 output 权重（基于任务维度动态调整）:
     ```
     OUTPUT_HEAVY_DIMS = {"coding", "creative_writing"}
     output_signal = sum(dim.score for dim in relevant_dimensions
                         if dim.dimension in OUTPUT_HEAVY_DIMS) / 10.0
     w_out = 0.3 + 0.4 * min(output_signal, 1.0)
     w_in = 1.0 - w_out
     ```
     - 搜索/分类类任务: w_out ≈ 0.3（output 短，input 主导）
     - 代码生成/写作类任务: w_out ≈ 0.5-0.7（output 长，output 成本主导）
  2. 计算加权有效价格并统一到每百万 token:
     ```
     input_price = pricing["input"] or PRICE_UNAVAILABLE_SENTINEL
     output_price = pricing["output"] or PRICE_UNAVAILABLE_SENTINEL
     effective_price = w_in * input_price + w_out * output_price
     # pricing 单位是 $/1k tokens，转换为 $/1M tokens
     effective_price_per_1m = effective_price * 1000
     ```
- 输出: `effective_price_per_1m: float`（USD/1M tokens）
- 用途: 作为唯一价格真源，同时用于
  - `compute_cost_score`（成本归一化打分）
  - `constraints.max_cost` 预算过滤

**函数 3: `compute_cost_score(pricing, relevant_dimensions) -> float`**

- 输入: `pricing: dict[str, Any]`（来自 `ModelMetadata.pricing`），`relevant_dimensions: tuple[DimensionScore, ...]`
- 逻辑:
  1. 先调用统一价格函数:
     ```
     effective_price_per_1m = compute_effective_price_per_1m(pricing, relevant_dimensions)
     ```
  2. 指数归一化（非线性，贵模型区分度更高）:
     ```
     ratio = min(effective_price_per_1m / PRICE_CAP, 1.0)
     cost_score = (e^(COST_ALPHA * ratio) - 1) / (e^COST_ALPHA - 1)
     ```
  3. 当 `ratio >= 1.0` 时直接返回 `1.0`
- 输出: `float` in [0.0, 1.0]（越低越便宜）
- 设计理由: 线性归一化对贵模型区分度不足。指数函数使便宜模型聚拢，
  贵模型拉开，让成本惩罚随价格加速增长。
  `COST_ALPHA` 控制曲线陡峭度：α=3 为默认值，增大则惩罚更激进。
  动态 output 权重使成本评估适应不同任务类型，避免 output 密集型任务低估成本。

  | effective_price/1M | 线性 | 指数(α=3) |
  |--------------------|------|-----------|
  | $0.10              | 0.01 | 0.003     |
  | $1.00              | 0.10 | 0.041     |
  | $3.00              | 0.30 | 0.163     |
  | $5.00              | 0.50 | 0.358     |
  | $8.00              | 0.80 | 0.699     |
  | $10.0              | 1.00 | 1.000     |

### 7.2 `storage/` — RouterStorage

**类: `RouterStorage`**

- `__init__(db_path: Path | None = None)` — 默认 `data/router_engine.db`
- `_init_db_sync()` — 建表
- 提供 async + sync 双接口（与 `AnalysisStorage` 风格一致）
- 所有写操作使用原子 SQL（`SET col = col + 1`），避免并发竞态

**model_availability 操作:**
- `get_availability(model_id) -> ModelAvailability | None`
- `mark_unable(model_id)` — 设置 status='unable', unable_since=now
- `mark_available(model_id)` — 设置 status='available', unable_since=NULL
- `list_unable_for_probe(interval_s) -> list[str]` — 查询需要探测的 unable 模型
- `update_probe_result(model_id, success: bool)` — 更新探测结果

**class_model_stats 操作:**
- `get_stats(agent_class, model_id) -> ClassModelStats | None` — 只读查询，供 `get_health_modifier` 使用
- `ensure_stats_row(agent_class, model_id)` — `INSERT OR IGNORE` 初始化行
- `atomic_increment_success(agent_class, model_id)` — 原子 `SET success_count = success_count + 1`
- `atomic_increment_fail(agent_class, model_id)` — 原子 `SET fail_count = fail_count + 1`
- `atomic_increment_exec_fail(agent_class, model_id)` — 原子 `SET exec_fail_count = exec_fail_count + 1`
- 注意：bonus_level / penalty_level 的写入仅通过 `on_quality_good_async` / `on_quality_fail_async` 的原子 SQL 完成，
  不单独暴露 `update_bonus_level` / `update_penalty_level` 接口，避免非原子误用

**class_pool 操作:**
- `get_pool_entries(agent_class) -> list[ClassPoolEntry]` — JOIN `class_model_stats` 填充 success_count/fail_count/success_rate
- `upsert_pool_entry(agent_class, model_id, **stats)`
- `delete_pool_entry(agent_class, model_id)`
- `count_pool(agent_class) -> int`

**class_pool_defaults 操作:**
- `get_default(agent_class, domain) -> ClassPoolDefault | None`
- `upsert_default(agent_class, domain, model_id, consecutive_success, is_locked)`
- `atomic_increment_consecutive_success(agent_class, domain) -> int` — 返回新值
- `atomic_increment_consecutive_fail(agent_class, domain) -> int` — 返回新值
- `reset_consecutive(agent_class, domain)` — 重置连续计数
- `clear_default(agent_class, domain)` — 清除默认状态
- 说明：demo 阶段所有操作内部统一写 `domain=DEFAULT_DOMAIN_KEY("__global__")`

**class_aliases / class_review_queue 操作:**
- `resolve_canonical_class(raw_class) -> str | None`
  — `normalize(raw_class)` 后先查 `class_aliases.alias_class`，命中返回 `canonical_class`
- `upsert_class_alias(alias_class, canonical_class, source="review") -> None`
  — 人工审核通过后写入映射；后续同 alias 自动归并
- `upsert_class_review_candidate(normalized_class, proposed_by) -> None`
  — 未命中字典时写入/累加：`hit_count += 1, last_seen_at = now`
- `list_pending_class_reviews(limit=100, min_hits=CLASS_REVIEW_MIN_HITS) -> list[dict]`
  — 供人工审核按热度处理
- `apply_class_review(review_id, action, canonical_class=None, review_note=None) -> None`
  — `action in ("approved","merged","rejected")`；`merged` 时同步写 alias 映射
- `cleanup_old_class_reviews(retention_days=90) -> int`
  — 仅清理 `status != "pending"` 且 `reviewed_at` 早于阈值的记录，避免误删待办

**class_success_log 操作:**
- `log_outcome(request_id, agent_name, agent_class, class_source, domain, model_id, record_id, outcome)`（支持 downgrade_start / downgrade_promote / downgrade_rollback）
- `query_by_class(agent_class, domain, limit) -> list[dict]`
- `query_by_class_cross_domain(agent_class, limit) -> list[dict]`
- `query_all(limit) -> list[dict]`
- `cleanup_old_logs(retention_days: int | None = None) -> int` — 手动维护接口；默认 `None` 不清理（便于持续本地保留成功案例）。仅当显式传入 `retention_days > 0` 时，删除 `created_at < datetime('now', '-{retention_days} days')` 的记录并返回删除行数。

**downgrade_trials 操作:**
- `get_active_downgrade_trial(agent_class, domain) -> dict | None`
- `is_downgrade_in_cooldown(agent_class, domain, challenger_model_id) -> bool`
- `start_downgrade_trial(agent_class, domain, incumbent_model_id, challenger_model_id, expected_savings_ratio, canary_ratio=DOWNGRADE_CANARY_RATIO) -> bool`
  — `INSERT` active trial；若已有 active trial（UNIQUE 索引冲突）返回 False
- `record_downgrade_trial_observation(agent_class, domain, model_id, outcome_type) -> dict | None`
  — 仅当 `model_id == challenger_model_id` 才计入 sampled_requests/quality_fail_count/exec_fail_count/success_count
- `finish_downgrade_trial(agent_class, domain, result: str, cooldown_h: int = DOWNGRADE_COOLDOWN_H) -> None`
  — `result in ("promoted", "rolled_back")`；rolled_back 时写 `cooldown_until`
- `cleanup_old_downgrade_trials(retention_days: int = 30) -> int` — 手动维护接口（demo 默认不自动调用）

**feedback_events 操作:**
- `try_record_event(request_id, model_id, event_type, event_ts) -> bool`
  — `INSERT OR IGNORE`，返回 True 表示首次写入（应处理），False 表示重复（应跳过）
- `get_events(request_id, model_id) -> list[str]`
  — 返回该 (request_id, model_id) 已记录的 event_type 列表，用于状态机校验
- `cleanup_old_events(retention_days: int = 7) -> int`
  — 手动维护接口；清理过期事件（比 log 保留期短，仅需覆盖重试窗口，demo 默认不自动调用）

### 7.3 `health.py` — HealthManager（事件驱动状态机）

健康状态分为两个正交维度，分别追踪：

| 维度 | 粒度 | 状态 | 存储 |
|------|------|------|------|
| 奖励（Bonus） | per (agent_class, model_id) | 无奖励 / 有奖励 | `class_model_stats.bonus_level` |
| 惩罚（Penalty） | per (agent_class, model_id) | 无惩罚 / 有惩罚 | `class_model_stats.penalty_level` |
| 可用性（Unable） | per model_id（全局） | available / degraded / unable | `model_availability` 表 |

**类: `HealthManager`**

- `__init__(router_storage: RouterStorage)`
- 不再依赖 `AnalysisStorage`，不再做定期聚合

#### 7.3.1 连续成功奖励 + 连续失败惩罚（成本反比加权）

**奖励公式：**

```
bonus_level = consecutive_success // SUCCESS_BONUS_STREAK    # // 3
raw_bonus = SUCCESS_BONUS_FACTOR ^ bonus_level               # 1.2^level
effective_multiplier = 1.0 + (raw_bonus - 1.0) * (1.0 - cost_score)
dimension_score *= effective_multiplier
```

- `cost_score` 来自 `compute_cost_score()`，范围 [0, 1]（0=最便宜，1=最贵）
- 便宜模型拿到接近满额的 1.2^level 奖励
- 贵模型奖励被大幅削减，但 effective_multiplier 永远 >= 1.0（不惩罚）
- **升阶例外：** 升阶场景下跳过成本衰减，直接使用 `raw_bonus`（见下方说明）
- 不设上限，允许 dimension_score 超过 1.0

**升阶场景不衰减的原因：**

升阶的目的是用更强（通常更贵）的模型来弥补当前模型的不足。如果对升阶目标仍然按成本衰减奖励，
贵模型的 effective_multiplier 会被大幅压缩（如 cost_score=0.699 时 bonus_level=3 仅 1.219），
与原模型的分差很小，导致升阶后效果改善不明显。因此升阶时使用 `raw_bonus = 1.2^level`，
让升阶目标获得与其历史表现匹配的完整奖励。

```
# 正常选择（成本反比加权）
effective_bonus = 1.0 + (raw_bonus - 1.0) * (1.0 - cost_score)

# 升阶选择（跳过成本衰减）
effective_bonus = raw_bonus    # 直接使用 1.2^level
```

**惩罚公式（新增）：**

```
penalty_level = consecutive_fail // FAIL_PENALTY_STREAK    # // 3
effective_penalty = FAIL_PENALTY_FACTOR ^ penalty_level     # 0.95^level
dimension_score *= effective_penalty
```

- 统一惩罚，不区分成本（与奖励的成本反比加权不同）
- `consecutive_success` 和 `consecutive_fail` 互斥重置，同一时刻模型只会处于奖励或惩罚其中一种状态
- 惩罚力度温和（0.95^level），避免模型被过快淘汰
- **奖励衰减容忍机制：** 第一次 poor 只重置 `consecutive_success`，不动 `bonus_level`（容忍偶发误判）；
  第二次连续 poor 才触发 `bonus_level = bonus_level // 2`（衰减而非归零）；
  连续 poor 持续衰减直至归零后进入惩罚状态。这使得 5-10% 的质量评估误判率不会频繁打断奖励积累。

**应用逻辑（互斥，不会同时生效）：**

```
if bonus_level > 0:
    dimension_score *= effective_bonus      # 奖励加权
elif penalty_level > 0:
    dimension_score *= effective_penalty    # 惩罚降权
```

**状态转换（四态，含容忍）：**

```
                    consecutive_success 达到 3N
        ┌──────────────┐ ──────────────────────→ ┌───────────┐
        │ 无修正       │                          │ 有奖励    │
        │ (B=0, P=0)   │                          │ (B>0)     │
        └──────────────┘ ←────────────────────── └───────────┘
              │ ▲          bonus 衰减至 0               │ 每 +3 次成功
              │ │                                       │ bonus_level++
              │ │                                       │ 奖励加深
              │ │                                       │
              │ │                                       ▼
              │ │                                 ┌───────────┐
              │ │                                 │ 容忍      │ ← 第 1 次 poor
              │ │                                 │ (B>0,F=1) │   consec_success=0
              │ │                                 └───────────┘   bonus_level 不变
              │ │                                       │
              │ │ 任意一次效果成功                        │ 第 2+ 次连续 poor
              │ │ → consec_fail=0, penalty_level=0      │ bonus_level //= 2
              │ │                                       │ 衰减至 0 后进入惩罚
              ▼ │                                       ▼
        ┌──────────────┐
        │ 有惩罚       │ ← consecutive_fail 达到 3N 且 bonus_level 已为 0
        │ (P>0)        │   每 +3 次失败 penalty_level++
        └──────────────┘
```

**触发条件：** `consecutive_success` 达到 3 的倍数（3, 6, 9...），同一个 `agent_class` 内。
**奖励衰减（非归零）：** 第一次 poor 重置 `consecutive_success = 0` 但保持 `bonus_level` 不变（容忍偶发误判）；
第二次连续 poor 触发 `bonus_level = bonus_level // 2`；持续 poor 继续衰减直至归零。
**成功时 bonus 只升不降：** `bonus_level = MAX(bonus_level, (consecutive_success + 1) / 3)`，
避免 consecutive_success 重新计数时把保留的 bonus_level 拉低。

**方法 1: `on_quality_good_async(agent_class, model_id) -> tuple[int, int]`**

```
1. 确保行存在（首次使用时初始化）:
   INSERT OR IGNORE INTO class_model_stats (agent_class, model_id)
   VALUES (?, ?);

2. 原子更新 class_model_stats:
   UPDATE ... SET success_count = success_count + 1,
                  consecutive_success = consecutive_success + 1,
                  consecutive_fail = 0,
                  success_rate = CAST(success_count + 1 AS REAL) / MAX(success_count + 1 + fail_count, 1),
                  bonus_level = MAX(bonus_level, (consecutive_success + 1) / 3),  -- 只升不降，保护容忍期保留的 bonus
                  penalty_level = 0,                            -- 重置惩罚
                  updated_at = datetime('now')
   WHERE agent_class = ? AND model_id = ?
   RETURNING consecutive_success, bonus_level;

3. 返回 `(consecutive_success, bonus_level)`
```

**方法 2: `on_quality_fail_async(agent_class, model_id) -> tuple[int, int, int]`**

```
1. 确保行存在（首次使用时初始化）:
   INSERT OR IGNORE INTO class_model_stats (agent_class, model_id)
   VALUES (?, ?);

2. 原子更新（含容忍机制）:
UPDATE class_model_stats
   SET fail_count = fail_count + 1,
       consecutive_fail = consecutive_fail + 1,
       consecutive_success = 0,                          -- 重置连续成功
       bonus_level = CASE
                       WHEN consecutive_fail >= 1        -- 更新前已有 >=1 次失败（这是第 2+ 次连续 poor）
                       THEN bonus_level / 2              -- 衰减（SQLite 整数除法向下取整）
                       ELSE bonus_level                  -- 第 1 次 poor：容忍，不动 bonus
                     END,
       penalty_level = CASE
                         WHEN consecutive_fail >= 1      -- 第 2+ 次连续 poor
                          AND bonus_level / 2 = 0        -- 且 bonus 衰减后已归零（才进入惩罚）
                         THEN (consecutive_fail + 1) / 3
                         ELSE 0                          -- bonus 未归零或第 1 次 poor：不进入惩罚
                       END,
       success_rate = CAST(success_count AS REAL) / MAX(success_count + fail_count + 1, 1),
       updated_at = datetime('now')
 WHERE agent_class = ? AND model_id = ?
 RETURNING consecutive_fail, bonus_level, penalty_level;
```

返回 `(consecutive_fail, bonus_level, penalty_level)`。

**方法 3: `get_health_modifier(agent_class, model_id, cost_score, is_escalation=False) -> tuple[str, float]`**

```
读取 class_model_stats 的 bonus_level 和 penalty_level:
  if bonus_level > 0:
      raw_bonus = 1.2 ^ bonus_level
      if is_escalation:
          effective = raw_bonus                                    # 升阶：跳过成本衰减
      else:
          effective = 1.0 + (raw_bonus - 1.0) * (1.0 - cost_score)  # 正常：成本反比加权
      return ("bonus", effective)
  elif penalty_level > 0:
      effective = 0.95 ^ penalty_level
      return ("penalty", effective)
  else:
      return ("healthy", 1.0)
```

返回 `(status, multiplier)`，调用方直接 `dimension_score *= multiplier`。
- `multiplier > 1.0` 表示奖励加权
- `multiplier < 1.0` 表示惩罚降权
- `multiplier == 1.0` 表示无修正
- `is_escalation=True` 时奖励不受成本衰减，升阶目标获得完整 `1.2^level` 奖励

**奖励力度示例（便宜模型 cost_score=0.003）：**

| consecutive_success | bonus_level | effective_multiplier | 效果 |
|---|---|---|---|
| 0-2 | 0 | 1.000 | 无奖励 |
| 3-5 | 1 | 1.199 | 轻微加权 |
| 6-8 | 2 | 1.438 | 明显加权 |
| 9-11 | 3 | 1.726 | 大幅加权 |

**奖励力度示例（贵模型 cost_score=0.699）：**

| consecutive_success | bonus_level | effective_multiplier | 效果 |
|---|---|---|---|
| 0-2 | 0 | 1.000 | 无奖励 |
| 3-5 | 1 | 1.060 | 微弱加权 |
| 6-8 | 2 | 1.132 | 轻微加权 |
| 9-11 | 3 | 1.219 | 有限加权 |

**奖励力度示例（升阶场景 is_escalation=True，贵模型 cost_score=0.699）：**

| consecutive_success | bonus_level | effective_multiplier | 效果 |
|---|---|---|---|
| 0-2 | 0 | 1.000 | 无奖励 |
| 3-5 | 1 | 1.200 | 满额加权（vs 正常 1.060） |
| 6-8 | 2 | 1.440 | 满额加权（vs 正常 1.132） |
| 9-11 | 3 | 1.728 | 满额加权（vs 正常 1.219） |

**惩罚力度示例（统一，不区分成本）：**

| consecutive_fail | penalty_level | effective_penalty | 效果 |
|---|---|---|---|
| 0-2 | 0 | 1.000 | 无惩罚 |
| 3-5 | 1 | 0.950 | 轻微降权 |
| 6-8 | 2 | 0.903 | 明显降权 |
| 9-11 | 3 | 0.857 | 大幅降权 |
| 12-14 | 4 | 0.815 | 严重降权 |

#### 7.3.2 可用性状态机（三态：Available → Degraded → Unable）

**状态转换：**

```
        ┌───────────┐   单次请求 3 次尝试全失败    ┌──────────┐   degraded 期间任意    ┌────────┐
        │ Available │ ──────────────────────────→ │ Degraded │ ─ 1 次执行失败 ──────→ │ Unable │
        └───────────┘                             └──────────┘                        └────────┘
              ▲                                       │ ▲                                  │
              │ 5 分钟内无新失败（自动恢复）              │ │                                  │
              └───────────────────────────────────────┘ │                                  │
              │                                         │ 探测成功                           │
              └─────────────────────────────────────────┴──────────────────────────────────┘
                                                每小时探测一次
```

**degraded 状态行为：**
- 模型仍可被选中，但在 selector 中受固定降权: `dimension_score -= DEGRADED_PENALTY`（0.05）
- 降权是加法（非乘法），语义为"排名往后挪一点"，不受 dimension_score 分布影响
- degraded 期间任意一次请求执行失败（不要求 3 连败）即确认故障 → unable
- 5 分钟内无新执行失败 → 自动恢复为 available（degraded_since 超过 DEGRADED_WINDOW_S）

**方法 4: `report_exec_failure_async(model_id) -> None`**

（原名 `mark_degraded_async`，改名以准确反映其三态状态机行为：available→degraded→unable）

```
1. INSERT INTO model_availability (model_id, status, degraded_since, updated_at)
   VALUES (?, 'degraded', datetime('now'), datetime('now'))
   ON CONFLICT(model_id) DO UPDATE
      SET status = CASE
            WHEN status = 'available' THEN 'degraded'   -- available → degraded
            WHEN status = 'degraded' THEN 'unable'      -- degraded 再失败 → unable
            ELSE status                                   -- unable 不变
          END,
          degraded_since = CASE
            WHEN status = 'available' THEN datetime('now')
            ELSE degraded_since
          END,
          unable_since = CASE
            WHEN status = 'degraded' THEN datetime('now')
            ELSE unable_since
          END,
          updated_at = datetime('now');

2. 若新 status == 'unable':
   DELETE FROM class_pool WHERE model_id = ?;
   生成用户告警: "模型 {model_id} 不可用（degraded 期间再次失败），已从池中移除"
```

**方法 5: `is_available(model_id) -> tuple[bool, bool, bool]`**

```
SELECT status, degraded_since, last_probe_at, last_probe_success
  FROM model_availability WHERE model_id = ?;
-- 无记录 → (True, False, False)  即 (available, not_degraded, not_cooldown)
-- status == 'available':
--   若 last_probe_success=1 且 last_probe_at > now - PROBE_COOLDOWN_S → (True, False, True)  冷启动降权
--   否则 → (True, False, False)
-- status == 'degraded':
--   若 degraded_since < now - DEGRADED_WINDOW_S → 自动恢复，UPDATE status='available'，返回 (True, False, False)
--   否则 → (True, True, False)  即 (可选中, 但需降权)
-- status == 'unable' → (False, False, False)
```

返回 `(selectable, is_degraded, is_probe_cooldown)`，selector 据此决定降权类型。

**方法 6: `probe_unable_models_async() -> list[str]`**

定期任务（每小时执行一次），探测所有 unable 模型：

```
1. SELECT model_id FROM model_availability
   WHERE status = 'unable'
     AND (last_probe_at IS NULL
          OR last_probe_at < datetime('now', '-1 hour'));

2. 对每个模型发送两段式探测请求（低成本但比单 token 更接近真实路径）:
   - system: "你是健康检查。只输出 {\"ok\":true}。"
   - user: ~200 token 填充文本 + "请输出 JSON"
   - max_tokens: 10
   - 验证: 能处理上下文 + 能输出结构化格式 + 不触发内容过滤
   - 成本: 约 200 input + 10 output tokens，每小时每模型 < $0.001

3. 探测成功:
   UPDATE model_availability
      SET status = 'available',
          degraded_since = NULL,
          unable_since = NULL,
          last_probe_at = datetime('now'),
          last_probe_success = 1,
          updated_at = datetime('now')
    WHERE model_id = ?;
   → 模型恢复可用，下次路由时可被选中
   → 不自动重新加入 class_pool（需重新积累成功次数达标后进池）
   → 冷启动降权: PROBE_COOLDOWN_S（5分钟）内 selector 对该模型降权 PROBE_COOLDOWN_PENALTY（0.02）
     判定依据: last_probe_at 非空 且 last_probe_success=1 且 last_probe_at > now - PROBE_COOLDOWN_S
     降权比 DEGRADED_PENALTY（0.05）更轻，语义为"刚恢复，先少接点流量"

4. 探测失败:
   UPDATE model_availability
      SET last_probe_at = datetime('now'),
          last_probe_success = 0,
          updated_at = datetime('now')
    WHERE model_id = ?;
   → 保持 unable，下次探测再试
```

**探测调度：** 由 `RouterEngine` 在初始化时启动后台 `asyncio.Task`，使用 `asyncio.sleep(UNABLE_PROBE_INTERVAL_S)` 循环。服务关闭时 cancel。

#### 7.3.3 与 Stampede 的关系

旧设计需要定期聚合 `analysis_records` 来计算 `poor_rate` → 缓存过期时产生 stampede。

新设计是事件驱动的：
- 奖励状态由 `on_quality_good_async` 在反馈事件到达时原子更新，不需要定期聚合
- 惩罚状态由 `on_quality_fail_async` 在反馈事件到达时原子更新，不需要定期聚合
- 奖励/惩罚查询（`get_health_modifier`）是单行读 + 简单计算，O(1) 操作
- Unable 状态由执行失败事件触发，探测是低频后台任务（每小时）

**Health Cache Stampede 在新设计下不存在。**

### 7.4 `defaults.py` — DefaultsStore（内部模块，由 ClassPoolManager 封装）

**不作为公共 API 导出。** 外部通过 `ClassPoolManager.get_default()` / `record_outcome()` / `set_user_override()` 访问。

**类: `DefaultsStore`**

- `__init__(router_storage: RouterStorage)`

**方法:**

1. `lookup_default_async(agent_class, domain) -> ClassPoolDefault | None`
   - demo 阶段忽略传入 domain，统一查询 `domain=DEFAULT_DOMAIN_KEY("__global__")`
   - 从 `class_pool_defaults` 表查询

2. `record_success_async(agent_class, domain, model_id) -> ClassPoolDefault | None`
   - demo 阶段忽略传入 domain，统一写 `domain=DEFAULT_DOMAIN_KEY`
   - 若当前默认模型 == model_id: `atomic_increment_consecutive_success`, `consecutive_fail = 0`
   - 若当前默认模型 != model_id: **不修改默认位计数**（避免非默认模型误伤默认撤销计数）
   - 返回值仅表示“默认位计数是否更新”，不负责晋升仲裁

3. `record_fail_async(agent_class, domain, model_id) -> bool`
   - demo 阶段忽略传入 domain，统一写 `domain=DEFAULT_DOMAIN_KEY`
   - 仅当 `model_id == 当前默认模型` 时才 `atomic_increment_consecutive_fail`, `consecutive_success = 0`
   - 若 `consecutive_fail >= QUALITY_FAIL_REVOKE`（3）: `clear_default` → 返回 True（已撤销）
   - 若 `model_id != 当前默认模型`: no-op，返回 False
   - 否则返回 False

4. `evaluate_and_promote_default_async(agent_class, domain, min_success: int = DEFAULT_PROMOTION_MIN_SUCCESS) -> ClassPoolDefault | None`
   - 作用：执行默认模型仲裁（非锁定时）
   - demo 阶段仲裁范围为 `agent_class`（domain 固定 `__global__`）；未来可切到 `(agent_class, domain)`
   - 候选过滤：
     - `success_count >= min_success`（普通晋升默认 20；降阶晋升可传 15）
     - `model_availability.status != 'unable'`
   - 评分：对每个候选计算 Wilson 下界（`z = WILSON_Z`）：
     ```
     n = success_count + fail_count
     p = success_count / n
     wlb = (p + z^2/(2n) - z*sqrt((p*(1-p)+z^2/(4n))/n)) / (1 + z^2/n)
     ```
   - 排序：`wlb` 降序；若 `abs(wlb_a - wlb_b) < SCORE_TIER_EPSILON` 视为同分，
     再按价格更低优先，再按 `model_release_date` 更新优先
   - 价格和发布时间由 `ClassPoolManager` 提供的 model metadata resolver 注入（DefaultsStore 不直接依赖 model_registry）
   - 切换规则：
     - 无默认：选择第一名作为默认
     - 有默认：challenger 必须满足
       - `challenger.consecutive_success >= CHALLENGER_LEAD_STREAK`（3）
       - `wlb(challenger) > wlb(incumbent)`
     - 满足后才写 `class_pool_defaults`，否则保持现状

5. `set_user_override_async(agent_class, domain, model_id)`
   - demo 阶段忽略传入 domain，统一写 `domain=DEFAULT_DOMAIN_KEY`
   - `upsert_default(..., is_locked=True)`

### 7.5 `rate_limiters/` — Redis 限流

**依赖:** `redis[hiredis]`（需添加到 `requirements.txt`）

**接口: `RateLimiter` (Protocol)**
```
is_rate_limited_async(model_id: str, limits: dict) -> bool
record_request_start_async(model_id: str, traffic_type: str = "normal") -> None
record_request_end_async(model_id: str, traffic_type: str = "normal") -> None
get_utilization_async(model_id: str, limits: dict) -> ModelUtilization
is_escalation_capped_async(model_id: str, limits: dict) -> bool
mark_limited(model_id: str) -> None
is_recently_limited(model_id: str) -> bool
```

`traffic_type` 取值: `"normal"` | `"escalation"`，用于并发计数器隔离。

**类 1: `RedisRateLimiter`**

- `__init__(redis_url: str)`
- 使用 `redis.asyncio.Redis`
- 进程内状态（无共享写）:
  - `_util_cache: dict[str, tuple[float, ModelUtilization]]` — 利用率缓存，TTL = `UTIL_CACHE_TTL_MS`
  - `_util_cache_lock: asyncio.Lock` — 缓存读写锁（注意：必须用 asyncio.Lock 而非 threading.Lock，避免阻塞事件循环）
  - `_limited_until: dict[str, float]` — 429 标记，value = `time.monotonic()` 过期时间

**Redis key 设计:**
- RPM: sorted set `route_agent:rpm:{model_id}`，score=timestamp，60s 窗口
  - `ZREMRANGEBYSCORE` 清理过期 → `ZCARD` 计数 → 与 `limits["max_requests_per_minute"]` 比较
- RPD: sorted set `route_agent:rpd:{model_id}`，score=timestamp，86400s 窗口
  - 同上，与 `limits["max_requests_per_day"]` 比较
- 并发（按流量类型隔离）:
  - `route_agent:conc:normal:{model_id}` — 正常流量计数器
  - `route_agent:conc:esc:{model_id}` — 升阶流量计数器
  - 总并发 = normal + escalation
  - `record_request_start(traffic_type)` → INCR 对应 key + EXPIRE 300s（防泄漏）
  - `record_request_end(traffic_type)` → DECR 对应 key

**`get_utilization_async` 实现:**
1. 检查 `_util_cache`，未过期直接返回（降 Redis 热点，TTL 150ms）
2. 过期 → Redis pipeline 批量读（一次 RTT）:
   - `ZREMRANGEBYSCORE` + `ZCARD`（rpm）
   - `GET route_agent:conc:normal:{model_id}`
   - `GET route_agent:conc:esc:{model_id}`
3. 计算 `rpm_ratio`, `conc_ratio`, `normal_conc_ratio`, `escalation_conc_ratio`
4. `escalation_capped = esc_conc >= int(max_conc * ESCALATION_CONC_RATIO)`
5. `is_limited = self.is_recently_limited(model_id)`
6. 写入 `_util_cache`

**`is_escalation_capped_async` 实现:**
- `escalation_cap = int(max_conc * ESCALATION_CONC_RATIO)`
- 读 `route_agent:conc:esc:{model_id}` → 与 cap 比较
- 可选严格模式（Lua 原子化 check-and-increment）:
  ```lua
  local current = tonumber(redis.call('GET', KEYS[1]) or '0')
  if current >= tonumber(ARGV[1]) then return 0 end
  redis.call('INCR', KEYS[1])
  redis.call('EXPIRE', KEYS[1], 300)
  return 1
  ```
  v1 先用非原子版本，观察超限情况再决定是否升级。

**`mark_limited` / `is_recently_limited` 实现:**
- 进程内 `_limited_until[model_id] = time.monotonic() + RECENT_LIMITED_TTL_S`
- 检查时 `time.monotonic() < deadline` → True，过期自动清理
- 多实例部署时，每个进程独立标记，最坏情况多打一次请求再发现
- 如需跨实例共享，升级为 Redis TTL key: `SET route_agent:limited:{model_id} 1 EX 5`
- 时间语义遵循 §5.1：这里必须使用 monotonic，不得改为墙钟时间比较

**类 2: `InMemoryRateLimiter`**

进程内限流器，无 Redis 时的默认降级方案。单进程精确，多进程各自独立计数。

- `__init__()` — 无外部依赖

**数据结构（per model_id）：**
- RPM: `dict[str, deque[float]]` — 滑动窗口时间戳队列
  - 每次 `allow()` 时懒清理: 弹出 `now - ts > 60s` 的条目
  - 上限保护: `maxlen = min(rpm_limit * 2, 2000)`，防止异常配置撑爆内存
- 并发: `dict[str, dict[str, int]]` — `{model_id: {"normal": n, "esc": n}}`
  - `acquire(model_id, traffic_type)` 成功才放行
  - `release(model_id, traffic_type)` 必须在 `try/finally` 中执行
  - 推荐用 `asynccontextmanager` 包装，保证借贷一致：
    ```python
    @asynccontextmanager
    async def concurrency_slot(self, model_id: str, traffic_type: str = "normal"):
        await self.record_request_start_async(model_id, traffic_type)
        try:
            yield
        finally:
            await self.record_request_end_async(model_id, traffic_type)
    ```
- 429 标记: 与 `RedisRateLimiter` 相同（`_limited_until: dict[str, float]`，进程内 TTL）
- 利用率缓存: 无需额外缓存（数据已在进程内，直接计算）

**局限性：**
- 多进程/多实例部署时各进程独立计数，总量可能超限
- 进程重启后计数归零
- 启动时检测 workers > 1 → log warning:
  `"InMemoryRateLimiter is per-process; for global rate limits configure Redis."`

**类 3: `NoOpRateLimiter`**

- 所有限流检查返回 `False` / no-op
- `get_utilization_async` → 返回全零 `ModelUtilization(0.0, 0.0, 0.0, 0.0, False, 0.0, False)`
- `is_escalation_capped_async` → 返回 `False`
- `mark_limited` / `is_recently_limited` → no-op / `False`
- **仅当用户显式指定 `--rate-limit=off` 时使用**，过载保护完全失效

**工厂函数: `create_rate_limiter(redis_url: str | None, mode: str = "auto", fail_strategy: str = "degrade") -> RateLimiter`**

| mode | 行为 |
|------|------|
| `"auto"`（默认） | Redis 可用 → `RedisRateLimiter`；不可用时按 `fail_strategy` 处理 |
| `"redis"` | 强制 Redis，连接失败则报错退出（适合生产硬要求） |
| `"inmemory"` | 强制 `InMemoryRateLimiter`（便于本地一致复现） |
| `"off"` | 强制 `NoOpRateLimiter`（完全放开，需显式选择） |

`fail_strategy`（仅 `mode="auto"` 生效）:
| fail_strategy | 行为 |
|---------------|------|
| `"degrade"`（默认） | 降级到 `InMemoryRateLimiter`，并发出事件 `rate_limiter_degraded_to_inmemory` |
| `"fail_fast"` | 直接抛错退出，不允许语义降级 |

默认语义：**Fail-Closed-ish**（降级但不放弃保护），而非 Fail-Open。

**状态与事件建议：**
- 当 `RedisRateLimiter` -> `InMemoryRateLimiter` 切换时：记录告警事件 `rate_limiter_degraded_to_inmemory`
- 当 Redis 恢复并重新启用 `RedisRateLimiter` 时：记录恢复事件 `rate_limiter_recovered_to_redis`

### 7.6 `selector.py` — ModelSelector（核心算法）

**最终排序分 — 单一真源（所有修正的计算顺序与公式）：**

```
# ① 基础维度分（scorer.py）
base_dim = compute_dimension_score(relevant_dimensions, capabilities)   # [0, 1]
cost     = compute_cost_score(pricing, relevant_dimensions)             # [0, 1]

# ② Pool 加成（class_pool.py，仅池内模型）
trials = success_count + fail_count
ratio  = min(trials / MIN_TRIALS, 1.0)
pct    = POOL_BONUS_BASE_RATIO + (POOL_BONUS_FULL_RATIO - POOL_BONUS_BASE_RATIO) * ratio
dim_after_pool = base_dim * (1 + pct)          # 池外模型跳过此步

# ③ 健康度修正（health.py，互斥）
(status, multiplier) = get_health_modifier(agent_class, model_id, cost, is_escalation)
dim_after_health = dim_after_pool * multiplier  # >1 奖励, <1 惩罚, =1 无修正

# ④ 可用性降权（加法，health.py）
if is_degraded:       dim_after_health -= DEGRADED_PENALTY        # 0.05
if is_probe_cooldown: dim_after_health -= PROBE_COOLDOWN_PENALTY  # 0.02

# ⑤ 最终排序
final_score = dim_after_health
# 先按 final_score 降序；同分段（delta < SCORE_TIER_EPSILON）内按 cost 升序
```

**示例（便宜池内模型，bonus_level=2，非 degraded）：**
```
base_dim=0.72, cost=0.003, pool trials=8 → pct=9.2%
dim_after_pool  = 0.72 * 1.092 = 0.786
multiplier      = 1.0 + (1.44 - 1.0) * (1.0 - 0.003) = 1.438
dim_after_health = 0.786 * 1.438 = 1.130
final_score     = 1.130
```

**类: `ModelSelector`**

- `__init__(health: HealthManager, rate_limiter: RateLimiter, class_pool_mgr: ClassPoolManager)`

**方法: `select_async(available_models: list[ModelMetadata], request: RouteRequest) -> RouteDecision`**

**Pipeline（9步）:**

```
Step 1: 过滤 unable 模型 + 标记 degraded / probe cooldown 模型
  ├─ (selectable, is_degraded, is_probe_cooldown) = health_manager.is_available(model_id)
  ├─ selectable == False → 排除（unable）
  ├─ is_degraded == True → 标记，Step 4.7 降权
  └─ is_probe_cooldown == True → 标记，Step 4.8 降权

Step 2: 过滤 rate-limited 模型
  └─ rate_limiter.is_rate_limited_async(model_id, limits) == True → 排除

Step 3: 过滤用户约束
  ├─ model_id in constraints.exclude_models → 排除
  ├─ constraints.require_provider 且 provider != require_provider → 排除
  ├─ constraints.max_cost 且 compute_effective_price_per_1m(pricing, relevant_dimensions) > max_cost → 排除
  │   （`max_cost` 单位固定为 USD/1M tokens，不再使用 input-only 口径）
  └─ constraints.estimated_input_tokens 不为 None
       且 model.limits 含 max_context_tokens 且值 > 0
       且 estimated_input_tokens > model.limits["max_context_tokens"] * CONTEXT_LIMIT_BUFFER_RATIO → 排除
       （安全缓冲防止边界截断：实际用量不得超过模型上限的 90%；
         limits 中无 max_context_tokens 或值为 None/0 时跳过此项过滤，不误杀未标注上限的模型）

Step 4: 计算每个模型的 dimension_score
  ├─ raw_dimension_score = compute_dimension_score(request.analysis.relevant_dimensions, model.capabilities)
  └─ 保留 raw_dimension_score 副本，供 Step 7 能力天花板槽位使用（不受后续 bonus/penalty 影响）

Step 4.5: 应用 Class Pool 加成（比例制，随试用次数递增）
  ├─ agent_class, class_source = class_pool_mgr.resolve_class(request)
  ├─ pool_entries = class_pool_mgr.get_pool_entries(agent_class)
  ├─ 池内模型: trials = success_count + fail_count
  │   ratio = min(trials / MIN_TRIALS, 1.0)
  │   pct = POOL_BONUS_BASE_RATIO + (POOL_BONUS_FULL_RATIO - POOL_BONUS_BASE_RATIO) * ratio
  │   dimension_score *= (1 + pct)
  │   （trials=5 → 6.6% 加成，trials=10 → 10% 满额加成）
  └─ 记录 pool_default（若有），供 Step 8 使用

Step 4.6: 应用健康度修正（奖励/惩罚）
  ├─ 对每个候选模型: (status, multiplier) = health_manager.get_health_modifier(agent_class, model_id, cost_score)
  ├─ 注意: 正常选择流程 is_escalation=False，奖励受成本衰减
  ├─ multiplier > 1.0: dimension_score *= multiplier（奖励加权）
  ├─ multiplier < 1.0: dimension_score *= multiplier（惩罚降权）
  └─ 修正在 Pool 加分之后应用，便宜模型获得更大奖励幅度

Step 4.7: 应用 Degraded 降权（固定加法惩罚）
  ├─ Step 1 中标记为 degraded 的模型: dimension_score -= DEGRADED_PENALTY（0.05）
  └─ 加法降权，语义为"排名往后挪一点"，不受 dimension_score 分布影响

Step 4.8: 应用探测冷启动降权
  ├─ 条件: last_probe_success=1 且 last_probe_at > now - PROBE_COOLDOWN_S（5分钟）
  ├─ dimension_score -= PROBE_COOLDOWN_PENALTY（0.02）
  └─ 比 degraded 更轻，语义为"刚从 unable 恢复，先少接点流量"

Step 4.9: 应用新模型探索加成（非池模型专属）
  ├─ 条件: 模型不在当前 agent_class 的 class_pool 中
       且 model_release_date 不为 None
       且 model_release_date >= now - NEW_MODEL_LOOKBACK_DAYS（30天内发布）
  ├─ dimension_score += NEW_MODEL_BONUS（0.04，加法加成）
  └─ 目的: 给新上线但尚无历史数据的模型提供小幅曝光机会，使其在探索槽竞争中
       获得优先排序，加速冷启动期积累试用次数
       注意: 不与 pool bonus 叠加（pool 模型已跳过此步）；
             加成幅度低于 POOL_BONUS_BASE_RATIO（0.06），不会越过有历史记录的模型

Step 5: 按 dimension_score 降序排序

Step 6: 同分段内（delta < SCORE_TIER_EPSILON）按 cost_score 升序
  └─ 稳定排序：先按 cost 升序，再按 dimension 降序
     能力相当的模型中优先选便宜的

Step 7: 构建 Top-5（Pool 优先 + 探索槽 + 能力天花板槽）
  ├─ 有 Pool（pool_entries 非空）:
  │   ├─ pool_candidates: 池内模型（已含 pool bonus），按排序后顺序
  │   ├─ E_slots = adaptive_explore_slots(pool_entries):
  │   │     avg_trials = mean(e.success_count + e.fail_count for e in pool_entries)
  │   │     pool_rich  = len(pool_entries) >= EXPLORE_POOL_RICH_THRESHOLD（5）
  │   │                  且 avg_trials >= EXPLORE_AVG_TRIALS_THRESHOLD（5.0）
  │   │     pool_thin  = len(pool_entries) < 3 或 avg_trials < EXPLORE_AVG_TRIALS_THRESHOLD（5.0）
  │   │     → pool_rich  → E_slots = EXPLORE_SLOTS_MIN（1）  # 池稳定，减少无效探索
  │   │     → pool_thin  → E_slots = EXPLORE_SLOTS_MAX（3）  # 池数据稀少，加速新模型发现
  │   │     → 其余       → E_slots = MAX_EXPLORE_SLOTS（2，默认）
  │   ├─ explore_candidates: 非池模型，按 dimension_score 降序（含 Step 4.9 新模型加成后的分数），
  │   │   经 enforce_provider_diversity(非池模型, E_slots, MAX_SAME_PROVIDER_IN_CANDIDATES) 过滤
  │   ├─ ceiling_model: 按 raw_dimension_score（Step 4 原始分，不含 pool bonus / health bonus）最高的模型
  │   │   若 ceiling_model 已在 pool_candidates 或 explore_candidates 中 → 跳过（不重复占位）
  │   │   否则 → 占用 CEILING_SLOTS（1）个槽位，插入 Top-5 首位（index=0，升阶终点）
  │   └─ Top-5 = [ceiling_model?] + pool_candidates[:N] + explore_candidates[:E]
  │       其中 C = 0 或 1（ceiling 是否占位）
  │       N = min(len(pool_candidates), 5 - C)
  │       E = min(E_slots, 5 - C - N)
  │       总数不超过 5
  │
  └─ 无 Pool（冷启动）:
      ├─ raw_candidates = sorted[:N]（N 足够大以容纳多样性过滤后的 5 个结果）
      └─ candidates = enforce_provider_diversity(raw_candidates, limit=5, max_per_provider=MAX_SAME_PROVIDER_IN_CANDIDATES)
          同一提供商最多占 3 个槽位；overflow 按原排序补位，保证返回 min(5, len) 个候选
          （冷启动时无 bonus 膨胀，raw_dimension_score 最高的模型自然在首位，无需额外处理）

Step 8: 确定 start_index
  ├─ constraints.preferred_model 在候选中且可用:
  │     start_index = preferred_model 位置（用户偏好，优先级最高）
  ├─ 有 Pool 默认模型在候选中:
  │     start_index = 默认模型位置
  ├─ 有 Pool 但无默认:
  │     start_index = 0（最佳池内模型）
  ├─ 无 Pool（冷启动）:
  │     start_index = cold_start_index(request.analysis, len(candidates))
  │     （基于 relevant_dimensions 最高分推导：score>=8→0, >=5→1, >=3→2, <3→3）
  │     （dimensions 为空时兜底 index=2，兼容原保守策略；结果 clamp 到 candidate_count-1）
  └─ 候选不足时:
        start_index = max(0, len(candidates) - 2)

Step 9: 生成告警
  └─ len(candidates) < MIN_CANDIDATES_FOR_AUTO → alerts += "仅 {n} 个候选模型可用"
```

**返回:** `RouteDecision(primary_model=candidates[start_index].model_id, ...)`

### 7.7 `escalation.py` — EscalationManager

**类: `EscalationManager`**

- `__init__(decision: RouteDecision, rate_limiter: RateLimiter, health_manager: HealthManager, available_models: list[ModelMetadata])` — 持有候选列表、当前位置、限流器、健康管理器和全局模型列表（`available_models` 用于升阶溢出时在 Top-5 之外寻找替代候选）
- 内部状态: `_attempts: list[ExecutionAttempt]`, `_current_index: int`
- 进程内状态: `_escalation_waiting_count: int`, `_escalation_waiting_lock: asyncio.Lock`

**方法 1: `record_attempt(attempt: ExecutionAttempt)`**
- 追加到 `_attempts`

**方法 2: `next_action(priority: str = "normal") -> EscalationResult`**

`priority` 取值:
| 来源 | priority | 含义 |
|------|----------|------|
| 自动升阶（连续失败） | `"normal"` | 系统判断，受完整过载保护约束 |
| 人工质量反馈触发 | `"elevated"` | 用户明确说效果差，过载保护放宽至 0.95 |
| 用户强制指定模型 | `"forced"` | 用户显式选择，仅在已 429 时拦截 |

**状态机逻辑:**

```
escalate 公共逻辑:
  ├─ 当前 index > 0: next_model = candidates[index - 1]
  │   携带前模型 output_snippet 作为 context_for_next
  │   注意: 探索模型也参与升阶，Top-5 中所有模型均可作为升阶目标
  │
  └─ 当前 index == 0（Top-5 天花板已触顶）:
       ├─ 升阶穿透: 从 available_models 中查找 Top-5 之外的候选
       │   条件: raw_dimension_score > candidates[0].raw_dimension_score
       │          且 status != 'unable' 且 not rate_limited
       │   评分使用 is_escalation=True（跳过成本衰减）
       │   按 raw_dimension_score 降序取第一个
       │
       ├─ 找到穿透候选 → action="escalate_breakthrough"
       │   next_model = 穿透候选
       │   alerts += "升阶穿透: Top-5 耗尽，启用窗口外模型 {model_id}"
       │
       └─ 无穿透候选（所有更强模型均 unable/limited）→ action="alert_top_failed"
           next_model = None

failure_type 区分:
  "deployment" → 直接 escalate（执行层已重试 3 次，到升阶层说明不是偶发抖动）
  "quality"    → 给 1 次重试机会（可能是 prompt 偶发/LLM 随机性），第 2 次 quality 失败即 escalate

quality 失败次数:
  0次 → action="retry"（给一次机会，next_model=当前模型）
  1次 → action="escalate"（两次都差，模型不适合此任务）
```

**升阶目标健康度重算：**

升阶选中 `candidates[index - 1]` 后，需要用 `is_escalation=True` 重算该模型的健康度修正：
```
(status, multiplier) = health_manager.get_health_modifier(
    agent_class, next_model_id, cost_score, is_escalation=True
)
```
升阶目标获得完整 `raw_bonus = 1.2^level` 奖励，不受成本衰减。
这确保升阶到贵模型时，其历史表现优势能完整体现，避免升阶后效果改善不明显。

**方法 3: `escalate_with_overload_check_async(current_model_id, priority, decision) -> EscalationResult`**

> 调用方主入口。内部先调用 `next_action(priority)` 确定动作（retry/escalate/alert），
> 若动作为 escalate 则叠加过载保护检查。调用方不应直接调用 `next_action`。

升阶时的过载保护检查（按 priority 分级）:

| priority | is_limited | rpm/conc 0.70-0.90 | rpm/conc > 0.90 | rpm/conc > 0.95 |
|----------|-----------|-------------------|----------------|----------------|
| normal | 拦截 | 概率跳过 | 必定跳过 | 必定跳过 |
| elevated | 拦截 | 放行 | 放行 | 拦截 |
| forced | 拦截 | 放行 | 放行 | 放行 |

**统一升阶目标遍历（最多 MAX_ESCALATION_ATTEMPTS = 3 个目标）:**

```
升阶请求（next_action 返回 escalate/escalate_breakthrough）
  │
  ├─ Step 0:【仅执行失败】Provider 连通性测试（_probe_providers_async）
  │    对每个 provider 的最便宜模型（_cheapest_per_provider）并发调用 probe_callback
  │    探测失败/异常的 provider 加入 unreachable_providers 集合
  │    质量失败不触发此步骤（unreachable_providers = 空集）
  │
  ├─ Step 1: 构建候选目标列表（_build_escalation_targets）
  │    从 candidates[current_index-1] 向 candidates[0] 收集（强度递增方向）
  │    不足 3 个时追加 _breakthrough_candidate()（若存在且不重复）
  │    截断到 MAX_ESCALATION_ATTEMPTS 个
  │
  ├─ Step 2: 依次检查每个目标
  │    a. provider in unreachable_providers → 跳过（仅 exec fail 时生效）
  │    b. is_limited → 跳过
  │    c. peak_ratio >= 阈值（按 priority 分级）→ 跳过
  │    d. is_escalation_capped → 跳过
  │    e. 全部通过 → 返回 escalate/escalate_breakthrough 到该目标
  │       目标在 candidates 中 → "escalate"，否则 → "escalate_breakthrough"
  │
  └─ Step 3: 所有目标均不可用 → 兜底
       ├─ 执行失败触发 → action="alert_escalation_unavailable"
       └─ 质量失败触发 → 检查原模型：
            ├─ 原模型可用 → action="retry", next_model=当前模型
            ├─ 原模型被限速 → wait-and-retry（指数退避）
            │    delay: 0.5→1.0→2.0s，总计 ≤ ESCALATION_WAIT_MAX_TOTAL(7s)
            │    每轮重新检查原模型 is_limited
            │    恢复可用 → action="retry"
            └─ 超时仍不可用 → action="alert_escalation_unavailable"
```

### 7.8 `downgrade.py` — DowngradeOptimizer

**类: `DowngradeOptimizer`**

- `__init__(class_pool_mgr: ClassPoolManager, health: HealthManager, rate_limiter: RateLimiter, router_storage: RouterStorage)`

**方法 1: `should_try_downgrade_async(agent_class, domain, current_model_id, next_cheaper: ModelCandidate) -> bool`**

条件（全部满足才返回 True）:
1. 当前模型连续成功 >= `DOWNGRADE_SUCCESS_THRESHOLD`（10次）
2. `current_dimension_score - next_cheaper.dimension_score < DOWNGRADE_SCORE_GAP_MAX`（0.10）
3. `next_cheaper.health_status != "unable"`
4. `next_cheaper.rate_limited == False`
5. `expected_savings_ratio >= DOWNGRADE_MIN_SAVINGS_RATIO`（10%）
6. challenger 不在降阶冷却期（`is_downgrade_in_cooldown(...) == False`）

**方法 2: `start_downgrade_trial_async(agent_class, domain, incumbent_model_id, challenger_model_id, expected_savings_ratio) -> bool`**
- 创建 active trial（`downgrade_trials` 表），`canary_ratio = DOWNGRADE_CANARY_RATIO`（50%）
- 同一 `(agent_class, domain)` 若已有 active trial，返回 False（避免并发试用互相干扰）

**方法 3: `choose_trial_model_async(agent_class, domain, decision: RouteDecision) -> RouteDecision`**
- 若存在 active trial：
  - 以 `canary_ratio` 概率将 `primary_model` 改为 challenger（小流量试用）
  - 其余请求仍走 incumbent（稳定主流量）
- 若无 active trial：原样返回 decision

**方法 4: `record_downgrade_result_async(agent_class, domain, model_id, outcome_type) -> str`**
- `outcome_type in {"quality_good", "quality_fail", "exec_fail"}`
- 仅当 `model_id == challenger_model_id` 时计入试用统计（避免非试用流量污染）
- 返回动作：`"continue" | "rollback" | "promote"`

回滚触发（命中任一即 `"rollback"`）:
1. `quality_fail_count >= DOWNGRADE_ROLLBACK_QUALITY_FAIL`（2）
2. `exec_fail_count >= DOWNGRADE_ROLLBACK_EXEC_FAIL`（1）

转正触发（全部满足才 `"promote"`）:
1. `sampled_requests >= DOWNGRADE_TRIAL_MIN_SAMPLES`（5）
2. 未触发回滚条件
3. challenger 满足降阶晋升门槛（`success_count >= DOWNGRADE_PROMOTION_MIN_SUCCESS`，15）
4. `DefaultsStore.evaluate_and_promote_default_async(..., min_success=DOWNGRADE_PROMOTION_MIN_SUCCESS)` 判定通过

**方法 5: `finalize_downgrade_trial_async(agent_class, domain, result) -> None`**
- `result == "promote"`：结束试用，state=`promoted`
- `result == "rollback"`：恢复 incumbent，state=`rolled_back`，并设置 `cooldown_until = now + DOWNGRADE_COOLDOWN_H`

**降阶试用追踪机制：**
- 降阶由“直接切默认”改为“先小流量试用，再转正”
- 试用统计与默认位计数解耦：试错不会直接污染 `class_pool_defaults`
- 失败快速回滚（2 次质量失败或 1 次执行失败），并进入冷却期，防止来回抖动
- 通过样本门槛 + Wilson 仲裁后才允许转正，避免小样本误判

### 7.9 `engine.py` — RouterEngine

**类: `RouterEngine`**

**构造:**
```
__init__(
    pool: MainModelPool,
    analysis_storage: AnalysisStorage,
    redis_url: str | None = None,
    router_db_path: str | None = None,
    rate_limit_mode: str = "auto",
    rate_limit_fail_strategy: str = "degrade",
)
```

内部初始化:
1. `RouterStorage(router_db_path)`
2. `HealthManager(router_storage)`
3. `create_rate_limiter(redis_url, mode=rate_limit_mode, fail_strategy=rate_limit_fail_strategy)` → `RateLimiter`
4. `ClassPoolManager(router_storage)` — 内部创建 `DefaultsStore`
5. `ModelSelector(health, rate_limiter, class_pool_mgr)`
6. `DowngradeOptimizer(class_pool_mgr, health, rate_limiter, router_storage)`
7. 启动后台探测任务: `asyncio.create_task(health.probe_loop_async())`
8. demo 阶段不启动后台清理任务（无 `_cleanup_loop_async`）
   - `cleanup_old_logs` / `cleanup_old_events` / `cleanup_old_downgrade_trials` 仅作为手动维护接口保留
   - `class_pool_mgr.evict_check(agent_class)` 仍在池变更路径触发（见 §14.3）

**方法 1: `route_async(request: RouteRequest) -> RouteDecision`**
1. `available = pool.list_available(provider=request.constraints.require_provider)`
2. `decision = selector.select_async(available, request)`
3. `agent_class, class_source = class_pool_mgr.resolve_class(request)`，
   `domain = request.analysis.domain if ENABLE_DOMAIN_DEFAULTS else DEFAULT_DOMAIN_KEY`
4. `decision = downgrade_optimizer.choose_trial_model_async(agent_class, domain, decision)`（若存在 active trial，按 canary 比例切到 challenger）
5. 在 `decision` 中携带 `class_source`（用于观测与日志）
6. 返回 `decision`

> **调用方职责：** `route_async` 仅做选择，不做并发计数。调用方在拿到 `decision` 后、
> 实际执行模型调用前后，必须调用限流计数接口以闭环并发追踪：
> ```
> decision = engine.route_async(request)
> await engine.rate_limiter.record_request_start_async(decision.primary_model, "normal")
> try:
>     result = await execute_model(decision.primary_model, ...)
> finally:
>     await engine.rate_limiter.record_request_end_async(decision.primary_model, "normal")
> ```
> 升阶场景同理，`traffic_type="escalation"`。若不调用，并发利用率将始终为 0，
> 升阶 cap 和过载保护形同虚设。

**方法 2: `route(request: RouteRequest) -> RouteDecision`**
- 同步包装，使用独立线程运行事件循环（避免 `nest_asyncio` monkey-patch）:
  ```python
  def route(self, request: RouteRequest) -> RouteDecision:
      try:
          loop = asyncio.get_running_loop()
      except RuntimeError:
          loop = None
      if loop and loop.is_running():
          # 已在事件循环中（如 FastAPI），用线程池避免死锁
          import concurrent.futures
          with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
              return pool.submit(asyncio.run, self.route_async(request)).result()
      else:
          return asyncio.run(self.route_async(request))
  ```

**方法 3: `create_escalation_manager(decision: RouteDecision) -> EscalationManager`**
- 返回 `EscalationManager(decision, self.rate_limiter, self.health_manager, self.pool.list_available())`

**方法 4: `report_quality_async(request_id, record_id, model_id, agent_name, agent_class, domain, rating, action, note, event_ts)`**
- 说明：demo 阶段 domain 参数仅用于日志/观测，默认模型学习内部固定 `domain=__global__`
- 幂等 + 状态机校验（见 §7.10）:
  1. `event_type = "quality_good" if rating in ("good", "fair") else "quality_poor"`
  2. `existing = router_storage.get_events(request_id, model_id)`
  3. 若 `"exec_fail" in existing` → 拒绝（执行失败后不应评估质量），log warning 并返回
  4. 若 `"exec_success" not in existing` → 拒绝（未上报执行成功就评估质量），log warning 并返回
  5. `ok = router_storage.try_record_event(request_id, model_id, event_type, event_ts)`
  6. 若 `ok == False` → 重复上报，跳过
- 若 `record_id is not None`：调用 `analysis_storage.update_quality_review_async(record_id, rating=rating, action=action, note=note)`
  （`record_id` 仅用于关联 analysis_records；幂等主键不依赖它）
- 若 rating in ("good", "fair"):  （fair 视同 good，均为正向反馈）
  - `health_manager.on_quality_good_async(agent_class, model_id)`
  - `class_pool_mgr.record_outcome(agent_class, domain, model_id, "success")`
  - `router_storage.log_outcome(request_id, agent_name, agent_class, class_source, domain, model_id, record_id, "success")`
  - `action = downgrade_optimizer.record_downgrade_result_async(agent_class, domain, model_id, "quality_good")`
  - 若 `action == "promote"`：
    - `downgrade_optimizer.finalize_downgrade_trial_async(..., "promote")`
    - `router_storage.log_outcome(..., request_id=request_id, class_source=class_source, record_id=record_id, outcome="downgrade_promote")`
  - 若当前默认满足降阶触发条件且 `start_downgrade_trial_async(...) == True`：
    - `router_storage.log_outcome(..., request_id=request_id, class_source=class_source, record_id=record_id, outcome="downgrade_start")`
- 若 rating == "poor":
  - `health_manager.on_quality_fail_async(agent_class, model_id)` — 第 1 次 poor 容忍（bonus 不变），第 2+ 次衰减 bonus_level //= 2，衰减至 0 后累积 penalty_level
  - `class_pool_mgr.record_outcome(agent_class, domain, model_id, "quality_fail")`
  - `router_storage.log_outcome(request_id, agent_name, agent_class, class_source, domain, model_id, record_id, "quality_fail")`
  - `action = downgrade_optimizer.record_downgrade_result_async(agent_class, domain, model_id, "quality_fail")`
  - 若 `action == "rollback"`：
    - `downgrade_optimizer.finalize_downgrade_trial_async(..., "rollback")`
    - `router_storage.log_outcome(..., request_id=request_id, class_source=class_source, record_id=record_id, outcome="downgrade_rollback")`

**方法 5: `report_execution_async(request_id, record_id, model_id, agent_name, agent_class, domain, completed, error_type, error_detail, event_ts)`**
- 说明：demo 阶段 domain 参数仅用于日志/观测，默认模型学习内部固定 `domain=__global__`
- 幂等 + 状态机校验（见 §7.10）:
  1. `event_type = "exec_success" if completed else "exec_fail"`
  2. `ok = router_storage.try_record_event(request_id, model_id, event_type, event_ts)`
  3. 若 `ok == False` → 重复上报，跳过
  4. 注意：exec_success 和 exec_fail 互斥（UNIQUE 约束保证同一 request_id+model_id 只能有一个执行结果）
- 若 `record_id is not None`：调用 `analysis_storage.update_execution_result_async(record_id, completed=completed, error_type=error_type, error_detail=error_detail)`
  （`record_id` 仅用于关联 analysis_records；幂等主键不依赖它）
- 若 `completed == False`:\
  - `health_manager.report_exec_failure_async(model_id)` — 渐进状态机：available→degraded→unable
  - `class_pool_mgr.record_outcome(agent_class, domain, model_id, "exec_fail")`
  - `router_storage.log_outcome(request_id, agent_name, agent_class, class_source, domain, model_id, record_id, "exec_fail")`
  - `action = downgrade_optimizer.record_downgrade_result_async(agent_class, domain, model_id, "exec_fail")`
  - 若 `action == "rollback"`：
    - `downgrade_optimizer.finalize_downgrade_trial_async(..., "rollback")`
    - `router_storage.log_outcome(..., request_id=request_id, class_source=class_source, record_id=record_id, outcome="downgrade_rollback")`
  - 执行失败不影响 consecutive_success / consecutive_fail / 默认模型

**方法 6: `list_pools_async() -> list[dict]`**
- 返回所有池的概览：`[{agent_class, model_count, default_model, ...}, ...]`

**方法 7: `inspect_pool_async(agent_class) -> list[ClassPoolEntry]`**
- 返回指定池的详细模型列表

**方法 8: `rate_limiter_status() -> dict`**
- 返回 `{"mode": "redis|inmemory|off", "fail_strategy": "...", "switched_at": "...", "last_error": "..."}`（用于运维观测）

### 7.10 反馈事件幂等与单请求状态机

#### 7.10.1 问题

1. **重复上报：** 网络重试、客户端 bug 导致同一 `(request_id, model_id)` 的事件被多次提交，计数器被重复递增。
2. **乱序上报：** 先到达 `exec_fail`，后到达 `exec_success`（或反之），统计被污染。
3. **双通道冲突：** 同一次请求同时触发 `report_execution_async` 和 `report_quality_async`，导致 `exec_fail_count` 和 `fail_count` 双计数。

#### 7.10.2 单请求生命周期状态机

每个 `(request_id, model_id)` 的合法事件迁移：

```
                    ┌─────────────┐
                    │  (无事件)    │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼                         ▼
      ┌──────────────┐         ┌──────────────┐
      │  exec_fail   │         │ exec_success │
      │  (终态)       │         └──────┬───────┘
      └──────────────┘                │
                           ┌──────────┼──────────┐
                           ▼                     ▼
                   ┌──────────────┐      ┌──────────────┐
                   │ quality_good │      │ quality_poor │
                   │ (终态)       │      │ (终态)       │
                   └──────────────┘      └──────────────┘
```

**合法迁移：**
| 当前已有事件 | 允许写入 | 拒绝写入 |
|-------------|---------|---------|
| (空) | `exec_success`, `exec_fail` | `quality_good`, `quality_poor` |
| `exec_fail` | (无) | 所有（终态） |
| `exec_success` | `quality_good`, `quality_poor` | `exec_fail`（互斥） |
| `exec_success` + `quality_*` | (无) | 所有（终态） |

**关键约束：**
- `exec_success` 和 `exec_fail` 互斥：UNIQUE(request_id, model_id, event_type) 保证不重复，且 `report_quality_async` 检查前置事件
- 质量事件必须在 `exec_success` 之后：`report_quality_async` 校验 `"exec_success" in existing`
- 执行失败后不允许质量评估：`report_quality_async` 校验 `"exec_fail" not in existing`
- 所有校验在写入 `feedback_events` 之前完成，利用 `INSERT OR IGNORE` 的原子性兜底
- `record_id` 可为空（fallback 场景），但不影响幂等：状态机主键是 `(request_id, model_id)`

#### 7.10.3 幂等保证

```
report_execution_async / report_quality_async 入口:
  1. 计算 event_type
  2. 校验状态机合法性（get_events 查已有事件）
  3. try_record_event(request_id, model_id, event_type, event_ts)
     └─ INSERT OR IGNORE INTO feedback_events ...
     └─ 返回 affected_rows > 0
  4. 若返回 False → 重复事件，直接 return（不执行任何副作用）
  5. 若返回 True → 首次处理，继续执行 health/pool/log 更新
```

**乱序处理：** `exec_success` 和 `exec_fail` 互斥写入。若 `exec_fail` 先到达并写入成功，后到达的 `exec_success` 会被状态机拒绝（`exec_fail` 是终态）。反之亦然。先到先得，后到丢弃。调用方应确保事件按因果序发送；若无法保证，可通过 `event_ts` 在应用层做额外仲裁（v2 扩展）。
`event_ts` 仅用于审计与排障记录，主状态仲裁仍以幂等表约束为准（见 §5.1）。

#### 7.10.4 保留与手动清理

demo 阶段默认不做自动清理。`feedback_events` / `class_success_log` / `downgrade_trials` / `class_review_queue`
若需要控库大小，使用手动维护接口按时间清理：
```
- router_storage.cleanup_old_logs(retention_days=30)
- router_storage.cleanup_old_events(retention_days=7)
- router_storage.cleanup_old_downgrade_trials(retention_days=30)
- router_storage.cleanup_old_class_reviews(retention_days=90)  # 仅清理非 pending 项
```

### 7.11 `__init__.py` — 公共 API

导出:
```
RouterEngine
RouteRequest, RouteConstraints, RouteDecision
ModelCandidate, ExecutionAttempt, EscalationResult
ModelAvailability, ClassPoolEntry, ClassPoolDefault
EscalationManager, ClassPoolManager, HealthManager
# DefaultsStore 不导出（内部模块，通过 ClassPoolManager 访问）
```

## 8. 实现阶段与依赖

```
Phase 1: schemas.py + constants.py + scorer.py + storage/
  └─ 无外部依赖，纯数据结构和计算

Phase 2: health.py（事件驱动，不依赖 AnalysisStorage）
  └─ 依赖 Phase 1

Phase 2.5: class_pool.py + defaults.py（内部） + class_pool 相关表
  └─ 依赖 Phase 1 (schemas, storage)
  └─ defaults.py 作为 ClassPoolManager 的内部模块一起实现
  └─ 与 Phase 3 可并行

Phase 3: rate_limiters/                    ← 可与 Phase 1-2 并行
  └─ 独立模块，仅依赖 Redis

Phase 4: selector.py（含 Step 4.5 Class Pool 集成）
  └─ 依赖 Phase 1, 2, 2.5, 3

Phase 5: escalation.py + downgrade.py
  └─ 依赖 Phase 4

Phase 6: engine.py + __init__.py
  └─ 依赖所有前置阶段

Phase 7: 替换 app/service.py 内联路由
  └─ 依赖 Phase 6

Phase 7.5: task_analyzer 扩展（task_class 字段 + LLM prompt）  ← NEW
  └─ 可与 Phase 2.5 并行

Phase 8: 全量测试
  └─ 每个 Phase 完成后写对应测试，Phase 8 补充集成测试
```

并行机会: Phase 1 + Phase 3 可同时开发。Phase 2.5 + Phase 3 + Phase 7.5 可并行。

## 9. app/service.py 接入方案

### 9.1 移除 / 降级保留

- `_detect_task_type()` → 重命名为 `_legacy_detect_task_type()`，仅在 task_analyzer 调用失败时作为降级路径
- `_estimate_complexity()` → 重命名为 `_legacy_estimate_complexity()`，同上
- `_choose_tier()` → 移除（方案 A：fallback 仅降级分析，不降级路由决策）
- `_as_float()` — 保留（通用工具函数）

正常流程使用 `TaskAnalysisResult` + `ModelSelector`。当 task_analyzer 抛出异常时，
回退到 `_legacy_*` 函数生成简化的 `RouteRequest`（domain 由关键词匹配，dimensions 由长度估算）。
fallback 生成的请求仍然进入 `RouterEngine.route_async()`（内部调用 `selector.select_async()`），由统一 selector 决策。

**legacy `task_type` 关键词映射（仅降级路径使用，顺序匹配）：**

| task_type | 关键词示例 |
|-----------|------------|
| `coding` | `code`, `coding`, `python`, `bug`, `debug`, `function`, `script`, `sql`, `programming` |
| `translation` | `translate`, `translation`, `localize` |
| `scrape` | `scrape`, `crawler`, `crawl`, `xpath`, `selector`, `html` |
| `extraction` | `extract`, `extraction`, `entity`, `schema`, `structured`, `json` |
| `summarization` | `summarize`, `summary`, `tldr`, `tl;dr`, `brief` |
| `classification` | `classify`, `classification`, `label`, `sentiment`, `categorize` |
| `rewrite` | `rewrite`, `paraphrase`, `polish`, `rephrase` |
| `review` | `review`, `audit`, `critique`, `evaluate`, `assessment` |
| `reasoning` | `reason`, `prove`, `analyze`, `analysis`, `deduce` |
| `math` | `math`, `equation`, `calculate`, `formula`, `algebra` |
| `qa` | 未命中以上关键词时兜底 |

### 9.2 新流程

```python
import uuid

# 模块级单例（应用启动时初始化一次，避免每次请求重复创建）
_engine: RouterEngine | None = None

def _get_engine(pool, analysis_storage) -> RouterEngine:
    global _engine
    if _engine is None:
        _engine = RouterEngine(
            pool, analysis_storage,
            redis_url=os.getenv("REDIS_URL"),
            rate_limit_mode=os.getenv("RATE_LIMIT_MODE", "auto"),
            rate_limit_fail_strategy=os.getenv("RATE_LIMIT_FAIL_STRATEGY", "degrade"),
        )
    return _engine

def run_route_agent(request, **kwargs):
    task = request["task"]
    system_prompt = str(request.get("system_prompt") or "")
    constraints = request.get("constraints", {})
    request_id = request.get("request_id") or str(uuid.uuid4())

    # 1. 获取模型池（不变）
    pool = ...  # 现有逻辑

    # 2. 任务分析（新增，含 _legacy_* 降级路径）
    from route_agent.task_analyzer import analyze
    try:
        analysis_result, record_id = analyze(
            agent_name="route_agent",
            system_prompt=system_prompt,
            task_prompt=task,
        )
    except Exception as e:
        import logging
        logging.warning("task_analyzer failed, falling back to legacy: %s", e)
        task_type = _legacy_detect_task_type(task)
        complexity = _legacy_estimate_complexity(task)
        analysis_result = _build_legacy_analysis(task_type, complexity)
        record_id = None

    # 3. 构建路由请求
    route_request = RouteRequest(
        agent_name="route_agent",
        agent_class=request.get("agent_class"),  # 可选，调用方传入
        request_id=request_id,
        system_prompt=system_prompt,
        task_prompt=task,
        analysis=analysis_result,
        record_id=record_id,
        constraints=RouteConstraints(
            max_cost=constraints.get("max_cost"),
            preferred_model=constraints.get("preferred_model"),
            exclude_models=tuple(constraints.get("exclude_models", [])),
            require_provider=constraints.get("require_provider"),
        ),
    )

    # 4. 路由决策（使用单例 engine）
    engine = _get_engine(pool, analysis_storage)
    decision = engine.route(route_request)

    # 5. 返回 payload（向后兼容）
    return {
        "result": None,
        "model_used": decision.primary_model,
        "cost": None,
        "routing_reason": decision.reason,
        "analysis": analysis_result.to_dict(),
        "candidates": [vars(c) for c in decision.candidates],
        "start_index": decision.start_index,
        "alerts": list(decision.alerts),
        "default_used": decision.default_used,
        "pool_hit": decision.pool_hit,
        "pool_class": decision.pool_class,
        # ... 保留 registry_sync 等字段
    }
```

### 9.3 CLI 兼容

- 保留所有现有 CLI 参数
- 新增可选参数: `--redis-url`, `--router-db-path`, `--agent-name`
- 新增限流模式: `--rate-limit=auto|redis|inmemory|off`（默认 `auto`），对应环境变量 `RATE_LIMIT_MODE`
- 新增降级策略: `--rate-limit-fail-strategy=degrade|fail_fast`（默认 `degrade`），对应环境变量 `RATE_LIMIT_FAIL_STRATEGY`
- `--exclude-models` 接受逗号分隔列表
- `--require-provider` 接受单个 provider 名

## 10. requirements.txt 变更

新增:
```
redis[hiredis] >= 5.0.0
```

## 11. 验证方式

| 阶段 | 命令 | 目标 |
|------|------|------|
| Phase 1 | `pytest route_agent/router_engine/tests/test_schemas.py test_scorer.py -v` | 数据结构 + 打分 |
| Phase 2 | `pytest route_agent/router_engine/tests/test_health.py test_defaults.py -v` | 健康 + 默认 |
| Phase 2.5 | `pytest route_agent/router_engine/tests/test_class_pool.py -v` | Class Pool 池操作 |
| Phase 3 | `pytest route_agent/router_engine/tests/test_router_engine_module.py -v` | 限流/路由模块基础接口验证 |
| Phase 4 | `pytest route_agent/router_engine/tests/test_selector.py -v` | 选择器边界 |
| Phase 5 | `pytest route_agent/router_engine/tests/test_escalation.py test_downgrade.py -v` | 状态机 |
| Phase 6 | `pytest route_agent/router_engine/tests/ -v --cov=route_agent/router_engine` | 全量 + 覆盖率 |
| Phase 7 | `python -m route_agent --task "Write a Python sort function" --max-cost 0.01` | 端到端 |
| 总体 | 覆盖率 ≥ 80% | — |

## 12. Arena 排行榜集成

### 12.1 数据来源

`model_registry/arena/` 模块从 `https://arena.ai/zh/leaderboard/` 动态爬取模型排行榜数据，覆盖 4 个类别：text、code、vision、search。

### 12.2 capabilities 填充规则

| Arena 类别 | capabilities 字段 |
|-----------|------------------|
| text | text, instruction_following, creative_writing, math |
| code | code |
| vision | vision |
| search | search |

- 仅填充值为 `None` 的字段，已有数据不覆盖
- 使用 rank+ELO 混合归一化（70% rank + 30% ELO），每个类别独立计算
- 填充后附带 `_source` 元信息标记数据来源和时间

### 12.3 对 scorer 的影响

- capabilities 值现在是 0-100 整数（Arena 归一化后），scorer 需除以 100 转为 [0, 1]
- `None` 值仍可能存在（Arena 未覆盖的模型），scorer 使用中性分 50（即 0.5 归一化后）
- `_source` 字段应被 scorer 跳过

### 12.4 三级降级

```
Arena 实时爬取 → SQLite 缓存 → None（capabilities 保持 None，scorer 用中性分）
```

通过 `ENABLE_ARENA_SCORING=1` 环境变量启用，默认关闭。

## 13. 风险与降级

| 风险 | 降级方案 |
|------|---------|
| Redis 不可用 | `mode=auto` 下按 `RATE_LIMIT_FAIL_STRATEGY` 处理：`degrade` → `InMemoryRateLimiter`（单进程有效，多进程存在失真）并告警；`fail_fast` → 直接报错退出 |
| 无质量反馈数据 | 所有模型 bonus_level = 0 且 penalty_level = 0，无奖励/惩罚修正，无默认模型学习 |
| 候选模型不足 `MIN_CANDIDATES_FOR_AUTO`（5）个 | 生成告警，`start_index = max(0, len-2)` |
| task_analyzer 调用失败 | 回退到 `_legacy_detect_task_type` + `_legacy_estimate_complexity` 生成简化分析，再进入统一 selector 路径（方案 A，见 9.1） |
| Arena 爬取失败 | 三级降级：SQLite 缓存 → capabilities 保持 None → scorer 用中性分 50 |
| Class Pool 为空（新 agent_class） | 无加分，正常走 selector 流程 |
| task_class 缺失/解析失败/未命中字典 | 使用 `agent_name + system_prompt` 做向量相似度备用匹配 class；仍未命中时回退 `DEFAULT_AGENT_CLASS("general")` |
| 池内所有模型 unable | 移出池 + 告警用户，回退到全局选择（排除 unable 模型） |
| 模型 unable 后恢复 | 三态渐进：available→degraded（降权5分钟）→unable（每小时探测），偶发抖动仅短暂降权 |
| model_release_date 不可用 | 跳过年龄淘汰规则，仅靠 success_rate 和池满淘汰 |
| task_class 命名不一致 / 新类爆炸 | 受控 class 字典（alias→canonical）+ 未命中进入 `class_review_queue`，先尝试向量备用，仍失败再回退 `general`，审核后再放行 |
| 反馈事件重复/乱序上报 | `feedback_events` 幂等表 + 单请求状态机互斥（见 §7.10），重复事件静默丢弃 |
| 降阶后质量抖动 | 启用小流量试用（50%）+ 快速回滚阈值（2 次 quality_fail 或 1 次 exec_fail）+ 24h 冷却期 |

## 14. Class Pool — Agent 类别模型池

### 14.1 概述

为不同类别的 Agent 任务维护专属模型池。同一类 Agent（如所有 scrape agent）共享一个模型池，池内模型通过两个并行渠道进入：渠道1（自动统计入池）来源于历史成功案例的置信度验证，渠道2（手动添加）允许用户通过 CLI/API 直接指定。池作为软偏好（score bonus）参与选择，不做硬过滤。

核心设计：
- 池（模型列表 + 统计）：按 `agent_class` 共享
- 默认模型：
  - demo 阶段按 `agent_class` 管理（domain 固定 `__global__`）
  - 未来按 `(agent_class, domain)` 管理（支持垂域分化）
- `agent_name`：记录在日志中用于追踪，不作为池的 key

### 14.2 Agent Class 解析（统一链路）

```
优先级 1: RouteRequest.agent_class     ← 可选 override（仅调试/人工强制指定）
优先级 2: TaskAnalysisResult.task_class ← LLM 分析时自动分类（Phase 7.5，字段存在时生效）
优先级 3: 向量相似度匹配（agent_name + system_prompt）
优先级 4: DEFAULT_AGENT_CLASS（"general"）
```

- `task_class` 由 task_analyzer 的 LLM 在分析时生成（Phase 7.5）
- LLM 缺失或 LLM 结果未命中字典时，使用向量作为备用；不使用 `task_prompt` 做相似度匹配，避免单任务噪声污染类别判定
- 向量匹配输入固定为：`classification_text = agent_name + "\n" + system_prompt`
- 匹配条件：`top1 >= CLASS_SIM_THRESHOLD` 且 `(top1 - top2) >= CLASS_SIM_MARGIN`，否则回退到 `DEFAULT_AGENT_CLASS`
- 示例：爬虫任务 → `"scrape"`，结构化抽取任务 → `"extraction"`，摘要任务 → `"summarization"`，分类任务 → `"classification"`，改写任务 → `"rewrite"`，评审任务 → `"review"`，翻译任务 → `"translation"`
- LLM prompt 中注入已有 class 列表作为参考，输出先经过受控字典归并（alias→canonical）
- 未命中字典的类别不直接入池：先记录到 `class_review_queue`，再尝试向量备用；仅当仍未命中时回退 `DEFAULT_AGENT_CLASS`
- 归一化：统一小写 + 空格转下划线（如 `"Web Scrape"` → `"web_scrape"`）

**状态流转图（实现基线）**

```text
RouteRequest.agent_class 有值?
  ├─ 是 → normalize → 返回 (resolved_class, "override")
  └─ 否
      ↓
TaskAnalysisResult.task_class 有值?
  ├─ 是
  │   ├─ 命中 class_aliases(alias->canonical)?
  │   │   ├─ 是 → 返回 (canonical_class, "llm")
  │   │   └─ 否 → 记入 class_review_queue(proposed_by="llm") → 进入向量备用
  │   └─ (受控字典关闭时：normalize 后直接返回 "llm")
  └─ 否 → 进入向量备用
      ↓
ENABLE_CLASS_SIM_FALLBACK?
  ├─ 否 → 返回 (DEFAULT_AGENT_CLASS, "default")
  └─ 是
      ↓
match_class_by_embedding(agent_name + system_prompt) 命中?
  ├─ 否 → 返回 (DEFAULT_AGENT_CLASS, "default")
  └─ 是
      ├─ 命中 class_aliases(alias->canonical)?
      │   ├─ 是 → 返回 (canonical_class, "vector")
      │   └─ 否 → 记入 class_review_queue(proposed_by="vector")
      └─ 返回 (DEFAULT_AGENT_CLASS, "default")
```

#### 14.2.1 受控字典与审核流程

- `canonical_class` 来自 `CLASS_DICT_INITIAL_SET` 与人工审核新增项。
- 解析顺序：先尝试 LLM 候选映射到 `class_aliases`，未命中再尝试向量候选映射。
- 未命中字典的 `normalized_class` 写入 `class_review_queue`（累加 `hit_count`）。
- 只有审核 `approved/merged` 后，类别才会进入长期学习面（池命中、默认学习、统计归并）。
- 审核频率可低频执行（demo 阶段人工处理即可），不影响在线请求时延。

#### 14.2.2 变量释义（Class 解析相关）

| 变量 | 类型 | 含义 |
|------|------|------|
| `agent_name` | `str` | Agent 名称，稳定身份信号；用于向量兜底输入 |
| `system_prompt` | `str | None` | Agent 系统提示词；用于向量兜底输入 |
| `task_prompt` | `str` | 单次任务文本；当前不参与向量兜底，避免噪声 |
| `agent_class` | `str | None` | 可选 override（人工强制类别）；传入即最高优先级 |
| `task_class` | `str | None` | LLM 分类结果（Phase 7.5 字段） |
| `normalized_class` | `str` | 归一化后的候选类名（小写 + 下划线） |
| `canonical_class` | `str` | 受控字典中的标准类名（最终用于统计与路由） |
| `resolved_class` | `str` | 解析后的最终类别（用于池命中、默认学习、统计） |
| `class_source` | `str` | `resolved_class` 来源：`override` / `llm` / `vector` / `default` |
| `classification_text` | `str` | 向量匹配输入，固定为 `agent_name + "\n" + system_prompt` |
| `DEFAULT_AGENT_CLASS` | `str` | 类别兜底值，当前为 `"general"` |
| `ENABLE_CLASS_SIM_FALLBACK` | `bool` | 是否开启向量兜底匹配（LLM 缺失或未命中字典时触发；当前默认开启） |
| `ENABLE_CONTROLLED_CLASS_DICT` | `bool` | 是否启用受控 class 字典归并 |
| `CLASS_SIM_THRESHOLD` | `float` | top-1 相似度最低阈值 |
| `CLASS_SIM_MARGIN` | `float` | top-1 与 top-2 最小差值阈值 |
| `CLASS_REVIEW_MIN_HITS` | `int` | 人工审核重点队列的最小命中次数阈值 |
| `DEFAULT_DOMAIN_KEY` | `str` | 默认模型存储 key 的 domain 固定值（`"__global__"`）；不参与 class 解析 |
| `request_id` | `str` | 请求级唯一 ID（幂等/状态机主键） |
| `record_id` | `int | None` | `analysis_records.id` 关联字段；可为空，不作为幂等主键 |

### 14.3 `class_pool.py` — ClassPoolManager

**类: `ClassPoolManager`**

**上线策略（当前）**

- 向量兜底当前采用全量启用，不做灰度分流或对照组实验。
- 线上若出现误匹配/成本/延迟异常，可将 `ENABLE_CLASS_SIM_FALLBACK=false` 立即回退到“LLM 缺失或未命中字典后直接使用 `DEFAULT_AGENT_CLASS`”路径。
- 通过 `class_source=vector` 维度做质量回看，持续监控命中效果。

- `__init__(router_storage: RouterStorage)` — 内部创建 `DefaultsStore(router_storage)`

**方法 1: `resolve_class(request: RouteRequest) -> tuple[str, str]`**

```
if request.agent_class:
    return normalize(request.agent_class), "override"  # 人工 override 直接生效

task_class = getattr(request.analysis, "task_class", None)
if task_class:
    if ENABLE_CONTROLLED_CLASS_DICT:
        canonical = resolve_canonical_class(task_class)  # alias -> canonical
        if canonical is not None:
            return canonical, "llm"
        upsert_class_review_candidate(normalize(task_class), proposed_by="llm")  # 先记录待审核，再走向量备用
    else:
        return normalize(task_class), "llm"

# 兜底阶段：LLM 缺失，或 LLM 未命中字典
if ENABLE_CLASS_SIM_FALLBACK:
    text = request.agent_name + "\n" + (request.system_prompt or "")
    hit = match_class_by_embedding(text, threshold=CLASS_SIM_THRESHOLD, margin=CLASS_SIM_MARGIN)
    if hit is not None:
        if ENABLE_CONTROLLED_CLASS_DICT:
            canonical = resolve_canonical_class(hit.class_name)
            if canonical is not None:
                return canonical, "vector"
            upsert_class_review_candidate(normalize(hit.class_name), proposed_by="vector")
        else:
            return normalize(hit.class_name), "vector"

return DEFAULT_AGENT_CLASS, "default"
```

`normalize()`: 小写 + 空格转下划线。
`resolve_canonical_class()`: 查 `class_aliases` 做 alias->canonical 归并。
说明：`domain="__global__"` 仅用于默认模型存储 key，不参与 class 解析。

**向量匹配函数契约：**
`match_class_by_embedding(classification_text, threshold, margin) -> MatchResult | None`

- 输入:
  - `classification_text: str`（固定为 `agent_name + "\n" + system_prompt`）
  - `threshold: float`（默认 `CLASS_SIM_THRESHOLD=0.82`）
  - `margin: float`（默认 `CLASS_SIM_MARGIN=0.05`）
- 输出:
  - 命中时返回 `MatchResult(class_name, top1_score, top2_score, margin)`
  - 未命中返回 `None`
- 命中条件（同时满足）:
  - `top1_score >= threshold`
  - `top1_score - top2_score >= margin`
- 观测字段:
  - `class_source="vector"`（命中）
  - `class_source="default"`（未命中，回退 `DEFAULT_AGENT_CLASS`）

**受控字典函数契约：**
`resolve_canonical_class(raw_class) -> str | None`

- 输入:
  - `raw_class: str`（来自 LLM 或向量匹配结果）
- 处理:
  - `normalized = normalize(raw_class)`
  - 查 `class_aliases.alias_class == normalized AND is_active=1`
- 输出:
  - 命中返回 `canonical_class`
  - 未命中返回 `None`

`upsert_class_review_candidate(normalized_class, proposed_by) -> None`

- 行为:
  - 若不存在：插入 `class_review_queue(status="pending", hit_count=1)`
  - 若已存在：`hit_count += 1`, `last_seen_at = now`
- 目的:
  - 不阻塞在线路由，把新类治理转为离线人工审核

**方法 2: `get_pool_entries(agent_class) -> list[ClassPoolEntry]`**

- 查询 `class_pool WHERE agent_class = ?`
- 按 success_rate 降序排序（`success_count / (success_count + fail_count)`）
- 未达 `MIN_TRIALS`（10次）的模型排末尾

**方法 3: `apply_pool_bonus(candidates: list[ModelCandidate], pool_entries: list[ClassPoolEntry]) -> list[ModelCandidate]`**

- 对 candidates 中 model_id 存在于 pool_entries 的模型，按试用次数递增加成：
  ```
  trials = success_count + fail_count
  ratio = min(trials / MIN_TRIALS, 1.0)
  pct = POOL_BONUS_BASE_RATIO + (POOL_BONUS_FULL_RATIO - POOL_BONUS_BASE_RATIO) * ratio
  dimension_score *= (1 + pct)
  ```
  - 刚进池（trials=5）：约 6.6% 加成
  - 转正（trials≥10）：10% 满额加成
- 返回新的 candidates 列表（不修改原列表，保持不可变）

**方法 4: `record_outcome(agent_class, domain, model_id, outcome_type) -> None`**

根据 outcome_type 更新池统计 + 默认模型追踪（原子操作）：

<!-- v2 可扩展 **kwargs 参数（traffic_type, error_kind, latency_bucket 等），
     Python 加可选参数不破坏兼容性，无需提前预留 -->

```
outcome_type == "success":
    class_model_stats.success_count/fail_count 由 HealthManager 在 on_quality_good_async/on_quality_fail_async 维护
    检查是否达到进池门槛 → try_add_to_pool()
    触发淘汰检查 → evict_check(agent_class)
    内部调用 defaults_store.record_success_async(agent_class, domain, model_id)  (targets class_pool_defaults)
    内部调用 defaults_store.evaluate_and_promote_default_async(agent_class, domain)

outcome_type == "quality_fail":
    触发淘汰检查 → evict_check(agent_class)
    内部调用 defaults_store.record_fail_async(agent_class, domain, model_id)  (targets class_pool_defaults)

outcome_type == "exec_fail":
    atomic on class_model_stats: exec_fail_count += 1  (keyed by agent_class, model_id)
    （不影响 success_rate，不影响 bonus_level，不影响默认模型）
```

**方法 5: `try_add_to_pool(agent_class, model_id) -> bool`**

进池条件（同时满足）：
1. 计算 Wilson 置信下界 `pool_conf_lb`（`z = WILSON_Z`，`p=success/n`, `n=success+fail`）
2. `pool_conf_lb >= POOL_ENTRY_CONF_LB_MIN`（0.25）
3. 池未满（< `POOL_MAX_SIZE`），或可淘汰现有模型腾出位置

`pool_conf_lb` 计算：
```
pool_conf_lb = (p + z^2/(2n) - z*sqrt((p*(1-p)+z^2/(4n))/n)) / (1 + z^2/n)
```

进池操作在事务内完成（先读后写，必须 `BEGIN IMMEDIATE` 防止并发超限）：
```sql
BEGIN IMMEDIATE;
SELECT COUNT(*) FROM class_pool WHERE agent_class = ?;
-- 若 >= POOL_MAX_SIZE，先淘汰
-- 再 INSERT
COMMIT;
```

返回 True 表示成功加入。

**方法 6: `evict_check(agent_class) -> list[str]`**

触发时机：池发生变更时（新模型进池、outcome 更新后）。

淘汰规则（按优先级）：
1. 模型被标记 unable → 由 `HealthManager.report_exec_failure_async` 状态机自动转为 unable 后移出（不经过 evict_check）
2. 模型发布超过 `POOL_MODEL_MAX_AGE_DAYS`（180天）→ 移出，除非满足豁免条件：
   - `success_count >= POOL_AGE_EXEMPT_SUCCESS`（10）AND `success_rate >= POOL_AGE_EXEMPT_RATE`（80%）
3. 池满时新模型进入 → 淘汰 success_rate 最低的非默认、非豁免模型

年龄淘汰在池变更路径触发（新模型进池、outcome 更新后），不额外启用后台定时任务。

返回被淘汰的 model_id 列表。

**方法 7: `get_default(agent_class, domain) -> str | None`**

- 委托内部 `_defaults_store.lookup_default_async(agent_class, domain)`
- demo 阶段 domain 参数仅为兼容保留，内部固定映射到 `__global__`
- 若默认模型不再被 model_registry 支持 → 清除默认状态，返回 None

**方法 8: `set_user_override(agent_class, domain, model_id) -> None`**

- 委托内部 `_defaults_store.set_user_override_async(agent_class, domain, model_id)`
- demo 阶段 domain 参数仅为兼容保留，内部固定映射到 `__global__`
- 用户手动锁定默认模型，不受自动晋升/撤销影响

**方法 9: `manual_add_to_pool(agent_class, model_id) -> dict[str, str]`**（渠道2：手动添加）

与 `try_add_to_pool`（渠道1：自动统计入池）并行，两者都通向同一个 `class_pool` 表：

```
              ┌────────────────────┐
              │     class_pool     │
              └────────▲───────────┘
                       │
          ┌────────────┼────────────┐
          │                         │
  渠道1: 自动统计             渠道2: 手动添加
  try_add_to_pool()          manual_add_to_pool()
  Wilson LB ≥ 0.25            注册表校验 + 池容量检查
  + 10次试验门槛              无统计门槛
```

- 跳过 Wilson 置信度门槛，直接入池
- 校验 `model_id` 在 `MainModelPool` 注册表中存在（不存在返回 `{"status": "error", "reason": "model_not_found"}`）
- 幂等：已在池中返回 `{"status": "already_exists"}`
- 池已满（≥ `POOL_MAX_SIZE`）返回 `{"status": "error", "reason": "pool_full"}`，不触发自动淘汰
- 入池后自动初始化 `class_model_stats` 行（零值），bonus 计算给予 `POOL_BONUS_BASE_RATIO`（6%）
- 入池后模型与自动入池模型享受相同的 pool bonus、eviction、default promotion 机制

CLI 用法：
```bash
python -m route_agent pool add --class extraction --model openai:gpt-4o
```

REST API：
```
POST /pool-status/classes/{agent_class}/models
Body: {"model_id": "openai:gpt-4o"}
```

**方法 10: `manual_remove_from_pool(agent_class, model_id) -> dict[str, str]`**

- 手动从类池移除模型
- 若模型是当前默认，先清除默认状态再移除
- 不在池中返回 `{"status": "not_found"}`

CLI 用法：
```bash
python -m route_agent pool remove --class extraction --model openai:gpt-4o
```

REST API：
```
DELETE /pool-status/classes/{agent_class}/models/{model_id}
```

### 14.4 失败类型区分

```
请求 → 调用模型
  ├─ 执行失败（API 超时/限流/500/连接错误）
  │     → 重试 EXEC_FAIL_RETRY（2）次，共 3 次尝试
  │     → 3 次全失败:
  │       ├─ health_manager.report_exec_failure_async(model_id)
  │       │   → available → degraded（降权，仍可选中）
  │       │   → degraded → unable（移出池 + 告警用户）
  │       └─ 回退到候选列表中下一个模型
  │     → 不计入 fail_count，不影响 success_rate
  │     → 不影响 consecutive_success / consecutive_fail
  │     → degraded 5 分钟内无新失败 → 自动恢复
  │     → unable 后每小时探测是否恢复
  │
  └─ 执行成功 → 评估质量
       ├─ 效果好（good/fair）
       │     health_manager.on_quality_good_async(agent_class, model_id)
       │     → success_count++, consecutive_success++, consecutive_fail = 0（原子）
       │     → bonus_level = MAX(bonus_level, consecutive_success // 3)（只升不降）
       │     → penalty_level = 0（重置惩罚）
       │     ├─ 调用 defaults_store.evaluate_and_promote_default_async()：
       │     │    success_count >= DEFAULT_PROMOTION_MIN_SUCCESS（20）且 Wilson 下界领先 incumbent
       │     │    且 challenger.consecutive_success >= CHALLENGER_LEAD_STREAK（3）→ 晋升默认
       │     └─ 默认位连续成功 >= DOWNGRADE_SUCCESS_THRESHOLD（10）→ 尝试降阶
       │
       └─ 效果差（poor）
             health_manager.on_quality_fail_async(agent_class, model_id)
             → fail_count++, consecutive_fail++, consecutive_success = 0（原子）
             → 第 1 次 poor：bonus_level 不变（容忍偶发误判）
             → 第 2+ 次连续 poor：bonus_level //= 2（衰减）
             → bonus 衰减至 0 后：penalty_level = consecutive_fail // 3（累积惩罚）
             └─ consecutive_fail >= QUALITY_FAIL_REVOKE（3）→ 撤销默认
```

> **幂等与互斥保证：** 上述两条路径（执行失败 / 执行成功→质量评估）通过 §7.10 单请求状态机互斥。
> 同一 `(request_id, model_id)` 不可能同时走两条路径，也不可能重复计数。
> 调用方无需自行去重，engine 内部通过 `feedback_events` 表原子保证。

### 14.5 池排序与默认模型

**池内排序:**
- 按 `success_rate = success_count / (success_count + fail_count)` 降序
- 需 >= `MIN_TRIALS`（10次试用）才参与排名
- 未达 MIN_TRIALS 的模型排末尾，获得递增加成（6%~10%）

**默认模型晋升:**
- 生效粒度：
  - demo 阶段：`agent_class`（domain 固定 `__global__`）
  - 未来阶段：`(agent_class, domain)`（同 class 不同 domain 可有不同默认）
- 候选门槛：`class_model_stats.success_count >= DEFAULT_PROMOTION_MIN_SUCCESS`（20）
- 候选评分：Wilson 置信下界（`z = WILSON_Z`）：
  `wlb = (p + z^2/(2n) - z*sqrt((p*(1-p)+z^2/(4n))/n)) / (1 + z^2/n)`，其中 `p=success/n`, `n=success+fail`
- 切换条件（有 incumbent 默认时）：
  - `wlb(challenger) > wlb(incumbent)`
  - `challenger.consecutive_success >= CHALLENGER_LEAD_STREAK`（3，防抖）
- 同分仲裁：若 `abs(wlb(challenger)-wlb(incumbent)) < SCORE_TIER_EPSILON`，
  则 price 更低优先；price 相同取 `model_release_date` 更新的
- 作用：写入 `class_pool_defaults`，调整 `start_index` 指向该模型

**默认模型撤销:**
- `class_pool_defaults.consecutive_fail` >= `QUALITY_FAIL_REVOKE`（3）→ 撤销
  （demo 阶段按 `(agent_class, "__global__")` 追踪；未来按 `(agent_class, domain)` 追踪）
- 默认模型不再被 model_registry 支持 → 撤销

**降阶尝试:**
- 默认位连续成功 >= `DOWNGRADE_SUCCESS_THRESHOLD`（10）
- 尝试池内更便宜且分差在 `DOWNGRADE_SCORE_GAP_MAX`（0.10）以内的模型
- 先发起小流量试用（`DOWNGRADE_CANARY_RATIO`，50%）并累计样本
- 试用期命中回滚阈值（2 次 quality_fail 或 1 次 exec_fail）→ 立即回滚 + 24h 冷却
- 试用样本达标（`DOWNGRADE_TRIAL_MIN_SAMPLES`，5）后，降阶候选满足晋升门槛（`success_count >= 15`）且 Wilson 下界领先 incumbent
  且 `challenger.consecutive_success >= CHALLENGER_LEAD_STREAK`（3）→ 切换默认

### 14.6 成功案例三级查询

```
查询 1: WHERE agent_class = ? AND domain = ?     ← 精确匹配
查询 2: WHERE agent_class = ?                     ← 跨 domain 降级
查询 3: 全局 class_success_log                     ← 兜底
```

每次执行后写入 `class_success_log`（含 agent_name），作为全局备份和追踪。

### 14.7 可观测性

**查询接口:**
- `RouterEngine.list_pools_async()` — 所有池概览（class, model_count, default_model）
- `RouterEngine.inspect_pool_async(agent_class)` — 池内详情

**RouteDecision 新增字段:**
- `pool_hit: bool` — 是否命中 Class Pool
- `pool_class: str | None` — 命中的 agent_class

### 14.8 池的生命周期

```
空池 → 首次路由
  └─ 无池加分，正常选择
  └─ 执行成功后记录到 class_success_log

累积成功 → 模型达到进池门槛
  └─ pool_conf_lb >= 0.25（Wilson 置信下界）
  └─ 加入池

池内竞争 → 按 success_rate 排序
  └─ 新模型（Arena 高分）自然进入候选
  └─ 池内模型获得比例加成（6%~10%），随试用次数递增至转正

默认模型产生 → 达到候选门槛并通过 Wilson 仲裁
  └─ 普通晋升：success_count >= 20；降阶晋升：success_count >= 15
  └─ 且 challenger 连续成功 >= 3 + wlb 领先 incumbent
  └─ 通知用户，调整 start_index

持续优化 → 连续成功 >= 10
  └─ 先以 50% 流量试用更便宜模型，达标后转正，失败则快速回滚并冷却

淘汰 → 模型 unable / 模型过老 / 池满
  └─ unable: 立即移出 + 告警用户
  └─ 过老/池满: 按 success_rate 淘汰，默认模型受保护
```

### 14.9 高并发设计

#### 14.9.1 总体策略

核心原则：**让并发窗口无害，而非消灭并发窗口。**

| 层级 | 策略 | 作用 |
|------|------|------|
| 热路径 | `UPDATE … SET col = col + 1 RETURNING col` | 原子加 + 原子读回，无需事务 |
| 冷路径 | 晋升/撤销/淘汰动作必须**幂等**（CAS / 闸门字段），允许空跑 | 多请求同时触发也不会产生错误状态 |
| 兜底 | `PRAGMA journal_mode = WAL` + `PRAGMA busy_timeout = 3000` | 并发写排队等待而非直接报错 |
| 维护 | `PRAGMA wal_autocheckpoint = 1000`，低峰期 `PRAGMA wal_checkpoint(TRUNCATE)` | 防止 WAL 文件持续膨胀 |

**何时使用 `BEGIN IMMEDIATE`：** 仅当事务内必须先读再写（读到的值决定写什么）时使用。
单条 `UPDATE` 或 `UPDATE … RETURNING` 不需要显式事务。

#### 14.9.2 风险点 ①：计数器竞态

**场景：** 多请求同时更新 `class_model_stats` 的 `success_count` / `fail_count` / `consecutive_success` 等字段。

**问题：** 应用层 "SELECT → +1 → UPDATE" 三步非原子，导致 lost update。

**方案：** 单条原子 SQL + `RETURNING` 读回新值：

**注意：SQLite UPDATE SET 中，所有右侧表达式引用的是更新前的行值。**
因此 `success_count + 1`、`consecutive_success + 1`、`consecutive_fail + 1` 等偏移量是正确的——
它们补偿了"左侧已 +1 但右侧还看不到"的时序差。实现时应在代码注释中明确这一假设。

```sql
-- 成功 SQL
UPDATE class_model_stats
   SET success_count       = success_count + 1,
       consecutive_success = consecutive_success + 1,
       consecutive_fail    = 0,
       success_rate        = CAST(success_count + 1 AS REAL) / MAX(success_count + 1 + fail_count, 1),
       bonus_level         = MAX(bonus_level, (consecutive_success + 1) / 3),  -- 只升不降
       penalty_level       = 0,
       updated_at          = datetime('now')
 WHERE agent_class = ? AND model_id = ?
 RETURNING consecutive_success, bonus_level, penalty_level, success_rate;

-- 失败 SQL（含容忍机制）
UPDATE class_model_stats
   SET fail_count          = fail_count + 1,
       consecutive_fail    = consecutive_fail + 1,
       consecutive_success = 0,
       bonus_level         = CASE
                               WHEN consecutive_fail >= 1 THEN bonus_level / 2  -- 第 2+ 次：衰减
                               ELSE bonus_level                                  -- 第 1 次：容忍
                             END,
       penalty_level       = CASE
                               WHEN consecutive_fail >= 1   -- 第 2+ 次连续 poor
                                AND bonus_level / 2 = 0     -- 且 bonus 衰减后已归零
                               THEN (consecutive_fail + 1) / 3
                               ELSE 0
                             END,
       success_rate        = CAST(success_count AS REAL) / MAX(success_count + fail_count + 1, 1),
       updated_at          = datetime('now')
 WHERE agent_class = ? AND model_id = ?
 RETURNING consecutive_fail, bonus_level, penalty_level, success_rate;
```

- 数据库引擎内部完成读-改-写，不存在并发窗口
- `RETURNING` 一次拿到新值，无需额外 SELECT（SQLite 3.35+）
- 如果新值触发了晋升/撤销等冷路径操作，再单独处理（见 14.9.3）

#### 14.9.3 风险点 ②：默认晋升竞态

**前提变更：** `consecutive_success` / `consecutive_fail` 在 `class_model_stats` 表中，per `(agent_class, model_id)` 独立追踪。
每个模型的连续成功/失败互不干扰，不同模型的并发更新落在不同行，无竞态。

**竞态只发生在写 `class_pool_defaults` 时：** 两个不同模型各自独立满足默认候选门槛（普通晋升：`success_count >= 20`；降阶晋升：`success_count >= 15`，且各自 Wilson 下界领先当前默认），同时尝试成为默认。
demo 阶段默认 key 为 `(agent_class, "__global__")`；未来扩展为 `(agent_class, domain)`。

**场景拆解：**

**场景 A — 同一 challenger 的两个成功同时到达：**
```
class_model_stats 行: (scrape, model_X, success_count=19, consecutive_success=2)

请求 A: UPDATE ... SET success_count = success_count + 1, consecutive_success = consecutive_success + 1 RETURNING → (20, 3)
请求 B: UPDATE ... SET success_count = success_count + 1, consecutive_success = consecutive_success + 1 RETURNING → (21, 4)
```
两个都触发晋升，但写入 `class_pool_defaults` 时 model_id 相同 → 幂等，无害。

**场景 B — 两个不同模型同时达到晋升门槛：**
```
class_model_stats: (scrape, model_X, success_count=19, consecutive_success=2) → 请求 A 完成后 → (20,3) ✓
class_model_stats: (scrape, model_Y, success_count=23, consecutive_success=2) → 请求 B 完成后 → (24,3) ✓
```
两个模型都有资格成为 `(scrape, coding)` 的默认。

**仲裁规则：** 先比较 Wilson 下界（`WILSON_Z`）；若 `abs(Δwlb) < SCORE_TIER_EPSILON` 再比较 price；price 相同再看 `model_release_date`。

**方案：`BEGIN IMMEDIATE` + 条件仲裁（冷路径，频率极低）：**

```sql
BEGIN IMMEDIATE;
-- 1. 读取当前默认（如果有）
SELECT model_id FROM class_pool_defaults
 WHERE agent_class = ? AND domain = ? AND is_locked = 0;

-- 2. 如果无默认 → 直接插入
-- 3. 如果有默认 → 比较 challenger 与 incumbent 的 Wilson 下界、price、release_date
--    challenger 更优 → 替换；否则 → 跳过（空跑）
INSERT INTO class_pool_defaults (agent_class, domain, model_id)
VALUES (?, ?, ?)
ON CONFLICT(agent_class, domain) DO UPDATE
   SET model_id   = excluded.model_id,
       updated_at  = datetime('now')
 WHERE is_locked = 0;
COMMIT;
```

比较逻辑在应用层完成（需要从 model_registry 查 price），SQL 只负责最终写入。
`BEGIN IMMEDIATE` 保证"读当前默认 → 比较 → 写入"整个过程串行化。
如果 `changes() == 0`（被锁定或已有更优模型），当前请求空跑，无副作用。

**场景 C — 晋升与撤销同时发生：**
```
model_X: success_count 达到门槛（普通 20 / 降阶 15）且 consecutive_success 达到 3 且 wlb 领先 → 触发晋升
model_X: consecutive_fail 达到 3   → 触发撤销（另一个 domain 的反馈）
```
不冲突。`consecutive_success` 和 `consecutive_fail` 在 `class_model_stats` 中是同一行的两个字段，
但效果好时 `consecutive_fail = 0`，效果差时 `consecutive_success = 0`，互斥重置。
同一模型不可能同时满足晋升和撤销条件。

#### 14.9.4 风险点 ③：池满淘汰竞态

**场景：** 池已有 9-10 个模型，两个请求同时将不同新模型加入池 → 都看到 `count < POOL_MAX_SIZE` → 池溢出。

**本质：** 跨行的"读聚合值 → 决策 → 写"，无法用单条 `UPDATE ... RETURNING` 解决。

**频率：** 冷路径。进池条件 `pool_conf_lb >= 0.25`，两个模型同时达标概率极低。

**方案：`BEGIN IMMEDIATE` 串行化整个"检查 → 淘汰 → 插入"流程：**

```sql
BEGIN IMMEDIATE;

-- 1. 当前池大小
SELECT COUNT(*) AS cnt FROM class_pool WHERE agent_class = ?;

-- 2. 池满时，找淘汰目标：success_rate 最低的非默认、非豁免模型
--    注意：模型只要是任意 domain 的默认即全局保护（不按 domain 区分），
--    因为同一模型在不同 domain 的默认地位应一并考虑
SELECT cp.model_id
  FROM class_pool cp
  JOIN class_model_stats cms
    ON cp.agent_class = cms.agent_class AND cp.model_id = cms.model_id
  LEFT JOIN class_pool_defaults cpd
    ON cp.agent_class = cpd.agent_class AND cp.model_id = cpd.model_id
 WHERE cp.agent_class = ?
   AND cpd.model_id IS NULL                          -- 非默认
   AND NOT (cms.success_count >= 10 AND              -- 非豁免
            cms.success_rate >= 0.80)
 ORDER BY cms.success_rate ASC
 LIMIT 1;

-- 3. 淘汰（如果有目标）
DELETE FROM class_pool WHERE agent_class = ? AND model_id = ?;

-- 4. 插入新模型
INSERT OR IGNORE INTO class_pool
       (agent_class, model_id, model_release_date)
VALUES (?, ?, ?);

COMMIT;
```

**并发安全保证：**
- `BEGIN IMMEDIATE` → 事务开始即持有写锁，第二个请求排队等待
- 第二个请求拿到锁后看到的是第一个事务提交后的状态 → count 正确
- `INSERT OR IGNORE` → 同一 model_id 重复插入自动跳过（UNIQUE 约束幂等）

**边界情况：**
- 同一 model_id 重复进池 → `INSERT OR IGNORE` 自动跳过（UNIQUE 约束幂等）
- 池内默认模型数量有限（受 domain 数量约束，通常 2-3 个），淘汰目标充足

#### 14.9.5 风险点 ④：默认模型过载

**场景：** demo 阶段默认模型按 `agent_class` 生效，可能被该 class 的所有请求命中，RPM/并发迅速打满。更危险的是雪崩效应：默认模型限流后，所有请求同时涌向同一个 fallback → 连锁过载。

**核心原则：** 在选择阶段做负载分散，全程不引入共享写状态，不会为了抗雪崩制造新的雪崩。

##### 14.9.5.1 层级 1：利用率感知（Redis 只读 + 本地缓存）

每次请求在决定用默认模型之前，从 Redis 读取当前利用率。

**语义定义：**
- `rpm_ratio`: 过去 60s 滑动窗口内请求数 / `max_requests_per_minute`
- `conc_ratio`: 瞬时 in-flight（`normal_conc + esc_conc`）/ `max_concurrency`
- `is_limited`: 进程内 TTL 标记，收到 429 后置 True，`RECENT_LIMITED_TTL_S`（5s）后自动过期

**本地 TTL 缓存（降 Redis 热点）：**
- 对同一 `model_id` 的 utilization 做 `UTIL_CACHE_TTL_MS`（150ms）短缓存
- 进程内 `dict[str, tuple[float, ModelUtilization]]`，不算共享写状态
- 150ms 内多个请求共用同一缓存值，Redis QPS 降一个数量级

##### 14.9.5.2 层级 2：RPM 和并发独立判定 + 二次曲线概率跳过

RPM 和并发的含义不同，分开判定，任一触发即跳过：

| 指标 | 含义 | 阈值（低/高） | 原因 |
|------|------|-------------|------|
| `rpm_ratio` | 滑动窗口吞吐趋势 | 0.70 / 0.90 | 有缓冲余地 |
| `conc_ratio` | 瞬时请求堆积 | 0.60 / 0.85 | 更敏感，堆积意味着即时变慢 |

**概率跳过（二次曲线，降临界抖动）：**

```
x = (ratio - LOW) / (HIGH - LOW)    # 归一化到 [0, 1]
p_skip = x ^ DEFAULT_SKIP_POWER     # 默认 2.0，二次曲线
```

| peak_ratio | 线性 P(skip) | 二次 P(skip) |
|------------|-------------|-------------|
| 0.70 | 0.00 | 0.00 |
| 0.75 | 0.25 | 0.06 |
| 0.80 | 0.50 | 0.25 |
| 0.85 | 0.75 | 0.56 |
| 0.90 | 1.00 | 1.00 |

二次曲线在 0.70-0.80 区间跳过概率显著更低，减少"刚过阈值就频繁跳过"的抖动。
`DEFAULT_SKIP_POWER` 可调参，线上观察到抖动时改这一个值即可。

**判定逻辑（归属 `selector.py`，作为 `ModelSelector` 的私有方法 `_should_skip_default`）：**
```
_should_skip_default(util):
    if util.is_limited → True
    rpm_skip = _check_skip(util.rpm_ratio, RPM_UTIL_LOW, RPM_UTIL_HIGH)
    conc_skip = _check_skip(util.conc_ratio, CONC_UTIL_LOW, CONC_UTIL_HIGH)
    return rpm_skip or conc_skip
```

##### 14.9.5.3 层级 3：加权分散（防雪崩）

跳过默认后，从池内加权随机选替代模型。权重 = 成功率 × 剩余容量。

**改进点（vs 简单 fallback 到第二名）：**

1. **Beta 平滑成功率**（避免低样本模型被吸走）：
   ```
   rate = (success_count + BETA_PRIOR) / (success_count + fail_count + 2 * BETA_PRIOR)
   ```
   `BETA_PRIOR = 2.0`（Beta(2,2) 先验），小样本更保守，样本多了趋近真实值。

2. **剩余容量加权**：
   ```
   headroom = max(1.0 - peak_ratio, FALLBACK_MIN_HEADROOM)
   weight = rate * headroom
   ```
   负载高的模型权重自然降低，请求分散到多个模型。

3. **剔除近期 429 的候选**：
   `is_limited == True` 的模型直接排除，避免反复撞限流。

##### 14.9.5.4 层级 4：全局兜底

池内所有模型都高负载时，回退到正常 selector 流程（无 pool bonus）。

**关键：** 把已知过载的模型加入 exclude 集合传给全局选择器，避免绕一圈又选回去：
```
overloaded_ids = {model_id for model in pool_entries
                  if is_limited or peak_ratio >= RPM_UTIL_HIGH}
→ 加入 constraints.exclude_models
```

##### 14.9.5.5 升阶与过载保护的交互

**核心矛盾：** 过载保护想避开忙的模型，升阶想用更强的模型，而更强的模型往往更忙。

**解决：升阶请求带优先级，不同优先级的过载保护行为不同。**

| priority | is_limited | rpm/conc 0.70-0.90 | rpm/conc > 0.90 | rpm/conc > 0.95 |
|----------|-----------|-------------------|----------------|----------------|
| `normal`（自动升阶） | 拦截 | 概率跳过 | 必定跳过 | 必定跳过 |
| `elevated`（人工反馈） | 拦截 | 放行 | 放行 | 拦截 |
| `forced`（用户指定） | 拦截 | 放行 | 放行 | 放行 |

**升阶对 class_pool 统计的影响：**
```
人工反馈 "poor" → fail_count++, consecutive_fail++, consecutive_success=0
  → bonus_level = 0, penalty_level = consecutive_fail // 3
  → consecutive_fail >= 3 → 撤销默认
  → 触发升阶（priority="elevated"）

升阶后模型执行成功且质量好 → success_count++, consecutive_success++
  → 普通晋升：success_count >= 20；降阶晋升：success_count >= 15
  → 且 wlb 领先 incumbent 且 consecutive_success >= 3 → 可能晋升为新默认
  → 新默认初始负载低，不触发过载保护
```

##### 14.9.5.6 升阶并发隔离（防升阶雪崩）

**场景：** 默认模型变慢 → 多个用户同时触发升阶 → 全部涌向同一个高阶模型 → 高阶模型也被打爆 → 连锁震荡。

**方案：** 并发计数器按流量类型隔离，升阶流量有独立上限。

**Redis key 拆分：**
```
route_agent:conc:normal:{model_id}   → 正常流量计数器
route_agent:conc:esc:{model_id}      → 升阶流量计数器
```

**限制规则：**
```
escalation_cap = max_conc * ESCALATION_CONC_RATIO    # 默认 30%

正常流量 → INCR normal → 检查 (normal + esc) < max_conc
升阶流量 → 先检查 esc < escalation_cap → 再检查 (normal + esc) < max_conc → INCR esc
```

效果：升阶流量最多占用 30% 并发槽位，70% 留给正常流量。即使 50 个升阶请求同时到达，最多 `max_conc * 0.30` 个能进去。

**计数器泄漏防护：**
- Redis key 设置 EXPIRE 300s，进程崩溃未 DECR 也会自动过期
- 应用层 `try/finally` 保证 DECR

**cap 判断的并发窗口：**
- 非原子的"读 cap → INCR"有短暂窗口，可能短暂超限
- 超限量 ≤ 并发请求数（几个到十几个），可接受
- 需要严格精确时，升级为 Redis Lua 脚本原子化 check-and-increment

##### 14.9.5.7 升阶溢出处理（目标模型 capped 时）

v1 简化方案（详见 7.7 节）：目标 capped → 尝试同 tier 替代候选 → 全部 capped → 直接降级（不等待）。

v2 可引入 jittered backoff 等待机制，观察 v1 实际溢出频率后决定。

##### 14.9.5.8 并发安全总结

| 操作 | 类型 | 风险 |
|------|------|------|
| `get_utilization` | Redis 读 + 进程内缓存 | 无 |
| `should_skip_default` | 纯函数，per-request 独立 | 无 |
| `select_pool_fallback` | 纯函数 + Redis 读 | 无 |
| `mark_limited` | 进程内存写，5s TTL | 不跨进程，无竞态 |
| `INCR/DECR` 并发计数 | Redis 原子操作 | 无 |
| `is_escalation_capped` → `INCR` | 非原子窗口 | 可接受，可选 Lua 升级 |
| `asyncio.sleep`（等待） | 非阻塞 | 不占线程，只占协程 |
| 等待队列计数 | 进程内 asyncio.Lock | 无跨进程竞态 |

整个过载保护 + 升阶隔离链路没有引入新的跨进程共享写操作。

##### 14.9.5.9 限流降级策略（三级）

**默认语义：Fail-Closed-ish（降级但不放弃保护）**

| 级别 | 实现 | 触发条件 | 保护能力 |
|------|------|---------|---------|
| L1 | `RedisRateLimiter` | Redis 可用 | 全局一致性，跨进程/跨实例精确 |
| L2 | `InMemoryRateLimiter` | Redis 不可用 + `mode=auto` + `fail_strategy=degrade` | 单进程精确，多进程各自独立 |
| L3 | `NoOpRateLimiter` | 仅 `--rate-limit=off` 显式指定 | 无保护，依赖 provider 自身 429 兜底 |

**`fail_strategy=fail_fast`（仅 `mode=auto`）：**
- Redis 不可用时不进入 L2，直接报错退出
- 适用于需要严格保证“全局限流语义不降级”的部署

**InMemory 降级时的行为：**
- `get_utilization_async` → 从进程内 deque/counter 计算，单进程精确
- `should_skip_default` → 正常工作（基于进程内利用率）
- `is_escalation_capped_async` → 正常工作（基于进程内并发计数）
- 429 标记 → 与 Redis 版无差异（本身就是进程内状态）
- 多进程时 log warning: `"InMemoryRateLimiter is per-process; for global rate limits configure Redis."`

**NoOp 降级时的行为（仅显式 `--rate-limit=off`）：**
- 所有限流检查返回 False，过载保护完全失效
- 适用场景：开发调试、单模型测试

##### 14.9.5.10 v2 预留：延迟维度

`ModelUtilization.latency_ratio`（默认 0.0）预留给 v2。

当模型响应变慢但未触发 429 时（隐蔽的过载前兆），`latency_ratio = p95_latency / expected_latency`。
`peak_ratio` 计算从 `max(rpm, conc)` 改为 `max(rpm, conc, latency)` 即可，上层代码零改动。

#### 14.9.6 风险点 ⑤：SQLite 写性能（待细化）

**场景：** 高并发写入超出 SQLite 单写者串行能力。

**方案方向（按并发级别分层）：**

| 并发级别 | 方案 |
|---------|------|
| < 50 QPS | SQLite WAL + `busy_timeout` + 短事务（当前方案足够） |
| 50-500 QPS | 热路径计数器用 Redis `INCR`，异步批量刷回 SQLite |
| > 500 QPS | PostgreSQL 替换 SQLite |

### 14.10 运维 Runbook

| 任务 | 调度方 | 频率 | 触发点 | 失败处理 |
|------|--------|------|--------|----------|
| Unable 模型探测 | `RouterEngine` 后台 `asyncio.Task` | 每 `UNABLE_PROBE_INTERVAL_S`（1h） | 引擎初始化时启动 | 探测失败 → 保持 unable，下次再试；任务异常 → log error + 自动重启循环 |
| Class 审核队列处理 | 人工（控制台/脚本） | 按需（建议每周） | `class_review_queue` 出现高频 pending | 审核失败/中断 → 保留 pending，不影响在线路由 |
| `class_success_log` / `feedback_events` / `downgrade_trials` 清理 | 手动维护（默认关闭自动） | 按需 | 运维脚本或人工触发 | 执行失败 → log warning；跳过即可，不影响在线路由 |
| Class Pool 年龄淘汰 | 在线路径（`evict_check`） | 事件触发 | 进池 / outcome 更新后 | 淘汰失败 → log warning，池可能暂时超龄但不影响功能 |
| WAL checkpoint | SQLite 自动（`wal_autocheckpoint=1000`） | 每 1000 页写入 | 自动 | 若 WAL 持续膨胀 → 低峰期手动 `PRAGMA wal_checkpoint(TRUNCATE)` |
| WAL 手动 checkpoint | 运维脚本 / cron | 每日低峰期（可选） | 外部触发 | 失败 → log warning，WAL 继续膨胀但不影响正确性 |

**告警建议（接入 monitoring 模块后实现）：**
- Unable 模型数量 > 总模型数 30% → 告警
- `class_success_log` 行数 > 100万 → 告警（demo 默认无自动清理，提示人工维护）
- `class_review_queue` 中 `pending` 且 `hit_count >= CLASS_REVIEW_MIN_HITS` 的条目 > 50 → 告警（类别治理积压）
- 24h 内 `downgrade_rollback / downgrade_start > 50%` → 告警（降阶策略过激或候选质量不足）
- 事件 `rate_limiter_degraded_to_inmemory` 触发 → 立即告警（限流语义发生降级）
- 事件 `rate_limiter_recovered_to_redis` 触发 → 恢复通知
- WAL 文件 > 100MB → 告警
- 后台任务连续 3 次循环失败 → 告警
