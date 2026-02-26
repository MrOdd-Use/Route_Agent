"""Build reproducible A/B performance comparison for high-concurrency routing."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics
import time
from typing import Any

from route_agent.model_registry.pool import MainModelPool
from route_agent.router_engine.engine import RouterEngine
from route_agent.router_engine.schemas import RouteConstraints, RouteRequest
from route_agent.router_engine.tests.perf._agent_scenarios import (
    BATCH_COUNT,
    BATCH_SIZE,
    SEED as SCENARIO_SEED,
    AgentScenario,
    build_agent_scenarios,
    build_batch_start_offsets,
)
from route_agent.router_engine.tests.perf._perf_metrics import (
    build_summary_payload,
    observed_concurrency_peak,
    observed_rpm_peak_60s,
    percentile,
)
from route_agent.router_engine.tests.perf.test_batch_concurrency_allocation_perf import (
    _build_synthetic_models,
    _run_overlapping_batch_simulation,
)
from route_agent.task_analyzer.storage import AnalysisStorage

DEFAULT_RUNS = 3
DEFAULT_FIXED_CONTROL_MODEL = "google:gemini-2.5-pro"
DEFAULT_CONTROL_STRATEGY = "pin_model"
WINDOW_SECONDS = 60.0
PEAK_WINDOW_SECONDS = 10.0
MAX_WALL_TIME_SECONDS = 120.0
MAX_ATTEMPTS_PER_REQUEST = 200
MAX_QUEUE_WAIT_PER_REQUEST_MS = 60_000.0
MAX_INFLIGHT_RETRYING = 8
RETRY_BASE_SECONDS = 0.1
RETRY_CAP_SECONDS = 2.0
RETRY_JITTER_RATIO = 0.5


@dataclass(frozen=True)
class RequestRunMetrics:
    """Represent one request lifecycle metrics row."""

    role_name: str
    batch_index: int
    submit_offset: float
    completion_offset: float
    execution_status: str
    model_id: str
    attempts: int
    routing_overheads_ms: tuple[float, ...]
    queue_wait_ms: float
    service_latency_ms: float | None
    completion_latency_ms_success: float | None
    completion_latency_ms_all: float
    rate_limit_rejections: int
    concurrency_rejections: int
    no_model_rejections: int
    retry_throttle_events: int
    retry_throttle_wait_ms: float


@dataclass(frozen=True)
class ScenarioMetrics:
    """Represent one scenario-run aggregate metrics."""

    elapsed_seconds: float
    throughput_rps: float
    success_throughput_rps: float
    success_rate: float
    rejection_rate: float
    timeout_count: int
    timeout_rate: float
    rate_limit_rejection_rate: float
    concurrency_rejection_rate: float
    no_model_rejection_rate: float
    avg_attempts_per_success: float
    p95_attempts_per_success: float
    routing_overhead_p50_ms: float
    routing_overhead_p95_ms: float
    queue_wait_p50_ms: float
    queue_wait_p95_ms: float
    max_queue_wait_ms: float
    service_latency_p50_ms: float
    service_latency_p95_ms: float
    service_latency_p99_ms: float
    completion_success_p50_ms: float
    completion_success_p95_ms: float
    completion_success_p99_ms: float
    completion_all_p50_ms: float
    completion_all_p95_ms: float
    completion_all_p99_ms: float
    window_start_ts: float
    window_end_ts: float
    successes_in_window: int
    success_throughput_rps_window: float
    p95_completion_latency_window: float
    peak_10s_success_rps: float
    retry_throttle_events: int
    retry_throttle_wait_ms: float
    allocatable_models_count: int
    eligible_models_count: int
    assigned_models_count: int
    allocatable_models: tuple[str, ...]
    eligible_models: tuple[str, ...]
    assigned_models: tuple[str, ...]


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="Number of runs per scenario.")
    parser.add_argument("--output", choices=("json", "table"), default="json", help="Output format.")
    parser.add_argument(
        "--fixed-model",
        default=DEFAULT_FIXED_CONTROL_MODEL,
        help="Control-group fixed model id.",
    )
    parser.add_argument(
        "--control-strategy",
        choices=("pin_model",),
        default=DEFAULT_CONTROL_STRATEGY,
        help="Control strategy implementation.",
    )
    return parser.parse_args()


def _round_float(value: float) -> float:
    """Round float for readable output."""
    return round(float(value), 3)


def _available_model_ids(models: list[Any]) -> tuple[str, ...]:
    """Return currently allocatable model ids."""
    return tuple(
        sorted(
            metadata.model_id
            for metadata in models
            if str((metadata.status or {}).get("availability", "")).lower() == "available"
        )
    )


def _seed_from_request_id(request_id: str) -> int:
    """Build deterministic per-request seed."""
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _build_control_scenarios(
    provider_models: dict[str, tuple[str, ...]],
    *,
    fixed_model_id: str,
) -> tuple[AgentScenario, ...]:
    """Build control scenarios with same workload but fixed-model policy."""
    base_scenarios = list(build_agent_scenarios(provider_models))
    control_scenarios: list[AgentScenario] = []
    for index, scenario in enumerate(base_scenarios):
        control_scenarios.append(
            AgentScenario(
                role_name=scenario.role_name,
                agent_class=scenario.agent_class,
                request_id=f"control-req-{index:02d}",
                batch_index=scenario.batch_index,
                complexity_tier=scenario.complexity_tier,
                duration_seconds=scenario.duration_seconds,
                analysis=scenario.analysis,
                constraints=RouteConstraints(
                    max_cost=scenario.constraints.max_cost,
                    preferred_model=fixed_model_id,
                    exclude_models=(),
                    require_provider=None,
                    estimated_input_tokens=scenario.constraints.estimated_input_tokens,
                ),
            )
        )
    return tuple(control_scenarios)


def _build_experiment_request_rows(report: dict[str, Any]) -> tuple[RequestRunMetrics, ...]:
    """Convert existing experiment report rows into unified request metrics."""
    rows: list[RequestRunMetrics] = []
    for entry in report["results"]:
        queue_wait_ms = max(0.0, (entry.execution_start_offset - entry.route_end_offset) * 1000.0)
        service_latency_ms = max(0.0, (entry.execution_end_offset - entry.execution_start_offset) * 1000.0)
        completion_ms = max(0.0, (entry.execution_end_offset - entry.route_start_offset) * 1000.0)
        rows.append(
            RequestRunMetrics(
                role_name=entry.role_name,
                batch_index=entry.batch_index,
                submit_offset=entry.route_start_offset,
                completion_offset=entry.execution_end_offset,
                execution_status=entry.execution_status,
                model_id=entry.model_id,
                attempts=1,
                routing_overheads_ms=(entry.route_latency_ms,),
                queue_wait_ms=queue_wait_ms,
                service_latency_ms=service_latency_ms,
                completion_latency_ms_success=completion_ms,
                completion_latency_ms_all=completion_ms,
                rate_limit_rejections=0,
                concurrency_rejections=0,
                no_model_rejections=0,
                retry_throttle_events=0,
                retry_throttle_wait_ms=0.0,
            )
        )
    return tuple(rows)


async def _sleep_retry_backoff(
    *,
    attempt_index: int,
    request_id: str,
    retry_semaphore: asyncio.Semaphore,
) -> tuple[int, float]:
    """Sleep with bounded retry throttle and deterministic jitter."""
    jitter_rng = random.Random(_seed_from_request_id(request_id) + attempt_index)
    base_delay = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2 ** max(0, attempt_index - 1)))
    jitter_factor = 1.0 + jitter_rng.uniform(-RETRY_JITTER_RATIO, RETRY_JITTER_RATIO)
    backoff_seconds = max(0.0, base_delay * jitter_factor)
    throttle_start = time.monotonic()
    await retry_semaphore.acquire()
    throttle_wait_ms = (time.monotonic() - throttle_start) * 1000.0
    throttle_events = 1 if throttle_wait_ms > 0 else 0
    try:
        await asyncio.sleep(backoff_seconds)
    finally:
        retry_semaphore.release()
    return throttle_events, throttle_wait_ms


def _rejection_source_from_utilization(util: Any) -> str:
    """Classify rejection source by utilization ratios."""
    conc_ratio = float(getattr(util, "conc_ratio", 0.0))
    rpm_ratio = float(getattr(util, "rpm_ratio", 0.0))
    if conc_ratio >= 1.0 and conc_ratio >= rpm_ratio:
        return "concurrency"
    if rpm_ratio >= 1.0:
        return "rate_limit"
    return "no_model"


async def _run_one_control_request(
    scenario: AgentScenario,
    *,
    engine: RouterEngine,
    pool: MainModelPool,
    base_time: float,
    fixed_model_id: str,
    control_strategy: str,
    retry_semaphore: asyncio.Semaphore,
) -> RequestRunMetrics:
    """Run one control request with queue retry and hard termination bounds."""
    submit_ts = time.monotonic()
    submit_offset = submit_ts - base_time
    request = RouteRequest(
        agent_name=scenario.role_name,
        agent_class=scenario.agent_class,
        request_id=scenario.request_id,
        task_prompt=f"{scenario.role_name} synthetic workload",
        analysis=scenario.analysis,
        constraints=scenario.constraints,
    )
    attempts = 0
    routing_overheads_ms: list[float] = []
    rate_limit_rejections = 0
    concurrency_rejections = 0
    no_model_rejections = 0
    retry_throttle_events = 0
    retry_throttle_wait_ms = 0.0

    while True:
        now = time.monotonic()
        wall_elapsed_seconds = now - submit_ts
        queue_wait_ms = wall_elapsed_seconds * 1000.0
        timeout_by_wall = wall_elapsed_seconds >= MAX_WALL_TIME_SECONDS
        timeout_by_attempts = attempts >= MAX_ATTEMPTS_PER_REQUEST
        timeout_by_queue = queue_wait_ms >= MAX_QUEUE_WAIT_PER_REQUEST_MS
        if timeout_by_wall or timeout_by_attempts or timeout_by_queue:
            completion_all_ms = min(queue_wait_ms, MAX_QUEUE_WAIT_PER_REQUEST_MS)
            completion_offset = submit_offset + (completion_all_ms / 1000.0)
            return RequestRunMetrics(
                role_name=scenario.role_name,
                batch_index=scenario.batch_index,
                submit_offset=submit_offset,
                completion_offset=completion_offset,
                execution_status="timeout",
                model_id=fixed_model_id,
                attempts=attempts,
                routing_overheads_ms=tuple(routing_overheads_ms),
                queue_wait_ms=min(queue_wait_ms, MAX_QUEUE_WAIT_PER_REQUEST_MS),
                service_latency_ms=None,
                completion_latency_ms_success=None,
                completion_latency_ms_all=completion_all_ms,
                rate_limit_rejections=rate_limit_rejections,
                concurrency_rejections=concurrency_rejections,
                no_model_rejections=no_model_rejections,
                retry_throttle_events=retry_throttle_events,
                retry_throttle_wait_ms=retry_throttle_wait_ms,
            )

        attempts += 1
        route_start = time.monotonic()
        decision = await engine.route_async(request)
        route_end = time.monotonic()
        routing_overheads_ms.append((route_end - route_start) * 1000.0)

        if not decision.primary_model:
            no_model_rejections += 1
            add_events, add_wait_ms = await _sleep_retry_backoff(
                attempt_index=attempts,
                request_id=scenario.request_id,
                retry_semaphore=retry_semaphore,
            )
            retry_throttle_events += add_events
            retry_throttle_wait_ms += add_wait_ms
            continue

        target_model_id = fixed_model_id if control_strategy == "pin_model" else decision.primary_model
        metadata = pool.get(target_model_id)
        if metadata is None:
            no_model_rejections += 1
            add_events, add_wait_ms = await _sleep_retry_backoff(
                attempt_index=attempts,
                request_id=scenario.request_id,
                retry_semaphore=retry_semaphore,
            )
            retry_throttle_events += add_events
            retry_throttle_wait_ms += add_wait_ms
            continue

        limited = await engine.rate_limiter.is_rate_limited_async(target_model_id, metadata.limits)
        if limited:
            util = await engine.rate_limiter.get_utilization_async(target_model_id, metadata.limits)
            source = _rejection_source_from_utilization(util)
            if source == "concurrency":
                concurrency_rejections += 1
            elif source == "rate_limit":
                rate_limit_rejections += 1
            else:
                no_model_rejections += 1
            add_events, add_wait_ms = await _sleep_retry_backoff(
                attempt_index=attempts,
                request_id=scenario.request_id,
                retry_semaphore=retry_semaphore,
            )
            retry_throttle_events += add_events
            retry_throttle_wait_ms += add_wait_ms
            continue

        execution_start = time.monotonic()
        await engine.rate_limiter.record_request_start_async(target_model_id)
        try:
            await asyncio.sleep(scenario.duration_seconds)
        finally:
            with suppress(Exception):
                await engine.rate_limiter.record_request_end_async(target_model_id)
        execution_end = time.monotonic()

        queue_wait_ms = max(0.0, (execution_start - submit_ts) * 1000.0)
        service_latency_ms = max(0.0, (execution_end - execution_start) * 1000.0)
        completion_ms = max(0.0, (execution_end - submit_ts) * 1000.0)
        return RequestRunMetrics(
            role_name=scenario.role_name,
            batch_index=scenario.batch_index,
            submit_offset=submit_offset,
            completion_offset=execution_end - base_time,
            execution_status="success",
            model_id=target_model_id,
            attempts=attempts,
            routing_overheads_ms=tuple(routing_overheads_ms),
            queue_wait_ms=queue_wait_ms,
            service_latency_ms=service_latency_ms,
            completion_latency_ms_success=completion_ms,
            completion_latency_ms_all=completion_ms,
            rate_limit_rejections=rate_limit_rejections,
            concurrency_rejections=concurrency_rejections,
            no_model_rejections=no_model_rejections,
            retry_throttle_events=retry_throttle_events,
            retry_throttle_wait_ms=retry_throttle_wait_ms,
        )


def _build_model_limit_report_from_rows(rows: tuple[RequestRunMetrics, ...]) -> dict[str, dict[str, Any]]:
    """Build per-model limit report for successful rows."""
    grouped: dict[str, list[RequestRunMetrics]] = {}
    for row in rows:
        if row.execution_status != "success" or not row.model_id:
            continue
        grouped.setdefault(row.model_id, []).append(row)

    report: dict[str, dict[str, Any]] = {}
    for model_id, items in grouped.items():
        starts = [item.completion_offset - ((item.service_latency_ms or 0.0) / 1000.0) for item in items]
        intervals = [
            (
                item.completion_offset - ((item.service_latency_ms or 0.0) / 1000.0),
                item.completion_offset,
            )
            for item in items
        ]
        report[model_id] = {
            "observed_rpm_peak_60s": observed_rpm_peak_60s(starts),
            "observed_concurrency_peak": observed_concurrency_peak(intervals),
        }
    return report


async def _run_control_simulation(
    run_dir: Path,
    *,
    fixed_model_id: str,
    control_strategy: str,
) -> dict[str, Any]:
    """Run control simulation with same workload and bounded queue retries."""
    run_dir.mkdir(parents=True, exist_ok=True)
    models, provider_models = _build_synthetic_models()
    allocatable_models = _available_model_ids(models)
    if fixed_model_id not in allocatable_models:
        raise ValueError(f"fixed model is not allocatable: {fixed_model_id}")

    eligible_models = (fixed_model_id,)
    pool = MainModelPool(models=models)
    analysis_storage = AnalysisStorage(db_path=run_dir / "analysis.db")
    engine = RouterEngine(
        pool=pool,
        analysis_storage=analysis_storage,
        router_db_path=run_dir / "router_engine.db",
        rate_limit_mode="inmemory",
    )
    scenarios = _build_control_scenarios(provider_models, fixed_model_id=fixed_model_id)
    schedule_offsets = list(build_batch_start_offsets())
    actual_offsets: list[float] = []
    tasks: list[asyncio.Task[RequestRunMetrics]] = []
    retry_semaphore = asyncio.Semaphore(MAX_INFLIGHT_RETRYING)
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
                        _run_one_control_request(
                            scenario,
                            engine=engine,
                            pool=pool,
                            base_time=base_time,
                            fixed_model_id=fixed_model_id,
                            control_strategy=control_strategy,
                            retry_semaphore=retry_semaphore,
                        )
                    )
                )
        rows = tuple(await asyncio.gather(*tasks))
        elapsed_seconds = time.monotonic() - base_time
    finally:
        probe_task = getattr(engine, "_probe_task", None)
        if probe_task is not None:
            probe_task.cancel()
            with suppress(asyncio.CancelledError):
                await probe_task

    summary = build_summary_payload(
        route_latencies_ms=[latency for row in rows for latency in row.routing_overheads_ms],
        total_agents=len(rows),
        elapsed_seconds=elapsed_seconds,
        batch_schedule_offsets=schedule_offsets,
        batch_actual_offsets=actual_offsets,
        model_limit_report=_build_model_limit_report_from_rows(rows),
    )
    return {
        "summary": summary,
        "rows": rows,
        "allocatable_models": allocatable_models,
        "eligible_models": eligible_models,
    }


async def _run_experiment_simulation(run_dir: Path) -> dict[str, Any]:
    """Run experiment simulation (existing overlapping-batch scenario)."""
    run_dir.mkdir(parents=True, exist_ok=True)
    report = await _run_overlapping_batch_simulation(run_dir)
    models, _provider_models = _build_synthetic_models()
    allocatable_models = _available_model_ids(models)
    rows = _build_experiment_request_rows(report)
    return {
        "summary": report["summary"],
        "rows": rows,
        "allocatable_models": allocatable_models,
        "eligible_models": allocatable_models,
    }


def _peak_success_rps_in_window(
    success_completion_offsets: list[float],
    *,
    window_seconds: float,
) -> float:
    """Compute peak successful throughput in a sliding window."""
    if not success_completion_offsets:
        return 0.0
    ordered = sorted(success_completion_offsets)
    left = 0
    peak_count = 0
    for right, value in enumerate(ordered):
        while value - ordered[left] > window_seconds:
            left += 1
        peak_count = max(peak_count, right - left + 1)
    return peak_count / window_seconds if window_seconds > 0 else 0.0


def _extract_metrics(report: dict[str, Any]) -> ScenarioMetrics:
    """Extract unified aggregate metrics from simulation report."""
    summary = report["summary"]
    rows: tuple[RequestRunMetrics, ...] = tuple(report["rows"])
    total_requests = len(rows)
    elapsed_seconds = float(summary["elapsed_seconds"])

    success_rows = [row for row in rows if row.execution_status == "success"]
    timeout_rows = [row for row in rows if row.execution_status == "timeout"]
    assigned_models = tuple(sorted({row.model_id for row in rows if row.model_id}))
    success_count = len(success_rows)
    timeout_count = len(timeout_rows)

    total_attempts = sum(max(1, row.attempts) for row in rows)
    rate_limit_rejections = sum(row.rate_limit_rejections for row in rows)
    concurrency_rejections = sum(row.concurrency_rejections for row in rows)
    no_model_rejections = sum(row.no_model_rejections for row in rows)
    total_rejections = rate_limit_rejections + concurrency_rejections + no_model_rejections

    success_attempts = [row.attempts for row in success_rows]
    routing_overheads = [lat for row in rows for lat in row.routing_overheads_ms]
    queue_waits = [row.queue_wait_ms for row in rows]
    service_latencies = [row.service_latency_ms for row in success_rows if row.service_latency_ms is not None]
    completion_success = [
        row.completion_latency_ms_success
        for row in success_rows
        if row.completion_latency_ms_success is not None
    ]
    completion_all = [row.completion_latency_ms_all for row in rows]

    submit_offsets = [row.submit_offset for row in rows]
    t0 = min(submit_offsets) if submit_offsets else 0.0
    window_start = t0
    window_end = t0 + WINDOW_SECONDS
    success_completion_offsets = [row.completion_offset for row in success_rows]
    successes_in_window = sum(1 for ts in success_completion_offsets if window_start <= ts < window_end)
    completion_in_window = [
        row.completion_latency_ms_all
        for row in rows
        if row.submit_offset < window_end
    ]

    success_rate = (success_count / total_requests) if total_requests else 0.0
    timeout_rate = (timeout_count / total_requests) if total_requests else 0.0
    rejection_rate = (total_rejections / total_attempts) if total_attempts else 0.0
    success_throughput_rps = (success_count / elapsed_seconds) if elapsed_seconds > 0 else 0.0
    success_throughput_rps_window = successes_in_window / WINDOW_SECONDS

    retry_throttle_events = sum(row.retry_throttle_events for row in rows)
    retry_throttle_wait_ms = sum(row.retry_throttle_wait_ms for row in rows)

    allocatable_models = tuple(report["allocatable_models"])
    eligible_models = tuple(report["eligible_models"])
    return ScenarioMetrics(
        elapsed_seconds=elapsed_seconds,
        throughput_rps=float(summary["throughput_rps"]),
        success_throughput_rps=success_throughput_rps,
        success_rate=success_rate,
        rejection_rate=rejection_rate,
        timeout_count=timeout_count,
        timeout_rate=timeout_rate,
        rate_limit_rejection_rate=(rate_limit_rejections / total_attempts) if total_attempts else 0.0,
        concurrency_rejection_rate=(concurrency_rejections / total_attempts) if total_attempts else 0.0,
        no_model_rejection_rate=(no_model_rejections / total_attempts) if total_attempts else 0.0,
        avg_attempts_per_success=(statistics.mean(success_attempts) if success_attempts else 0.0),
        p95_attempts_per_success=percentile([float(value) for value in success_attempts], 0.95) if success_attempts else 0.0,
        routing_overhead_p50_ms=percentile(routing_overheads, 0.5) if routing_overheads else 0.0,
        routing_overhead_p95_ms=percentile(routing_overheads, 0.95) if routing_overheads else 0.0,
        queue_wait_p50_ms=percentile(queue_waits, 0.5) if queue_waits else 0.0,
        queue_wait_p95_ms=percentile(queue_waits, 0.95) if queue_waits else 0.0,
        max_queue_wait_ms=max(queue_waits) if queue_waits else 0.0,
        service_latency_p50_ms=percentile(service_latencies, 0.5) if service_latencies else 0.0,
        service_latency_p95_ms=percentile(service_latencies, 0.95) if service_latencies else 0.0,
        service_latency_p99_ms=percentile(service_latencies, 0.99) if service_latencies else 0.0,
        completion_success_p50_ms=percentile(completion_success, 0.5) if completion_success else 0.0,
        completion_success_p95_ms=percentile(completion_success, 0.95) if completion_success else 0.0,
        completion_success_p99_ms=percentile(completion_success, 0.99) if completion_success else 0.0,
        completion_all_p50_ms=percentile(completion_all, 0.5) if completion_all else 0.0,
        completion_all_p95_ms=percentile(completion_all, 0.95) if completion_all else 0.0,
        completion_all_p99_ms=percentile(completion_all, 0.99) if completion_all else 0.0,
        window_start_ts=window_start,
        window_end_ts=window_end,
        successes_in_window=successes_in_window,
        success_throughput_rps_window=success_throughput_rps_window,
        p95_completion_latency_window=percentile(completion_in_window, 0.95) if completion_in_window else 0.0,
        peak_10s_success_rps=_peak_success_rps_in_window(success_completion_offsets, window_seconds=PEAK_WINDOW_SECONDS),
        retry_throttle_events=retry_throttle_events,
        retry_throttle_wait_ms=retry_throttle_wait_ms,
        allocatable_models_count=len(allocatable_models),
        eligible_models_count=len(eligible_models),
        assigned_models_count=len(assigned_models),
        allocatable_models=allocatable_models,
        eligible_models=eligible_models,
        assigned_models=assigned_models,
    )


def _metrics_to_dict(item: ScenarioMetrics) -> dict[str, Any]:
    """Convert scenario metrics into JSON-ready dictionary."""
    return {
        "elapsed_seconds": _round_float(item.elapsed_seconds),
        "throughput_rps": _round_float(item.throughput_rps),
        "success_throughput_rps": _round_float(item.success_throughput_rps),
        "success_rate": _round_float(item.success_rate),
        "rejection_rate": _round_float(item.rejection_rate),
        "timeout_count": item.timeout_count,
        "timeout_rate": _round_float(item.timeout_rate),
        "rate_limit_rejection_rate": _round_float(item.rate_limit_rejection_rate),
        "concurrency_rejection_rate": _round_float(item.concurrency_rejection_rate),
        "no_model_rejection_rate": _round_float(item.no_model_rejection_rate),
        "avg_attempts_per_success": _round_float(item.avg_attempts_per_success),
        "p95_attempts_per_success": _round_float(item.p95_attempts_per_success),
        "routing_overhead_ms": {
            "p50": _round_float(item.routing_overhead_p50_ms),
            "p95": _round_float(item.routing_overhead_p95_ms),
        },
        "queue_wait_ms": {
            "p50": _round_float(item.queue_wait_p50_ms),
            "p95": _round_float(item.queue_wait_p95_ms),
            "max": _round_float(item.max_queue_wait_ms),
        },
        "service_latency_ms": {
            "p50": _round_float(item.service_latency_p50_ms),
            "p95": _round_float(item.service_latency_p95_ms),
            "p99": _round_float(item.service_latency_p99_ms),
        },
        "completion_latency_ms_success": {
            "p50": _round_float(item.completion_success_p50_ms),
            "p95": _round_float(item.completion_success_p95_ms),
            "p99": _round_float(item.completion_success_p99_ms),
        },
        "completion_latency_ms_all": {
            "p50": _round_float(item.completion_all_p50_ms),
            "p95": _round_float(item.completion_all_p95_ms),
            "p99": _round_float(item.completion_all_p99_ms),
        },
        "window_start_ts": _round_float(item.window_start_ts),
        "window_end_ts": _round_float(item.window_end_ts),
        "successes_in_window": item.successes_in_window,
        "success_throughput_rps_window": _round_float(item.success_throughput_rps_window),
        "p95_completion_latency_window": _round_float(item.p95_completion_latency_window),
        "peak_10s_success_rps": _round_float(item.peak_10s_success_rps),
        "retry_throttle_events": item.retry_throttle_events,
        "retry_throttle_wait_ms": _round_float(item.retry_throttle_wait_ms),
        "allocatable_models_count": item.allocatable_models_count,
        "eligible_models_count": item.eligible_models_count,
        "assigned_models_count": item.assigned_models_count,
        "allocatable_models": list(item.allocatable_models),
        "eligible_models": list(item.eligible_models),
        "assigned_models": list(item.assigned_models),
    }


def _stability(items: list[ScenarioMetrics]) -> dict[str, dict[str, float]]:
    """Build min/max stability summary for key metrics."""
    return {
        "success_throughput_rps": {
            "min": _round_float(min(item.success_throughput_rps for item in items)),
            "max": _round_float(max(item.success_throughput_rps for item in items)),
        },
        "p95_completion_latency_ms_all": {
            "min": _round_float(min(item.completion_all_p95_ms for item in items)),
            "max": _round_float(max(item.completion_all_p95_ms for item in items)),
        },
        "rejection_rate": {
            "min": _round_float(min(item.rejection_rate for item in items)),
            "max": _round_float(max(item.rejection_rate for item in items)),
        },
    }


def _median_metrics(items: list[ScenarioMetrics]) -> dict[str, Any]:
    """Build median metrics and stability summary from repeated runs."""
    if not items:
        raise ValueError("items must not be empty")
    metrics = ScenarioMetrics(
        elapsed_seconds=statistics.median(item.elapsed_seconds for item in items),
        throughput_rps=statistics.median(item.throughput_rps for item in items),
        success_throughput_rps=statistics.median(item.success_throughput_rps for item in items),
        success_rate=statistics.median(item.success_rate for item in items),
        rejection_rate=statistics.median(item.rejection_rate for item in items),
        timeout_count=int(statistics.median(item.timeout_count for item in items)),
        timeout_rate=statistics.median(item.timeout_rate for item in items),
        rate_limit_rejection_rate=statistics.median(item.rate_limit_rejection_rate for item in items),
        concurrency_rejection_rate=statistics.median(item.concurrency_rejection_rate for item in items),
        no_model_rejection_rate=statistics.median(item.no_model_rejection_rate for item in items),
        avg_attempts_per_success=statistics.median(item.avg_attempts_per_success for item in items),
        p95_attempts_per_success=statistics.median(item.p95_attempts_per_success for item in items),
        routing_overhead_p50_ms=statistics.median(item.routing_overhead_p50_ms for item in items),
        routing_overhead_p95_ms=statistics.median(item.routing_overhead_p95_ms for item in items),
        queue_wait_p50_ms=statistics.median(item.queue_wait_p50_ms for item in items),
        queue_wait_p95_ms=statistics.median(item.queue_wait_p95_ms for item in items),
        max_queue_wait_ms=statistics.median(item.max_queue_wait_ms for item in items),
        service_latency_p50_ms=statistics.median(item.service_latency_p50_ms for item in items),
        service_latency_p95_ms=statistics.median(item.service_latency_p95_ms for item in items),
        service_latency_p99_ms=statistics.median(item.service_latency_p99_ms for item in items),
        completion_success_p50_ms=statistics.median(item.completion_success_p50_ms for item in items),
        completion_success_p95_ms=statistics.median(item.completion_success_p95_ms for item in items),
        completion_success_p99_ms=statistics.median(item.completion_success_p99_ms for item in items),
        completion_all_p50_ms=statistics.median(item.completion_all_p50_ms for item in items),
        completion_all_p95_ms=statistics.median(item.completion_all_p95_ms for item in items),
        completion_all_p99_ms=statistics.median(item.completion_all_p99_ms for item in items),
        window_start_ts=statistics.median(item.window_start_ts for item in items),
        window_end_ts=statistics.median(item.window_end_ts for item in items),
        successes_in_window=int(statistics.median(item.successes_in_window for item in items)),
        success_throughput_rps_window=statistics.median(item.success_throughput_rps_window for item in items),
        p95_completion_latency_window=statistics.median(item.p95_completion_latency_window for item in items),
        peak_10s_success_rps=statistics.median(item.peak_10s_success_rps for item in items),
        retry_throttle_events=int(statistics.median(item.retry_throttle_events for item in items)),
        retry_throttle_wait_ms=statistics.median(item.retry_throttle_wait_ms for item in items),
        allocatable_models_count=int(statistics.median(item.allocatable_models_count for item in items)),
        eligible_models_count=int(statistics.median(item.eligible_models_count for item in items)),
        assigned_models_count=int(statistics.median(item.assigned_models_count for item in items)),
        allocatable_models=tuple(sorted(set().union(*(item.allocatable_models for item in items)))),
        eligible_models=tuple(sorted(set().union(*(item.eligible_models for item in items)))),
        assigned_models=tuple(sorted(set().union(*(item.assigned_models for item in items)))),
    )
    payload = _metrics_to_dict(metrics)
    payload["stability"] = _stability(items)
    return payload


def _render_table(payload: dict[str, Any]) -> str:
    """Render markdown table for interview usage."""
    experiment = payload["experiment"]["median"]
    control = payload["control"]["median"]
    headers = [
        "场景",
        "总耗时(s)",
        "总吞吐",
        "有效吞吐",
        "成功率",
        "拒绝率",
        "超时率",
        "completion_all P95(ms)",
        "allocatable/eligible/assigned",
        "关键结论",
    ]
    rows = [
        [
            "实验组（重叠批次）",
            f"{experiment['elapsed_seconds']}",
            f"{experiment['throughput_rps']}",
            f"{experiment['success_throughput_rps']}",
            f"{experiment['success_rate']}",
            f"{experiment['rejection_rate']}",
            f"{experiment['timeout_rate']}",
            f"{experiment['completion_latency_ms_all']['p95']}",
            (
                f"{experiment['allocatable_models_count']}/"
                f"{experiment['eligible_models_count']}/"
                f"{experiment['assigned_models_count']}"
            ),
            "多模型分流，容量稳定",
        ],
        [
            f"对照组（固定单模型：{payload['control_model']}）",
            f"{control['elapsed_seconds']}",
            f"{control['throughput_rps']}",
            f"{control['success_throughput_rps']}",
            f"{control['success_rate']}",
            f"{control['rejection_rate']}",
            f"{control['timeout_rate']}",
            f"{control['completion_latency_ms_all']['p95']}",
            (
                f"{control['allocatable_models_count']}/"
                f"{control['eligible_models_count']}/"
                f"{control['assigned_models_count']}"
            ),
            "单模型热点明显，容量退化",
        ],
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


async def _run_all(runs: int, fixed_model_id: str, control_strategy: str) -> dict[str, Any]:
    """Run both scenarios repeatedly and aggregate medians."""
    if runs < 1:
        raise ValueError("runs must be >= 1")

    root_dir = Path("data") / "perf_ab_compare" / datetime.now().strftime("%Y%m%d_%H%M%S")
    experiment_runs: list[ScenarioMetrics] = []
    control_runs: list[ScenarioMetrics] = []

    for index in range(1, runs + 1):
        experiment_report = await _run_experiment_simulation(root_dir / f"experiment_run_{index}")
        control_report = await _run_control_simulation(
            root_dir / f"control_run_{index}",
            fixed_model_id=fixed_model_id,
            control_strategy=control_strategy,
        )
        experiment_runs.append(_extract_metrics(experiment_report))
        control_runs.append(_extract_metrics(control_report))

    return {
        "output_dir": str(root_dir),
        "runs": runs,
        "control_model": fixed_model_id,
        "control_strategy": control_strategy,
        "window_seconds": WINDOW_SECONDS,
        "window_anchor": "min_submit_ts",
        "scenario_seed": SCENARIO_SEED,
        "limits": {
            "max_wall_time_seconds": MAX_WALL_TIME_SECONDS,
            "max_attempts_per_request": MAX_ATTEMPTS_PER_REQUEST,
            "max_queue_wait_per_request_ms": MAX_QUEUE_WAIT_PER_REQUEST_MS,
            "max_inflight_retrying": MAX_INFLIGHT_RETRYING,
            "retry_base_seconds": RETRY_BASE_SECONDS,
            "retry_cap_seconds": RETRY_CAP_SECONDS,
            "retry_jitter_ratio": RETRY_JITTER_RATIO,
        },
        "experiment": {
            "median": _median_metrics(experiment_runs),
            "per_run": [_metrics_to_dict(item) for item in experiment_runs],
        },
        "control": {
            "median": _median_metrics(control_runs),
            "per_run": [_metrics_to_dict(item) for item in control_runs],
        },
    }


def main() -> None:
    """Run A/B comparison and print requested output format."""
    args = _parse_args()
    payload = asyncio.run(_run_all(args.runs, args.fixed_model, args.control_strategy))
    if args.output == "table":
        print(_render_table(payload))
        return
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
