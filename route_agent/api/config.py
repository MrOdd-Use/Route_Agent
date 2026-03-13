"""API configuration via environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings

from route_agent.app.contracts import RouteAgentRunOptions


class ApiSettings(BaseSettings):
    """Configuration for the Route Agent API server."""

    model_config = {"env_prefix": "ROUTE_AGENT_API_", "frozen": True}

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    cors_allow_credentials: bool = True
    api_prefix: str = "/api/v1"
    log_level: str = "info"

    limit: int = 8
    include_ollama: bool = False
    db_backend: str = "sqlite"
    sqlite_path: str | None = None
    postgres_dsn: str | None = None
    sync_interval_days: int = 30
    redis_url: str | None = None
    router_db_path: str | None = None
    rate_limit_mode: str = "auto"
    rate_limit_fail_strategy: str = "degrade"
    agent_name: str = "route_agent"

    def to_run_options(self) -> RouteAgentRunOptions:
        """Translate API settings into application run options."""
        return RouteAgentRunOptions(
            limit=self.limit,
            include_ollama=self.include_ollama,
            min_total_threshold=5,
            load_env_file=True,
            db_backend=self.db_backend,
            sqlite_path=self.sqlite_path,
            postgres_dsn=self.postgres_dsn,
            sync_interval_days=self.sync_interval_days,
            keep_registry_history=2,
            default_agent_name=self.agent_name,
            redis_url=self.redis_url,
            router_db_path=self.router_db_path,
            rate_limit_mode=self.rate_limit_mode,
            rate_limit_fail_strategy=self.rate_limit_fail_strategy,
        )
