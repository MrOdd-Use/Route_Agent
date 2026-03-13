# Route Agent

Route Agent is a model-routing control plane for multi-agent systems. It analyzes a task, inspects the available model pool, and returns the most suitable LLM choice under cost, quality, health, and rate-limit constraints.

The project is intentionally split into reusable modules: `model_registry`, `task_analyzer`, `router_engine`, and `monitoring`, with one shared application layer used by both CLI and API entrypoints.

Important boundary: Route Agent does not try to be an inference gateway. Its core job is to decide which model should handle a task and expose enough diagnostics for your agent platform to understand why.

## Why This Exists

In real agent systems, tasks are uneven:

- Some requests are cheap and mechanical: extraction, classification, rewrite, translation.
- Some requests need stronger reasoning or code generation.
- Provider availability, model quality, and cost can change during runtime.

Always sending traffic to the strongest model is expensive. Always sending traffic to the cheapest model hurts quality. Route Agent sits between those two extremes and turns model choice into a measurable, testable subsystem.

## What The System Does

- Detects task type (`coding`, `translation`, `scrape`, `extraction`, `summarization`, `classification`, `rewrite`, `review`, `reasoning`, `math`, fallback `qa`) and estimates complexity before routing.
- Aggregates model metadata across providers into one normalized pool.
- Filters and ranks candidate models under explicit constraints such as `max_cost`, `preferred_model`, `exclude_models`, `require_provider`, and estimated prompt size.
- Learns from execution outcomes through class pools, health state, and downgrade trials.
- Applies overload-aware escalation and limiter-aware routing instead of naive retries.
- Persists routing and execution telemetry for dashboards, debugging, and strategy tuning.

## Technical Highlights

### 1. Snapshot-first multi-provider registry

The model registry pulls metadata from OpenAI, DeepSeek, Google, Anthropic, Groq, and optionally Ollama, then normalizes provider-specific differences into one internal shape. Instead of depending on live provider fetches for every route, the runtime reads from a local snapshot first.

Why it matters:

- Startup and routing stay fast because reads are local.
- Provider sync failures do not immediately break routing.
- The system can fall back to the last successful snapshot when a refresh returns empty data or errors.

Related code:
- [route_agent/model_registry/service.py](route_agent/model_registry/service.py)
- [route_agent/model_registry/storage/sqlite.py](route_agent/model_registry/storage/sqlite.py)
- [route_agent/model_registry/storage/postgres.py](route_agent/model_registry/storage/postgres.py)

### 2. Structured task analysis with fallback chain

Before routing, Route Agent converts a raw task into structured analysis: domain, task type, relevant dimensions, and complexity. The analyzer supports an LLM chain and falls back to heuristic analysis if the analyzer path is unavailable.

Why it matters:

- Routing logic works on structured inputs instead of keyword-only rules.
- Analyzer failures degrade gracefully instead of blocking the route path.
- Analysis records are persisted so downstream routing and feedback can be linked to the same task.

Related code:
- [route_agent/task_analyzer/analyzer.py](route_agent/task_analyzer/analyzer.py)
- [route_agent/app/analysis.py](route_agent/app/analysis.py)
- [route_agent/app/legacy_analysis.py](route_agent/app/legacy_analysis.py)

### 3. Router that combines capability, cost, health, and online learning

The router is not a fixed mapping table. It blends task analysis, model capability metadata, price signals, class-pool history, provider health, and rate-limit pressure when selecting candidates.

Why it matters:

- Model choice remains sensitive to cost, not just raw quality.
- Repeated success or failure can reshape future defaults for an agent class.
- Health and limiter state can suppress routes that would likely fail at execution time.

Related code:
- [route_agent/router_engine/engine.py](route_agent/router_engine/engine.py)
- [route_agent/router_engine/selector.py](route_agent/router_engine/selector.py)
- [route_agent/router_engine/scorer.py](route_agent/router_engine/scorer.py)
- [route_agent/router_engine/class_pool.py](route_agent/router_engine/class_pool.py)
- [route_agent/router_engine/health.py](route_agent/router_engine/health.py)

### 4. Closed-loop optimization: downgrade canary plus overload-aware escalation

The routing loop does more than pick a model once. It also supports:

- downgrade trials that test whether a cheaper challenger can replace a stable default
- escalation decisions when a stronger model is justified
- overload checks that stop escalation from amplifying congestion

Why it matters:

- Cost can improve over time without manual retuning.
- Failures do not automatically trigger the most expensive path.
- Escalation and downgrade behavior are explicit modules, which makes them easier to test and reason about.

