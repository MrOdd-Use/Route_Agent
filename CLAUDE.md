# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Route Agent** is an intelligent LLM routing system that automatically analyzes task characteristics and assigns the most suitable model. The goal is to optimize cost and efficiency by routing complex tasks to powerful models and simple tasks to lightweight models.

**Key Concept**: Dynamic model selection based on task type, complexity, and cost constraints.

## Environment Setup

The project uses a Python virtual environment (`.venv`):

```bash
# Activate the virtual environment
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

## Running Tests

Tests use pytest with async support. Tests live under each module's `tests/` subdirectory:

```bash
# Run all tests
pytest

# Run specific module tests
pytest route_agent/model_registry/tests/
pytest route_agent/task_analyzer/tests/

# Run with verbose output
pytest -v
```

## Environment Configuration

The `.env` file contains API keys and configuration for:
- **LLM Providers**: DeepSeek, Google Gemini, OpenAI, Anthropic, Groq, Ollama
- **Search**: Tavily API
- **Embeddings**: Ollama with nomic-embed-text
- **Document Path**: `DOC_PATH` specifies where research documents are stored

### Key Environment Variables

- `FAST_LLM` / `SMART_LLM` / `STRATEGIC_LLM`: Model selection in format `provider:model` (e.g., `deepseek:deepseek-chat`)
- `RETRIEVER`: Comma-separated list of retrievers (tavily, mcp)
- `OLLAMA_BASE_URL`: Local Ollama instance URL
- `EMBEDDING`: Embedding model for vector search

## Architecture

This project uses:
- **LangChain**: LLM orchestration framework
- **LangGraph**: Stateful agent workflows with checkpointing
- **FastAPI**: REST API server
- **MCP (Model Context Protocol)**: Tool integration layer

The system consists of four main components:

1. **Model Registry** (✅ implemented): Manages model capabilities, costs, and availability across 6 providers
2. **Task Analyzer** (✅ implemented): LLM-based analysis of task type, complexity, and capability dimensions
3. **Router Engine** (🔲 placeholder): Routes tasks based on rules, semantic similarity, or cost constraints (currently inline in `main.py`)
4. **Monitor** (🔲 placeholder): Tracks routing decisions, costs, and performance metrics (currently inline diagnostics in response)

### Routing Flow

```
Task Input  Task Analysis  Router Decision  Model Execution  Response
                                                          
           (type, complexity)                    (model used, cost)
```

## Data Storage

All runtime data files are stored in the `data/` directory:
- `data/route_agent_registry.sqlite3` — Model registry snapshots (SQLite)
- `data/task_analysis.db` — Task analysis records

Override paths via environment variables: `ROUTE_AGENT_SQLITE_PATH`, `ROUTE_AGENT_POSTGRES_DSN`.

## Configuration

Models are configured with:
- **Capabilities**: reasoning, coding, math, context_length scores
- **Costs**: input/output token pricing
- **Constraints**: max tokens, rate limits

See `models.yaml` for model configuration and `routing_rules.yaml` for routing logic (not yet created — see [PRD.md](PRD.md) for example configs).

## Development

See [PRD.md](PRD.md) for detailed product requirements and development roadmap.

---

## Working Guidelines for Claude

### When to Explore the Codebase

**DO explore when:**
- User asks questions like "How does X work->", "Where is Y handled->", "What's the architecture->"
- Bug fixing - need to find where an error originates
- Refactoring - need to understand existing patterns before changing
- User says "research", "investigate", "understand", "explore"

**DO NOT explore when:**
- Creating new files that don't depend on existing code
- Simple, self-contained tasks (e.g., "create a config", "add a util function")
- User gives clear, specific instructions
- The task is straightforward and well-defined

### Quick Decision Flow

```
Task unclear->  Yes  Explore
Task clear and simple->  Yes  Just do it
Modifying existing code->  Read only the relevant files
Creating new independent code->  No exploration needed
```

### General Principles

1. **Prefer direct action** - When in doubt, act rather than explore
2. **Minimize tool calls** - Each tool call adds latency
3. **Ask clarifying questions** instead of exploring when requirements are vague
4. **Read only what you need** - Don't glob/grep the entire project for simple tasks
5. **No unnecessary questions** - If the task is clear, just execute
