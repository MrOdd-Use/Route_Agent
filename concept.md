# Part E：背景知识与概念解析

> 用途：当你在 `questions.md` 里遇到术语（Redis、SQLite、幂等、Canary、Wilson 下界等）时，可在本文件直接查概念、实现口径与工程边界。
>
> 阅读说明：每个概念按四个维度展开——
> 1. **为何提出**：这个概念解决的是什么核心痛点
> 2. **如何解决**：它的核心机制与原理
> 3. **同层对比**：与同一抽象层次的竞品方案相比，优劣势与适用边界
> 4. **本项目运作**：在本项目中的具体落地方式与代码定位

---

## E1. Redis 核心知识点

### E1.1 Redis 是什么

**为何提出**

传统关系型数据库（MySQL、PostgreSQL）面向持久化、复杂查询设计，磁盘 I/O 使其延迟通常在 ms 级甚至更高。当系统需要高频、低延迟的状态读写（如每次请求都要检查计数器、刷新限流窗口）时，关系型数据库成为瓶颈。内存数据库的核心价值就在于：把"最常被访问的热数据"完全放在内存，把延迟从 ms 量级压到 µs 量级。

**如何解决**

Redis 以内存为主存储，提供丰富数据结构（String、Hash、List、Sorted Set、Stream 等），命令处理采用"单线程 + I/O 多路复用"模型：

- 单线程：消除同一实例内的命令级别锁竞争，单条命令天然原子。
- I/O 多路复用：单线程仍能高效处理海量并发网络连接（epoll/kqueue）。
- 持久化可选：支持 RDB 快照与 AOF 日志，可按需取舍内存速度与数据安全性。

**同层对比**

| 方案 | 延迟 | 数据结构丰富度 | 并发写安全 | 持久化 | 典型适用场景 |
|---|---|---|---|---|---|
| Redis | µs 级 | 丰富（ZSET/Lua/Pipeline） | 命令原子 | 可选 | 缓存、限流、会话、Pub/Sub |
| Memcached | µs 级 | 仅 KV | 无内置原子 | 不支持 | 纯缓存、最大吞吐 |
| 关系型 DB | ms 级 | 丰富 SQL | 完整 ACID | 强持久 | 业务数据、复杂查询 |
| 本地进程内存 | ns 级 | 自定义 | 单进程无竞争 | 不支持 | 单实例、无分布式需求 |

Redis 相比 Memcached 多了数据结构与原子 Lua；相比关系型 DB 少了 ACID 完整性但快 1-2 个数量级；相比进程内存多了分布式共享与可选持久化。

**本项目运作**

项目在 `route_agent/router_engine/rate_limiters/redis.py` 实现 `RedisRateLimiter`。Redis 承担三维限流状态的跨进程共享存储：RPM（每分钟请求数）、RPD（每日请求数）、并发 in-flight 计数。通过 `Redis.from_url()` 连接，所有读写均走异步接口（`redis.asyncio`）。

---

### E1.2 String + INCR/DECR（计数器）

**为何提出**

并发场景下，多个进程/协程同时修改一个整数计数器，如果使用"先读后写"（read-modify-write）模式，会产生竞态：两个并发请求都读到 `3`，各自加 1 后都写回 `4`，结果计数少加了一次。需要一种天然原子的自增/自减操作。

**如何解决**

Redis 的 `INCR`/`DECR` 是单条命令，由 Redis 单线程串行执行，中途不会被其他命令插入。这保证了"读 + 计算 + 写"三步作为一个原子单元完成。即使上万并发协程同时 `INCR`，最终计数也是精确的。

**同层对比**

| 方案 | 原子保证 | 并发安全 | 场景 |
|---|---|---|---|
| Redis INCR/DECR | 单命令原子 | 高 | 跨进程共享计数 |
| DB `UPDATE SET n=n+1` | 行锁原子 | 高（但慢） | 业务表内计数 |
| Python `asyncio.Lock + 内存变量` | 协程级原子 | 仅单进程 | 单实例内计数 |
| 乐观锁 CAS | 非原子，需重试 | 中 | 低竞争场景 |

Redis INCR 是轻量计数的最优方案；DB 行锁更重且延迟高；进程内 Lock 无法跨进程共享；乐观锁高竞争下重试开销大。

**本项目运作**

在 `redis.py` 的 `record_request_start_async()` 里，每次请求开始调用 `pipe.incr(conc_key)` 将并发计数加 1，并设置 TTL = 300s 防泄露。请求结束在 `record_request_end_async()` 调用 `decr(conc_key)` 还原。

Key 命名格式：`route_agent:conc:normal:{model_id}` / `route_agent:conc:esc:{model_id}`（分离常规流量与升级流量）。TTL 防泄露的意义：若进程异常崩溃没有走 DECR，最多 300s 后计数自动归零，避免限流状态永久锁死。

---

### E1.3 Sorted Set（ZSET）+ 滑动窗口限流

**为何提出**

固定时间窗计数（固定窗口限流）有一个经典问题：在窗口边界，两个相邻窗口的配额可以在极短时间内叠加使用，形成"边界突刺"（瞬时流量可达限额 2 倍）。需要一种能精确感知"过去 N 秒内真实请求数量"的机制。

**如何解决**

ZSET 的 `score` 字段存储请求的 Unix 时间戳，`member` 存储唯一请求标识。每次检查时：
1. `ZREMRANGEBYSCORE` 清除窗口外（当前时间 - 窗口长度）之前的记录。
2. `ZCARD` 获取窗口内剩余请求数。
3. 若未超限则 `ZADD` 写入当前请求，并刷新 TTL。

因为 ZSET 按 score 有序，裁剪操作 O(log N + K)，查询 O(1)，整体性能优秀。

**同层对比**

| 算法 | 精度 | 边界突刺 | 内存成本 | 适用 QPS |
|---|---|---|---|---|
| 固定窗口（INCR + EXPIRE） | 中 | 高 | O(1) | 超高 QPS |
| 滑动窗口 ZSET（精确） | 高 | 无 | O(N)，N=窗口内请求数 | 中等 QPS |
| 近似滑动窗口（双格加权） | 中 | 低 | O(1) | 高 QPS |
| 令牌桶 | 高 | 允许突刺 | O(1) | 中高 QPS |
| 漏桶 | 高 | 无，绝对平滑 | O(N)，N=队列长度 | 低延迟要求高 |

ZSET 滑窗是精度与工程可解释性的最佳权衡点；固定窗口内存最省但有突刺问题；令牌桶允许短时突发（适合 API 配额场景）；漏桶强制平滑（适合下游吞吐受限场景）。

**本项目运作**

项目用两个独立 ZSET 分别保护 RPM（60s 窗口）和 RPD（86400s 窗口）。在 `record_request_start_async()` 里，`pipe.zadd(rpm_key, {member: score})` 写入；`is_rate_limited_async()` 里先 `zremrangebyscore` 裁剪再 `zcard` 计数。

RPM TTL 设为 120s（窗口 2x），RPD TTL 设为 90000s（略大于 86400s）。选择比窗口大的 TTL 是为了防止窗口未结束时 key 过期导致低估用量。

---

### E1.4 TTL（Key 生命周期）

**为何提出**

Redis 是内存数据库，内存有限。若计数类 key 永不过期，随着模型数量增长，会无限积累历史 key，最终触发内存淘汰（OOM 或 eviction）。更严重的是，若并发计数 key 因进程崩溃而未归零，没有 TTL 则计数永久为正值，导致相关模型被永久限流。

**如何解决**

`EXPIRE`/`PEXPIRE` 命令为 key 设置生命周期，到期后 Redis 自动删除（懒删除 + 周期扫描两阶段）。对于不同用途的 key，TTL 策略不同：

- 窗口类 key（RPM/RPD）：TTL 略大于窗口长度，保证窗口完整性。
- 并发计数 key：TTL 覆盖"最长请求耗时 + 保护余量"（本项目 300s）。
- 利用率缓存：TTL 极短（本项目 `UTIL_CACHE_TTL_MS`，毫秒级），保证新鲜度。

**同层对比**

| 策略 | 机制 | 优点 | 风险 |
|---|---|---|---|
| Redis TTL | key 到期自动清理 | 简单、无侵入 | TTL 设置不当会误删或泄露 |
| 手动删除 | 程序主动 DEL | 精确控制 | 崩溃路径遗漏 |
| LRU/LFU 淘汰 | 内存满时自动淘汰 | 无需显式管理 | 不可预期哪些 key 被淘汰 |
| 不过期 | 永久保留 | 无遗漏 | 内存无限增长 |

TTL 是"防崩溃泄露"的最关键机制，是并发计数场景必须配置的安全网。

**本项目运作**

在 `redis.py` 中，每次 `record_request_start_async()` 调用都在 Pipeline 内同时刷新 TTL：
- `pipe.expire(rpm_key, 120)`
- `pipe.expire(rpd_key, 90000)`
- `pipe.expire(conc_key, 300)`

每次写入都重置 TTL 是为了防止 key 在活跃窗口期提前过期。另外项目还维护了一个进程内的 `_limited_until` 字典（本地内存缓存），通过 `RECENT_LIMITED_TTL_S` 控制其生命周期，用于快速判断"最近是否限流"而不每次都打 Redis。

---

### E1.5 Lua 脚本（原子操作）

**为何提出**

Redis 单条命令是原子的，但业务逻辑往往需要"多命令组合"：先裁剪窗口，再计数，再判断是否超限，再写入——这四步如果分开执行，每步之间都可能被其他并发请求插入，导致竞态（例如两个请求同时判断未超限，都写入，但计数已溢出）。

**如何解决**

Redis `EVAL` 命令将一段 Lua 脚本作为原子单元执行：脚本开始执行后，Redis 不接受其他命令，直到脚本完成。这样"裁剪 + 计数 + 判断 + 写入"四步成为一次原子事务，消除竞态。Lua 脚本在 Redis 服务端执行，同时减少网络往返（RTT）。

**同层对比**

| 方案 | 原子性 | RTT | 复杂度 | 适用场景 |
|---|---|---|---|---|
| Lua EVAL | 完整原子 | 1 RTT | 需编写 Lua | gate 逻辑（判断 + 写入） |
| Pipeline | 无原子性 | 1 RTT | 简单 | 批量读写，容忍竞态 |
| MULTI/EXEC 事务 | 部分原子 | 2 RTT | 简单 | 简单的乐观锁场景 |
| 多次独立命令 | 无原子性 | N RTT | 最简单 | 低并发、不敏感场景 |

