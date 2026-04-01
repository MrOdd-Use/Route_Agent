"""In-memory hot-path lease store with TTL-based auto-expiry.

Active leases live here; historical records persist in FederationStorage (SQLite).
TTL expiry only clears hot-path concurrency state — never touches persistent mappings.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from route_agent.federation.schemas import ConcurrencyLease


@dataclass
class _LeaseEntry:
    """Internal wrapper pairing a ConcurrencyLease with its monotonic expiry time."""

    lease: ConcurrencyLease
    expires_at: float  # monotonic clock


class LeaseStore:
    """Thread-safe in-memory store for active concurrency leases."""

    def __init__(self) -> None:
        """Initialise an empty lease store with a threading lock."""
        self._leases: dict[str, _LeaseEntry] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    def acquire(
        self,
        app_id: str,
        agent_name: str,
        agent_class: str,
        proposed_model_id: str,
        granted_model_id: str,
        mode: str,
        ttl_seconds: int = 300,
    ) -> ConcurrencyLease:
        """Create and store a new active lease; return the ConcurrencyLease."""
        lease_id = str(uuid.uuid4())
        now_dt = datetime.now(tz=timezone.utc)
        lease = ConcurrencyLease(
            lease_id=lease_id,
            app_id=app_id,
            agent_name=agent_name,
            agent_class=agent_class,
            proposed_model_id=proposed_model_id,
            granted_model_id=granted_model_id,
            mode=mode,
            acquired_at=now_dt,
            ttl_seconds=ttl_seconds,
        )
        expires_at = time.monotonic() + ttl_seconds
        with self._lock:
            self._leases[lease_id] = _LeaseEntry(lease=lease, expires_at=expires_at)
        return lease

    def release(self, lease_id: str) -> bool:
        """Remove a lease by ID; return True if it existed."""
        with self._lock:
            return self._leases.pop(lease_id, None) is not None

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get(self, lease_id: str) -> ConcurrencyLease | None:
        """Return the lease if it exists and has not expired."""
        with self._lock:
            entry = self._leases.get(lease_id)
            if entry is None:
                return None
            if time.monotonic() >= entry.expires_at:
                del self._leases[lease_id]
                return None
            return entry.lease

    def active_count(self, model_id: str) -> int:
        """Return number of non-expired active leases for a model."""
        now = time.monotonic()
        with self._lock:
            return sum(
                1
                for entry in self._leases.values()
                if entry.lease.granted_model_id == model_id
                and now < entry.expires_at
            )

    def active_count_for_class(self, agent_class: str) -> int:
        """Return number of non-expired active leases for a class."""
        now = time.monotonic()
        with self._lock:
            return sum(
                1
                for entry in self._leases.values()
                if entry.lease.agent_class == agent_class
                and now < entry.expires_at
            )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """Remove all expired leases; return count removed."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._leases.items() if now >= v.expires_at]
            for key in expired:
                del self._leases[key]
        return len(expired)

    def snapshot(self) -> list[ConcurrencyLease]:
        """Return all non-expired leases (for diagnostics)."""
        now = time.monotonic()
        with self._lock:
            return [
                entry.lease
                for entry in self._leases.values()
                if now < entry.expires_at
            ]
