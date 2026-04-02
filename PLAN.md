# Implementation Plan: Federated Routing - Declared Agents + Local Autonomy + Central Coordination

## Problem Statement

The current routing system runs the full analysis chain on every request:

- vector profile matching
- LLM new-class determination
- legacy fallback

This works for the current single-entry workflow, but in a multi-application and high-frequency environment it creates three structural problems:

1. High latency: every route decision pays repeated analysis cost.
2. No app-level autonomy: different applications share one central route flow and cannot stably reuse their own routing knowledge.
3. No knowledge reuse for known agents: the same agent repeatedly performs the same type of work, but the system keeps re-inferring its class from scratch.

## Design Philosophy

Declared agents first: known agents use persistent `app_id + agent_name (+ agent_version) -> agent_class` knowledge for local-first routing, while unknown agents continue to use the existing inference chain.

Core principles:

- Known agents use declared or persisted mappings; unknown agents keep using the existing `vector profile -> LLM new-class -> legacy fallback` chain.
- Known agents stop paying repeated runtime class-inference cost. They use class profiles to build a lightweight `TaskAnalysisResult`.
- Agent-to-class mappings and local pool snapshots are persistent SQLite knowledge and do not use TTL.
- Active leases are temporary coordination state stored in Redis or in-memory state with TTL cleanup.
- Lease expiry must never delete historical mappings, local pool snapshots, or learned routing knowledge.
- Central coordination only has value in two cases: missing local knowledge or resource contention on a proposed model.
- If an unknown agent cannot reach the central server, it falls back to the default class `general`.
- The initial canonical class set stays unchanged in the first federation rollout.

## Architecture Overview

```text
                    +------------------------------------------------------+
                    |            Route Agent (central service)              |
                    |                                                      |
                    |  +------------------+  +--------------------------+  |
                    |  | App Registry     |  | Pool Version Manager     |  |
                    |  | declared agents  |  | class snapshot versions  |  |
                    |  +------------------+  +--------------------------+  |
                    |  +------------------+  +--------------------------+  |
                    |  | Lease Store      |  | Lease-backed RateLimiter |  |
                    |  | Redis/in-memory  |  | reuse existing protocol  |  |
                    |  +------------------+  +--------------------------+  |
                    |  +-----------------------------------------------+   |
                    |  | Mode Decision Engine (agent_class + model_id) |   |
                    |  +-----------------------------------------------+   |
                    |                                                      |
                    |  Existing compatibility API:                         |
                    |  POST /api/v1/route                                  |
                    |                                                      |
                    |  Federation API:                                     |
                    |  POST /api/v1/apps/register                          |
                    |  POST /api/v1/concurrency/acquire                    |
                    |  POST /api/v1/concurrency/release                    |
                    |  POST /api/v1/outcomes/report                        |
                    |  GET  /api/v1/pool-version                           |
                    |  GET  /api/v1/mode                                   |
                    +-----------------------------+------------------------+
                                                  |
             +------------------------------------+------------------------------------+
             |                                    |                                    |
   +-------------------------+         +-------------------------+         +-------------------------+
   | Auto Research Engine    |         | Coding Agent App        |         | App C                   |
   |                         |         |                         |         |                         |
   |  Local Store (SQLite)   |         |  Local Store (SQLite)   |         |  Local Store (SQLite)   |
   |  - agent mappings       |         |  - agent mappings       |         |  - agent mappings       |
   |  - pool snapshots       |         |  - pool snapshots       |         |  - pool snapshots       |
   |                         |         |                         |         |                         |
   |  RouteClient SDK        |         |  RouteClient SDK        |         |  RouteClient SDK        |
   +-------------------------+         +-------------------------+         +-------------------------+
```

Central service responsibilities:

- persist app registrations and declared agents
- store class pool versions
- track temporary active leases on the hot path
- decide whether a proposed model can stay local or must be centrally overridden

Client responsibilities:

- persist app-scoped agent mappings
- persist class pool snapshots
- route known agents locally first
- call the central compatibility route only for unknown agents

