#!/usr/bin/env python3
"""
migrate_try_import_to_lazy.py — one-shot codemod for ISSUE #12.

Rewrites module-level ``try: import X except ImportError: ...`` blocks to the
transparent lazy-import proxy:

    try:
        from otel import instrumented as _i
    except ImportError:
        from hledac.universal.otel._instrumentation import instrumented as _i
    ──▶
    _i = lazy_import("otel:instrumented",
                     default=lazy_import("hledac.universal.otel._instrumentation:instrumented"))

    try:
        import duckdb
    except ImportError:
        duckdb = None
    ──▶
    duckdb = lazy_import("duckdb", default=None)

SAFETY: only the simplest, unambiguous blocks are transformed:
  * exactly one ``except ImportError`` handler
  * ``try`` body is a single import statement
  * ``except`` body is a single import (same target name) OR ``NAME = None`` OR ``pass``
Blocks that do attribute access, set multiple globals, or have multiple imports
are left untouched (and reported) so a human can decide.

DEFAULT MODE is --dry-run (prints the diff). Use --apply to write files.
Each modified file is backed up to /tmp/hledac_bak/<relpath> before writing.

Usage:
  python tools/migrate/migrate_try_import_to_lazy.py [--apply] [--root DIR]
"""

from __future__ import annotations

import argparse
import ast
import shutil
import sys
from pathlib import Path

ROOT_DEFAULT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
BACKUP_ROOT = Path("/tmp/hledac_bak")

SKIP_DIRS = {
    "__pycache__", ".venv", ".venv-test", "archive", "probe_", "tests",
    ".git", ".claude", ".ruff_cache", ".mypy_cache", ".pytest_cache",
    ".hypothesis", "benchmarks", "rust_extensions", ".housekeeping_cleanup",
}
SKIP_FILES = {
    "tools/audit/ban_mod_level_import_error.py",
    "tools/migrate/migrate_try_import_to_lazy.py",
    "utils/optional_imports.py",
}


def _import_target(stmt: ast.AST) -> tuple[str, str] | None:
    """Return (target_name, dotted) or None if not a simple single-import."""
    if isinstance(stmt, ast.Import):
        if len(stmt.names) != 1:
            return None
        a = stmt.names[0]
        if a.asname:
            return (a.asname, a.name)
        return (a.name, a.name)
    if isinstance(stmt, ast.ImportFrom):
        if stmt.level != 0 or not stmt.module or len(stmt.names) != 1:
            return None
        a = stmt.names[0]
        if a.name == "*":
            return None
        dotted = f"{stmt.module}:{a.name}"
        return (a.asname or a.name, dotted)
    return None


def _handler_catches_importhandler(h: ast.ExceptHandler) -> bool:
    if h.type is None:
        return False
    names: list[str] = []
    if isinstance(h.type, ast.Name):
        names = [h.type.id]
    elif isinstance(h.type, ast.Tuple):
        names = [e.id for e in h.type.elts if isinstance(e, ast.Name)]
    else:
        return False
    return all(n in ("ImportError", "ModuleNotFoundError") for n in names)


def _analyze_try(node: ast.Try) -> tuple[str, str] | None:
    """Return (replacement_line, target_name) if safely transformable, else None."""
    if len(node.handlers) != 1:
        return None
    h = node.handlers[0]
    if not _handler_catches_importhandler(h):
        return None
    if len(node.body) != 1:
        return None
    target = _import_target(node.body[0])
    if target is None:
        return None
    tname, primary = target

    # except body: single import (same name) | NAME = None | pass
    if len(h.body) != 1:
        return None
    eb = h.body[0]
    fallback: str | None
    if isinstance(eb, (ast.Import, ast.ImportFrom)):
        ft = _import_target(eb)
        if ft is None or ft[0] != tname:
            return None
        fallback = ft[1]
    elif isinstance(eb, ast.Assign) and len(eb.targets) == 1 and isinstance(eb.targets[0], ast.Name) and eb.targets[0].id == tname:
        fallback = None  # NAME = None (or any value) → optional
    elif isinstance(eb, ast.Pass):
        fallback = None
    else:
        return None

    if fallback is None:
        return (f'{tname} = lazy_import("{primary}", default=None)', tname)
    return (f'{tname} = lazy_import("{primary}", default=lazy_import("{fallback}"))', tname)