Lua 是原子性要求最严格的"gate 判断"场景的最佳选择；Pipeline 只提速不保证原子；MULTI/EXEC 不能在事务中做条件分支（if/else）。

**本项目运作**

项目的 `is_rate_limited_async()` 方法使用 Pipeline（非 Lua），将裁剪 + 查询 + 并发读打包为一次批量 I/O，减少 RTT。这是一个工程权衡：判断限流时，允许极小的竞态窗口（因为有独立的 `record_request_start_async()` 写入操作），优先降低延迟而非强原子性。在 `questions.md` 里 E1.5 的 Lua 示例展示了如果要强一致 gate 时的完整脚本形态，本项目可在高并发场景升级替换。

---

### E1.6 Pipeline（流水线）

**为何提出**

每次 Redis 命令都需要一次网络往返（RTT）。若一个业务操作需要执行 6 条 Redis 命令（如 `record_request_start_async()` 里的 6 条），则需要 6 次 RTT。在局域网内每次 RTT 约 0.1-1ms，6 次就是 0.6-6ms，这对限流这类需要在主链路调用的操作影响显著。

**如何解决**

Pipeline 将多条命令一次性发送给 Redis，Redis 批量执行后一次性返回所有结果。RTT 从 N 次降为 1 次，但注意：Pipeline 内的命令仍然是独立执行的，没有原子性保证，命令之间可以有其他客户端命令插入。

**同层对比**

| 方案 | RTT | 原子性 | 场景 |
|---|---|---|---|
| Pipeline | 1 RTT（批量） | 无 | 批量读写，容忍微小竞态 |
| Lua EVAL | 1 RTT（原子） | 有 | 需要强原子的 gate 逻辑 |
| 多次独立命令 | N RTT | 每条单独原子 | 命令间有数据依赖时 |

Pipeline 是性能优化利器，但不能替代 Lua 的原子性。两者常配合使用：非关键路径用 Pipeline 提速，关键 gate 用 Lua 保一致。

**本项目运作**

项目大量使用 Pipeline：
- `is_rate_limited_async()`：6 条命令（裁剪 RPM/RPD + 查 ZCARD + 查并发 2 个 key）打包为一次 Pipeline（`route_agent/router_engine/rate_limiters/redis.py`）。命令顺序与 `pipe.execute()` 返回的 `result[]` 索引一一对应：
  1. `ZREMRANGEBYSCORE route_agent:rpm:{model_id} 0 (now-60)`：裁剪 RPM 窗口外请求（返回删除条数，项目不使用 -> `result[0]`）。
  2. `ZCARD route_agent:rpm:{model_id}`：裁剪后的 RPM 计数（`rpm_count = result[1]`）。
  3. `ZREMRANGEBYSCORE route_agent:rpd:{model_id} 0 (now-86400)`：裁剪 RPD 窗口外请求（返回删除条数，不使用 -> `result[2]`）。
  4. `ZCARD route_agent:rpd:{model_id}`：裁剪后的 RPD 计数（`rpd_count = result[3]`）。
  5. `GET route_agent:conc:normal:{model_id}`：normal 流量的并发 in-flight 计数（不存在时视为 0 -> `result[4]`）。
  6. `GET route_agent:conc:esc:{model_id}`：escalation 流量的并发 in-flight 计数（不存在时视为 0 -> `result[5]`）。

  最终 `conc_total = normal + esc`，并以 `rpm_count >= rpm_limit or rpd_count >= rpd_limit or conc_total >= conc_limit` 作为限流判定。

  注意：Pipeline 解决的是 **RTT**（6 次往返 → 1 次往返），不是原子性。6 条命令在 Redis 端会按顺序执行，但它们不是一个不可打断的“快照读”：在 `ZREMRANGEBYSCORE` 与 `ZCARD` 之间、以及两次 `GET` 之间，其他客户端的 `ZADD/INCR` 仍可能插入。因此该实现追求“低延迟的近似一致”；若需要强一致的 gate（裁剪 + 计数 + 判断 + 写入必须原子），应使用 Lua `EVAL`（见 E1.5）。
- `record_request_start_async()`：6 条命令（2 ZADD + 2 EXPIRE + 1 INCR + 1 EXPIRE）打包为一次 Pipeline（`route_agent/router_engine/rate_limiters/redis.py`），用于“请求开始时写入限流状态”。命令顺序如下：
  1. `ZADD route_agent:rpm:{model_id} {member: now}`：写入 RPM 滑窗记录（member 由 `"{score}:{time.monotonic()}"` 组成，避免同一秒内碰撞）。
  2. `EXPIRE route_agent:rpm:{model_id} 120`：设置/重置 RPM key 的 TTL。TTL 是按 key 维度生效：每次请求都会把过期时间“续命”为从当前起 120s；当停止请求后约 120s key 自动回收。
  3. `ZADD route_agent:rpd:{model_id} {member: now}`：写入 RPD（24h）滑窗记录（复用同一个 member/score）。
  4. `EXPIRE route_agent:rpd:{model_id} 90000`：设置/重置 RPD key 的 TTL（略大于 86400s，减少边界抖动）。
  5. `INCR route_agent:conc:{normal|esc}:{model_id}`：并发 in-flight 计数加 1。`traffic_type == "escalation"` 时使用 `route_agent:conc:esc:{model_id}`，否则使用 `route_agent:conc:normal:{model_id}`。
  6. `EXPIRE route_agent:conc:{normal|esc}:{model_id} 300`：设置/重置并发 key 的 TTL，用于“防泄露”：如果进程崩溃没走 `record_request_end_async()` 的 `DECR`，最多 300s 后自动回收，避免并发计数永久卡死。

  注意：这里的 Pipeline 同样只减少 RTT，不保证原子性；但该路径是“写入计数”，而 gate 判断在 `is_rate_limited_async()`，因此工程上通常可接受轻微乱序/竞态带来的极小误差。
- `get_utilization_async()`：4 条命令（裁剪 + ZCARD + 2 个 GET）打包为一次 Pipeline（`route_agent/router_engine/rate_limiters/redis.py`），用于构造 `ModelUtilization`（给候选打分/排序提供负载信号）。命令顺序与 `result[]` 索引对应为：
  1. `ZREMRANGEBYSCORE route_agent:rpm:{model_id} 0 (now-60)`：裁剪 RPM 窗口外请求（返回删除条数，不使用 -> `result[0]`）。
  2. `ZCARD route_agent:rpm:{model_id}`：裁剪后的 RPM 计数（`rpm_count = result[1]`）。
  3. `GET route_agent:conc:normal:{model_id}`：normal in-flight（不存在视为 0 -> `result[2]`）。
  4. `GET route_agent:conc:esc:{model_id}`：escalation in-flight（不存在视为 0 -> `result[3]`）。

  `get_utilization_async()` 是“观测/打分”用途：它不负责“判闸/拒绝请求”，而是提供当前负载的连续信号，供上层路由策略做排序、是否等待/升阶、以及与其他模型的 headroom 对比。它最终返回 `ModelUtilization`，核心字段含义为：
  - `rpm_ratio`：`rpm_count / rpm_limit`（上限截断为 1.0）。
  - `conc_ratio`：`(normal + esc) / conc_limit`（上限截断为 1.0）。
  - `normal_conc_ratio` / `escalation_conc_ratio`：两类流量各自的并发占比（用于“升级流量”单独观测/限额）。
  - `escalation_capped`：升级流量是否达到单独的 cap（`ESCALATION_CONC_RATIO × max_concurrency`）。
  - `is_limited`：进程内的“最近被限流”记忆位（`RECENT_LIMITED_TTL_S`），用于快速抑制抖动。
  - `latency_ratio`：当前实现为 0.0（占位字段，预留给未来把延迟/超时作为负载信号）。

  由于候选排序可能高频触发，函数内部还有 `UTIL_CACHE_TTL_MS` 的本地短缓存（详见 E1.7），在缓存有效期内直接返回内存中的 `ModelUtilization`，避免每次都打 Redis。允许极小竞态窗口是该路径的工程权衡：更关注低延迟与降低 Redis 压力，而不是强一致快照。

  容易混淆的一点：Redis 存的是“原始限流状态（事实数据）”，`ModelUtilization` 是把这些事实“读出来算一遍后的仪表盘读数（派生视图）”。两者的关系是 **derived view**：
  - Redis（shared state）记录三维限流的事实数据：
    - `route_agent:rpm:{model_id}`（ZSET）：每次请求开始写入一个时间戳事件（`score=now`），用于“过去 60s 精确滑窗计数”。查询时先 `ZREMRANGEBYSCORE(now-60)` 裁剪，再 `ZCARD` 得到 `rpm_count`。
    - `route_agent:rpd:{model_id}`（ZSET）：同理，但窗口为 86400s（一天），用于 RPD gate。注意 `get_utilization_async()` 当前不读取 RPD，仅 `is_rate_limited_async()` 会读取并参与判闸。
    - `route_agent:conc:normal:{model_id}` / `route_agent:conc:esc:{model_id}`（String 计数器）：当前 in-flight 并发数，请求开始 `INCR`、请求结束 `DECR`（并设置 TTL 防泄露）。
  - `ModelUtilization`（derived view）把 `rpm_count`、`normal/esc` 并发等事实归一化成 0~1 的比例与布尔标记（如 `rpm_ratio`、`conc_ratio`、`escalation_capped`），用来给路由策略提供“多忙/离限流多近”的连续信号；它不是模型的静态元信息（如价格、能力、provider），也不需要持久化或跨进程共享。

所有 Pipeline 调用均通过 `self._redis.pipeline()` 构建，`await pipe.execute()` 一次提交。

---

### E1.7 热 key 问题

**为何提出**

当某个 Redis key 的访问量远高于其他 key 时（例如最热门模型的限流 key、全局 utilization 缓存 key），单 key 的命令处理压力会集中在 Redis 单线程的一个哈希槽上，导致 CPU 成为瓶颈，延迟上升，甚至影响同实例上其他 key 的服务质量。这与 Redis 单线程模型直接相关。

**如何解决**

热 key 问题无法靠"提升 Redis 配置"解决，因为单线程是设计约束。需要从写端和读端两个方向缓解：
- 写端：分片（sharding）——将热 key 拆成多个 key（如 `key:shard0`、`key:shard1`），写时分散，读时汇总。
- 读端：本地缓存——客户端侧缓存热 key 的值，设置极短 TTL，减少 Redis 读取次数。

**同层对比**

