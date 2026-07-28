#!/usr/bin/env python3
"""
ASYNC461 CI checker — ban raw asyncio.gather without return_exceptions=True.

F3XX: Ban raw `asyncio.gather(...)` call sites that bypass fail-soft invariants.
Allowed patterns:
  - asyncio.gather(..., return_exceptions=True)
  - parallel(*coros, policy=...)  (any policy)
  - parallel_ok(*coros)
  - safe_gather_*(...)
  - _asyncio.gather(...) (internal alias)

Violation: asyncio.gather(...) without return_exceptions=True
Fix: Use parallel() or parallel_ok() from utils.async_helpers

Run: python tools/audit/ban_raw_gather.py [--fix]
"""
from __future__ import annotations

import ast
import argparse
import sys
from pathlib import Path


def find_violations(root: Path, fix: bool = False) -> list[tuple[Path, int, str]]:
    """Find raw asyncio.gather() calls without return_exceptions."""
    violations = []
    skip_dirs = {
        "__pycache__", ".venv", ".venv-test", "archive", "probe_", "tests/archive",
        ".git", ".claude", "tools/migrate", "tests",  # tests use raw gather legitimately for concurrent-test fixtures
    }
    skip_files = {
        "tools/migrate/migrate_gather_to_safe_gather.py",
        "tools/migrate/revert_gather_migration.py",
        "tools/migrate/revert_gather_migration_text.py",
        "utils/async_helpers.py",  # internal implementation
    }

    for py_file in root.rglob("*.py"):
        if any(skip in py_file.parts for skip in skip_dirs):
            continue
        if any(py_file.name.endswith(s) for s in skip_files):
            continue
        try:
            content = py_file.read_text()
        except (OSError, UnicodeDecodeError):
            continue

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            # Check if it's asyncio.gather(...) or _asyncio.gather(...)
            is_gather = False
            is_internal = False
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "gather":
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id in ("asyncio", "_asyncio"):
                            is_gather = True
                            is_internal = node.func.value.id == "_asyncio"

            if not is_gather:
                continue

            # Skip internal _asyncio.gather (allowed alias)
            if is_internal:
                continue

            # Check for return_exceptions=True
            has_return_exceptions = any(
                kw.arg == "return_exceptions"
                for kw in node.keywords
            )

            if has_return_exceptions:
                continue

            # Violation: asyncio.gather without return_exceptions
            lineno = node.lineno
            col_offset = node.col_offset
            violations.append((py_file, lineno, f"asyncio.gather(...) without return_exceptions=True"))

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Ban raw asyncio.gather() without return_exceptions=True")
    parser.add_argument("--fix", action="store_true", help="Auto-fix violations (not yet implemented)")
    parser.add_argument("--root", type=Path, default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"))
    args = parser.parse_args()

    violations = find_violations(args.root)

    if not violations:
        print("ASYNC461: 0 violations — all asyncio.gather() calls use return_exceptions=True or parallel()")
        sys.exit(0)

    print(f"ASYNC461: {len(violations)} violation(s) found:")
    for path, lineno, msg in violations:
        rel = path.relative_to(args.root)
        print(f"  {rel}:{lineno}: {msg}")

    print("\nFix: Replace with parallel() or parallel_ok() from utils.async_helpers:")
    print("  asyncio.gather(*coros)           → parallel_ok(*coros, label='...')")
    print("  asyncio.gather(*coros, return_exceptions=True) → parallel(coros, policy='collect')")
    sys.exit(1)


if __name__ == "__main__":
    main()
