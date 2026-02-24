"""Scoring utilities for router_engine."""

from __future__ import annotations

import math
from typing import Any

from route_agent.model_registry.constants import PRICE_UNAVAILABLE_SENTINEL
from route_agent.router_engine.constants import COST_ALPHA, PRICE_CAP
from route_agent.task_analyzer.schemas import DimensionScore

_OUTPUT_HEAVY_DIMS: set[str] = {"coding", "creative_writing"}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def compute_dimension_score(
    relevant_dimensions: tuple[DimensionScore, ...],
    model_capabilities: dict[str, Any],
) -> float:
    """Compute weighted capability match score in [0, 1]."""
    if not relevant_dimensions:
        return 0.0

    weighted_sum = 0.0
    total_weight = 0.0

    for dim in relevant_dimensions:
        weight = float(dim.score)
        cap_raw = model_capabilities.get(dim.dimension)
        cap_value = _safe_float(cap_raw)
        if cap_value is None:
            cap_value = 50.0
        normalized = _clip(cap_value / 100.0, 0.0, 1.0)
        weighted_sum += weight * normalized
        total_weight += weight

    if total_weight <= 0:
        return 0.0
    return _clip(weighted_sum / total_weight, 0.0, 1.0)


def compute_effective_price_per_1m(
    pricing: dict[str, Any],
    relevant_dimensions: tuple[DimensionScore, ...],
) -> float:
    """Compute effective price in USD per 1M tokens.

    The source pricing format is expected as per_1k_tokens.
    """
    input_price = _safe_float(pricing.get("input"))
    output_price = _safe_float(pricing.get("output"))

    input_price = input_price if input_price is not None else PRICE_UNAVAILABLE_SENTINEL
    output_price = output_price if output_price is not None else PRICE_UNAVAILABLE_SENTINEL

    output_signal = sum(
        dim.score for dim in relevant_dimensions if dim.dimension in _OUTPUT_HEAVY_DIMS
    ) / 10.0
    w_out = 0.3 + 0.4 * min(output_signal, 1.0)
    w_in = 1.0 - w_out

    effective_price = (w_in * input_price) + (w_out * output_price)

    unit = str(pricing.get("unit") or "").strip().lower()
    if unit == "per_1m_tokens":
        return effective_price
    # Default path: per_1k_tokens -> per_1M_tokens
    return effective_price * 1000.0


def compute_cost_score(
    pricing: dict[str, Any],
    relevant_dimensions: tuple[DimensionScore, ...],
) -> float:
    """Compute normalized cost score in [0, 1], lower is cheaper."""
    effective_price_per_1m = compute_effective_price_per_1m(pricing, relevant_dimensions)

    if effective_price_per_1m >= PRICE_UNAVAILABLE_SENTINEL:
        return 1.0

    ratio = _clip(effective_price_per_1m / PRICE_CAP, 0.0, 1.0)
    if ratio >= 1.0:
        return 1.0

    numerator = math.exp(COST_ALPHA * ratio) - 1.0
    denominator = math.exp(COST_ALPHA) - 1.0
    if denominator <= 0:
        return ratio

    return _clip(numerator / denominator, 0.0, 1.0)
