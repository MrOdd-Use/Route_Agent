# Route Agent

Route Agent is a model-routing control plane for multi-agent systems. It decides which model should handle a request by balancing task difficulty, model capability, price, health, and recent execution history.

It is built for teams that want model choice to behave like infrastructure instead of a pile of one-off heuristics. Route Agent does not proxy inference. Its job is to make a good routing decision, explain that decision, and improve the next one through feedback.

## Why Route Agent

Real agent workloads are uneven.

- Some requests are cheap and mechanical.
- Some need stronger reasoning or better code generation.
- Some are quality-sensitive but still need cost control.
- Some arrive exactly when the best model is overloaded or unhealthy.

Route Agent automatically assigns models based on task characteristics: stronger large models for complex coding tasks, cost-effective smaller models for lighter tasks like summarization and classification, and vertical models for specialized domains. This turns model selection into a measurable subsystem with memory, policy, and operational safeguards — ensuring quality while minimizing overall cost.

## Quick Start

### Prerequisites

- Python 3.11
- `uv`
- At least one provider API key, or a reachable Ollama instance for local-only metadata

### 1. Install dependencies

```bash
uv sync --dev
```

### 2. Create `.env`

On Unix-like shells:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Then fill in only the provider keys you plan to use.

### 3. Route one request from the CLI

```bash
uv run python -m route_agent --agent-name release-bot --system-prompt "You are a release engineering agent. Read product changes, summarize key updates, and produce concise release notes for internal and external audiences." --task "Summarize the latest changelog into release notes"
```

### 4. Start the API

```bash
uv run python -m route_agent --serve
```

Windows PowerShell startup when `.venv` is already synced:

```powershell
.\.venv\Scripts\python.exe -m route_agent --serve
```

### 5. Open the dashboard

```text
http://localhost:8000/api/v1/dashboard
```

## When Route Agent Helps

Route Agent is especially useful when one system prompt or one agent class can receive tasks with very different cost and quality profiles.

Typical examples:

- A code review agent that should stay cheap for routine lint-like checks but escalate for concurrency, correctness, or architecture-heavy reviews.
- A release-note or documentation agent that usually works well on a lighter model but should preserve a stronger fallback for harder synthesis tasks.
- An extraction or classification agent running at volume, where small pricing differences matter and gradual downgrade can save meaningful cost.
- A multi-agent platform that wants routing decisions to remain understandable and observable instead of being buried inside prompt logic.

## What Route Agent Does

- Reads a stable local snapshot of the model universe instead of depending on live provider calls for every route.
- Turns raw requests into routing signals through a three-tier analysis pipeline (vector profile → LLM new-class → keyword fallback).
- Filters models against hard constraints like provider, budget, exclusions, and context window.
- Builds an ordered candidate set instead of choosing a single model in one shot.
- Learns class-specific preferences from execution and quality feedback.
- Protects the system from unhealthy or overloaded models before they become repeated incidents.
- Creates a feedback loop for both promotion to stronger defaults and safe downgrade to cheaper challengers.

## How Route Agent Thinks

### 1. Start from a stable model universe

The router begins with a local model snapshot. That keeps routing fast and predictable, even when a provider refresh is slow or temporarily failing. Provider sync still exists, but it does not have to sit on the hot path of every decision.

### 2. Analyze the request through a three-tier pipeline

The current request shape is `agent_name + system_prompt + task`. Route Agent runs a three-tier analysis chain to turn that into structured routing signals:

1. **Vector profile matching** — the task input is embedded via a local Ollama model (`nomic-embed-text`) and matched against class-pool description embeddings using cosine similarity. When the top match exceeds the threshold (default 0.6), the system uses predefined dimension scores for that class, completing analysis with zero LLM calls.
2. **LLM new-class determination** — when the vector match misses, an LLM judges whether the task belongs to an existing class or suggests creating a new class pool. Suggested new classes are written to a review queue.
3. **Legacy keyword heuristic** — if both the vector analyzer and LLM fail, a simple keyword-based fallback assigns task type and complexity.

