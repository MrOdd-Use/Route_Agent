# Architecture Index

Route Agent automatically assigns models based on task characteristics: stronger large models for complex tasks, cost-effective smaller models for lighter work, and vertical models for specialized domains. The modules below implement this routing pipeline.

## Module Boundaries

- `route_agent.api`: FastAPI interface layer. Only HTTP schema validation, settings loading, and route adapters live here.
- `route_agent.app`: application layer. Owns request normalization, registry/analyzer/router orchestration, monitoring side effects, and payload assembly.
- `route_agent.model_registry`: provider extraction, normalization, and snapshot storage.
- `route_agent.task_analyzer`: vector-profile matching, LLM-based task analysis, new-class determination, and analysis-record persistence.
- `route_agent.router_engine`: candidate selection, class pool learning, escalation, downgrade, and rate limiting.
- `route_agent.monitoring`: side-car observability (decision record/recent/stats APIs, execution lifecycle tracking, realtime watch CLI).

## Application Submodules

- `route_agent.app.contracts`: canonical request and runtime option models shared by CLI, API, and direct service calls.
- `route_agent.app.analysis`: three-tier task-analysis chain (vector profile → LLM new-class determination → legacy heuristic fallback).
- `route_agent.app.registry`: model-registry snapshot loading and `MainModelPool` construction.
- `route_agent.app.monitoring`: route-decision monitoring event building and best-effort persistence.
- `route_agent.app.orchestrator`: end-to-end route execution flow.
- `route_agent.app.service`: compatibility facade that keeps `run_route_agent(...)` as the stable public entrypoint.

## Implementation Docs

- `docs/MODULE_IMPLEMENTATION_GUIDE.md`: full implementation map (entrypoints, paths, key methods).
- `route_agent/model_registry/MODEL_REGISTRY.md`: model-registry implementation guide.
- `route_agent/task_analyzer/TASK_ANALYZER.md`: task-analyzer implementation guide.
- `route_agent/router_engine/ROUTER_ENGINE.md`: router-engine implementation guide.
- `route_agent/monitoring/MONITORING.md`: monitoring implementation guide.

## Request Flow

CLI path:
1. `python -m route_agent` enters `route_agent.__main__`.
2. `route_agent.app.cli.main` parses CLI args into `RouteAgentRequest` and `RouteAgentRunOptions`.
3. `route_agent.app.service.run_route_agent(...)` delegates to the app orchestrator and returns the unified payload.

Pool management CLI path (manual class-pool channel):
1. `python -m route_agent pool <add|remove|list>` enters `route_agent.__main__`.
2. `route_agent.app.pool_cli.pool_main` dispatches to pool add/remove/list subcommands.
3. Operations delegate to `RouterEngine.add_model_to_pool(...)` / `remove_model_from_pool(...)` / `list_pools_async(...)`.

API path:
1. `python -m route_agent --serve` enters `route_agent.__main__` and boots `route_agent.api.main`.
2. API routes validate request bodies, convert them into `RouteAgentRequest`, and call `run_route_agent(...)`.
3. The same app-layer orchestration path is reused, so CLI and API share routing behavior.
4. On Windows PowerShell with an existing virtual environment, you can start the same API path with `.\.venv\Scripts\python.exe -m route_agent --serve`.

Shared application path:
1. `route_agent.app.registry.build_registry_context(...)` loads a registry snapshot/live report and builds `MainModelPool`.
2. `route_agent.app.analysis.resolve_task_analysis(...)` runs a three-tier analysis chain: ① vector-profile matching via local Ollama embeddings, ② LLM new-class determination when the vector match misses, ③ legacy keyword heuristic fallback.
3. `route_agent.app.orchestrator.execute_route(...)` builds `RouteRequest` and routes via `RouterEngine`.
4. `route_agent.app.monitoring.record_route_decision(...)` persists route telemetry in best-effort mode.
5. `route_agent.app.payloads.build_route_payload(...)` returns the unified response body.

## Data Stores

- `data/route_agent_registry.sqlite3`: model-registry snapshots.
- `data/task_analysis.db`: task-analyzer records and feedback.
- `data/router_engine.db`: router-engine class pool, defaults, events, availability, downgrade trials.
- `data/route_agent_monitoring.db`: monitoring decision events, execution lifecycle, and aggregates.
- `data/profile_embeddings.db`: cached Ollama embeddings for class-pool descriptions (used by the vector-profile analyzer).

## Test Layout

- Module-local tests stay close to implementation:
  - `route_agent/model_registry/tests/`
  - `route_agent/model_registry/arena/tests/`
  - `route_agent/task_analyzer/tests/`
  - `route_agent/router_engine/tests/`
  - `route_agent/router_engine/tests/perf/`
  - `route_agent/monitoring/tests/`
  - `route_agent/api/tests/`
- Cross-module and package-entry tests are centralized in:
  - `route_agent/tests/core/`
## Operational Scripts

- `scripts/model_registry_full_dump.py`: manual dump and DB inspection utility (not collected by pytest).
- `scripts/perf_ab_compare.py`: performance A/B comparison utility.
- `scripts/project_audit.py`: structural audit checks for the project.

## Extension Checklist

When adding a new domain/module:

1. Add package-level public exports (`__init__.py`) for stable import boundaries.
2. Keep interface layers thin. CLI/API adapters should translate inputs into `route_agent.app` contracts instead of reimplementing orchestration.
3. Add or update tests in module-local `tests/` and `route_agent/tests/core/` as needed.
4. Update `README.md`, this architecture index, and `docs/MODULE_IMPLEMENTATION_GUIDE.md`.
5. Document storage impact if any new DB/table is introduced.
