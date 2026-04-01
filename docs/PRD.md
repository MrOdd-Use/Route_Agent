# Route Agent - Product Requirements Document (PRD)

## 1. Project Overview

### 1.1 Project Name
**Route Agent** - Intelligent LLM Routing System

### 1.2 Background
In multi-agent systems, model selection is not only a capability-matching problem. It is also a learning problem under sparse feedback, changing model health, provider limits, and cost constraints.

A standalone router can optimize for one application, but it learns slowly when a new agent or a low-traffic task class has little local history. The key opportunity is to let applications share routing experience without giving up low-latency local autonomy.

Route Agent addresses this by turning model routing into a feedback-driven control plane. Its core differentiator is a federation-inspired learning loop: each application keeps local routing authority and local fast paths, while cross-application class-level outcomes are aggregated and fed back into future decisions. This improves cold-start behavior, stabilizes class-pool learning, and reduces cost while preserving quality.

### 1.3 Goals
Build a routing system that analyzes task features and assigns the most suitable LLM to achieve:
- **Local Autonomy with Shared Learning**: keep low-latency local routing while sharing class-level experience across applications.
- **Quality-Cost Balance**: route hard tasks to stronger models and lighter tasks to lower-cost models without static one-size-fits-all rules.
- **Feedback-Driven Improvement**: continuously refine class pools, defaults, and candidate ordering from execution outcomes.
- **Operational Resilience**: remain stable under provider failures, rate limits, and contention through health-aware routing and graceful degradation.

---

## 2. Core Functional Requirements

### 2.1 Model Registry

| Function | Description | Priority |
|---|---|---|
| Capability Definition | Define capability dimensions per model (reasoning, coding, math, context length, etc.) | P0 |
| Cost Configuration | Configure token pricing for each model | P0 |
| Local Persistent Pool | Store normalized model configs in SQLite for fast local reads | P0 |
| Periodic Refresh | Sync provider model configs on a fixed schedule (every 30 days) | P0 |
| Extensible Storage Backend | Support PostgreSQL and other centralized backends | P1 |
| Dynamic Availability | Detect model availability and support auto-fallback | P1 |

### 2.2 Task Analyzer

| Function | Description | Priority |
|---|---|---|
| Task Type Detection | Detect task types (`general`, `scrape`, `extraction`, `summarization`, `classification`, `rewrite`, `review`, `translation`) via a three-tier chain: vector profile -> LLM -> keyword fallback | P0 |
| Vector Profile Matching | Use local Ollama embeddings to match task input against class-pool descriptions via cosine similarity | P0 |
| LLM New-Class Determination | When vector match misses threshold, LLM judges whether the task belongs to an existing class or suggests a new class pool | P0 |
| Complexity Estimation | Estimate complexity (0-1) from length, context needs, and reasoning depth | P0 |
| Intent Analysis | Detect true user intent to avoid shallow misclassification | P1 |
| Class Pool Descriptions | Each task class has a detailed natural-language description used for embedding matching and LLM prompts | P0 |
| Dimension Profiles | Each task class has predefined capability-dimension scores for fast profile-based analysis | P0 |

### 2.3 Router Engine

| Function | Description | Priority |
|---|---|---|
| Constraint-Aware Filtering | Filter the global candidate base by availability, provider, budget, exclusions, context window, and rate limits | P0 |
| Class-Pool Learning | Maintain per-agent-class pools from seed models and feedback with Wilson-confidence gating | P0 |
| Default Model Management | Promote and revoke class defaults using conservative success estimates, streak checks, and cost-aware tie-breaks | P0 |
| Candidate Set Composition | Build ordered candidates with class-pool priority, one ceiling model, and controlled exploration slots | P0 |
| Escalation and Downgrade | Escalate along the candidate chain on failure and run canary downgrade trials for cost reduction | P0 |
| Manual Pool Management | Manually add/remove models to class pools via CLI and REST API, parallel to the automatic feedback-driven channel | P1 |

### 2.4 Monitoring and Feedback

| Function | Description | Priority |
|---|---|---|
| Decision Logging | Record each routing decision and rationale | P0 |
| Cost Tracking | Track token usage and cost by model | P0 |
| Effectiveness Evaluation | Collect user feedback for routing optimization | P1 |

### 2.5 Federation

