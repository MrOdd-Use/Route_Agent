"""GET /models endpoint."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Query

from route_agent.api.dependencies import get_model_pool
from route_agent.api.schemas import ModelsResponse

router = APIRouter()


@router.get("/models", response_model=ModelsResponse)
async def list_models(provider: str | None = Query(default=None)) -> ModelsResponse:
    """Return available models from the registry pool."""
    pool = get_model_pool()
    if pool is None:
        return ModelsResponse(models=[], total=0, pool_summary={})

    available = pool.list_available(provider=provider)
    models_data: list[dict[str, Any]] = []
    for m in available:
        try:
            models_data.append(asdict(m))
        except Exception:
            models_data.append({"model_id": m.model_id, "provider": m.provider, "display_name": m.display_name})

    return ModelsResponse(
        models=models_data,
        total=len(models_data),
        pool_summary=pool.summary(),
    )
