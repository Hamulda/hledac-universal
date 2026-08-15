#!/usr/bin/env python3
"""
Codemod: bare → canonical imports (ISSUE 1.2) — SAFE line-based version


Replaces bare sibling-package imports using LINE-BASED string replacement:
    from hledac.universal.runtime.foo import bar  →  from hledac.universal.runtime.foo import bar
    import hledac.universal.runtime.foo           →  import hledac.universal.runtime.foo

Key insight: Only imports at module level need fixing — line-based replacement
is safer than AST for multi-line imports because AST transformer breaks when
`from pkg import (\n  foo,\n  bar,\n)` spans multiple lines.

Safe:
- Line-based: processes import lines individually (handles multi-line correctly)
- Excludes tests/, probe/, archive/, tools/_archive/
- Idempotent: running twice = same result
- Dry-run mode

Usage:
    python scripts/codemod_dual_namespace.py --check
    python scripts/codemod_dual_namespace.py --fix

CI gate:
    python scripts/codemod_dual_namespace.py --check
    # exit 1 = violations, exit 0 = clean
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple
from _core import aclose


ROOT = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

# Banned bare import roots (must use hledac.universal.<pkg>)
BANNED: frozenset[str] = frozenset({
    "runtime", "brain", "knowledge", "coordinators", "intel", "intelligence",
    "transport", "network", "export", "report", "rendering", "layers",
    "prefetch", "cli", "tools", "discovery", "federated", "security",
    "infrastructure", "memory", "multimodal", "monitoring", "graph",
    "prefilt", "hledac",
    # Also add core/utils/recon/fetching/rl (create dual-load with hledac.universal.*)
    "core", "utils", "recon", "fetching", "rl",
})

# Stdlib / test packages allowed as bare imports
ALLOWED: frozenset[str] = frozenset({
    "asyncio", "typing", "pathlib", "os", "sys", "re", "json", "abc",
    "argparse", "ast", "contextlib", "copy", "dataclasses", "enum",
    "functools", "inspect", "io", "itertools", "logging", "math",
    "pickle", "queue", "random", "struct", "tempfile", "threading",
    "time", "traceback", "types", "unittest", "warnings", "weakref",
    "collections", "operator", "signal", "socket", "statistics", "string",
    "textwrap", "tokenize", "tracemalloc", "uuid", "zipfile",
    "pytest", "coverage", "hypothesis",
    "_asyncio", "_threading", "_io", "_collections",
})

# Excluded directories
EXCLUDE_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".venv", ".venv-test", ".git", ".claude",
    "archive", "tests", "benchmarks", ".mypy_cache",
    ".pytest_cache", "stubs",
})
EXCLUDE_SUBPATHS: frozenset[str] = frozenset({
    "probe_", "tools/_archive", "tools/audit",
})

# Canonical prefix
CANON = "hledac.universal."


class Violation(NamedTuple):
    file: Path
    line_no: int
    line: str
    matched_module: str
    replacement: str


def should_exclude(path: Path) -> bool:
    parts = path.parts
    for excl in EXCLUDE_DIRS:
        if excl in parts:
            return True
    rel = str(path.relative_to(ROOT))
    for sub in EXCLUDE_SUBPATHS:
        if sub in rel:
            return True
    return False


def process_line(line: str) -> tuple[bool, str, str]:
    """
    Process a single line for bare import replacement.
    Returns (was_fixed, new_line, matched_module).
    """
    orig = line

    # Skip empty lines / comments
    stripped = line.lstrip()
    if not stripped or stripped.startswith('#'):
        return False, orig, ""

    # Pattern: from <banned_pkg>. or from <banned_pkg> import
    # Must NOT already have hledac.universal prefix
    m = re.match(r'^(\s*)from\s+([\w.]+)\s+import\s+(.*)$', line, re.DOTALL)
    if m:
        indent, module, rest = m.group(1), m.group(2), m.group(3)
        root = module.split(".")[0]
        if root in BANNED and root not in ALLOWED:
            if not module.startswith("hledac.universal."):
                new_line = f"{indent}from {CANON}{module} import {rest}"
                return True, new_line, module
    # Alternative: from <banned_pkg> import (single line)
    m2 = re.match(r'^(\s*)from\s+([\w.]+)\s+import\s+(.*)$', line)
    if m2:
        indent, module, rest = m2.group(1), m2.group(2), m2.group(3)
        root = module.split(".")[0]
        if root in BANNED and root not in ALLOWED:
            if not module.startswith("hledac.universal."):
                new_line = f"{indent}from {CANON}{module} import {rest}"
                return True, new_line, module

    # Pattern: import <banned_pkg>. or import <banned_pkg>
    m3 = re.match(r'^(\s*)import\s+([\w.]+)(.*)$', line)
    if m3:
        indent, module, rest = m3.group(1), m3.group(2), m3.group(3)
        root = module.split(".")[0]
        if root in BANNED and root not in ALLOWED:
            if not module.startswith("hledac.universal."):
                new_line = f"{indent}import {CANON}{module}{rest}"
                return True, new_line, module

    return False, orig, ""


def check_file(path: Path) -> list[Violation]:
    """Check file for bare imports. Returns list of violations."""
    violations = []
    try:
        lines = path.read_text(encoding="utf-8").split('\n')
    except (OSError, UnicodeDecodeError):
        return violations

    for i, line in enumerate(lines, 1):
        fixed, new_line, matched = process_line(line)
        if fixed:
            violations.append(Violation(
                file=path, line_no=i, line=line,
                matched_module=matched, replacement=new_line
            ))
    return violations


def fix_file(path: Path, dry_run: bool = True) -> tuple[bool, int]:
    """Fix bare imports in a file. Returns (was_modified, violation_count)."""
    violations = check_file(path)
    if not violations:
        return False, 0

    if dry_run:
        return False, len(violations)

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, len(violations)

    lines = content.split('\n')
    # Build line-number → new_line map (reverse order to preserve line numbers)
    for v in reversed(violations):
        if 0 < v.line_no <= len(lines):
            fixed, new_line, _ = process_line(lines[v.line_no - 1])
            if fixed:
                lines[v.line_no - 1] = new_line

    new_content = '\n'.join(lines)
    if new_content != content:
        path.write_text(new_content, encoding="utf-8")
        return True, len(violations)
    return False, len(violations)


def get_all_python_files() -> list[Path]:
    """Get all Python source files, excluding tests/archive/probe."""
    files = []
    for py_file in ROOT.rglob("*.py"):
        if should_exclude(py_file):
            continue
        files.append(py_file)
    return files


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Codemod: bare → canonical imports")
    parser.add_argument("--check", action="store_true", help="Dry run")
    parser.add_argument("--fix", action="store_true", help="Apply fixes")
    parser.add_argument("--files", nargs="*", help="Specific files")
    args = parser.parse_args()

    if not args.check and not args.fix:
        parser.print_help()
        print("\nMust specify --check or --fix")
        sys.exit(1)

    if args.files:
        files = [Path(f) for f in args.files]
    else:
        files = get_all_python_files()

    print(f"Scanning {len(files)} Python files...")
    if args.check:
        print("MODE: DRY RUN\n")

    total_violations = 0
    files_with_violations = 0
    files_fixed = 0

    for f in sorted(files):
        was_modified, count = fix_file(f, dry_run=not args.fix)
        if count > 0:
            files_with_violations += 1
            total_violations += count
            if args.check:
                rel = f.relative_to(ROOT)
                print(f"  {rel}: {count} import(s)")
        if was_modified:
            files_fixed += 1

    print(f"\n{'='*60}")
    print(f"RESULT: {total_violations} violation(s) in {files_with_violations} file(s)")
    if args.fix:
        print(f"        {files_fixed} file(s) fixed")
    print(f"{'='*60}")

    if args.check and total_violations > 0:
        print(f"\nRun with --fix to apply these changes.")
        sys.exit(1)
    elif total_violations == 0:
        print("\n✓ All imports are canonical — 0 violations")
        sys.exit(0)


if __name__ == "__main__":
    main()
