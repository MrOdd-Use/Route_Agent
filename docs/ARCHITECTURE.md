# Architecture Index

## Module Boundaries

- `route_agent.app`: application layer (CLI parsing, request orchestration, dependency wiring, payload mapping).
- `route_agent.model_registry`: provider extraction, normalization, and snapshot storage.
- `route_agent.task_analyzer`: LLM-based task analysis and analysis-record persistence.
- `route_agent.router_engine`: candidate selection, class pool learning, escalation, downgrade, and rate limiting.
- `route_agent.monitoring`: side-car observability (decision record/recent/stats APIs).

## Implementation Docs

- `docs/MODULE_IMPLEMENTATION_GUIDE.md`: full implementation map (entrypoints, paths, key methods).
- `route_agent/model_registry/MODEL_REGISTRY.md`: model-registry implementation guide.
- `route_agent/task_analyzer/TASK_ANALYZER.md`: task-analyzer implementation guide.
- `route_agent/router_engine/ROUTER_ENGINE.md`: router-engine implementation guide.
- `route_agent/monitoring/MONITORING.md`: monitoring implementation guide.

## Request Flow

1. `python -m route_agent` enters `route_agent.app.cli.main`.
2. CLI calls `route_agent.app.service.run_route_agent`.
3. Service fetches model metadata from `model_registry` local-pool service.
4. Service invokes `task_analyzer` (fallback to legacy analysis heuristics on failure).
5. Service builds `RouteRequest` and routes via `RouterEngine`.
6. Service returns a unified payload for caller/API response.
7. `monitoring` can be enabled separately to record route decisions.

## Data Stores

- `data/route_agent_registry.sqlite3`: model-registry snapshots.
- `data/task_analysis.db`: task-analyzer records and feedback.
- `data/router_engine.db`: router-engine class pool, defaults, events, availability, downgrade trials.
- `data/route_agent_monitoring.db`: monitoring decision events and aggregates.

## Test Layout

- Module-local tests stay close to implementation:
  - `route_agent/model_registry/tests/`
  - `route_agent/model_registry/arena/tests/`
  - `route_agent/task_analyzer/tests/`
  - `route_agent/router_engine/tests/`
  - `route_agent/router_engine/tests/perf/`
  - `route_agent/monitoring/tests/`
- Cross-module and package-entry tests are centralized in:
  - `route_agent/tests/core/`
- Detailed test-file map:
  - `docs/TESTING_GUIDE.md`

## Operational Scripts

- `scripts/model_registry_full_dump.py`: manual dump and DB inspection utility (not collected by pytest).

## Extension Checklist

When adding a new domain/module:

1. Add package-level public exports (`__init__.py`) for stable import boundaries.
2. Add or update tests in module-local `tests/` and `route_agent/tests/core/` as needed.
3. Update `README.md`, this architecture index, and `docs/MODULE_IMPLEMENTATION_GUIDE.md`.
4. Document storage impact if any new DB/table is introduced.