## Current Class Model Assumptions

The first federation rollout keeps the current canonical class set unchanged:

- `general`
- `scrape`
- `extraction`
- `summarization`
- `classification`
- `rewrite`
- `review`
- `translation`

Important implication:

- federation does not add a new canonical class in phase 1
- known agents must map to one of the existing classes
- unknown agents may still suggest new classes through the existing review queue, but that remains outside the declared-agent hot path

## Data Models

### Central Side

```python
# route_agent/federation/schemas.py

@dataclass(frozen=True)
class AppRegistration:
    app_id: str
    app_name: str
    registered_at: datetime
    last_seen_at: datetime

@dataclass(frozen=True)
class RegisteredAgent:
    app_id: str
    agent_name: str
    agent_class: str
    agent_version: str = "v1"
    registered_at: datetime
    updated_at: datetime

@dataclass(frozen=True)
class ConcurrencyLease:
    lease_id: str
    app_id: str
    agent_name: str
    agent_class: str
    proposed_model_id: str
    granted_model_id: str
    mode: str                # "local" | "central"
    acquired_at: datetime
    ttl_seconds: int = 300
    released_at: datetime | None = None

@dataclass(frozen=True)
class PoolVersionEntry:
    version: int
    agent_class: str
    model_ids: tuple[str, ...]
    updated_at: datetime

@dataclass(frozen=True)
class ModeDecision:
    agent_class: str
    model_id: str
    mode: str                # "local" | "central"
    active_leases: int
    threshold: int
    decided_at: datetime
```

### Client Side (SDK)

```python
# route_agent/federation/client/local_store.py

@dataclass(frozen=True)
class AgentMappingEntry:
    """Persistent app-scoped agent mapping. No TTL."""
    app_id: str
    agent_name: str
    agent_class: str
    agent_version: str = "v1"
    source: str = "declared"   # "declared" | "learned" | "manual"
    created_at: datetime
    updated_at: datetime

@dataclass(frozen=True)
class LocalPoolSnapshot:
    """Local ordered replica of class pool. No TTL."""
    agent_class: str
    ordered_model_ids: tuple[str, ...]
    default_model_id: str | None
    pool_version: int
    synced_at: datetime
```

## API Contracts

### POST /api/v1/apps/register

Register an application and its declared agents with the central router.

```json
// Request
{
  "app_id": "auto_research_engine",
  "app_name": "Auto Research Engine",
  "agents": [
    {
      "agent_name": "research_planner",
      "agent_class": "general",
      "agent_version": "v1"
    },
    {
      "agent_name": "web_fetcher",
      "agent_class": "scrape",
      "agent_version": "v1"
    },
    {
      "agent_name": "content_extractor",
      "agent_class": "extraction",
      "agent_version": "v1"
    },
    {
      "agent_name": "report_summarizer",
      "agent_class": "summarization",
      "agent_version": "v1"
    }
  ]
}

// Response 200
{
  "app_id": "auto_research_engine",
  "registered_at": "2026-03-31T10:00:00Z",
  "pool_versions": {
    "general": 42,
    "scrape": 38,
    "extraction": 17,
    "summarization": 21
  }
}
```

### POST /api/v1/concurrency/acquire

Acquire a lease for a locally proposed model before execution.

```json
// Request
{
  "app_id": "auto_research_engine",
  "agent_name": "report_summarizer",
  "agent_version": "v1",
  "agent_class": "summarization",
  "proposed_model_id": "deepseek-chat",
  "candidate_model_ids": ["deepseek-chat", "gemini-2.0-flash"],
  "estimated_duration_s": 30
}

// Response 200 (no contention)
{
  "lease_id": "uuid-...",
  "granted": true,
  "mode": "local",
  "granted_model_id": "deepseek-chat",
  "pool_version": 21,
  "pool_changed": false
}

// Response 200 (contention detected -> central override)
{
  "lease_id": "uuid-...",
  "granted": true,
  "mode": "central",
  "granted_model_id": "gemini-2.0-flash",
  "reason": "deepseek-chat contention (5 active leases)",
  "pool_version": 22,
  "pool_changed": true
}
```