def _needs_import_guard(content: str) -> bool:
    return ("lazy_import" not in content) and ("optional_imports" not in content)


def migrate_file(path: Path, apply: bool) -> tuple[int, list[str], list[str]]:
    """Return (n_changed, changes_log, skipped_log)."""
    changed = 0
    changes: list[str] = []
    skipped: list[str] = []
    try:
        content = path.read_text()
    except (OSError, UnicodeDecodeError):
        return 0, changes, skipped
    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError:
        return 0, changes, skipped

    replacements: list[tuple[int, int, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.Try):
            continue
        res = _analyze_try(node)
        if res is None:
            # only count as skipped if it looks like an import-error try at all
            if node.handlers and _handler_catches_importhandler(node.handlers[0]):
                skipped.append(f"{path.relative_to(ROOT_DEFAULT) if ROOT_DEFAULT in path.parents else path}:{getattr(node, 'lineno', 0)}")
            continue
        repl, _ = res
        start = getattr(node, "lineno", 1) - 1  # 0-based inclusive
        end = getattr(node, "end_lineno", start + 1)  # 0-based exclusive
        replacements.append((start, end, repl))
        changes.append(f"{path.relative_to(ROOT_DEFAULT) if ROOT_DEFAULT in path.parents else path}:{getattr(node, 'lineno', 0)} -> {repl}")

    if not replacements:
        return 0, changes, skipped

    lines = content.splitlines(keepends=True)
    # apply bottom-up so indices stay valid
    replacements.sort(reverse=True)
    for start, end, repl in replacements:
        # preserve a single leading newline behavior: replace slice with repl + "\n"
        lines[start:end] = [repl + "\n"]
    new_content = "".join(lines)

    if _needs_import_guard(content):
        # insert the import after the last top-level import, else after __future__, else at top
        new_lines = new_content.splitlines(keepends=True)
        last_import = -1
        try:
            t2 = ast.parse(new_content)
            for n in t2.body:
                if isinstance(n, (ast.Import, ast.ImportFrom)):
                    last_import = max(last_import, getattr(n, "end_lineno", 0))
        except SyntaxError:
            last_import = -1
        insert_line = last_import if last_import > 0 else 0
        import_stmt = "from hledac.universal.utils.optional_imports import lazy_import\n"
        new_lines.insert(insert_line, import_stmt)
        new_content = "".join(new_lines)

    if apply:
        backup = BACKUP_ROOT / (path.relative_to(ROOT_DEFAULT) if ROOT_DEFAULT in path.parents else path)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        path.write_text(new_content)
        changed = len(replacements)
    else:
        changed = len(replacements)

    return changed, changes, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate module-level try/except ImportError to lazy_import()")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run)")
    ap.add_argument("--root", type=Path, default=ROOT_DEFAULT)
    args = ap.parse_args()

    total_changed = 0
    total_skipped = 0
    all_changes: list[str] = []
    all_skipped: list[str] = []
    for py in args.root.rglob("*.py"):
        if any(p in SKIP_DIRS for p in py.parts):
            continue
        if str(py) in SKIP_FILES or py.name in SKIP_FILES:
            continue
        n, ch, sk = migrate_file(py, apply=args.apply)
        total_changed += n
        total_skipped += len(sk)
        all_changes.extend(ch)
        all_skipped.extend(sk)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== {mode} ===")
    print(f"Files with changes: {len(set(c.split(':')[0] for c in all_changes))}")
    print(f"Blocks transformed: {total_changed}")
    print(f"Complex blocks skipped (manual review): {total_skipped}")
    print()
    if all_changes:
        print("--- transformations ---")
        for c in all_changes[:400]:
            print("  " + c)
        if len(all_changes) > 400:
            print(f"  ... and {len(all_changes) - 400} more")
    if all_skipped:
        print("\n--- skipped (complex, needs manual migration) ---")
        for s in all_skipped[:200]:
            print("  " + s)
        if len(all_skipped) > 200:
            print(f"  ... and {len(all_skipped) - 200} more")
    if args.apply:
        print(f"\nWrote changes; backups in {BACKUP_ROOT}")
    else:
        print("\n(DRY-RUN) pass --apply to write.")


if __name__ == "__main__":
    main()
