# Module Implementation Guide

This guide explains where each module is implemented, which methods are the primary entrypoints, and how the modules connect at runtime.

## 1. End-to-End Request Path

1. `python -m route_agent` enters [route_agent/__main__.py](/d:/agent/Route_Agent/route_agent/__main__.py).
2. CLI parsing happens in [route_agent/app/cli.py](/d:/agent/Route_Agent/route_agent/app/cli.py), then calls `run_route_agent(...)`.
3. Application orchestration is in [route_agent/app/service.py](/d:/agent/Route_Agent/route_agent/app/service.py).
4. `model_registry` provides model metadata and builds a `MainModelPool`.
5. `task_analyzer` produces task domain and dimension scores (or legacy fallback if analyzer fails).
6. `router_engine` selects candidates and returns a `RouteDecision`.
7. Application payload is assembled by [route_agent/app/payloads.py](/d:/agent/Route_Agent/route_agent/app/payloads.py).
8. Optional monitoring can persist route events through `route_agent.monitoring` APIs.

## 2. Module Map

| Module | Main Purpose | Key Paths |
|---|---|---|
| `route_agent.app` | CLI entry and top-level orchestration | `route_agent/app/cli.py`, `service.py`, `wiring.py`, `payloads.py`, `legacy_analysis.py` |
| `route_agent.model_registry` | Fetch/normalize/store model catalog from providers | `model_registry/service.py`, `registry.py`, `pool.py`, `providers/`, `storage/` |
| `route_agent.task_analyzer` | LLM-based task analysis and analysis record persistence | `task_analyzer/analyzer.py`, `client.py`, `prompt.py`, `storage.py` |
| `route_agent.router_engine` | Candidate scoring, class-pool learning, escalation/downgrade, rate limiting | `router_engine/engine.py`, `selector.py`, `class_pool.py`, `health.py`, `storage/`, `rate_limiters/` |
| `route_agent.monitoring` | Side-car route observability (`record/recent/stats`) | `monitoring/service.py`, `storage.py`, `schemas.py`, `config.py` |

## 3. `route_agent.app`

Implementation paths:
- [route_agent/app/cli.py](/d:/agent/Route_Agent/route_agent/app/cli.py): `parse_args()`, `main()`
- [route_agent/app/service.py](/d:/agent/Route_Agent/route_agent/app/service.py): `run_route_agent(...)`
- [route_agent/app/wiring.py](/d:/agent/Route_Agent/route_agent/app/wiring.py): singleton wiring (`analyze_task`, `get_analysis_storage`, `get_engine`)
- [route_agent/app/payloads.py](/d:/agent/Route_Agent/route_agent/app/payloads.py): response payload construction
- [route_agent/app/legacy_analysis.py](/d:/agent/Route_Agent/route_agent/app/legacy_analysis.py): fallback heuristics (`detect_task_type`, `estimate_complexity`, `build_legacy_analysis`)

Core method flow:
- `run_route_agent(...)` validates request, loads model registry snapshot/live data, runs task analysis, builds `RouteRequest`, calls engine route, persists `routed_model`, and returns unified payload.

## 4. `route_agent.model_registry`

Implementation paths:
- Public exports: [route_agent/model_registry/__init__.py](/d:/agent/Route_Agent/route_agent/model_registry/__init__.py)
- Service orchestration: [route_agent/model_registry/service.py](/d:/agent/Route_Agent/route_agent/model_registry/service.py)
- In-memory registry: [route_agent/model_registry/registry.py](/d:/agent/Route_Agent/route_agent/model_registry/registry.py)
- Tier pool builder: [route_agent/model_registry/pool.py](/d:/agent/Route_Agent/route_agent/model_registry/pool.py)
- Provider adapter contract and factory: [providers/base.py](/d:/agent/Route_Agent/route_agent/model_registry/providers/base.py), [providers/factory.py](/d:/agent/Route_Agent/route_agent/model_registry/providers/factory.py)
- Provider implementations: [providers/vendors.py](/d:/agent/Route_Agent/route_agent/model_registry/providers/vendors.py)
- Normalization and pricing/quota policy: [providers/utils.py](/d:/agent/Route_Agent/route_agent/model_registry/providers/utils.py)
- Dynamic pricing resolver: [pricing/dynamic.py](/d:/agent/Route_Agent/route_agent/model_registry/pricing/dynamic.py)
- Storage backends: [storage/sqlite.py](/d:/agent/Route_Agent/route_agent/model_registry/storage/sqlite.py), [storage/postgres.py](/d:/agent/Route_Agent/route_agent/model_registry/storage/postgres.py)
- Optional Arena enrichment: [arena/mapper.py](/d:/agent/Route_Agent/route_agent/model_registry/arena/mapper.py), [arena/scraper.py](/d:/agent/Route_Agent/route_agent/model_registry/arena/scraper.py)

Key methods:
- `fetch_model_registry_report(...)`: direct provider fetch.
- `get_model_registry_report_with_local_pool(...)`: snapshot-first fetch with due-based refresh and fallback.
- `sync_model_registry_to_local_pool(...)`: explicit sync entry.
- `MainModelPool.from_report(...)`: build fast/smart/strategic slots from report.
- Store methods: `ensure_schema()`, `is_sync_due()`, `save_snapshot()`, `load_latest_success_snapshot()`.

## 5. `route_agent.task_analyzer`