The result is a structured analysis containing domain, task class, and weighted dimensions like reasoning, coding, math, or instruction following. Those dimensions become the raw inputs for scoring.

### 3. Build the global candidate base

Before any class-specific learning is applied, the router removes models that should not even be considered. A model is filtered out if it is unavailable, already rate-limited, explicitly excluded, outside the required provider, above the cost ceiling, or too small for the estimated prompt size.

What survives becomes the global candidate base. Each surviving model receives:

- a capability-match score from the task dimensions
- a cost score from effective pricing
- a health-aware adjustment from recent success or failure history
- an overload penalty when the model is close to throughput or concurrency pressure

### 4. Resolve the agent class

Route Agent groups learning by agent class. In the current implementation, resolution follows this order:

1. an explicit `agent_class` override if the caller sends one
2. a vector-profile match if the task embedding is close enough to a known class description
3. an LLM new-class determination if the vector match misses
4. the analyzer's `task_class` if it matches the controlled vocabulary
5. the fallback class `general`

Each task class has a detailed natural-language description and a predefined dimension profile. These descriptions power both the vector-profile matching and the LLM prompt context.

This class boundary matters because a release-note agent and a code-review agent should not silently train the same routing baseline.

### 5. Compose a final candidate set, then choose where to start

The router separates two ideas:

- which models belong in the final ordered candidate set
- which model should be tried first for this request

That distinction is what makes Route Agent more flexible than a simple "top-1 score wins" rule.

## Candidate Set Lifecycle

One of the most important parts of Route Agent is that the candidate set changes as a class matures.

### Cold start

When a class has no meaningful history yet, Route Agent relies on the global ranking and keeps up to five diverse candidates. The goal is not to pretend that the system already knows the best class-specific answer. The goal is to start with a reasonable spread and gather evidence safely.

### Early learning

Once a class begins to accumulate successful outcomes, Route Agent starts building a class pool. In this phase the candidate set becomes a blend of three groups:

- one ceiling model, which preserves awareness of the strongest known option
- models already proven inside the class pool
- several exploration slots from outside the pool

This phase is intentionally more exploratory. The router is still discovering which models truly belong to the class.

### Stable operation

When the class pool becomes richer, the candidate set narrows. The ceiling model still remains so the router never loses sight of the quality ceiling, but more slots are reserved for pool members and fewer are spent on exploration.

At this point Route Agent starts behaving less like a fresh install and more like a system that understands this class's traffic.

### Downgrade trial

If the current default has been stable for long enough, Route Agent may test a cheaper challenger that already looks close enough on quality. The candidate ordering stays intact, but sampled requests can start from the challenger instead of the incumbent.

This is not a silent permanent switch. It is a canary phase whose purpose is to answer one question: can we keep the quality bar while paying less?

### Escalation

If execution later shows that the current answer is not good enough, Route Agent climbs upward inside the same ordered candidate set toward stronger models. It does not have to recompute the world from scratch. It moves through a pre-ranked path and skips targets that are unhealthy or too overloaded.

## Class Pools 

The class pool is the core learning mechanism of Route Agent.

Think of it as a shortlist that each agent class gradually builds for itself. A model enters that shortlist only after it has shown repeatable value. Once it is inside, future requests of the same class treat it as a known-good option rather than a stranger.

This solves a practical problem: global model metadata is useful, but it does not know your prompts, your agents, or your tolerance for mistakes. The class pool adds local memory on top of global metadata.

Important characteristics:

- Pool membership is confidence-driven, not one-shot.
- Pool bonuses are intentionally modest, so history helps without fully dominating present capability.
- The pool is capped, so low-value or stale members can be pushed out.
- Models marked as unavailable do not remain inside the pool as dead weight.
- In the current settings, one class pool can hold up to 10 models.
- The current implementation keeps defaults at the class level rather than a separate default per domain.

## Why Models Move Up or Down

### Joining the class pool

A model joins the class pool only after it has enough successful evidence to clear a confidence floor. One lucky response should not create long-term routing privilege.

### Becoming the class default

