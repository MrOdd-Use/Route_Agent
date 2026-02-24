"""Overlapping batch-start performance tests for router engine."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

import pytest

from route_agent.model_registry.pool import MainModelPool
from route_agent.model_registry.schemas import ModelMetadata
from route_agent.router_engine.engine import RouterEngine
from route_agent.router_engine.schemas import RouteRequest
from route_agent.router_engine.tests.perf._agent_scenarios import (
    BATCH_COUNT,
    BATCH_SIZE,
    INTERVAL_MAX,
    INTERVAL_MIN,
    TIME_SCALE,
    TOTAL_AGENTS,
    AgentScenario,
    build_agent_scenarios,
    build_batch_start_offsets,
)
from route_agent.router_engine.tests.perf._perf_metrics import (
    build_summary_payload,
    observed_concurrency_peak,
    observed_rpm_peak_60s,
)
from route_agent.task_analyzer.storage import AnalysisStorage

_BATCH_START_TOLERANCE_SECONDS = 0.1
_DURATION_OBS_TOLERANCE_SECONDS = 0.2


@dataclass(frozen=True)
class AgentRunResult:
    """Represent `AgentRunResult`."""
    role_name: str
    batch_index: int
    complexity_tier: str
    sampled_duration_seconds: float
    observed_duration_seconds: float
    scheduled_batch_start_offset: float
    actual_batch_start_offset: float
    route_start_offset: float
    route_end_offset: float
    route_latency_ms: float
    execution_start_offset: float
    execution_end_offset: float
    model_id: str
    provider: str
    limits: dict[str, Any]


def _build_synthetic_models() -> tuple[list[ModelMetadata], dict[str, tuple[str, ...]]]:
    """Execute `_build_synthetic_models`."""
    provider_models: dict[str, tuple[str, ...]] = {
        "openai": ("openai:gpt-4.1-mini", "openai:gpt-4.1", "openai:gpt-5.2-codex"),
        "google": ("google:gemini-2.5-flash", "google:gemini-2.5-pro", "google:gemini-1.5-flash"),
        "deepseek": ("deepseek:deepseek-chat", "deepseek:deepseek-v3", "deepseek:deepseek-reasoner"),
        "anthropic": ("anthropic:claude-3-5-haiku", "anthropic:claude-3-7-sonnet", "anthropic:claude-opus-4-1"),
    }

    provider_profiles: dict[str, dict[str, float]] = {
        "openai": {"cap_bias": 4.0, "price_mult": 1.25, "rpm_base": 8.0, "conc_base": 2.0, "ctx_base": 120000.0},
        "google": {"cap_bias": 2.0, "price_mult": 1.05, "rpm_base": 9.0, "conc_base": 2.0, "ctx_base": 180000.0},
        "deepseek": {"cap_bias": 0.0, "price_mult": 0.8, "rpm_base": 10.0, "conc_base": 3.0, "ctx_base": 110000.0},
        "anthropic": {"cap_bias": 3.0, "price_mult": 1.15, "rpm_base": 7.0, "conc_base": 2.0, "ctx_base": 140000.0},
    }

    def _clip_cap(value: float) -> int:
        """Execute `_clip_cap`."""
        return int(max(1, min(round(value), 99)))

    models: list[ModelMetadata] = []
    for provider_idx, (provider, model_ids) in enumerate(provider_models.items()):
        profile = provider_profiles[provider]
        for idx, model_id in enumerate(model_ids):
            display_name = model_id.split(":", maxsplit=1)[1]
            tier_factor = float(idx + 1)

            capabilities = {
                "instruction_following": _clip_cap(66 + tier_factor * 8 + profile["cap_bias"] + provider_idx),
                "text": _clip_cap(64 + tier_factor * 9 + profile["cap_bias"] + provider_idx * 2),
                "reasoning": _clip_cap(60 + tier_factor * 11 + profile["cap_bias"] + provider_idx),
                "code": _clip_cap(58 + tier_factor * 12 + profile["cap_bias"] + provider_idx * 2),
                "math": _clip_cap(57 + tier_factor * 10 + profile["cap_bias"] + provider_idx),
            }

            input_price = round((0.0007 + idx * 0.0009) * profile["price_mult"], 6)
            output_price = round(input_price * (2.3 + idx * 0.5), 6)

            limits = {
                "max_requests_per_minute": int(profile["rpm_base"] + idx * 3 + provider_idx),
                "max_requests_per_day": int(2400 + idx * 450 + provider_idx * 180),
                "max_concurrency": int(profile["conc_base"] + (idx % 2) + (provider_idx % 2)),
                "max_context_tokens": int(profile["ctx_base"] + idx * 70000 + provider_idx * 12000),
            }
            models.append(
                ModelMetadata(
                    model_id=model_id,
                    display_name=display_name,
                    provider=provider,
                    api_model_name=display_name,
                    capabilities=capabilities,
                    pricing={"input": input_price, "output": output_price, "unit": "per_1k_tokens"},
                    limits=limits,
                    status={"availability": "available"},
                    routing={"release_date": f"2025-{provider_idx + 1:02d}-{idx + 1:02d}"},
                )
            )
    return models, provider_models


def _duration_bounds(tier: str) -> tuple[float, float]:
    """Execute `_duration_bounds`."""
    if tier == "simple":
        return 1.0 * TIME_SCALE, 3.0 * TIME_SCALE
    if tier == "medium":
        return 5.0 * TIME_SCALE, 10.0 * TIME_SCALE
    return 30.0 * TIME_SCALE, 40.0 * TIME_SCALE


async def _execute_agent(
    scenario: AgentScenario,
    *,
    engine: RouterEngine,
    pool: MainModelPool,
    base_time: float,
    batch_schedule_offset: float,
    batch_actual_offset: float,
) -> AgentRunResult:
    """Execute `_execute_agent`."""
    route_start = time.monotonic()
    request = RouteRequest(
        agent_name=scenario.role_name,
        agent_class=scenario.agent_class,
        request_id=scenario.request_id,
        task_prompt=f"{scenario.role_name} synthetic workload",
        analysis=scenario.analysis,
        constraints=scenario.constraints,
    )
    decision = await engine.route_async(request)
    route_end = time.monotonic()
    route_latency_ms = (route_end - route_start) * 1000.0

    primary_model = decision.primary_model
    if not primary_model:
        raise AssertionError(f"{scenario.role_name} failed to receive a model")

    metadata = pool.get(primary_model)
    if metadata is None:
        raise AssertionError(f"{scenario.role_name} got unknown model: {primary_model}")

    execution_start = time.monotonic()
    await engine.rate_limiter.record_request_start_async(primary_model)
    try:
        await asyncio.sleep(scenario.duration_seconds)
    finally:
        await engine.rate_limiter.record_request_end_async(primary_model)
    execution_end = time.monotonic()

    return AgentRunResult(
        role_name=scenario.role_name,
        batch_index=scenario.batch_index,
        complexity_tier=scenario.complexity_tier,
        sampled_duration_seconds=scenario.duration_seconds,
        observed_duration_seconds=execution_end - execution_start,
        scheduled_batch_start_offset=batch_schedule_offset,
        actual_batch_start_offset=batch_actual_offset,
        route_start_offset=route_start - base_time,
        route_end_offset=route_end - base_time,
        route_latency_ms=route_latency_ms,
        execution_start_offset=execution_start - base_time,
        execution_end_offset=execution_end - base_time,
        model_id=primary_model,
        provider=metadata.provider,
        limits=dict(metadata.limits),
    )


def _build_model_limit_report(results: list[AgentRunResult]) -> dict[str, dict[str, Any]]:
    """Execute `_build_model_limit_report`."""
    grouped: dict[str, list[AgentRunResult]] = {}
    for item in results:
        grouped.setdefault(item.model_id, []).append(item)

    report: dict[str, dict[str, Any]] = {}
    for model_id, items in grouped.items():
        starts = [entry.execution_start_offset for entry in items]
        intervals = [(entry.execution_start_offset, entry.execution_end_offset) for entry in items]
        limits = items[0].limits
        rpm_limit = int(limits.get("max_requests_per_minute") or 0)
        conc_limit = int(limits.get("max_concurrency") or 0)
        report[model_id] = {
            "rpm_limit": rpm_limit,
            "concurrency_limit": conc_limit,
            "observed_rpm_peak_60s": observed_rpm_peak_60s(starts),
            "observed_concurrency_peak": observed_concurrency_peak(intervals),
        }
    return report


async def _run_overlapping_batch_simulation(tmp_dir: Path) -> dict[str, Any]:
    """Execute `_run_overlapping_batch_simulation`."""
    models, provider_models = _build_synthetic_models()
    pool = MainModelPool(models=models)
    analysis_storage = AnalysisStorage(db_path=tmp_dir / "analysis.db")
    engine = RouterEngine(
        pool=pool,
        analysis_storage=analysis_storage,
        router_db_path=tmp_dir / "router_engine.db",
        rate_limit_mode="inmemory",
    )

    scenarios = build_agent_scenarios(provider_models)
    if len(scenarios) != TOTAL_AGENTS:
        raise AssertionError(f"Expected {TOTAL_AGENTS} scenarios, got {len(scenarios)}")
    schedule_offsets = list(build_batch_start_offsets())
    if len(schedule_offsets) != BATCH_COUNT:
        raise AssertionError(f"Expected {BATCH_COUNT} schedule offsets, got {len(schedule_offsets)}")

    actual_offsets: list[float] = []
    tasks: list[asyncio.Task[AgentRunResult]] = []
    base_time = time.monotonic()
    try:
        for batch_index in range(BATCH_COUNT):
            target_start = base_time + schedule_offsets[batch_index]
            now = time.monotonic()
            if target_start > now:
                await asyncio.sleep(target_start - now)
            batch_actual_offset = time.monotonic() - base_time
            actual_offsets.append(batch_actual_offset)

            batch_slice = scenarios[batch_index * BATCH_SIZE : (batch_index + 1) * BATCH_SIZE]
            for scenario in batch_slice:
                tasks.append(
                    asyncio.create_task(
                        _execute_agent(
                            scenario,
                            engine=engine,
                            pool=pool,
                            base_time=base_time,
                            batch_schedule_offset=schedule_offsets[batch_index],
                            batch_actual_offset=batch_actual_offset,
                        )
                    )
                )

        results = await asyncio.gather(*tasks)
        elapsed_seconds = time.monotonic() - base_time
    finally:
        probe_task = getattr(engine, "_probe_task", None)
        if probe_task is not None:
            probe_task.cancel()
            with suppress(asyncio.CancelledError):
                await probe_task

    model_limit_report = _build_model_limit_report(results)
    summary = build_summary_payload(
        route_latencies_ms=[entry.route_latency_ms for entry in results],
        total_agents=len(results),
        elapsed_seconds=elapsed_seconds,
        batch_schedule_offsets=schedule_offsets,
        batch_actual_offsets=actual_offsets,
        model_limit_report=model_limit_report,
    )
    return {
        "results": results,
        "batch_schedule_offsets": schedule_offsets,
        "batch_actual_offsets": actual_offsets,
        "elapsed_seconds": elapsed_seconds,
        "model_limit_report": model_limit_report,
        "summary": summary,
    }


@pytest.fixture(scope="module")
def overlapping_batch_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Execute `overlapping_batch_report`."""
    tmp_dir = tmp_path_factory.mktemp("router_overlapping_batch_perf")
    return asyncio.run(_run_overlapping_batch_simulation(tmp_dir))


