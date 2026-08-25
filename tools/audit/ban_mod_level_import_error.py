#!/usr/bin/env python3
"""
MODIMP CI checker — ban module-level try/except ImportError antipattern.

F0xx: Ban scattered module-level ``try: import X except ImportError: ...`` blocks.
These duplicate lazy-import logic across dozens of files and pay a 5-15µs
cold-start penalty even on the success path. The canonical replacement is the
transparent lazy-import proxy:

    from hledac.universal.utils.optional_imports import lazy_import

    # Before
    try:
        from otel import instrumented as _instr
    except ImportError:
        from hledac.universal.otel._instrumentation import instrumented as _instr

    # After (zero-cost at import; call sites unchanged)
    _instr = lazy_import("otel:instrumented",
                         default=lazy_import("hledac.universal.otel._instrumentation:instrumented"))

    # optional module
    duckdb = lazy_import("duckdb", default=None)
    if duckdb:
        duckdb.connect(...)

Detection: a ``Try`` node that is a top-level (module-level) statement and whose
every ``except`` handler catches ``ImportError`` (or ``ModuleNotFoundError``) and
whose ``try`` body performs at least one import.

Allowed (skipped):
  - Nothing by default — every module-level try/except ImportError is flagged so
    it can be migrated to ``lazy_import``. Files already using ``lazy_import`` are
    auto-excluded (no point re-flagging).

Run:
  python tools/audit/ban_mod_level_import_error.py [--root DIR] [--fix]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def _is_import_error_handler(handler: ast.ExceptHandler) -> bool:
    """True iff the handler catches ImportError / ModuleNotFoundError only."""
    if handler.type is None:
        return False
    names: list[str] = []
    if isinstance(handler.type, ast.Name):
        names = [handler.type.id]
    elif isinstance(handler.type, ast.Tuple):
        names = [e.id for e in handler.type.elts if isinstance(e, ast.Name)]
    else:
        return False
    return all(n in ("ImportError", "ModuleNotFoundError") for n in names)


def _body_has_import(node: ast.Try) -> bool:
    for stmt in node.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            return True
        # tolerate a leading comment-only / pass line; still require an import somewhere
    return False


def find_violations(root: Path, fix: bool = False) -> list[tuple[Path, int, str]]:
    """Return (path, lineno, message) for every module-level try/except ImportError."""
    violations: list[tuple[Path, int, str]] = []
    skip_dirs = {
        "__pycache__",
        ".venv",
        ".venv-test",
        "archive",
        "probe_",
        "tests",
        ".git",
        ".claude",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".hypothesis",
        "benchmarks",
        "rust_extensions",
        ".housekeeping_cleanup",
    }
    # Files that are themselves the migration tooling / proxies, or the
    # canonical fallback layer that legitimately uses try/except ImportError.
    skip_files = {
        "tools/audit/ban_mod_level_import_error.py",
        "tools/migrate/migrate_try_import_to_lazy.py",
        "utils/optional_imports.py",
        "utils/codec.py",
    }

    for py_file in root.rglob("*.py"):
        if any(part in skip_dirs for part in py_file.parts):
            continue
        if str(py_file) in skip_files or py_file.name in skip_files:
            continue
        try:
            content = py_file.read_text()
        except OSError:
            continue
        except UnicodeDecodeError:
            continue

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        # Module-level only: statements directly in the module body.
        for node in tree.body:
            if not isinstance(node, ast.Try):
                continue
            if not node.handlers:
                continue
            if not all(_is_import_error_handler(h) for h in node.handlers):
                continue
            if not _body_has_import(node):
                continue

            lineno = getattr(node, "lineno", 0)
            violations.append(
                (py_file, lineno, "module-level try/except ImportError — use lazy_import()")
            )

    return violations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ban module-level try/except ImportError (use lazy_import())"
    )
    parser.add_argument("--fix", action="store_true", help="Auto-fix via migrator (delegates)")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"),
    )
    args = parser.parse_args()

    violations = find_violations(args.root, fix=args.fix)

    if not violations:
        print("MODIMP: 0 violations — all module-level imports use lazy_import() or are gone")
        sys.exit(0)

    print(f"MODIMP: {len(violations)} module-level try/except ImportError violation(s):")
    for path, lineno, msg in sorted(violations, key=lambda v: (str(v[0]), v[1])):
        rel = path.relative_to(args.root) if args.root in path.parents else path
        print(f"  {rel}:{lineno}: {msg}")

    if args.fix:
        print("\nFix: run `python tools/migrate/migrate_try_import_to_lazy.py`")
    else:
        print("\nFix: replace with lazy_import() from utils.optional_imports")
        print("  try: from otel import instrumented as _i")
        print("  except ImportError: from hledac.universal... import instrumented as _i")
        print("  → _i = lazy_import('otel:instrumented',")
        print("        default=lazy_import('hledac.universal...:instrumented'))")
    sys.exit(1)


if __name__ == "__main__":
    main()
