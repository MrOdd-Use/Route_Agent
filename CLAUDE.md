# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Route Agent** is an intelligent LLM routing system that analyzes task characteristics and selects a suitable model based on quality, cost, and runtime constraints.

Current implementation is **CLI-first**:
- Entrypoint: `python -m route_agent`
- Primary flow: `route_agent.app` -> `task_analyzer` -> `router_engine` -> payload
- REST API endpoints are documented in PRD but are not wired in code yet

## Environment Setup

Use `uv` for dependency and test execution:

```bash
uv sync --dev
```

Or with venv + pip:

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

## Running Tests

```bash
# Run all tests
uv run pytest -q

# Module-level runs
uv run pytest -v route_agent/model_registry/tests/
uv run pytest -v route_agent/task_analyzer/tests/
uv run pytest -v route_agent/router_engine/tests/
uv run pytest -v route_agent/monitoring/tests/
uv run pytest -v route_agent/tests/core/
```

## Runtime Configuration

Bootstrap from `.env.example`:

```bash
cp .env.example .env
```

Key environment variables:
- `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`
- `FAST_LLM`, `SMART_LLM`, `STRATEGIC_LLM`
- `ROUTE_AGENT_SQLITE_PATH`, `ROUTE_AGENT_POSTGRES_DSN`
- `ROUTER_DB_PATH`, `REDIS_URL`, `RATE_LIMIT_MODE`, `RATE_LIMIT_FAIL_STRATEGY`
- `ROUTE_AGENT_MONITORING_ENABLED`, `ROUTE_AGENT_MONITORING_DB_PATH`, `ROUTE_AGENT_MONITORING_RETENTION_DAYS`

## Architecture

Main modules:
- `route_agent.app`: CLI parsing, request orchestration, payload assembly
- `route_agent.model_registry`: provider extraction, normalization, and local snapshot storage
- `route_agent.task_analyzer`: LLM-based task/domain analysis with fallback
- `route_agent.router_engine`: candidate scoring, selection, class-pool/defaults, limiter integration
- `route_agent.monitoring`: decision event record/recent/stats APIs

Reference: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Data Storage

Runtime data files are under `data/`:
- `data/route_agent_registry.sqlite3`
- `data/task_analysis.db`
- `data/router_engine.db`
- `data/route_agent_monitoring.db`

## Config Templates

Planning templates live under `config/`:
- `config/models.yaml`
- `config/routing_rules.yaml`
- `config/registry_sync.yaml`

Note: current CLI/runtime path is environment-variable driven and does not auto-load these YAML files.

## Development References

- Product requirements and roadmap: [docs/PRD.md](docs/PRD.md)
- Contributor guidance: [AGENTS.md](AGENTS.md)

## Working Guidelines for Claude

### When to Explore the Codebase

Explore when:
- The user asks for architecture/behavior explanation
- You need to debug a failing behavior
- You are refactoring existing modules

Avoid broad exploration when:
- The task is simple and self-contained
- The user gives explicit files and concrete edits

### General Principles

1. Prefer direct action when requirements are clear.
2. Read only the files needed for the requested change.
3. Keep docs and implementation claims in sync.
4. Add or update tests for behavioral changes.
