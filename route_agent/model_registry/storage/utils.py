"""Shared utilities for storage backends."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalize datetime value to timezone-aware UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_snapshot_version(now: datetime) -> str:
    """Build a human-readable unique snapshot version."""
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    return f"registry-{ts}-{uuid4().hex[:8]}"


def error_summary(errors: dict[str, str]) -> str | None:
    """Compress provider errors into one text field for quick inspection."""
    if not errors:
        return None
    return " | ".join(f"{provider}: {message}" for provider, message in errors.items())
