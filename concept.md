# Part E：背景知识与概念解析

> 用途：当你在 `questions.md` 里遇到术语（Redis、SQLite、幂等、Canary、Wilson 下界等）时，可在本文件直接查概念、实现口径与工程边界。

## E1. Redis 核心知识点

### E1.1 Redis 是什么
Redis 是以内存为主的数据结构数据库，典型优势是低延迟与高吞吐。命令处理模型通常是“单线程执行命令 + I/O 多路复用”，所以单条命令天然原子；是否持久化是可选项（RDB/AOF）。

- 内存优先：读写快，但要关注内存上限与淘汰策略。
- 单线程命令模型：避免了同一实例内命令级别的锁竞争。
- I/O 多路复用：并发连接多时，网络层效率更好。
- 持久化可选：可做缓存、限流计数、也可落盘恢复。

本项目关联：Q05、Q06、Q30、Q63。

### E1.2 String + INCR/DECR（计数器）
`INCR/DECR` 常用于并发计数、重试计数、漏斗状态。单条命令原子，适合高并发下的轻量计数。

原子性原理：
- Redis 单线程串行执行命令，同一 key 的 `INCR` 不会被另一条命令打断。
- 但“多命令组合”不是天然原子（例如 `GET` 后 `INCR`），要用 Lua 或事务脚本化。

典型命令：
```text
INCR route_agent:conc:normal:modelA
DECR route_agent:conc:normal:modelA
EXPIRE route_agent:conc:normal:modelA 300
GET route_agent:conc:normal:modelA
```

TTL 防泄露：
- 若请求异常退出没走 `DECR`，TTL 可避免“并发计数永久卡住”。
- TTL 过短会误放行，过长会误伤恢复时延。

| 场景 | 优势 | 风险 |
| --- | --- | --- |
| 并发 in-flight 计数 | O(1) 命令、成本低 | 崩溃路径可能泄露计数 |
| 短窗计数 | 快速、简单 | 无法表达窗口内分布 |

本项目关联：Q07、Q58、Q64、Q67。

### E1.3 Sorted Set（ZSET）+ 滑动窗口限流
ZSET 的 `score=timestamp`，可做时间窗裁剪，适合 RPM/RPD 限流。

60 秒 RPM 命令序列（示例）：
```text
ZADD route_agent:rpm:modelA 1730000000 "1730000000:uuid"
ZREMRANGEBYSCORE route_agent:rpm:modelA 0 (1729999940)
ZCARD route_agent:rpm:modelA
EXPIRE route_agent:rpm:modelA 120
```

86400 秒 RPD 命令序列（示例）：
```text
ZADD route_agent:rpd:modelA 1730000000 "1730000000:uuid"
ZREMRANGEBYSCORE route_agent:rpd:modelA 0 (1729913600)
ZCARD route_agent:rpd:modelA
EXPIRE route_agent:rpd:modelA 90000
```

固定窗口 vs 滑动窗口：

| 方案 | 精度 | 边界突刺 | 成本 |
| --- | --- | --- | --- |
| 固定窗口 | 中 | 高（窗口切换可瞬时翻倍） | 低 |
| 滑动窗口（ZSET） | 高 | 低 | 中 |

实现 RPM/RPD：
- RPM：窗口 60s，防短时突刺。
- RPD：窗口 86400s，防日配额与成本失控。
- 工程上常与并发计数并用，形成三维保护。

本项目关联：Q05、Q06、Q62、Q63。

### E1.4 TTL（Key 生命周期）
TTL 负责 key 生命周期治理，避免计数类 key 无界增长。

常见命令：
```text
EXPIRE mykey 120
PEXPIRE mykey 120000
TTL mykey
PTTL mykey
```

窗口对齐建议：
- RPM 60s 窗口：TTL 常设 2x 左右（如 120s）。
- RPD 86400s 窗口：TTL 可稍大于窗口（如 90000s）。
- 并发 key：TTL 要覆盖“最长请求耗时 + 保护余量”。

风险：
- 过短：窗口未结束 key 已过期，导致低估用量。
- 过长：泄露计数恢复慢，系统“自我惩罚”过久。

本项目关联：Q06、Q07、Q66。

### E1.5 Lua 脚本（原子操作）
`EVAL` 可把多命令打包成单次原子执行，适合“裁剪 + 计数 + 判断 + 写入”这一类 gate 逻辑。

`EVAL` 语义：
- 在 Redis 内一次执行完整脚本，中间不被其他命令插入。
- 适合减少竞态与网络往返，但脚本逻辑要短小。

