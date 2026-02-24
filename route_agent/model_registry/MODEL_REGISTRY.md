# Model Registry Module Guide

This document explains what `route_agent/model_registry` does, how it runs, and how to extend or troubleshoot it.

## 1. Module Role

`model_registry` is the model metadata center of Route Agent. It:
1. Fetches available model lists from providers.
2. Normalizes provider-specific payloads into a shared schema (`ModelMetadata`).
3. Builds an aggregated report (`ModelRegistryReport`) with models, alerts, errors, and skipped providers.
4. Optionally persists snapshots to SQLite or PostgreSQL.
5. Supplies normalized models to `MainModelPool` for tier-based selection.

In short: this module manages model metadata and snapshot caching; it does not execute LLM inference.

---

## 2. Directory Responsibilities

Main files in `route_agent/model_registry`:
- `__init__.py`: stable public exports.
- `schemas.py`: core data structures.
- `registry.py`: in-memory registry orchestration.
- `service.py`: local-pool vs live-refresh decision flow.
- `pool.py`: fast/smart/strategic model selection logic.
- `providers/base.py`: provider adapter interface.
- `providers/factory.py`: adapter creation from environment.
- `providers/vendors.py`: provider-specific HTTP fetch implementations.
- `providers/utils.py`: shared normalization, limits, and pricing policy.
- `pricing/dynamic.py`: optional dynamic pricing fetcher.
- `storage/sqlite.py`: SQLite snapshot store.
- `storage/postgres.py`: PostgreSQL snapshot store.

---

## 3. Public Entrypoints

Use exports from `route_agent.model_registry`:
1. `fetch_model_registry_report(...)` for direct live fetch without local snapshot dependency.
2. `get_model_registry_report_with_local_pool(...)` (recommended) for snapshot-first behavior with scheduled refresh.
3. `sync_model_registry_to_local_pool(...)` for explicit sync jobs.
4. `sync_model_registry_to_postgres(...)` as postgres sync alias.

---

## 4. End-to-End Flow

1. `service.fetch_model_registry_report(...)` loads env vars and builds provider adapters from `providers/factory.py`.
2. `registry.ModelRegistry` refreshes each configured provider through `ProviderAdapter.fetch_latest_models(...)`.
3. Provider payloads are normalized into `schemas.ModelMetadata` and collected in `schemas.ModelRegistryReport`.
4. `service.get_model_registry_report_with_local_pool(...)` decides read path:
   - use latest successful local snapshot when sync is not due,
   - otherwise refresh providers and persist a new snapshot,
   - if refresh fails and an old snapshot exists, return fallback snapshot with alert.
5. `pool.MainModelPool.from_report(...)` derives tier slots (`fast/smart/strategic`) for routing.

### Local-Pool Execution Detail

For `get_model_registry_report_with_local_pool(...)`:
1. Resolve backend (`sqlite`, `postgres`, or none).
2. Create corresponding store when available.
3. If no store is available, run live fetch immediately.
4. If store exists, load latest successful snapshot and evaluate refresh due time.
5. If not due and snapshot exists, return local snapshot.
6. If due (or forced), perform provider refresh and persist snapshot.
7. If refresh fails but old snapshot exists, return fallback snapshot with alert.
8. If refresh fails and no snapshot exists, return failed live result.

---

## 5. Key Data Structures

### 5.1 `ModelMetadata`
Normalized model object with fields such as:
- `model_id`, `display_name`, `provider`, `api_model_name`
- `endpoint`, `auth`, `capabilities`, `pricing`, `limits`, `status`, `routing`

### 5.2 `SkippedProvider`
Records providers that were requested but skipped (for example missing API keys).

### 5.3 `ModelRegistryReport`
Aggregated extraction result:
- requested/configured providers
- skipped providers and errors
- normalized model list and total count
- alerts

---

## 6. Provider Layer

`ProviderAdapter` requires one method:
- `fetch_latest_models(limit: int) -> list[ModelMetadata]`

Factory behavior (`providers/factory.py`) is environment-driven:
- `OPENAI_API_KEY` -> OpenAI
- `DEEPSEEK_API_KEY` -> DeepSeek
- `GOOGLE_API_KEY` -> Google
- `ANTHROPIC_API_KEY` -> Anthropic
- `GROQ_API_KEY` -> Groq
- `include_ollama=True` -> Ollama

Missing keys produce skipped-provider records rather than hard failure.

---

## 7. Quota and Pricing Policies

Implemented in `providers/utils.py`.

### Quota policy (static)
- All models: `TPM = 250000`
- Flash tier: `RPM=10`, `RPD=60`, `max_concurrency=5`
- Pro tier: `RPM=5`, `RPD=30`, `max_concurrency=3`

### Pricing policy
- Static tier pricing is always available.
- Optional dynamic pricing is enabled by `ENABLE_DYNAMIC_PRICING`.
- Dynamic failures always fall back to static pricing.

---

## 8. Storage Schema Summary

Both SQLite and PostgreSQL keep aligned semantics:
1. `model_registry_snapshots`: snapshot metadata.
2. `model_configs`: per-snapshot model rows.
3. `provider_sync_state`: provider-level sync diagnostics.

Key behaviors:
- transactional snapshot writes
- latest successful snapshot restore
- due-time calculation for refresh
- old snapshot pruning via `keep_history`

---

## 9. Relationship to `run_route_agent`

`route_agent/main.py`:
1. obtains registry report via local-pool service,
2. builds `MainModelPool` from report,
3. selects target tier from task analysis,
4. chooses model with `pool.pick_tier(...)`.

`model_registry` determines what models are available; router logic determines which one is selected.

---

## 10. Design Rationale

- Separate layers (provider, registry, service, storage, pool) keep API calls, orchestration, persistence, and selection logic independent.
- Snapshot-first mode reduces provider dependency during runtime and stabilizes routing behavior.

### Advantages
- Deterministic data model and stable public API (`model_registry.__init__`).
- Graceful partial failure handling (skip/error/fallback).
- Switchable storage backend (`sqlite` default, `postgres` optional).

### Disadvantages
- More moving parts than direct live-fetch-only design.
- Tier inference and pricing ranking are heuristic.
- Snapshot freshness depends on sync interval and job reliability.

---

## 11. Extension Directions

- Add provider health scoring and weighted routing inputs.
- Add schema versioning/migration strategy for long-term compatibility.
- Add richer sync metrics (latency, per-provider success rate, drift alerts).
- Add policy hooks for enterprise constraints (region, compliance, allowlists).

---

## 12. Troubleshooting Checklist

1. Too few models:
- verify API keys in `.env`
- inspect `skipped_providers` and `errors`

2. No refresh happening:
- check `sync_interval_days`, `force_sync`, and `source`

3. Local DB issues:
- verify `ROUTE_AGENT_SQLITE_PATH` or `ROUTE_AGENT_POSTGRES_DSN`

4. Pricing mismatch:
- check `pricing.source` (`static` or `dynamic`)

5. Unexpected routing choice:
- inspect `preferred_model`, `max_cost`, and selected tier inputs

---

## 13. Example Test Script

```bash
.venv\Scripts\python route_agent\model_registry\tests\test_model_registry_full_dump.py --limit 8
```
