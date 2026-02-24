# Testing Guide

This document explains what each test file covers and how to run it.

## 1. Quick Commands

Run full suite:

```bash
uv run pytest -q
```

Run by module:

```bash
uv run pytest -q route_agent/tests/core/
uv run pytest -q route_agent/task_analyzer/tests/
uv run pytest -q route_agent/router_engine/tests/
uv run pytest -q route_agent/monitoring/tests/
uv run pytest -q route_agent/model_registry/arena/tests/
```

Run one file:

```bash
uv run pytest -q path/to/test_file.py
```

## 2. Test File Matrix

| Test File | Module Under Test | What It Validates | How to Run |
|---|---|---|---|
| `route_agent/tests/core/test_package_exports.py` | package lazy exports (`route_agent`, `route_agent.model_registry`) | Importing package boundaries does not eagerly import heavy submodules | `uv run pytest -q route_agent/tests/core/test_package_exports.py` |
| `route_agent/tests/core/test_app_service.py` | `route_agent.app.service` and legacy fallback | `run_route_agent(...)` request validation, route request assembly, payload shape, analyzer fallback, keyword task-type fallback categories | `uv run pytest -q route_agent/tests/core/test_app_service.py` |
| `route_agent/task_analyzer/tests/test_task_analyzer_module.py` | `task_analyzer` internals (`storage`, `client`, `analyzer`) | SQLite param serialization, feedback merge behavior, retry logic, parse error wrapping, analyzer fallback chain behavior | `uv run pytest -q route_agent/task_analyzer/tests/test_task_analyzer_module.py` |
| `route_agent/router_engine/tests/test_router_engine_module.py` | `router_engine` package export | Public module exposes `route(...)` helper API | `uv run pytest -q route_agent/router_engine/tests/test_router_engine_module.py` |
| `route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py` | `router_engine` performance behavior under overlapping batches | 20-agent synthetic batches, scheduler timing drift, complete allocation, per-model RPM/concurrency limit adherence, latency/throughput summary, and monitoring dashboard snapshot (agent -> model -> status) | `uv run pytest -q route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py` |
| `route_agent/model_registry/arena/tests/test_mapper.py` | `model_registry.arena.mapper` | Score normalization, fuzzy model matching, capability fill rules, scale validation, batch fill behavior | `uv run pytest -q route_agent/model_registry/arena/tests/test_mapper.py` |
| `route_agent/model_registry/arena/tests/test_scraper.py` | `model_registry.arena.scraper` parsing helpers | Regex/HTML parsing robustness for leaderboard text and Next.js payload extraction | `uv run pytest -q route_agent/model_registry/arena/tests/test_scraper.py` |
| `route_agent/model_registry/arena/tests/test_storage.py` | `model_registry.arena.storage` | SQLite cache save/load, replacement semantics, empty leaderboard behavior, TTL expiry behavior | `uv run pytest -q route_agent/model_registry/arena/tests/test_storage.py` |
| `route_agent/monitoring/tests/test_monitoring_module.py` | `monitoring` public service API | Sync + async decision recording, execution lifecycle tracking (`start/end`), agent-model status snapshots, and dashboard rendering | `uv run pytest -q route_agent/monitoring/tests/test_monitoring_module.py` |
| `route_agent/monitoring/tests/test_watch_cli.py` | `monitoring.watch` CLI | Realtime watch CLI arg parsing and single-frame rendering path | `uv run pytest -q route_agent/monitoring/tests/test_watch_cli.py` |

## 3. Performance Test Notes

File: `route_agent/router_engine/tests/perf/test_batch_concurrency_allocation_perf.py`

- This test intentionally simulates asynchronous overlap and may run longer than regular unit tests.
- It validates behavior under synthetic load, not provider network I/O.
- It prints a JSON performance summary to stdout for inspection.

## 4. Helper Files in `tests/perf`

These are support modules used by the performance test, not standalone test entrypoints:

- `route_agent/router_engine/tests/perf/_agent_scenarios.py`
- `route_agent/router_engine/tests/perf/_perf_metrics.py`

## 5. Current Coverage Boundaries

- `model_registry/tests/` currently has no direct tests outside Arena submodule tests.
- `router_engine/tests/` currently has one API-surface test plus one performance test; many internals are exercised transitively but not yet isolated by dedicated unit tests.

If you extend behavior in these modules, add focused tests near the changed code path.