原子限流示例（滑窗 gate）：
```lua
-- KEYS[1]=zset_key
-- ARGV[1]=now, ARGV[2]=window_sec, ARGV[3]=limit, ARGV[4]=member, ARGV[5]=ttl_sec
local k = KEYS[1]
local now = tonumber(ARGV[1])
local win = tonumber(ARGV[2])
local lim = tonumber(ARGV[3])
local member = ARGV[4]
local ttl = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', k, 0, now - win)
local cnt = redis.call('ZCARD', k)
if cnt >= lim then
  redis.call('EXPIRE', k, ttl)
  return {0, cnt}
end
redis.call('ZADD', k, now, member)
redis.call('EXPIRE', k, ttl)
return {1, cnt + 1}
```

优劣边界：
- 优势：原子性好、RTT 少。
- 边界：脚本复杂会拖慢 Redis 主线程；不适合长逻辑。

本项目关联：Q06、Q07、Q58、Q63。

### E1.6 Pipeline（流水线）
Pipeline 把多条命令批量发给 Redis，减少 RTT，但不提供原子性。

RTT 对比（示意）：

| 场景 | 3 条命令网络往返 |
| --- | --- |
| 非 Pipeline | 3 RTT |
| Pipeline | 1 RTT |

与 Lua 的区别：
- Pipeline：提速，不原子。
- Lua：原子，也能降 RTT，但需控制脚本复杂度。

适用场景：
- 读多写多但可容忍微小竞态时，优先 Pipeline。
- 必须同一时刻一致判断时，优先 Lua。

本项目关联：Q06、Q63、Q66。

### E1.7 热 key 问题
热 key 指访问过度集中到少数 key，导致单 key CPU/网络成为瓶颈。

常见原因：
- 爆款模型被集中命中。
- 所有请求共享同一计数 key。
- 高频轮询同一 utilization key。

缓解策略：

| 策略 | 机制 | 代价 |
| --- | --- | --- |
| 本地短缓存 | 减少重复读 Redis | 新鲜度下降 |
| Key 分片 | 写扩散到多个 key | 读汇总复杂 |
| Pipeline | 降 RTT | 非原子 |
| Lua 原子脚本 | 降竞态 + 降 RTT | 主线程压力更集中 |
| 近似计数器 | 降维护成本 | 精度损失 |

本项目关联：Q01、Q33、Q63、Q66。

### E1.8 可用性与降级策略
限流层常见四级模式：Redis / InMemory / Off / Auto。

| 模式 | 一致性 | 可用性 | 典型用途 |
| --- | --- | --- | --- |
| Redis | 跨进程较好 | 依赖外部 Redis | 生产主模式 |
| InMemory | 单进程内一致 | 高（无外部依赖） | 降级保底 |
| Off | 无保护 | 最高 | 测试/离线 |
| Auto | 按策略切换 | 高 | 生产默认建议 |

降级代价：
- Redis -> InMemory 后，多实例场景会低估全局利用率。
- Off 模式可能带来配额失控或拥塞放大。

本项目的 `fail_fast` vs `degrade`：
- `fail_fast`：Redis 不可用直接失败，语义严格。
- `degrade`：自动切 InMemory，优先可用性。

本项目关联：Q30、Q65、Q67。

### E1.9 持久化（RDB/AOF）
Redis 持久化常见两种：RDB（快照）与 AOF（追加日志）。

| 维度 | RDB | AOF |
| --- | --- | --- |
| 原理 | 周期快照 | 记录写命令 |
| 恢复点 | 可能丢最近窗口数据 | 通常更接近实时 |
| 体积 | 较小 | 较大 |
| 恢复速度 | 快 | 取决于日志大小 |

对本项目影响：
- 限流计数本质是“短期控制状态”，通常不追求强持久恢复。
- 若 Redis 重启丢短窗数据，系统可通过保守阈值 + 上层降级策略兜底。

本项目关联：Q30、Q33、Q65。

## E2. SQLite 事务与锁

### E2.1 锁模型
SQLite 锁状态常按五态理解：
`UNLOCKED -> SHARED -> RESERVED -> PENDING -> EXCLUSIVE`

要点：
- 多读可并发（SHARED）。
- 写入最终要 EXCLUSIVE。
- 单 writer 限制始终存在，写高并发场景必须控写放大。

本项目关联：Q01、Q02、Q69。

### E2.2 WAL
WAL（Write-Ahead Logging）核心流程：
1. 写事务先写 WAL 文件。
2. 读事务读快照，不必阻塞写。
3. Checkpoint 把 WAL 合并回主库。

WAL vs 传统 journal：

