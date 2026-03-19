"""Pool-status API endpoints used by dashboard model views."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from route_agent.api.dependencies import build_router_engine, get_api_settings, get_registry_context
from route_agent.api.pool_status import build_class_pool_detail_payload, build_global_pool_status_payload
from route_agent.api.schemas import (
    ClassPoolDetailResponse,
    ClassPoolListResponse,
    GlobalPoolStatusResponse,
    ManualPoolAddRequest,
    ManualPoolOperationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/pool-status/global", response_model=GlobalPoolStatusResponse)
async def get_global_pool_status() -> GlobalPoolStatusResponse:
    """Return aggregated status cards for all models in the global pool."""
    context = get_registry_context()
    if context is None:
        return GlobalPoolStatusResponse()

    try:
        from route_agent.monitoring import get_model_execution_metrics_async

        execution_metrics = await get_model_execution_metrics_async()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_global_pool_status metrics failed: %s", exc)
        execution_metrics = []

    payload = build_global_pool_status_payload(
        models=context.report.models,
        execution_metrics=execution_metrics,
    )
    return GlobalPoolStatusResponse(**payload)


@router.get("/pool-status/classes", response_model=ClassPoolListResponse)
async def get_class_pool_list() -> ClassPoolListResponse:
    """Return directory cards for existing class pools."""
    context = get_registry_context()
    if context is None:
        return ClassPoolListResponse()

    try:
        engine = build_router_engine(context.pool, get_api_settings())
        items = await engine.list_pools_async()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_class_pool_list failed: %s", exc)
        return ClassPoolListResponse()

    classes = sorted(items, key=lambda item: str(item.get("agent_class") or ""))
    return ClassPoolListResponse(classes=classes, count=len(classes))


@router.get("/pool-status/classes/{agent_class}", response_model=ClassPoolDetailResponse)
async def get_class_pool_detail(agent_class: str) -> ClassPoolDetailResponse:
    """Return detail cards for one class pool."""
    context = get_registry_context()
    if context is None:
        raise HTTPException(status_code=503, detail="registry context unavailable")

    try:
        engine = build_router_engine(context.pool, get_api_settings())
        pool_entries = await engine.inspect_pool_async(agent_class)
        if not pool_entries:
            raise HTTPException(status_code=404, detail=f"class pool '{agent_class}' not found")
        default_model_ids = await engine.list_default_model_ids_async(agent_class)
        class_list = await engine.list_pools_async()
        history_rows = await engine.query_class_history_async(agent_class, limit=500)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_class_pool_detail failed for agent_class=%s: %s", agent_class, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    default_model = None
    description = ""
    for item in class_list:
        if str(item.get("agent_class") or "") == agent_class:
            default_model = item.get("default_model")
            description = str(item.get("description") or "")
            break
    payload = build_class_pool_detail_payload(
        agent_class=agent_class,
        description=description,
        default_model=default_model,
        pool_entries=pool_entries,
        default_model_ids=default_model_ids,
        history_rows=history_rows,
        model_resolver=context.pool.get,
    )
    return ClassPoolDetailResponse(**payload)


@router.post(
    "/pool-status/classes/{agent_class}/models",
    response_model=ManualPoolOperationResponse,
)
async def add_model_to_class_pool(
    agent_class: str,
    body: ManualPoolAddRequest,
) -> ManualPoolOperationResponse:
    """手动添加模型到类池（渠道2：与自动统计入池并行）。"""
    context = get_registry_context()
    if context is None:
        raise HTTPException(status_code=503, detail="registry context unavailable")

    try:
        engine = build_router_engine(context.pool, get_api_settings())
        result = await engine.add_model_to_pool_async(agent_class, body.model_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("add_model_to_class_pool failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("reason", "unknown error"))

    return ManualPoolOperationResponse(**result)


@router.delete(
    "/pool-status/classes/{agent_class}/models/{model_id:path}",
    response_model=ManualPoolOperationResponse,
)
async def remove_model_from_class_pool(
    agent_class: str,
    model_id: str,
) -> ManualPoolOperationResponse:
    """手动从类池移除模型。"""
    context = get_registry_context()
    if context is None:
        raise HTTPException(status_code=503, detail="registry context unavailable")

    try:
        engine = build_router_engine(context.pool, get_api_settings())
        result = await engine.remove_model_from_pool_async(agent_class, model_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("remove_model_from_class_pool failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail=f"model '{model_id}' not in pool '{agent_class}'")

    return ManualPoolOperationResponse(**result)