| 策略 | 降低 Redis 压力 | 数据新鲜度 | 实现复杂度 | 适用 |
|---|---|---|---|---|
| 本地短缓存 | 高 | 略有延迟 | 低 | 高频读、容忍旧值 |
| Key 分片 | 高 | 实时 | 中 | 高频写 |
| Pipeline 批量 | 中 | 实时 | 低 | 多 key 批量读写 |
| 近似计数器 | 高 | 精度损失 | 低 | 超高 QPS、可接受误差 |

**本项目运作**

`get_utilization_async()` 内实现了本地缓存层（`_util_cache` 字典 + `_util_cache_lock`），由 `UTIL_CACHE_TTL_MS` 控制新鲜度。同一 `model_id` 在缓存有效期内直接返回内存中的 `ModelUtilization`，不打 Redis。这是热 key 防护的本地短缓存策略。同时 `_limited_until` 字典也是热 key 防护：最近被限流的模型通过进程内记忆直接快速失败，不需要每次查询 Redis。

---

### E1.8 可用性与降级策略

**为何提出**

若系统强依赖 Redis，Redis 故障则全链路不可用。对于限流这类"辅助保护"功能，Redis 不可用时不应该直接让主业务（路由选模型）崩溃——而应该有一个降级策略，用可用性换精确性。

**如何解决**

设计多级降级模式，每级独立可用：
- **Redis 模式**：最精确，跨进程共享状态，生产主力。
- **InMemory 模式**：进程内计数，无外部依赖，Redis 不可用时自动切换。
- **Off 模式**：完全关闭限流，适合测试/离线调试。
- **Auto 模式**：根据 Redis 连接健康状况自动在 Redis 和 InMemory 间切换。

**同层对比**

| 模式 | 一致性 | 可用性 | 外部依赖 | 适用场景 |
|---|---|---|---|---|
| Redis | 跨进程一致 | 依赖 Redis 健康 | Redis | 生产主路径 |
| InMemory | 单进程内一致 | 最高（无依赖） | 无 | 降级保底、本地测试 |
| Off | 无保护 | 最高 | 无 | 单元测试、离线演示 |
| Auto | 自适应 | 高 | Redis（可降级） | 生产默认推荐 |

核心权衡：Redis -> InMemory 降级后，多实例部署中各进程各自计数，全局用量会被低估（即实际总量可能超配额），但主链路不中断。这是"牺牲精确性换可用性"的经典工程取舍。

**本项目运作**

通过 `RATE_LIMIT_MODE` 环境变量（`redis`/`inmemory`/`off`/`auto`）和 `RATE_LIMIT_FAIL_STRATEGY` 环境变量（`fail_fast`/`degrade`）控制行为。

`fail_fast`：Redis 不可用直接抛出异常，语义严格，适合对计量精确性要求极高的场景。
`degrade`：Redis 不可用自动切换 InMemory，优先可用性，适合大多数生产场景。

具体实现在 `route_agent/router_engine/rate_limiters/factory.py` 的工厂函数中。

---

### E1.9 持久化（RDB/AOF）

**为何提出**

Redis 以内存为主存储，进程重启或宕机后内存数据全部丢失。对于某些数据（如用户 Session、重要业务状态），内存易失是不可接受的。持久化机制是 Redis 在"纯缓存"和"带持久化的数据库"之间提供的能力谱。

**如何解决**

- **RDB（快照）**：按配置周期（如每 5 分钟）将内存 fork 一个子进程，生成当前快照文件。重启时从快照恢复。优点是文件小、恢复快；缺点是两次快照间的数据在宕机时丢失。
- **AOF（追加日志）**：每次写命令追加到 AOF 文件。重启时重放命令恢复状态。可配置 `fsync` 策略（每秒/每命令），数据丢失窗口更小；缺点是文件大、恢复慢。

**同层对比**

| 维度 | RDB | AOF | 无持久化 |
|---|---|---|---|
| 数据丢失风险 | 两次快照间的数据 | 最多 1 秒（每秒 fsync） | 全部丢失 |
| 恢复速度 | 快 | 慢（重放命令） | 无需恢复 |
| 磁盘占用 | 小 | 大 | 无 |
| 适用数据 | 容忍少量丢失的缓存 | 关键状态 | 纯缓存 |

**本项目运作**

本项目 Redis 主要承载限流计数，这类数据本质上是"短期控制状态"——窗口结束后自然归零，不需要跨重启恢复。因此项目不强依赖 Redis 持久化；即使 Redis 重启导致窗口数据丢失，通过保守阈值设置 + Auto 降级策略可以兜底。关键业务状态（模型池、健康记录、路由事件）存储在 SQLite，而非 Redis，这是有意识的分层设计。

---

## E2. SQLite 事务与锁

### E2.1 锁模型

**为何提出**

关系型数据库需要在"允许并发访问"和"保证数据一致性"之间取得平衡。多个进程/线程同时读写同一数据库文件时，若不加保护，会产生脏读、不可重复读、写写冲突等问题。SQLite 通过文件级锁（file-level locking）机制提供并发保护，但其设计取向是"读优先、写保守"。

**如何解决**

SQLite 的锁状态是一个五态状态机，任何连接必须按顺序爬升到写锁：

```
UNLOCKED -> SHARED -> RESERVED -> PENDING -> EXCLUSIVE
```

- **SHARED**：读锁，允许多个连接同时持有。
- **RESERVED**：表示"我将来要写"，但还未阻塞读，同一时刻只能有一个 RESERVED 锁。
- **PENDING**：即将获取 EXCLUSIVE，新 SHARED 锁不再被授予。
- **EXCLUSIVE**：独占写锁，所有其他锁必须释放。

**同层对比**

| 锁策略 | 读并发 | 写并发 | 实现复杂度 | 典型系统 |
|---|---|---|---|---|
| 文件级锁（SQLite 默认） | 高（多读并发） | 低（单 writer） | 低 | SQLite Rollback Journal |
| WAL 模式（见 E2.2） | 更高 | 低（单 writer） | 低 | SQLite WAL |
| MVCC（多版本并发控制） | 高 | 中（写写仍冲突） | 高 | PostgreSQL、MySQL |
| 行级锁 | 高 | 高（不同行可并发） | 高 | PostgreSQL、MySQL |

SQLite 文件级锁适合单机、低写并发、嵌入式场景；PostgreSQL 等通过 MVCC 实现更细粒度的并发，但需要独立服务进程。

**本项目运作**

项目所有 SQLite 数据库（`router_engine.db`、`task_analysis.db`、`route_agent_monitoring.db`）都使用 SQLite，因为它无需独立服务、零部署依赖，符合"本地优先、低依赖"的设计取向。在 `route_agent/router_engine/storage/connection.py` 中统一管理连接，配合 WAL 模式与 `busy_timeout` 缓解锁冲突。

---

### E2.2 WAL（Write-Ahead Logging）

**为何提出**

SQLite 传统的 Rollback Journal 模式在写入时需要先把原始页面备份到 Journal 文件，写完再提交。这个过程会阻塞正在进行的读操作（因为 Checkpoint 需要独占锁）。在读写混合负载下，读写互相阻塞是明显的性能瓶颈。

**如何解决**

WAL（Write-Ahead Logging）改变了写入逻辑：

1. **写事务**：把新页面追加写入 WAL 文件，主数据库文件不动。
2. **读事务**：读主数据库文件 + 必要时查询 WAL 文件（根据快照时间点确定读哪个版本）。
3. **Checkpoint**：后台任务（或触发点）将 WAL 文件合并回主数据库。

关键效果：读事务不再需要等待写事务完成（写操作只追加 WAL），读写可以并发进行。

**同层对比**

| 维度 | WAL 模式 | Rollback Journal 模式 |
|---|---|---|
| 读写互阻 | 低（读不阻塞写） | 高 |
| 并发读性能 | 好 | 一般 |
| 写写并发 | 仍单 writer | 仍单 writer |
| 额外文件 | WAL 文件 + SHM 文件 | Journal 文件（临时） |
| 跨机器共享 | 不适合（NFS 等） | 同样不适合 |

WAL 解决的是读写互阻，写写冲突仍然存在（SQLite 始终只有一个 writer）。

**本项目运作**

项目在 SQLite 连接初始化时通过 `PRAGMA journal_mode=WAL` 启用 WAL 模式（见 `connection.py`）。这保证了监控写入（sidecar）和路由决策读取可以并发执行，不互相阻塞。项目的"best-effort 写入"（E8.2）也依赖 WAL 的宽松并发特性：监控写入即使短暂慢了，也不会延迟主链路的读取。

---

### E2.3 busy_timeout

**为何提出**

SQLite 在写锁被占用时，默认行为是立即返回 `SQLITE_BUSY` 错误（即失败，不等待）。但在真实业务中，写锁通常只被占用极短时间（毫秒级）——如果能稍等一会儿，大多数情况下可以成功获取锁。默认"立即失败"会导致大量无谓的重试与错误日志。

**如何解决**

`PRAGMA busy_timeout = N`（毫秒）告诉 SQLite：当遇到锁冲突时，不要立即失败，而是最多等待 N 毫秒，期间不断重试。超过 N 毫秒仍无法获取锁才返回 `SQLITE_BUSY`。这把"立即失败"变成了"有界等待后失败"，大幅减少瞬时锁冲突导致的错误。

**同层对比**

| 策略 | 等待 | 影响 | 适用 |
|---|---|---|---|
| `busy_timeout=0`（默认） | 不等待，立即失败 | 高错误率，需上层重试 | 对延迟极敏感场景 |
| `busy_timeout=N` | 等待最多 N ms | 降低错误率，但增加延迟 | 一般业务场景 |
| 上层重试 + 退避 | 由应用控制 | 灵活，但延迟累加 | 精确控制重试策略 |

`busy_timeout` 和上层重试不是二选一，通常组合使用：`busy_timeout` 处理极短的锁占用，上层重试处理 busy_timeout 也不能解决的持续竞争。

**本项目运作**

项目建议在 `connection.py` 中设置 `PRAGMA busy_timeout = 3000`（3 秒）作为基础配置，与 WAL 模式配合使用。3 秒的设置要与上游请求超时预算协调——如果路由决策的总超时是 5 秒，busy_timeout 不能超过 5 秒，否则会导致不可接受的长尾延迟。

---

### E2.4 BEGIN IMMEDIATE 事务

**为何提出**

SQLite 默认的 `BEGIN`（`DEFERRED`）事务会延迟到第一次写操作时才争夺写锁。这在"先读后写"（check-then-act）场景中产生竞态：两个事务都先读（各自成功，都是 SHARED 锁），然后各自尝试写，只有一个能成功，另一个失败并回滚。这种竞态在高并发下会导致大量事务重试。

**如何解决**