| Function | Description | Priority |
|---|---|---|
| App and Agent Registry | Register applications and declared agents so known agents can resolve class mappings quickly | P0 |
| Known-Agent Fast Path | Let known agents bypass the three-tier analysis chain through local mappings and lightweight analysis | P0 |
| Outcome Aggregation | Aggregate class-level success/failure outcomes across applications without centralizing raw task content | P0 |
| Pool Version Sync | Signal cross-application score changes through pool-version updates and local score refresh | P0 |
| Blended Scoring | Merge local and federated confidence signals so low-sample classes benefit from fleet-wide experience | P0 |
| Concurrency Lease Control | Coordinate hot-model contention across applications and redirect traffic when lease thresholds are exceeded | P0 |

---

## 3. Technical Architecture

### 3.1 System Diagram

```text
+------------------------- Client Applications --------------------------+
|  App A                    App B                    App C               |
|  - local agent mapping    - local pool snapshot    - local router      |
|  - lightweight analysis   - federated scores       - outcome writer    |
+-------------------------------+----------------------------------------+
                                |
                                v
+--------------------------- Central Route Agent -------------------------+
| +---------------+  +----------------+  +-----------------------------+ |
| | Model Registry|->| Task Analyzer  |->| Router Engine               | |
| | local snapshot|  | three-tier     |  | class pools / defaults /    | |
| | + pool build  |  | analysis chain |  | candidate ordering          | |
| +---------------+  +----------------+  +-----------------------------+ |
|         |                    |                      |                  |
|         v                    v                      v                  |
| +----------------+  +----------------+  +---------------------------+ |
| | Monitoring     |  | Federation     |  | Rate Limiting / Health    | |
| | decisions/logs |  | app registry   |  | overload / degradation    | |
| | executions     |  | pool versions  |  | escalation guardrails     | |
| | stats/events   |  | outcomes       |  |                           | |
| |                |  | leases         |  |                           | |
| +----------------+  +----------------+  +---------------------------+ |
+------------------------------------------------------------------------+
```

### 3.2 Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Runtime | Python + asyncio | Core application runtime and async orchestration |
| API | FastAPI | REST API service |
| Configuration | Environment variables + Pydantic + YAML templates | Runtime configuration and planning templates |
| Storage | SQLite (default) / PostgreSQL (extension) | Registry, analysis, router, monitoring, and federation persistence |
| Logging | Python `logging` | Structured application logs |
| LLM Providers | OpenAI, DeepSeek, Google Gemini, Anthropic, Groq, Ollama | Multi-provider model access |
| Embedding | Ollama (`nomic-embed-text`, local) | Vector-profile task matching against class-pool descriptions |
| Coordination | Redis (optional) | Rate limiting and operational safeguards |

### 3.3 Data Flow

1. Applications register agents and receive current class-pool versions.
2. Known agents use local agent-to-class mappings and lightweight analysis to skip the three-tier analysis chain.
3. The local router builds an ordered candidate set from the local model snapshot, class-pool state, and runtime constraints.
4. A central lease check may keep the proposed model in local mode or redirect to another candidate under contention.
5. After execution, outcomes are written locally for immediate learning and reported centrally for cross-application aggregation.
6. When aggregated evidence changes significantly, the federation layer bumps the class pool version and clients pull fresh federation scores.

---

## 4. Example Configurations

### 4.1 Model Config (`config/models.yaml`)

```yaml
models:
  gpt4o:
    provider: openai
    name: gpt-4o
    capabilities:
      reasoning: 0.95
      coding: 0.90
      math: 0.85
      context_length: 128000
    cost_per_1k:
      input: 0.005
      output: 0.015

  deepseek_chat:
    provider: deepseek
    name: deepseek-chat
    capabilities:
      reasoning: 0.75
      coding: 0.80
      math: 0.70
      context_length: 16000
    cost_per_1k:
      input: 0.0001
      output: 0.0002
```

### 4.2 Routing Rules (`config/routing_rules.yaml`)

```yaml
rules:
  - name: code_task
    condition: "task_type == 'coding'"
    action: "select_best_model('coding')"

  - name: high_complexity
    condition: "complexity > 0.7"
    action: "select_model('gpt4o')"

  - name: cost_optimized
    condition: "max_cost < 0.01"
    action: "select_cheapest_model()"

  - name: default
    condition: "true"
    action: "select_balanced_model()"
```

### 4.3 Registry Sync (`config/registry_sync.yaml`)