### POST /api/v1/concurrency/release

Release a lease after execution completes.

```json
// Request
{
  "lease_id": "uuid-..."
}

// Response 200
{
  "released": true
}
```

### POST /api/v1/outcomes/report

Report execution or quality outcomes independently of lease release.

```json
// Request
{
  "lease_id": "uuid-...",
  "request_id": "uuid-...",
  "model_id": "gemini-2.0-flash",
  "agent_class": "summarization",
  "outcome_type": "exec_success",
  "duration_ms": 2340,
  "quality_score": 0.85
}

// Response 200
{
  "accepted": true
}
```

### GET /api/v1/pool-version?classes=general,scrape,summarization

Lightweight version check for pool sync.

```json
// Response 200
{
  "versions": {
    "general": 42,
    "scrape": 38,
    "summarization": 21
  }
}
```

### GET /api/v1/mode?agent_class=summarization&model_id=deepseek-chat

Query the current mode decision for one class/model pair.

```json
// Response 200
{
  "agent_class": "summarization",
  "model_id": "deepseek-chat",
  "mode": "local",
  "active_leases": 2,
  "threshold": 5
}
```

### Existing Compatibility Path

`POST /api/v1/route` remains backward compatible and also serves as the unknown-agent fallback path used by the SDK when no declared or learned mapping exists.

## Client SDK Design

```text
route_agent/federation/
├── __init__.py
├── schemas.py
├── server/
│   ├── __init__.py
│   ├── storage.py
│   ├── lease_store.py
│   ├── lease_limiter.py
│   ├── pool_version.py
│   └── app_registry.py
├── client/
│   ├── __init__.py
│   ├── route_client.py
│   ├── local_store.py
│   ├── lightweight_analysis.py
│   └── sync.py
└── api/
    ├── __init__.py
    └── routes.py
```

### RouteClient (SDK entry point)

```python
class RouteClient:
    """Lightweight SDK for federated routing."""

    def __init__(self, app_id: str, server_url: str, db_path: str):
        self._app_id = app_id
        self._server_url = server_url
        self._local_store = LocalStore(db_path)
        self._local_router = LocalRouter(self._local_store)
        self._http = httpx.AsyncClient(base_url=server_url, timeout=5.0)

    async def route(self, agent_name: str, task: str) -> RouteResult:
        # 1. app-scoped mapping lookup
        mapping = self._local_store.get_agent_mapping(self._app_id, agent_name)

        if mapping is None:
            # Unknown agent: reuse existing central inference chain
            result = await self._central_route(agent_name, task)

            if result.agent_class in CANONICAL_CLASSES:
                self._local_store.save_agent_mapping(
                    app_id=self._app_id,
                    agent_name=agent_name,
                    agent_class=result.agent_class,
                    source="learned",
                )
            return result

        # 2. Known agent: build lightweight analysis from class profile
        analysis = build_lightweight_analysis_for_class(mapping.agent_class)

        # 3. Reuse local routing semantics to build a RouteDecision
        decision = await self._local_router.route_known_agent(
            agent_name=agent_name,
            task=task,
            agent_class=mapping.agent_class,
            analysis=analysis,
        )

        # 4. Acquire lease for the locally selected primary model
        lease = await self._acquire_lease(
            agent_name=agent_name,
            agent_version=mapping.agent_version,
            agent_class=mapping.agent_class,
            proposed_model_id=decision.primary_model,
            candidate_model_ids=[c.model_id for c in decision.candidates],
        )

        # 5. Use the granted model while preserving the local candidate ladder
        return RouteResult(
            model=lease.granted_model_id,
            lease_id=lease.lease_id,
            mode=lease.mode,
            analysis=analysis,
            candidates=decision.candidates,
            local_reason=decision.reason,
        )

    async def release(self, lease_id: str) -> None:
        await self._http.post("/api/v1/concurrency/release", json={"lease_id": lease_id})

    async def report_outcome(self, lease_id: str, **payload) -> None:
        # 1. Local learning: immediate feedback into local pool (synchronous)
        self._local_router.process_outcome(lease_id, **payload)
        # 2. Central aggregation: fire-and-forget (do not await)
        asyncio.create_task(
            self._http.post("/api/v1/outcomes/report", json={"lease_id": lease_id, **payload})
        )
```

