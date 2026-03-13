"""ASGI middleware for request tracing and timing."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Represent `RequestIdMiddleware`."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Execute `dispatch`."""
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Represent `RequestTimingMiddleware`."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Execute `dispatch`."""
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Response-Time-Ms"] = f"{elapsed_ms:.1f}"
        return response
