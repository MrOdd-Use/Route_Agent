# Route Agent 竞品对比

## 市场定位

Route Agent 的核心价值在于根据任务特点自动完成模型分配。面对高复杂度的代码类任务，选择能力更强的大模型；面对总结、分类这类轻量任务，优先选择高性价比的小模型；在专业场景下，还能将任务路由到相应的垂类模型——在保证效果的同时尽可能降低整体调用成本。

LLM 路由产品覆盖三个层级。Route Agent 定位于模型选择层——根据任务特征智能选择最优模型，而非转发流量或调度 GPU。

```
用户请求
  │
  ▼
┌─────────────────────────────┐
│  模型选择层（选哪个模型）      │  Route Agent / Martian / Not Diamond
│  分析 prompt → 选最优模型     │  RouteLLM / Unify / OpenRouter
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│  API 网关层（请求怎么到达）    │  Higress / 阿里云 AI Gateway / LiteLLM
│  认证、限流、缓存、协议转换    │
└──────────────┬──────────────┘
               ▼
┌─────────────────────────────┐
│  推理调度层（发给哪个 GPU）    │  PAI LLM 智能路由 / ACK Gateway
│  KV Cache 亲和、PD 分离       │
└─────────────────────────────┘
```

三层可组合部署：Route Agent 选完模型后，Higress 转发请求，PAI 调度到具体 GPU 实例。

## 产品概览

| 项目 | 类型 | 路由层级 | 开源 | 定位 |
|------|------|---------|------|------|
| **Route Agent** | 本地 CLI/API | 模型选择层 | 是 | 自托管智能路由控制面 |
| **Martian** | SaaS API | 模型选择层 | 否 | 企业级路由即服务 |
| **Not Diamond** | SaaS + SDK | 模型选择层 | 部分 | 路由优化层 |
| **OpenRouter** | SaaS API | 模型选择 + 网关 | 否 | 统一多模型网关 |
| **Unify AI** | SaaS API | 模型选择层 | 否 | 性能导向路由评估平台 |
| **LiteLLM** | 开源代理 | 网关层 | 是 | LLM API 网关代理 |
| **RouteLLM** | 开源框架 | 模型选择层 | 是 | 学术级强弱模型路由 |
| **Higress** | 开源网关 | 网关层 | 是 | AI Native API Gateway |
| **阿里云 AI Gateway** | 云服务 | 网关层 | 否 | 企业级 AI API 网关 |
| **PAI LLM 智能路由** | 云服务 | 推理调度层 | 否 | GPU 推理调度优化 |

## 详细对比

### 1. 路由智能程度

| 能力 | Route Agent | Martian | Not Diamond | OpenRouter | Unify | LiteLLM | RouteLLM | Higress | 阿里云 AI GW | PAI LLM 路由 |
|------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 基于 prompt 内容选模型 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ | ❌ |
| 任务类型分类 | ✅ 8类 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 多维能力匹配 | ✅ 10+维度 | ✅ | ✅ | 有限 | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 成本约束感知 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ RPM/TPM | ❌ | ❌ | ❌ | ❌ |
| 零 LLM 调用快速路径 | ✅ 向量匹配 | ❌ | ❌ | ❌ | ❌ | N/A | ✅ 本地分类器 | N/A | N/A | N/A |
| GPU 负载感知调度 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ 6种策略 |

### 2. 自适应学习

| 能力 | Route Agent | Martian | Not Diamond | OpenRouter | LiteLLM | RouteLLM | Higress | 阿里云 AI GW | PAI LLM 路由 |
|------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 执行反馈学习 | ✅ Class Pool | 云端 | 云端 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 自动降级试验 | ✅ Canary | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 自动升级恢复 | ✅ 状态机 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 质量驱动池化 | ✅ Wilson 置信度 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 动态 PD 分离 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

### 3. 可靠性与容错