Becoming the default is harder than merely entering the pool. A challenger needs enough successful history, a better conservative success estimate than the incumbent, and a sustained lead instead of a one-request spike. When two models are close on quality, Route Agent breaks ties in favor of lower cost and then newer release recency.

In the current policy, normal promotion expects at least 20 successful examples and a real winning streak before a challenger replaces the incumbent. That is deliberate: the default is the model the system is willing to trust first.

### Losing default status

Default status is revoked quickly after repeated quality failures. A default exists to be the safe starting point. If it stops being safe, the system should not keep honoring it out of inertia.

Today that revocation happens after three poor-quality feedback events rather than waiting for a long statistical decay. The router prefers to lose confidence quickly and relearn.

### Downgrading to a cheaper model

Downgrade does not mean "pick the cheaper model because it is cheaper." It means "the current default has been stable long enough that we can safely test whether a cheaper alternative is good enough."

In the current policy, the incumbent default needs 10 consecutive successes, the challenger needs to look close enough on quality, and the expected savings need to be at least 10% before a trial begins.

Trial traffic is sent as a canary instead of a full switch. Route Agent currently samples about half of eligible traffic during that trial, can promote with a lower bar than a cold default change once the challenger has enough successful evidence, and rolls back quickly after 2 quality failures or 1 execution failure.

This exists for one reason: cost optimization should be reversible.

### Escalating to a stronger model

Escalation exists for the opposite reason. Sometimes the current choice was reasonable, execution succeeded, and the output is still not good enough. In that case Route Agent climbs toward a stronger candidate from the same ordered set.

It does not escalate blindly. Targets that are unhealthy, rate-limited, or already saturated are skipped. That prevents "save quality at any cost" from turning into "stampede the most expensive model and create a second outage."

### Health demotion

Repeated quality failures can push a model into a degraded state. Repeated execution failures can push it out of the candidate universe entirely until recovery probes succeed. Recovered models do not immediately return at full trust; they come back carefully.

This is one of the main reasons Route Agent remains stable under provider incidents: a model can lose routing privilege before users keep paying the price for it.

## What You Get Back

Both CLI and API return the same routing payload. The most important fields are:

- `model_used`: the starting model for this request
- `routing_reason`: a short explanation of why routing started there
- `candidates`: the ordered candidate set considered for this route
- `analysis`: the structured task-analysis result
- `pool_summary`: a summary of the current model pool
- `alerts`, `registry_alerts`, and `registry_errors`: diagnostics about constraints, registry freshness, and provider state
- `rate_limiter`: live limiter mode and routing-time pressure signals

## CLI Usage Notes

The current route input shape is `agent_name + system_prompt + task`.

- `task` is the required request text and the primary analyzer input.
- `system_prompt` adds role context that helps the analyzer understand what kind of agent is asking.
- `max_cost`, `preferred_model`, `exclude_models`, and `require_provider` narrow the candidate space before ranking.
- `estimated_input_tokens` helps reject models with too little effective context window.
- `force_registry_sync` refreshes provider metadata immediately instead of waiting for the normal snapshot schedule.
- `rate-limit` controls limiter mode: `auto`, `redis`, `inmemory`, or `off`.
- `rate-limit-fail-strategy` controls whether `auto` mode degrades gracefully or fails fast when Redis is unavailable.

Constraint-aware example:

```bash
uv run python -m route_agent --agent-name code-reviewer --system-prompt "You are a senior Python code review agent. Focus on concurrency bugs, race conditions, and correctness risks." --task "Review this async Python function for race conditions" --max-cost 0.03 --require-provider openai --estimated-input-tokens 2400
```

## API Service

Route Agent ships a REST API alongside the CLI entrypoint.

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/route` | Route a task and return the full routing payload |
| `GET` | `/api/v1/models` | List current models in the active pool |
| `GET` | `/api/v1/stats` | Read monitoring statistics |
| `GET` | `/api/v1/health` | Health check |
| `GET` | `/api/v1/dashboard` | Open the dashboard UI |
| `GET` | `/api/v1/pool-status/global` | Read the global model-card view |
| `GET` | `/api/v1/pool-status/classes` | Read the class-pool directory view (includes class descriptions) |

Start the API with:

```bash
uv run python -m route_agent --serve
```

Example request:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/route \
  -H "Content-Type: application/json" \
  -d '{
    "agent_name": "release-bot",
    "system_prompt": "You are a release engineering agent. Convert raw product changes into clear release notes for stakeholders.",
    "task": "Summarize this product spec into release notes",
    "request_id": "8c3b7f16-5a3b-4af5-9c8a-3fc9bbeb0a12",
    "constraints": {
      "max_cost": 0.02,
      "require_provider": "google",
      "estimated_input_tokens": 1800
    }
  }'
```

