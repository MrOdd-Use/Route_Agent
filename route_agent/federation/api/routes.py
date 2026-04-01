"""Federation API routes.

Endpoints:
    POST /apps/register
    POST /concurrency/acquire
    POST /concurrency/release
    POST /outcomes/report
    GET  /pool-version
    GET  /mode
"""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from route_agent.federation.api.schemas import (
    AcquireLeaseRequest,
    AcquireLeaseResponse,
    FederationAppRegisterRequest,
    FederationAppRegisterResponse,
    FederationModelScore,
    FederationScoresResponse,
    ModeQueryResponse,
    PoolVersionQueryResponse,
    ReleaseLeaseRequest,
    ReleaseLeaseResponse,
    ReportOutcomeRequest,
    ReportOutcomeResponse,
)
from route_agent.federation.schemas import AgentDeclaration, AppRegisterRequest
from route_agent.federation.server.app_registry import AppRegistry
from route_agent.federation.server.lease_limiter import LeaseLimiter
from route_agent.federation.server.lease_store import LeaseStore
from route_agent.federation.server.outcome_processor import OutcomeProcessor
from route_agent.federation.server.pool_version import PoolVersionManager
from route_agent.federation.server.storage import FederationStorage, validate_federation_db_path

logger = logging.getLogger(__name__)
_GENERIC_SERVER_ERROR = "internal federation service error"
_DEFAULT_LEASE_TTL_S = 300
_LEASE_TTL_MULTIPLIER = 2

# ---------------------------------------------------------------------------
# Dependency singletons (lru_cache, reset on test via cache_clear)
# ---------------------------------------------------------------------------


def _federation_db_path() -> Path:
    """Return the SQLite path for federation storage (env override or default)."""
    raw = os.getenv("FEDERATION_DB_PATH", "")
    if raw:
        return validate_federation_db_path(raw)
    return validate_federation_db_path(
        Path(__file__).resolve().parents[4] / "data" / "federation.db"
    )


@functools.lru_cache(maxsize=1)
def _get_storage() -> FederationStorage:
    """Return the shared FederationStorage singleton."""
    return FederationStorage(_federation_db_path())


@functools.lru_cache(maxsize=1)
def _get_pool_version_mgr() -> PoolVersionManager:
    """Return the shared PoolVersionManager singleton."""
    return PoolVersionManager(_get_storage())


@functools.lru_cache(maxsize=1)
def _get_app_registry() -> AppRegistry:
    """Return the shared AppRegistry singleton."""
    return AppRegistry(_get_storage(), _get_pool_version_mgr())


@functools.lru_cache(maxsize=1)
def _get_lease_store() -> LeaseStore:
    """Return the shared LeaseStore singleton."""
    return LeaseStore()


@functools.lru_cache(maxsize=1)
def _get_lease_limiter() -> LeaseLimiter:
    """Return the shared LeaseLimiter singleton."""
    return LeaseLimiter(_get_lease_store())


@functools.lru_cache(maxsize=1)
def _get_outcome_processor() -> OutcomeProcessor:
    """Return the shared OutcomeProcessor singleton."""
    return OutcomeProcessor(_get_storage(), _get_pool_version_mgr())


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

_apps_router = APIRouter(prefix="/apps", tags=["federation"])
_concurrency_router = APIRouter(prefix="/concurrency", tags=["federation"])
_outcomes_router = APIRouter(prefix="/outcomes", tags=["federation"])
_pool_router = APIRouter(tags=["federation"])
_mode_router = APIRouter(tags=["federation"])


# ---------------------------------------------------------------------------
# POST /apps/register
# ---------------------------------------------------------------------------


@_apps_router.post("/register", response_model=FederationAppRegisterResponse)
async def register_app(body: FederationAppRegisterRequest) -> FederationAppRegisterResponse:
    """Register an app and its declared agents; return current pool versions."""
    request = AppRegisterRequest(
        app_id=body.app_id,
        app_name=body.app_name,
        agents=tuple(
            AgentDeclaration(
                agent_name=a.agent_name,
                agent_class=a.agent_class,
                agent_version=a.agent_version,
            )
            for a in body.agents
        ),
    )
    try:
        resp = await _get_app_registry().register_async(request)
    except Exception as exc:
        logger.exception("register_app failed | app_id=%s", body.app_id)
        raise HTTPException(status_code=500, detail=_GENERIC_SERVER_ERROR) from exc

    return FederationAppRegisterResponse(
        app_id=resp.app_id,
        registered_at=resp.registered_at.isoformat(),
        pool_versions=resp.pool_versions,
    )


# ---------------------------------------------------------------------------
# POST /concurrency/acquire
# ---------------------------------------------------------------------------


