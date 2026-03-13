"""Tests for API Pydantic schemas."""

from __future__ import annotations

from route_agent.api.schemas import (
    RouteConstraintsBody,
    RouteRequestBody,
    RouteResponse,
)


class TestRouteRequestBody:
    """Represent `TestRouteRequestBody`."""

    def test_valid_request(self) -> None:
        """Test valid request construction."""
        body = RouteRequestBody(
            agent_name="coder-agent",
            system_prompt="You are a coding expert.",
            task="Write a sorting algorithm",
        )
        assert body.agent_name == "coder-agent"
        assert body.system_prompt == "You are a coding expert."
        assert body.task == "Write a sorting algorithm"
        assert body.request_id is None
        assert body.constraints is None

    def test_empty_task_allowed(self) -> None:
        """Empty task should be accepted at schema level; API handles fallback."""
        body = RouteRequestBody(task="", agent_name="test")
        assert body.task == ""

    def test_defaults(self) -> None:
        """Test default field values."""
        body = RouteRequestBody(task="hello")
        assert body.agent_name is None
        assert body.system_prompt is None
        assert body.request_id is None
        assert body.agent_class is None
        assert body.constraints is None

    def test_with_constraints(self) -> None:
        """Test request with constraints attached."""
        body = RouteRequestBody(
            task="translate this",
            constraints=RouteConstraintsBody(
                max_cost=0.05,
                require_provider="deepseek",
            ),
        )
        assert body.constraints is not None
        assert body.constraints.max_cost == 0.05
        assert body.constraints.require_provider == "deepseek"
        assert body.constraints.preferred_model is None


class TestRouteConstraintsBody:
    """Represent `TestRouteConstraintsBody`."""

    def test_all_optional(self) -> None:
        """Test all constraint fields are optional."""
        c = RouteConstraintsBody()
        assert c.max_cost is None
        assert c.preferred_model is None
        assert c.exclude_models is None
        assert c.require_provider is None
        assert c.estimated_input_tokens is None


class TestRouteResponse:
    """Represent `TestRouteResponse`."""

    def test_shape(self) -> None:
        """Test response model shape."""
        resp = RouteResponse(
            model_used="deepseek:deepseek-chat",
            routing_reason="selected",
            candidates=[],
            analysis={"domain": "qa"},
            alerts=[],
            start_index=0,
            default_used=False,
            pool_hit=False,
            pool_class=None,
            class_source="llm",
        )
        assert resp.model_used == "deepseek:deepseek-chat"
        assert resp.routing_reason == "selected"