Related code:
- [route_agent/router_engine/downgrade.py](route_agent/router_engine/downgrade.py)
- [route_agent/router_engine/escalation.py](route_agent/router_engine/escalation.py)

### 5. Dual limiter architecture with degradation path

The router can run with Redis-backed rate limiting or an in-memory limiter. In `auto` mode, the system can degrade instead of hard-failing when the preferred limiter path is unavailable.

Why it matters:

- You can use Redis sliding-window plus concurrency control in higher-concurrency deployments.
- You can still run locally or in simpler environments without external infrastructure.
- Limiter state becomes part of routing, not an afterthought.

Related code:
- [route_agent/router_engine/rate_limiters/redis.py](route_agent/router_engine/rate_limiters/redis.py)
- [route_agent/router_engine/rate_limiters/inmemory.py](route_agent/router_engine/rate_limiters/inmemory.py)
- [route_agent/router_engine/rate_limiters/factory.py](route_agent/router_engine/rate_limiters/factory.py)

### 6. SQLite-first observability with operational dashboards

Monitoring is built in, not bolted on. Route Agent records decision events, execution lifecycle state, recent activity, aggregated stats, and dashboard-facing card data for the global pool, class pools, and agent assignment views.

Why it matters:

- You can inspect why a route happened, not just which model was picked.
- Operational debugging stays local and lightweight.
- The same telemetry supports product dashboards, performance tests, and routing strategy review.

Related code:
- [route_agent/monitoring/service.py](route_agent/monitoring/service.py)
- [route_agent/monitoring/storage.py](route_agent/monitoring/storage.py)
- [route_agent/api/routes/dashboard.py](route_agent/api/routes/dashboard.py)
- [route_agent/api/pool_status.py](route_agent/api/pool_status.py)

### 7. Thin interfaces and one shared application layer

CLI and API do not maintain separate business logic. Both normalize input into shared contracts and call the same application orchestration path.

Why it matters:

- One route path means less drift between terminal usage and HTTP usage.
- Testing is easier because orchestration stays in `route_agent.app`.
- Route payload shape and side effects stay consistent across entrypoints.

Related code:
- [route_agent/app/contracts.py](route_agent/app/contracts.py)
- [route_agent/app/orchestrator.py](route_agent/app/orchestrator.py)
- [route_agent/app/service.py](route_agent/app/service.py)
- [route_agent/api/routes/route.py](route_agent/api/routes/route.py)

## End-to-End Flow

1. A request enters through `python -m route_agent` or `POST /api/v1/route`.
2. The interface layer normalizes the payload into `RouteAgentRequest` and `RouteAgentRunOptions`.
3. The registry loads a local model snapshot and refreshes provider data only when needed.
4. The task analyzer produces structured routing inputs or falls back to heuristic analysis.
5. The router filters candidates, scores them, applies class-pool defaults, and checks health plus limiter state.
6. Monitoring records the decision, execution lifecycle events, and dashboard aggregates.
7. The caller receives a routing payload with the chosen model, candidate list, analysis result, registry state, and limiter diagnostics.

## Architecture

```text
CLI / API
    |
    v
Application Layer
(contracts, orchestration,
 payloads, monitoring hooks)
    |
    +--> Task Analyzer
    +--> Model Registry
    +--> Router Engine
    +--> Monitoring
```

See also:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/MODULE_IMPLEMENTATION_GUIDE.md](docs/MODULE_IMPLEMENTATION_GUIDE.md)

## Quick Start

### Prerequisites

- Python 3.11
- `uv`
- At least one configured provider API key, or a reachable Ollama instance if you want local-only routing data

### 1. Install dependencies

```bash
uv sync --dev
```

### 2. Create `.env`

On Unix-like shells:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then fill in the provider keys you actually plan to use.

### 3. Run one route decision from the CLI

Current route input shape is `agent_name + system_prompt + task`.
In the current implementation, `task` is still required and is the primary text analyzed by `task_analyzer`; `system_prompt` carries the agent's role/SP and is used as additional routing context plus class-resolution signal.

Basic example:

```bash
uv run python -m route_agent --agent-name release-bot --system-prompt "You are a release engineering agent. Read product changes, summarize key updates, and produce concise release notes for internal and external audiences." --task "Summarize the latest changelog into release notes"
```

Constraint-aware example:

```bash
uv run python -m route_agent --agent-name code-reviewer --system-prompt "You are a senior Python code review agent. Focus on concurrency bugs, race conditions, and correctness risks." --task "Review this async Python function for race conditions" --max-cost 0.03 --require-provider openai --estimated-input-tokens 2400
```

### 4. Start the API

```bash
uv run python -m route_agent --serve
```

Windows PowerShell startup when `.venv` is already synced:

