"""Enforce repository docstring policy for Python source files."""

from __future__ import annotations

import ast
from pathlib import Path


def _iter_python_files() -> list[Path]:
    """List Python source files under route_agent and scripts directories."""
    repo_root = Path(__file__).resolve().parents[3]
    source_roots = [repo_root / "route_agent", repo_root / "scripts"]
    files: list[Path] = []
    for root in source_roots:
        if not root.exists():
            continue
        files.extend([p for p in root.rglob("*.py") if "__pycache__" not in p.parts])
    return files


def test_all_modules_classes_and_functions_have_docstrings() -> None:
    """Fail when any module, class, or function is missing a docstring."""
    missing: list[str] = []
    for path in _iter_python_files():
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source)

        if ast.get_docstring(tree) is None:
            missing.append(f"{path}: module")

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if ast.get_docstring(node) is None:
                    missing.append(f"{path}:{node.lineno} class {node.name}")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.get_docstring(node) is None:
                    missing.append(f"{path}:{node.lineno} function {node.name}")

    assert not missing, "Missing docstrings:\n" + "\n".join(missing)
