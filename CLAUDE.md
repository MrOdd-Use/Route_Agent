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
- `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`
- `OLLAMA_BASE_URL` (local model provider)
- `ROUTE_AGENT_SQLITE_PATH`, `ROUTE_AGENT_POSTGRES_DSN`
- `ROUTER_DB_PATH`, `REDIS_URL`, `RATE_LIMIT_MODE`, `RATE_LIMIT_FAIL_STRATEGY`
- `ROUTE_AGENT_MONITORING_ENABLED`, `ROUTE_AGENT_MONITORING_DB_PATH`, `ROUTE_AGENT_MONITORING_RETENTION_DAYS`
- `ENABLE_DYNAMIC_PRICING`, `DYNAMIC_PRICING_TIMEOUT_SECONDS`, `DYNAMIC_PRICING_CACHE_TTL_SECONDS`
- `ENABLE_ARENA_SCORING`, `ARENA_CACHE_DB_PATH`
- `PROFILE_EMBEDDING_MODEL`, `PROFILE_STORAGE_DB_PATH`, `NEW_CLASS_FEEDBACK_TIMEOUT_S` (vector profile analyzer)

## Architecture

Main modules:
- `route_agent.app`: CLI parsing, request orchestration, payload assembly
- `route_agent.model_registry`: provider extraction, normalization, and local snapshot storage
- `route_agent.task_analyzer`: three-tier task analysis (vector profile → LLM new-class → keyword fallback)
- `route_agent.router_engine`: candidate scoring, selection, class-pool/defaults, limiter integration
- `route_agent.monitoring`: decision event record/recent/stats APIs, execution lifecycle tracking, realtime watch CLI

Reference: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Data Storage

Runtime data files are under `data/`:
- `data/route_agent_registry.sqlite3`
- `data/task_analysis.db`
- `data/router_engine.db`
- `data/route_agent_monitoring.db`
- `data/profile_embeddings.db`

## Config Templates

Planning templates live under `config/`:
- `config/models.yaml`
- `config/routing_rules.yaml`
- `config/registry_sync.yaml`

Note: current CLI/runtime path is environment-variable driven and does not auto-load these YAML files.

## Development References

- Product requirements and roadmap: [docs/PRD.md](docs/PRD.md)
- Contributor guidance: [AGENTS.md](AGENTS.md)

## Review-Before-Write Policy

### 必须遵守的写入审阅规则

对于所有会产生副作用的操作（包括但不限于 Edit、Write、Bash 中的文件修改/删除/git push/发送消息等），必须严格遵循以下流程：

1. **先展示完整内容**：在调用任何写入工具之前，先把要写入/修改的完整文本内容以 markdown 格式展示给用户阅读
2. **等待明确同意**：展示后必须等待用户明确回复同意（如"好的"、"写入"、"可以"等），才能调用 Edit/Write 工具执行写入
3. **禁止静默操作**：绝对不允许未经展示和同意就直接调用写入工具

### 覆盖范围

以下操作全部需要先展示再审批：
- `Edit` 工具：展示 old_string → new_string 的完整替换内容
- `Write` 工具：展示要写入的完整文件内容
- `Bash` 中任何有副作用的命令：文件写入、删除、git push、进程操作等
- 创建新文件
- 删除或重命名文件

### 豁免范围

以下操作无需审批，可直接执行：
- `Read`、`Glob`、`Grep`：只读操作
- `Bash` 中的只读命令：git status、git log、git diff、ls、uv run pytest 等
- `Agent` 工具：子代理研究任务

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
