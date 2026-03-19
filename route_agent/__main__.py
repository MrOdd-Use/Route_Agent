"""Module execution entry for `python -m route_agent`."""

from __future__ import annotations

import sys


def _entry() -> int:
    """Dispatch to API server, pool management, or route CLI."""
    if "--serve" in sys.argv:
        sys.argv.remove("--serve")
        from route_agent.api.main import run_server

        run_server()
        return 0

    if len(sys.argv) > 1 and sys.argv[1] == "pool":
        from route_agent.app.pool_cli import pool_main

        return pool_main(sys.argv[2:])

    from route_agent.app.cli import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_entry())
