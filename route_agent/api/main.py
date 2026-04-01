"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from route_agent.api.config import ApiSettings
from route_agent.api.dependencies import get_api_settings
from route_agent.api.middleware import RequestIdMiddleware, RequestTimingMiddleware
from route_agent.api.routes import all_routers
from route_agent.federation.api.routes import federation_routers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Execute `_lifespan`."""
    logger.info("Route Agent API starting")
    yield
    logger.info("Route Agent API shutting down")


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build and return a configured FastAPI application."""
    resolved = settings or get_api_settings()

    app = FastAPI(
        title="Route Agent API",
        description="Intelligent LLM routing system REST API",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=resolved.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    for r in all_routers:
        app.include_router(r, prefix=resolved.api_prefix)

    for r in federation_routers:
        app.include_router(r, prefix=resolved.api_prefix)

    @app.exception_handler(Exception)
    async def _global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Execute `_global_exception_handler`."""
        request_id = getattr(request.state, "request_id", None)
        logger.exception("unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": str(exc),
                "request_id": request_id,
            },
        )

    return app


def run_server() -> None:
    """Entry point for console script."""
    import uvicorn

    resolved = get_api_settings()
    uvicorn.run(
        create_app(resolved),
        host=resolved.host,
        port=resolved.port,
        log_level=resolved.log_level,
    )
