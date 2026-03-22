# AGENTS

Repository guidance for contributors and coding agents.

## What is Route Agent

Route Agent automatically assigns models based on task characteristics: stronger large models for complex coding tasks, cost-effective smaller models for lighter work like summarization and classification, and vertical models for specialized domains — ensuring quality while minimizing overall cost.

## Current Product Scope

- Primary entrypoint: CLI (`python -m route_agent`)
- Core modules: `model_registry`, `task_analyzer` (vector profile + LLM new-class + keyword fallback), `router_engine`, `monitoring`
- REST API routes are documented in `docs/PRD.md` and partially wired (route, models, stats, health, dashboard, pool-status)

## Local Setup

```bash
uv sync --dev
uv run pytest -q
uv run python -m route_agent --task "Write a Python hello world script"
uv run python -m route_agent --serve
```

Windows PowerShell API startup:

```powershell
.\.venv\Scripts\python.exe -m route_agent --serve
```

## Configuration Sources

- Runtime configuration is currently environment-variable driven (`.env`)
- Use `.env.example` as the bootstrap template
- `config/models.yaml`, `config/routing_rules.yaml`, and `config/registry_sync.yaml` are planning templates and are not auto-loaded by the current CLI path

## Collaboration Rules

- Before adding or deleting repository content, first show the user the exact planned additions or removals and wait for explicit confirmation before editing files.
- For ambiguous user queries, ask a clarifying question promptly before proceeding with implementation.
- Guide the user to fill in missing details such as goal, scope, constraints, input data, expected output, and priority.
- When possible, offer a small set of concrete options so the user can clarify quickly.
- Remove temporary files or staging artifacts promptly after they are no longer needed.

## Code Quality Expectations

- Keep package-level imports lazy where applicable (to avoid eager heavy dependency loading)
- Add tests for each behavior change in the closest module test folder
- Keep docs in sync when changing module boundaries or public entrypoints
- All Python modules, classes, and functions must include docstrings; treat missing docstrings as a quality issue to fix before merge.

## Test Layout

- Module-local tests:
  - `route_agent/model_registry/arena/tests/`
  - `route_agent/task_analyzer/tests/`
  - `route_agent/router_engine/tests/`
  - `route_agent/monitoring/tests/`
- Cross-module tests:
  - `route_agent/tests/core/`
