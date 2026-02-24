# AGENTS

Repository guidance for contributors and coding agents.

## Current Product Scope

- Primary entrypoint: CLI (`python -m route_agent`)
- Core modules: `model_registry`, `task_analyzer`, `router_engine`, `monitoring`
- REST API routes are documented in `docs/PRD.md` but not wired in code yet

## Local Setup

```bash
uv sync --dev
uv run pytest -q
uv run python -m route_agent --task "Write a Python hello world script"
```

## Configuration Sources

- Runtime configuration is currently environment-variable driven (`.env`)
- Use `.env.example` as the bootstrap template
- `config/models.yaml`, `config/routing_rules.yaml`, and `config/registry_sync.yaml` are planning templates and are not auto-loaded by the current CLI path

## Code Quality Expectations

- Keep package-level imports lazy where applicable (to avoid eager heavy dependency loading)
- Add tests for each behavior change in the closest module test folder
- Keep docs in sync when changing module boundaries or public entrypoints

## Test Layout

- Module-local tests:
  - `route_agent/model_registry/tests/`
  - `route_agent/task_analyzer/tests/`
  - `route_agent/router_engine/tests/`
  - `route_agent/monitoring/tests/`
- Cross-module tests:
  - `route_agent/tests/core/`
