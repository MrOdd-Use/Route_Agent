"""Project structure audit checks for stale or redundant repository metadata."""

from __future__ import annotations

import argparse
import re
import sys
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
    """Return repository root path inferred from this script location."""
    return Path(__file__).resolve().parents[1]


def _iter_markdown_files(root: Path) -> list[Path]:
    """List repository markdown files while excluding local virtual env folders."""
    return [p for p in root.rglob("*.md") if ".venv" not in p.parts]


def _find_absolute_local_links(root: Path) -> list[str]:
    """Find markdown links that embed machine-specific absolute local paths."""
    issues: list[str] = []
    for md in _iter_markdown_files(root):
        text = md.read_text(encoding="utf-8-sig")
        for target in _LINK_RE.findall(text):
            normalized = target.strip().lower()
            if normalized.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if normalized.startswith(_ABS_LOCAL_PREFIXES):
                issues.append(f"{md}: absolute local link -> {target}")
    return issues


def _find_readme_staleness(root: Path) -> list[str]:
    """Check README high-signal sections for known stale statements."""
    issues: list[str] = []
    readme = root / "README.md"
    if not readme.exists():
        return ["README.md: missing"]

    text = readme.read_text(encoding="utf-8-sig")
    if "## API Endpoints (Planned)" not in text:
        issues.append("README.md: API section should be explicitly marked as planned.")
    if "not wired in the current codebase yet" not in text:
        issues.append("README.md: API section should state endpoints are not wired yet.")
    if "uv sync --dev" not in text:
        issues.append("README.md: Quick Start should include uv-based setup command.")
    return issues


def _find_declared_test_path_drift(root: Path) -> list[str]:
    """Verify AGENTS test paths exist and contain at least one test file."""
    issues: list[str] = []
    agents = root / "AGENTS.md"
    if not agents.exists():
        return ["AGENTS.md: missing"]

    text = agents.read_text(encoding="utf-8-sig")
    declared_paths = sorted(set(re.findall(r"`([^`]*tests/[^`]*)`", text)))
    for rel in declared_paths:
        path = root / rel
        if not path.exists():
            issues.append(f"AGENTS.md: declared test path missing -> {rel}")
            continue
        tests = list(path.rglob("test_*.py"))
        if not tests:
            issues.append(f"AGENTS.md: declared test path has no test files -> {rel}")
    return issues


def run_audit(strict: bool = False) -> int:
    """Run all structure audits and print findings."""
    root = _repo_root()
    checks = (
        _find_absolute_local_links(root),
        _find_readme_staleness(root),
        _find_declared_test_path_drift(root),
    )
    issues = [item for group in checks for item in group]

    if not issues:
        print("Project audit passed: no stale-structure findings.")
        return 0

    print("Project audit findings:")
    for issue in issues:
        print(f"- {issue}")

    if strict:
        return 1
    return 0


def main() -> int:
    """Parse CLI args and run project audit checks."""
    parser = argparse.ArgumentParser(description="Audit repository structure for stale metadata.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with non-zero code when findings are detected.",
    )
    args = parser.parse_args()
    return run_audit(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
