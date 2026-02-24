"""Application-level entrypoints for Route Agent."""

from route_agent.app.cli import main
from route_agent.app.service import run_route_agent

__all__ = ["main", "run_route_agent"]
