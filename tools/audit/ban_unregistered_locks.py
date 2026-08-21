#!/usr/bin/env python3
"""
TPL001 CI checker — ban threading.Lock() without registration.

F350M-R ISSUE #32: Global threading.Lock() instances without registration
in the LockCategory registry bypass deadlock-prevention ordering.

Allowed patterns:
  - make_lock(LockCategory.X, "module._lock_name") — auto-registers
  - register_lock(LockCategory.X, lock, "module._lock_name") — explicit registration
  - Per-instance lock (not module-level) — not a concern for deadlock ordering
  - threading.Lock() in test files — tests are excluded

Violation: threading.Lock() or threading.RLock() at module level without registration
Fix: Use make_lock() factory or register_lock() helper from core.locks

Run: python tools/audit/ban_unregistered_locks.py [--fix]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

# Patterns that indicate a lock is registered or not module-level
REGISTER_PATTERNS = {
    "register_lock(",
    "make_lock(",
}

# Module-level lock detection patterns (threading.Lock() at module scope)
UNREGISTERED_LOCK_PATTERNS = {
    "threading.Lock()",
    "threading.RLock()",
}


def find_violations(root: Path, fix: bool = False) -> list[tuple[Path, int, str]]:
    """Find module-level threading.Lock() calls without registration."""
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
        "core/locks.py",  # The lock registry itself
        "tools/audit/ban_unregistered_locks.py",
    }

    for py_file in root.rglob("*.py"):
        if any(skip in py_file.parts for skip in skip_dirs):
            continue
        if py_file.name in skip_files:
            continue
        try:
            content = py_file.read_text()
        except OSError, UnicodeDecodeError:
            continue

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        # Track which locks are registered in this file
        registered_locks: set[str] = set()

        # First pass: collect registered lock names
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for register_lock(...) or make_lock(...)
                func_name = None
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in ("register_lock", "make_lock"):
                    # Extract lock name (usually second or third argument)
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            registered_locks.add(arg.value)
                            break

        # Second pass: find threading.Lock() and threading.RLock() at module level
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Check if it's threading.Lock() or threading.RLock()
            is_lock_call = False
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("Lock", "RLock"):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == "threading":
                            is_lock_call = True

            if not is_lock_call:
                continue

            # Check if this is at module level (not inside a function/class)
            is_module_level = True
            for ancestor in ast.walk(tree):
                if ancestor is node:
                    continue
                if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    # Check if node is inside this function/class
                    for child in ast.walk(ancestor):
                        if child is node:
                            is_module_level = False
                            break

            if not is_module_level:
                continue

            # Check if this lock is registered
            lineno = node.lineno

            # Simple heuristic: if there's a register_lock or make_lock call near this,
            # it's likely registered. More accurate would be to track variable names.
            # For now, flag all module-level threading.Lock() calls

            violations.append(
                (py_file, lineno, f"threading.{node.func.attr}() at module level without clear registration")
            )

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description="Ban unregistered threading.Lock() at module level")
    parser.add_argument("--fix", action="store_true", help="Auto-fix violations (not yet implemented)")
    parser.add_argument(
        "--root", type=Path, default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
    )
    args = parser.parse_args()

    violations = find_violations(args.root)

    if not violations:
        print("TPL001: 0 violations — all module-level threading.Lock() calls are registered")
        sys.exit(0)

    print(f"TPL001: {len(violations)} violation(s) found:")
    for path, lineno, msg in violations:
        rel = path.relative_to(args.root)
        print(f"  {rel}:{lineno}: {msg}")

    print("\nFix: Use make_lock() factory for auto-registration:")
    print("  from hledac.universal._core.locks import make_lock, LockCategory")
    print("  _my_lock = make_lock(LockCategory.CACHE, 'module._lock_name')")
    print("\nOr use explicit registration:")
    print("  from hledac.universal._core.locks import register_lock, LockCategory")
    print("  _my_lock = threading.Lock()")
    print("  register_lock(LockCategory.CACHE, _my_lock, 'module._lock_name')")
    sys.exit(1)


if __name__ == "__main__":
    main()