| 能力 | Route Agent | Martian | OpenRouter | LiteLLM | Higress | 阿里云 AI GW | PAI LLM 路由 |
|------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| 健康状态追踪 | ✅ 三态 | ✅ | ✅ | ✅ 冷却池 | ✅ | ✅ | ✅ |
| 自动 Fallback | ✅ 升级链 | ✅ | ✅ Provider级 | ✅ | ✅ 权重切换 | ✅ | ✅ 降级轮询 |
| 分布式限流 | ✅ Redis | N/A | ✅ | ✅ | ✅ Token级 | ✅ 多维度 | ❌ |
| 金丝雀发布 | ✅ 降级试验 | ❌ | ❌ | ❌ | ✅ 权重分配 | ✅ 模型灰度 | ❌ |
| 请求缓冲/过载保护 | ❌ | N/A | N/A | ❌ | ❌ | ✅ | ✅ 排队缓冲 |

### 4. 安全能力

| 能力 | Route Agent | LiteLLM | Higress | 阿里云 AI GW |
|------|:-:|:-:|:-:|:-:|
| 身份认证 | ❌ | ✅ SSO/JWT | ✅ 5种认证 | ✅ API Key/JWT/HMAC |
| 内容安全审查 | ❌ | ✅ Guardrails | ✅ WAF插件 | ✅ 五维防护 |
| Prompt 注入检测 | ❌ | ❌ | ❌ | ✅ |
| 敏感数据检测 | ❌ | ❌ | ❌ | ✅ |
| 数字水印 | ❌ | ❌ | ❌ | ✅ |
| 密钥托管 | ❌ | ✅ Vault | ❌ | ✅ KMS |

### 5. 生态与协议

| 能力 | Route Agent | LiteLLM | Higress | 阿里云 AI GW | PAI LLM 路由 |
|------|:-:|:-:|:-:|:-:|:-:|
| 模型 Provider 数 | 6 | 100+ | 国内外主流 | OpenAI/Anthropic/Bedrock 等 | vLLM/SGLang |
| MCP 支持 | ❌ | ❌ | ✅ 托管+转换 | ✅ OpenAPI→MCP | ❌ |
| A2A 协议 | ❌ | ❌ | ❌ | ✅ REST→A2A | ❌ |
| OpenAI 兼容 | ❌ | ✅ | ✅ 协议转换 | ✅ 协议转换 | ✅ |
| K8s 原生 | ❌ | ✅ Helm | ✅ Ingress Controller | ✅ | ✅ EAS |
| Wasm 插件扩展 | ❌ | ❌ | ✅ 多语言 | ✅ | ❌ |

### 6. 可观测性

| 能力 | Route Agent | LiteLLM | Higress | 阿里云 AI GW | PAI LLM 路由 |
|------|:-:|:-:|:-:|:-:|:-:|
| 路由决策记录 | ✅ | ✅ | ❌ | ❌ | ❌ |
| Token 用量追踪 | ✅ | ✅ | ✅ | ✅ TPS | ✅ 吞吐量 |
| 质量反馈闭环 | ✅ good/fair/poor | ❌ | ❌ | ❌ | ❌ |
| GPU 指标监控 | ❌ | ❌ | ❌ | ❌ | ✅ 缓存/并发/延迟 |
| 实时 Dashboard | ✅ 终端 | ✅ Web | ✅ 控制台 | ✅ 多维图表 | ✅ TTFT/TPOT |

### 7. 部署模式

| 维度 | Route Agent | LiteLLM | RouteLLM | Higress | 阿里云 AI GW | PAI LLM 路由 |
|------|:-:|:-:|:-:|:-:|:-:|:-:|
| 自托管 | ✅ | ✅ | ✅ | ✅ | ❌ 云服务 | ❌ 云服务 |
| 数据不出域 | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| Docker 一键部署 | ❌ | ✅ | ❌ | ✅ | N/A | N/A |
| 托管高可用 | ❌ | ❌ | ❌ | ❌ | ✅ 99.99% SLA | ✅ |
| 零依赖启动 | ✅ SQLite | ❌ | ✅ | ❌ | N/A | N/A |