`BEGIN IMMEDIATE` 在事务开始时立即争夺 RESERVED 锁，而不是等到第一次写操作。效果：
- 若 RESERVED 锁可用，事务立即进入"我是下一个 writer"状态，后续写操作不需要再争锁。
- 若 RESERVED 锁已被占用，事务在入口就失败（等 busy_timeout），而非在写操作时才失败。

这样"失败点"被提前到事务入口，语义更清晰；check-then-act 的竞态被消除，因为 RESERVED 锁保证了不会有第二个并发 writer 同时通过"读"阶段进入"写"阶段。

**同层对比**

| 事务类型 | 争锁时机 | 适用场景 | 并发冲突点 |
|---|---|---|---|
| `BEGIN DEFERRED` | 第一次写时 | 只读事务、写冲突低 | 写操作时 |
| `BEGIN IMMEDIATE` | 事务开始时（RESERVED） | check-then-act 逻辑 | 事务入口 |
| `BEGIN EXCLUSIVE` | 事务开始时（EXCLUSIVE） | 强独占需求（极少用） | 事务入口，阻塞所有读 |

`BEGIN IMMEDIATE` 是"先检查后操作"场景的标准选择；`EXCLUSIVE` 比 `IMMEDIATE` 更激进，会阻塞读，通常不必要。

**本项目运作**

路由引擎的关键状态变更（模型健康状态更新、降级试验记录、class pool 写入）均需要 check-then-act 语义。在 `router_storage.py`（RouterStorage）的原子操作方法中，使用 `BEGIN IMMEDIATE` 确保"读状态 -> 判断 -> 写新状态"这三步不会被并发写入打断，保证状态机转换的正确性。

---

### E2.5 多实例（Multi-Instance）与存储后端选型

**为何提出**

单进程程序将 SQLite 文件放在本地磁盘，读写无竞争，部署简单。但当服务需要水平扩展（多台机器同时运行相同程序）或高可用（主挂后副本接管）时，"本地文件"模型就失效了：每台机器拥有独立副本，状态不共享，统计被稀释，策略无法联动。这就是"多实例"带来的存储选型压力。

**如何解决**

多实例的核心需求是：**存储可被多个进程/机器并发访问，且并发写入安全**。不同后端对应不同部署规模：

| 后端 | 访问方式 | 并发写安全 | 跨机共享 | 适用阶段 |
|---|---|---|---|---|
| SQLite（文件） | 进程内直接读写 | 单 writer（WAL 改善读写互阻，但写仍串行） | 不支持（NFS 挂载锁可靠性差） | 单机 CLI / 嵌入工具 |
| PostgreSQL | 网络连接 | 行级锁 + MVCC，支持高并发写 | 支持 | API 服务化 / 多实例部署 |
| MySQL | 网络连接 | 行级锁 + MVCC | 支持 | API 服务化 / 多实例部署 |
| Redis | 网络连接 | 单线程命令原子 | 支持 | 高频计数、限流状态 |

SQLite 在多实例场景的核心问题：
- **写写串行**：多个 worker 同时写同一 `.db` 文件，文件锁排队，写吞吐无法线性扩展，高并发时退化为"超时→重试→更拥塞"雪崩。
- **数据孤岛**：若每台机器各有独立 `.db` 文件，class_pool 入池/淘汰统计无法合并，监控无法汇总，在线学习闭环各自为政，样本被稀释。
- **NFS 风险**：通过网络文件系统共享 SQLite 文件，锁语义在 NFS 上不可靠，可能导致数据损坏。

**同层对比：PostgreSQL vs MySQL**

两者均可支持多实例，Route Agent 选择预留 PostgreSQL 而非 MySQL，原因在于：

| 维度 | PostgreSQL | MySQL |
|---|---|---|
| SQL 方言与 SQLite 的相似度 | 高（`ON CONFLICT DO NOTHING`、类型系统接近） | 低（`INSERT IGNORE`、部分语法差异） |
| 异步驱动成熟度 | `asyncpg`，与 `aiosqlite` 对称的高性能异步方案 | `aiomysql`（功能完整但生态相对小） |
| JSON 原生支持 | `JSONB`（二进制存储 + 索引），适合 `metadata_json` | `JSON`（5.7+ 支持，但功能弱于 JSONB） |
| 事务隔离与 MVCC | 严格 MVCC，读不阻写 | 可配置，默认 REPEATABLE READ |
| 开源协议 | PostgreSQL License（极宽松） | GPL（商用需注意） |

**本项目运作**

项目在 `route_agent/model_registry/storage/` 实现了两后端：

- `sqlite.py` → `SqliteModelRegistryStore`：默认，零外部依赖，适合 CLI 嵌入场景。
- `postgres.py` → `PostgresModelRegistryStore`：通过环境变量 `ROUTE_AGENT_POSTGRES_DSN` 激活，业务逻辑层（`service.py`）无需改动即可切换。

当前四个 `.db` 文件（registry、task_analysis、router_engine、monitoring）均为单进程本地读写，不存在跨进程竞争，SQLite 完全够用。演进路径为：

1. **当前（CLI 单实例）**：SQLite + WAL + best-effort 写，零基础设施依赖（P0 目标）。
2. **中期（API 服务化）**：高频写（限流计数、并发计数）迁入 Redis；model_registry 切换 `PostgresModelRegistryStore`；SQLite 退化为本地快照与审计落盘。
3. **长期（多实例部署）**：全量切换共享 PostgreSQL，class_pool/monitoring 统计全局可见，class_pool 在线学习闭环跨实例共享，支撑水平扩展。

> 关联题：Q72（存储选型）、Q02（WAL 锁策略）、Q32（监控选型）、Q30（Redis 降级）

---

## E3. 高并发编程基础

### E3.1 锁竞争（Lock Contention）

**为何提出**

锁是并发安全的基础工具，但锁本身也是一种串行化机制。当多个线程/协程争夺同一把锁时，等待时间累加，整体吞吐下降。极端情况下，锁竞争会让并发系统的性能退化为串行系统（线性扩展消失）。如何在保证正确性的同时最小化锁竞争，是高并发系统设计的核心课题。

**如何解决**

缓解锁竞争有五类策略：

| 策略 | 核心做法 | 适用场景 |
|---|---|---|
| 减少写频率 | 批量写、聚合写、采样写 | 监控写入、日志记录 |
| 缩短持锁时间 | 事务内禁止 I/O/重计算 | 所有锁保护的写操作 |
| 读写分离 | 热路径只读、冷路径写 | 读多写少的配置读取 |
| 无锁化 | 原子单语句替代读改写 | Redis INCR、DB `UPDATE SET n=n+1` |
| 分区分片 | 热点 key/行按维度拆分 | 热模型的限流 key |

识别锁竞争的信号：P95/P99 延迟随并发非线性恶化、timeout / locked / 429 错误集中爆发。

**本项目运作**

项目在两个层面处理锁竞争：
- **Redis 层**：`INCR/DECR` 替代"GET + SET"消除竞态；Pipeline 减少 RTT；本地 `_util_cache` 减少 Redis 热 key 压力。
- **SQLite 层**：`busy_timeout` 减少瞬时失败；WAL 模式减少读写互阻；`BEGIN IMMEDIATE` 将竞争点提前到事务入口。监控写入采用 best-effort（失败不重试），避免监控写入竞争主链路写锁。

---

### E3.2 幂等性（Idempotency）

**为何提出**

分布式系统中，消息/请求传递不可靠：网络抖动可能导致同一请求被重复发送，或者在不确定是否成功的情况下发起重试。如果业务逻辑不具备幂等性，重复操作会产生副作用（如双重扣款、重复写入数据库记录、状态机错误转换）。幂等性是"at-least-once 投递"场景下数据正确性的基本保证。

**如何解决**

幂等性的核心是：用一个唯一标识符（幂等键）标记一个语义请求，确保相同语义请求只被处理一次。实现方式：

| 方式 | 机制 | 优点 | 适用 |
|---|---|---|---|
| 数据库 unique 约束 | `(request_id, model_id, event_type)` 唯一键，重复插入报错 | 简单、天然防重 | 事件记录、去重日志 |
| 幂等键表 | 先写去重记录，再执行业务 | 便于审计 | 高价值操作 |
| 乐观锁（version/CAS） | 检查版本号，版本不匹配则失败 | 无需额外表 | 状态更新 |
| 自然幂等 | 操作本身重复无副作用（如 SET） | 最简单 | 覆盖写场景 |

`at-least-once + 幂等消费` 是工程标准，优于追求 `exactly-once`（后者在跨系统实现时成本极高）。

**本项目运作**

路由事件记录（`event_repo.py`）通过 `(request_id, model_id, event_type)` 复合唯一键防止重复插入，配合 `ON CONFLICT IGNORE` 语义实现幂等写入。这保证了即使监控层重复调用，也不会产生重复的事件记录，统计数据不被污染。下载试验（downgrade trial）和 class pool 的原子更新也依赖 `BEGIN IMMEDIATE` + unique 约束确保幂等性。

---

### E3.3 重试风暴（Retry Storm）

**为何提出**

系统出现故障（如下游模型超时、限流、临时不可用）时，客户端通常会重试。但如果大量客户端同时遇到故障，同时重试，会形成"重试风暴"：故障时刻的流量瞬间翻倍，进一步加重故障，形成正反馈死循环（`故障 -> 重试 -> 更高流量 -> 更严重故障`）。

**如何解决**

两个关键机制：

1. **指数退避（Exponential Backoff）**：重试间隔按 `base * 2^k`（k=重试次数）递增，避免所有客户端以固定频率同步重试。
2. **随机抖动（Jitter）**：在退避间隔上加随机偏移（如 `interval + random(0, interval)`），将同时重试的客户端"分散"到不同时刻，打破同步性。

两者缺一不可：纯指数退避没有抖动时，多个客户端的重试时刻仍然是同步对齐的（都是 1s、2s、4s……）。

**同层对比**

| 策略 | 同步重试风险 | 收敛速度 | 实现复杂度 |
|---|---|---|---|
| 固定间隔重试 | 高（同步） | 快（但可能加剧故障） | 极简 |
| 指数退避 | 中 | 中 | 简单 |
| 指数退避 + Jitter | 低 | 中（随机分散） | 简单 |
| 指数退避 + Jitter + 熔断 | 最低 | 快（主动感知恢复） | 中等 |

**本项目运作**

路由引擎在选择模型失败时，通过 `start_index` 机制（候选列表中的起始索引）实现轻量级"软重试"——当首选模型（index=0）不可用时，自动尝试 index=1、2……的候选，而非立即重试同一模型。

