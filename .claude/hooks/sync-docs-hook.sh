#!/usr/bin/env bash
# PostToolUse hook: remind Claude to sync docs when .py files are modified
#
# Receives JSON on stdin with: tool_name, tool_input, tool_output
# If stdout is non-empty, it gets injected as user-facing feedback.

set -euo pipefail

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || true)

# Only trigger on Edit or Write tools
if [[ "$TOOL_NAME" != "Edit" && "$TOOL_NAME" != "Write" ]]; then
  exit 0
fi

FILE_PATH=$(echo "$INPUT" | python -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))" 2>/dev/null || true)

# Only trigger for Python source files under route_agent/
if [[ "$FILE_PATH" != *route_agent*.py ]]; then
  exit 0
fi

# Skip test files
if [[ "$FILE_PATH" == *test_* || "$FILE_PATH" == *tests/* ]]; then
  exit 0
fi

cat <<'MSG'
[sync-docs] route_agent/ source modified. Sync docs if needed:

Classify & act:
- Structural (add/remove/rename module/class/function) -> update docs/ARCHITECTURE.md + docs/MODULE_IMPLEMENTATION_GUIDE.md + module-level doc (e.g. MODEL_REGISTRY.md)
- Flow (request pipeline, routing logic) -> update docs/ARCHITECTURE.md "Request Flow"
- Config (env var, CLI flag) -> update CLAUDE.md "Runtime Configuration" + .env.example
- Internal-only refactor, no public API change -> skip

Rules:
- Read source before updating docs. Minimal edits only — don't rewrite unaffected sections.
- Verify cross-references between docs still valid.
- New public module without a doc? Create one following existing pattern.
- Removed module? Clean references from all docs.
MSG
