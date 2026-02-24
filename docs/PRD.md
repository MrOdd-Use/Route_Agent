# Route Agent - Product Requirements Document (PRD)

## 1. Project Overview

### 1.1 Project Name
**Route Agent** - Intelligent LLM Routing System

### 1.2 Background
In agent applications, task requirements vary significantly:
- Simple Q&A can run on low-cost small models.
- Complex reasoning needs stronger models with higher cost.
- Code generation benefits from code-specialized models.
- Long-context tasks require large context windows.

Current pain point: manually selecting a model per task is inefficient and does not optimize by task complexity.

### 1.3 Goals
Build a routing system that analyzes task features and assigns the most suitable LLM to achieve:
- **Cost Optimization**: use lower-cost models for simple tasks.
- **Quality Assurance**: use stronger models for complex tasks.
- **Efficiency Improvement**: reduce unnecessary compute and token usage.

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
| Task Type Detection | Detect task types (`coding`, `translation`, `scrape`, `extraction`, `summarization`, `classification`, `rewrite`, `review`, `reasoning`, `math`, fallback `qa`) | P0 |
| Complexity Estimation | Estimate complexity (0-1) from length, context needs, reasoning depth | P0 |
| Intent Analysis | Detect true user intent to avoid shallow misclassification | P1 |

### 2.3 Router Engine

| Function | Description | Priority |
|---|---|---|
| Rule-Based Routing | Route by predefined rules (for example coding task -> coding-capable model) | P0 |
| Semantic Routing | Route by semantic similarity | P1 |
| Cost-Constrained Routing | Choose best model under budget constraints | P2 |
| A/B Routing | Compare routing outcomes across model candidates | P2 |

### 2.4 Monitoring and Feedback

| Function | Description | Priority |
|---|---|---|
| Decision Logging | Record each routing decision and rationale | P0 |
| Cost Tracking | Track token usage and cost by model | P0 |
| Effectiveness Evaluation | Collect user feedback for routing optimization | P1 |

---

## 3. Technical Architecture

### 3.1 System Diagram

```text
+------------------------- Client Application -------------------------+
                                |
                                v
+---------------------------- Route Agent -----------------------------+
| +-----------+   +---------------+   +-----------------------------+ |
| | ModelPool |-->| Task Analyzer |-->| Router Engine               | |
| | GPT-4o    |   | type          |   | rule/semantic/cost routing  | |
| | Claude    |   | complexity    |   +-----------------------------+ |
| | DeepSeek  |   | intent        |                                 | |
| | Ollama    |   +---------------+                                 | |
| +-----------+           |                                         | |
|       ^                 v                                         | |
| +----------------+  +----------------+                            | |
| | Config Store   |  | Monitor & Logs |                            | |
| | model specs    |  | decisions      |                            | |
| | routing rules  |  | costs          |                            | |
| | cost config    |  | feedback       |                            | |
| +----------------+  +----------------+                            | |
+---------------------------------------------------------------------+
```

### 3.2 Technology Stack

| Layer | Technology | Purpose |
|---|---|---|
| Framework | LangChain + LangGraph | LLM integration, orchestration, and stateful workflows |
| API | FastAPI | REST API service |
| Configuration | YAML / Pydantic | Configuration management and validation |
| Storage | SQLite (default) / PostgreSQL (extension) | Registry and analysis persistence |
| Logging | Loguru | Structured logging |
| LLM Providers | OpenAI, DeepSeek, Google Gemini, Anthropic, Groq, Ollama | Multi-provider model access |
| Vector Store | Chroma / FAISS (optional) | Semantic routing embeddings |

### 3.3 Data Flow

```python
# 1. Request
request = {
    "request_id": "uuid-v4-string",
    "task": "Explain the principles of quantum entanglement",
    "context": {...},
    "constraints": {"max_cost": 0.1},
}

# 2. Task analysis
analysis = TaskAnalyzer.analyze(request)
# => {"type": "explanation", "complexity": 0.8, "domain": "physics"}

# 3. Routing decision
model = Router.route(analysis)
# => "openai:gpt-4o"

# 4. Execute and return
response = model.invoke(request["task"])
```

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

### 5.1 Core Endpoints

```python
# Route and execute task
POST /api/v1/route
{
  "request_id": "string (required, UUID recommended)",
  "task": "string",
  "context": "dict (optional)",
  "constraints": {
    "max_cost": "float (optional)",
    "preferred_model": "string (optional)"
  }
}
-> {
  "result": "string",
  "model_used": "string",
  "cost": "float",
  "routing_reason": "string"
}

# Notes:
# - request_id is the request-level idempotency key for feedback/event deduplication.
# - record_id (analysis_records.id) is an internal linkage field and may be null in fallback.

# Suggest route only (no execution)
POST /api/v1/suggest
{
  "request_id": "string (optional, UUID recommended)",
  "task": "string"
}
-> {
  "suggested_model": "string",
  "confidence": "float",
  "reason": "string"
}

# Routing statistics
GET /api/v1/stats
-> {
  "total_requests": "int",
  "model_distribution": "dict",
  "total_cost": "float",
  "avg_complexity": "float"
}
```

---

## 6. Roadmap

### Phase 1: MVP
- [x] Project initialization
- [x] Model Registry — provider adapters, normalization, SQLite/PostgreSQL persistence, pricing
- [x] Task Analyzer — LLM-based analysis engine with structured output, fallback chain, SQLite persistence
- [x] Support 6 model providers (OpenAI, DeepSeek, Google, Anthropic, Groq, Ollama)
- [x] Persist registry snapshots to SQLite (with PostgreSQL backend option)
- [x] 30-day registry sync scheduler (snapshot-first with refresh)
- [ ] Rule-based router engine (currently inline in `main.py`, not yet extracted to `router_engine/`)
- [ ] Monitoring module (currently inline diagnostics in response payload, not yet extracted)
- [ ] Basic API endpoints (FastAPI configured but endpoints not yet wired)
- [x] Configuration templates (`config/models.yaml`, `config/routing_rules.yaml`, `config/registry_sync.yaml`)

### Phase 2: Intelligent Routing
- [ ] Extract router engine into standalone `router_engine/` module
- [ ] Extract monitoring into standalone `monitoring/` module
- [ ] Semantic routing with embeddings
- [ ] Improved complexity model
- [ ] Dynamic rule engine
- [ ] Cost tracking and metrics persistence

### Phase 3: Advanced Features
- [ ] Feedback-based routing strategy learning
- [ ] Multi-model collaboration
- [ ] Caching layer
- [ ] A/B test framework

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

*Document Version: v1.1*
*Created: 2025-02-07*
*Updated: 2025-02-15*