对于执行失败（`report_exec_failure_async()`），健康状态机将模型转换为 `degraded` 或 `unable` 状态，配合 `probe cooldown` 防止刚恢复的模型被立即打爆（类似 half-open + jitter 语义）。

---

### E3.4 背压（Backpressure）

**为何提出**

在流式系统或异步处理系统中，生产者速度可能远超消费者处理能力。如果没有反馈机制，会导致消费者缓冲区溢出（OOM）或服务质量崩溃。背压是"下游向上游传递容量信号"的机制，让上游主动降速，保护系统整体稳定性。

**如何解决**

三种主要实现方式：

| 方式 | 机制 | 优点 | 适用 |
|---|---|---|---|
| 拒绝（Reject） | 当负载超限时立即返回 429/503 | 保护下游，信号清晰 | API 网关、限流层 |
| 有界队列 | 请求入队，满了就拒绝新入队 | 允许短时缓冲 | 异步任务、消息队列 |
| 主动限速（Throttle） | 控制上游发送速率 | 平滑流量 | 生产者可控的场景 |

背压区别于熔断：背压是容量边界的主动通知，熔断是故障状态的快速切断。两者常配合使用。

**本项目运作**

项目的限流层（RPM/RPD/并发三维）本质上是背压机制的实现：当任一维度超限时，`is_rate_limited_async()` 返回 `True`，上层选模型逻辑将该模型排除在候选集外，路由到其他候选或返回"无可用模型"错误。这是"拒绝"型背压。

同时，`get_utilization_async()` 返回的 `ModelUtilization`（包含 `rpm_ratio`、`conc_ratio`）也是背压的软信号——在 `selector.py` 的 `_should_skip_default()` 中，当 default 模型的 utilization 高时，软切换到其他候选，而不是等到真正超限。

---

## E4. 限流算法

### E4.1 固定窗口（Fixed Window）

**为何提出**

最简单的限流需求：每分钟不超过 100 次请求。最直觉的实现是：按分钟（或任意固定时间段）切一个窗口，窗口内累计计数，到下一个窗口清零。这就是固定窗口算法。

**如何解决**

Redis `INCR` + `EXPIRE`（到窗口边界时过期）实现：每个时间窗对应一个 key，请求到来时 `INCR`，窗口结束时 key 过期自动清零。实现极简，内存 O(1)。

**核心缺陷**：在窗口切换点，前一窗口末尾和后一窗口开头各可以使用满额配额，即在窗口边界附近，瞬时流量可以达到限额的 2 倍（边界突刺/burst）。

**同层对比（所有限流算法对比见 E4.5 后的汇总表）**

与滑动窗口相比，固定窗口的精度更低但内存成本更低；与令牌桶相比，固定窗口不允许任何形式的突发（令牌桶允许积累令牌后的突发），但更简单。

**本项目关联**：Q06。固定窗口在本项目中未采用，主要是因为 RPM/RPD 场景需要精确边界保护（避免 API provider 计费窗口对齐时被锁定）。

---

### E4.2 滑动窗口（Sliding Window）

**为何提出**

固定窗口的边界突刺问题源于"窗口"的硬边界。如果把"窗口"的起点从"固定时间点"改为"当前时刻往前 N 秒"，窗口就变成了实时滚动的——任何时刻都看最近 N 秒内的真实请求数，不存在边界突刺。

**如何解决**

ZSET 实现（详见 E1.3）：每个请求以 Unix 时间戳为 score 写入 ZSET，查询时先用 `ZREMRANGEBYSCORE` 裁掉窗口外的请求，再用 `ZCARD` 统计。窗口始终是"当前时刻 - N 秒"到"当前时刻"的精确滑动窗口。

**同层对比**

| 对比维度 | 固定窗口 | 滑动窗口（ZSET） |
|---|---|---|
| 边界突刺 | 有（可达 2x 配额） | 无 |
| 内存消耗 | O(1) | O(N)，N 为窗口内请求数 |
| 实现复杂度 | 极低 | 低 |
| 精度 | 中 | 高 |
| 适用 QPS | 任何（因为 O(1)） | 中等 QPS（N 不能太大） |

**本项目运作**

项目 RPM 和 RPD 均采用 ZSET 滑动窗口，实现在 `redis.py` 的 `is_rate_limited_async()` 和 `record_request_start_async()` 中。两个 ZSET key：
- `route_agent:rpm:{model_id}`（60s 窗口）
- `route_agent:rpd:{model_id}`（86400s 窗口）

---

### E4.3 滑动窗口计数器（近似）

**为何提出**

精确滑动窗口（ZSET）的内存消耗随请求数线性增长，在超高 QPS 场景（每秒万次请求级别）下，单个 key 的 ZSET 可能包含数十万个 member，内存和裁剪操作开销不可忽视。需要一种在精度和内存之间取折中的近似方案。

**如何解决**

将时间窗分成两个相邻的固定格（当前格 + 前一格），用加权计算近似当前窗口内的请求数：

```
count_approx = curr_count + prev_count × (1 - elapsed / window_duration)
```

`elapsed` 是当前格已经过去的时间比例。当前格越靠近开始，前一格的权重越高（越接近完整窗口），越靠近结束，前一格权重越低。

内存从 O(N) 降为 O(1)（只需两个计数器），精度从"精确"降为"近似"（误差通常在 5-10% 以内）。

**同层对比**

| 对比维度 | ZSET 精确滑窗 | 双格近似滑窗 | 固定窗口 |
|---|---|---|---|
| 精度 | 精确 | 近似（~95%） | 低（边界突刺） |
| 内存 | O(N) | O(1) | O(1) |
| CPU 开销 | 裁剪 O(K) | O(1) | O(1) |
| 实现复杂度 | 低 | 中 | 极低 |

**本项目运作**

本项目未采用近似滑窗，选择精确 ZSET 方案，因为模型的 RPM/RPD 配额价值高（影响成本控制），精确性优先于内存节省。如果未来需要支持超高 QPS 场景（如每秒万次路由），近似滑窗是可考虑的升级路径，对应 Q33、Q66。

---

### E4.4 令牌桶（Token Bucket）

**为何提出**

某些场景需要允许短时突发（burst）：例如用户平时每分钟 10 次请求，但某个时刻有一批任务需要快速处理，希望允许突发到 50 次。滑动窗口对突发一视同仁地限制，无法区分"正常突发"和"滥用"。令牌桶允许"积攒配额后的合理突发"。

**如何解决**

桶以固定速率补充令牌（如每秒 10 个），桶有容量上限（如 50 个）。每次请求消耗一个令牌，令牌不足则限流。当系统空闲时，令牌积累；当需要突发时，消耗积累的令牌。长期平均速率受补充速率控制，短时突发受桶容量上限控制。

**同层对比**

| 对比维度 | 令牌桶 | 漏桶 | 滑动窗口 |
|---|---|---|---|
| 允许突发 | 是（最多桶容量） | 否 | 否 |
| 输出速率 | 不完全平滑（突发时） | 完全平滑（固定速率） | 不平滑 |
| 适用场景 | 允许短时突发的 API 限流 | 下游吞吐受限的平滑输出 | 精确配额保护 |

**本项目运作**

本项目的 API provider（如 OpenAI）本身对模型使用令牌桶/滑动窗口限流。本项目的 RPM/RPD 是在客户端侧做的预防性限流（提前拦截，避免打到 provider 后被 429），因此精确性要求高，选择滑动窗口而非令牌桶。令牌桶适合"内部系统对外暴露 API"时允许客户端有合理突发的场景。

---

### E4.5 漏桶（Leaky Bucket）

**为何提出**

某些下游服务（如外部 API、数据库写入）对输入速率有严格的物理限制，超速必定拥塞。令牌桶虽然控制了长期平均速率，但允许突发的瞬时流量仍可能打爆下游。漏桶强制将输出速率完全平滑，保护敏感下游。

**如何解决**

请求到来后先放入一个有界队列（"桶"），然后以固定速率从桶中取出处理（"漏出"）。无论输入流量如何突发，输出速率始终恒定。超出桶容量的请求被直接丢弃（或返回拒绝）。

**同层对比**

| 对比维度 | 漏桶 | 令牌桶 |
|---|---|---|
| 输出是否平滑 | 是（绝对恒定） | 否（允许突发） |
| 高峰处理 | 排队等待（超时丢弃） | 消耗积累令牌 |
| 适用下游 | 吞吐受限的弱下游 | 弹性较好的下游 |
| 用户体验 | 高峰延迟明显 | 高峰可快速响应 |

**限流算法综合对比（汇总）**

| 算法 | 精度 | 内存 | 允许突发 | 平滑输出 | 本项目采用 |
|---|---|---|---|---|---|
| 固定窗口 | 中（边界突刺） | O(1) | 边界处是 | 否 | 否 |
| 滑动窗口（ZSET） | 高 | O(N) | 否 | 否 | **是（RPM/RPD）** |
| 近似滑窗（双格） | 中高 | O(1) | 否 | 否 | 否（备选） |
| 令牌桶 | 高 | O(1) | 是 | 否 | 否 |
| 漏桶 | 高 | O(N) | 否 | 是 | 否 |

**本项目选择**：`ZSET` 滑窗（RPM/RPD）+ `INCR/DECR`（并发），三维联合限流（RPM + RPD + 并发），兼顾精确性与工程可解释性。

---

## E5. 统计决策与在线学习

### E5.1 置信下界（Wilson Lower Bound）

**为何提出**

用"成功率"（success / total）评估模型质量时，存在一个小样本陷阱：一个模型只被调用 3 次，全部成功，成功率 100%；另一个模型被调用 1000 次，成功率 95%。简单按成功率排序，会错误地把小样本 100% 的模型排在大样本 95% 的模型之上——但前者的"成功率"可信度远低于后者。需要一种把"成功率"和"可信度"都纳入考虑的评分方法。

**如何解决**

Wilson 置信下界（Wilson Score Interval 的下界）是解决这个问题的经典方法。它的核心思想：对于一个二项分布（成功/失败），在给定置信水平（如 90%）下，真实成功率的下界是什么？

公式：
```
LB = (p + z²/2n - z·√((p(1-p) + z²/4n)/n)) / (1 + z²/n)
```

其中 `p = success/n`，`n = success + fail`，`z = 1.645`（90% 置信水平）。

效果：小样本（n 小）时，LB 显著低于 p，体现"不确定性惩罚"；大样本时，LB 趋近 p。3 次全成功的 LB 约 0.43；1000 次 95% 成功的 LB 约 0.936。正确地把高可信度的大样本排前面。

**同层对比**

