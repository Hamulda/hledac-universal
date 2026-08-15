#!/usr/bin/env uv run python
"""
CI Guard: TST001 — production code must not import from tests.transports.

Run: uv run python tools/ci_tst001_guard.py
Exit 1 = violation found (production import from tests/transports)
Exit 0 = clean (no violations)

This is a Ruff-lint complementary check since Ruff has no native rule
for "import-from-tests" enforcement.
"""

import ast
import sys
from pathlib import Path
from core import aclose

ROOT = Path(__file__).parent.parent
TESTS_TRANSPORTS = ROOT / "tests" / "transports"


def _check_file(src_path: Path) -> list[tuple[int, str]]:
    """Return list of (lineno, message) for TST001 violations in src_path."""
    violations = []
    try:
        source = src_path.read_text()
    except Exception:
        return violations

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return violations

    # Skip files inside tests/transports/ itself
    if TESTS_TRANSPORTS in src_path.parents and src_path != TESTS_TRANSPORTS / "__init__.py":
        return violations

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module and node.module.startswith("tests.transports"):
            for alias in node.names:
                violations.append((node.lineno, f"TST001: {src_path.relative_to(ROOT)} imports from tests.transports ({alias.name})"))
        # from hledac.universal.tests.transports import ...
        if node.module and "hledac" in str(node.module) and "tests.transports" in str(node.module):
            for alias in node.names:
                violations.append((node.lineno, f"TST001: {src_path.relative_to(ROOT)} imports from tests.transports ({alias.name})"))

    return violations


def _iter_production_files():
    """Yield production .py files in key production dirs only (fast CI scan)."""
    scan_dirs = {"transport", "coordinators", "federated", "brain", "runtime", "knowledge",
                 "fetching", "intelligence", "layers", "utils", "core"}
    skip_dirs = {"tests", "stubs", ".venv", "build", "dist", ".git", ".claude", "node_modules",
                 "__pycache__", ".pytest_cache"}
    for scan_dir in scan_dirs:
        d = ROOT / scan_dir
        if not d.exists():
            continue
        for path in d.rglob("*.py"):
            if any(part in path.parts for part in skip_dirs):
                continue
            if path.name == "ci_tst001_guard.py":
                continue
            yield path


def main() -> int:
    total_violations = 0
    for src_path in sorted(_iter_production_files()):
        violations = _check_file(src_path)
        if violations:
            total_violations += len(violations)
            for lineno, msg in violations:
                print(f"  {src_path}:{lineno}  {msg}")

    if total_violations:
        print(f"\nTST001: {total_violations} violation(s) found — production code imports from tests/transports/")
        return 1
    print("TST001: clean — no production imports from tests/transports/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
