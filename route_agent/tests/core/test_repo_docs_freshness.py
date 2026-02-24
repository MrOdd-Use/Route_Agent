"""Repository-level tests for documentation freshness and portability."""

from __future__ import annotations

import re
from pathlib import Path

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_ABS_LOCAL_PREFIXES = (
    "/d:/",
    "/c:/",
    "/users/",
    "/home/",
    "d:\\",
    "c:\\",
)


def _repo_root() -> Path:
    """Return repository root path for repo-wide document checks."""
    return Path(__file__).resolve().parents[3]


def test_markdown_links_do_not_use_machine_specific_absolute_paths() -> None:
    """Keep markdown links portable across machines and CI environments."""
    root = _repo_root()
    failures: list[str] = []
    for md in root.rglob("*.md"):
        if ".venv" in md.parts:
            continue
        text = md.read_text(encoding="utf-8-sig")
        for target in _LINK_RE.findall(text):
            normalized = target.strip().lower()
            if normalized.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if normalized.startswith(_ABS_LOCAL_PREFIXES):
                failures.append(f"{md}: {target}")

    assert not failures, "Found machine-specific absolute links:\n" + "\n".join(failures)


def test_readme_api_section_is_marked_as_planned() -> None:
    """Avoid implying REST API endpoints are already implemented."""
    readme = (_repo_root() / "README.md").read_text(encoding="utf-8-sig")
    assert "## API Endpoints (Planned)" in readme
    assert "not wired in the current codebase yet" in readme
