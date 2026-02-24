"""Main model pool built from model registry report.

This module converts a flat `ModelRegistryReport.models` list into an in-memory
selection pool with three stable routing tiers:
- fast
- smart
- strategic

The pool is intentionally heuristic and deterministic:
- explicit model IDs win if provided;
- otherwise select the cheapest available model inside each inferred tier;
- finally fall back to the globally cheapest available model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from route_agent.model_registry.constants import (
    PRICE_UNAVAILABLE_SENTINEL,
    TIER_KEYWORDS_FAST,
    TIER_KEYWORDS_STRATEGIC,
)
from route_agent.model_registry.schemas import ModelMetadata, ModelRegistryReport


def _safe_float(value: Any) -> float | None:
    """Best-effort float conversion used by pricing comparisons."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_model_available(model: ModelMetadata) -> bool:
    """Treat unknown status as available; only explicit bad states are filtered."""
    availability = str(model.status.get("availability") or "").strip().lower()
    if not availability:
        return True
    return availability not in {"down", "unavailable", "error", "offline"}


def _price_score(model: ModelMetadata) -> float:
    # Use input-token price for rough ordering.
    # Missing/invalid values are pushed to the end by using a large sentinel.
    value = _safe_float(model.pricing.get("input"))
    return value if value is not None else PRICE_UNAVAILABLE_SENTINEL


def _tier_hint(model: ModelMetadata) -> str:
    """Infer one routing tier from model ID/display-name keywords."""
    name = f"{model.model_id} {model.display_name}".lower()
    if any(token in name for token in TIER_KEYWORDS_STRATEGIC):
        return "strategic"
    if any(token in name for token in TIER_KEYWORDS_FAST):
        return "fast"
    return "smart"


@dataclass(slots=True)
class TierSelection:
    """Return type for one tier pick operation."""
    tier: str
    model: ModelMetadata | None
    reason: str


class MainModelPool:
    """In-memory pool with stable tiered slots: fast/smart/strategic."""

    def __init__(
        self,
        models: list[ModelMetadata],
        *,
        fast_model: str | None = None,
        smart_model: str | None = None,
        strategic_model: str | None = None,
    ) -> None:
        # Fast lookup by model_id for direct preference resolution.
        self._models_by_id: dict[str, ModelMetadata] = {m.model_id: m for m in models}
        # Grouped view is kept for potential provider-aware introspection.
        self._models_by_provider: dict[str, list[ModelMetadata]] = {}
        for model in models:
            self._models_by_provider.setdefault(model.provider, []).append(model)

        # Availability filtering is done once at construction time.
        self._available_models = [m for m in models if _is_model_available(m)]
        self._slots: dict[str, str | None] = {
            "fast": None,
            "smart": None,
            "strategic": None,
        }
        self._build_slots(
            fast_model=fast_model,
            smart_model=smart_model,
            strategic_model=strategic_model,
        )

    @classmethod
    def from_report(
        cls,
        report: ModelRegistryReport,
        *,
        fast_model: str | None = None,
        smart_model: str | None = None,
        strategic_model: str | None = None,
    ) -> "MainModelPool":
        """Convenience constructor from registry report."""
        return cls(
            report.models,
            fast_model=fast_model,
            smart_model=smart_model,
            strategic_model=strategic_model,
        )

    def _build_slots(
        self,
        *,
        fast_model: str | None,
        smart_model: str | None,
        strategic_model: str | None,
    ) -> None:
        """Populate fast/smart/strategic slots using explicit and heuristic rules."""
        explicit = {
            "fast": fast_model,
            "smart": smart_model,
            "strategic": strategic_model,
        }
        for tier, model_id in explicit.items():
            if model_id and model_id in self._models_by_id and self._models_by_id[model_id] in self._available_models:
                self._slots[tier] = model_id

        # Fill missing slots from tier hints.
        for tier in ("fast", "smart", "strategic"):
            if self._slots[tier] is None:
                picked = self._pick_best_by_tier_hint(tier)
                self._slots[tier] = picked.model_id if picked else None

        # Final fallback: globally cheapest available model.
        fallback = sorted(self._available_models, key=_price_score)
        fallback_id = fallback[0].model_id if fallback else None
        for tier in ("fast", "smart", "strategic"):
            if self._slots[tier] is None:
                self._slots[tier] = fallback_id

    def _pick_best_by_tier_hint(self, target_tier: str) -> ModelMetadata | None:
        """Pick the cheapest available model whose inferred tier matches target."""
        candidates = [
            m for m in self._available_models if _tier_hint(m) == target_tier
        ]
        if not candidates:
            return None
        # Prefer lower price within same tier bucket.
        candidates.sort(key=_price_score)
        return candidates[0]

    def get(self, model_id: str) -> ModelMetadata | None:
        """Get one model by exact model_id."""
        return self._models_by_id.get(model_id)

    def list_available(self, provider: str | None = None) -> list[ModelMetadata]:
        """List available models, optionally filtered by provider."""
        if provider is None:
            return list(self._available_models)
        return [m for m in self._available_models if m.provider == provider]

    def pick_tier(
        self,
        tier: str,
        *,
        max_cost: float | None = None,
        preferred_model: str | None = None,
    ) -> TierSelection:
        """Select one model for the requested tier with optional constraints.

        Selection precedence:
        1) `preferred_model` if valid and available.
        2) cheapest model within `max_cost` constraint.
        3) configured tier slot.
        4) global cheapest available fallback.
        """
        normalized_tier = tier.strip().lower()
        if normalized_tier not in {"fast", "smart", "strategic"}:
            normalized_tier = "smart"

        if preferred_model:
            preferred = self.get(preferred_model)
            if preferred and _is_model_available(preferred):
                return TierSelection(
                    tier=normalized_tier,
                    model=preferred,
                    reason=f"use preferred model '{preferred_model}' from request constraints",
                )

        slot_model_id = self._slots.get(normalized_tier)
        slot_model = self.get(slot_model_id) if slot_model_id else None

        if max_cost is not None:
            affordable = []
            for model in self._available_models:
                model_cost = _safe_float(model.pricing.get("input"))
                if model_cost is not None and model_cost <= max_cost:
                    affordable.append(model)
            if affordable:
                affordable.sort(key=_price_score)
                return TierSelection(
                    tier=normalized_tier,
                    model=affordable[0],
                    reason=f"selected cheapest affordable model under max_cost={max_cost}",
                )

        if slot_model and _is_model_available(slot_model):
            return TierSelection(
                tier=normalized_tier,
                model=slot_model,
                reason=f"selected tier slot '{normalized_tier}'",
            )

        available_sorted = sorted(self._available_models, key=_price_score)
        if available_sorted:
            return TierSelection(
                tier=normalized_tier,
                model=available_sorted[0],
                reason="slot model unavailable, fallback to cheapest available model",
            )

        return TierSelection(
            tier=normalized_tier,
            model=None,
            reason="no available model in pool",
        )

    def summary(self) -> dict[str, Any]:
        """Return lightweight pool diagnostics for logging/API responses."""
        return {
            "total_models": len(self._models_by_id),
            "available_models": len(self._available_models),
            "providers": sorted(self._models_by_provider.keys()),
            "slots": dict(self._slots),
        }
