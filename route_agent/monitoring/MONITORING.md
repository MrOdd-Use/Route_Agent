# Monitoring 模块技术说明书（v0.2，含已实现最小骨架）

## 1. 文档摘要
本文档定义 `route_agent.monitoring` 的最小可落地设计，用于记录路由决策、提供统计查询、输出阈值告警，并与 `app/service.py` + `router_engine` 集成。  
当前版本已实现 SQLite + sync/async 的最小监控闭环（record/recent/stats）。

## 1.1 当前已实现范围（v0.2）
1. `record_decision` / `record_decision_async`
2. `get_recent_decisions` / `get_recent_decisions_async`
3. `get_stats` / `get_stats_async`
4. `MonitoringConfig` 与环境变量读取
5. SQLite 表 `monitoring_decisions` 与基础聚合统计

## 2. 建设目标
1. 建立统一的路由决策事件模型。
2. 支持历史落盘与近实时查询。
3. 支持基础聚合指标（24h、7d、all）。
4. 支持阈值告警钩子（回调 + 日志）。
5. 不影响当前路由主流程稳定性与返回契约。
6. 支持调用方到执行完成的实时生命周期可视化（caller -> agent -> model -> done/failed）。
7. 支持模型实时并发与执行 token 消耗观测。

## 3. 非目标
1. 不改造路由策略本身。
2. 不引入 RL/多臂老虎机在线学习。
3. 不实现成本精确核算（当前 `cost` 无稳定来源）。
4. 不做外部监控系统 exporter（Prometheus/OTel 留后续）。

## 4. 模块边界与协作关系
1. `monitoring`：记录与观测，不做选路决策。
2. `app/service.py` / `router_engine`：产出决策并调用 `monitoring.record_decision`。
3. `model_registry`：提供模型供给与同步状态，监控只消费其结果字段。
4. `task_analyzer`：提供任务分析信号，监控只记录摘要字段。

结论：该模块与其他模块不冲突，属于旁路观测层。

## 5. 对外接口（Public API）
1. `record_decision(event, *, config=None, alert_callback=None) -> int`
2. `record_decision_async(event, *, config=None, alert_callback=None) -> int`
3. `get_stats(*, config=None, windows=("24h","7d","all")) -> dict`
4. `get_stats_async(*, config=None, windows=("24h","7d","all")) -> dict`
5. `get_recent_decisions(*, config=None, limit=50, source=None, since_hours=None) -> list[dict]`
6. `get_recent_decisions_async(*, config=None, limit=50, source=None, since_hours=None) -> list[dict]`
7. `start_execution(event, *, config=None) -> str`
8. `start_execution_async(event, *, config=None) -> str`
9. `end_execution(event, *, config=None) -> None`
10. `end_execution_async(event, *, config=None) -> None`
11. `list_active_executions(*, caller_id=None, agent_name=None, model_id=None, limit=200) -> list[dict]`
12. `get_model_concurrency(*, top_n=20) -> list[dict]`

## 6. 核心类型定义
### 6.1 RouteDecisionEvent
建议字段：
1. `source` (`main` | `router_engine`)
2. `agent_name`, `agent_class`, `domain`
3. `task_hash`, `task_length`（不存原文）
4. `model_used`, `provider`
5. `routing_reason`
6. `pool_hit`, `pool_class`
7. `registry_error_count`, `registry_error_providers`
8. `skipped_provider_count`, `skipped_providers`
9. `analysis_complexity`
10. `sync_source`, `sync_performed`, `snapshot_version`
11. `metadata`（扩展字段）

### 6.2 MonitoringConfig
1. `enabled`
2. `db_path`
3. `retention_days`
4. `alert_rules`

### 6.3 AlertEvent
1. `rule_key`
2. `status` (`firing` / `resolved`)
3. `window`
4. `value`
5. `threshold`
6. `sample_size`
7. `generated_at`

### 6.4 ExecutionStartEvent
建议字段：
1. `execution_id`
2. `caller_id`
3. `agent_name`
4. `request_id`
5. `model_id`
6. `provider`
7. `started_at`
8. `metadata`（可选扩展）