```yaml
registry_sync:
  storage:
    backend: sqlite
    sqlite_path_env: ROUTE_AGENT_SQLITE_PATH
    # postgres_dsn_env: ROUTE_AGENT_POSTGRES_DSN
  schedule:
    mode: interval
    every_days: 30
  snapshot:
    keep_latest: true
    keep_history: 2
```

---

## 5. API Design

### 5.1 Core Routing Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/route` | Route a task and return the routing payload |
| `GET` | `/api/v1/models` | List models in the active pool |
| `GET` | `/api/v1/stats` | Read monitoring statistics |
| `GET` | `/api/v1/health` | Health check |
| `GET` | `/api/v1/dashboard` | Dashboard UI |
| `GET` | `/api/v1/pool-status/global` | Global model-card view |
| `GET` | `/api/v1/pool-status/classes` | Class-pool directory view |
| `POST` | `/api/v1/pool-status/classes/{class}/models` | Manually add a model to a class pool |
| `DELETE` | `/api/v1/pool-status/classes/{class}/models/{model}` | Manually remove a model from a class pool |

### 5.2 Federation Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/apps/register` | Register an application and its declared agents |
| `POST` | `/api/v1/concurrency/acquire` | Acquire a concurrency lease before execution |
| `POST` | `/api/v1/concurrency/release` | Release a lease after execution |
| `POST` | `/api/v1/outcomes/report` | Report execution outcomes for cross-app aggregation |
| `GET` | `/api/v1/pool-version` | Query current class-pool versions |
| `GET` | `/api/v1/mode` | Query local-vs-central mode decision for a model |
| `GET` | `/api/v1/federation-scores/{class}` | Read aggregated class-level model scores |

---

## 6. Roadmap

### Phase 1: MVP
- [x] Project initialization
- [x] Model Registry - provider adapters, normalization, SQLite/PostgreSQL persistence, pricing
- [x] Task Analyzer - vector-profile matching, LLM new-class determination, legacy fallback, and SQLite persistence
- [x] Support 6 model providers (OpenAI, DeepSeek, Google, Anthropic, Groq, Ollama)
- [x] Persist registry snapshots to SQLite (with PostgreSQL backend option)
- [x] 30-day registry sync scheduler (snapshot-first with refresh)
- [x] Rule-based router engine extracted into standalone `router_engine/` module
- [x] Monitoring module extracted into standalone `monitoring/` module
- [x] Core API endpoints (route, models, stats, health, dashboard, pool-status)
- [x] Configuration templates (`config/models.yaml`, `config/routing_rules.yaml`, `config/registry_sync.yaml`)

### Phase 2: Intelligent Routing
- [x] Vector-profile analyzer with local Ollama embeddings for semantic class matching
- [x] LLM new-class determination with review queue for class-pool expansion
- [x] Class-pool descriptions and per-class dimension profiles
- [x] Three-tier analysis pipeline (vector profile -> LLM new-class -> legacy fallback)
- [ ] Improved complexity model
- [ ] Dynamic rule engine
- [ ] Cost tracking and metrics persistence

### Phase 3: Advanced Features
- [x] Feedback-based routing strategy learning (Wilson Lower Bound confidence scoring; replaces fixed bonus/penalty tiers)
- [x] Federation layer: app/agent registry, concurrency lease control, outcome aggregation, pool version management
- [x] Federation fast path: known-agent routing bypasses the three-tier analysis chain
- [x] Blended scoring: `alpha * local_wilson + (1 - alpha) * fed_wilson` mixes local and federated confidence signals
- [ ] Multi-model collaboration
- [ ] Caching layer
- [x] Reproducible performance A/B benchmark harness

---

## 7. Success Metrics

| Metric | Target |
|---|---|
| Cost reduction | 40-60% vs always using large models |
| Routing accuracy | >=90% of tasks routed to appropriate models |
| Latency reduction | 50% lower response time for simple tasks |
| Availability | 99.9% system availability |

---

## 8. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Wrong routing lowers quality | Manual override and fallback mechanisms |
| Inaccurate complexity estimation | Continuous feedback-based tuning |
| Unstable model availability | Health checks and automatic degradation |
| Uncontrolled costs | Budget limits and real-time monitoring |

---

*Document Version: v1.3*
*Created: 2025-02-07*
*Updated: 2026-04-01*