## 各产品核心优势

| 产品 | 核心优势 |
|------|---------|
| **Route Agent** | 三级零失败分析 + Class Pool 自学习闭环 + 降级/升级状态机 + 完全自托管数据不出域 |
| **Martian** | 机械可解释性路由精度业界领先；300+ 企业验证；合规审批功能 |
| **Not Diamond** | Prompt Adaptation 自动优化 prompt；准确率提升 39%；可叠加在现有网关上 |
| **OpenRouter** | 生态最大模型最全；统一 API 最简；免费模型路由；社区活跃 |
| **Unify AI** | 全局 MoE 路由 + 内置基准评测；质量/成本/延迟三维可视化调优 |
| **LiteLLM** | 100+ API 统一；企业功能最全（SSO/审计/预算）；负载均衡策略丰富 |
| **RouteLLM** | 学术背景严谨（UC Berkeley）；成本削减高达 85%；路由模型可迁移 |
| **Higress** | Wasm 插件热更新不断连；MCP 托管；毫秒配置生效；Docker 一键部署 |
| **阿里云 AI Gateway** | 五维安全防护最全面；多维限流；MCP/A2A 协议支持；99.99% SLA |
| **PAI LLM 智能路由** | 6种 GPU 调度策略；Prefill/Decode 动态分离；KV Cache 亲和调度 |

## Route Agent 差异化总结

### 独有能力

1. **自学习闭环** — Class Pool + 降级试验 + 升级状态机，唯一在本地实现完整反馈驱动学习循环的开源项目
2. **三级零失败分析** — 向量匹配→LLM→关键词逐级降级，确保任何情况下都能完成路由决策
3. **Multi-Agent 隔离** — 按 agent × 任务类型独立学习，代码审查 agent 和文档翻译 agent 各自维护模型池
4. **完全自托管** — 数据不出域，SQLite 零依赖启动，适合数据敏感场景

### 待补强方向

1. **生产就绪度** — 当前 CLI-first，REST API 部分实现，缺少 Docker 部署和高可用方案
2. **路由精度验证** — 缺少与 Martian/Not Diamond 的标准化基准对比数据
3. **模型覆盖面** — 支持 6 个 Provider（LiteLLM 支持 100+）
4. **企业功能** — 缺少身份认证、SSO、审计日志、预算管理
5. **协议兼容** — 无 OpenAI 兼容接口、MCP 支持、协议转换能力

### 推荐组合部署

Route Agent 与网关层产品互补，推荐组合：

- **Route Agent + Higress**：Route Agent 选模型，Higress 做流量管理、安全、缓存（均开源自托管）
- **Route Agent + LiteLLM**：Route Agent 选模型，LiteLLM 统一 100+ Provider API + 负载均衡
- **Route Agent + 阿里云 AI Gateway**：适合需要企业级安全和 SLA 的场景

## 参考链接

- [Martian](https://withmartian.com) — 机械可解释性模型路由
- [Not Diamond](https://www.notdiamond.ai) — AI 模型路由优化层
- [OpenRouter](https://openrouter.ai) — 统一多模型网关
- [Unify AI](https://unify.ai) — 性能导向路由平台
- [LiteLLM](https://github.com/BerriAI/litellm) — 开源 LLM API 代理
- [RouteLLM](https://github.com/lm-sys/RouteLLM) — UC Berkeley 开源路由框架
- [Higress](https://github.com/alibaba/higress) — 阿里巴巴 AI Native API Gateway
- [阿里云 AI Gateway](https://www.alibabacloud.com/help/en/api-gateway/ai-gateway/product-overview/what-is-an-ai-gateway) — 企业级 AI API 网关
- [PAI LLM 智能路由](https://help.aliyun.com/zh/pai/user-guide/use-llm-intelligent-router-to-improve-inference-efficiency) — GPU 推理调度优化
- [Awesome AI Model Routing](https://github.com/Not-Diamond/awesome-ai-model-routing) — 模型路由项目汇总