def test_batch_start_based_scheduler_timing(overlapping_batch_report: dict[str, Any]) -> None:
    """Test batch start based scheduler timing."""
    schedule_offsets: list[float] = overlapping_batch_report["batch_schedule_offsets"]
    actual_offsets: list[float] = overlapping_batch_report["batch_actual_offsets"]

    assert len(schedule_offsets) == BATCH_COUNT
    assert len(actual_offsets) == BATCH_COUNT

    for idx in range(BATCH_COUNT):
        drift = abs(actual_offsets[idx] - schedule_offsets[idx])
        assert drift <= _BATCH_START_TOLERANCE_SECONDS, (
            f"batch={idx} start drift too high: {drift:.3f}s"
        )

    for idx in range(1, BATCH_COUNT):
        scheduled_gap = schedule_offsets[idx] - schedule_offsets[idx - 1]
        actual_gap = actual_offsets[idx] - actual_offsets[idx - 1]
        assert INTERVAL_MIN <= scheduled_gap <= INTERVAL_MAX
        assert abs(actual_gap - scheduled_gap) <= _BATCH_START_TOLERANCE_SECONDS


def test_overlapping_batches_all_agents_allocated(overlapping_batch_report: dict[str, Any]) -> None:
    """Test overlapping batches all agents allocated."""
    results: list[AgentRunResult] = overlapping_batch_report["results"]

    assert len(results) == TOTAL_AGENTS
    assert all(item.model_id for item in results)

    counts_by_batch = [0] * BATCH_COUNT
    for item in results:
        counts_by_batch[item.batch_index] += 1

        lo, hi = _duration_bounds(item.complexity_tier)
        assert lo <= item.sampled_duration_seconds <= hi
        assert abs(item.observed_duration_seconds - item.sampled_duration_seconds) <= _DURATION_OBS_TOLERANCE_SECONDS

    assert counts_by_batch == [BATCH_SIZE] * BATCH_COUNT