### 6.5 ExecutionEndEvent
建议字段：
1. `execution_id`
2. `ended_at`
3. `status`（见 `ExecutionStatus`）
4. `token_input`
5. `token_output`
6. `token_total`
7. `error_type`
8. `error_detail`

### 6.6 ExecutionStatus
`running | success | failed | timeout | cancelled`

### 6.7 执行态主键与 token 口径
1. 调用方聚合主键使用 `caller_id + agent_name`。
2. token 仅统计执行模型消耗，不混入 task analyzer token。
3. 展示与存储均使用 `input/output/total` 三项。

## 7. 存储设计（SQLite 先行）
数据库默认路径：`data/route_agent_monitoring.db`

### 7.1 表 monitoring_decisions
记录全部决策观测字段，建议索引：
1. `created_at`
2. `(source, created_at)`
3. `model_used`
4. `provider`

### 7.2 表 monitoring_alert_state
维护告警状态机，字段建议：
1. `rule_key`（主键）
2. `is_firing`
3. `last_value`
4. `last_checked_at`
5. `last_emitted_at`

### 7.3 表 monitoring_executions
记录执行生命周期事实数据，建议核心字段：
1. `execution_id`（主键）
2. `caller_id`
3. `agent_name`
4. `request_id`
5. `model_id`
6. `provider`
7. `started_at`
8. `ended_at`
9. `status`
10. `token_input`
11. `token_output`
12. `token_total`
13. `error_type`
14. `error_detail`
15. `created_at`
16. `updated_at`

### 7.4 表 monitoring_active_executions
记录当前活跃执行集合，建议核心字段：
1. `execution_id`（主键）
2. `caller_id`
3. `agent_name`
4. `model_id`
5. `provider`
6. `started_at`
7. `last_heartbeat_at`

### 7.5 表 monitoring_model_concurrency
记录模型当前并发，建议核心字段：
1. `model_id`（主键）
2. `inflight_count`
3. `updated_at`

### 7.6 并发计数规则
1. `start_execution` 时对对应 `model_id` 执行 `inflight + 1`。
2. `end_execution` 时对对应 `model_id` 执行 `inflight - 1`。
3. `inflight_count` 下限为 0，禁止出现负值。

### 7.7 超时收敛规则
1. 当执行超过 5 分钟无 `end_execution` 事件时，自动判定为 `timeout`。
2. 超时任务从活跃表移除，并回收并发计数。
3. 超时任务写回 `monitoring_executions.status = timeout`。

### 7.8 保留策略
1. 默认保留 30 天。
2. 每次写入后执行过期清理。

## 8. 统计口径
固定窗口：`24h`、`7d`、`all`

每个窗口输出：
1. `total_decisions`
2. `no_model_count` / `no_model_rate`
3. `registry_error_decision_count` / `registry_error_decision_rate`
4. `skipped_provider_decision_count` / `skipped_provider_decision_rate`
6. `source_counts`
7. `top_models`
8. `pool_hit_rate`（有数据时）
9. `active_execution_count`
10. `concurrency_by_model`
11. `token_input/token_output/token_total`（1m/5m/24h）
12. `execution_success_rate`
13. `execution_timeout_count`

## 9. 告警规则（MVP）
窗口固定为 24h，最小样本 20：

1. `no_model_rate >= 0.15`
2. `registry_error_decision_rate >= 0.30`
3. `skipped_provider_decision_rate >= 0.40`

输出方式：
1. 默认日志输出。
2. 可选 `alert_callback(AlertEvent)`。
3. 回调异常只记录 warning，不影响主流程。

## 10A. 实时可视化与 Dashboard
1. 独立服务入口：`python -m route_agent.monitoring.web`。
2. 默认端口：`8765`。
3. 路径规范：`/monitor/*`。
4. 页面实时策略：SSE（事件触发 + 心跳）。
5. 页面必须展示：
   1. 按 `caller_id+agent_name` 的活跃执行流（caller -> agent -> model -> status）。
   2. 每个模型当前并发（in-flight）。
   3. 执行 token 消耗（input/output/total）。
   4. 执行完成后从活跃区退出，进入历史区保留最近记录。

## 10. 接入方案
### 10.1 app/service.py 接入
1. 路由完成后构造 `RouteDecisionEvent`。
2. `monitoring_enabled` 为真时调用记录函数。
3. 监控写入失败只打日志，不抛出到调用方。
4. 默认关闭，通过参数或环境变量开启。
5. 执行阶段必须统一走 `start_execution/end_execution` wrapper 埋点。