SDK behavior rules:

- lookup key is `app_id + agent_name`
- declared mappings win over learned mappings
- unknown agents continue using the current central `/route` compatibility path
- known agents skip runtime class inference only; they do not bypass local router semantics
- local known-agent routing must still preserve class-pool-first selection, controlled exploration, downgrade canary behavior, and escalation compatibility
- `acquire` applies to the locally selected `decision.primary_model`; it does not replace local routing

## Lightweight Analysis for Known Agents

The router still depends on `TaskAnalysisResult.relevant_dimensions` for scoring. Because of that, the known-agent path must not skip all analysis. It only skips runtime class inference.

Known-agent lightweight analysis should:

- set `task_class` to the known `agent_class`
- set `domain` to the class name or another stable app-defined domain string
- set `domain_description` from the existing canonical class descriptions
- derive `relevant_dimensions` from the existing class dimension profile

Important boundary:

- lightweight analysis replaces repeated class inference
- lightweight analysis does not replace local candidate selection, exploration, downgrade trials, or escalation behavior
- known-agent local routing must still build a full `RouteDecision`

## Learning, Exploration, Escalation, and Downgrade Are Preserved

Federation does not remove the current `router_engine` advantages. It only avoids repeated class inference for known agents.

The known-agent local path must continue to preserve:

- class-pool-first candidate selection with controlled exploration
- success and failure feedback into class pool learning
- default-model promotion and revocation
- downgrade canary trials, promotion, and rollback
- execution-failure escalation using the existing candidate ladder

Operational rule:

- the client does not pick a model by reading the first entry of a snapshot
- the client asks a local router adapter to build a `RouteDecision`
- `acquire` applies to `RouteDecision.primary_model`
- `report_outcome` feeds the existing execution and quality reporting semantics

## Lease-Backed Mode Decision

Per-model contention detection with configurable thresholds:

```text
active_leases(proposed_model) < threshold   -> mode = "local"
active_leases(proposed_model) >= threshold  -> mode = "central"
```

Default thresholds (configurable via env):

- Free-tier models: threshold = 3
- Rate-limited models: threshold = 2
- Unlimited local models: threshold = infinity

Implementation notes:

- federation reuses the existing `route_agent.router_engine.rate_limiters` protocol through a lease-backed implementation
- the hot path for active leases lives in Redis or in-memory state with TTL cleanup
- SQLite remains the persistent store for registrations, pool versions, and audit history
- this avoids introducing a second parallel concurrency subsystem
- mode decisions are scoped to `agent_class + model_id`, not class-only
- local federation routing still relies on current router semantics instead of replacing them with snapshot-first fixed model selection

## Feedback Learning: Dual-Write Model

Federation must preserve the existing quality feedback loop. The design uses a dual-write model: local learning for immediate quality protection, central aggregation for cross-app knowledge sharing.

### Feedback Flow

```text
Execution completes
  │
  ├──→ Local: local_router processes outcome synchronously (0ms)
  │      ├─ class pool success/failure feedback → pool reorder
  │      ├─ canary trial judgment → promote / rollback
  │      └─ exploration result judgment → admit to pool / discard
  │
  └──→ Remote: report_outcome() fire-and-forget to central (async, non-blocking)
         └─ Central outcome_processor aggregates across all apps
         └─ Statistically significant signal → global pool reorder → version bump
         └─ Clients pull updated baseline on next sync cycle
```

### Design Rules

