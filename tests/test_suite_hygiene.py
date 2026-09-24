"""Suite hygiene checks.

These do not test the product; they test the test suite itself, because two ways
of silently losing coverage showed up during development:

1. A duplicate test function name inside one file. Python keeps the last
   definition, so pytest collects it once and the earlier body becomes dead code
   — the suite still reports "all passed", just with fewer tests. This happened
   while inserting a helper above an existing test, and was only noticed by
   comparing the collected count against the expected number.
2. A test module that edits `sys.path`/imports at collection time and thus can
   only be collected in one order. Not checked here (pytest's own collection
   covers the common case); noted so the next person knows it was considered.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py"))


def test_no_duplicate_test_function_names_in_a_file():
    problems: list[str] = []
    for path in _test_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - pytest would fail first
            problems.append(f"{path.name}: syntax error: {exc}")
            continue
        seen: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    seen.setdefault(node.name, 0)
                    seen[node.name] += 1
            # test classes can shadow too
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                inner: dict[str, int] = {}
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        inner[item.name] = inner.get(item.name, 0) + 1
                for name, count in inner.items():
                    if count > 1:
                        problems.append(f"{path.name}::{node.name}.{name} defined {count}x")
        for name, count in seen.items():
            if count > 1:
                problems.append(f"{path.name}::{name} defined {count}x")

    assert not problems, (
        "duplicate test definitions silently drop coverage (only the last one is "
        "collected):\n  " + "\n  ".join(problems)
    )


def test_every_test_file_actually_defines_tests():
    """A file named test_*.py with no test functions is almost always a mistake
    (a helper module that got the wrong name)."""
    empty = [p.name for p in _test_files() if not _has_collectable_test(p)]
    assert not empty, f"test modules with no test functions: {empty}"


def _has_collectable_test(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover
        return True
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            return True
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            return True
    return False


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.name)
def test_test_file_parses(path: Path):
    ast.parse(path.read_text(encoding="utf-8"))