### 10.2 router_engine 预留接入
1. 在 `route_async` 决策后构造同结构事件。
2. 补充 `pool_hit/pool_class/agent_class/domain`。
3. 复用同一 monitoring API。
4. 执行阶段同样使用统一 wrapper 记录开始/结束事件。
5. 监控链路故障不影响路由主流程与模型执行。

## 11. 配置约定
1. `ROUTE_AGENT_MONITORING_ENABLED`，默认 `0`
2. `ROUTE_AGENT_MONITORING_DB_PATH`，默认 `data/route_agent_monitoring.db`
3. `ROUTE_AGENT_MONITORING_RETENTION_DAYS`，默认 `30`
4. `ROUTE_AGENT_MONITORING_WEB_HOST`，默认 `127.0.0.1`
5. `ROUTE_AGENT_MONITORING_WEB_PORT`，默认 `8765`
6. `ROUTE_AGENT_MONITORING_SSE_HEARTBEAT_SECONDS`，默认 `15`
7. `ROUTE_AGENT_MONITORING_EXECUTION_TIMEOUT_SECONDS`，默认 `300`

优先级：函数参数 > 环境变量 > 默认值。

## 12. 测试与验收
### 12.1 测试项
1. 事件写入（sync/async）成功。
2. `get_recent_decisions(limit=50)` 默认行为正确。
3. `get_stats` 三窗口口径正确。
4. retention 清理生效。
5. 告警状态变化触发 firing/resolved。
6. callback 异常不影响主流程。
7. `run_route_agent` 默认不开启监控。
8. 开启后可写入监控且不改变原 payload 结构。
9. `start_execution/end_execution` 可正确写入执行生命周期。
10. 模型并发计数在并发 start/end 下保持准确且不为负。
11. 无结束事件可在 5 分钟后自动 timeout 并回收并发。
12. SSE 可持续推送：初始快照、增量事件、心跳与断线重连。
13. 活跃区退出逻辑正确：执行完成后退出活跃区并进入历史区。

### 12.2 验收标准
1. 不破坏现有 `route_agent/tests/core/test_app_service.py`。
2. `route_agent/monitoring/tests/test_monitoring_module.py` 从 xfail 转为真实通过。
3. 所有监控失败场景不影响路由主流程返回。
4. 页面可实时看到 `caller_id+agent_name` 分配到的模型与执行状态流。
5. 页面可实时看到每个模型并发与 token 消耗数据。
6. 并发计数准确、超时自动回收、活跃区退出逻辑正确。

## 13. 风险与应对
1. 写库开销：默认关闭，支持灰度启用。
2. 数据膨胀：30 天保留 + 索引控制。
3. 隐私风险：不存任务原文，只存 hash 与长度。
4. 误报风险：滑窗 + 最小样本 + 状态机去抖。

## 14. 后续演进（Phase 2+）
1. 增加 PostgreSQL 后端适配。
2. 增加 Prometheus/OTel exporter。
3. 增加成本与延迟指标（待稳定数据源）。
4. 在数据闭环稳定后评估 contextual bandit，而非直接 RL。

## 15. 默认假设（已锁定）
1. 双轨兼容（`app/service.py` + `router_engine`）。
2. SQLite 先行。
3. 同步+异步双接口。
4. 默认保留 30 天。
5. 默认不存 task 原文。
6. 默认 recent limit = 50。
7. 成本监控本轮不做。
8. 监控默认关闭，显式开启。
9. 调用方聚合键使用 `caller_id+agent_name`。
10. 并发定义为实时 in-flight。
11. token 仅执行模型，按 `input/output/total` 口径。
12. 超时阈值为 5 分钟。
13. 首版内网无鉴权。

## 16. 变更记录
1. v0.1（本次增补）：
   1. 新增实时执行态可视化需求（caller -> agent -> model -> done/failed）。
   2. 新增 execution 生命周期 API 与事件类型。
   3. 新增执行态存储表与并发计数规则。
   4. 新增 SSE Dashboard 规范与展示要求。
   5. 新增执行态测试与验收标准。