- Local learning is the first line of quality defense. Feedback takes effect immediately without network dependency.
- Central aggregation is the cross-app knowledge sharing channel. It solves the small-sample problem for individual apps.
- `report_outcome()` is fire-and-forget. It must not block the execution chain.
- If the central server is unreachable, local learning continues uninterrupted.

### Pool Sync Merge Strategy

When a new pool version arrives from central, the client must not blindly overwrite local state. Instead it applies a weighted merge:

```text
On pool version update:
  1. Central global ordering → new baseline
  2. Local recent outcomes (last N executions) → local adjustment overlay
  3. Merge: final_score = α × central_score + (1 − α) × local_score
     α decreases as local sample count grows (more local experience → more local trust)
```

Cold-start behavior: a new app with no local history uses α ≈ 1.0 (fully trusts central). As local outcomes accumulate, α decays toward a floor (e.g., 0.3), giving the app personalized adaptation while still benefiting from global signals.

### Outcome Processor (Central Side)

The central `outcome_processor` receives reported outcomes and:

1. Accumulates per-class per-model success/failure/quality metrics
2. When a model's recent sample count crosses a significance threshold (configurable, default 20), re-evaluates its position in the class pool
3. Triggers `class_pool` reorder and `pool_version` bump on meaningful rank changes
4. Emits monitoring events for dashboard visibility

This closes the gap between `POST /api/v1/outcomes/report` accepting the payload and the class pool actually updating.

## Implementation Phases

### Phase 0: App-layer extraction for federation reuse

Files to create/modify:

- MODIFY `route_agent/app/analysis.py` -> expose a lightweight analysis builder for known classes
- MODIFY `route_agent/app/orchestrator.py` -> split full-route path from route-with-analysis path
- MODIFY `route_agent/router_engine/engine.py` -> make local route-with-known-class reuse explicit
- MODIFY `route_agent/router_engine/class_pool.py` -> promote class-profile-to-dimensions helper for reuse

Tests:

- `route_agent/tests/core/test_app_analysis.py`
- `route_agent/tests/core/test_app_orchestrator.py`

### Phase 1: Federation schemas + persistent storage

Files to create/modify:

- NEW `route_agent/federation/__init__.py`
- NEW `route_agent/federation/schemas.py`
- NEW `route_agent/federation/server/storage.py` -> SQLite tables: `apps`, `registered_agents`, `pool_versions`
- NEW `route_agent/federation/server/__init__.py`

Tests:

- `route_agent/federation/tests/test_schemas.py`
- `route_agent/federation/tests/test_storage.py`

### Phase 2: Hot-path lease store + lease-backed rate limiter + baseline metrics

Files to create/modify:

- NEW `route_agent/federation/server/lease_store.py` -> Redis/in-memory active lease state + TTL cleanup
- NEW `route_agent/federation/server/lease_limiter.py` -> reuse the existing `RateLimiter` protocol
- NEW `route_agent/federation/server/app_registry.py`
- NEW `route_agent/federation/server/pool_version.py`
- MODIFY `route_agent/router_engine/class_pool.py` -> emit version bumps on pool change
- MODIFY `route_agent/monitoring/service.py` -> add federation counters and event types early
- NEW `route_agent/federation/server/outcome_processor.py` -> aggregate outcomes across apps, trigger class_pool reorder + version bump on statistically significant signal

Tests:

- `route_agent/federation/tests/test_lease_store.py`
- `route_agent/federation/tests/test_lease_limiter.py`
- `route_agent/federation/tests/test_pool_version.py`
- `route_agent/federation/tests/test_outcome_processor.py`

### Phase 3: Federation API routes

Files to create/modify:

- NEW `route_agent/federation/api/__init__.py`
- NEW `route_agent/federation/api/routes.py` -> `apps/register`, `concurrency/acquire`, `concurrency/release`, `outcomes/report`, `pool-version`, `mode`
- MODIFY `route_agent/api/main.py` -> mount federation router
- MODIFY `route_agent/api/schemas.py` -> federation request/response models

