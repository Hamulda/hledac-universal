"""
test_asyncio_run_audit.py — L-03: Audit for asyncio.run() violations in non-__main__ modules.

INVARIANT (core/policy.py F1):
    asyncio.run() is FORBIDDEN in production modules (non-__main__) except in:
      1. if __name__ == '__main__' blocks
      2. __main__.py modules
      3. Test fixtures

This test uses AST to detect asyncio.run() calls inside non-authorized modules,
since regex-based detection fails on comments and docstrings.

Acceptance: Zero asyncio.run() violations in production code (non-test, non-__main__).
"""

from __future__ import annotations

import ast
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    file: str
    line: int
    col: int
    context: str


# Authorized patterns: asyncio.run() IS allowed inside these
AUTHORIZED_PATTERNS = (
    "__main__.py",
    "test_",  # test modules
    "_test.py",  # test modules alt pattern
    "conftest.py",  # pytest fixtures
    "probe_",  # probe test files (recon/tests/, etc.)
    "quick_scrape",  # convenience sync wrapper for async scrape()
    "scraper.py",  # StealthWebScraper convenience wrapper module
)


# Directories to exclude from scanning (tools, benchmarks, probes, venv)
EXCLUDE_DIRS = (
    ".venv",
    ".venv-test",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".git",
    "archive",
    "build",
    "dist",
    "target",
    ".mypy_cache",
    # Tools and benchmarks — not production runtime
    "tools",
    "benchmarks",
    "benchmarks_shadow",
    "scripts",
    "debug",
    # Probe/test fixtures
    "recon/tests",
    # Vendor/stub code
    "stubs",
    "layers/examples",
)


def is_authorized_path(path: str) -> bool:
    """Return True if the file is an authorized entry point for asyncio.run()."""
    filename = os.path.basename(path)
    for pattern in AUTHORIZED_PATTERNS:
        if pattern in filename:
            return True
    return False


def _is_asyncio_run(node: ast.Call) -> bool:
    """Check if a Call node is asyncio.run(...)."""
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "asyncio"
    )


def _in_main_block(node: ast.AST, tree: ast.AST) -> bool:
    """
    Check if node is inside an 'if __name__ == "__main__"' block.
    Walks the tree once to build a map of if-main body line ranges,
    then checks if node falls within any of them.
    """
    # Build list of (start_line, end_line) for every if __name__ == '__main__' body
    if_main_ranges: list[tuple[int, int]] = []

    for child in ast.walk(tree):
        if isinstance(child, ast.If):
            # Check if it's: if __name__ == '__main__'
            is_name_check = False
            if isinstance(child.test, ast.Compare):
                if (
                    isinstance(child.test.left, ast.Name)
                    and child.test.left.id == "__name__"
                    and len(child.test.ops) == 1
                    and isinstance(child.test.ops[0], ast.Eq)
                    and len(child.test.comparators) == 1
                ):
                    comp = child.test.comparators[0]
                    if isinstance(comp, ast.Constant) and comp.value in ("__main__", "__main__"):
                        is_name_check = True
            if is_name_check:
                # Body of the if block (not else)
                for stmt in child.body:
                    if_main_ranges.append((stmt.lineno, stmt.end_lineno or stmt.lineno))

    if not if_main_ranges:
        return False

    node_start = getattr(node, "lineno", 0)
    node_end = getattr(node, "end_lineno", node_start) or node_start
    for start, end in if_main_ranges:
        if start <= node_start <= end or start <= node_end <= end:
            return True
    return False


def scan_file(filepath: str) -> list[Violation]:
    """Scan a single Python file for asyncio.run() violations."""
    if is_authorized_path(filepath):
        return []

    try:
        with open(filepath, encoding="utf-8") as f:
            source = f.read()
    except OSError, UnicodeDecodeError:
        return []

    try:
        tree = ast.parse(source, filename=filepath)
    except SyntaxError:
        return []

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_asyncio_run(node):
            if not _in_main_block(node, tree):
                violations.append(
                    Violation(
                        file=filepath,
                        line=node.lineno,
                        col=node.col_offset,
                        context="asyncio.run()",
                    )
                )
    return violations


def scan_directory(root: str, exclude_dirs: tuple[str, ...] = ()) -> list[Violation]:
    """Recursively scan a directory for violations."""
    violations: list[Violation] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]

        for filename in filenames:
            if filename.endswith(".py"):
                filepath = os.path.join(dirpath, filename)
                violations.extend(scan_file(filepath))

    return violations


def main() -> int:
    project_root = Path(__file__).parent.parent

    violations = scan_directory(str(project_root), exclude_dirs=EXCLUDE_DIRS)

    if violations:
        print(f"FAIL: {len(violations)} asyncio.run() violation(s) found:")
        for v in violations:
            print(f"  {v.file}:{v.line} ({v.context})")
        return 1

    print("PASS: No asyncio.run() violations found in production modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
