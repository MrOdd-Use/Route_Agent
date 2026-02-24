"""Metrics helpers for router_engine perf tests."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class LatencySummary:
    """Represent `LatencySummary`."""
    p50_ms: float
    p95_ms: float
    p99_ms: float


def percentile(values: list[float], q: float) -> float:
    """Return percentile for q in [0, 1] using linear interpolation."""
    if not values:
        return 0.0
    if q <= 0:
        return min(values)
    if q >= 1:
        return max(values)

    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return float(ordered[lo])
    mix = idx - lo
    return float(ordered[lo] * (1.0 - mix) + ordered[hi] * mix)


def summarize_latency_ms(values: list[float]) -> LatencySummary:
    """Execute `summarize_latency_ms`."""
    return LatencySummary(
        p50_ms=percentile(values, 0.50),
        p95_ms=percentile(values, 0.95),
        p99_ms=percentile(values, 0.99),
    )


def observed_rpm_peak_60s(starts: list[float]) -> int:
    """Peak count of starts within any 60-second sliding window."""
    if not starts:
        return 0
    ordered = sorted(starts)
    left = 0
    peak = 0
    for right, value in enumerate(ordered):
        while value - ordered[left] > 60.0:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


def observed_concurrency_peak(intervals: list[tuple[float, float]]) -> int:
    """Peak overlap count from (start, end) intervals."""
    if not intervals:
        return 0

    events: list[tuple[float, int]] = []
    for start, end in intervals:
        events.append((start, 1))
        events.append((end, -1))

    # Start before end on same timestamp, to conservatively count overlap.
    events.sort(key=lambda item: (item[0], -item[1]))

    current = 0
    peak = 0
    for _ts, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


def to_jsonable_float(value: float) -> float:
    """Execute `to_jsonable_float`."""
    return round(float(value), 3)


def build_summary_payload(
    *,
    route_latencies_ms: list[float],
    total_agents: int,
    elapsed_seconds: float,
    batch_schedule_offsets: list[float],
    batch_actual_offsets: list[float],
    model_limit_report: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Execute `build_summary_payload`."""
    lat = summarize_latency_ms(route_latencies_ms)
    throughput = (total_agents / elapsed_seconds) if elapsed_seconds > 0 else 0.0
    return {
        "total_agents": total_agents,
        "elapsed_seconds": to_jsonable_float(elapsed_seconds),
        "throughput_rps": to_jsonable_float(throughput),
        "latency_ms": {
            "p50": to_jsonable_float(lat.p50_ms),
            "p95": to_jsonable_float(lat.p95_ms),
            "p99": to_jsonable_float(lat.p99_ms),
        },
        "batch_schedule_offsets": [to_jsonable_float(v) for v in batch_schedule_offsets],
        "batch_actual_offsets": [to_jsonable_float(v) for v in batch_actual_offsets],
        "model_limits": model_limit_report,
    }