On Windows PowerShell:

```powershell
$body = @{
  agent_name = "release-bot"
  system_prompt = "You are a release engineering agent. Convert raw product changes into clear release notes for stakeholders."
  task = "Summarize this product spec into release notes"
  request_id = "8c3b7f16-5a3b-4af5-9c8a-3fc9bbeb0a12"
  constraints = @{
    max_cost = 0.02
    require_provider = "google"
    estimated_input_tokens = 1800
  }
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/v1/route" -ContentType "application/json" -Body $body
```

## Configuration

Runtime configuration is environment-variable driven.

Provider access:

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI API key |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `GOOGLE_API_KEY` | Google Gemini API key |
| `GEMINI_API_KEY` | Alternative Gemini API key variable |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GROQ_API_KEY` | Groq API key |
| `OLLAMA_BASE_URL` | Local Ollama base URL |

Storage and runtime state:

| Variable | Description |
|---|---|
| `ROUTE_AGENT_SQLITE_PATH` | SQLite path for registry snapshots |
| `ROUTER_DB_PATH` | SQLite path for router state |
| `ROUTE_AGENT_POSTGRES_DSN` | Optional PostgreSQL DSN for shared registry storage |
| `REDIS_URL` | Optional Redis URL for the router limiter |
| `RATE_LIMIT_MODE` | Limiter mode: `auto`, `redis`, `inmemory`, `off` |
| `RATE_LIMIT_FAIL_STRATEGY` | Auto-mode behavior: `degrade` or `fail_fast` |
| `ROUTE_AGENT_MONITORING_ENABLED` | Enable monitoring sidecar |

Vector profile analyzer:

| Variable | Description |
|---|---|
| `PROFILE_EMBEDDING_MODEL` | Ollama embedding model (default: `nomic-embed-text`) |
| `PROFILE_MATCH_THRESHOLD` | Cosine similarity threshold for profile matching (default: `0.6`, set in code) |
| `PROFILE_STORAGE_DB_PATH` | SQLite path for embedding cache (default: `data/profile_embeddings.db`) |
| `NEW_CLASS_FEEDBACK_TIMEOUT_S` | Timeout for new-class feedback before LLM auto-assigns (default: `0`) |

Optional enrichment:

| Variable | Description |
|---|---|
| `ENABLE_DYNAMIC_PRICING` | Enable dynamic pricing fetcher (`0` or `1`) |
| `ENABLE_ARENA_SCORING` | Enable external leaderboard enrichment (`0` or `1`) |

## Monitoring and Persistence

Route Agent is SQLite-first by default. It keeps local state for registry snapshots, structured task analysis, router learning state, downgrade trials, and monitoring events. That keeps local development simple while still leaving room for shared deployments.

The monitoring layer is meant to answer three practical questions:

- Why did this route happen?
- What happened after the route?
- Is the class pool getting better or drifting in the wrong direction?

For a terminal-oriented live view:

```bash
uv run python -m route_agent.monitoring.watch --interval 1 --source router_engine_perf_test
```

## Comparison with Other Products

See [docs/COMPARISON.md](docs/COMPARISON.md) for a detailed comparison of Route Agent with other LLM routing products including Martian, Not Diamond, OpenRouter, Unify AI, LiteLLM, RouteLLM, Higress, and Alibaba Cloud AI Gateway.

## Development

Run the full test suite:

```bash
uv run pytest -q
```

Useful commands during development:

```bash
uv run python -m route_agent --task "Write a Python hello world script"
uv run python -m route_agent --serve
```