```powershell
.\.venv\Scripts\python.exe -m route_agent --serve
```

### 5. Open the dashboard

```text
http://localhost:8000/api/v1/dashboard
```

## CLI Usage Notes

Common flags:

| Flag | Purpose |
|---|---|
| `--task` | Required current request text; this is the primary analyzer input in the current implementation |
| `--agent-name` | Agent identity used in analysis and monitoring |
| `--system-prompt` | Agent system prompt / role description used as extra routing context and class-resolution signal |
| `--max-cost` | Upper bound used during candidate selection |
| `--preferred-model` | Soft preference for one model id |
| `--exclude-models` | Comma-separated model ids to remove from candidates |
| `--require-provider` | Restrict routing to one provider |
| `--estimated-input-tokens` | Helps context-window filtering |
| `--force-registry-sync` | Force an immediate provider refresh |
| `--rate-limit` | Limiter mode: `auto`, `redis`, `inmemory`, or `off` |
| `--rate-limit-fail-strategy` | In `auto`, choose `degrade` or `fail_fast` |

The CLI prints the full routing payload as JSON. Important fields include:

- `model_used`: final selected model id
- `routing_reason`: short explanation of the route
- `candidates`: ranked candidates considered by the router
- `analysis`: structured task analysis result
- `pool_summary`: current model-pool summary
- `registry_alerts` and `registry_errors`: provider refresh diagnostics
- `rate_limiter`: limiter mode and route-time limiter signals

## API Service

The project ships a REST API alongside the CLI entrypoint.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/route` | Route a task and return the full routing payload |
| `GET` | `/api/v1/models` | List current pool models |
| `GET` | `/api/v1/stats` | Read monitoring statistics |
| `GET` | `/api/v1/health` | Health check |
| `GET` | `/api/v1/dashboard` | Main dashboard UI |
| `GET` | `/api/v1/pool-status/global` | Global model-card payload |
| `GET` | `/api/v1/pool-status/classes` | Class-pool directory payload |

Start the API with `uv run python -m route_agent --serve`.
On Windows, if the virtual environment is already present, you can also use `.\.venv\Scripts\python.exe -m route_agent --serve`.

Example route request:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/route \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "release-bot",
    "system_prompt": "You are a release engineering agent. Convert raw product changes into clear release notes for stakeholders.",
    "task": "Summarize this product spec into release notes",
    "request_id": "8c3b7f16-5a3b-4af5-9c8a-3fc9bbeb0a12",
    "constraints": {
      "max_cost": 0.02,
      "require_provider": "google",
      "estimated_input_tokens": 1800
    }
  }'
```

Windows PowerShell equivalent:

```powershell
$body = @{
  agent_name = "release-bot"
  system_prompt = "You are a release engineering agent. Convert raw product changes into clear release notes for stakeholders."
  task = "Summarize this product spec into release notes"
  request_id = "8c3b7f16-5a3b-4af5-9c8a-3fc9bbeb0a12"
  constraints = @{
    max_cost = 0.02
    require_provider = "google"
    estimated_input_tokens = 1800
  }
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/route" -ContentType "application/json" -Body $body
```

Request convention:

- For `/route`, prefer sending `agent_name + system_prompt + task` together so routing has both the agent role and the current request context.
- `agent_class` is an optional manual override; do not send it in normal requests unless you explicitly want to force class resolution.
- Include `request_id` as a client-controlled idempotency key whenever possible.
- `record_id` is an internal analysis linkage field, not a client-supplied id.
- `task` is still required in the current code path; empty-task requests return a fixed fallback payload instead of going through the full route path.

## Configuration

Runtime configuration is environment-variable driven through `.env`.

