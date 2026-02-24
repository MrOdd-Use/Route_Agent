"""Public API for router_engine."""

from __future__ import annotations

from route_agent.router_engine.class_pool import ClassPoolManager
from route_agent.router_engine.engine import RouterEngine
from route_agent.router_engine.escalation import EscalationManager
from route_agent.router_engine.health import HealthManager
from route_agent.router_engine.schemas import (
    ClassPoolDefault,
    ClassPoolEntry,
    EscalationResult,
    ExecutionAttempt,
    ModelAvailability,
    ModelCandidate,
    RouteConstraints,
    RouteDecision,
    RouteRequest,
)

__all__ = [
    "RouterEngine",
    "RouteRequest",
    "RouteConstraints",
    "RouteDecision",
    "ModelCandidate",
    "ExecutionAttempt",
    "EscalationResult",
    "ModelAvailability",
    "ClassPoolEntry",
    "ClassPoolDefault",
    "EscalationManager",
    "ClassPoolManager",
    "HealthManager",
    "route",
]


def route(engine: RouterEngine, request: RouteRequest) -> RouteDecision:
    """Convenience API mirroring `engine.route(request)`.

    Exposed at module level for lightweight integrations/tests.
    """
    return engine.route(request)
