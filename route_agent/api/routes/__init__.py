"""API route sub-package collecting all routers."""

from route_agent.api.routes.dashboard import router as dashboard_router
from route_agent.api.routes.health import router as health_router
from route_agent.api.routes.models import router as models_router
from route_agent.api.routes.monitoring import router as monitoring_router
from route_agent.api.routes.pool_status import router as pool_status_router
from route_agent.api.routes.route import router as route_router
from route_agent.api.routes.stats import router as stats_router

all_routers = [
    health_router,
    route_router,
    models_router,
    pool_status_router,
    stats_router,
    monitoring_router,
    dashboard_router,
]