| 维度 | WAL | Rollback Journal |
| --- | --- | --- |
| 读写互阻 | 更少 | 更明显 |
| 并发读体验 | 更好 | 一般 |
| 写写冲突 | 仍存在 | 仍存在 |
| 维护点 | 需 checkpoint | 相对简单 |

边界：WAL 改善读写互阻，不会把写并发变成线性扩展。

本项目关联：Q02、Q32、Q69。

### E2.3 busy_timeout
`busy_timeout` 让“锁冲突立即失败”变成“有限等待后失败”。

实践建议：
- 常见起点：`3000ms`（按负载调整）。
- 必须与上游超时预算联动，避免把失败换成不可接受的长尾延迟。
- 通常与 WAL 一起配：减少瞬时 `database is locked`。

本项目关联：Q02、Q03、Q69。

### E2.4 BEGIN IMMEDIATE 事务
三种常见起始方式：

| 方式 | 起始语义 | 适用场景 |
| --- | --- | --- |
| `BEGIN`/`DEFERRED` | 延迟到写时才争锁 | 普通读写混合 |
| `BEGIN IMMEDIATE` | 事务开始即争写锁 | check-then-act 强一致 |
| `BEGIN EXCLUSIVE` | 更激进独占 | 极少数强控制场景 |

`BEGIN IMMEDIATE` 价值：
- 解决 check-then-act 竞态（先查再写的并发穿透）。
- 将不可预测冲突提前到事务入口，失败语义更清晰。

本项目关联：Q03、Q68、Q69。

## E3. 高并发编程基础

### E3.1 锁竞争（Lock Contention）
识别方法：
- 观察同一资源等待时间是否随并发非线性上升。
- 错误是否集中在 timeout / locked / 429。
- P95/P99 是否在高峰显著恶化。

缓解策略：

| 策略 | 核心做法 |
| --- | --- |
| 减少写频率 | 批量写、聚合写、采样写 |
| 缩短持锁时间 | 事务内禁止网络 IO/重计算 |
| 读写分离 | 热路径只读、冷路径写 |
| 无锁化 | 原子单语句替代读改写 |
| 分区分片 | 热点 key/行按维度拆分 |

本项目关联：Q01、Q33、Q69。

### E3.2 幂等性（Idempotency）
定义：同一语义事件重复提交，最终状态只改变一次。

实现方式：

| 方式 | 典型手段 | 备注 |
| --- | --- | --- |
| unique 约束 | `(request_id, model_id, event_type)` 唯一键 | 最常用 |
| 幂等键表 | 先写去重记录再执行业务 | 便于审计 |
| 乐观锁 | version/compare-and-swap | 需重试策略 |
| 自然幂等 | 重复执行结果天然一致 | 场景有限 |

`at-least-once` vs `exactly-once`：
- 工程上多数系统采用“至少一次投递 + 幂等消费”。
- exactly-once 成本高，跨系统实现复杂。

本项目关联：Q04、Q68、Q70。

### E3.3 重试风暴（Retry Storm）
触发路径（典型）：
`短时故障 -> 超时/429 -> 客户端重试 -> 压力更高 -> 更多失败`

缓解：指数退避 + 抖动（jitter）
- 指数退避：`base * 2^k`。
- 抖动：在退避基础上随机化，避免“同一时刻再冲击”。
- 总预算上限：例如限制总等待或最大重试次数。

本项目关联：Q05、Q17、Q29、Q69。

### E3.4 背压（Backpressure）
背压本质是“告诉上游：系统已接近容量边界”。

三种常见实现：

| 方式 | 描述 | 适合场景 |
| --- | --- | --- |
| 拒绝（Reject） | 立即失败并返回可重试语义 | 强保护 |
| 有界队列 | 超过队列上限即拒绝 | 可容忍短排队 |
| 限速（Throttle） | 控制进入速率，平滑流量 | 波动负载 |

本项目关联：Q08、Q17、Q58、Q62。

## E4. 限流算法

### E4.1 固定窗口（Fixed Window）
做法：按固定时间段累计计数，到下一窗口清零。

缺点：边界突刺明显，窗口切换点容易瞬时翻倍。

本项目关联：Q06。

### E4.2 滑动窗口（Sliding Window）
做法：按当前时刻向前看窗口长度，动态裁剪历史请求。

- 在本文件实现细节见 E1.3（ZSET 版）。
- 相比固定窗口，边界更平滑。

本项目关联：Q06、Q63。

### E4.3 滑动窗口计数器（近似）
思路：把窗口切成两格（当前格 + 前一格）做加权近似。

两格加权公式（示例）：
`count_approx = curr_count + prev_count * (1 - elapsed/current_bucket_span)`

