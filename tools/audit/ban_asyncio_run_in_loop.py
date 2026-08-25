#!/usr/bin/env python3
"""
ASYNC462 CI checker — ban asyncio.run() inside try/except get_running_loop pattern.

ISSUE-2: asyncio.run() cannot be called from a running event loop.
The broken pattern:
    try:
        asyncio.get_running_loop()  # succeeds in async context
    except RuntimeError:  # catches "no loop" case
        with asyncio.Runner() as runner:
            return runner.run(coro)
    return asyncio.run(coro)  # ← CRASHES in async context

Fix: Use run_sync_async() from utils.sync_bridge (handles both cases).

Allowed patterns:
- run_sync_async(coro)  [from utils.sync_bridge]
- asyncio.Runner().run(coro)  [explicit, no try/except wrapper]
- asyncio.run_coroutine_threadsafe(coro, loop).result()  [explicit threadsafe]
- asyncio.run() at module level or in __main__ guard  [legitimate entry points]
- asyncio.run() in else branch of "if loop.is_running()" check  [document_intelligence.py]

Violation examples:
- asyncio.run() inside a try block following get_running_loop() without guard
- asyncio.run() in except handler (inverted pattern)

Run: python tools/audit/ban_asyncio_run_in_loop.py [--fix]
"""

from __future__ import annotations

import ast
import argparse
import sys
from pathlib import Path


def _find_calls(node: ast.AST) -> list[ast.Call]:
    return [child for child in ast.walk(node) if isinstance(child, ast.Call)]


def _is_get_running_loop(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Attribute):
        if call.func.attr == "get_running_loop":
            if isinstance(call.func.value, ast.Name):
                return call.func.value.id in ("asyncio", "_asyncio")
    elif isinstance(call.func, ast.Name):
        if call.func.id == "get_running_loop":
            return True
    return False


def _is_asyncio_run(call: ast.Call) -> bool:
    if isinstance(call.func, ast.Attribute):
        if call.func.attr == "run":
            if isinstance(call.func.value, ast.Name):
                return call.func.value.id in ("asyncio", "_asyncio")
    return False


def _is_asyncio_run_guarded_by_is_running(call: ast.Call, try_node: ast.Try) -> bool:
    """
    Check if asyncio.run() is guarded by 'if loop.is_running(): ... else: asyncio.run()'.
    These are NOT violations because asyncio.run() is only reached when loop is NOT running.
    """
    for stmt in try_node.body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.If):
                continue
            # Check if this If's test calls is_running()
            test_calls = _find_calls(node.test)
            is_running_calls = [
                c for c in test_calls
                if isinstance(c.func, ast.Attribute) and c.func.attr == "is_running"
            ]
            if not is_running_calls:
                continue
            # Check if our call is in the orelse (not the body)
            for orelse_node in (node.orelse or []):
                for child in ast.walk(orelse_node):
                    if isinstance(child, ast.Call) and child is call:
                        return True
    return False


def find_violations(root: Path, fix: bool = False) -> list[tuple[Path, int, str]]:
    violations = []
    skip_dirs = {
        "__pycache__",
        ".venv",
        ".venv-test",
        "archive",
        "probe_",
        "tests/archive",
        ".git",
        ".claude",
        "tools/migrate",
        "tests",
    }
    skip_files = {
        "tools/migrate/migrate_asyncio_run_pattern.py",
        "tools/migrate/revert_asyncio_run_migration.py",
        "utils/sync_bridge.py",
        # document_intelligence.py uses the VALID pattern:
        #   if loop.is_running(): run_coroutine_threadsafe
        #   else: asyncio.run()  <- only reached when no loop is running
        "recon/document_intelligence.py",
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
            if not isinstance(node, ast.Try):
                continue

            all_calls = _find_calls(node)
            has_grl = any(_is_get_running_loop(c) for c in all_calls)
            has_run = any(_is_asyncio_run(c) for c in all_calls)

            # Case 1: asyncio.run() in except handler (broken inverted pattern)
            if not has_grl:
                for handler in node.handlers:
                    for call in _find_calls(handler):
                        if _is_asyncio_run(call):
                            violations.append((
                                py_file,
                                call.lineno,
                                "asyncio.run() in except handler without get_running_loop in try body",
                            ))

            # Case 2: asyncio.run() in try body after get_running_loop (ISSUE-2 broken pattern)
            # Skip if guarded by "if loop.is_running(): ... else: asyncio.run()"
            if has_grl and has_run:
                for call in all_calls:
                    if _is_asyncio_run(call):
                        if not _is_asyncio_run_guarded_by_is_running(call, node):
                            violations.append((
                                py_file,
                                call.lineno,
                                "asyncio.run() in try body after get_running_loop() without loop.is_running() guard",
                            ))

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ban asyncio.run() inside try/except get_running_loop() pattern"
    )
    parser.add_argument("--fix", action="store_true", help="Auto-fix violations (not yet implemented)")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"),
    )
    args = parser.parse_args()

    violations = find_violations(args.root)

    if not violations:
        print(
            "ASYNC462: 0 violations — no asyncio.run() found inside get_running_loop() try/except pattern"
        )
        sys.exit(0)

    print(f"ASYNC462: {len(violations)} violation(s) found:")
    for path, lineno, msg in violations:
        rel = path.relative_to(args.root)
        print(f"  {rel}:{lineno}: {msg}")

    print("\nFix: Use run_sync_async() from utils.sync_bridge:")
    print("  Or simply: return run_sync_async(coro)")
    sys.exit(1)


if __name__ == "__main__":
    main()