def test_overlapping_batches_respect_rpm_and_concurrency_limits(
    overlapping_batch_report: dict[str, Any],
) -> None:
    """Test overlapping batches respect rpm and concurrency limits."""
    model_limit_report: dict[str, dict[str, Any]] = overlapping_batch_report["model_limit_report"]
    assert model_limit_report

    for model_id, row in model_limit_report.items():
        rpm_limit = int(row["rpm_limit"])
        conc_limit = int(row["concurrency_limit"])
        observed_rpm = int(row["observed_rpm_peak_60s"])
        observed_conc = int(row["observed_concurrency_peak"])
        assert observed_rpm <= rpm_limit, f"{model_id} exceeded RPM: {observed_rpm} > {rpm_limit}"
        assert observed_conc <= conc_limit, f"{model_id} exceeded concurrency: {observed_conc} > {conc_limit}"


def test_overlapping_batches_latency_and_throughput_report(
    overlapping_batch_report: dict[str, Any],
) -> None:
    """Test overlapping batches latency and throughput report."""
    summary: dict[str, Any] = overlapping_batch_report["summary"]
    lat = summary["latency_ms"]

    assert summary["total_agents"] == TOTAL_AGENTS
    assert summary["elapsed_seconds"] > 0
    assert summary["throughput_rps"] > 0
    assert lat["p50"] <= lat["p95"] <= lat["p99"]

    print(json.dumps(summary, indent=2, ensure_ascii=False))