对比：
- 精度低于 ZSET 真滑窗。
- 内存与 CPU 成本更低，适合超高 QPS 热点。

本项目关联：Q06、Q33、Q66。

### E4.4 令牌桶（Token Bucket）
机制：按固定速率补令牌，请求消耗令牌。

特点：
- 允许突刺（桶里有积累令牌时）。
- 长期平均速率可控。

本项目关联：Q05、Q06。

### E4.5 漏桶（Leaky Bucket）
机制：请求先入桶，再按固定速率流出。

特点：
- 输出速率绝对平滑。
- 高峰时排队/丢弃更明显。

本项目关联：Q05、Q08。

本项目选择：`ZSET` 滑窗 + `INCR/DECR` 并发，做 RPM + RPD + 并发三维联合限流；兼顾事前保护与工程可解释性。

本项目关联：Q05、Q06、Q07、Q62、Q64。

## E5. 统计决策与在线学习

### E5.1 置信下界（Wilson Lower Bound）
Wilson 下界常用于“成功率 + 样本量”联合评分，抑制小样本偶然高分。

公式：
`LB = (p + z^2/(2n) - z * sqrt((p*(1-p) + z^2/(4n))/n)) / (1 + z^2/n)`

其中：
- `p = success / n`
- `n = success + fail`
- `z` 为置信水平对应系数（如 1.645）

为什么用下界而不是均值：
- 均值只看比例，不看可信度。
- 下界天然对小样本更保守，能降低误晋升。

本项目关联：Q26、Q48、Q49、Q52。

### E5.2 探索与利用权衡
常见策略：

| 策略 | 核心思想 | 优点 | 风险 |
| --- | --- | --- | --- |
| ε-Greedy | 以 ε 概率随机探索 | 简单 | 探索质量较粗糙 |
| UCB | 选“均值 + 不确定性”高者 | 平衡好 | 参数敏感 |
| Thompson Sampling | 按后验采样决策 | 实战效果常好 | 需后验建模 |
| Contextual Bandit | 引入任务上下文 | 最灵活 | 特征与反馈要求高 |

本项目当前更偏“可解释探索槽位 + 置信门槛”，再逐步向 bandit 演进。

本项目关联：Q15、Q25、Q35、Q43。

### E5.3 Canary 测试
Canary 是小流量验证机制，用于“先稳后省”。

核心参数：
- 比例：流量暴露程度。
- 样本量：达到再决策，避免小样本抖动。
- 回滚条件：执行失败通常更敏感。
- 冷却期：防止 promote/rollback 抖动。

本项目关联：Q18、Q27、Q59、Q60。

### E5.4 统计显著性与最小样本量
经验规则（工程化）：
- 先设最小样本门槛，再讨论晋升/回滚。
- 高风险业务用更保守阈值（更大样本、更低容错）。
- 反馈延迟明显时，优先保护可用性指标。

工程妥协：
- 不追求学术最优检验流程，而追求“可解释 + 可回放 + 可回滚”。

本项目关联：Q26、Q27、Q59。

## E6. 系统可用性设计

### E6.1 降级（Graceful Degradation）
典型层次：
`完整能力（Redis） -> 降级能力（InMemory） -> 最小能力（Off）`

目标：在依赖故障时保主链路可用，牺牲部分准确性或一致性。

本项目关联：Q30、Q65、Q67。

### E6.2 熔断（Circuit Breaker）
三状态机：
- Closed：正常放行。
- Open：快速失败，避免继续打爆下游。
- Half-Open：小流量探测恢复。

与 `health.py` 的对应可理解为：
- `available` 近似 Closed
- `unable` 近似 Open
- `probe`/`cooldown` 近似 Half-Open

本项目关联：Q16、Q30、Q50。

### E6.3 Outbox Pattern
核心思想：业务状态变更与“待发送事件”同事务写入，后续异步消费。

流程：
1. 事务内写业务表 + outbox 事件。
2. 后台 worker 拉取未发送事件。
3. 下游消费幂等处理，可重放。

价值：
- 降低跨存储写入不一致。
- 失败可恢复、可补偿。

本项目关联：Q04、Q68、Q69。

## E7. 路由与评分系统

### E7.1 多维加权评分
概念公式（通用表达）：
`score = capability_score - cost_penalty + health_modifier - congestion_penalty`

在本项目里，更偏“分层融合”：
- 先做可用性与硬过滤。
- 再按能力主分排序。
- 近似同分时用成本换挡。
- 健康/拥塞信号做修正与门控。

本项目关联：Q21、Q36、Q37、Q41。

### E7.2 候选集构建（Selector）
三层候选：
- 保底：能力天花板兜底。
- 池优先：历史命中优先，保稳定。
- 探索补位：持续发现更优模型。

