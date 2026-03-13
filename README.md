# Route Agent

Intelligent LLM routing system that automatically analyzes task characteristics and assigns the most suitable model, optimizing cost and efficiency.

## Key Features

- **Task Analysis** - detects task type (`coding`, `translation`, `scrape`, `extraction`, `summarization`, `classification`, `rewrite`, `review`, `reasoning`, `math`, fallback `qa`) and estimates complexity (0-1 scale)
- **Rule-Based Routing** - routes tasks to model tiers (fast / smart / strategic) based on configurable rules
- **Cost-Constrained Selection** - picks the best model under a given budget
- **Model Registry** - aggregates provider model lists, normalizes metadata, and persists snapshots locally
- **Monitoring** - records routing diagnostics plus per-agent execution lifecycle (assigned model + running/success/failed status) with text dashboard rendering

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

## Quick Start

```bash
# Install dependencies and dev tooling
uv sync --dev

# Configure environment
cp .env.example .env          # then fill in API keys

# Run a single routing decision
uv run python -m route_agent --task "Write a Python sort function"

# Start the REST API
uv run python -m route_agent --serve
```

## Configuration

Key environment variables (set in `.env`):

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `GOOGLE_API_KEY` | Google Gemini API key |
| `GEMINI_API_KEY` | Gemini API key (alternative to GOOGLE_API_KEY) |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GROQ_API_KEY` | Groq API key |
| `OLLAMA_BASE_URL` | Local Ollama instance URL |
| `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM` | Model selection per tier, format `provider:model` |
| `ROUTE_AGENT_SQLITE_PATH` | SQLite DB path for model registry snapshots |
| `ROUTER_DB_PATH` | SQLite path for router engine state |
| `REDIS_URL` | Optional Redis URL for rate limiter |
| `RATE_LIMIT_MODE` | Rate limiter mode (`auto`/`redis`/`inmemory`/`off`) |
| `ROUTE_AGENT_MONITORING_ENABLED` | Enable monitoring sidecar (`true`/`false`) |
| `ENABLE_DYNAMIC_PRICING` | Enable dynamic pricing fetcher (`0`/`1`) |
| `ENABLE_ARENA_SCORING` | Enable Arena leaderboard integration (`0`/`1`) |

Runtime configuration is environment-variable driven (`.env`).  
`config/models.yaml`, `config/routing_rules.yaml`, and `config/registry_sync.yaml` are planning templates and are not auto-loaded by the current CLI/API runtime path.

## Project Structure

```text
route_agent/
- __init__.py
- __main__.py              # top-level entrypoint for CLI and --serve mode
- api/                     # FastAPI interface layer (schemas, routes, settings adapters)
- app/                     # Application contracts and orchestration shared by CLI/API
- model_registry/          # Model metadata, providers, storage, pricing
- task_analyzer/           # LLM-based task analysis engine
- router_engine/           # Routing engine (selector, escalation, class pool, storage, rate-limiters)
- monitoring/              # SQLite-backed monitoring APIs (decisions + execution lifecycle + dashboards)

data/                        # Runtime data (gitignored)
- route_agent_registry.sqlite3
- task_analysis.db
- router_engine.db
- route_agent_monitoring.db

scripts/
- model_registry_full_dump.py  # Manual registry dump/inspection script
- perf_ab_compare.py           # Performance A/B comparison utility
- project_audit.py             # Structural audit checks
```

## Config Templates

```text
config/                      # Planning config templates (not auto-loaded by runtime)
- models.yaml
- routing_rules.yaml
- registry_sync.yaml
```

## API Service

The project ships a REST API alongside the CLI entrypoint.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/route` | Route a task and return the full routing payload |
| `POST` | `/api/v1/suggest` | Suggest a model without execution |
| `GET` | `/api/v1/models` | List current pool models |
| `GET` | `/api/v1/stats` | Read monitoring statistics |
| `GET` | `/api/v1/health` | Health check |

Start the API with `uv run python -m route_agent --serve`.

Request convention (recommended):
- Include `request_id` (UUID) in each routing request for idempotent event tracking.
- `record_id` is an internal analysis linkage field, not a client idempotency key.

## Development

```bash
# Run all tests
uv run pytest -q

# Run a specific module's tests
uv run pytest -q route_agent/model_registry/arena/tests/
uv run pytest -q route_agent/task_analyzer/tests/
uv run pytest -q route_agent/api/tests/
uv run pytest -q route_agent/tests/core/
uv run pytest -q route_agent/monitoring/tests/

# Realtime monitoring dashboard
uv run python -m route_agent.monitoring.watch --interval 1 --source router_engine_perf_test

# Run structural audit checks
uv run python scripts/project_audit.py
```

## Documentation

- [PRD.md](docs/PRD.md) - Full product requirements document and roadmap
- [AGENTS.md](AGENTS.md) - Repository guidelines and coding conventions
- [config/models.yaml](config/models.yaml) - Model catalog template
- [config/routing_rules.yaml](config/routing_rules.yaml) - Routing policy template
- [config/registry_sync.yaml](config/registry_sync.yaml) - Registry sync policy template
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Current module boundaries and request/data flow
- [docs/MODULE_IMPLEMENTATION_GUIDE.md](docs/MODULE_IMPLEMENTATION_GUIDE.md) - Implementation paths and key methods across modules
- [docs/TESTING_GUIDE.md](docs/TESTING_GUIDE.md) - Per-test-file coverage map and run commands
- [route_agent/model_registry/MODEL_REGISTRY.md](route_agent/model_registry/MODEL_REGISTRY.md) - Model Registry module guide
- [route_agent/router_engine/ROUTER_ENGINE.md](route_agent/router_engine/ROUTER_ENGINE.md) - Router Engine module guide
- [route_agent/task_analyzer/TASK_ANALYZER.md](route_agent/task_analyzer/TASK_ANALYZER.md) - Task Analyzer module guide
- [route_agent/monitoring/MONITORING.md](route_agent/monitoring/MONITORING.md) - Monitoring module guide
