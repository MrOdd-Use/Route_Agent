"""Unit tests for task_analyzer module behaviors."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

TASK_ANALYZER_DIR = Path(__file__).resolve().parents[1]
ROUTE_AGENT_DIR = TASK_ANALYZER_DIR.parent


def _load_module(module_name: str, file_path: Path) -> types.ModuleType:
    """Execute `_load_module`."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module spec for {module_name}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def task_analyzer_modules() -> dict[str, types.ModuleType]:
    """Load task_analyzer modules without importing route_agent/__init__.py."""
    injected_names: list[str] = []

    route_pkg = types.ModuleType("route_agent")
    route_pkg.__path__ = [str(ROUTE_AGENT_DIR)]  # type: ignore[attr-defined]
    sys.modules["route_agent"] = route_pkg
    injected_names.append("route_agent")

    sub_pkg = types.ModuleType("route_agent.task_analyzer")
    sub_pkg.__path__ = [str(TASK_ANALYZER_DIR)]  # type: ignore[attr-defined]
    sys.modules["route_agent.task_analyzer"] = sub_pkg
    injected_names.append("route_agent.task_analyzer")

    modules = {
        "config": _load_module(
            "route_agent.task_analyzer.config",
            TASK_ANALYZER_DIR / "config.py",
        ),
        "schemas": _load_module(
            "route_agent.task_analyzer.schemas",
            TASK_ANALYZER_DIR / "schemas.py",
        ),
        "storage": _load_module(
            "route_agent.task_analyzer.storage",
            TASK_ANALYZER_DIR / "storage.py",
        ),
        "prompt": _load_module(
            "route_agent.task_analyzer.prompt",
            TASK_ANALYZER_DIR / "prompt.py",
        ),
        "client": _load_module(
            "route_agent.task_analyzer.client",
            TASK_ANALYZER_DIR / "client.py",
        ),
        "analyzer": _load_module(
            "route_agent.task_analyzer.analyzer",
            TASK_ANALYZER_DIR / "analyzer.py",
        ),
    }
    injected_names.extend(
        [
            "route_agent.task_analyzer.config",
            "route_agent.task_analyzer.schemas",
            "route_agent.task_analyzer.storage",
            "route_agent.task_analyzer.prompt",
            "route_agent.task_analyzer.client",
            "route_agent.task_analyzer.analyzer",
        ]
    )

    try:
        yield modules
    finally:
        for name in reversed(injected_names):
            sys.modules.pop(name, None)


def _inject_langchain_core_exceptions() -> None:
    """Execute `_inject_langchain_core_exceptions`."""
    langchain_core_pkg = types.ModuleType("langchain_core")
    langchain_core_pkg.__path__ = []  # type: ignore[attr-defined]
    exceptions_mod = types.ModuleType("langchain_core.exceptions")

    class OutputParserException(Exception):
        """Represent `OutputParserException`."""
        pass

    exceptions_mod.OutputParserException = OutputParserException
    sys.modules["langchain_core"] = langchain_core_pkg
    sys.modules["langchain_core.exceptions"] = exceptions_mod


def _inject_langchain_core_messages() -> None:
    """Execute `_inject_langchain_core_messages`."""
    langchain_core_pkg = sys.modules.get("langchain_core", types.ModuleType("langchain_core"))
    langchain_core_pkg.__path__ = []  # type: ignore[attr-defined]
    messages_mod = types.ModuleType("langchain_core.messages")

    class _Msg:
        """Represent `_Msg`."""
        def __init__(self, content: str):
            """Initialize the instance."""
            self.content = content

    messages_mod.SystemMessage = _Msg
    messages_mod.HumanMessage = _Msg
    sys.modules["langchain_core"] = langchain_core_pkg
    sys.modules["langchain_core.messages"] = messages_mod


def test_record_to_params_preserves_empty_collections(task_analyzer_modules: dict[str, types.ModuleType]) -> None:
    """Test record to params preserves empty collections."""
    storage = task_analyzer_modules["storage"]
    record = storage.AnalysisRecord(
        agent_name="agent",
        prompt="task",
        domain="domain",
        dimensions=(),
        analyzer_model="m1",
        routed_model=None,
        success=True,
        token_usage={},
        response_time_ms=12,
    )

    params = storage._record_to_params(record)
    assert params[3] == "[]"
    assert params[7] == "{}"


def test_update_execution_result_preserves_quality(
    task_analyzer_modules: dict[str, types.ModuleType],
    tmp_path: Path,
) -> None:
    """Test update execution result preserves quality."""
    storage_mod = task_analyzer_modules["storage"]
    db_path = tmp_path / "analysis.db"
    store = storage_mod.AnalysisStorage(db_path=db_path)

    record_id = store.save(
        storage_mod.AnalysisRecord(
            agent_name="agent",
            prompt="task",
            domain="domain",
            dimensions=(),
            analyzer_model="m1",
            routed_model=None,
            success=True,
        )
    )
    store.update_quality_review(record_id, rating="good", action="keep", note="manual pass")
    store.update_execution_result(record_id, completed=True)

    with sqlite3.connect(db_path) as conn:
        raw_feedback = conn.execute(
            "SELECT feedback FROM analysis_records WHERE id = ?",
            (record_id,),
        ).fetchone()[0]

    feedback = json.loads(raw_feedback)
    assert feedback["execution"]["completed"] is True
    assert feedback["quality"]["rating"] == "good"


