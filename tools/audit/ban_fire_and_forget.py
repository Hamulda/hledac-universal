#!/usr/bin/env python3
"""
ASYNC462 CI checker — ban raw asyncio.create_task() without safe_create_task.

F350M-R ISSUE #32: Fire-and-forget asyncio.create_task() without done-callback
can silently swallow exceptions, leading to invisible failures in production.

Allowed patterns:
  - safe_create_task(...) from utils.asyncx._parallel or utils.async_helpers
  - asyncio.create_task(...).add_done_callback(...) with explicit error logging
  - asyncio.TaskGroup.create_task(...) (structured concurrency)
  - _safe_task_factory(...) internal wrapper

Violation: asyncio.create_task(...) without done-callback or safe wrapper
Fix: Use safe_create_task() from utils.asyncx

Run: python tools/audit/ban_fire_and_forget.py [--fix]
"""
from __future__ import annotations

import ast
import argparse
import sys
from pathlib import Path
from _core import aclose


def find_violations(root: Path, fix: bool = False) -> list[tuple[Path, int, str]]:
    """Find raw asyncio.create_task() calls without safe wrapper or done-callback."""
    violations = []
    skip_dirs = {
        "__pycache__", ".venv", ".venv-test", "archive", "probe_", "tests/archive",
        ".git", ".claude", "tools/migrate", "tests",  # tests use raw create_task legitimately
    }
    skip_files = {
        "tools/migrate/migrate_create_task_to_safe.py",
        "utils/async_helpers.py",  # internal implementation
        "utils/asyncx/_parallel.py",  # safe_create_task implementation
        "otel/_instrumentation_asyncio.py",  # OTel instrumentation layer
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
            
            # Check if it's asyncio.create_task(...) or asyncio.create_task(..., ...)
            is_create_task = False
            is_safe_wrapper = False
            is_internal = False
            
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "create_task":
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == "asyncio":
                            is_create_task = True
                        elif node.func.value.id == "_asyncio":
                            is_create_task = True
                            is_internal = True

            if not is_create_task:
                continue

            # Skip internal _asyncio.create_task (allowed alias)
            if is_internal:
                continue

            # Check if it's wrapped in safe_create_task call
            # e.g., safe_create_task(asyncio.create_task(...)) - this is a violation
            parent = getattr(node, '_parent', None)
            if parent and isinstance(parent, ast.Call):
                if isinstance(parent.func, ast.Name) and parent.func.id == "safe_create_task":
                    is_safe_wrapper = True
                elif isinstance(parent.func, ast.Attribute) and parent.func.attr == "safe_create_task":
                    is_safe_wrapper = True

            # Check for .add_done_callback() in the same statement chain
            has_callback = False
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                # Check if parent of this node has add_done_callback
                if hasattr(node, '_parent') and node._parent:
                    parent = node._parent
                    if isinstance(parent, ast.Attribute) and parent.attr == "add_done_callback":
                        has_callback = True

            # Violation: raw asyncio.create_task without safe wrapper or callback
            if is_create_task and not is_safe_wrapper and not has_callback:
                lineno = node.lineno
                violations.append((py_file, lineno, f"asyncio.create_task(...) without safe_create_task or done-callback"))

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Ban raw asyncio.create_task() without safe_create_task")
    parser.add_argument("--fix", action="store_true", help="Auto-fix violations (not yet implemented)")
    parser.add_argument("--root", type=Path, default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"))
    args = parser.parse_args()

    violations = find_violations(args.root)

    if not violations:
        print("ASYNC462: 0 violations — all asyncio.create_task() calls use safe_create_task or done-callback")
        sys.exit(0)

    print(f"ASYNC462: {len(violations)} violation(s) found:")
    for path, lineno, msg in violations:
        rel = path.relative_to(args.root)
        print(f"  {rel}:{lineno}: {msg}")

    print("\nFix: Replace with safe_create_task() from utils.asyncx:")
    print("  asyncio.create_task(coro) → safe_create_task(coro, name='...')")
    print("  from utils.asyncx import safe_create_task")
    sys.exit(1)


if __name__ == "__main__":
    main()