并行约束：
- Provider 占比上限，避免单供应商集中风险。
- 候选不足时自动退化放宽，保障可用性。

本项目关联：Q14、Q42、Q43、Q44、Q47。

### E7.3 模型健康状态机
状态流（简化）：
`available -> degraded -> unable -> probe -> available`

- `degraded`：软惩罚，仍可选。
- `unable`：硬过滤，不可选。
- `probe cooldown`：恢复后短冷却，避免刚恢复即被打爆。

本项目关联：Q16、Q50、Q71。

## E8. 可观测性（Observability）

### E8.1 监控三支柱
三支柱：
- Metrics：聚合指标（吞吐、延迟、错误率、利用率）。
- Logs：结构化事件（路由原因、错误摘要、关键字段）。
- Traces：跨链路时序（请求经过哪些组件）。

工具示例：Prometheus、ELK、OpenTelemetry。

本项目当前选择：SQLite 侧车优先，重在低依赖、可离线排障与回放。

本项目关联：Q19、Q32、Q71。

### E8.2 Best-Effort 写入
Best-Effort 原则：观测写失败不阻断主链路。

常见模式：
```python
try:
    monitoring.write(event)
except Exception:
    logger.warning("monitoring write failed")
```

权衡：
- 优点：提升主链路可用性。
- 代价：可能丢失部分诊断信息。

本项目关联：Q19、Q68。

### E8.3 关键可观测字段
四类常见场景建议字段：

| 场景 | 关键字段（示例） |
| --- | --- |
| 候选为空 | `routing_reason`, `candidate_count`, `filtered_reasons` |
| 限流触发 | `rpm_ratio`, `conc_ratio`, `is_limited`, `rate_limiter_mode` |
| Provider 抖动 | `provider`, `error_type`, `status`, `failure_streak` |
| 策略偏移 | `score_breakdown`, `start_index_reason`, `pool_hit`, `explore_slots` |

本项目关联：Q71、Q19、Q65。

## E9. 各概念之间的关系图

从“任务请求”到“监控落盘”的数据流（ASCII）：

```text
[Task Request]
      |
      v
[Task Analyzer]
  (结构化维度)
      |
      v
[Selector + Scorer]
  (候选构建 + 多维评分)
      |
      v
[Rate Limiter]
  (RPM/RPD/并发: Redis or InMemory)
      |
      v
[Execute / Retry / Escalate / Downgrade Canary]
      |
      +--------------------------+
      |                          |
      v                          v
[Router Storage(SQLite)]   [Monitoring Sidecar(SQLite)]
(幂等事件/统计/状态机)       (决策与执行观测)
      |                          |
      +------------+-------------+
                   v
             [排障与回放分析]
```

每一层一句话原则：
- 任务分析层：输入必须结构化，避免下游策略漂移。
- 候选与评分层：先保可用再排序，能力主导、成本换挡。
- 限流层：先事前保护再失败处理，避免重试放大。
- 执行决策层：先稳后省，升阶救火与降级试验分工明确。
- 存储层：关键状态强一致，观测写入 best-effort。
- 观测层：字段可解释、可回放，支持快速定位。

本项目关联：Q09、Q21、Q42、Q62、Q17、Q18、Q68、Q71。

## E10. 常见面试追问与速答模板

1. Redis ZSET vs INCR 的场景区分
- 速答：`ZSET` 用于时间窗计数（RPM/RPD），`INCR/DECR` 用于并发 in-flight 计数；两者通常组合而不是互斥。
- 本项目关联：Q06、Q07、Q62。

2. SQLite WAL + Redis 单线程 != 无并发
- 速答：WAL 只缓解读写互阻，SQLite 仍单 writer；Redis 单线程只保证命令原子，不消除跨命令竞态与分布式并发问题。
- 本项目关联：Q01、Q02、Q63。

3. 置信下界 vs 普通成功率
- 速答：成功率只看比例，置信下界同时看样本量；小样本高成功率会被下界保守处理，更适合入池/晋升门槛。
- 本项目关联：Q26、Q48。

4. SQLite 侧车 vs Prometheus/OTel
- 速答：SQLite 侧车适合低依赖与本地回放；Prometheus/OTel 适合规模化统一观测。项目早期先侧车，规模化再升级。
- 本项目关联：Q19、Q32。

5. 探索槽位 vs ε-Greedy
- 速答：探索槽位是工程可解释的“候选位预算”，ε-Greedy 是概率动作策略；前者更易控风险，后者更算法化。
- 本项目关联：Q15、Q25、Q43、Q35。
