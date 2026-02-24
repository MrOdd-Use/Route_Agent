# Route Agent

Intelligent LLM routing system that automatically analyzes task characteristics and assigns the most suitable model, optimizing cost and efficiency.

## Key Features

- **Task Analysis** - detects task type (`coding`, `translation`, `scrape`, `extraction`, `summarization`, `classification`, `rewrite`, `review`, `reasoning`, `math`, fallback `qa`) and estimates complexity (0-1 scale)
- **Rule-Based Routing** - routes tasks to model tiers (fast / smart / strategic) based on configurable rules
- **Cost-Constrained Selection** - picks the best model under a given budget
- **Model Registry** - aggregates provider model lists, normalizes metadata, and persists snapshots locally
- **Monitoring** - embeds routing diagnostics (reason, tier, cost, alerts) in every response

## Architecture

```text
Client Request
      |
      v
Task Analyzer  -->  Router Engine  -->  Model Execution
 (type, complexity)   (tier selection)     (selected model)
      |                    |
      v                    v
 Model Registry       Monitor & Logs
 (capabilities,       (decisions, costs,
  costs, snapshots)    alerts)
```

## Quick Start

```bash
# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env          # then fill in API keys

# Run a single routing decision
python -m route_agent --task "Write a Python sort function"
```

## Configuration

Key environment variables (set in `.env`):

| Variable | Description |
|---|---|
| `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM` | Model selection per tier, format `provider:model` |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `GOOGLE_API_KEY` | Google Gemini API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `OLLAMA_BASE_URL` | Local Ollama instance URL |
| `EMBEDDING` | Embedding model for vector search |
| `RETRIEVER` | Comma-separated retriever list (tavily, mcp) |
| `DOC_PATH` | Path to research documents |

See `config/models.yaml` for model capabilities/costs and `config/routing_rules.yaml` for routing logic templates.

## Project Structure

```text
route_agent/
- __init__.py
- __main__.py              # CLI entrypoint
- app/                     # Application orchestration (CLI + service + wiring)
- model_registry/          # Model metadata, providers, storage, pricing
- task_analyzer/           # LLM-based task analysis engine
- router_engine/           # Routing engine (selector, escalation, class pool, storage, rate-limiters)
- monitoring/              # SQLite-backed monitoring APIs (record/recent/stats)

data/                        # Runtime data (gitignored)
- route_agent_registry.sqlite3
- task_analysis.db
- router_engine.db
- route_agent_monitoring.db

scripts/
- model_registry_full_dump.py  # Manual registry dump/inspection script
```

## Config Templates

```text
config/                      # Planning config templates (not auto-loaded by CLI)
- models.yaml
- routing_rules.yaml
- registry_sync.yaml
```

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/route` | Route and execute a task |
| `POST` | `/api/v1/suggest` | Suggest a model without executing |
| `GET` | `/api/v1/stats` | Routing statistics |

Request convention (recommended):
- Include `request_id` (UUID) in each routing request for idempotent event tracking.
- `record_id` is an internal analysis linkage field, not a client idempotency key.

## Development

```bash
# Run all tests
python -m pytest -v

# Run a specific module's tests
python -m pytest -v route_agent/model_registry/tests/
python -m pytest -v route_agent/task_analyzer/tests/
python -m pytest -v route_agent/tests/core/
python -m pytest -v route_agent/monitoring/tests/
```

## Documentation

- [PRD.md](docs/PRD.md) - Full product requirements document and roadmap
- [AGENTS.md](AGENTS.md) - Repository guidelines and coding conventions
- [config/models.yaml](config/models.yaml) - Model catalog template
- [config/routing_rules.yaml](config/routing_rules.yaml) - Routing policy template
- [config/registry_sync.yaml](config/registry_sync.yaml) - Registry sync policy template
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - Current module boundaries and request/data flow
- [route_agent/model_registry/MODEL_REGISTRY.md](route_agent/model_registry/MODEL_REGISTRY.md) - Model Registry module guide
- [route_agent/router_engine/ROUTER_ENGINE.md](route_agent/router_engine/ROUTER_ENGINE.md) - Router Engine module guide
- [route_agent/task_analyzer/TASK_ANALYZER.md](route_agent/task_analyzer/TASK_ANALYZER.md) - Task Analyzer module guide
- [route_agent/monitoring/MONITORING.md](route_agent/monitoring/MONITORING.md) - Monitoring module guide