| 评分方法 | 是否惩罚小样本 | 计算复杂度 | 可解释性 | 适用 |
|---|---|---|---|---|
| 简单成功率 (p) | 否 | O(1) | 极高 | 样本量足够大时 |
| Wilson 下界 (LB) | 是 | O(1) | 高 | 样本量不稳定时 |
| 贝叶斯均值（Beta 分布期望） | 是（通过先验） | O(1) | 中 | 有先验知识时 |
| UCB（上置信界） | 是（惩罚少探索） | O(1) | 中 | 需要探索-利用权衡时 |

Wilson 下界是"排名/筛选"场景最常用的统计方法；UCB 更适合主动探索场景；贝叶斯方法需要先验分布。

**本项目运作**

在 `downgrade.py` 的 `_wilson_lower_bound()` 函数中实现（第 31-41 行），用于降级试验的挑战者评估：`_select_cheaper_candidate_async()` 计算挑战者与当前模型各自的 Wilson 下界，通过 `ratio_score = challenger_wlb / current_wlb` 评估挑战者的相对可信质量。只有挑战者的 Wilson 下界与当前模型接近（高 ratio_score）且价格更低时，才会启动降级试验。

---

### E5.2 探索与利用权衡（Explore-Exploit Tradeoff）

**为何提出**

路由系统选模型时面临一个根本性困境：已知好的模型（利用已有知识，低风险）vs 尝试未知模型（探索潜在更好方案，有风险）。纯利用（always pick the best known）会错过更优模型；纯探索（always try new）会频繁选到差模型损害用户体验。需要在两者之间找到平衡。

**如何解决**

多臂老虎机（Multi-Armed Bandit）是这一问题的经典框架。常见策略：

| 策略 | 核心思想 | 优点 | 缺点 |
|---|---|---|---|
| ε-Greedy | 以 ε 概率随机选任意臂（探索），其余时间选最优臂（利用） | 极简，易实现 | 探索是随机的，质量差 |
| UCB（Upper Confidence Bound） | 选"均值 + 不确定性上界"最高的臂，样本少的臂不确定性高 | 理论保证、平衡好 | 参数（置信系数）敏感 |
| Thompson Sampling | 从每条臂的后验分布中采样，选采样值最高的 | 实战效果强 | 需要建模后验分布 |
| Contextual Bandit | 引入上下文特征（任务特征）影响臂选择 | 最灵活 | 特征工程复杂、冷启动难 |

**本项目运作**

本项目采用工程化的"探索槽位"方案而非标准 bandit 算法：

在 `selector.py` 的 `_adaptive_explore_slots()` 函数中，根据 class pool 的大小（`pool_size`）和平均试验次数（`avg_trials`）动态计算探索槽位数（1-3 个），从候选集中预留位置给非池模型（`is_explore=True`）。这是一种"确定性探索预算"方案，可解释性强（每次路由最多 3 个探索候选），但比 ε-Greedy 更保守。

`pool_rich`（池子大 + 试验充足）时减少探索；`pool_thin`（池子小 + 试验少）时增加探索。Pool 内模型提供利用，探索槽位提供探索。这种方案的工程价值在于：风险可控（探索槽位有上限），决策可解释（能从 `RouteDecision.candidates` 的 `is_explore` 字段直接看到哪些是探索候选）。

---

### E5.3 Canary 测试

**为何提出**

将新模型（或新策略）全量上线的风险极高：若新方案表现差，影响 100% 的流量，损害难以快速回滚。需要一种"先小流量验证，验证通过再扩量"的机制，让风险暴露在可控的小范围内。

**如何解决**

Canary（金丝雀）测试：将小比例（如 5-15%）流量路由到候选方案（challenger），其余流量保持现有方案（incumbent）。收集足够样本后，根据关键指标（质量成功率、延迟、成本）决定：

- **晋升（Promote）**：challenger 表现等同或更优，将其设为新 incumbent。
- **回滚（Rollback）**：challenger 表现低于阈值，停止试验，恢复全量到 incumbent，并设冷却期防止立即重试。
- **继续（Continue）**：样本量不足，继续收集数据。

**同层对比**

| 发布策略 | 风险暴露 | 验证成本 | 回滚成本 | 适用 |
|---|---|---|---|---|
| 全量发布 | 100% 流量 | 低 | 高（影响全量） | 低风险变更 |
| Canary | 小比例（可配置） | 中 | 低（切回 incumbent） | 高风险变更 |
| 蓝绿部署 | 100%（瞬间切换） | 高（维护两套环境） | 极低（秒级切换） | 基础设施变更 |
| A/B 测试 | 50%/50% | 中 | 中 | 功能效果对比 |

Canary 与 A/B 的区别：Canary 是单向验证（新方案是否足够好），A/B 是双向对比（哪个方案更好）。

**本项目运作**

`DowngradeOptimizer`（`downgrade.py`）完整实现了 Canary 语义：

- `DOWNGRADE_CANARY_RATIO`（常数，约 0.1-0.15）控制试验流量比例。
- `choose_trial_model_async()`：通过 `random.random() < ratio` 决定是否将本次请求路由到 challenger。
- `record_downgrade_result_async()`：记录每次观测结果，当 `quality_fail_count >= DOWNGRADE_ROLLBACK_QUALITY_FAIL` 或 `exec_fail_count >= DOWNGRADE_ROLLBACK_EXEC_FAIL` 时触发回滚；当样本充足且 Wilson 下界满足条件时触发晋升。
- `DOWNGRADE_COOLDOWN_H` 控制回滚后的冷却期，防止同一 challenger 在证明不可行后立即被重新尝试。

这是一个降级优化的 Canary：试验的不是"升级"（更好更贵），而是"降级"（更便宜但质量相近）。

---

### E5.4 统计显著性与最小样本量

**为何提出**

Canary 试验中，如果在样本量极小时就做出晋升/回滚决策，结论极不可靠：前几次请求的结果可能是随机噪声。统计显著性回答的问题是："我们需要多少样本，才能对观测结果有足够的置信度？"

**如何解决**

工程化规则（而非严格统计检验）：
- 设置最小样本量门槛（`DOWNGRADE_TRIAL_MIN_SAMPLES`），达到前只观测，不决策。
- 晋升门槛额外要求最小成功次数（`DOWNGRADE_PROMOTION_MIN_SUCCESS`），双重保险。
- 回滚条件更宽松（失败次数门槛低），因为回滚是保守行为，代价小。
- Wilson 下界本身已经隐含了对小样本的保守处理。

**同层对比**

| 方法 | 严格程度 | 工程实现成本 | 适用 |
|---|---|---|---|
| 严格统计检验（t-test、卡方） | 高 | 高 | 学术研究、高精度 A/B |
| 工程化最小样本门槛 | 中 | 低 | 生产 Canary、路由优化 |
| Wilson 下界（隐含） | 中 | 极低（一个公式） | 排名、筛选 |
| 不设门槛 | 无 | 无 | 不推荐 |

**本项目运作**

在 `record_downgrade_result_async()` 中：`sampled_requests < DOWNGRADE_TRIAL_MIN_SAMPLES` 时直接返回 `"continue"`，不做晋升/回滚判断。晋升必须同时满足：`sampled_requests >= DOWNGRADE_TRIAL_MIN_SAMPLES` 且 `success_count >= DOWNGRADE_PROMOTION_MIN_SUCCESS`。这是"工程妥协版"统计显著性控制，优先可解释性和安全性，而非追求最优统计检验流程。

---

## E6. 系统可用性设计

### E6.1 降级（Graceful Degradation）

**为何提出**

分布式系统中，依赖链上的任何一环都可能发生故障（Redis 宕机、外部 API 不可用、数据库锁定）。如果系统在任何依赖故障时都完全不可用（硬依赖），整体可用性会是所有依赖可用性的乘积（往往远低于单个组件）。通过降级，系统可以在依赖降级时仍提供"缩减版"服务，而非完全不可用。

**如何解决**

设计多级降级层次，每级都有独立的可用性保证：

```
完整能力（Redis 限流 + SQLite 存储 + 外部 API）
    ↓ 降级
进程内限流（InMemory）+ 本地 SQLite + 外部 API
    ↓ 降级
关闭限流（Off）+ 本地 SQLite + 外部 API（仅路由，无保护）
```

每级降级牺牲部分准确性或功能，但保留核心链路可用。

**同层对比**

| 设计取向 | 机制 | 代价 | 适用 |
|---|---|---|---|
| 优雅降级（Graceful Degradation） | 依赖故障时切换到简化能力 | 功能/精度损失 | 核心链路必须可用的场景 |
| 熔断（Circuit Breaker） | 检测到故障时快速切断依赖调用 | 功能暂时不可用 | 防止级联故障 |
| 超时 + 快速失败 | 依赖超时立即返回错误 | 功能不可用 | 延迟预算严格的场景 |
| 重试 + 退避 | 失败后重试 | 延迟增加 | 短暂性故障 |

降级和熔断通常配合使用：熔断在检测到故障时切断，降级提供切断后的备用能力。

**本项目运作**

限流层降级：`RATE_LIMIT_MODE=auto` + `RATE_LIMIT_FAIL_STRATEGY=degrade` 时，Redis 连接失败自动切换到 InMemory 限流器，主链路路由不中断。InMemory 限流器（`inmemory.py`）与 Redis 限流器实现相同接口（通过 `RateLimiter` 抽象），降级对上层透明。路由层降级：若 SQLite 不可用，监控写入的 best-effort 模式（E8.2）保证写失败不阻塞路由决策返回。

---

### E6.2 熔断（Circuit Breaker）

**为何提出**

当下游服务出现故障时，如果每个请求都去尝试调用下游（并等待超时），会导致：1）请求线程/协程堆积，资源耗尽；2）故障服务可能因持续请求压力无法恢复；3）级联到上游系统（超时传播）。熔断器的目标是：在检测到下游故障后，快速拒绝新请求（"断开电路"），给下游恢复时间，并周期性探测恢复。

**如何解决**

三状态机：

```
Closed（正常）→ [失败率超阈值] → Open（断开，快速失败）
Open → [等待冷却时间] → Half-Open（探测，小流量）
Half-Open → [探测成功] → Closed
Half-Open → [探测失败] → Open
```

**同层对比**

| 机制 | 场景 | 恢复机制 | 代价 |
|---|---|---|---|
| 熔断 | 下游持续故障 | 主动探测 | 服务暂不可用 |
| 重试 + 退避 | 短暂性故障 | 被动等待 | 延迟增加 |
| 超时 | 下游超慢 | 无 | 每次都等超时 |
| 降级 | 下游不可用 | 无（用降级替代） | 功能/精度降低 |

**本项目运作**