def test_ainvoke_with_retry_validates_parameters(
    task_analyzer_modules: dict[str, types.ModuleType],
) -> None:
    """Test ainvoke with retry validates parameters."""
    _inject_langchain_core_exceptions()
    client = task_analyzer_modules["client"]

    class _Chain:
        """Represent `_Chain`."""
        async def ainvoke(self, _prompt: Any) -> Any:
            """Execute `ainvoke`."""
            return {"ok": True}

    with pytest.raises(ValueError, match="max_attempts"):
        asyncio.run(client.ainvoke_with_retry(_Chain(), "x", max_attempts=0))

    with pytest.raises(ValueError, match="backoff_seconds"):
        asyncio.run(client.ainvoke_with_retry(_Chain(), "x", backoff_seconds=-1))


def test_ainvoke_with_retry_retries_then_succeeds(
    task_analyzer_modules: dict[str, types.ModuleType],
) -> None:
    """Test ainvoke with retry retries then succeeds."""
    _inject_langchain_core_exceptions()
    exceptions_mod = sys.modules["langchain_core.exceptions"]
    client = task_analyzer_modules["client"]
    state = {"count": 0}

    class _Chain:
        """Represent `_Chain`."""
        async def ainvoke(self, _prompt: Any) -> Any:
            """Execute `ainvoke`."""
            state["count"] += 1
            if state["count"] == 1:
                raise exceptions_mod.OutputParserException("parse failed")
            return {"ok": True}

    result = asyncio.run(
        client.ainvoke_with_retry(_Chain(), "x", max_attempts=2, backoff_seconds=0),
    )
    assert result == {"ok": True}
    assert state["count"] == 2


def test_do_analysis_wraps_dimension_value_error_as_task_error(
    task_analyzer_modules: dict[str, types.ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test do analysis wraps dimension value error as task error."""
    _inject_langchain_core_messages()
    analyzer = task_analyzer_modules["analyzer"]
    schemas = task_analyzer_modules["schemas"]

    class _FakeLLM:
        """Represent `_FakeLLM`."""
        def with_structured_output(self, _schema: Any, include_raw: bool = True) -> Any:
            """Execute `with_structured_output`."""
            assert include_raw is True
            return object()

    async def _fake_ainvoke_with_retry(_chain: Any, _prompt: Any, **_kwargs: Any) -> Any:
        """Execute `_fake_ainvoke_with_retry`."""
        parsed = SimpleNamespace(
            domain="engineering",
            domain_description="desc",
            relevant_dimensions=[
                SimpleNamespace(dimension="code", score=11, reasoning="invalid score")
            ],
        )
        return {"parsed": parsed, "raw": None}

    monkeypatch.setattr(analyzer, "get_chat_model", lambda *_args, **_kwargs: _FakeLLM())
    monkeypatch.setattr(analyzer, "build_response_schema", lambda: object())
    monkeypatch.setattr(analyzer, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(analyzer, "build_user_prompt", lambda _a, _t: "user")
    monkeypatch.setattr(analyzer, "ainvoke_with_retry", _fake_ainvoke_with_retry)

    with pytest.raises(schemas.TaskAnalysisError, match="parse"):
        asyncio.run(analyzer._do_analysis("agent", "task", "m1", "p1"))


def test_analyze_with_fallback_continues_after_task_analysis_error(
    task_analyzer_modules: dict[str, types.ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test analyze with fallback continues after task analysis error."""
    analyzer = task_analyzer_modules["analyzer"]
    schemas = task_analyzer_modules["schemas"]

    chain = [
        {"model": "m1", "provider": "p1"},
        {"model": "m2", "provider": "p2"},
    ]
    monkeypatch.setattr(analyzer, "ANALYZER_CHAIN", chain)

    calls: list[tuple[str, str]] = []

    async def _fake_analyze_async(
        agent_name: str,
        task_prompt: str,
        model: str,
        provider: str,
    ) -> Any:
        """Execute `_fake_analyze_async`."""
        calls.append((model, provider))
        if model == "m1":
            raise schemas.TaskAnalysisError("llm_invocation_failed", "first failed")
        return (
            schemas.TaskAnalysisResult(
                domain="ok",
                domain_description="ok",
                relevant_dimensions=(),
            ),
            123,
        )

    monkeypatch.setattr(analyzer, "analyze_async", _fake_analyze_async)

    result, record_id = asyncio.run(analyzer.analyze_with_fallback("agent", "task"))
    assert result.domain == "ok"
    assert record_id == 123
    assert calls == [("m1", "p1"), ("m2", "p2")]