@_concurrency_router.post("/acquire", response_model=AcquireLeaseResponse)
async def acquire_lease(body: AcquireLeaseRequest) -> AcquireLeaseResponse:
    """Acquire a concurrency lease; may override the proposed model on contention."""
    limiter = _get_lease_limiter()
    decision = limiter.decide_mode(
        agent_class=body.agent_class,
        model_id=body.proposed_model_id,
    )

    reason: str | None = None
    if decision.mode == "local":
        granted_model_id = body.proposed_model_id
    else:
        alternatives = [m for m in (body.candidate_model_ids or []) if m != body.proposed_model_id]
        granted_model_id = alternatives[0] if alternatives else body.proposed_model_id
        reason = (
            f"{body.proposed_model_id} contention "
            f"({decision.active_leases} active leases)"
        )

    ttl = (
        body.estimated_duration_s * _LEASE_TTL_MULTIPLIER
        if body.estimated_duration_s
        else _DEFAULT_LEASE_TTL_S
    )
    lease = _get_lease_store().acquire(
        app_id=body.app_id,
        agent_name=body.agent_name,
        agent_class=body.agent_class,
        proposed_model_id=body.proposed_model_id,
        granted_model_id=granted_model_id,
        mode=decision.mode,
        ttl_seconds=ttl,
    )

    pool_entry = await _get_pool_version_mgr().get_async(body.agent_class)
    pool_version = pool_entry.version if pool_entry else 0

    return AcquireLeaseResponse(
        lease_id=lease.lease_id,
        granted=True,
        mode=decision.mode,
        granted_model_id=granted_model_id,
        pool_version=pool_version,
        pool_changed=False,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# POST /concurrency/release
# ---------------------------------------------------------------------------


@_concurrency_router.post("/release", response_model=ReleaseLeaseResponse)
async def release_lease(body: ReleaseLeaseRequest) -> ReleaseLeaseResponse:
    """Release a concurrency lease after execution completes."""
    released = _get_lease_store().release(body.lease_id)
    return ReleaseLeaseResponse(released=released)


# ---------------------------------------------------------------------------
# POST /outcomes/report
# ---------------------------------------------------------------------------


@_outcomes_router.post("/report", response_model=ReportOutcomeResponse)
async def report_outcome(body: ReportOutcomeRequest) -> ReportOutcomeResponse:
    """Accept an execution outcome; triggers pool reorder when statistically significant."""
    try:
        await _get_outcome_processor().process_async(
            model_id=body.model_id,
            agent_class=body.agent_class,
            outcome_type=body.outcome_type,
            duration_ms=body.duration_ms,
            quality_score=body.quality_score,
        )
    except Exception as exc:
        logger.exception("report_outcome failed | model=%s class=%s", body.model_id, body.agent_class)
        raise HTTPException(status_code=500, detail=_GENERIC_SERVER_ERROR) from exc

    return ReportOutcomeResponse(accepted=True)


# ---------------------------------------------------------------------------
# GET /pool-version
# ---------------------------------------------------------------------------


@_pool_router.get("/pool-version", response_model=PoolVersionQueryResponse)
async def get_pool_version(classes: str = "") -> PoolVersionQueryResponse:
    """Return current pool versions for the requested comma-separated agent classes."""
    requested = [c.strip() for c in classes.split(",") if c.strip()] if classes else []
    versions = await _get_pool_version_mgr().get_versions_async(requested)
    return PoolVersionQueryResponse(versions=versions)


# ---------------------------------------------------------------------------
# GET /mode
# ---------------------------------------------------------------------------


@_mode_router.get("/mode", response_model=ModeQueryResponse)
async def get_mode(agent_class: str = "", model_id: str = "") -> ModeQueryResponse:
    """Query the current mode decision for an (agent_class, model_id) pair."""
    if not agent_class or not model_id:
        raise HTTPException(status_code=422, detail="agent_class and model_id are required")

    decision = _get_lease_limiter().decide_mode(agent_class=agent_class, model_id=model_id)
    return ModeQueryResponse(
        agent_class=decision.agent_class,
        model_id=decision.model_id,
        mode=decision.mode,
        active_leases=decision.active_leases,
        threshold=decision.threshold,
    )


# ---------------------------------------------------------------------------
# GET /federation-scores/{agent_class}
# ---------------------------------------------------------------------------

_scores_router = APIRouter(tags=["federation"])


@_scores_router.get("/federation-scores/{agent_class}", response_model=FederationScoresResponse)
async def get_federation_scores(agent_class: str) -> FederationScoresResponse:
    """Return aggregated federation scores for all models in one agent class."""
    storage = _get_storage()
    rows = await storage.get_outcome_stats_for_class_async(agent_class)
    scores = [
        FederationModelScore(
            model_id=row["model_id"],
            success_count=int(row.get("success_count") or 0),
            fail_count=int(row.get("fail_count") or 0),
            total_count=int(row.get("total_count") or 0),
            success_rate=_safe_rate(row),
        )
        for row in rows
    ]
    return FederationScoresResponse(agent_class=agent_class, scores=scores)


def _safe_rate(row: dict) -> float:
    """Compute success_rate from a stats row, returning 0.0 on zero total."""
    total = int(row.get("total_count") or 0)
    if total <= 0:
        return 0.0
    return int(row.get("success_count") or 0) / total


# ---------------------------------------------------------------------------
# All federation routers (consumed by api/main.py)
# ---------------------------------------------------------------------------

federation_routers = [
    _apps_router,
    _concurrency_router,
    _outcomes_router,
    _pool_router,
    _scores_router,
    _mode_router,
]