模型健康状态机（`health.py` + `availability_repo.py`）是针对模型可用性的熔断器实现：

- **Closed** ≈ `available`：模型正常可选。
- **Open** ≈ `unable`：执行失败达到阈值，模型被硬排除出候选集（`is_available()` 返回 `False`）。
- **Half-Open** ≈ `probe cooldown`：模型通过 `probe_unable_models_async()` 探测成功后进入冷却期（`PROBE_COOLDOWN_S`），冷却期内仍可选但有 `PROBE_COOLDOWN_PENALTY` 扣分，防止立即被打爆。

状态转换由 `report_exec_failure_async()` 驱动（执行失败 → 向 `unable` 转变），由 `probe_loop_async()` 定期探测恢复（`unable` → `available`）。

---

### E6.3 Outbox Pattern

**为何提出**

跨存储一致性问题：假设我们要同时写"业务状态变更"（如 class pool 更新）和"通知事件"（如发送路由决策给监控系统），如果两步写入不在同一事务中，可能出现：业务状态已写但事件未发送（监控丢失数据），或事件已发但业务状态未写成功（数据不一致）。分布式系统中，跨存储的强一致性代价极高（2PC/3PC），Outbox Pattern 是一个实用的工程妥协。

**如何解决**

1. 业务状态变更 + outbox 事件记录放在**同一本地事务**中写入（保证原子性）。
2. 后台 worker 异步拉取 outbox 中未处理的事件，推送给下游。
3. 下游消费幂等处理（配合幂等键，E3.2）。

这样，业务状态和"待发事件"的一致性由本地事务保证；事件到下游的传递通过后台重试保证最终一致性。

**同层对比**

| 方案 | 一致性保证 | 实现复杂度 | 适用 |
|---|---|---|---|
| Outbox Pattern | 本地事务 + 最终一致 | 中 | 跨存储、消息队列 |
| 2PC（两阶段提交） | 强一致 | 高 | 极少用，性能差 |
| Saga Pattern | 最终一致（通过补偿） | 高 | 长事务、多步骤 |
| 直接双写 | 无保证（有窗口） | 低 | 低价值事件 |

**本项目运作**

本项目的监控系统（sidecar）设计天然具有 Outbox 思想：路由决策的"事实"（选了哪个模型、时间戳）在路由引擎内完成后，通过 best-effort 方式异步写入 `route_agent_monitoring.db`。路由决策本身不等待监控写入完成（监控写入是 outbox 中的"事件"）。事件写入失败时只记录 warning 日志，不影响路由主链路（最终一致性可接受丢少量监控数据）。SQLite 的 `(request_id, model_id, event_type)` 唯一键配合幂等键保证重放安全。

---

## E7. 路由与评分系统

### E7.1 多维加权评分

**为何提出**

模型选择是一个多目标优化问题：既要质量（能力匹配度），又要成本（价格），还要可靠性（健康状态）、可用性（不限流）。单一维度排序无法同时优化多个目标。例如纯按能力排序，总是选最贵的模型；纯按成本排序，总是选最低质量的模型。需要一个能综合多个维度的评分体系。

**如何解决**

将多个维度转换为同一量纲的分数，再按某种规则（加权、分层、门控）融合：

1. **能力分（dimension_score）**：任务维度（reasoning、coding 等）× 模型能力矩阵，加权平均，反映"模型有多适合这个任务"。
2. **成本分（cost_score）**：按价格归一化（[0,1]），值越高表示越贵。排序时成本作为次要排序键（能力近似时选便宜的）。
3. **健康修正（health_modifier）**：成功历史给乘数加成（bonus），失败历史给乘数扣罚（penalty）。
4. **门控过滤**：不可用（`unable`）、限流、排除列表等作为硬过滤，不进入评分。

**同层对比**

| 融合策略 | 机制 | 优点 | 风险 |
|---|---|---|---|
| 加权求和 | 各维度权重相加 | 简单、连续 | 权重敏感，需调参 |
| 分层过滤 + 排序 | 先硬过滤，再主维度排序 | 可解释，门槛清晰 | 分层边界设计复杂 |
| 帕累托最优 | 不被任何方案在所有维度上同时支配 | 理论最优 | 候选集可能很大 |
| 机器学习排名 | 学习历史决策 | 自动适应 | 黑盒，冷启动难 |

本项目采用"分层融合"而非简单加权求和，原因是可解释性优先：每一层的决策逻辑清晰（先过滤不可用 → 再按能力排序 → 成本作为次要键换挡 → 健康信号修正）。

**本项目运作**

评分体系在 `scorer.py` 和 `selector.py` 中分层实现：

1. `compute_dimension_score()`（`scorer.py` 第 30-53 行）：任务维度 × 模型能力加权平均，结果 [0,1]。
2. `compute_cost_score()`（第 85-104 行）：价格归一化后用指数映射（`COST_ALPHA` 参数），使价差在低价区更明显。
3. 在 `selector.py` 的 `select_async()` 中：先按成本升序，再按能力降序双重排序（第 270-271 行）；近似同分时成本低的上浮（第 274-278 行）。
4. 健康修正在 `get_health_modifier()` 中：`bonus_level > 0` 时给成功奖励（但奖励与成本负相关，避免高成本模型被过度加分），`penalty_level > 0` 时乘以惩罚因子。

---

### E7.2 候选集构建（Selector）

**为何提出**

路由系统不仅要选出"最优"的单个模型，还要维护一个候选列表（ranked candidates），用于"起始模型失败时的后备切换"。候选集的构建策略决定了系统的韧性（一个坏了能切哪些）、多样性（避免单点）和学习能力（能发现更优模型）。

**如何解决**

三层候选构建策略：

1. **天花板保底（Ceiling）**：raw_dimension_score 最高的模型，不管历史，保证"最差情况下有最强能力"作为兜底。
2. **Pool 优先**：class pool（历史优质模型集合）中的候选优先入选，保证稳定性。
3. **探索补位**：预留 1-3 个探索槽位，从非池模型中按多样性选入（`_provider_diverse_limit` 控制同一 provider 比例上限），发现新优质模型。

`_provider_diverse_limit()` 保证候选集不全来自同一 provider，降低单点依赖风险。

**同层对比**

| 候选构建策略 | 稳定性 | 探索性 | 供应商分散 | 复杂度 |
|---|---|---|---|---|
| 纯 Top-K | 高 | 低（总是同一批） | 不保证 | 低 |
| Pool + 探索 | 高（pool 保稳） | 中（探索槽位） | 有（provider 限制） | 中 |
| 随机采样 | 低 | 高 | 随机 | 低 |
| 基于 UCB 的选择 | 中 | 高（算法驱动） | 不保证 | 高 |

**本项目运作**

`select_async()` 方法（`selector.py`）实现了完整的三层候选构建（第 280-327 行）：

- `CEILING_SLOTS = 1`：天花板模型始终在候选列表中（`ceiling_model = max(ranked, key=lambda item: item.raw_dimension_score)`）。
- Pool 候选（`pool_candidates`）：class pool 中健康、可用的模型，按评分排序后填入候选列表。
- 探索候选（`explore_candidates`）：`_adaptive_explore_slots()` 动态决定槽位数（`EXPLORE_SLOTS_MIN` 到 `EXPLORE_SLOTS_MAX`，实际值根据 pool 大小和 avg_trials 确定），`_provider_diverse_limit()` 控制供应商分散。

`is_explore=True` 标记在 `RouteDecision.candidates` 中，方便监控层追踪探索效果。

---

### E7.3 模型健康状态机

**为何提出**

模型的质量和可用性不是静态的：有时暂时超时，有时 API 返回错误，有时质量下滑。如果用静态配置标记模型状态，无法快速响应实时故障；如果每次都实时检测，延迟高且资源消耗大。状态机是一种能快速记录、快速响应、并具备自动恢复能力的动态健康追踪机制。

**如何解决**

四状态机（本项目简化版）：

```
available ─[执行失败累积]→ degraded ─[失败持续]→ unable
unable ─[cooldown结束]→ probe cooldown ─[探测成功]→ available
degraded ─[自然时间窗结束]→ available
```

- **available**：正常，可选，无惩罚。
- **degraded**：软惩罚，仍可选（`DEGRADED_PENALTY` 扣分），`DEGRADED_WINDOW_S` 秒后自动恢复。
- **unable**：硬过滤，不可选，需等待 `probe_loop_async()` 探测恢复。
- **probe cooldown**：探测成功后的冷却期（`PROBE_COOLDOWN_S`），可选但有 `PROBE_COOLDOWN_PENALTY` 扣分，防止立刚恢复就被打爆。

**同层对比**

| 健康追踪方式 | 响应速度 | 自动恢复 | 状态细粒度 | 实现复杂度 |
|---|---|---|---|---|
| 状态机（本项目） | 快（事件驱动） | 有（probe 机制） | 中（4 状态） | 中 |
| 心跳检测 | 中（轮询间隔） | 有 | 低（up/down） | 低 |
| 实时检测 | 最快 | 自动 | 高 | 高（性能开销） |
| 静态配置 | 无（手动更新） | 无 | 任意 | 最低 |

**本项目运作**

状态机存储在 `router_engine.db` 的 availability 表（`availability_repo.py`），通过 `HealthManager`（`health.py`）操作：

- 执行失败时：`report_exec_failure_async()` → `report_exec_failure_transition_async()` 更新状态。
- 选模型时：`is_available_async()` 返回 `(selectable, is_degraded, is_probe_cooldown)`，`selector.py` 据此过滤或扣分。
- 恢复时：`probe_loop_async()` 定期调用 `probe_unable_models_async()`，通过 `probe_callback`（通常是发送一个轻量测试请求）检测恢复。
- 质量历史（success/fail）存储在 stats 表，`get_health_modifier()` 将连续成功/失败转换为 `bonus_level`/`penalty_level`，再换算为评分乘数（`SUCCESS_BONUS_FACTOR`/`FAIL_PENALTY_FACTOR`）。

---

## E8. 可观测性（Observability）

### E8.1 监控三支柱

**为何提出**

系统出现问题时（延迟上升、错误率增加、路由策略偏移），工程师需要快速定位根因。单一的监控手段往往不足以全面诊断：
- 只有 Metrics：知道"错误率是 5%"，但不知道"是哪些请求错误、错误原因是什么"。
- 只有 Logs：能看到个别错误详情，但无法聚合看趋势。
- 只有 Traces：能看到单次请求链路，但无法跨请求统计。

三支柱互补，形成完整的可观测性体系。

**如何解决**