Provider access:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `GOOGLE_API_KEY` | Google Gemini API key |
| `GEMINI_API_KEY` | Alternative Gemini API key variable |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GROQ_API_KEY` | Groq API key |
| `OLLAMA_BASE_URL` | Local Ollama base URL |

Storage and runtime state:

| Variable | Description |
|---|---|
| `ROUTE_AGENT_SQLITE_PATH` | SQLite path for model registry snapshots |
| `ROUTER_DB_PATH` | SQLite path for router engine state |
| `ROUTE_AGENT_POSTGRES_DSN` | Optional PostgreSQL DSN for shared registry storage |
| `REDIS_URL` | Optional Redis URL for the router limiter |
| `RATE_LIMIT_MODE` | Limiter mode: `auto`, `redis`, `inmemory`, `off` |
| `RATE_LIMIT_FAIL_STRATEGY` | Limiter fallback strategy: `degrade` or `fail_fast` |
| `ROUTE_AGENT_MONITORING_ENABLED` | Enable monitoring sidecar |

Optional enrichment flags:

| Variable | Description |
|---|---|
| `ENABLE_DYNAMIC_PRICING` | Enable dynamic pricing fetcher (`0` or `1`) |
| `ENABLE_ARENA_SCORING` | Enable Arena leaderboard enrichment (`0` or `1`) |

Planning templates in `config/` are not auto-loaded by the current runtime path. They are reference material, not the active config source:

```text
config/
- models.yaml
- routing_rules.yaml
- registry_sync.yaml
```

## State And Persistence

Route Agent keeps operational state in local databases by default:

| File | Purpose |
|---|---|
| `data/route_agent_registry.sqlite3` | Model registry snapshots and provider sync state |
| `data/task_analysis.db` | Structured analysis records and feedback linkage |
| `data/router_engine.db` | Class pools, defaults, downgrade trials, availability, and router events |
| `data/route_agent_monitoring.db` | Monitoring decisions, execution lifecycle, and aggregates |

This SQLite-first design keeps local development simple while still leaving a PostgreSQL extension path for shared registry deployments.

## Monitoring And Dashboards

Dashboard-related endpoints:

- `GET /api/v1/dashboard` for the main operational UI
- `GET /api/v1/dashboard/class-pools/{agent_class}` for one class pool page
- `GET /api/v1/pool-status/global` for global model cards
- `GET /api/v1/pool-status/classes` for class pool directory data
- `GET /api/v1/pool-status/classes/{agent_class}` for one class pool payload

The monitoring subsystem is useful for:

- tracing recent route decisions
- tracking execution lifecycle state per agent
- identifying model health regressions or limiter pressure
- inspecting class-pool defaults and assignment behavior

For a terminal-oriented live view:

```bash
uv run python -m route_agent.monitoring.watch --interval 1 --source router_engine_perf_test
```

## Project Structure

```text
route_agent/
- __init__.py
- __main__.py              # top-level entrypoint for CLI and --serve mode
- api/                     # FastAPI interface layer
- app/                     # Shared application contracts and orchestration
- model_registry/          # Provider adapters, normalization, snapshots, pricing
- task_analyzer/           # Structured task analysis
- router_engine/           # Selection, scoring, health, class pools, escalation, limiter logic
- monitoring/              # Decision and execution telemetry

data/                      # Runtime data (gitignored)
- route_agent_registry.sqlite3
- task_analysis.db
- router_engine.db
- route_agent_monitoring.db

scripts/
- model_registry_full_dump.py
- perf_ab_compare.py
- project_audit.py

docs/
- ARCHITECTURE.md
- MODULE_IMPLEMENTATION_GUIDE.md
- TESTING_GUIDE.md
- interview/
  - concept.md
  - questions.md
  - agent_interview_questions.md
```

## Development

Run the full test suite:

```bash
uv run pytest -q
```

Common targeted test commands:

```bash
uv run pytest -q route_agent/model_registry/arena/tests/
uv run pytest -q route_agent/task_analyzer/tests/
uv run pytest -q route_agent/api/tests/
uv run pytest -q route_agent/monitoring/tests/
uv run pytest -q route_agent/tests/core/
```

Structural checks:

```bash
uv run python scripts/project_audit.py
```

Performance harness:

```bash
uv run pytest -q route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py
uv run python scripts/perf_ab_compare.py
```

## Documentation

- [docs/PRD.md](docs/PRD.md) - Product requirements and roadmap
- [AGENTS.md](AGENTS.md) - Repository guidance and coding conventions
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Module boundaries and request/data flow
- [docs/MODULE_IMPLEMENTATION_GUIDE.md](docs/MODULE_IMPLEMENTATION_GUIDE.md) - Implementation paths and key methods
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) - Test coverage map and run commands
- [route_agent/model_registry/MODEL_REGISTRY.md](route_agent/model_registry/MODEL_REGISTRY.md) - Model registry guide
- [route_agent/task_analyzer/TASK_ANALYZER.md](route_agent/task_analyzer/TASK_ANALYZER.md) - Task analyzer guide
- [route_agent/router_engine/ROUTER_ENGINE.md](route_agent/router_engine/ROUTER_ENGINE.md) - Router engine guide
- [route_agent/monitoring/MONITORING.md](route_agent/monitoring/MONITORING.md) - Monitoring guide
- [docs/interview/concept.md](docs/interview/concept.md) - Background concepts for interview prep
- [docs/interview/questions.md](docs/interview/questions.md) - Main interview Q&A set
- [docs/interview/agent_interview_questions.md](docs/interview/agent_interview_questions.md) - Agent-focused interview Q&A set
