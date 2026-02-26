# Route Agent 项目面试题库（Questions & Standard Answers）

> 面向：路由/推荐策略 + 高并发工程 + 多模型平台  
> 使用方法：每题按”**结论一句话**→**关键点拆解**→**常见坑/边界**→**取舍对比**→**可优化点**”回答；遇到追问按”定义→实现→边界→取舍→优化”展开。

## 通用回答模板（Q01–Q35 全部适用）

> 以下框架适用于 Q01–Q35 每道题的各段，各题中不再重复声明：
>
> - **关键词**：吞吐（throughput）、尾延迟（tail latency）、背压（backpressure）、幂等（idempotency）。
> - **定义段**：优先写清数据结构/状态、更新时机、并发控制点（锁/事务/限流）与关键阈值（窗口/比例/上限）。
> - **边界段**：至少列 3 个会被高并发放大的边界（重试风暴、热 key、长事务、陈旧数据等），并说明如何观测定位。
> - **取舍段**：对比至少 1 个替代设计（更简单/更强一致/更智能），说明收益、成本与为什么当前不采用。
> - **优化段**：给出短/中/长期优化路线，并标注验收指标（成功率、平均成本、P95/P99、429/timeout、候选为空率）。

---

## 简历要点（B1–B7）

- **B1**：路由打分与排序（算法）：对每个候选模型计算“能力匹配分 + 健康度修正 − 拥塞/降级惩罚”，能力匹配按任务相关维度做加权平均（缺失能力按中位默认值处理）；成本侧将输入/输出单价按任务类型信号动态加权为“有效单价”，再用指数归一化得到成本分，用于与能力分共同排序并在近似分数时以更低成本优先  
- **B2**：候选集构建（探索/多样性）：采用“保底强模型 + 历史命中池优先 + 探索补位”的 Top 候选组合；探索槽位随池子丰富度与历史试验次数自适应调整；对候选增加“同一供应商占比上限”约束，避免高并发流量集中打到单一 provider  
- **B3**：在线学习闭环（统计决策）：按业务/代理类别维护模型池与默认模型，基于成功/失败样本统计计算置信下界，达到阈值才允许入池与晋升默认；对连续失败触发淘汰/惩罚，降低策略在高并发下被偶然噪声带偏的概率  
- **B4**：降级试验（canary 优化成本）：当默认模型稳定成功后，自动挑选更低价 challenger 发起降级试验；设置最小节省比例、样本量门槛、冷却期与回滚条件，在不牺牲质量的前提下持续用小流量验证并逐步推广更低成本模型  
- **B5**：升阶与重试（状态机）：对执行失败/质量差按策略触发重试或升阶；升阶前结合实时利用率与并发上限做过载检查，拥塞时抑制升阶，避免高并发下“失败→升阶→更拥塞”的连锁放大  
- **B6**：高并发限流（工程实现细节）：Redis 模式用滑动窗口统计 RPM/RPD（有序集合裁剪+计数）与并发计数（自增/自减+TTL），并区分正常流量与升阶流量设置升阶并发封顶；提供利用率缓存与本地内存降级模式，Redis 不可用时按策略自动切换，保证路由服务持续可用；核心证据来自 `route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py` 与 `scripts/perf_ab_compare.py` 的 A/B：实验组（重叠批次）3 次中位数为 `elapsed_seconds=20.625`、`throughput_rps=0.97`、`success_throughput_rps=0.97`、`success_rate=1.0`、`rejection_rate=0.0`、`completion_all_p95=14684.15ms`、`allocatable/eligible/assigned=12/12/12`；对照组（同 workload/同批次节奏 + `control_strategy=pin_model` 固定 `google:gemini-2.5-pro`）3 次中位数为 `elapsed_seconds=66.11`、`throughput_rps=0.303`、`success_throughput_rps=0.303`、`success_rate=1.0`、`rejection_rate=0.929`、`completion_all_p95=61437.85ms`、`allocatable/eligible/assigned=12/1/1`，并且窗口锚点固定为 `t0=min(submit_ts)`（`window_start_ts=0.0`、`window_end_ts=60.0`），可直接复算窗口指标；超时边界与指标定义口径详见 Q20。  
- **B7**：并发安全与落盘：异步优先编排，后台探测任务维护模型健康状态；关键写入采用事务/锁策略确保并发下统计一致性；路由事件、执行生命周期与聚合指标落盘，支持快速定位“限流触发、候选为空、provider 抖动、策略偏移”等高并发问题；全量测试 84/84 通过（22.54s）作为稳定性补充，支撑高并发策略迭代的可回归验证。

---

## Part 0：按项目模块整理（Q01-Q71 + B1-B7）

> 索引规则：每题仅有 1 个主归属；跨模块信息通过次标签标注。

### 模块分组区

#### cross-module
- **Q01** 什么是“高并发”下的锁竞争？在你们项目里主要体现在哪些地方？ | 主归属：cross-module | 次标签：router_engine / monitoring | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。
- **Q02** SQLite 在高并发写场景的锁策略怎么选？WAL 有什么用？ | 主归属：cross-module | 次标签：router_engine/storage / monitoring/storage | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。
- **Q03** 什么是 `BEGIN IMMEDIATE`/事务隔离在 SQLite 中的意义？为什么要用它？ | 主归属：cross-module | 次标签：router_engine/storage | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。
- **Q04** 你们如何保证“同一请求/同一事件”在重试/并发下不会重复计数？ | 主归属：cross-module | 次标签：router_engine / monitoring | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。
- **Q20** 你会怎么给这类系统设计测试用例？哪些必须覆盖？ | 主归属：cross-module | 次标签：tests/core | 项目关联：对应 docs/TESTING_GUIDE.md 与各模块 tests 目录的测试分层和覆盖策略设计。
- **Q33** 当某个模型成为热点，哪些表/哪些计数会变成写热点？你会怎么优化？ | 主归属：cross-module | 次标签：router_engine/storage / monitoring/storage | 项目关联：对应 router_engine/storage 与 monitoring/storage 的写热点识别、分流与扩展优化路径。
- **Q68** 哪些写入必须强一致，哪些可以 best-effort？ | 主归属：cross-module | 次标签：monitoring / router_engine/storage | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。
- **Q69** 事务/锁如何避免长事务引发重试风暴？ | 主归属：cross-module | 次标签：router_engine/storage | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。
- **Q70** 事件幂等键如何设计以防重复计数和统计污染？ | 主归属：cross-module | 次标签：task_analyzer / router_engine / monitoring | 项目关联：横切并发一致性/事务/幂等主题，贯穿 router_engine/storage 与 monitoring/storage 的状态更新和事件落盘。

#### task_analyzer
- **Q09** 为什么要 Structured Output（JSON Schema/Pydantic）？不做会怎样？ | 主归属：task_analyzer | 次标签：app | 项目关联：对应 task_analyzer 的结构化输出、维度约束与分析置信度治理（analyzer.py/prompt.py/schemas.py）。
- **Q10** 任务分析为什么只输出“相关维度”？怎么保证维度集合可控？ | 主归属：task_analyzer | 次标签：router_engine/scorer | 项目关联：对应 task_analyzer 的结构化输出、维度约束与分析置信度治理（analyzer.py/prompt.py/schemas.py）。
- **Q39** 任务维度缺失或置信不足时如何稳健打分？ | 主归属：task_analyzer | 次标签：router_engine/scorer | 项目关联：对应 task_analyzer 的结构化输出、维度约束与分析置信度治理（analyzer.py/prompt.py/schemas.py）。

