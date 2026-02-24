"""Pricing utilities for model metadata normalization.

The pricing package currently exports dynamic-pricing capabilities used by
provider normalization helpers. Static pricing policy lives in
`providers/utils.py` and can fall back when dynamic fetch fails.
"""

from route_agent.model_registry.pricing.dynamic import (
    DynamicPricingResolver,
    create_dynamic_pricing_resolver,
)

__all__ = ["DynamicPricingResolver", "create_dynamic_pricing_resolver"]