Tests:

- `route_agent/federation/tests/test_api.py`

### Phase 4: Client SDK + known-agent lightweight path

Files to create/modify:

- NEW `route_agent/federation/client/__init__.py`
- NEW `route_agent/federation/client/route_client.py`
- NEW `route_agent/federation/client/local_store.py`
- NEW `route_agent/federation/client/local_router.py` -> reuse local router semantics for known agents; process execution outcomes synchronously into local pool (success/failure feedback, canary judgment, exploration admission)
- NEW `route_agent/federation/client/lightweight_analysis.py`
- NEW `route_agent/federation/client/sync.py` -> periodic pool version check + weighted merge (α-blend central baseline with local adjustments, α decays with local sample count)

Tests:

- `route_agent/federation/tests/test_route_client.py`
- `route_agent/federation/tests/test_local_store.py`

### Phase 5: Integration with Auto Research Engine

Files to modify (in `d:\agent\Auto_Research_Engine`):

- add `route_agent` SDK dependency
- register declared agents at startup
- replace direct model selection with `RouteClient.route()`
- wire `release()` and `report_outcome()` into execution callbacks

This phase validates the complete federated loop end-to-end with a declared-agent application.

### Phase 6: Dashboard and observability polish

Files to create/modify:

- NEW `route_agent/federation/api/metrics.py` -> `GET /api/v1/federation/metrics` 端点，从
  `LeaseStore` 读取每个模型的 `active_count + threshold`（利用率），从 `FederationStorage` +
  `AppRegistry` 读取每个 app 的 declared/learned mapping 数量；不引入独立 metrics 模块
- MODIFY `route_agent/federation/api/routes.py` -> 挂载 metrics 路由
- MODIFY `route_agent/api/routes/dashboard.py` -> 新增第四个 tab "联邦状态 Federation"，
  JS 调用 `/api/v1/federation/metrics`，展示：
    - 各模型租约利用率进度条（active / threshold）
    - 各 app 已注册 agent 数（declared + learned），反映 federation 覆盖程度

Tests:

- `route_agent/federation/tests/test_metrics_api.py`

## Migration Strategy

1. Backward compatible: existing CLI and `POST /api/v1/route` continue to work unchanged.
2. Opt-in federation: applications only use federation when they instantiate `RouteClient`.
3. Declared agents first: registered agents use declared mappings immediately; unknown agents keep using the existing analyzer chain.
4. No knowledge loss: lease expiry only clears hot-path concurrency state; historical mappings and pool snapshots remain persisted in SQLite.
5. Initial class set stays unchanged: first rollout reuses the existing canonical classes.

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Central server down | Known agents fall back to local mapping + local pool snapshot; unknown agents fall back to default class `general` |
| Lease leak (client crash) | TTL-based auto-expiry in Redis or in-memory hot state |
| Historical knowledge loss | Persistent mappings and pool snapshots live in SQLite and are never deleted by lease cleanup |
| Pool version drift | Periodic background sync in client SDK (every 60s) |
| Acquire hot-path saturation | Keep active leases in Redis or in-memory hot state; keep SQLite off the hot lease path |
| Unknown-agent repeated analysis | Persist learned mappings locally after the first successful central resolution |
| Granularity mismatch | Mode decisions are scoped to `agent_class + model_id`, not class-only |
| Feedback-learning regression | Dual-write model: local outcome processing for immediate quality protection + central aggregation for cross-app learning; pool sync uses weighted merge (α-blend) instead of overwrite |

## Success Metrics

- Known-agent class-resolution latency: < 1ms from local store
- Acquire decision latency (p95): < 15ms in normal deployment
- Central override latency (p95): < 50ms
- Pool sync freshness: < 60s lag
- Zero permanently leaked active leases after client crash
- Federation traffic continues to produce class-pool learning and default-promotion signals
- Downgrade canary, promotion, and rollback behavior remain active under federated traffic
- 80%+ test coverage on federation module