#### model_registry
- **Q11** 候选模型能力字段缺失时为什么用中位/默认值？有什么替代？ | 主归属：model_registry | 次标签：router_engine/scorer | 项目关联：对应 model_registry 的 provider 归一化、快照优先与到期刷新/失败回退策略（service.py/providers/*/storage/*）。
- **Q12** Provider 适配层需要统一哪些差异？你们怎么做归一化？ | 主归属：model_registry | 次标签：app | 项目关联：对应 model_registry 的 provider 归一化、快照优先与到期刷新/失败回退策略（service.py/providers/*/storage/*）。
- **Q13** 为什么要“快照优先 + 到期刷新 + 失败回退”？ | 主归属：model_registry | 次标签：app | 项目关联：对应 model_registry 的 provider 归一化、快照优先与到期刷新/失败回退策略（service.py/providers/*/storage/*）。
- **Q31** 快照优先会不会导致价格/能力过期？怎么降低风险？ | 主归属：model_registry | 次标签：router_engine | 项目关联：对应 model_registry 的 provider 归一化、快照优先与到期刷新/失败回退策略（service.py/providers/*/storage/*）。

#### router_engine/health
- **Q16** 什么是 degraded/unable？它们在路由里怎么影响候选？ | 主归属：router_engine/health | 次标签：router_engine/selector | 项目关联：对应 health.py 的模型健康状态机及其对候选过滤/惩罚的影响。

#### router_engine/scorer
- **Q21** 你们怎么把能力、成本、健康、拥塞融合到同一排序里？为什么这样融合？ | 主归属：router_engine/scorer | 次标签：B1 | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q22** 为什么要输入/输出动态加权？如果不加权会怎样？ | 主归属：router_engine/scorer | 次标签：B1 | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q23** 用默认值填补能力缺失会带来什么统计偏差？怎么缓解？ | 主归属：router_engine/scorer | 次标签：model_registry | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q34** 为什么不用“纯规则/纯成本最小/纯能力最大/固定 tier 路由”？ | 主归属：router_engine/scorer | 次标签：architecture | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q35** 如果让你升级到学习型策略，你会选什么？为什么现在不直接用？ | 主归属：router_engine/scorer | 次标签：router_engine/class_pool | 项目关联：对应 scorer + class_pool 的策略演进方向（从启发式到学习型闭环）。
- **Q36** 能力分/成本分/健康修正/拥塞惩罚的融合公式如何设计？ | 主归属：router_engine/scorer | 次标签：B1 | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q37** 为什么能力分与成本分分开建模后再融合？ | 主归属：router_engine/scorer | 次标签：B1 | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q38** 近似分数阈值如何设置并验证不伤质量？ | 主归属：router_engine/scorer | 次标签：B1 | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q40** 成本“输入/输出动态加权”如何按任务类型落地？ | 主归属：router_engine/scorer | 次标签：task_analyzer | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。
- **Q41** 健康修正与拥塞惩罚如何避免重复惩罚？ | 主归属：router_engine/scorer | 次标签：router_engine/health | 项目关联：对应 scorer.py 的能力-成本-健康-拥塞融合打分、排序与阈值策略。

#### router_engine/selector
- **Q14** 为什么限制同一 provider 的候选占比？对效果有什么影响？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q15** 为什么要探索（exploration）？探索比例怎么定？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q24** 为什么不是在全局做 provider 负载均衡，而是在候选集阶段限制占比？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q25** 探索槽位“自适应”具体靠什么信号？为什么不是固定比例？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q42** “保底强模型+池优先+探索补位”组合的触发顺序是什么？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q43** 探索槽位自适应依赖哪些在线信号？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q44** 同 Provider 占比上限如何设定与退化？ | 主归属：router_engine/selector | 次标签：B2 | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q45** 候选阶段多样性与全局负载均衡如何分工？ | 主归属：router_engine/selector | 次标签：router_engine/rate_limiters | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。
- **Q47** 候选不足时 Top-K 退化策略如何保证可用性？ | 主归属：router_engine/selector | 次标签：router_engine/escalation | 项目关联：对应 selector.py 的候选编排、探索补位、provider 占比约束与退化策略。

#### router_engine/class_pool
- **Q26** 为什么用 Wilson lower bound（或类似置信下界）做入池/晋升门槛？ | 主归属：router_engine/class_pool | 次标签：B3 | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q46** 池命中优先如何避免路径依赖与模型固化？ | 主归属：router_engine/class_pool | 次标签：router_engine/selector | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q48** 为什么用置信下界而不是 success rate 做入池/晋升？ | 主归属：router_engine/class_pool | 次标签：B3 | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q49** 入池阈值、晋升阈值、淘汰阈值如何协同？ | 主归属：router_engine/class_pool | 次标签：B3 | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q50** 连续失败惩罚如何区分模型退化与外部异常？ | 主归属：router_engine/class_pool | 次标签：router_engine/health | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q51** 默认模型切换如何防抖（promote/rollback 抖动）？ | 主归属：router_engine/class_pool | 次标签：router_engine/defaults | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q52** 类池学习中的冷启动如何避免噪声误导？ | 主归属：router_engine/class_pool | 次标签：B3 | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。
- **Q53** 反馈延迟或缺失时学习闭环如何保持稳定？ | 主归属：router_engine/class_pool | 次标签：storage | 项目关联：对应 class_pool.py/defaults.py 与 storage repos 的入池、晋升、淘汰及防抖闭环。

#### router_engine/escalation
- **Q17** 升阶一般发生在什么条件？为什么要做过载检查？ | 主归属：router_engine/escalation | 次标签：B5 | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q29** 为什么在升阶前检查利用率？为什么 normal/elevated 不同阈值？ | 主归属：router_engine/escalation | 次标签：router_engine/rate_limiters | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q54** 升阶状态机里 retry 与 escalate 的分界是什么？ | 主归属：router_engine/escalation | 次标签：B5 | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q55** 为什么区分 quality fail 与 exec fail 两类失败信号？ | 主归属：router_engine/escalation | 次标签：router_engine/downgrade | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q56** 升阶前过载检查为何放在动作前而非动作后？ | 主归属：router_engine/escalation | 次标签：router_engine/rate_limiters | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q57** normal/elevated/forced 优先级的流量语义如何定义？ | 主归属：router_engine/escalation | 次标签：router_engine/rate_limiters | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q58** 升阶并发封顶如何防止“越忙越升阶”正反馈？ | 主归属：router_engine/escalation | 次标签：router_engine/rate_limiters | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。
- **Q61** 升阶与降级同时触发时如何裁决优先级？ | 主归属：router_engine/escalation | 次标签：router_engine/downgrade | 项目关联：对应 escalation.py 的 retry/escalate 判定、优先级语义与过载前置检查。

#### router_engine/downgrade
- **Q18** 为什么要“降级试验（canary）”而不是直接切默认到更便宜的模型？ | 主归属：router_engine/downgrade | 次标签：B4 | 项目关联：对应 downgrade.py 的 canary 参数联动、推广回滚与冲突裁决规则。
- **Q27** canary 比例、最小节省、最小样本量、冷却期分别解决什么问题？ | 主归属：router_engine/downgrade | 次标签：B4 | 项目关联：对应 downgrade.py 的 canary 参数联动、推广回滚与冲突裁决规则。
- **Q28** 为什么区分 exec fail 和 quality fail？两者的策略含义不同在哪？ | 主归属：router_engine/downgrade | 次标签：router_engine/health | 项目关联：对应 downgrade.py 的 canary 参数联动、推广回滚与冲突裁决规则。
- **Q59** 降级 canary 比例、样本量、节省阈值如何联动？ | 主归属：router_engine/downgrade | 次标签：B4 | 项目关联：对应 downgrade.py 的 canary 参数联动、推广回滚与冲突裁决规则。
- **Q60** 降级试验中的回滚条件为何执行失败更敏感？ | 主归属：router_engine/downgrade | 次标签：B4 | 项目关联：对应 downgrade.py 的 canary 参数联动、推广回滚与冲突裁决规则。

#### router_engine/rate_limiters
- **Q05** RPM/RPD/并发上限分别保护什么？为什么三者都要？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q06** Redis 滑动窗口限流怎么实现？为什么不用固定窗口或单计数器？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q07** 并发计数为什么要 `incr/decr + TTL`？有什么坑？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q08** 什么是限流“利用率（utilization）”？你们用它做了什么决策？ | 主归属：router_engine/rate_limiters | 次标签：router_engine/escalation | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q30** 为什么要支持 Redis / In-memory / Off + Auto 降级？为什么不是强依赖 Redis？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q62** RPM/RPD/并发三限流维度的决策优先级如何定？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q63** Redis 滑窗在高并发下的误差与一致性边界是什么？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q64** normal/escalation 分桶计数为何优于单桶并发计数？ | 主归属：router_engine/rate_limiters | 次标签：B6 | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q65** Redis 不可用切 InMemory 时风险如何评估与告警？ | 主归属：router_engine/rate_limiters | 次标签：monitoring | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q66** 利用率缓存窗口如何在准确性与降压间平衡？ | 主归属：router_engine/rate_limiters | 次标签：performance | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。
- **Q67** fail_fast 与 degrade 两种策略的适用场景是什么？ | 主归属：router_engine/rate_limiters | 次标签：router_engine/escalation | 项目关联：对应 rate_limiters 的 RPM/RPD/并发控制、Redis/InMemory 切换与利用率治理。

#### monitoring
- **Q19** 你们监控记录哪些数据来支持排障？为什么要“best-effort”？ | 主归属：monitoring | 次标签：cross-module | 项目关联：对应 monitoring/service.py 与 storage.py 的决策落盘、统计聚合与排障查询能力。
- **Q32** 为什么选择 SQLite 侧车而不是 MQ/OTel/时序数据库？ | 主归属：monitoring | 次标签：architecture | 项目关联：对应 monitoring/service.py 与 storage.py 的决策落盘、统计聚合与排障查询能力。
- **Q71** 落盘观测如何快速定位候选为空/限流触发/provider抖动？ | 主归属：monitoring | 次标签：router_engine | 项目关联：对应 monitoring 的决策事件字段体系，用于快速定位候选为空/限流触发/provider 抖动。

### 追问链区（B1-B7）

- **B1** | 主归属：router_engine/scorer | 次标签：- | 关联题：Q21 Q11 Q8 Q22 Q10 Q12 Q34 Q35 Q36 Q38 Q39 Q41
- **B2** | 主归属：router_engine/selector | 次标签：- | 关联题：Q15 Q14 Q25 Q24 Q35 Q42 Q43 Q44 Q46 Q47
- **B3** | 主归属：router_engine/class_pool | 次标签：- | 关联题：Q26 Q16 Q04 Q20 Q35 Q48 Q49 Q51 Q52 Q53
- **B4** | 主归属：router_engine/downgrade | 次标签：- | 关联题：Q18 Q27 Q28 Q35 Q59 Q60 Q61
- **B5** | 主归属：router_engine/escalation | 次标签：- | 关联题：Q17 Q29 Q8 Q30 Q21 Q54 Q56 Q57 Q58
- **B6** | 主归属：router_engine/rate_limiters | 次标签：- | 关联题：Q05 Q08 Q06 Q07 Q30 Q33 Q32 Q62 Q63 Q64 Q65 Q67
- **B7** | 主归属：monitoring | 次标签：cross-module | 关联题：Q19 Q13 Q02 Q03 Q33 Q31 Q32 Q20 Q35 Q68 Q69 Q70 Q71

### 反向索引区（按题号快速查模块）

- Q01 -> cross-module
- Q02 -> cross-module
- Q03 -> cross-module
- Q04 -> cross-module
- Q05 -> router_engine/rate_limiters
- Q06 -> router_engine/rate_limiters
- Q07 -> router_engine/rate_limiters
- Q08 -> router_engine/rate_limiters
- Q09 -> task_analyzer
- Q10 -> task_analyzer
- Q11 -> model_registry
- Q12 -> model_registry
- Q13 -> model_registry
- Q14 -> router_engine/selector
- Q15 -> router_engine/selector
- Q16 -> router_engine/health
- Q17 -> router_engine/escalation
- Q18 -> router_engine/downgrade
- Q19 -> monitoring
- Q20 -> cross-module
- Q21 -> router_engine/scorer
- Q22 -> router_engine/scorer
- Q23 -> router_engine/scorer
- Q24 -> router_engine/selector
- Q25 -> router_engine/selector
- Q26 -> router_engine/class_pool
- Q27 -> router_engine/downgrade
- Q28 -> router_engine/downgrade
- Q29 -> router_engine/escalation
- Q30 -> router_engine/rate_limiters
- Q31 -> model_registry
- Q32 -> monitoring
- Q33 -> cross-module
- Q34 -> router_engine/scorer
- Q35 -> router_engine/scorer
- Q36 -> router_engine/scorer
- Q37 -> router_engine/scorer
- Q38 -> router_engine/scorer
- Q39 -> task_analyzer
- Q40 -> router_engine/scorer
- Q41 -> router_engine/scorer
- Q42 -> router_engine/selector
- Q43 -> router_engine/selector
- Q44 -> router_engine/selector
- Q45 -> router_engine/selector
- Q46 -> router_engine/class_pool
- Q47 -> router_engine/selector
- Q48 -> router_engine/class_pool
- Q49 -> router_engine/class_pool
- Q50 -> router_engine/class_pool
- Q51 -> router_engine/class_pool
- Q52 -> router_engine/class_pool
- Q53 -> router_engine/class_pool
- Q54 -> router_engine/escalation
- Q55 -> router_engine/escalation
- Q56 -> router_engine/escalation
- Q57 -> router_engine/escalation
- Q58 -> router_engine/escalation
- Q59 -> router_engine/downgrade
- Q60 -> router_engine/downgrade
- Q61 -> router_engine/escalation
- Q62 -> router_engine/rate_limiters
- Q63 -> router_engine/rate_limiters
- Q64 -> router_engine/rate_limiters
- Q65 -> router_engine/rate_limiters
- Q66 -> router_engine/rate_limiters
- Q67 -> router_engine/rate_limiters
- Q68 -> cross-module
- Q69 -> cross-module
- Q70 -> cross-module
- Q71 -> monitoring

---

## Part A：基础/中等（20 题）

### Q01（基础概念）什么是“高并发”下的锁竞争？在你们项目里主要体现在哪些地方？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：高并发下的锁竞争本质是“共享状态更新被串行化”，带来排队（queueing）、尾延迟（P95/P99）上升与连锁放大。
  - 锁不仅是显式 mutex/lock，也包括数据库写锁、Redis 单 key 热点、线程池排队、连接池耗尽等“隐式串行瓶颈”。
  - 判断是否锁竞争：看同一资源的等待时间是否随并发非线性上升（convoy effect），以及失败是否集中在 timeout/locked/429。
- **实现：**
  - 本项目典型共享资源：SQLite 文件写（单 writer）、模型统计/健康状态更新（同一 model_id/agent_class 热点行）、限流计数（同一模型 key 热点）、监控事件写入（写放大）。
  - 第一层缓解：WAL + busy_timeout≈3s（缩短读写互阻），并将监控/学习等副作用写设置为 best-effort（失败不阻断主链路）。
  - 第二层缓解：减少写频率（批量写/聚合写/采样写）、幂等（unique key）把重试变成 no-op、热路径只读/冷路径才写。
- **边界：**
  - SQLite 天然单 writer：写热点无法被 WAL 完全解决；当写压力持续存在时会出现“锁等待→超时→重试→更拥塞”雪崩。
  - Redis 热 key：即使集群整体有余量，单 key 的 CPU 与网络也可能成为瓶颈；pipeline/Lua 只能缓解部分 RTT。
  - 无幂等的重试：会把一次错误放大成多次写入与多次计数，污染统计并误导在线学习。
- **取舍：**
  - 为什么不把所有写都做强一致同步：会把监控/学习变成主链路单点，降低可用性；通常以“主链路可用性优先”。
  - 为什么不一开始就上分布式 DB/MQ：对单机/CLI/MVP 成本过高；先靠降写、限流与异步化往往足够。
- **优化：**
  - 短期：对高频状态加本地缓存（例如 utilization 缓存≈150ms）、监控采样、减少每请求落盘次数。
  - 中期：引入异步事件管道（outbox/MQ），把聚合统计从同步写转为异步消费（eventual consistency）。
  - 长期：拆分写模型（服务型 DB/流式系统）与读模型（OLAP/TSDB），支持水平扩展与离线回放训练。

**Related：B6, B7**

### Q02（锁策略）SQLite 在高并发写场景的锁策略怎么选？WAL 有什么用？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：WAL（Write-Ahead Logging）主要改善“读写互阻”，但不能让 SQLite 写吞吐随并发线性扩展（写仍是串行提交）。
  - SQLite 适合“读多写少 + 单机落盘/侧车审计”场景；写多场景要靠“减少写/异步化/迁移存储”。
- **实现：**
  - 推荐组合（常见工程折中）：`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=3000; PRAGMA wal_autocheckpoint=1000;`
  - WAL 机制：写先落 WAL 文件、读读快照（snapshot），因此读不必等待写事务完成。
  - busy_timeout≈3s：把短期写冲突从“立刻失败”变成“有限等待”，降低 database is locked 的瞬时失败率。
  - checkpoint：控制 WAL 增长与合并频率；过于频繁会增加 IO，过于稀疏会导致 WAL 膨胀。
- **边界：**
  - 写写冲突：WAL 不解决写写排队，长事务会显著放大尾延迟；应避免在事务内做网络/重计算。
  - 部署环境：网络盘/异常文件系统可能让 WAL 效果变差；需要在目标环境压测验证。
  - 高并发下“等待”不是免费：busy_timeout 会把失败变成延迟，需配合上游超时与降级。
- **取舍：**
  - 为什么仍选 SQLite：零依赖、易部署、便于本地回放；用于快照缓存/监控侧车非常合适。
  - 为什么不直接用 Postgres：引入运维复杂度；在单机与轻量写场景收益未必覆盖成本。
- **优化：**
  - 短期：缩短写事务、减少索引写放大、对监控/统计做采样与批处理。
  - 中期：把高频计数迁到 Redis（或内存）+ 周期性落盘；SQLite 仅保留审计与快照。
  - 长期：迁移到服务型 DB 并做分区/归档；引入异步写管道支撑更高吞吐。

**Related：B7**

### Q03（事务）什么是 `BEGIN IMMEDIATE`/事务隔离在 SQLite 中的意义？为什么要用它？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：`BEGIN IMMEDIATE` 的价值是“先拿写锁再执行复合逻辑”，把并发下的不确定回滚变成可预测的等待/失败。
  - 在 SQLite 的 check-then-act（先查再写）场景中，如果不包事务，多个并发可能同时读到相同状态然后都写，造成竞态与越界。
- **实现：**
  - 典型模式：`BEGIN IMMEDIATE;` → `SELECT` 当前计数/状态 → 根据结果 `INSERT/DELETE/UPDATE` → `COMMIT;`
  - 适用：池容量上限（例如 pool max size≈10）检查+淘汰+插入、默认模型切换、需要原子保障的“统计更新+条件判断”。
  - 与 busy_timeout 配合：允许短时间等待写锁，避免高并发下频繁回滚与重试风暴。
- **边界：**
  - 不能滥用：所有写都用 IMMEDIATE 会显著降低写并发；应只用于“冷路径/关键一致性”。
  - 长事务风险：事务内做复杂计算/网络 IO 会延长锁持有时间，放大队列与尾延迟。
  - 失败语义：获取锁失败应有明确降级路径（跳过写/延迟写），否则上游重试会放大拥塞。
- **取舍：**
  - 为什么不只靠 unique constraint：唯一约束保证最终不重复，但无法保证“中间过程”的原子淘汰/计数逻辑。
  - 为什么不做全量 OCC：OCC 需要更多应用层重试逻辑；IMMEDIATE 在关键点更简单可控。
- **优化：**
  - 短期：把“热读 + 冷写”分离，冷写才使用 IMMEDIATE；并对冷写做批处理。
  - 中期：将强一致复合操作迁到支持更强并发写的 DB，或用 Redis Lua 实现原子决策。
  - 长期：事件溯源（event sourcing）+ 幂等消费，以可回放校正替代强事务依赖。

**Related：B3, B7**

### Q04（幂等）你们如何保证“同一请求/同一事件”在重试/并发下不会重复计数？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：幂等（idempotency）就是“同一语义事件写多次，状态只改变一次”，是高并发重试体系的地基。
  - 没有幂等：一次超时→重试→重复扣配额/重复计数→统计污染→策略被误导。
- **实现：**
  - 幂等键：用稳定的 `request_id` 作为请求级标识，再组合 `model_id + event_type`（如 exec_success/exec_fail/quality_good/quality_fail）。
  - 存储层：对幂等键加 unique constraint；写入事件表成功才更新聚合统计；若 unique 冲突则直接 no-op。
  - 顺序：先写事件，再写聚合，是为了让聚合更新可重放/可对账（eventual consistency）。
  - 监控侧车同理：execution 以 execution_id 为主键；重复 start/end 变成覆盖更新而非重复插入。
- **边界：**
  - request_id 不稳定：需先解决“唯一性/可复现性”，否则幂等不可用；临时方案是 task_hash + caller_id，但会有碰撞与误去重风险。
  - 幂等键太粗：会误去重（不同请求被当成同一件事）；太细：会去重失败（同一请求被当成不同事）。
  - 跨存储一致性：事件落 SQLite、计数在 Redis 时，只能保证最终一致；必须提供对账/重放能力。
- **取舍：**
  - 为什么不用“先查再写”：并发下查写会竞态；unique constraint 是更可靠的并发控制原语。
  - 为什么不追求 exactly-once：实现成本高；工程上用“至少一次 + 幂等消费”更稳健。
- **优化：**
  - 短期：把幂等冲突率做指标；冲突率高通常意味着上游重试风暴或 request_id 构造问题。
  - 中期：用 outbox pattern（同事务写事件+业务表）提高一致性与可恢复性。
  - 长期：建立事件回放与对账任务，定期校正聚合统计与学习状态。

**Related：B3, B5, B7**

### Q05（限流概念）RPM/RPD/并发上限分别保护什么？为什么三者都要？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：RPM 控短时突刺、RPD 控长周期配额/成本、并发上限控排队与超时；三者缺一就会留下系统性漏洞。
  - 在 LLM 调用里，供应商限制通常同时存在（显式或隐式），需要多维保护形成稳定闭环。
- **实现：**
  - RPM：滑动窗口 60s 计数；ratio=`rpm_count/rpm_limit`，用于候选 skip 与升阶抑制（例如 normal 阈值≈0.9）。
  - RPD：滑动窗口 86400s 计数；防止一天内慢慢把额度打满或成本失控。
  - 并发：in-flight 计数；区分 normal 与 escalation，并设置 escalation 并发封顶比例≈0.3。
  - 利用率（utilization）：把 rpm_ratio 与 conc_ratio 统一到 [0,1]，用 peak 决策；可加短缓存（≈150ms）降压。
- **边界：**
  - 只控 RPM：长时间中等负载仍可能耗尽日配额；成本不可控。
  - 只控 RPD：短时突刺仍会触发 429/超时，引发重试风暴与级联失败。
  - 只控并发：若请求很快结束，RPM 仍可能爆；若请求很慢，RPD 未满但并发已打满导致雪崩。
- **取舍：**
  - 为什么不只看 429：429 是事后信号，会触发重试放大；限流应尽量事前（proactive）。
  - 为什么不做强一致全局调度：实现复杂；很多场景“近似一致 + 降级”已能显著降低事故率。
- **优化：**
  - 短期：引入 token 维度（TPM）限流，让保护更贴近真实成本与负载。
  - 中期：按 caller/agent_class 分桶限流，避免单客户拖垮全局。
  - 长期：自适应限流（adaptive throttling）与 error budget 体系，动态调节阈值与降级策略。

**Related：B6**

### Q06（滑动窗口）Redis 滑动窗口限流怎么实现？为什么不用固定窗口或单计数器？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：滑动窗口（sliding window）用时间戳精确裁剪窗口，避免固定窗口（fixed window）在边界处瞬时放大流量。
  - 对 LLM 这种易触发 429 的外部依赖，窗口边界突刺会被重试放大，滑动窗口更稳。
- **实现：**
  - 数据结构：每个模型一个 ZSET，score=事件时间戳（秒/毫秒），member=唯一字符串（例如 `ts:nonce`）。
  - 60s RPM 窗口的典型命令序列（可 pipeline 执行降低 RTT）：
    - `ZREMRANGEBYSCORE rpm_key 0 (now-60)`（裁剪窗口外请求）
    - `ZCARD rpm_key`（窗口内计数）
    - `EXPIRE rpm_key 120`（控制 key 生命周期）
  - 86400s RPD 同理：`ZREMRANGEBYSCORE rpd_key 0 (now-86400)` + `ZCARD` + `EXPIRE 90000`。
  - 并发计数一般更适合 `INCR/DECR`（开始+1、结束-1）+ TTL，且 normal/escalation 分 key，便于升阶并发封顶（≈0.3×并发上限）。
- **边界：**
  - 时钟漂移（clock skew）：多实例用本地时间可能导致窗口误差；可用 Redis TIME 或统一 NTP 校时。
  - 热 key：高 QPS 模型会让 ZSET 操作成本高；需要 pipeline、Lua 原子脚本或分片（key sharding）。
  - 原子性：裁剪与计数分两步会有微小竞态；通常可接受，但要避免把它当强一致。
- **取舍：**
  - 为什么不用固定窗口：实现简单但边界突刺明显（窗口切换瞬间可翻倍），在高并发下风险更高。
  - 为什么不用单计数器：无法表达窗口内分布，且需要定时 reset，误差更大。
  - 为什么不用 token bucket：也很常用且平滑，但需要维护补充速率与状态更新；跨实例一致与并发更新更复杂。
- **优化：**
  - 短期：用 Lua 把“裁剪+计数+判断”合成原子操作；热点用 pipeline。
  - 中期：改用近似计数（分桶环形数组/rolling counter）降低 ZSET 维护成本。
  - 长期：把限流做成独立 sidecar/service，统一策略下发、统一观测、可水平扩展。

**Related：B6**

### Q07（并发计数）并发计数为什么要 `incr/decr + TTL`？有什么坑？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：并发计数（in-flight concurrency）保护“同时在跑的请求数”，比 RPM 更能反映排队与超时风险。
  - 并发是“资源占用时间”的函数：请求越慢，并发越容易被打满，触发雪崩。
- **实现：**
  - 开始：`INCR conc_key`；结束（finally）：`DECR conc_key`，并对负数做截断（max(0, value)）。
  - TTL：避免进程崩溃/异常路径导致未 `DECR` 的“并发泄露”永久存在；例如给 conc_key 设置 300s TTL。
  - 分流：normal 与 escalation 分 key；升阶并发封顶按比例计算（escalation_cap≈0.3×max_concurrency）。
  - 辅助：对“近期 limited”的模型打短 TTL 标记（≈5s），避免反复探测加剧拥塞。
- **边界：**
  - 异常泄露：TTL 只能缓解，不能避免短期误伤；如果 TTL 太长会误伤更久，太短会误放行。
  - 双加/双减：网络重试或重复回调可能导致计数漂移；需要请求级幂等或 lease-id 机制。
  - 分布式不一致：多实例各自计数会低估全局并发；需要 Redis 或集中式计数才能更准确。
- **取舍：**
  - 为什么不用分布式锁：锁更重且更容易死锁/误释放；并发计数更贴合限流目的。
  - 为什么不只看 429：429 是事后且会触发重试；并发计数能事前阻止堆积。
- **优化：**
  - 短期：Lua 原子 gate（超上限则不自增且直接拒绝）；并在结束时做幂等释放。
  - 中期：lease + heartbeat：为每个请求分配租约，定期心跳，超时自动回收。
  - 长期：全链路 backpressure：排队/拒绝/降级输出与限流协同，形成稳定负载控制系统。

**Related：B6**

### Q08（利用率）什么是限流“利用率（utilization）”？你们用它做了什么决策？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：利用率（utilization）把“当前用量/上限”归一化到 [0,1]，让 RPM、并发等不同维度可比较并驱动统一决策。
  - 常用口径：peak utilization = max(rpm_ratio, conc_ratio)；也可拆出 normal/escalation 并发占比。
- **实现：**
  - rpm_ratio=`rpm_count/rpm_limit`；conc_ratio=`inflight/max_concurrency`；并计算 escalation_cap≈0.3×max_concurrency 判断升阶是否封顶。
  - 候选过滤：`is_limited` 或 peak 超阈值时跳过（skip）或施加惩罚（penalty）。
  - 升阶过载检查：normal 优先稳定（peak≥约 0.9/0.85 就不升阶）；elevated 可更激进但仍有硬上限≈0.95。
  - 工程降压：utilization 可做短 TTL 缓存（≈150ms），减少频繁读 Redis 的 RTT 与热 key 压力。
- **边界：**
  - 上限（limits）不准：默认 max_concurrency=5、RPM=10 等如果与真实配额不符，会导致系统性误判。
  - 指标延迟：采样与缓存会产生短暂误差；需要 hysteresis/cooldown 防止阈值附近抖动（flapping）。
  - 只看 utilization 会忽略质量：低利用率但质量差的模型仍应被健康惩罚或淘汰。
- **取舍：**
  - 为什么不用纯概率 skip：阈值策略更可解释、可回归；概率策略需要更多统计稳定性与调参难度更高。
  - 为什么分 normal/elevated：不同优先级对应不同风险容忍度，显式分层更可控。
- **优化：**
  - 短期：纳入延迟（latency）、429 率等信号，形成多信号拥塞判断。
  - 中期：引入自适应阈值（adaptive thresholds），根据实时 SLO 与错误预算动态调节。
  - 长期：统一负载控制（admission control + queueing + shedding），让路由与执行共享同一 backpressure 体系。

**Related：B5, B6**

### Q09（结构化输出）为什么要 Structured Output（JSON Schema/Pydantic）？不做会怎样？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：Structured Output 的目标是把 LLM 从“会说话”变成“可验证的数据结构”，让下游路由算法的输入稳定、可审计、可回放。
  - 对路由系统而言，任务分析输出是算法输入；输入不稳定会直接导致决策不可控与难以回归。
- **实现：**
  - 用 schema 约束字段（domain、domain_description、relevant_dimensions），对分值做范围约束（如 1–10）。
  - 维度名用 enum/Literal（来自注册表维度集合）限制，避免输出未知维度导致下游对齐失败。
  - 解析失败策略：重试（retry）→降级到备选分析器（fallback chain）→最终回退到规则/启发式（legacy）。
  - 额外收益：可统一提取 token usage、响应时延等元信息，形成可量化的分析质量指标。
- **边界：**
  - “格式正确但语义错误”：模型可能按 schema 输出但判断错维度；需要后续反馈闭环（质量评价/执行反馈）纠偏。
  - schema 版本变化：维度集合变动会导致 schema 变化；需要缓存（LRU）与版本化回放。
  - 模型供应商差异：structured output 支持程度不同；需要更强的容错与降级。
- **取舍：**
  - 为什么不用纯正则解析：复杂场景脆弱、难覆盖边界、回归成本高。
  - 为什么不用全自由文本 + embedding：可解释性弱、调参难，且对结构化下游不友好。
- **优化：**
  - 短期：为关键字段加入置信度与来源标注；解析失败计入监控并驱动 prompt 改进。
  - 中期：引入自一致性（self-consistency）或多模型交叉验证，提高分析稳定性。
  - 长期：用离线数据集评估分析准确率，并将其纳入路由策略训练与灰度准入。

**Related：B1, B7**

### Q10（维度约束）任务分析为什么只输出“相关维度”？怎么保证维度集合可控？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：只输出相关维度是在做“特征选择（feature selection）”，避免无关维度噪声稀释关键能力信号。
  - 路由能力匹配通常是加权平均；维度越多，权重越分散，越容易遮蔽难点。
- **实现：**
  - 提示词明确要求“只列相关维度”，并对每维给难度分（1–10）与理由（reasoning），让权重有可解释来源。
  - 维度集合来自注册表（capabilities），通过 enum/Literal 固定到 schema，防止输出未知维度。
  - 下游打分：以维度难度为权重，对候选能力 0–100 归一到 0–1 后做加权平均；缺失能力用中位默认值（≈50）兜底。
  - 可加护栏：最少维度数（避免只输出 1 个维度过拟合），或关键维度兜底（如 text/code 至少其一）。
- **边界：**
  - 维度漂移：注册表维度新增/删除会导致 schema 变化；需要缓存与版本控制，否则解析与回放会出问题。
  - 漏关键维度：模型可能把“代码审查”只标 text 不标 code；需要 few-shot 与规则校验缓解。
  - 维度过少/过多：过少会激进且不稳，过多会稀释信号；需要上限/下限与回归验证。
- **取舍：**
  - 为什么不输出全维度：信息看似更多，但噪声更大，且输出更不稳定；工程上更难回归。
  - 为什么不固定权重：固定权重对任务不适配；动态权重更贴近任务难点与成本结构。
- **优化：**
  - 短期：加入维度数量约束与一致性检查；对关键维度设最低权重或必须出现。
  - 中期：用历史数据学习“任务→维度分布”的先验，对模型输出做校正。
  - 长期：将维度选择与权重纳入学习策略（contextual bandit 的 context 特征之一）。

**Related：B1**

### Q11（缺失能力）候选模型能力字段缺失时为什么用中位/默认值？有什么替代？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：用中位/默认值填补缺失能力，是对“未知（unknown）”做保守近似，避免把缺失误当成 0（过惩罚）或 100（虚高）。
  - 缺失能力常见原因是“未评估/未补全”，不等于能力差。
- **实现：**
  - 能力尺度通常 0–100；缺失时用 50 作为中位默认值，归一化 `cap_norm = clip((cap or 50)/100, 0, 1)`。
  - 为了可审计：对能力值建议带来源（static/leaderboard/observed/unknown）与更新时间；只填 None，不覆盖已有值。
  - 若要更保守：默认值可略低（如 40）并叠加不确定性惩罚（uncertainty penalty），但会影响探索与新模型进入候选。
- **边界：**
  - 向均值回归偏差：默认值会把缺失模型“拉向中间”，可能过选或漏选；长尾维度（vision/search）更明显。
  - 关键维度缺失：若缺失恰好是任务关键维度，默认值会严重误导；应对关键维度缺失加更强惩罚或强制补全。
  - 尺度错配：不同来源能力分布不同（即使都 0–100），会引入量纲错配；需要尺度校验（min/max/ratio）与来源权重。
- **取舍：**
  - 为什么不把缺失当 0：会系统性扼杀新模型/未评估模型，探索被抑制，策略陷入局部最优。
  - 为什么不把缺失当 100：会导致高风险虚高，把流量导向不可控模型。
  - 为什么不强制实时评测：成本高、周期长，且离线评测与线上任务分布有偏移；工程上更常用“默认值+反馈闭环”。
- **优化：**
  - 短期：加入缺失惩罚（missing ratio penalty），缺失越多分数越打折；并把缺失率纳入监控。
  - 中期：自动能力补全（榜单/离线评测）并引入时间衰减（staleness），减少陈旧能力误导。
  - 长期：把能力当随机变量（带置信区间），决策基于下置信界而不是点估计（更稳）。

**Related：B1**

### Q12（多供应商）Provider 适配层需要统一哪些差异？你们怎么做归一化？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：归一化（normalization）把异构供应商的模型目录映射到同一语义字段集合，让路由算法能在同一标尺上比较候选。
  - 适配层核心不是“拉取”，而是“把数据变成可决策输入 + 可诊断输出”。
- **实现：**
  - 统一字段：模型标识（provider+name）、展示名、能力维度（0–100）、价格（input/output + 单位 per_1k/per_1M）、配额（RPM/RPD/max_concurrency）、可用性（available/degraded/unable）、endpoint/auth 元信息。
  - 价格单位统一：per_1k → per_1M（×1000）；缺失价格用 sentinel（如 1e12）表示不可用/极贵，保证成本排序不崩。
  - 配额缺失兜底：用策略默认值（例如 max_concurrency=5、RPM=10）保证 utilization 可计算。
  - 聚合报告：列出 requested/configured/skipped providers、errors、alerts、models，总量低于阈值时给出告警便于排障。
- **边界：**
  - 供应商字段变更：payload 结构常变；需要容错（类型转换、缺字段默认值）与回归测试。
  - 能力/价格可信度：自报能力不一致；动态价格抓取可能失败；必须允许“动态失败→静态回退”。
  - 本地模型特殊性：本地模型成本/延迟模型与云不同，不能简单复用 token 计费逻辑，需要单独策略。
- **取舍：**
  - 为什么不在路由算法里写 provider 特化：会导致算法分支爆炸、难测难维护；归一化层更清晰。
  - 为什么不完全依赖动态价格：动态来源不稳定；需要静态兜底才能保证可用性。
- **优化：**
  - 短期：对字段增加来源与更新时间，增强可解释与审计；对关键字段做一致性校验（单位/范围）。
  - 中期：引入 schema versioning 与迁移，确保历史快照可回放。
  - 长期：做 registry service（增量更新、订阅推送、统一对账），把模型元数据从应用中解耦。

**Related：B1, B6**

### Q13（缓存策略）为什么要“快照优先 + 到期刷新 + 失败回退”？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：快照优先（snapshot-first）用“上次成功的确定性输入”换“外部依赖的不确定性”，提升路由稳定性与启动可用性。
  - 到期刷新保证数据不会无限陈旧；失败回退保证刷新失败时仍能服务（availability first）。
- **实现：**
  - 读路径：若快照未到期（例如 interval_days=30），直接使用最近成功快照，不访问外部 provider。
  - 刷新路径：到期或强制刷新时拉取 provider 列表写入新快照；若刷新返回 0 模型或失败，则回退到上一份成功快照并附加 alert。
  - 历史保留：保留少量历史快照（例如 keep_history=2），便于回放与排障，同时避免存储膨胀。
- **边界：**
  - 陈旧风险：价格/配额变化会导致成本评估偏差；新模型无法进入候选；需要监控快照年龄并可强制刷新。
  - 刷新风暴：多实例同一时间刷新会打爆 provider；需要 jitter、集中刷新或分布式锁。
  - 快照一致性：写快照必须事务化（头+行一致），否则会出现“快照头成功但模型行不全”的半成品。
- **取舍：**
  - 为什么不每次 live fetch：外部依赖会成为实时单点，延迟与失败直接传导到路由。
  - 为什么不强一致配置系统：成本与复杂度高；路由更看重可回放与可用性。
- **优化：**
  - 短期：监控快照年龄、刷新失败率与“回退次数”；超阈值告警。
  - 中期：分层快照：模型列表低频、价格/配额高频（热更新），降低 staleness 的负面影响。
  - 长期：增量同步与订阅机制，让快照接近实时同时保留回放能力。

**Related：B7**

### Q14（候选多样性）为什么限制同一 provider 的候选占比？对效果有什么影响？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：候选多样性（provider diversity）是风险控制：避免流量在高并发下集中到单点 provider，导致 429/拥塞引发全局失败。
  - 多样性提升“可替代路径”，尤其在升阶/重试时减少“无路可走”。
- **实现：**
  - 约束方式：候选 Top-N 中同一 provider 最多占 K 个（例如 K≈3），其余位置用其他 provider 或探索候选补位。
  - 与保底结合：保留 1 个 ceiling slot（最强能力保底），其余再做多样性与池优先组合。
  - 动态化：当某 provider utilization 接近阈值（peak≥0.85–0.9）时，进一步降低其候选占比。
- **边界：**
  - 单 provider 可用：约束必须自动放宽，否则候选为空；需要“diverse limit 的 fallback”逻辑。
  - 质量差异：若某 provider 整体更弱，多样性会牺牲体验；需要健康/反馈闭环与动态约束强度。
  - 能力尺度不一致：不同来源能力打分偏差会影响“谁被挤出候选”；需要尺度校验与来源权重。
- **取舍：**
  - 为什么不在执行阶段做全局负载均衡：候选阶段更可解释且更早避免单点拥塞；执行阶段再均衡容易不可控。
  - 为什么不用随机分流：随机难以保证关键请求成功率；约束更可控。
- **优化：**
  - 短期：把 K 做成随拥塞变化的函数（拥塞越高越严格，空闲越放宽）。
  - 中期：引入 provider 级预算与健康分，形成更精细的风险控制。
  - 长期：两阶段策略学习（先 provider 再 model），学习型负载分配更接近全局最优。

**Related：B2, B6**

### Q15（探索）为什么要探索（exploration）？探索比例怎么定？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：探索（exploration）用于发现更优/更便宜模型，避免策略陷入局部最优；利用（exploitation）用于保证短期稳定与成功率。
  - 没有探索：新模型与更优组合永远无法被验证，成本优化与质量提升会停滞。
- **实现：**
  - 候选组合：保底强模型（ceiling slot≈1）+ 池候选优先 + 探索补位（explore slots≈1–3）。
  - 自适应探索：池丰富且平均试验次数高→少探索；池薄或样本少→多探索；并设置硬上限（例如 ≤2 或 ≤3）。
  - 安全阀：探索候选也要通过健康与限流过滤（is_limited 直接跳过），并受同 provider 占比上限约束。
- **边界：**
  - 探索过高：会拉低短期成功率并放大事故面；高并发主链路必须保守。
  - 探索过低：长期停滞，无法发现 10%+ 节省或更强模型；策略缺乏进化能力。
  - 奖励信号噪声：质量评价不稳定会误导探索；需要置信下界与最小样本门槛（例如 min trials≈10）。
- **取舍：**
  - 为什么不固定探索比例：不同成熟度与风险承受不同；固定比例难以解释与调参。
  - 为什么不用纯 bandit：bandit 需要可靠 reward 与风控；工程上常先用可解释探索槽位建立数据闭环。
- **优化：**
  - 短期：按 agent_class/domain 分桶设置探索强度（成熟桶少探索，新桶多探索）。
  - 中期：引入 UCB/Thompson Sampling 等置信区间驱动探索，替代硬编码槽位。
  - 长期：contextual bandit：用任务维度、长度、约束、拥塞作为上下文，实现更细粒度探索。

**Related：B2, B3**

### Q16（健康度）什么是 degraded/unable？它们在路由里怎么影响候选？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：健康度（health）把“执行稳定性/质量稳定性”转换为可用于过滤与打分的状态信号，避免路由反复选到不可靠模型。
  - unable 是硬过滤（不可选），degraded 是软惩罚（可选但降权），二者对应不同风险等级。
- **实现：**
  - unable：执行失败触发不可用状态，并进入探测；探测间隔可做小时级（例如 3600s）避免频繁打扰。
  - degraded：短窗口内失败增多触发降级状态，窗口可做 300s；在窗口内对分数加惩罚（例如 ≈0.05）。
  - probe cooldown：探测成功后短期（例如 300s）内避免反复探测带来的抖动，并可施加轻微惩罚（例如 ≈0.02）避免“刚恢复就被打爆”。
  - 与路由结合：unable 直接从候选列表剔除；degraded 在排序时降低其最终分；必要时在拥塞时更激进跳过。
- **边界：**
  - 失败类型混淆：执行失败（timeout/429）与质量失败（输出差）风险不同，健康逻辑应区分，否则会误杀或放任。
  - 状态抖动（flapping）：阈值过敏会导致 degraded↔available 频繁切换；需要窗口与冷却（hysteresis）。
  - 数据延迟：健康状态落盘与读取有延迟，短时间内可能继续选到不健康模型；需与限流/升阶兜底联动。
- **取舍：**
  - 为什么不用永久黑名单：模型与供应商状态可能恢复；永久封禁降低弹性并导致候选变薄。
  - 为什么不做全量主动探测：成本高且会制造额外负载；按需探测 unable 模型更经济。
- **优化：**
  - 短期：把 429/超时等信号直接纳入拥塞与健康转换，形成更快的自我保护。
  - 中期：引入灰度恢复（逐步放量），比从 unable 直接切 available 更稳。
  - 长期：引入 error budget 与 SLO，把健康状态管理体系化（可解释、可调参、可回放）。

**Related：B1, B7**

### Q17（升阶）升阶一般发生在什么条件？为什么要做过载检查？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：升阶（escalation）是在当前模型失败时切到更强候选重试以提高成功率，但必须做过载检查，否则会触发“越失败越拥塞”的正反馈雪崩。
  - 升阶不是“无脑上更贵”，而是“在成本与系统稳定性约束下提升成功率”。
- **实现：**
  - 触发：执行失败（timeout/429/网络）或质量失败（输出差）达到阈值；质量失败通常允许先重试 1 次再升阶。
  - 选择：优先候选列表中更强项；若候选耗尽可尝试突破候选（breakthrough）——能力显著更高但不在原候选集。
  - 过载检查：对目标计算 peak utilization；normal 模式下若 peak≥约 0.9/0.85 则不升阶（回退重试/换替代），elevated 上限≈0.95。
  - 升阶并发封顶：升阶流量与正常流量分开计数，封顶比例≈0.3×并发上限，避免升阶吃光并发。
- **边界：**
  - 候选为空/全部封顶：必须返回“不可升阶/告警”或退化重试；否则会无限循环或持续失败。
  - 失败类型误判：把质量差当执行失败会错误升阶；把执行失败当质量差会延误避让拥塞。
  - 过载检查过严：拥塞时可能牺牲成功率；需要优先级分层（normal/elevated）控制风险。
- **取舍：**
  - 为什么不拥塞时更应该升阶：如果目标模型也拥塞，升阶只会增加排队与失败；应优先选择未封顶替代候选或等待/降级。
  - 为什么不直接排队等待：排队提高成功率但增加延迟；交互任务常对延迟敏感，需要更强的失败/降级策略。
- **优化：**
  - 短期：指数退避（backoff）+ 最大等待预算（例如总等待≤7s）避免重试风暴。
  - 中期：把升阶策略与历史反馈结合（哪些任务/哪些类更适合升阶，哪些应直接换 provider）。
  - 长期：将升阶动作纳入学习策略（bandit/RL），在不同拥塞水平下学到最优动作。

**Related：B5, B6**

### Q18（降级）为什么要“降级试验（canary）”而不是直接切默认到更便宜的模型？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：降级试验（downgrade canary）用小流量验证“更便宜模型是否能维持质量”，避免直接切换导致全量质量事故。
  - 目标是“质量约束下的成本优化”，不是无条件选最便宜。
- **实现：**
  - 前置：当前默认模型需稳定成功（连续成功达到阈值）才允许尝试降级。
  - 选择 challenger：必须更便宜且预计节省≥10%（min savings≈0.10），并且质量差距不应过大。
  - canary：按比例（例如 0.5）切小流量；累计样本≥5（min samples≈5）再评估；回滚阈值：质量失败≥2 或执行失败≥1。
  - 推广：challenger 成功样本达到门槛（例如 ≥15）可晋升默认；回滚后冷却期≈24h 防震荡。
- **边界：**
  - 采样偏差：必须随机且可重放，否则结论不可信；必要时按 request_id 哈希取模。
  - 反馈延迟：质量评价可能晚到，回滚不及时；因此 exec fail 门槛更敏感（更快止血）。
  - challenger 不可用：若当前不可用或不在候选中，canary 必须跳过并记录原因。
- **取舍：**
  - 为什么不直接切默认：节省可能很小但风险很大；canary 用可控风险换稳健结论。
  - 为什么不一上来做 AB 平台：平台更强但成本高；内置 canary 是更轻量的 MVP。
- **优化：**
  - 短期：引入置信区间/显著性检验，减少小样本误判。
  - 中期：按 domain/子任务分桶试验，避免平均掩盖子域质量下降。
  - 长期：把实验与策略学习打通，形成自动化成本优化平台。

**Related：B4**

### Q19（监控落盘）你们监控记录哪些数据来支持排障？为什么要“best-effort”？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：监控的第一原则是“不影响主链路可用性”，因此监控写入应 best-effort（失败可接受但要可观测）。
  - 监控的价值是“可解释 + 可回放”：回答为什么选这个模型、当时是否拥塞、是否触发降级/升阶。
- **实现：**
  - 路由事件：记录选中模型、候选摘要、路由原因、是否命中池、provider 错误/跳过计数、分析域与复杂度、限流模式与关键利用率。
  - 执行生命周期：start/end，包含 execution_id/request_id、状态（running/success/failed/timeout）、持续时间、错误摘要、token 统计（如有）。
  - 存储：SQLite 侧车可用 WAL + busy_timeout≈3s，提高并发友好；用 retention days 清理旧数据。
  - best-effort：写失败只 warning，不阻断路由返回；必要时对监控写做采样降低写放大。
- **边界：**
  - 写热点：高并发下监控表成为瓶颈；必须采样/批处理/异步化，否则监控拖慢系统。
  - 隐私合规：不落原始 task 文本；用 hash/长度/维度摘要替代。
  - 关联一致性：路由事件与执行事件可能乱序/缺失；用 request_id/execution_id 关联并容忍不完整。
- **取舍：**
  - 为什么不用 Prometheus/OTel 一开始上：对单机/CLI 依赖重；SQLite 侧车更轻量、可离线分析。
  - 为什么不用 MQ：可靠但运维与一致性成本高；best-effort 能覆盖多数排障需求。
- **优化：**
  - 短期：监控采样（只记录异常/升阶/候选为空等关键事件），降低写放大。
  - 中期：异步事件管道（outbox+consumer），批处理写入并支持重放。
  - 长期：统一观测体系（OTel+TSDB），监控数据与学习数据打通形成闭环。

**Related：B7**

### Q20（测试）你会怎么给这类系统设计测试用例？哪些必须覆盖？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：这类系统测试重点是“高并发下策略是否可预测、可回归、可解释”，而不仅是函数返回值是否正确。
  - 需要同时覆盖：算法排序、状态机升阶/降级、存储幂等/事务、限流与降级、观测 best-effort。
- **实现：**
  - **并发测试流程（统一流程）**：
    - 场景构造：固定合成 workload，`BATCH_COUNT=5`、`BATCH_SIZE=4`，总请求 `5×4=20`，批次按 start-to-start 间隔重叠启动（`INTERVAL_MIN~INTERVAL_MAX`）。
    - 批次调度：按计划 `schedule_offsets` 启动每一批，并记录 `actual_offsets`，校验调度漂移在容忍阈值内（`_BATCH_START_TOLERANCE_SECONDS`）。
    - 并发执行：每个请求执行 `route_async -> 限流判定 -> 记录开始/结束`，并采集 `route latency / queue wait / service latency / completion latency`。
    - 指标汇总：按请求级明细聚合成功、拒绝、超时与延迟分位数；再按模型聚合 `observed_rpm_peak_60s`、`observed_concurrency_peak` 与分配分散度。
    - A/B 聚合：实验组与对照组分别跑 `--runs 3`，输出 `median` 与 `stability(min/max)`，窗口统一按 `window_anchor=min_submit_ts`（`window_start_ts=0.0`、`window_end_ts=60.0`）。
  - **实验组流程（overlapping batch）**：
    - 直接复用 `route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py` 的重叠批次执行模型。
    - 20 个请求按批次重叠启动，允许模型池分流，核心验收包括：全请求可分配、批次漂移可控、模型 RPM/并发不超限、监控快照完整。
    - 该组用来回答“多模型分流策略在并发挤压下是否稳定”。
  - **对照组流程（pin_model）**：
    - 使用 `scripts/perf_ab_compare.py` 的 `control_strategy=pin_model`，将同 workload 固定到单模型 `google:gemini-2.5-pro`。
    - 请求仍按同批次节奏提交，但会在单模型限流下进入重试队列，重现热点模型拥塞与排队放大。
    - 该组用来回答“若不分流，仅固定单模型，会出现怎样的拒绝率与尾延迟恶化”。
  - **超时边界与收敛阈值**：
    - `max_wall_time_seconds=120`：单请求从提交开始的总墙钟时间上限，超过即强制终止，防止无界等待。
    - `max_attempts_per_request=200`：单请求最大重试次数上限，避免在高拒绝下无限重试。
    - `max_queue_wait_per_request_ms=60000`：单请求排队等待上限，超过即按超时收敛，避免长尾拖垮统计。
    - `max_inflight_retrying=8`：重试节流信号量上限，限制并发重试波峰，防止重试风暴自激。
    - 触发逻辑是“谁先达到上限先终止”，用于把对照组的拥塞行为收敛到可比较区间，而不是让实验无限拉长。
  - **其他必测项（功能与一致性）**：
    - 单测：给定候选与维度，验证加权能力分、成本归一化（alpha≈3、price cap≈10）、近似分 tie-break（epsilon≈0.05）与 provider 多样性约束。
    - 组件测：模拟 Redis 不可用→auto 切换 in-memory；模拟 utilization 超阈值→跳过/不升阶。
    - 存储测：幂等唯一约束防重复；复合操作用事务（BEGIN IMMEDIATE）保证原子性；监控写失败不影响主链路。
    - 回放测：固定随机种子/时间戳回放历史决策，比较策略改动前后差异（diff），确保可解释与可回归。
  - **指标说明表（是什么 / 怎么测 / 意义 / 如何看实验组 vs 对照组）**：

| 指标 | 怎么测（公式/口径） | 指标意义 | 实验组 vs 对照组解读 |
| --- | --- | --- | --- |
| `throughput_rps` | `total_requests / elapsed_seconds` | 总处理速率（含成功、拒绝、超时路径） | 只看它会被“快速拒绝”误导，必须结合有效吞吐与延迟看 |
| `success_throughput_rps` | `success_count / elapsed_seconds` | 真正业务可用吞吐 | 实验组高说明分流把成功请求稳定落地；对照组低说明热点拥塞 |
| `success_throughput_rps_window` | `successes_in_window / 60s`（窗口锚点 `min_submit_ts`） | 固定时间窗内的有效产能 | 对照组通常更低，反映窗口内持续拥塞而非瞬时抖动 |
| `peak_10s_success_rps` | 任意 10s 滑窗内成功完成峰值 / 10 | 短时峰值承载能力 | 实验组峰值更高，说明突刺时分流更有效 |
| `success_rate` | `success_count / total_requests` | 请求最终成功比例 | 两组可都高，但不代表过程健康，仍需看拒绝和等待代价 |
| `rejection_rate` | `total_rejections / total_attempts` | 尝试层面的拒绝压力（重试越多越敏感） | 对照组高，代表同一请求在队列里被反复拒绝 |
| `timeout_rate` | `timeout_count / total_requests` | 超时终止占比 | 用于判断系统是否进入“排队不可收敛”状态 |
| `rate_limit_rejection_rate` | `rate_limit_rejections / total_attempts` | 速率配额触发占比 | 识别是 RPM 类约束主导还是并发主导 |
| `concurrency_rejection_rate` | `concurrency_rejections / total_attempts` | 并发上限触发占比 | 对照组若偏高，说明热点模型并发槽位持续打满 |
| `no_model_rejection_rate` | `no_model_rejections / total_attempts` | 无可用模型/不可分配占比 | 辅助定位是限流问题还是候选供给问题 |
| `routing_overhead_ms(p50/p95)` | 每次 `route_async` 耗时分位数 | 路由决策本身开销 | 若该值异常高，瓶颈在路由器而非模型执行 |
| `queue_wait_ms(p50/p95/max)` | `execution_start - submit_ts`（超时样本按上限截断） | 请求排队时延，直接反映拥塞强度 | 对照组上升更明显，说明热点下排队堆积 |
| `service_latency_ms(p50/p95/p99)` | `execution_end - execution_start`（仅成功样本） | 模型执行耗时 | 区分“模型慢”与“队列慢” |
| `completion_latency_ms_success` | `execution_end - submit_ts`（仅成功样本） | 成功请求端到端时延 | 用于看用户成功路径体验 |
| `completion_latency_ms_all` | 全样本端到端；含成功与超时 | 防止“快速拒绝导致看起来更快”的假象 | A/B 对比必须优先看该口径的 P95/P99 |
| `observed_rpm_peak_60s` | 任意 60s 滑窗内启动数峰值 | 验证是否逼近/突破模型 RPM 上限 | 用于检查限流是否真正生效 |
| `observed_concurrency_peak` | 执行区间重叠峰值 | 验证是否逼近/突破并发上限 | 与 `max_concurrency` 对照判断是否超限 |
| `allocatable/eligible/assigned` | 可分配模型数 / 符合策略模型数 / 实际命中模型数 | 分流覆盖度与热点集中度 | 实验组 `12/12/12` 代表分散；对照组 `12/1/1` 代表单点热点 |
| `stability(min/max)` | 3-run 内关键指标最小/最大值 | 评估重复实验稳健性 | 对照组通常波动区间更差，说明拥塞状态不稳定 |

  - **超时如何记账（统一口径）**：
    - 超时样本记录为 `execution_status="timeout"`，触发条件为墙钟上限/重试上限/排队上限任一先到。
    - `completion_latency_ms_all` 对超时样本记到当前等待耗时上限（`min(queue_wait_ms, 60000)`），因此会真实反映“等待成本”。
    - `completion_latency_ms_success` 只统计成功样本，超时不会进入该分布，避免污染成功路径时延。
    - 这也是“实验组 `timeout_rate=0` 但对照组仍可高 `rejection_rate`”的原因：对照组主要是重试拒绝累积，而非超时主导。
  - A/B 结果（3 次中位数）：

| 场景 | 总耗时(s) | 总吞吐(RPS) | 有效吞吐(RPS) | 成功率 | 拒绝率 | 超时率 | completion_all P95(ms) | allocatable/eligible/assigned | 关键结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 实验组（重叠批次） | 20.625 | 0.97 | 0.97 | 1.0 | 0.0 | 0.0 | 14684.15 | 12/12/12 | 多模型分流，容量稳定 |
| 对照组（固定单模型：`google:gemini-2.5-pro`） | 66.11 | 0.303 | 0.303 | 1.0 | 0.929 | 0.0 | 61437.85 | 12/1/1 | 单模型热点明显，重试拒绝密集，完成时延显著变差 |
  - **如何解读对照组 vs 实验组：**
    - 实验组：分流后 `rejection_rate` 与 tail latency（尤其 `completion_latency_ms_all` 的 P95/P99）显著受控，容量可持续。
    - 对照组：固定单模型导致拥塞重试，`completion_all_p95` 与 `rejection_rate` 明显恶化，体现单点热点放大效应。
    - 结论口径：A/B 对比必须同时看“有效吞吐 + 拒绝/超时 + completion(all)”，不能只看总耗时或总吞吐。
- **边界：**
  - 候选为空：必须返回明确原因与告警，不得抛异常导致调用方崩溃。
  - provider 刷新返回 0：必须回退到上次成功快照，并记录 alert。
  - canary 抖动：样本不足不 promote/rollback；回滚后冷却期（≈24h）要生效。
- **取舍：**
  - 为什么压力测试不能替代回归：压力测试难复现且噪声大；策略迭代需要确定性回放与金样本（golden set）。
  - 为什么要强调可解释：策略改动多为调参/启发式迭代，没有可解释与回放无法安全上线。
- **优化：**
  - 性能验收口径：将 A/B 压测都纳入策略改动后的固定门禁；实验组要求“批次漂移在容忍范围内 + 请求全分配 + 模型 RPM/并发不超限 + `assigned_models_count` 维持多模型分散（当前 `allocatable/eligible/assigned=12/12/12`）”，对照组要求“固定模型命中稳定（当前 `allocatable/eligible/assigned=12/1/1`）”；并强制同时看 `throughput_rps` 与 `success_throughput_rps`、`success_rate`、`rejection_rate`、`timeout_rate`、`completion_latency_ms_all`，避免被“快速拒绝导致总耗时变短”误导；窗口指标统一按 `window_anchor=min_submit_ts`，并输出 `window_start_ts/window_end_ts` 供复算；稳定性至少输出 3-run `min/max`（当前对照组 `success_throughput_rps=0.281~0.303`、`p95_completion_latency_ms_all=59964.7~61640.05`、`rejection_rate=0.926~0.929`）；持续跟踪回归通过率（当前基线 `84/84 passed in 22.54s`）。
  - 短期：建立金样本与差分回归（regression diff）流水线，策略改动必须通过离线门槛。
  - 中期：故障注入（chaos）与合成数据覆盖长尾维度组合；完善拥塞/429 风暴场景。
  - 长期：把业务指标（成功率/成本/延迟）纳入 CI 的离线评估 gating，并支持影子模式验证。

**Related：B1–B7**

---

## Part B：复杂（15 题）

### Q21（多目标融合）你们怎么把能力、成本、健康、拥塞融合到同一排序里？为什么这样融合？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：多目标融合的核心是“先把信号归一化到同一量纲，再确定主目标与约束目标”，否则能力/成本/拥塞会互相打架。
  - 路由常见目标：质量（能力匹配）最大化、成本最小化、可用性最大化、拥塞风险最小化；其中文件化输出要可解释、可回放。
- **实现：**
  - 能力匹配：对相关维度做加权平均（权重=维度难度 1–10），模型能力 0–100 归一到 0–1；缺失能力用中位默认值≈50。
  - 成本分：input/output 单价合成有效单价（输出权重随任务信号在 0.3–0.7），统一到 per_1M tokens；指数归一化（alpha≈3，price cap≈10）得到 cost_score∈[0,1]（越小越便宜）。
  - 健康度：对稳定模型给予 bonus，对不稳定模型 penalty；degraded 额外惩罚≈0.05；unable 直接过滤。
  - 拥塞：计算 utilization（peak=max(rpm_ratio, conc_ratio)）；normal 阈值≈0.9/0.85，elevated 上限≈0.95；拥塞时跳过/抑制升阶。
  - 排序与 tie-break：能力主分优先；当差异 < epsilon≈0.05 时用更低成本优先，避免“几乎一样强但更贵”。
- **边界：**
  - 信号不一致：能力来自不同来源（static/榜单/反馈）尺度可能错配；需要尺度校验与来源权重。
  - 成本估计偏差：真实 output/input token 比例漂移会导致有效单价失准；需要在线 token 统计校正。
  - 惩罚叠加过强：健康/拥塞惩罚过大可能把候选打到负分或造成候选为空；需要兜底策略（保底模型/放宽约束）。
- **取舍：**
  - 为什么不把成本作为硬约束：硬约束易导致候选为空；工程上常用“能力优先 + 成本次优 + max_cost 兜底”。
  - 为什么不用 Pareto-front 直接选：Pareto 仍需二次决策规则；启发式更易解释与回归，可先落地再演进。
- **优化：**
  - 短期：将所有特征（能力/成本/健康/拥塞）落盘用于回放与解释，形成可调参的数据闭环。
  - 中期：离线回放学习权重（learning-to-rank），上线 shadow mode 验证，再小流量灰度。
  - 长期：contextual bandit/策略学习，直接优化“质量约束下成本最小 + SLO 达标”的综合目标。

**Related：B1, B6**

### Q22（有效单价）为什么要输入/输出动态加权？如果不加权会怎样？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：动态加权是在估计“期望成本”，因为不同任务 output/input token 比例差异很大；不加权会系统性误判成本。
  - 直觉：生成类（coding/creative）输出更长→output 单价更重要；抽取/分类类输出短→input 更重要。
- **实现：**
  - 定义输出权重 w_out：用任务信号映射到 [0.3, 0.7]；w_in=1-w_out。
  - 有效单价：`price_eff = w_in*price_in + w_out*price_out`，统一单位到 per_1M tokens（per_1k → ×1000）。
  - 成本归一化：ratio=clip(price_eff/price_cap,0,1)，再用指数映射（alpha≈3）增强高价区分度。
  - 校验：用历史请求真实 token usage 回放，计算“预测成本 vs 实际成本”（MAPE、P95），并按 domain 校正 w_out。
- **边界：**
  - token 比例漂移：提示词变化/任务分布变化会让 w_out 失准；需要周期性重估或在线更新。
  - 计费差异：不同供应商对 system/tool token 计费口径不同；名义单价可能偏离实际账单。
  - 极端长输出：即使 w_out=0.7 仍可能低估；可引入预计输出长度/复杂度特征提高鲁棒性。
- **取舍：**
  - 为什么不精确预测 output tokens：精确预测需要额外模型与训练数据；动态权重是可解释、低成本的近似。
  - 为什么不 50/50：会对两端任务都产生系统性偏差（生成类被低估、抽取类被高估）。
- **优化：**
  - 短期：按 agent_class/domain 分桶统计真实 output/input 比例，替换手工映射。
  - 中期：引入轻量回归预测 output tokens，再算期望成本。
  - 长期：把成本模型与路由策略联合训练，直接优化成本-质量权衡。

**Related：B1**

### Q23（缺失能力的偏差）用默认值填补能力缺失会带来什么统计偏差？怎么缓解？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：默认值填补会产生“向均值回归”的偏差（regression to the mean），对新模型与长尾维度尤为明显。
  - 这会让策略在“未知但可能更优”的区域探索不足，或在“未知但风险高”的区域过度放量。
- **实现：**
  - 基线做法：缺失能力→用 50/100；只填 None 不覆盖；并记录来源（unknown/static/leaderboard/observed）。
  - 缓解 1：不确定性惩罚（uncertainty penalty），例如缺失维度占比越高，最终分数乘以更小系数。
  - 缓解 2：来源加权：static 低置信、榜单中置信、在线反馈高置信；同一数值不同权重。
  - 缓解 3：将高缺失候选更多放入探索槽位，而不是池/默认候选。
- **边界：**
  - 关键维度缺失：vision/search 等如果缺失且任务强相关，默认值会严重误导；需要强惩罚或强制补全。
  - 尺度错配：不同来源分布不同会导致“同样 80 分含义不同”；需要尺度校验与校准（calibration）。
  - 非 IID：任务分布漂移时，历史补全策略可能失效；需要按时间衰减或分桶更新。
- **取舍：**
  - 为什么不“缺失即不选”：会扼杀探索，新模型永远进不来；策略会固化。
  - 为什么不完全依赖榜单：榜单覆盖不全且与真实任务分布可能偏离；必须与在线反馈结合。
- **优化：**
  - 短期：把缺失率与来源分布纳入监控，发现异常来源或补全失败。
  - 中期：自动补全（榜单/离线评测）+ 时间衰减，降低陈旧与缺失带来的误判。
  - 长期：Bayesian/置信区间建模，把缺失视为高不确定性并由探索策略驱动验证。

**Related：B1, B2**

### Q24（候选多样性约束）为什么不是在全局做 provider 负载均衡，而是在候选集阶段限制占比？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：候选阶段做多样性约束是“提前消除单点风险”，比执行阶段再均衡更可解释，也更能保证升阶/重试时有替代路径。
  - 全局负载均衡偏调度（scheduling），候选多样性偏风险控制（risk control）。
- **实现：**
  - 候选阶段：Top 候选中同 provider ≤K（例如 K≈3），其余位置优先来自不同 provider 的池候选/探索候选。
  - 与过载联动：当某 provider utilization 接近阈值时，动态降低其候选占比；升阶若封顶则找未封顶替代。
  - 观测：记录候选 provider 分布、最终命中 provider、429/timeout 分布，验证多样性是否降低单点失败。
- **边界：**
  - 单 provider 可用时必须退化放宽，否则候选为空；退化逻辑必须明确且可监控。
  - 多样性会牺牲最优：需保留保底强模型槽位（ceiling slot≈1）减少体验损失。
  - provider 之间质量差距大：多样性应随成熟度与健康动态调整，否则会长期牺牲体验。
- **取舍：**
  - 为什么不用 WRR/RR：轮询忽略任务差异；路由要做任务匹配，不能只做负载均衡。
  - 为什么不用全局最优化（min-cost flow）：需要全局状态与预测，复杂度高；候选约束是低成本近似。
- **优化：**
  - 短期：动态 K（拥塞越高越严格），并对关键任务强制保留高质量 provider。
  - 中期：引入 provider 预算与健康分，形成“风险预算 + 成本预算”双约束。
  - 长期：两阶段学习（先 provider 后 model），学习型分配更接近全局最优。

**Related：B2, B6, B5**

### Q25（冷启动与探索槽位）探索槽位“自适应”具体靠什么信号？为什么不是固定比例？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：自适应探索用“策略信心”驱动探索强度：信心强（池丰富、样本多）少探索，信心弱（池薄、样本少）多探索。
  - 固定比例难以兼顾冷启动（需要探索）与成熟期（需要稳定与成本优化）。
- **实现：**
  - 信号 1：池子大小（例如 ≥5 认为较丰富）；信号 2：平均试验次数（例如 ≥5 认为有一定统计基础）。
  - 自适应规则示例：池丰富且试验多→explore slots≈1；池薄或试验少→explore slots≈3；并设置硬上限（例如 ≤2 或 ≤3）。
  - 保底机制：ceiling slot≈1（能力上限保底）；新模型激励：lookback≈30 天内可加小 bonus≈0.04，但要防止“新模型霸榜”。
- **边界：**
  - 奖励噪声：质量信号不稳定会让探索误导；需要置信下界与最小样本门槛（min trials≈10）。
  - 任务异质：同一 agent_class 内任务差异大时，需要按 domain 再分桶自适应，否则平均信号会误导。
  - 高并发风险：探索在高峰期应更保守，可把探索强度与 utilization 绑定（拥塞高→少探索）。
- **取舍：**
  - 为什么不用固定 5%/10%：不同流量规模与风险承受不同；固定比例很难解释与调参。
  - 为什么不用全量离线评测替代探索：离线与线上分布偏移明显；在线探索仍是发现真实效果的关键。
- **优化：**
  - 短期：按风险等级/业务优先级动态调整探索（低风险多探索，高风险少探索）。
  - 中期：UCB/Thompson Sampling，把置信区间直接用于探索与利用平衡。
  - 长期：contextual bandit，把任务特征与拥塞特征作为上下文，学到更精细的探索策略。

**Related：B2, B3**

### Q26（置信下界）为什么用 Wilson lower bound（或类似置信下界）做入池/晋升门槛？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：置信下界（confidence lower bound）把“小样本的偶然高成功率”压下去，让默认/入池决策更稳，减少被噪声带偏。
  - 直觉：同样 90% 成功率，(9/10) 与 (900/1000) 的可信度完全不同；下界会惩罚前者。
- **实现：**
  - Wilson lower bound 用 success_count、fail_count 与 z 值（例如 z≈1.645≈90% 置信）计算保守成功概率下界。
  - 入池：当下界≥阈值（例如 0.25）且试验次数≥min trials≈10，才允许进入“历史命中池”。
  - 晋升默认：challenger 必须在足够样本下持续领先，并达到最小成功数（例如默认晋升≥20；降级试验场景≥15）才晋升，避免运气好就晋升。
  - 与连续成功/失败结合：连续成功可给 bonus，连续失败给 penalty，形成更敏感但仍稳健的动态调整。
- **边界：**
  - 奖励口径不一致：exec fail 与 quality fail 若混为一个 fail_count，会让统计无意义；必须区分或设权重。
  - 非 IID：请求分布会漂移；下界对历史整体的解释会下降，需要分桶（domain/时间窗）与时间衰减。
  - 严重度差异：某些失败（例如 429 风暴）比一般质量差更危险；单纯 success/fail 不够，需要 severity。
- **取舍：**
  - 为什么不用简单 success rate：小样本波动太大，容易错误晋升/淘汰。
  - 为什么不用 EWMA：EWMA 平滑但不提供置信度，解释性差；Wilson 下界更像“保守估计”，更容易解释。
  - 为什么不用 Bayesian posterior：可用（Beta-Binomial/Thompson），但实现与解释成本更高；Wilson 是工程折中。
- **优化：**
  - 短期：加入时间衰减（recent-weighted）与分桶下界，降低陈旧样本误导。
  - 中期：把置信区间直接用于探索策略（UCB/Thompson），形成统一的学习框架。
  - 长期：把 reward 升级为多目标（质量/成本/延迟），并引入可控实验平台保证安全探索。

**Related：B3**

### Q27（降级试验参数）canary 比例、最小节省、最小样本量、冷却期分别解决什么问题？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：这些参数分别控制“风险暴露、收益门槛、统计稳定性、策略震荡”，让降级优化可控而不是线上随机试错。
  - 参数设计要匹配流量规模与业务风险：流量越大越可小 canary，风险越高越需更严格回滚。
- **实现：**
  - canary 比例（例如 0.5）：决定切到 challenger 的流量；越大收敛越快但事故面越大。
  - 最小节省（例如 ≥10%）：避免为了很小节省承担质量风险与运维成本（ROI 约束）。
  - 最小样本量（例如 ≥5）：样本不足不 promote/rollback，避免小样本抖动误判。
  - 回滚阈值：质量失败≥2 或执行失败≥1；执行失败门槛更低体现“可用性优先止血”。
  - 冷却期（例如 24h）：回滚后暂停再试，避免短时间反复切换导致用户体验震荡与统计污染。
- **边界：**
  - 流量很小：样本长期达不到门槛；需要延长观察窗或用离线评测替代。
  - 流量突刺：短时间采样过快会放大风险；可在拥塞时动态降低 canary（risk-aware canary）。
  - 反馈延迟：质量评价滞后会延迟回滚；因此 exec fail 门槛更敏感（更快回滚）。
- **取舍：**
  - 为什么不 canary=0.1 更安全：更安全但收敛慢；流量大时 0.1 也足够快，需按流量定，而非固定。
  - 为什么不用成熟 AB 平台：平台更强但成本高；内置参数化 canary 是轻量 MVP。
- **优化：**
  - 短期：自适应 canary：根据实时失败率与拥塞动态调节；触发阈值可更保守。
  - 中期：显著性检验与序贯测试（sequential testing）减少误判与缩短收敛时间。
  - 长期：实验管理平台化（实验版本、指标看板、自动回滚），与策略学习统一。

**Related：B4**

### Q28（回滚条件）为什么区分 exec fail 和 quality fail？两者的策略含义不同在哪？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：exec fail 是“系统可用性风险”，需要快速止血；quality fail 是“效果风险”，需要更稳健统计但最终也要保护体验。
  - 两者对应不同故障模型：执行失败会立刻影响成功率/延迟；质量失败可能更隐蔽且信号更稀疏。
- **实现：**
  - exec fail：超时、429、连接错误等；在 canary 中门槛更低（例如 ≥1 就回滚），避免把系统推入拥塞雪崩。
  - quality fail：基于人工/自动评价；门槛更高（例如 ≥2）避免偶然差样本导致震荡。
  - 升阶策略：exec fail 更倾向换 provider/避让拥塞；quality fail 更倾向升阶更强模型或撤销默认晋升。
- **边界：**
  - 误分类：把质量差当执行失败会错误回滚/避让；把执行失败当质量差会延迟止血；必须有清晰 failure_type 判定来源。
  - 质量反馈缺失：质量信号稀疏时策略会过度依赖 exec fail，可能忽略体验下降；需要自动质量指标补充。
  - 子域差异：同一模型对不同 domain 质量差异大；统一阈值会误判，需要分桶阈值。
- **取舍：**
  - 为什么不统一成一个 fail_count：会混淆风险等级；执行失败对系统稳定性威胁更大，应更敏感。
  - 为什么 quality fail 不设 1：质量评价噪声更大，门槛太低会导致策略频繁震荡。
- **优化：**
  - 短期：引入 severity 权重（timeout/429 权重大），把不同失败映射到统一风险分。
  - 中期：建设自动质量评估（回归集/规则/对比评测）补充人工反馈。
  - 长期：error budget 化：同时约束成功率、延迟、质量与成本，形成系统级风险控制。

**Related：B4, B5**

### Q29（升阶过载检查）为什么在升阶前检查利用率？为什么 normal/elevated 不同阈值？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：升阶把请求推向更强更贵资源；如果目标已接近满载，升阶会把排队变成失败并引发雪崩，所以必须先做过载检查（overload check）。
  - normal/elevated 是优先级分层：优先级越高，越愿意冒一定拥塞风险换成功率，但仍要有上限。
- **实现：**
  - 计算 peak utilization=max(rpm_ratio, conc_ratio)；并检查升阶并发是否封顶（escalation cap≈0.3×并发上限）。
  - normal：若 peak≥约 0.9（RPM）或 ≥0.85（并发），不升阶，优先重试当前或选未封顶替代候选。
  - elevated：更激进，但若 peak≥约 0.95 则禁止升阶/触发告警，避免把系统推到不可恢复状态。
  - 若目标封顶：从候选中寻找未封顶替代；没有则回退（normal 回退重试，elevated 可直接不可用告警）。
- **边界：**
  - 指标延迟与缓存：utilization 缓存≈150ms 会导致短暂误判；需 hysteresis 与冷却避免抖动。
  - 目标模型更强但更拥塞：升阶可能降低成功率（排队超时）；此时应优先可用性策略而非能力策略。
  - 优先级滥用：如果所有请求都 elevated，会冲垮系统；需要权限、配额与审计。
- **取舍：**
  - 为什么不“排队等一等”再升阶：排队会提高成功率但延迟不可控；交互任务常宁可失败或降级响应。
  - 为什么不用全局调度：全局更精确但复杂；局部过载检查是性价比最高的保护手段。
- **优化：**
  - 短期：加入等待预算（例如总等待≤7s）与最大等待队列（例如 ≤50）避免重试/升阶等待挤爆。
  - 中期：把延迟、429 率纳入过载信号，替代单一 utilization。
  - 长期：自适应阈值（基于 error budget）与学习型动作选择，形成更优的负载控制策略。

**Related：B5, B6**

### Q30（限流架构）为什么要支持 Redis / In-memory / Off + Auto 降级？为什么不是强依赖 Redis？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：限流的目标是保护主链路，不能反过来成为主链路单点；因此需要多模式与 auto 降级保障可用性。
  - Redis 模式提供跨进程一致计数；in-memory 模式提供“无外部依赖保底”；off 模式用于离线/测试。
- **实现：**
  - auto：配置了 Redis 则优先 Redis；Redis 不可用时若策略为 degrade，则切换到 in-memory，并记录切换时间与错误摘要。
  - fail_fast：若业务强约束（例如严控成本），Redis 不可用直接失败，避免无保护运行。
  - 上层统一口径：不管实现模式，输出一致的 utilization（rpm_ratio/conc_ratio/is_limited）与封顶判断，避免上层策略分叉。
- **边界：**
  - in-memory 的局限：多实例不共享计数，会低估全局利用率；但仍能保护单实例不爆炸。
  - 切换抖动：Redis 反复抖动会导致来回切换；需要熔断（circuit breaker）与切换冷却。
  - off 模式风险：生产误开会失去保护；需要显式配置与防呆校验。
- **取舍：**
  - 为什么不强依赖 Redis：Redis 故障会变成主链路故障；路由系统需要“可降级可生存”。
  - 为什么不做更强一致计数服务：复杂度与成本高；很多场景近似一致已能显著降低事故率。
- **优化：**
  - 短期：切换事件纳入监控告警；加入切换冷却避免抖动。
  - 中期：in-memory 模式支持本地持久化或与 Redis 恢复后对账，降低切换期间偏差。
  - 长期：限流 sidecar/service 化（策略下发、统一观测、水平扩展），并引入 TPM 等更真实的负载度量。

**Related：B6**

### Q31（快照数据过期）快照优先会不会导致价格/能力过期？怎么降低风险？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：快照必然带来陈旧（staleness）风险，但它换来确定性与可用性；关键是把 staleness 变成“可观测、可控、可回退”。
  - 过期影响主要在两类：成本评估偏差（价格变了）与机会损失（新模型没进来）。
- **实现：**
  - 到期刷新：按 interval_days（例如 30 天）判断是否刷新；支持强制刷新（force sync）用于紧急修复。
  - 失败回退：刷新返回 0 模型或失败时，回退到最近一次成功快照并记录 alert，避免候选直接归零。
  - 观测：记录快照版本、刷新来源与是否刷新、刷新错误摘要与快照年龄；超阈值告警。
  - 策略兜底：关键请求可用 max_cost 约束防成本失控；必要时对价格做热更新而不等快照刷新。
- **边界：**
  - 价格快速变化：30 天过长会导致成本偏差；需要更短周期价格热更新或更严格 max_cost。
  - 多实例刷新风暴：同一时间刷新会压垮 provider；需要 jitter、集中刷新或分布式锁。
  - 能力来源漂移：外部榜单/评测更新后能力变化，快照会滞后；需要独立缓存或更频繁刷新。
- **取舍：**
  - 为什么不实时拉取：实时把外部依赖变成实时单点；延迟与失败直接传导到路由，稳定性差。
  - 为什么不强一致配置中心：复杂度与成本高；路由更看重可回放与可用性。
- **优化：**
  - 短期：对快照年龄与刷新失败率做告警；必要时自动切换到 live fetch 或触发强制刷新。
  - 中期：分层快照（模型列表低频、价格/配额高频），减少 staleness 的负面影响。
  - 长期：增量同步与订阅（push-based），让 staleness 降到分钟级同时保留回放能力。

**Related：B7, B1**

### Q32（监控选型）为什么选择 SQLite 侧车而不是 MQ/OTel/时序数据库？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：SQLite 侧车适合“低依赖、易部署、可本地回放”的 MVP 观测；MQ/OTel/TSDB 适合“分布式高吞吐与统一观测”的规模化阶段。
  - 选择的关键是阶段与成本：先跑通观测闭环，再逐步体系化扩展。
- **实现：**
  - SQLite：WAL + busy_timeout≈3s 提升并发友好；按 retention days 清理旧数据；提供 recent/stats 便于排障。
  - best-effort：监控写失败不影响主链路；关键异常事件（候选为空、回退快照、升阶不可用）优先记录。
  - 字段设计：可解释（原因/候选/限流状态）+ 可脱敏（task_hash/长度/维度摘要），便于合规。
- **边界：**
  - 写吞吐：高并发下 SQLite 监控写会成为瓶颈；必须采样/批处理/异步化。
  - 分布式聚合：多实例 SQLite 不好做全局统计；需要集中式后端（OTel collector/TSDB）。
  - 事件乱序：start/end 可能乱序或缺失；用 execution_id/request_id 关联并容忍不完整。
- **取舍：**
  - 为什么不直接用 MQ：可靠但引入运维、消费一致性与回压处理；对单机/CLI 不划算。
  - 为什么不直接用 OTel：很好但依赖 collector 与后端存储；MVP 更重。
- **优化：**
  - 短期：采样与批处理；异常路径优先记录，正常路径抽样。
  - 中期：提供可选 MQ/OTel 输出，SQLite 保留为 fallback 与本地调试。
  - 长期：统一观测体系（OTel+TSDB）+ 告警规则，把观测与策略学习数据打通。

**Related：B7**

### Q33（热点与扩展）当某个模型成为热点，哪些表/哪些计数会变成写热点？你会怎么优化？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：热点模型会把所有按 model_id 维度的状态推成热写（hot writes），问题通常来自单 key/单行/单索引争用，而不是平均吞吐不够。
  - 需要优先识别“写在哪里集中”“写是否可降级”“是否能聚合/分片”。
- **实现：**
  - 热点来源清单（典型）：RPM/RPD ZSET（同一个 key）、并发计数 key（normal/escalation）、成功/失败统计行、降级试验采样计数、监控事件落盘表。
  - 优化抓手：
    - 降写：utilization 缓存≈150ms、监控采样、统计聚合后批量写。
    - 分片：对 RPM/RPD key 做 shard（`key:{model}:{shard}`），读取时汇总；监控按时间分区。
    - 异步：事件先写队列，聚合统计异步更新（eventual consistency）。
- **边界：**
  - 分片读放大：写分片会增加读汇总成本；需要权衡“写热点 vs 读成本”。
  - 统计延迟：异步聚合会让学习状态滞后；策略要对短期误差不敏感（阈值与窗口）。
  - 热点迁移：热点模型随时间变化；分片策略要可扩缩容，不能写死。
- **取舍：**
  - 为什么不把所有统计都放 Redis：Redis 写强但持久化/审计弱；需要落盘做回放与长期分析。
  - 为什么不强一致更新：强一致降低吞吐；对学习统计通常接受 eventual consistency，并用对账校正。
- **优化：**
  - 短期：采样、批处理、减少低价值记录；把异常与关键事件优先记录。
  - 中期：将学习统计迁到更适合写扩展的存储（服务型 DB/流式系统），SQLite 仅保留审计与快照。
  - 长期：统一事件管道（log-based），支持在线策略与离线训练共用同一事件源。

**Related：B6, B7, B3**

### Q34（替代设计）为什么不用“纯规则/纯成本最小/纯能力最大/固定 tier 路由”？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：单目标或纯规则策略无法长期解决“质量-成本-拥塞”三角冲突；需要多信号融合与反馈闭环才能稳定演进。
  - 固定 tier 可以做框架，但仍要处理 tier 内差异、拥塞避让、故障切换、新模型演进等动态问题。
- **实现：**
  - 纯规则：任务类型→模型映射，短期快但维护成本高，且对新任务/新模型适应差。
  - 纯成本最小：易把流量推向便宜模型，质量风险上升；一旦事故发生需大量人工补丁。
  - 纯能力最大：成本不可控；拥塞时更容易雪崩；且无法利用降级试验持续降本。
  - 固定 tier：缺少细粒度排序与升阶/降级机制，很难在高并发与故障下保持稳定。
- **边界：**
  - 约束输入：max_cost、preferred_model、require_provider 等会让纯策略候选为空或不稳定。
  - 故障场景：provider 抖动与 429 风暴时，固定策略无法自救，需要动态避让与降级。
  - 数据漂移：任务分布变化会让规则失效；没有反馈闭环无法自适应。
- **取舍：**
  - 为什么不只靠离线评测选最强：离线与线上分布偏移；必须用在线反馈持续校正。
  - 为什么不一开始就 RL：reward 难、风险高；工程上先用可解释启发式+实验更稳。
- **优化：**
  - 短期：规则作为 guardrail（安全/合规/硬约束），在 guardrail 内用打分排序与限流/健康控制。
  - 中期：离线回放学习权重（learning-to-rank），上线 shadow 与小流量灰度。
  - 长期：contextual bandit/策略平台化，形成自动化“质量约束下成本最优”路由。

**Related：B1–B6**

### Q35（下一步优化）如果让你升级到学习型策略，你会选什么？为什么现在不直接用？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：学习型策略用数据自动找到权衡，但前提是可靠 reward 与严格风控；否则会把线上当实验田。
  - 常见方向：contextual bandit、Bayesian 优化、两阶段策略（先 provider 后 model）、learning-to-rank。
- **实现：**
  - reward 设计：至少包含执行成功率、质量评价（人工/自动）、成本（token 计费）、延迟（p95/p99）、拥塞（utilization）。
  - 风控：探索预算、error budget、灰度（canary≈0.5 或更小）、自动回滚（质量≥2/执行≥1）、冷却期（≈24h）。
  - 迁移路径：离线回放评估→影子模式（shadow）不影响线上→小流量实验→逐步扩大；所有阶段必须可解释与可回放。
- **边界：**
  - reward 稀疏：质量评价可能不全；代理指标（proxy metrics）会引入偏差。
  - 非平稳（non-stationary）：模型能力与任务分布会变化，学习算法必须适应漂移（drift）。
  - 探索成本：探索会引入失败与成本波动；没有足够风控会导致事故。
- **取舍：**
  - 为什么现在不直接用：需要数据积累、观测闭环、可控实验平台；否则风险过高且难以排障。
  - 为什么先启发式：启发式可解释、易回归，能快速建立训练数据与安全边界，为学习提供基础设施。
- **优化：**
  - 短期：把启发式参数化并可配置，配合离线回放寻找更优参数。
  - 中期：在低风险桶试点 bandit（Thompson/UCB），逐步扩大到更多任务。
  - 长期：策略平台化（策略版本、实验管理、自动回滚、特征存档），实现持续优化与安全迭代。

**Related：B2–B5**


### Q36（融合公式）能力分/成本分/健康修正/拥塞惩罚的融合公式如何设计？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：本项目把“质量排序”放在主轴（`dimension_score`），把“成本/拥塞”做成过滤与 tie-break，再叠加健康/可用性修正，保证解释性与稳定性。
  - 这里的“融合”不是把所有因子线性相加，而是分层决策：先保可用，再排序，再近似同分时用成本打平。
- **实现：**
  - 质量主分：`raw_dimension_score = compute_dimension_score(relevant_dimensions, model.capabilities)`，能力缺失默认 50 分并归一到 `[0,1]`。
  - 成本信号：`cost_score = compute_cost_score(model.pricing, relevant_dimensions)`，并用 `compute_effective_price_per_1m(...)` 做 `max_cost` 过滤（价格缺失则视为不可用价格）。
  - 池加成：`ClassPoolManager.apply_pool_bonus(...)` 按 trials 比例将 `dimension_score *= (1 + pct)`（`POOL_BONUS_BASE_RATIO~POOL_BONUS_FULL_RATIO`，`MIN_TRIALS` 归一）。
  - 健康修正：`HealthManager.get_health_modifier(...)` 返回 `(status, multiplier)`，成功 streak 用 `SUCCESS_BONUS_FACTOR ** bonus_level` 奖励，失败 streak 用 `FAIL_PENALTY_FACTOR ** penalty_level` 惩罚；奖励在非升阶场景会按 `(1 - cost_score)` 做“贵的少奖、便宜的多奖”。
  - 可用性/探测惩罚：selector 中对 `degraded` 扣 `DEGRADED_PENALTY`，对 probe cooldown 扣 `PROBE_COOLDOWN_PENALTY`；`unable` 直接不可选。
  - 新模型鼓励：非池候选若 `release_date` 在 `NEW_MODEL_LOOKBACK_DAYS` 内，加 `NEW_MODEL_BONUS`。
  - 拥塞处理：限流是硬过滤（`RateLimiter.is_rate_limited_async`），默认模型的拥塞是“跳过默认起点”（`_should_skip_default` 基于 `rpm_ratio/conc_ratio`），升阶前还有 `EscalationManager.escalate_with_overload_check_async` 的利用率门控与封顶。
- **边界：**
  - 当前实现里成本不进入主分，只在过滤与近似同分 tie-break 中发挥作用；因此“成本优化”主要体现在阈值/过滤与同分换挡，而不是改变强模型排序主序。
  - 需要注意：如果 `pricing` 缺失，`compute_cost_score` 会把成本分打到最差（1.0）；若用户设置了 `max_cost` 约束，则该候选会因有效单价为不可用哨兵值而被过滤，否则仍可能作为候选参与排序。
- **取舍：**
  - 分层融合（过滤/排序/tie-break）比“一条公式加权求和”更不容易被单一信号拖偏，也更利于排障（能说清是被过滤、被惩罚还是同分换挡）。
  - 代价是成本优化力度受限；如果后续要更激进降本，需要把成本更显式地进入排序（或引入 bandit/L2R）。
- **优化：**
  - 监控分项贡献：把 `raw_dimension_score`、pool bonus、health multiplier、degraded/cooldown 扣分、`cost_score` 同分换挡次数写入 monitoring metadata，减少“看不见的排序变化”。
  - 若要更强的拥塞惩罚：可在候选排序阶段引入 `utilization` 软惩罚（目前只对 default 起点做 skip），但需要避免把限流与健康重复惩罚导致候选被清空。

**Related：B1**

### Q37（融合策略）为什么能力分与成本分分开建模后再融合？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：在本项目里能力分决定主排序，成本分主要用于“约束（max_cost）+近似同分换挡”，因此必须分开才能保持质量语义稳定。
  - 分开建模还让“成本缺失/单位差异”不至于污染能力主分。
- **实现：**
  - `compute_dimension_score(...)` 专注于相关维度加权匹配；`compute_effective_price_per_1m(...)` 与 `compute_cost_score(...)` 专注于价格归一与成本尺度映射。
  - selector 里先按 `dimension_score` 排，再在 `abs(score_gap) < SCORE_TIER_EPSILON` 时以 `cost_score` 交换相邻候选，实现同分换挡降本。
  - 另外，健康奖励对“贵的模型”自动收敛到 1.0（`effective = 1 + (raw_bonus-1)*(1-cost_score)`），避免“又贵又被奖励”长期霸榜。
- **边界：**
  - 如果把成本直接加权进主分，权重一旦漂移会导致全局排序翻转，排障会变得困难（到底是能力变化还是价格变化）。
  - 分层也有风险：如果 `SCORE_TIER_EPSILON` 设太小，同分换挡触发少，降本效果会很弱。
- **取舍：**
  - 当前取舍偏可解释与稳定：成本不“抢方向盘”，只在边界区间做决策；这更符合路由系统“先稳再省”的演进路径。
- **优化：**
  - 把 `SCORE_TIER_EPSILON` 做成按 `agent_class/domain` 分桶配置；对高风险任务收紧同分换挡，对低风险任务放宽以更强降本。
  - 离线回放：统计“同分换挡触发率”与其对成功率/成本的边际影响，给阈值选型提供依据。

**Related：B1**

### Q38（近似阈值）近似分数阈值如何设置并验证不伤质量？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：近似阈值用于识别“能力差异不显著”的候选，再用成本优先降低总开销。
  - 阈值本质是质量与成本交换区间。
- **实现：**
  - 当前实现：候选先按 `dimension_score` 逆序，再单次遍历相邻元素，若 `abs(prev.dimension_score - curr.dimension_score) < SCORE_TIER_EPSILON` 且 `curr.cost_score < prev.cost_score`，交换两者顺序实现 tie-break。
  - 验证方法：离线回放对比不同 `SCORE_TIER_EPSILON` 的“交换次数/请求占比、平均有效单价、质量反馈（good/fair/poor）占比、升级次数与 P95 延迟”。
- **边界：**
  - 阈值过大：会把明显更强模型错误让位给低价模型。
  - 阈值过小：成本优化空间不足，策略退化为纯能力优先。
- **取舍：**
  - 固定阈值实现简单、可回归；动态阈值更灵活但调参复杂。
  - 生产初期优先固定阈值，避免策略抖动。
- **优化：**
  - 按任务域分层阈值（代码/数学阈值更保守）。
  - 建立阈值变更灰度与自动回滚门槛。

**Related：B1**

### Q39（缺失信号）任务维度缺失或置信不足时如何稳健打分？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：缺失信号不可避免，关键是让策略“保守退化而不是随机漂移”。
  - 稳健性目标是限制误差传播到排序前列。
- **实现：**
  - 能力缺失：`compute_dimension_score` 对缺失能力默认填 50 分（中位值思想），并将能力归一到 `[0,1]`，避免 NaN 进入排序。
  - 维度缺失：`relevant_dimensions` 为空时能力分直接返回 0.0；在“没有维度信号”的情况下，selector 会走冷启动起点 `_cold_start_index`（没有维度时默认选第 2 位附近），避免一上来就把最强/最贵模型当默认。
  - 价格缺失：`compute_cost_score` 会把成本分打到 1.0（最差）；若有 `max_cost` 约束则会被过滤，否则会在同分换挡与健康奖励调制中自然处于劣势，避免“无法估价却被当便宜”。
- **边界：**
  - 默认值会产生向均值回归偏差，对长尾任务影响更明显。
  - 维度全缺失时必须切换到更保守的冷启动策略。
- **取舍：**
  - 硬拒绝缺失输入最安全但可用性差；兜底填补可用性高但精度下降。
  - 路由系统通常优先“可用+可观测”，再逐步提升精度。
- **优化：**
  - 对缺失率做监控并反推分析器质量。
  - 用历史反馈逐步学习维度默认值而非长期固定常量。

**Related：B1**

### Q40（有效单价）成本“输入/输出动态加权”如何按任务类型落地？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：不同任务的输入/输出 token 结构不同，必须用任务信号做动态加权才接近真实成本。
  - “有效单价”是面向决策的成本代理，不是账单精确值。
- **实现：**
  - 当前实现不直接用“task_type 枚举”，而是用 `relevant_dimensions` 作为任务信号：当维度包含输出侧更敏感的维度（如 `coding`、`creative_writing`）时，提高 `w_out`。
  - `compute_effective_price_per_1m`：`w_out = 0.3 + 0.4 * min(output_signal, 1.0)`，其中 `output_signal = sum(dim.score for dim in relevant_dimensions if dim.dimension in {'coding','creative_writing'}) / 10`，因此 `w_out` 在 `[0.3, 0.7]`。
  - 单位归一：默认认为上游价格为 `per_1k_tokens` 并转换到 `per_1m_tokens`；若 `pricing.unit == 'per_1m_tokens'` 则直接使用。
  - `effective_price_per_1m` 用于 `max_cost` 过滤；`cost_score` 用于近似同分 tie-break 以及健康奖励的“贵/便宜”调制。
- **边界：**
  - 权重长期不更新会与真实分布脱节，导致成本估计偏差。
  - 当输出长度波动很大（如生成任务）时应提高 `w_out` 敏感度。
- **取舍：**
  - 动态权重比固定权重复杂，但能显著降低结构性成本误判。
  - 精确预测 token 成本更准，但建模与维护成本更高。
- **优化：**
  - 周期性对账估算成本与真实账单偏差。
  - 引入任务级 token 预测模型替代静态权重表。

**Related：B1**

### Q41（惩罚去重）健康修正与拥塞惩罚如何避免重复惩罚？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：健康反映“模型质量/可用性趋势”，拥塞反映“实时容量压力”，两者应分工而非叠加过罚。
  - 去重目标是避免同一故障在多个因子被重复扣分。
- **实现：**
  - 本项目把信号分成三层，天然减少重复惩罚：
  - 过滤层：`unable` 不可选；`is_rate_limited_async` 直接过滤；`max_cost/context` 直接过滤。
  - 排序层：健康 streak 仅通过 multiplier 影响 `dimension_score`；`degraded` 与 probe cooldown 仅扣固定值（`DEGRADED_PENALTY`/`PROBE_COOLDOWN_PENALTY`），不再重复打“拥塞分”。
  - 决策层：拥塞主要影响“是否从 default 起步”和“升阶是否允许”，而不是对所有候选统一扣分（避免一次拥塞把候选整体压扁）。
- **边界：**
  - provider 抖动会同时触发失败和拥塞，若无门控会导致候选被整体清空。
  - 惩罚衰减过慢会让恢复后的模型长期被压制。
- **取舍：**
  - 强惩罚更保守但可能误伤可恢复模型；弱惩罚更激进但可能放大故障。
  - 默认采用分层惩罚+快速恢复窗口是工程折中。
- **优化：**
  - 增加惩罚来源标签，便于观测“谁在主导排序变化”。
  - 对惩罚因子做时间衰减和分桶校准。

**Related：B1/B6**

### Q42（候选编排）“保底强模型+池优先+探索补位”组合的触发顺序是什么？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：先保留能力天花板，再利用历史命中池保证稳定，最后用探索槽位控制学习速度。
  - 这是“性能下限+经验优先+持续探索”的组合策略。
- **实现：**
  - 本项目的编排发生在 `router_engine/selector.py`，并且是“有池/无池”两套路径：
  - 有池（`pool_entries` 非空）：
  - 先选 1 个 ceiling：`ceiling_model = max(ranked, key=raw_dimension_score)`，由 `CEILING_SLOTS` 控制是否必选，确保“能力天花板”一定在候选里。
  - 再填池：把池内候选按排序顺序填入，直到候选数达到 5（去重）。
  - 再探索补位：对非池候选取 `explore_slots = _adaptive_explore_slots(len(pool_entries), avg_trials)` 个探索位，并通过 `_provider_diverse_limit(..., max_per_provider=MAX_SAME_PROVIDER_IN_CANDIDATES)` 做 provider 多样性约束。
  - 无池：
  - 直接取 `ranked` 的前部候选（并做 provider 多样性限制）组成最多 5 个候选。
  - 起始索引（start_index）是单独决策的：优先 `preferred_model`；其次类默认模型（若 default 未过载）；否则 pool top1；再否则冷启动 `_cold_start_index(...)`。
- **边界：**
  - pool 很小时探索不足会导致学习停滞。
  - pool 很大但质量集中时，探索过多会增加波动。
- **取舍：**
  - 先池后探索可稳定线上质量，但收敛新模型较慢。
  - 先探索后池能更快发现新模型，但质量风险更高。
- **优化：**
  - 引入按风险等级动态调整槽位比例。
  - 记录每次候选构成原因，支持回放解释。

**Related：B2**

### Q43（探索自适应）探索槽位自适应依赖哪些在线信号？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：探索比例应随“池子成熟度”变化，而不是固定常数。
  - 关键是识别“什么时候该稳，什么时候该学”。
- **实现：**
  - 当前实现只依赖两类在线信号（可解释、易回归）：`pool_size` 与 `avg_trials`（池内平均试验次数 = `(success+fail)/len(pool)`）。
  - `_adaptive_explore_slots(pool_size, avg_trials)`：
  - 当 `pool_size >= EXPLORE_POOL_RICH_THRESHOLD` 且 `avg_trials >= EXPLORE_AVG_TRIALS_THRESHOLD`，返回 `EXPLORE_SLOTS_MIN`（少探索，偏稳）。
  - 当 `pool_size < 3` 或 `avg_trials < EXPLORE_AVG_TRIALS_THRESHOLD`，返回 `EXPLORE_SLOTS_MAX`（多探索，偏学）。
  - 否则返回 `MAX_EXPLORE_SLOTS`（折中）。
- **边界：**
  - 信号窗口过短会抖动，过长会迟钝。
  - 单一信号决策容易误判（例如仅看 pool size）。
- **取舍：**
  - 固定探索比例实现最简单，但无法适应不同阶段。
  - 自适应更智能，但需要更多监控与调参。
- **优化：**
  - 引入最小/最大探索边界防止极端。
  - 按 agent_class 分桶维护探索策略，减少全局耦合。

**Related：B2**

### Q44（供应商上限）同 Provider 占比上限如何设定与退化？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：Provider 占比上限是防单点拥塞与供应商抖动扩散的第一道保护。
  - 目标是候选集内多样性，而非全局绝对公平。
- **实现：**
  - 本项目用 `_provider_diverse_limit(candidates, limit, max_per_provider)` 做软约束：
  - 第一轮：每个 provider 最多取 `MAX_SAME_PROVIDER_IN_CANDIDATES` 个，直到达到 `limit`（候选上限）。
  - 若第一轮选不满：从 overflow 里继续补齐（此时相当于自动放宽上限），保证“可用性优先，不因多样性把候选弄成 0”。
- **边界：**
  - 可用候选天然集中于单 provider 时，硬限制会造成候选为空。
  - 供应商能力差异很大时，过严上限会损失质量。
- **取舍：**
  - 严格上限提高韧性但可能降质；宽松上限提高质量但集中风险增加。
  - 生产通常采用“默认限制+短缺放宽”的退化策略。
- **优化：**
  - 按业务优先级设置不同上限。
  - 结合供应商实时健康度做动态上限调整。

**Related：B2/B6**

### Q45（层级职责）候选阶段多样性与全局负载均衡如何分工？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：候选阶段解决“局部可选空间”，全局负载均衡解决“跨请求容量分配”。
  - 两者目标不同、时效不同，不应混成一层。
- **实现：**
  - 候选阶段（selector）：做 provider 多样性、pool/explore 编排，并在“default 过载”时通过 `_should_skip_default(util)` 改变 start_index（不改变候选列表）。
  - 全局负载（rate_limiter + escalation）：用 `is_rate_limited_async` 过滤候选（RPM/RPD/并发任一触线即拦），用 `get_utilization_async` 决定 default 是否过载、升阶是否允许，以及升阶并发封顶（`is_escalation_capped_async`）。
- **边界：**
  - 若只做候选多样性，不做全局限流，仍可能在高并发下雪崩。
  - 若只做全局限流，不做候选多样性，会放大供应商单点风险。
- **取舍：**
  - 单层实现简单但边界模糊；分层实现更复杂但可维护性更高。
  - 路由系统更适合分层治理。
- **优化：**
  - 在观测面统一输出“候选原因+限流原因”。
  - 做跨层回放分析，定位策略冲突点。

**Related：B2/B6**

### Q46（路径依赖）池命中优先如何避免路径依赖与模型固化？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：池优先能稳定质量，但天然存在“历史优势自增强”风险。
  - 需要用探索与淘汰机制打破固化。
- **实现：**
  - 探索打破固化：pool 之外固定留探索槽位，并且探索位受 provider 多样性约束，降低“永远只试同一家”的路径依赖。
  - 入池门槛：`try_add_to_pool` 以 Wilson lower bound（`POOL_ENTRY_CONF_LB_MIN`）作为门槛，抑制小样本偶然命中导致的“早入池、难出池”。
  - 池内奖励是渐进的：`apply_pool_bonus` 的加成比例随 trials 从 0 到 `MIN_TRIALS` 线性增长，避免刚入池就被强力加成固化。
  - 退场机制：`evict_check` 会移出 `unable` 模型；对超龄模型（`POOL_MODEL_MAX_AGE_DAYS`）做淘汰，默认模型与高成功率模型可豁免（`POOL_AGE_EXEMPT_*`）。
  - 新模型轻微鼓励：非池且近期发布会加 `NEW_MODEL_BONUS`，提高“被看到”的概率。
- **边界：**
  - 探索过少：新模型难以进入，策略老化。
  - 探索过多：线上波动上升，学习噪声变大。
- **取舍：**
  - 稳定性与新鲜度是典型冲突，需要按业务风险分层。
  - 高风险业务更偏稳定，低风险业务可提高探索。
- **优化：**
  - 引入年龄衰减或最近表现权重，弱化历史包袱。
  - 增加“长期未挑战默认模型”告警。

**Related：B2/B3**

### Q47（候选不足）候选不足时 Top-K 退化策略如何保证可用性？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：候选不足时应优先“可用退化”，不能因为策略完整性阻断请求。
  - 退化策略需要明确起始索引与告警信号。
- **实现：**
  - selector 会在候选数不足时继续返回决策（不补“假候选”），并通过 `alerts` 标注（阈值为 `MIN_CANDIDATES_FOR_AUTO`）。
  - start_index 退化是明确的：
  - 有 `preferred_model` 则直接选；
  - 有 default 则先查 utilization，过载则改为从 0 起步（reason: `default overloaded...`），不然从 default 起步；
  - 无 default 且无 pool 时走 `_cold_start_index(...)`；
  - 若最终候选数 < 5 且没有 preferred/default/pool，会把 start_index 调到 `len(candidates)-2`（避免“候选太少时一上来就打最强/最贵”的极端）。
- **边界：**
  - 候选长期不足意味着上游注册表、限流或过滤条件异常。
  - 若未输出可解释告警，排障成本会显著上升。
- **取舍：**
  - 继续服务可能降低质量；直接失败最清晰但可用性差。
  - 大多数在线路由场景优先继续服务并可观测降级。
- **优化：**
  - 输出候选被过滤原因统计（成本、provider、context、health）。
  - 针对长期不足自动触发参数巡检。

**Related：B2/B5**

### Q48（统计门槛）为什么用置信下界而不是 success rate 做入池/晋升？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：置信下界把样本量纳入决策，能抑制小样本偶然高分。
  - success rate 只看点估计，容易被噪声带偏。
- **实现：**
  - 本项目在 `router_engine/class_pool.py` 与 `router_engine/defaults.py` 都实现了 Wilson lower bound（`_wilson_lower_bound`，`z=WILSON_Z`），用它把 success rate 与样本量绑定成一个“保守可用”的分数。
  - 入池：`ClassPoolManager.try_add_to_pool` 要求 `wilson_lb >= POOL_ENTRY_CONF_LB_MIN` 才允许进入 `class_pool`。
  - 晋升默认：`DefaultsStore.evaluate_and_promote_default_async` 对候选要求 `success_count >= DEFAULT_PROMOTION_MIN_SUCCESS`，并用 `wlb` 与 incumbent 对比决定是否切换。
- **边界：**
  - 样本太少时下界保守，可能延缓优秀新模型晋升。
  - 反馈延迟会导致下界更新滞后。
- **取舍：**
  - 保守统计提高稳定性但降低响应速度。
  - 激进统计收敛快但事故概率高。
- **优化：**
  - 按风险等级配置不同最小样本门槛。
  - 用先验平滑与分桶统计提升冷启动体验。

**Related：B3**

### Q49（阈值协同）入池阈值、晋升阈值、淘汰阈值如何协同？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：三类阈值必须形成有“滞回区间”的状态机，避免频繁进出与默认抖动。
  - 协同重点是让“进入比退出更难/或相反”可配置。
- **实现：**
  - 入池阈值：`POOL_ENTRY_CONF_LB_MIN`（Wilson LB）门槛控制“进池速度”；入池后奖励通过 `apply_pool_bonus` 随 trials 渐进（`MIN_TRIALS` 归一），避免“刚进池就固化”。
  - 晋升阈值：默认晋升由 `evaluate_and_promote_default_async` 控制，至少满足 `success_count >= DEFAULT_PROMOTION_MIN_SUCCESS`，并且 challenger 需要 `consecutive_success >= CHALLENGER_LEAD_STREAK` 才允许替换（防抖）。
  - 回滚/撤销阈值：默认模型在 `record_fail_async` 中累计 `consecutive_fail`，达到 `QUALITY_FAIL_REVOKE` 会自动清空默认；降级试验则有独立回滚阈值与冷却期（`DOWNGRADE_ROLLBACK_*`、`DOWNGRADE_COOLDOWN_H`）。
  - 淘汰阈值：执行失败驱动可用性状态机（available→degraded→unable），进入 `unable` 会被直接移出池；另外 `evict_check` 会按年龄（`POOL_MODEL_MAX_AGE_DAYS`）淘汰，并允许默认/高成功率模型豁免（`POOL_AGE_EXEMPT_*`）。
- **边界：**
  - 阈值过近会造成来回切换。
  - 阈值过远会导致策略僵化，难以适应分布变化。
- **取舍：**
  - 强滞回稳定但收敛慢；弱滞回收敛快但抖动大。
  - 生产通常偏强滞回，再用 canary 加速验证。
- **优化：**
  - 周期性回放“阈值敏感性”并输出推荐区间。
  - 按 agent_class 维护差异化阈值。

**Related：B3**

### Q50（异常归因）连续失败惩罚如何区分模型退化与外部异常？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：惩罚前要先做失败归因，否则会把外部故障误判成模型质量问题。
  - 核心是区分 `exec_fail` 与 `quality_fail`。
- **实现：**
  - 在 `router_engine/engine.py` 里是两条明确链路：
  - `report_execution_async(..., completed=False)` 记录 `exec_fail`（幂等事件表先去重），并调用 `HealthManager.report_exec_failure_async` 驱动可用性状态迁移（available→degraded→unable），同时 `ClassPoolManager.record_outcome(..., 'exec_fail')` 只累积 `exec_fail_count`。
  - `report_quality_async(..., rating=poor)` 记录 `quality_poor`，并调用 `HealthManager.on_quality_fail_async` 更新 bonus/penalty（质量趋势），`DefaultsStore.record_fail_async` 可能触发默认撤销。
  - 质量反馈还带有“因果门控”：`report_quality_async` 只有在同一 `(request_id, model_id)` 已存在 `exec_success` 且不存在 `exec_fail` 时才会生效，降低“外部执行失败却被打质量差”的误惩罚。
- **边界：**
  - 归因不准确会导致错惩罚，进而误导学习闭环。
  - provider 大面积抖动时多模型会同步失败，需有跨模型异常识别。
- **取舍：**
  - 细粒度归因更准确但实现复杂。
  - 粗粒度归因简单但误判率高。
- **优化：**
  - 引入失败类型质量审计与抽样复核。
  - 在监控中增加“外部异常占比”指标。

**Related：B3/B7**

### Q51（防抖）默认模型切换如何防抖（promote/rollback 抖动）？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：防抖核心是“晋升与回滚门槛不对称 + 冷却期 + 连续性条件”。
  - 目标是减少策略来回切换对用户体验的冲击。
- **实现：**
  - 用户锁定优先：`class_pool_defaults.is_locked` 为真时，`evaluate_and_promote_default_async` 直接返回当前默认，不做自动切换。
  - 晋升防抖（promote）：挑战者必须满足 `success_count >= min_success`，并且 `consecutive_success >= CHALLENGER_LEAD_STREAK`；比较指标以 Wilson LB 为主，价格与发布时间作为次级 tie-break（且在 `abs(wlb_gap) < SCORE_TIER_EPSILON` 时才进入价格/时间比较）。
  - 回滚/撤销（rollback/revoke）：默认模型质量失败由 `QUALITY_FAIL_REVOKE` 控制，连续失败达到阈值直接清空默认；降级试验回滚由 `DOWNGRADE_ROLLBACK_EXEC_FAIL/DOWNGRADE_ROLLBACK_QUALITY_FAIL` 控制，并进入 `DOWNGRADE_COOLDOWN_H` 冷却期。
- **边界：**
  - 冷却期过短无法抑制抖动，过长会错失降本机会。
  - 若默认被用户锁定，应禁止自动切换。
- **取舍：**
  - 防抖强意味着响应慢；防抖弱意味着更敏捷但更不稳定。
  - 高并发生产环境通常优先稳定。
- **优化：**
  - 将抖动频率纳入核心告警。
  - 使用序贯检验替代固定阈值，减少误切换。

**Related：B3/B4**

### Q52（冷启动）类池学习中的冷启动如何避免噪声误导？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：冷启动阶段数据稀疏，应限制其对默认决策的影响权重。
  - 目标是“允许探索，不允许轻易定性”。
- **实现：**
  - “先少量试，再逐步加权”：新模型主要通过探索槽位进入候选（由 `_adaptive_explore_slots` 控制），并且入池前必须通过 Wilson LB 门槛（`POOL_ENTRY_CONF_LB_MIN`）。
  - “进池也不立刻固化”：池内 bonus 是随 trials 渐进放大（`MIN_TRIALS` 归一），降低早期噪声对排序的放大效应。
  - “默认晋升更保守”：默认切换要求 `DEFAULT_PROMOTION_MIN_SUCCESS` 与 `CHALLENGER_LEAD_STREAK`，避免冷启动把默认抖来抖去。
- **边界：**
  - 流量太低时收敛速度慢。
  - 高噪声反馈会导致早期结论不稳定。
- **取舍：**
  - 快速启用新模型能抢先收益，但风险高。
  - 保守启用更稳，但可能错失窗口期。
- **优化：**
  - 对冷启动流量单独分桶并追踪。
  - 结合离线评测给冷启动模型提供初始优先级。

**Related：B3/B2**

### Q53（反馈延迟）反馈延迟或缺失时学习闭环如何保持稳定？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：延迟反馈会造成“决策先行、真相后到”，闭环必须容忍最终一致。
  - 稳定目标是减少短期噪声对长期策略的误导。
- **实现：**
  - 幂等事件表先行：`RouterStorage.try_record_event` 以 `(request_id, model_id, event_type)` 唯一键 `INSERT OR IGNORE`，允许上游重试与乱序到达。
  - 因果门控减少乱序污染：质量反馈只有在“先有 exec_success、且没有 exec_fail”时才会更新健康与默认；否则直接返回（等待后续补齐，再次上报也不会重复计数）。
  - 对缺失反馈保持保守：没有质量信号就不会触发 `on_quality_fail/good`，系统更倾向用执行可用性保护与冷启动起点维持稳定。
- **边界：**
  - 长期缺失会使质量指标失真，偏向执行成功率。
  - 乱序写入若无幂等约束会造成重复统计。
- **取舍：**
  - 严格等待完整反馈最准确但延迟大。
  - 先决策后纠偏可用性高但需要对账能力。
- **优化：**
  - 监控反馈完整率和反馈时延分布。
  - 定期回放对账修正聚合统计。

**Related：B3**

### Q54（升阶分界）升阶状态机里 retry 与 escalate 的分界是什么？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：retry 适用于可恢复短暂失败，escalate 适用于能力不足或持续失败。
  - 分界点是失败类型与失败次数，而非单次结果。
- **实现：**
  - 本项目的状态机在 `router_engine/escalation.py`：
  - 无历史尝试时永远 `retry` 当前模型（让调用侧至少跑一次，不因为空记录直接升阶）。
  - 质量失败（`failure_type == 'quality'`）会“容忍 1 次”：同一模型第一次质量差仍 `retry`，从第二次开始才会向更强候选 `escalate`。
  - 非质量失败（例如执行失败、超时等）不会走“质量容忍”，会更快进入 `escalate`（按候选序列向更强模型移动）。
  - 升阶目标优先取候选序列中更靠前（更强）的模型（`current_index-1`）；若已经到顶仍失败，尝试 `escalate_breakthrough`（从全量可用模型里找一个“原始能力上界更高”的突破候选），否则 `alert_top_failed`。
- **边界：**
  - 过多重试会放大延迟和拥塞。
  - 过早升阶会显著提高成本。
- **取舍：**
  - 保守 retry 降成本但可能伤体验。
  - 激进 escalate 提升成功率但成本与拥塞风险上升。
- **优化：**
  - 按 failure_type 维护独立重试预算。
  - 输出每次状态机动作原因，支持回放评估。

**Related：B5**

### Q55（失败语义）为什么区分 quality fail 与 exec fail 两类失败信号？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：两类失败对应不同控制面：质量失败影响策略偏好，执行失败影响可用性保护。
  - 混淆两者会导致错误动作。
- **实现：**
  - `exec_fail`（执行面）：`report_execution_async` 触发 `HealthManager.report_exec_failure_async`，驱动可用性状态机（available→degraded→unable），并可能将模型从 `class_pool` 移除，属于“止血”。
  - `quality_fail`（策略面）：`report_quality_async` 触发 `on_quality_fail_async` 更新 bonus/penalty（影响排序偏好），并通过 `DefaultsStore.record_fail_async` 维护默认撤销逻辑，属于“学习/偏好更新”。
  - 降级试验也复用这一区分：回滚阈值对 `exec_fail` 更敏感（`DOWNGRADE_ROLLBACK_EXEC_FAIL` 小于 `DOWNGRADE_ROLLBACK_QUALITY_FAIL`）。
- **边界：**
  - 误分类会出现“该止血不止血”或“该学习不学习”。
  - 多源反馈冲突时需有优先级规则。
- **取舍：**
  - 细分语义增加实现复杂度，但显著提升决策准确性。
  - 粗粒度失败处理简单但副作用大。
- **优化：**
  - 建立失败分类字典与抽样复审流程。
  - 用监控统计分类漂移与误判率。

**Related：B5/B4**

### Q56（过载前置）升阶前过载检查为何放在动作前而非动作后？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：过载检查必须前置，才能阻断“失败→升阶→更拥塞”的正反馈回路。
  - 后置检查属于事后补救，已产生额外压力。
- **实现：**
  - `EscalationManager.escalate_with_overload_check_async` 在返回 `escalate` 之前先做利用率门控：
  - 目标模型 `util.is_limited` 直接视为不可升阶（返回 `alert_escalation_unavailable`）。
  - `priority == 'normal'`：若 `peak >= max(RPM_UTIL_HIGH, CONC_UTIL_HIGH)`，直接回退为 `retry` 当前模型（避免把升阶流量进一步打爆拥塞模型）。
  - `priority == 'elevated'`：若 `peak >= ESCALATION_UTIL_CEILING`，直接禁止升阶并告警（保留更硬的上限）。
  - 同时检查 `is_escalation_capped_async`（升阶并发封顶），若封顶则尝试选择未封顶的候选替代，否则回退或告警。
- **边界：**
  - 检查过严会错失本可成功的升阶机会。
  - 检查过松会放大高峰期雪崩风险。
- **取舍：**
  - 前置检查牺牲部分成功率峰值，换取系统稳定上限。
  - 对生产系统通常是合理交换。
- **优化：**
  - 引入等待/退避策略，减少直接放弃。
  - 按业务优先级分层阈值。

**Related：B5/B6**

### Q57（优先级语义）normal/elevated/forced 优先级的流量语义如何定义？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：优先级是“是否允许穿透拥塞保护”的策略开关，不是简单标签。
  - 三类优先级应映射到不同过载容忍度。
- **实现：**
  - 本项目代码里实际使用的是 `normal` 与 `elevated` 两档（`forced` 未在状态机中落地，可作为未来扩展）。
  - `normal`：当目标模型利用率达到 `RPM_UTIL_HIGH/CONC_UTIL_HIGH` 时不允许升阶（回退 retry）。
  - `elevated`：允许在更高负载下尝试升阶，但仍受 `ESCALATION_UTIL_CEILING` 与 `is_limited` 的硬边界约束。
  - 若未来引入 `forced`：建议只绕过 soft 阈值（high）而不绕过硬边界（`is_limited`、封顶、候选为空），并必须纳入审计。
- **边界：**
  - 若 elevated 滥用，会挤占正常流量。
  - forced 无审计会变成隐形旁路。
- **取舍：**
  - 严格权限控制降低滥用但操作不灵活。
  - 宽松策略灵活但系统风险高。
- **优化：**
  - 为 elevated/forced 建立调用审计与配额。
  - 周期复盘优先级使用收益与代价。

**Related：B5/B6**

### Q58（升阶封顶）升阶并发封顶如何防止“越忙越升阶”正反馈？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：将升阶流量与正常流量分桶并设封顶，阻断异常时的放大链路。
  - 封顶本质是给正常流量保留生存空间。
- **实现：**
  - rate_limiter 里把并发拆成两桶：`route_agent:conc:normal:<model_id>` 与 `route_agent:conc:esc:<model_id>`（Redis）/ `{'normal','escalation'}`（InMemory）。
  - 封顶规则：`esc_cap = max(1, int(max_concurrency * ESCALATION_CONC_RATIO))`，当 `esc >= esc_cap` 认为升阶被封顶（`is_escalation_capped_async`）。
  - 状态机行为：封顶时尝试在候选列表里找一个“未封顶且未限流”的替代模型；找不到则 `normal` 回退为 retry，`elevated` 直接告警不可升阶。
- **边界：**
  - cap 太低会压制必要升阶，cap 太高失去隔离意义。
  - 非原子检查存在短暂超限窗口。
- **取舍：**
  - 强隔离提升稳定性但会牺牲部分峰值成功率。
  - 弱隔离提升短期成功率但故障时风险更高。
- **优化：**
  - 将 cap 做成按时段/负载自适应。
  - 热点模型优先启用原子脚本降低超限误差。

**Related：B5/B6**

### Q59（Canary联动）降级 canary 比例、样本量、节省阈值如何联动？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：三参数分别控制风险暴露、统计稳定和收益门槛，必须联动而非独立调参。
  - canary 目标是在可控风险下验证降本。
- **实现：**
  - 本项目在 `router_engine/downgrade.py` 做了显式联动：
  - 先过“是否值得试”的门槛：`expected_savings_ratio >= DOWNGRADE_MIN_SAVINGS_RATIO`，并且 incumbent 必须是当前默认且连续成功达到 `DOWNGRADE_SUCCESS_THRESHOLD`（先稳再省）。
  - 再过“质量差距”门槛：用 Wilson LB 比值近似质量比（`ratio_score`），要求 `score_gap <= DOWNGRADE_SCORE_GAP_MAX`，避免拿明显更差的模型来做降级试验。
  - 通过后才启动试验并写入 `downgrade_trials`（唯一活跃索引保证同一 `(class,domain)` 同时最多 1 个试验），并固定 `canary_ratio=DOWNGRADE_CANARY_RATIO` 随机导流到 challenger。
  - 决策门槛：样本不足（`sampled_requests < DOWNGRADE_TRIAL_MIN_SAMPLES`）只记录不决策；达到样本门槛后才可能 promote/rollback。
- **边界：**
  - 低流量场景样本积累慢，评估周期会很长。
  - 高波动场景固定样本门槛可能不稳。
- **取舍：**
  - 大 canary 收敛快但风险高；小 canary 风险低但收敛慢。
  - 需要结合业务流量规模设定。
- **优化：**
  - 用序贯检验替代固定样本门槛。
  - 对不同 agent_class 使用不同 canary 参数。

**Related：B4**

### Q60（回滚敏感度）降级试验中的回滚条件为何执行失败更敏感？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：执行失败直接影响可用性，应优先止血；质量失败通常可通过更多样本确认。
  - 回滚敏感度体现“可用性优先”原则。
- **实现：**
  - 在 `record_downgrade_result_async` 中：
  - `exec_fail_count >= DOWNGRADE_ROLLBACK_EXEC_FAIL` 立即回滚（默认阈值为 1），优先止血可用性风险。
  - `quality_fail_count >= DOWNGRADE_ROLLBACK_QUALITY_FAIL` 才回滚（默认阈值更高），允许质量信号有一定噪声空间。
  - 回滚后通过 `finish_downgrade_trial_async(..., cooldown_h=DOWNGRADE_COOLDOWN_H)` 写入冷却期，避免短时间重复试验同一个 challenger。
- **边界：**
  - 若执行失败来自外部网络抖动，可能导致误回滚。
  - 若质量反馈延迟，质量回滚会滞后。
- **取舍：**
  - 高敏感止血快但误回滚概率增大。
  - 低敏感更稳健但事故窗口更长。
- **优化：**
  - 结合失败分类与跨模型对照降低误回滚。
  - 将回滚原因结构化落盘，便于后续复盘。

**Related：B4**

### Q61（动作冲突）升阶与降级同时触发时如何裁决优先级？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：当“质量救火”和“成本优化”冲突时，优先级必须明确为先稳后省。
  - 裁决核心是风险等级而非成本收益。
- **实现：**
  - 本项目的“冲突裁决”更多是通过触发条件隔离实现的：
  - 升阶发生在执行链路里（调用侧根据 `EscalationManager` 决定下一步）；它面对的是“当前请求失败如何救火”。
  - 降级试验只会在质量反馈链路中、且 incumbent 达到连续成功阈值、并且 incumbent 是默认模型时才会 `maybe_start_downgrade_trial_async`（不满足稳定窗口就不会启动试验）。
  - 一旦出现质量失败或执行失败，降级试验会被回滚（对应 `DOWNGRADE_ROLLBACK_*`），因此实际效果就是“先稳后省”。
- **边界：**
  - 若长期处于高压，降级优化会持续被抑制。
  - 若裁决逻辑不透明，容易产生策略误解。
- **取舍：**
  - 先稳后省牺牲短期降本，但显著降低事故风险。
  - 激进降本可短期节约成本，但会放大质量波动。
- **优化：**
  - 建立“稳态窗口”判定指标自动恢复降级试验。
  - 输出冲突裁决日志，便于可解释审计。

**Related：B4/B5**

### Q62（多维限流）RPM/RPD/并发三限流维度的决策优先级如何定？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：并发保护短时稳定，RPM 控短窗吞吐，RPD 控日级预算，三者是互补关系。
  - 决策应以“最先触线者优先拦截”。
- **实现：**
  - `RateLimiter.is_rate_limited_async` 在一次检查里同时取 RPM（60s 滑窗）、RPD（24h 滑窗）与并发（normal+escalation 总和）；任一超限即返回限流（OR 语义）。
  - `get_utilization_async` 输出的利用率主要覆盖 RPM 与并发（以及升阶封顶状态），供 selector 的 default skip 与 escalation 的过载检查复用；RPD 作为“预算上限”只参与硬限流判断（不做实时利用率曲线）。
- **边界：**
  - 只看单维会留下系统漏洞。
  - 不同维度阈值不协调会造成策略冲突。
- **取舍：**
  - 严格多维检查更稳但可能更保守。
  - 简化为单维实现更轻但风险不可控。
- **优化：**
  - 引入 TPM 等更贴近真实负载的维度。
  - 按租户/业务分桶限流提升公平性。

**Related：B6**

### Q63（滑窗误差）Redis 滑窗在高并发下的误差与一致性边界是什么？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：ZSET 滑窗是近似一致方案，能显著降低边界突刺，但并非强一致事务。
  - 误差主要来自并发读写窗口和时间源漂移。
- **实现：**
  - 典型流程是裁剪窗口外样本后计数，再与阈值比较。
  - 通过 pipeline 降低 RTT，并配合 key TTL 控制存储开销。
- **边界：**
  - 裁剪与计数分步执行存在短暂竞态。
  - 多机时钟不一致会扩大窗口偏差。
- **取舍：**
  - 近似一致足以覆盖大多数限流场景，成本低。
  - 强一致脚本更精确，但复杂度和维护成本更高。
- **优化：**
  - 热点模型使用 Lua 原子脚本。
  - 统一时间源或增加时钟健康监控。

**Related：B6**

### Q64（分桶并发）normal/escalation 分桶计数为何优于单桶并发计数？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：分桶的价值是隔离异常流量，防止升阶流量吞噬正常容量。
  - 单桶只看总量，无法做优先级治理。
- **实现：**
  - 请求开始/结束按流量类型分别 `INCR/DECR`，并计算升阶占比是否达 cap。
  - 总并发与分桶并发同时纳入利用率快照。
- **边界：**
  - 分桶后需要保证计数不泄漏（异常退出、超时）。
  - 分桶过多会增加管理复杂度。
- **取舍：**
  - 双桶方案在稳定性和复杂度间平衡较好。
  - 单桶最简单，但在高并发故障场景防护不足。
- **优化：**
  - 对关键模型做桶级监控与告警。
  - 将 cap 按时间段动态调整。

**Related：B6**

### Q65（降级风控）Redis 不可用切 InMemory 时风险如何评估与告警？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：切 InMemory 保住可用性，但牺牲跨实例全局一致限流语义。
  - 风险评估重点是“可接受偏差区间”。
- **实现：**
  - `create_rate_limiter(mode='auto', fail_strategy='degrade')` 会在 Redis 初始化失败时自动回退到 `InMemoryRateLimiter`，并把 `RateLimiterStatus.switched_at/last_error` 填上（可用于观测与审计）。
  - 路由监控事件里当前记录的是 `rate_limiter_mode` 与 `rate_limiter_fail_strategy`；`switched_at/last_error` 已在 `engine.rate_limiter_status()` 暴露，若需要落盘可把它们追加到 monitoring metadata。
- **边界：**
  - 多实例部署时会低估全局利用率。
  - 若频繁抖动来回切换，会导致行为不稳定。
- **取舍：**
  - degrade 提升连续可用性；fail_fast 保持语义严格性。
  - 选择取决于业务是“可用优先”还是“强约束优先”。
- **优化：**
  - 增加熔断与最短驻留时间，避免抖动切换。
  - 将降级期间误差纳入容量预算评估。

**Related：B6/B7**

### Q66（缓存窗口）利用率缓存窗口如何在准确性与降压间平衡？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：短缓存是“以轻微新鲜度损失换显著后端降压”的工程策略。
  - 核心是控制缓存窗口在可接受误差内。
- **实现：**
  - Redis 限流器对 `get_utilization_async` 做了毫秒级缓存（`UTIL_CACHE_TTL_MS`，当前为 150ms），用来压缩热点模型的 Redis 读放大。
  - 关键动作仍会做实时门控：例如升阶时会再次读取目标模型 utilization 并检查 `is_limited` 与 `is_escalation_capped_async`，避免缓存导致的长期误判。
- **边界：**
  - 窗口过大会导致拥塞反应滞后。
  - 窗口过小降压效果不明显。
- **取舍：**
  - 保守窗口提升准确性但增加后端负载。
  - 激进窗口降低负载但可能带来短时误判。
- **优化：**
  - 对高峰时段动态缩短窗口。
  - 对热点模型单独配置缓存策略。

**Related：B6**

### Q67（策略选择）fail_fast 与 degrade 两种策略的适用场景是什么？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：`fail_fast` 保护语义一致性，`degrade` 保护可用性连续性。
  - 这是“严格性 vs 可用性”的显式业务决策。
- **实现：**
  - `fail_fast`：Redis 不可用直接报错，不进入本地限流。
  - `degrade`：自动切 InMemory，继续提供路由并保留观测。
- **边界：**
  - `fail_fast` 在基础设施故障时会扩大业务失败面。
  - `degrade` 在多实例下可能突破全局预算约束。
- **取舍：**
  - 成本/配额强约束业务适合 fail_fast。
  - 高可用实时交互业务更适合 degrade。
- **优化：**
  - 支持按业务线或环境（prod/stage）差异化配置。
  - 增加自动切换审计与事后复盘模板。

**Related：B6**

### Q68（一致性分层）哪些写入必须强一致，哪些可以 best-effort？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：影响状态机正确性的写入必须强一致；观测与诊断类写入可 best-effort。
  - 分层目标是“主链路先活，再完善可观测”。
- **实现：**
  - 强一致（router_engine SQLite，影响状态机正确性）：
  - `feedback_events` 幂等去重（`(request_id, model_id, event_type)` 唯一键，决定是否触发后续统计更新）。
  - `class_model_stats` 原子更新（success/fail/exec_fail、bonus/penalty），决定健康修正与学习闭环。
  - `class_pool` / `class_pool_defaults`（入池/默认切换/用户锁定），决定候选编排与默认起点。
  - `downgrade_trials`（试验状态、样本计数、回滚/冷却期），决定 canary 与回滚。
  - best-effort（monitoring sidecar，影响排障但不应阻塞主链路）：
  - `monitoring.record_decision*`/`record_execution_*` 一律 try/except 吞掉异常（写入失败不影响路由），并允许 alert_callback 失败。
- **边界：**
  - 若把监控写入放到强一致主链路，会放大尾延迟与故障传播。
  - 若把关键状态写入降为 best-effort，会破坏学习闭环正确性。
- **取舍：**
  - 强一致提高正确性但吞吐受限。
  - best-effort 提升可用性但可能丢失诊断信息。
- **优化：**
  - 用异步事件管道承接观测写入。
  - 为强一致路径增加冲突重试与锁等待监控。

**Related：B7**

### Q69（事务治理）事务/锁如何避免长事务引发重试风暴？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：高并发下应把事务限制在最小关键区，避免锁持有时间扩散。
  - 重试风暴本质是失败放大链路。
- **实现：**
  - 本项目的原则是“热路径用单条 SQL 原子更新，冷路径才用显式事务”：
  - 热路径：`atomic_increment_success/fail/exec_fail` 用单条 `UPDATE ... RETURNING` 完成计数与派生字段更新，避免读改写长事务。
  - 冷路径：`try_add_to_pool` 才使用 `BEGIN IMMEDIATE` 把“池大小检查 + 淘汰 + 插入”串成一个短关键区，且严格不做网络调用。
  - SQLite 侧配合 WAL + `busy_timeout` 限制锁等待扩散；调用侧依赖幂等事件表把重试变成 no-op，避免“失败重试导致二次写放大”。
- **边界：**
  - 锁等待过长会把失败转化为高延迟。
  - 无上限重试会形成放大回路。
- **取舍：**
  - 严格事务提高一致性但吞吐更低。
  - 放松事务吞吐更高但竞态风险上升。
- **优化：**
  - 对热点写入做批处理或异步化。
  - 增加锁冲突率与事务耗时告警。

**Related：B7**

### Q70（幂等键）事件幂等键如何设计以防重复计数和统计污染？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：幂等键要唯一标识“同一语义事件”，重复写入必须变成 no-op。
  - 这是重试安全与统计可信的基础。
- **实现：**
  - 使用 `(request_id, model_id, event_type)` 做唯一键，写入事件表采用 `INSERT OR IGNORE`。
  - 仅新事件才触发后续统计更新与状态机动作。
- **边界：**
  - `request_id` 不稳定会导致去重失效。
  - 键设计过粗会误去重，过细会重复计数。
- **取舍：**
  - 简洁键实现容易且可解释。
  - 复杂复合键覆盖更细语义但维护成本高。
- **优化：**
  - 监控幂等冲突率识别上游重试风暴。
  - 周期性对账事件表与聚合统计。

**Related：B7/B3**

### Q71（观测排障）落盘观测如何快速定位候选为空/限流触发/provider抖动？

**详细回答（定义→实现→边界→取舍→优化）：**
- **定义：**
  - 结论一句话：观测要回答“当时为什么这样选、为什么失败、哪里瓶颈”，而不是只记录结果。
  - 快速定位依赖结构化字段而非文本日志。
- **实现：**
  - 本项目的可落盘观测分两块：
  - monitoring sidecar（`monitoring_decisions`/`monitoring_executions`）：
  - 决策表记录 `model_used`、`provider`、`routing_reason`、`pool_hit/pool_class`、`analysis_domain/complexity`，以及 `metadata_json`（含 `alerts/start_index/default_used/class_source/rate_limiter_mode/rate_limiter_fail_strategy`）。
  - 执行表记录 `execution_id/request_id/model_used/provider/status/duration_ms/error_message`，可用 `watch` 命令快速看“某 agent 最近用哪个模型、成功/失败分布”。
  - router_engine 主库（`RouterStorage`）：
  - `model_availability` 可定位某模型是否因执行失败进入 `degraded/unable`，以及 probe 恢复情况；`feedback_events` 可核对某请求是否重复上报导致去重命中。
  - 三类常见定位路径：
  - 候选为空：看 `monitoring_decisions.routing_reason` 是否为 `no candidate models available after filters`，并结合 `alerts`、`registry_error_count/skipped_provider_count` 判断是注册表异常还是过滤条件过严；再下钻到 router_db 的 `model_availability` 看是否大面积 `unable`。
  - 限流触发：决策层面常表现为 `routing_reason` 出现 `default overloaded...`（default 被 utilization skip），并结合 `rate_limiter_mode/rate_limiter_fail_strategy` 判断限流器工作模式；若要定位是否发生 Redis→InMemory 降级，可进一步查看路由返回的 `rate_limiter_status.switched_at/last_error`（或把它们追加落盘到 monitoring metadata）。
  - provider 抖动：在 `monitoring_executions` 里按 `provider+status+error_message` 聚合，快速识别某 provider 的失败突增；同时观察 `model_availability` 的 degraded/unable 转换是否集中在同一 provider。
- **边界：**
  - 只记成功不记失败会让根因分析失真。
  - 字段无统一口径会导致跨模块无法对齐。
- **取舍：**
  - 全量采集信息最全但写放大明显。
  - 采样采集成本低但细粒度排障能力下降。
- **优化：**
  - 当前决策事件里没有“每个候选被过滤的具体原因/利用率快照”，限流排障有时只能靠 `routing_reason` 侧推；可把每次过滤原因与关键 utilization 指标追加到 `metadata_json`（并对正常路径采样）。
  - 对异常路径全量、正常路径采样，配合固定排障 SQL/视图（候选为空率、default skip 率、降级切换次数、provider 失败尖峰）。

**Related：B7**

---

## Part C：按简历要点映射的追问链（定义→实现→边界→取舍→优化）

> 用法：面试官围绕某条 Bx 提问时，你按 5 段结构回答；每段优先引用本题库中对应的 Q 编号。

### B1 追问链（打分与排序）

- **定义**：什么是能力匹配/成本分/健康度/拥塞惩罚？（Q21/Q11/Q8）
- **实现**：如何归一化成本？如何加权能力维度？缺失能力怎么处理？（Q22/Q11/Q10）
- **边界**：维度变更、能力缺失、价格单位不一致、候选近似分数如何 tie-break？（Q12/Q11/Q21）
- **取舍**：为什么能力主导、成本用于打平？为什么不用纯成本/纯能力？（Q34/Q21）
- **优化**：如何从启发式升级到 bandit/学习排序？（Q35）
- **深挖**：融合公式、近似阈值、缺失信号稳健性与惩罚去重如何落地？（Q36/Q38/Q39/Q41）

### B2 追问链（候选集/探索/多样性）

- **定义**：什么是保底强模型/池优先/探索补位/多样性约束？（Q15/Q14）
- **实现**：探索槽位如何自适应？provider 占比怎么限制？（Q15/Q14/Q25）
- **边界**：池很小/候选很少/单 provider 可用时怎么退化？（Q15/Q14）
- **取舍**：为什么在候选阶段做多样性，而不是全局负载均衡？（Q24）
- **优化**：动态多样性（随拥塞变化）、更精细的探索策略（contextual bandit）。（Q24/Q35）
- **深挖**：候选编排顺序、探索信号、provider 上限退化与路径依赖治理。（Q42/Q43/Q44/Q46/Q47）

### B3 追问链（在线学习闭环）

- **定义**：什么是模型池、默认模型、置信下界、连续失败惩罚？（Q26/Q16）
- **实现**：入池/晋升门槛如何设？幂等怎么保证统计不重复？（Q26/Q4）
- **边界**：小样本、反馈噪声、延迟反馈、数据缺失怎么处理？（Q26/Q20）
- **取舍**：为什么不用简单 success rate/EWMA？（Q26）
- **优化**：更丰富 reward（业务指标）、离线回放、影子实验。（Q35/Q20）
- **深挖**：阈值协同、防抖、冷启动与延迟反馈下的闭环稳定性。（Q48/Q49/Q51/Q52/Q53）

### B4 追问链（降级试验 canary）

- **定义**：什么是降级试验/挑战者/推广/回滚/冷却期？（Q18/Q27）
- **实现**：canary 采样怎么做？回滚阈值怎么定？（Q27/Q28）
- **边界**：挑战者不在候选里、节省不达标、样本不足、抖动怎么处理？（Q27/Q18）
- **取舍**：为什么不直接切默认？为什么区分 exec/quality 失败？（Q18/Q28）
- **优化**：更稳健的统计检验、更细粒度按域/任务类型分桶试验。（Q27/Q35）
- **深挖**：canary 参数联动、执行失败敏感回滚与升降级冲突裁决。（Q59/Q60/Q61）

### B5 追问链（升阶与重试）

- **定义**：什么是升阶、突破升阶（breakthrough）、重试策略、优先级？（Q17/Q29）
- **实现**：过载检查怎么做？替代候选如何找？（Q29/Q8）
- **边界**：候选为空/全部封顶/429 风暴/执行失败频发如何处理？（Q17/Q30）
- **取舍**：拥塞时抑制升阶会不会降低成功率？怎么权衡？（Q29/Q21）
- **优化**：引入队列等待/退避、分级 SLO、按业务优先级更精细控制。（Q29/Q30）
- **深挖**：retry/escalate 分界、过载前置检查、优先级语义与升阶并发封顶。（Q54/Q56/Q57/Q58）

### B6 追问链（高并发限流）

- **定义**：RPM/RPD/并发/升阶并发封顶/利用率是什么？（Q05/Q08）
- **实现**：ZSET 滑动窗口如何做？并发计数如何做？TTL 为什么要？（Q06/Q07）
- **边界**：崩溃导致并发泄露、下溢、网络重试双计数、Redis 故障。（Q07/Q30）
- **取舍**：为什么要 Auto 降级到内存？为什么不用 token bucket？（Q30/Q06）
- **优化**：key 分片、批量上报、近似一致、从 SQLite sidecar 演进到分布式观测。（Q33/Q32）
- **深挖**：多维限流优先级、滑窗误差边界、分桶并发与降级风控策略。（Q62/Q63/Q64/Q65/Q67）
- **量化追问（主答）**：先报 Q20 的 A/B 表格：实验组 `elapsed_seconds=20.625`、`success_throughput_rps=0.97`、`rejection_rate=0.0`、`completion_all_p95=14684.15ms`、`allocatable/eligible/assigned=12/12/12`；对照组 `elapsed_seconds=66.11`、`success_throughput_rps=0.303`、`rejection_rate=0.929`、`completion_all_p95=61437.85ms`、`allocatable/eligible/assigned=12/1/1`；再补窗口口径 `window_start_ts=0.0`、`window_end_ts=60.0`、超时边界常量（`max_wall_time_seconds=120`、`max_attempts_per_request=200`、`max_queue_wait_per_request_ms=60000`、`max_inflight_retrying=8`），并按“有效吞吐 -> 拒绝/超时 -> completion(all)”顺序解释结论，避免只看总时长/总吞吐。

### B7 追问链（并发安全与落盘/观测）

- **定义**：为什么要落盘？哪些数据必须可审计？（Q19/Q13）
- **实现**：WAL/busy_timeout/事务怎么保障并发一致性？best-effort 如何不影响主链路？（Q02/Q03/Q19）
- **边界**：写热点、锁冲突、数据过期、快照刷新失败。（Q33/Q13/Q31）
- **取舍**：为什么用 SQLite 侧车而不是 MQ/OTel？（Q32）
- **优化**：异步事件管道、离线回放、指标化验收（成本/成功率/延迟）。（Q20/Q32/Q35）
- **深挖**：一致性分层、事务治理、幂等键设计与排障观测字段体系。（Q68/Q69/Q70/Q71）
- **量化追问**：你怎么证明”可回归验证”？直接引用 Q20 的全量结果 `84/84 passed in 22.54s`，并说明其与性能压测指标共同构成”稳定性 + 性能”双证据。

---

## Part D：并发测试专项说明

> 本节详细说明高并发性能测试的完整流程、测试了哪些指标、如何测试以及各指标的意义。
> 核心文件：`route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py`
> A/B 对比脚本：`scripts/perf_ab_compare.py`

---

### D1. 并发测试的整体流程

#### 1.1 测试架构：A/B 对比实验

测试分为**实验组（experiment）**与**对照组（control）**，二者使用完全相同的工作负载，仅路由策略不同：

| 维度 | 实验组（重叠批次） | 对照组（固定单模型） |
|---|---|---|
| 路由策略 | 路由引擎动态分配（多模型分流） | 固定路由到 `google:gemini-2.5-pro` |
| 模型池 | 12 个合成模型（4 providers） | 仅 1 个模型可用 |
| 工作负载 | 5 批 × 4 agents = 20 请求 | 完全相同 |
| 重复次数 | 3 次，取中位数（median） | 3 次，取中位数 |

每轮重复运行后，分别对各轮次的 `ScenarioMetrics` 字段取中位数，并附上 `stability` 区间（min/max），用于评估跨轮次稳定性。

#### 1.2 合成模型池（`_build_synthetic_models`）

测试使用 **12 个合成模型**，覆盖 4 个 provider，均带有精确的限流参数，避免对真实 API 产生依赖：

| Provider | 模型 ID | RPM 上限 | 并发上限 |
|---|---|---|---|
| openai | gpt-4.1-mini / gpt-4.1 / gpt-5.2-codex | 8–14 | 2–3 |
| google | gemini-2.5-flash / gemini-2.5-pro / gemini-1.5-flash | 9–15 | 2–3 |
| deepseek | deepseek-chat / deepseek-v3 / deepseek-reasoner | 10–16 | 3–4 |
| anthropic | claude-3-5-haiku / claude-3-7-sonnet / claude-opus-4-1 | 7–13 | 2–3 |

每个模型的能力（`instruction_following`/`text`/`reasoning`/`code`/`math`）和定价均通过公式计算，体现模型等级差异，使路由引擎的打分与排序逻辑能真实触发。

#### 1.3 合成场景（`build_agent_scenarios`）

20 个 agent 场景通过固定种子（`SEED=20260224`）生成，保证跨运行可复现：

- **角色（role_name）**：20 种，如 `report_writer`、`risk_analyst`、`code_reviewer` 等，覆盖多个业务领域
- **复杂度层次（complexity_tier）**：
  - `simple`（8个）：2 个维度，评分 2–4，执行时长 0.4–1.2s
  - `medium`（8个）：3 个维度，评分 5–7，执行时长 2.0–4.0s
  - `complex`（4个）：4 个维度，评分 8–10，执行时长 12.0–16.0s
- **任务分析（TaskAnalysisResult）**：每个场景带有 domain（reasoning/coding/extraction 等）与维度评分，直接输入路由引擎打分器
- **路由约束（RouteConstraints）**：包含 `preferred_model`、`require_provider`、`estimated_input_tokens`，触发完整的候选筛选与打分逻辑

#### 1.4 批次调度与重叠并发（`_run_overlapping_batch_simulation`）

这是测试”重叠并发”效果的核心机制：

```
批次 0 → t=0.0s      提交 agents 0–3
批次 1 → t≈1.2–2.0s  提交 agents 4–7  （批次 0 的 complex agents 仍在执行）
批次 2 → t≈2.4–4.0s  提交 agents 8–11 （与批次 0、1 重叠）
批次 3 → t≈3.6–6.0s  提交 agents 12–15
批次 4 → t≈4.8–8.0s  提交 agents 16–19
```

- **批次间隔**：随机采样 `[1.2s, 2.0s]`（`INTERVAL_MIN=3.0×0.4`，`INTERVAL_MAX=5.0×0.4`），固定种子确保可复现
- **并发实现**：`asyncio.create_task` 异步提交，`await asyncio.gather(*tasks)` 并发等待全部完成
- **Rate limiter 模式**：`inmemory`，不依赖 Redis，测试在单进程内完全自洽
- **完整生命周期**：每个 agent 经过 `RouterEngine.route_async` → 限流检查 → `record_request_start_async` → 模拟执行（`asyncio.sleep`） → `record_request_end_async`

#### 1.5 对照组的排队重试机制（`_run_one_control_request`）

对照组固定路由到单一模型，当该模型并发/RPM 超限时，请求进入**有界重试队列**：

- `MAX_ATTEMPTS_PER_REQUEST=200`：最多重试 200 次
- `MAX_QUEUE_WAIT_PER_REQUEST_MS=60,000ms`：等待超 60s 视为超时
- `MAX_WALL_TIME_SECONDS=120s`：总挂钟时间上限
- **指数退避 + 抖动**：`base=0.1s`，`cap=2.0s`，抖动 ±50%，request_id 确定性种子
- **重试节流信号量**：`MAX_INFLIGHT_RETRYING=8`，防止并发重试爆炸
- **拒绝来源分类**：通过 `util.conc_ratio`/`rpm_ratio` 区分 `concurrency`/`rate_limit`/`no_model` 三类

---

### D2. 测试了哪些指标以及如何测试

#### 2.1 时间与吞吐类

| 指标 | 采集方式 | 计算方式 |
|---|---|---|
| `elapsed_seconds` | `time.monotonic()` 壁钟差（base_time 到 gather 完成） | 总挂钟时间 |
| `throughput_rps` | `total_agents / elapsed_seconds` | 20个请求 ÷ 总耗时（含失败） |
| `success_throughput_rps` | `success_count / elapsed_seconds` | 成功数 ÷ 总耗时 |
| `success_throughput_rps_window` | 统计 `[t0, t0+60s]` 窗口内成功完成数 ÷ 60 | 固定60秒标准窗口 |
| `peak_10s_success_rps` | 对所有成功 `completion_offset` 做滑动窗口 | 任意10秒内成功完成数 ÷ 10 |

#### 2.2 成功/拒绝/超时率类

| 指标 | 计算方式 | 采集依据 |
|---|---|---|
| `success_rate` | `success_count / total_requests` | `execution_status == “success”` |
| `rejection_rate` | `total_rejections / total_attempts` | 所有重试轮次中被拒绝的次数之和 |
| `timeout_rate` | `timeout_count / total_requests` | `execution_status == “timeout”` |
| `rate_limit_rejection_rate` | `rate_limit_rejections / total_attempts` | `util.rpm_ratio >= 1.0` 的轮次 |
| `concurrency_rejection_rate` | `concurrency_rejections / total_attempts` | `util.conc_ratio >= 1.0` 且 `>= rpm_ratio` |
| `no_model_rejection_rate` | `no_model_rejections / total_attempts` | 路由返回空 / 模型池无记录 |

拒绝来源分类核心逻辑（`_rejection_source_from_utilization`）：
```
conc_ratio >= 1.0 且 >= rpm_ratio → “concurrency”
rpm_ratio >= 1.0                  → “rate_limit”
其余                               → “no_model”
```

#### 2.3 重试与节流类

| 指标 | 计算方式 | 意义 |
|---|---|---|
| `avg_attempts_per_success` | 成功 rows 的 `attempts` 字段均值 | 平均每次成功需要尝试几次 |
| `p95_attempts_per_success` | 同上，P95 百分位 | 尾部重试压力 |
| `retry_throttle_events` | 退避时 Semaphore 等待 > 0ms 记 1 次，全 rows 求和 | 重试并发是否触及 MAX_INFLIGHT_RETRYING 上限 |
| `retry_throttle_wait_ms` | 每次 `Semaphore.acquire()` 的实际等待 ms，全 rows 求和 | 重试节流对总时延的贡献 |

#### 2.4 延迟分解类

每个请求生命周期拆分为三段独立采集：

```
submit_ts ──[routing_overhead]──→ execution_start ──[service_latency]──→ execution_end
           │                                                               │
           └─────────────── queue_wait (含所有重试等待) ───────────────────┘
           └───────────────────── completion_latency (端到端) ────────────┘
```

| 指标 | 测量区间 |
|---|---|
| `routing_overhead_ms` (p50/p95) | 每次 `route_async` 调用的耗时，取所有尝试的分布 |
| `queue_wait_ms` (p50/p95/max) | 最终执行开始时刻 - 首次提交时刻（含所有重试等待） |
| `service_latency_ms` (p50/p95/p99) | 最后一次执行的实际占用时间（`asyncio.sleep` 时长） |
| `completion_latency_ms_success` (p50/p95/p99) | 端到端，仅统计成功请求 |
| `completion_latency_ms_all` (p50/p95/p99) | 端到端，包含 timeout（用截断值填充） |

#### 2.5 限流合规类（实验组核心验证）

| 指标 | 采集方式 | 验证断言 |
|---|---|---|
| `observed_rpm_peak_60s` | 对每个模型的 `execution_start_offset` 列表做 60s 滑动窗口峰值 | `assert observed_rpm <= rpm_limit` |
| `observed_concurrency_peak` | 对 `(start, end)` 区间列表扫描事件流，求最大重叠计数 | `assert observed_conc <= conc_limit` |

这两项指标是**限流器正确性的直接证据**：测试通过即证明路由引擎在高并发重叠批次下，每个模型的实际使用始终不超出其 RPM 与并发上限。

#### 2.6 模型分配类

| 指标 | 来源 | 说明 |
|---|---|---|
| `allocatable_models_count` | `availability == “available”` 的模型数 | 理论上可分配的模型总数 |
| `eligible_models_count` | 实验组=allocatable；对照组=1 | 实际参与路由竞争的模型数 |
| `assigned_models_count` | `len({row.model_id for row in rows})` | 实际被分配到请求的不同模型数 |

#### 2.7 时间窗口类

| 指标 | 窗口锚点 | 意义 |
|---|---|---|
| `window_start_ts` | `min(submit_offsets)` = 0.0 | 以最早提交时刻为基准 |
| `window_end_ts` | `window_start_ts + 60.0` | 固定60秒 |
| `successes_in_window` | `window_start <= completion_offset < window_end` 的成功数 | 标准时间窗口内的有效产出 |
| `success_throughput_rps_window` | `successes_in_window / 60.0` | 可跨组直接对比的吞吐 |
| `p95_completion_latency_window` | 窗口内所有请求（含未完成）的 P95 | 更保守的尾延迟估计 |

---

### D3. 各指标的意义与解读

#### 3.1 核心结论（面试量化首答）

| 指标 | 实验组 | 对照组 | 差异意义 |
|---|---|---|---|
| `elapsed_seconds` | 20.625s | 66.11s | 对照组 3.2× 耗时，大量时间消耗在排队 |
| `success_throughput_rps` | 0.97 | 0.303 | 实验组有效吞吐是对照组的 **3.2×** |
| `success_rate` | 1.0 | 1.0 | 两组最终都成功（对照组靠排队重试） |
| `rejection_rate` | 0.0 | 0.929 | 对照组 92.9% 的尝试被拒绝后重试 |
| `completion_all_p95` | 14,684ms | 61,438ms | 对照组尾延迟是实验组的 **4.2×** |
| `allocatable/eligible/assigned` | 12/12/12 | 12/1/1 | 实验组充分利用全部容量 |

> **关键洞察**：`success_rate=1.0` 相同会产生误解。对照组靠大量重试最终都成功，但代价是 3× 的总耗时、4× 的尾延迟、93% 的无效尝试。评估并发能力必须同时看 `success_throughput_rps`、`rejection_rate` 和 `completion_latency_ms_all`，不能只看 `success_rate`。

#### 3.2 延迟三段分解的意义

1. **`routing_overhead_ms`** — 路由引擎自身成本
   - 应保持毫秒级。P95 显著升高说明高并发下有锁争用或打分计算瓶颈
   - 对比两组此值可验证路由决策逻辑的时间复杂度是否可接受

2. **`queue_wait_ms`** — 因限流等待重试的累计时间
   - 区分”系统忙（高 service_latency）”与”系统拥堵（高 queue_wait）”的关键
   - 对照组 `queue_wait` 远高于实验组，直接量化”单模型热点”的排队代价

3. **`service_latency_ms`** — 模型实际执行延迟
   - 等价于真实场景的 LLM API 响应时间
   - 若路由按复杂度正确匹配模型，此值分布应与 complexity_tier 期望范围对齐

#### 3.3 吞吐指标的层次关系

```
throughput_rps（含失败，反映系统调度强度）
    └─ success_throughput_rps（仅成功，反映有效产出）
           └─ success_throughput_rps_window（60s标准窗口，消除总时长差异）
                  └─ peak_10s_success_rps（10s峰值，反映系统瞬时上限）
```

`throughput_rps` 高但 `success_throughput_rps` 低说明存在大量无效尝试（拒绝/超时），系统在”空转”而非有效产出。

#### 3.4 拒绝率三类来源的策略含义

| 类型 | 触发条件 | 策略含义 |
|---|---|---|
| `concurrency_rejection_rate` | 并发槽位耗尽（`conc_ratio >= 1.0`） | 需分流到并发容量更充裕的模型 |
| `rate_limit_rejection_rate` | 分钟请求数超限（`rpm_ratio >= 1.0`） | 需降低发送频率或换 provider |
| `no_model_rejection_rate` | 路由无候选/模型池无记录 | 候选过滤条件过严或注册表故障，与限流无关 |

对照组 `rejection_rate=0.929` 几乎全来自 `concurrency`/`rate_limit`（单模型快速触发上限）；实验组 `rejection_rate=0.0` 证明多模型分流将负载分散，12个模型各自不触限。

#### 3.5 模型分配三层含义

```
allocatable（注册表中可用）
    ├─ eligible（参与本次路由竞争）
    └─ assigned（实际承载过至少1个请求）
```

- `allocatable > eligible`：路由策略主动限制候选范围（对照组强制 `pin_model`）
- `eligible > assigned`：部分候选因打分/限流被淘汰，未真正承载流量
- 三者相等（实验组 12/12/12）：路由充分利用全部可用资源，无”僵尸模型”

#### 3.6 窗口指标的对齐价值

固定锚点（`window_start=0.0`，`window_end=60.0`）消除实验组（20s完成）与对照组（66s完成）总时长差异，使 `success_throughput_rps_window` 可直接横向对比。`p95_completion_latency_window` 包含超时请求，提供更保守（更接近真实）的尾延迟估计。

#### 3.7 限流合规验证的工程价值

`observed_rpm_peak_60s` 与 `observed_concurrency_peak` 的断言不是性能指标，而是**正确性指标**：

- 它们证明 InMemoryRateLimiter 在真实并发调度下没有竞态导致超限
- 结合 `rejection_rate=0.0`，共同证明”路由引擎分流 + 限流器执行”形成了闭环：请求被正确分散，每个模型的实际负载不超出其声明上限

---

### D4. 测试设计关键取舍

| 取舍 | 选择 | 原因 |
|---|---|---|
| Rate limiter 模式 | `inmemory`（不用 Redis） | 测试自洽，不依赖外部基础设施；InMemory 与 Redis 接口一致，逻辑完全复用 |
| 执行时长模拟 | `asyncio.sleep`（不真实调用 LLM） | 消除网络抖动干扰，精确控制并发重叠形态，使限流行为完全可观测 |
| 重复次数 | 3 次取中位数 | 对抗 asyncio/GC 调度抖动；中位数比均值对异常值更稳健 |
| `completion_latency_ms_all` | 包含 timeout（截断值填充） | 不过滤失败请求，反映最差情况下的用户侧感知，避免”幸存者偏差” |
| 窗口锚点 | `min(submit_ts)` 固定 = 0.0 | 消除两组总时长差异，保证 `success_throughput_rps_window` 可直接对比 |