- **Metrics（指标）**：时序聚合数据（计数、直方图、gauge）。回答"整体趋势是什么"。工具：Prometheus、InfluxDB。
- **Logs（日志）**：结构化事件流。回答"每次发生了什么"。工具：ELK、Loki。
- **Traces（链路追踪）**：跨服务请求链路时序。回答"一次请求经过了哪些组件、各耗时多少"。工具：Jaeger、OpenTelemetry。

**同层对比**

| 工具 | 数据量 | 实时性 | 聚合能力 | 部署依赖 | 适用阶段 |
|---|---|---|---|---|---|
| SQLite 侧车（本项目） | 低（采样/保留） | 中（批量写） | 低（SQL 查询） | 无 | 早期/小规模 |
| Prometheus + Grafana | 高 | 实时 | 强 | 需额外服务 | 生产规模 |
| ELK Stack | 极高 | 近实时 | 强 | 重 | 大规模日志分析 |
| OpenTelemetry | 可变 | 实时 | 强（标准化） | 需 backend | 分布式追踪 |

**本项目运作**

本项目选择"SQLite 侧车优先"：`route_agent_monitoring.db` 存储路由决策事件（`monitoring/storage.py`）。

选择理由：
1. **低依赖**：无需外部 Prometheus/InfluxDB 服务，本地可运行。
2. **可离线排障**：SQLite 文件可直接复制查询，便于离线分析。
3. **可回放**：事件记录完整，可重放历史决策，验证策略调整效果。

代价：跨实例聚合能力弱（多进程部署时需合并多个 SQLite 文件）。演进路径：项目规模化后，可将监控写入对接 Prometheus 或 OpenTelemetry，SQLite 侧车降为本地调试工具。

---

### E8.2 Best-Effort 写入

**为何提出**

监控/日志写入是辅助操作，不是业务核心。如果监控写入失败（如 SQLite 锁竞争、磁盘空间不足）导致业务主链路（路由选模型）也失败，是明显的"本末倒置"。辅助操作不应影响主操作的可用性。

**如何解决**

Best-Effort 模式：尝试写入，如果失败，只记录 warning 日志，不向上层抛异常。主链路继续正常执行，不受监控写入成功与否影响。

```python
try:
    monitoring.write(event)
except Exception:
    logger.warning("monitoring write failed, event dropped")
    # 主链路继续
```

**同层对比**

| 写入策略 | 主链路可用性 | 数据完整性 | 适用 |
|---|---|---|---|
| Best-Effort | 最高（失败不阻塞） | 可能丢失部分 | 监控、日志、诊断 |
| At-Least-Once | 高（有重试） | 几乎完整（幂等处理） | 重要事件、计费 |
| Exactly-Once | 中（需事务协调） | 完整 | 财务、关键状态 |
| Transactional | 低（事务失败阻塞） | 强一致 | 业务核心状态 |

**本项目运作**

`monitoring/service.py` 的 `record_decision()` 方法包在 try-except 中，写入失败仅 `logger.warning()`。`route_agent/app/service.py` 在路由决策后调用监控写入，无论写入是否成功，都会返回路由结果给调用方。

SQLite 采用 WAL 模式（E2.2）进一步降低写入失败概率：监控写入与路由决策的读取并发不互阻。`monitoring/watch.py` 的实时监控也采用只读模式，不影响写入。

---

### E8.3 关键可观测字段

**为何提出**

日志和监控记录了什么字段，直接决定了排障时能回答哪些问题。字段设计不合理，排障时可能"日志有，但问不出来"；字段过多，存储成本高且难以抓住重点。关键字段设计是一种事先的"故障预案"——预判最可能遇到哪些问题，为每个问题预留对应的观测字段。

**如何解决**

按"典型故障场景"设计字段，确保每类故障都有对应的诊断维度：

| 故障场景 | 必要字段 | 目的 |
|---|---|---|
| 候选为空 | `routing_reason`、`candidate_count`、`filtered_reasons` | 定位为什么没有可用候选 |
| 限流触发 | `rpm_ratio`、`conc_ratio`、`is_limited`、`rate_limiter_mode` | 判断是哪个维度触发、是否降级模式 |
| Provider 抖动 | `provider`、`error_type`、`status`、`failure_streak` | 判断是单模型还是 provider 整体问题 |
| 策略偏移 | `score_breakdown`、`start_index_reason`、`pool_hit`、`is_explore` | 追踪为什么路由到了意料外的模型 |

**本项目运作**

`RouteDecision`（`router_engine/schemas.py`）中包含：`primary_model`、`candidates`（含每个候选的 `dimension_score`、`cost_score`、`health_status`、`is_pool`、`is_explore`、`rank`）、`start_index`、`reason`、`alerts`、`pool_hit`、`class_source`。

这些字段在 `monitoring/storage.py` 中被序列化写入 `route_agent_monitoring.db`，通过 `monitoring/service.py` 的 `get_recent_decisions()` 和 `get_stats()` API 可读取分析。`monitoring/watch.py` 提供实时流式观测（`watch_agent_status_async()`），支持运维人员实时监控路由行为。

---

## E9. 各概念之间的关系图

从"任务请求"到"监控落盘"的数据流（ASCII）：

```text
[Task Request]
      |
      v
[Task Analyzer]                         ← E5.1-E5.4（统计决策）
  (结构化维度: domain, complexity,
   relevant_dimensions)
      |
      v
[Selector + Scorer]                     ← E7.1-E7.3（评分 + 候选集 + 健康状态机）
  (天花板保底 + Pool优先 + 探索槽位)
  (dimension_score × health_modifier
   - cost换挡 - degraded/cooldown扣分)
      |
      v
[Rate Limiter]                          ← E1-E4（Redis + 限流算法）
  (RPM: ZSET滑窗60s)
  (RPD: ZSET滑窗86400s)
  (并发: INCR/DECR + TTL300s)
      |
      v
[Execute / Retry / Escalate / Downgrade Canary]  ← E3.3（重试风暴）、E5.3（Canary）
      |
      +--------------------------+
      |                          |
      v                          v
[Router Storage(SQLite)]    [Monitoring Sidecar(SQLite)]  ← E8（可观测性）
(幂等事件/统计/状态机         (best-effort写入: E8.2)
 BEGIN IMMEDIATE: E2.4       (SQLite WAL: E2.2)
 WAL + busy_timeout: E2.2-3)
      |                          |
      +------------+-------------+
                   v
             [排障与回放分析]    ← E8.3（关键字段）
```

**每一层一句话原则**

- **任务分析层**：输入必须结构化（domain + relevant_dimensions），避免下游评分策略因任务描述漂移。
- **候选与评分层**：先保可用再排序，能力主导、成本次要换挡、健康信号做软/硬修正。
- **限流层**：先事前保护（RPM/RPD/并发三维），再失败处理（降级 InMemory / fail_fast），避免重试放大冲击 provider。
- **执行决策层**：先稳后省（Canary 下行试验），升阶（escalation）救火与降级（downgrade）试验分工明确，互不干扰。
- **存储层**：关键状态（健康状态机、class pool）走 BEGIN IMMEDIATE 强一致，观测写入 best-effort 弱一致。
- **观测层**：字段可解释（`reason`/`alerts`/`score_breakdown`）、可回放（事件记录完整）、可流式（watch API），支持快速定位。

---

## E10. 常见面试追问与速答模板

**1. Redis ZSET vs INCR 的场景区分**

速答：`ZSET` 用于时间窗计数（RPM/RPD）——需要"裁剪过期记录后精确计数"；`INCR/DECR` 用于并发 in-flight 计数——只需要原子自增自减。两者通常组合而不是互斥：本项目三维限流 = ZSET（时间窗）× 2 + INCR（并发）。

本项目关联：Q06、Q07、Q62。

**2. SQLite WAL + Redis 单线程 != 无并发问题**

速答：WAL 只缓解"读写互阻"（读不再等写），SQLite 仍是单 writer，写写冲突依然存在，所以仍需 `busy_timeout` 和 `BEGIN IMMEDIATE`；Redis 单线程只保证单条命令原子，多命令组合（如 GET + SET）不原子，仍需 Lua 或业务层设计来消除竞态。

本项目关联：Q01、Q02、Q63。

**3. Wilson 下界 vs 普通成功率**

速答：成功率（p = success/n）只看比例，不考虑样本量可信度；Wilson 下界同时考虑 p 和 n，样本量小时下界显著低于 p，提供"置信保守估计"。3 次全成功的 WLB ≈ 0.43，1000 次 95% 成功的 WLB ≈ 0.936，正确地后者排前。适合"入池/晋升门槛"、"降级试验评估"场景。

本项目关联：Q26、Q48、`downgrade.py:31`。

**4. SQLite 侧车 vs Prometheus/OTel**

速答：SQLite 侧车优势——零部署依赖、本地可离线查询、事件完整可回放，适合单机或早期阶段；Prometheus/OTel 优势——实时告警、跨实例聚合、生态工具丰富，适合规模化多实例部署。架构演进路径：早期 SQLite 侧车，规模化后监控接入 Prometheus，SQLite 降为本地调试辅助。

本项目关联：Q19、Q32。

**5. 探索槽位 vs ε-Greedy**

速答：探索槽位是工程化的"候选位预算"——每次路由固定保留 1-3 个位置给非池模型，探索数量有上限，可解释，风险可控；ε-Greedy 是概率策略——以 ε 概率随机选任意臂，探索方向不可控（可能重复探索已知差模型）。前者更保守但更安全，后者更算法化但需要调参。

本项目关联：Q15、Q25、Q43、Q35。

**6. 降级 vs 熔断**

速答：两者目标相近但机制不同。降级是"依赖 A 故障时，切换到能力更弱的备选 A'"（保持链路可用，牺牲部分功能）；熔断是"依赖 A 故障时，直接切断对 A 的调用，快速失败"（防止故障蔓延，功能暂不可用）。本项目中：`degrade` 策略 = Redis 故障时降级到 InMemory（降级）；`fail_fast` 策略 = Redis 故障时直接失败（熔断语义）；模型健康状态机的 `unable` 状态 = 熔断（模型故障时被排除出候选集）。

本项目关联：Q30、Q65、Q67。

**7. Canary 试验 vs A/B 测试**

速答：Canary 是单向验证（challenger 是否达到与 incumbent 相近的质量标准），目标是"能否安全降级"；A/B 是双向对比（哪个方案更优），目标是"哪个更好"。本项目的 Canary 方向是"下行"（更便宜的 challenger），不是"上行"（更强的 challenger），这与常见的 Canary 发布方向相反——这是成本优化驱动的特殊设计。

本项目关联：Q18、Q27、Q59、Q60、`downgrade.py`。