Implementation paths:
- Public exports: [route_agent/task_analyzer/__init__.py](/d:/agent/Route_Agent/route_agent/task_analyzer/__init__.py)
- Orchestration: [analyzer.py](/d:/agent/Route_Agent/route_agent/task_analyzer/analyzer.py)
- LLM client/retry: [client.py](/d:/agent/Route_Agent/route_agent/task_analyzer/client.py)
- Prompt/schema: [prompt.py](/d:/agent/Route_Agent/route_agent/task_analyzer/prompt.py)
- Config: [config.py](/d:/agent/Route_Agent/route_agent/task_analyzer/config.py)
- Data models: [schemas.py](/d:/agent/Route_Agent/route_agent/task_analyzer/schemas.py)
- Persistence: [storage.py](/d:/agent/Route_Agent/route_agent/task_analyzer/storage.py)

Key methods:
- `analyze_async(...)`: run one analyzer model and persist analysis record.
- `analyze_with_fallback(...)`: iterate analyzer chain (`ANALYZER_CHAIN`) until success.
- `analyze(...)`: sync wrapper compatible with active event loops.
- `AnalysisStorage.save(...)`, `update_routed_model(...)`, `update_execution_result(...)`, `update_quality_review(...)`.

## 6. `route_agent.router_engine`

Implementation paths:
- Public exports: [route_agent/router_engine/__init__.py](/d:/agent/Route_Agent/route_agent/router_engine/__init__.py)
- Facade/orchestrator: [engine.py](/d:/agent/Route_Agent/route_agent/router_engine/engine.py)
- Candidate selection: [selector.py](/d:/agent/Route_Agent/route_agent/router_engine/selector.py)
- Scoring: [scorer.py](/d:/agent/Route_Agent/route_agent/router_engine/scorer.py)
- Health state: [health.py](/d:/agent/Route_Agent/route_agent/router_engine/health.py)
- Class pool/defaults: [class_pool.py](/d:/agent/Route_Agent/route_agent/router_engine/class_pool.py), [defaults.py](/d:/agent/Route_Agent/route_agent/router_engine/defaults.py)
- Escalation and downgrade: [escalation.py](/d:/agent/Route_Agent/route_agent/router_engine/escalation.py), [downgrade.py](/d:/agent/Route_Agent/route_agent/router_engine/downgrade.py)
- Rate limiters: [rate_limiters/factory.py](/d:/agent/Route_Agent/route_agent/router_engine/rate_limiters/factory.py), [rate_limiters/inmemory.py](/d:/agent/Route_Agent/route_agent/router_engine/rate_limiters/inmemory.py), [rate_limiters/redis.py](/d:/agent/Route_Agent/route_agent/router_engine/rate_limiters/redis.py)
- Storage and repos: [storage/router_storage.py](/d:/agent/Route_Agent/route_agent/router_engine/storage/router_storage.py) and wrappers in `storage/*_repo.py`

Key methods:
- `RouterEngine.route(...)` / `route_async(...)`: main selection pipeline.
- `RouterEngine.report_execution_async(...)` / `report_quality_async(...)`: post-execution feedback loop.
- `ModelSelector.select_async(...)`: filters + scores + candidate ranking.
- `HealthManager` methods: quality/execution transitions and probe loop.
- `ClassPoolManager.resolve_class_async(...)`, `record_outcome(...)`, `get_default(...)`.
- `DowngradeOptimizer` methods: trial start, canary decision, promote/rollback.
- `EscalationManager.next_action(...)`, `escalate_with_overload_check_async(...)`.

## 7. `route_agent.monitoring`

Implementation paths:
- Public exports: [route_agent/monitoring/__init__.py](/d:/agent/Route_Agent/route_agent/monitoring/__init__.py)
- Service API: [service.py](/d:/agent/Route_Agent/route_agent/monitoring/service.py)
- Storage: [storage.py](/d:/agent/Route_Agent/route_agent/monitoring/storage.py)
- Schemas: [schemas.py](/d:/agent/Route_Agent/route_agent/monitoring/schemas.py)
- Config: [config.py](/d:/agent/Route_Agent/route_agent/monitoring/config.py)

Key methods:
- `record_decision(...)` / `record_decision_async(...)`
- `get_recent_decisions(...)` / `get_recent_decisions_async(...)`
- `get_stats(...)` / `get_stats_async(...)`
- `MonitoringConfig.from_env()` for env-driven setup.

## 8. Runtime Databases

| File | Producer Module | Main Tables |
|---|---|---|
| `data/route_agent_registry.sqlite3` | `model_registry` | `model_registry_snapshots`, `model_configs`, `provider_sync_state` |
| `data/task_analysis.db` | `task_analyzer` | `analysis_records` |
| `data/router_engine.db` | `router_engine` | `class_model_stats`, `class_pool`, `class_pool_defaults`, `feedback_events`, `downgrade_trials`, `model_availability`, ... |
| `data/route_agent_monitoring.db` | `monitoring` | `monitoring_decisions` |

## 9. Related Docs

- [docs/ARCHITECTURE.md](/d:/agent/Route_Agent/docs/ARCHITECTURE.md)
- [docs/TESTING_GUIDE.md](/d:/agent/Route_Agent/docs/TESTING_GUIDE.md)
- [route_agent/model_registry/MODEL_REGISTRY.md](/d:/agent/Route_Agent/route_agent/model_registry/MODEL_REGISTRY.md)
- [route_agent/task_analyzer/TASK_ANALYZER.md](/d:/agent/Route_Agent/route_agent/task_analyzer/TASK_ANALYZER.md)
- [route_agent/router_engine/ROUTER_ENGINE.md](/d:/agent/Route_Agent/route_agent/router_engine/ROUTER_ENGINE.md)
- [route_agent/monitoring/MONITORING.md](/d:/agent/Route_Agent/route_agent/monitoring/MONITORING.md)
