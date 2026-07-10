#!/usr/bin/env python3
# hledac/universal/tools/migrate_gather_to_safe_gather.py
#
# Sprint F262: AST codemod — migrate `asyncio.gather(...)` to `safe_gather_*(...)`.
#
# Pure-Python stdlib only (ast + dataclasses + argparse), M1-safe.
# Idempotent: re-running on an already-migrated file is a no-op.
#
# Classification (driven by the F262 audit of 162 production sites):
#   - bare `await asyncio.gather(*coros, return_exceptions=True)`         → safe_gather_fire_and_forget
#   - bare `await asyncio.gather(*coros)` (BUG: no return_exceptions)    → safe_gather_fire_and_forget + warning
#   - `results = await asyncio.gather(*coros, return_exceptions=True)`   → safe_gather_ok
#   - `results = await asyncio.gather(*coros)` (BUG)                     → safe_gather_ok + warning
#   - `return await asyncio.gather(...)`                                 → safe_gather_ok (return)
#   - nested in if/while/for/with                                         → left as-is (manual review)
#
# Usage:
#   python tools/migrate_gather_to_safe_gather.py --dry-run <files...>
#   python tools/migrate_gather_to_safe_gather.py <files...>
#   python tools/migrate_gather_to_safe_gather.py --all        # whole repo
#   python tools/migrate_gather_to_safe_gather.py --report     # JSON stats
#
# Exit codes:
#   0  no changes (or all changes applied)
#   1  bugs found (sites without return_exceptions) — still applies fixes
#   2  parse error in a target file


import argparse
import ast
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
import msgspec
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Source → target mapping
REPLACEMENT_MAP = {
    "FIRE_AND_FORGET": "safe_gather_fire_and_forget",
    "ASSIGN_WITH_RET_EXC": "safe_gather_ok",
    "ASSIGN_NO_RET_EXC_BUG": "safe_gather_ok",   # also a bug fix
    "RETURN_WITH_RET_EXC": "safe_gather_ok",
    "RETURN_NO_RET_EXC_BUG": "safe_gather_ok",   # also a bug fix
    "BUG_BARE_NO_RET_EXC": "safe_gather_fire_and_forget",  # also a bug fix
}

# We always add `return_exceptions=True` to the source if missing — that's the
# whole point of the migration. The replacement is still safe_gather_ok /
# safe_gather_fire_and_forget, which already filter / log errors.

# Files / directories we should never touch.
SKIP_PATH_PARTS = {
    ".venv", "venv", "__pycache__", "node_modules", ".git",
    "build", "dist", ".venv-test", "site-packages", ".cache", "target",
    # Our own codemod / probe scripts
    "tools/migrate_gather_to_safe_gather.py",
    "tools/revert_gather_migration.py",
    "tools/revert_gather_migration_text.py",
    "tools/fix_broken_codemod.py",
    "tools/probe_f262_*.py",
    # The file that DEFINES safe_gather_* — must not import from itself
    "utils/async_helpers.py",
}

# Functions that already do gather-like work — skip the migration (idempotency)
SAFE_GATHER_FUNCTIONS = {
    "safe_gather",
    "safe_gather_ok",
    "safe_gather_fire_and_forget",
    "safe_gather_strict",
}


# ---------------------------------------------------------------------------
# AST visitor — locate gather calls + classify them
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class GatherSite:
    """One `asyncio.gather(...)` call site."""
    file: str
    line: int          # 1-based
    col: int           # 0-based
    end_line: int      # 1-based
    end_col: int       # 0-based
    n_coros: int
    has_return_exceptions: bool
    pattern: str       # FIRE_AND_FORGET / ASSIGN_WITH_RET_EXC / etc.
    replacement: str   # safe_gather_ok / safe_gather_fire_and_forget
    is_bug: bool       # True if missing return_exceptions=True
    is_nested: bool    # True if nested in if/while/etc. — manual review


def _build_parent_map(tree: ast.Module) -> dict[int, ast.AST]:
    parent_map: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent
    return parent_map


def _enclosing_statement(node: ast.AST, parent_map: dict[int, ast.AST]) -> ast.AST | None:
    """Walk up to the closest Expr / Assign / Return / AugAssign node."""
    cur = node
    while id(cur) in parent_map:
        cur = parent_map[id(cur)]
        if isinstance(cur, ast.Module):
            return None
        if isinstance(cur, (ast.Expr, ast.Assign, ast.Return, ast.AugAssign)):
            return cur
    return None


def _is_gather_call(node: ast.Call) -> bool:
    """True if `node` is `asyncio.gather(...)` or `_asyncio.gather(...)`."""
    func = node.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "gather":
        return False
    if not isinstance(func.value, ast.Name):
        return False
    return func.value.id in ("asyncio", "_asyncio")


def _classify(node: ast.Call, stmt: ast.AST | None) -> tuple[str, str, bool, bool]:
    """Classify the gather call.

    Returns (pattern, replacement, is_bug, is_nested).
    """
    has_re = any(
        kw.arg == "return_exceptions"
        and isinstance(kw.value, ast.Constant)
        and kw.value.value is True
        for kw in node.keywords
    )

    # Nested in if/while/for/with/etc.
    if stmt is None:
        return ("NESTED", "safe_gather_ok", not has_re, True)

    if isinstance(stmt, ast.Expr):
        if has_re:
            return ("FIRE_AND_FORGET", "safe_gather_fire_and_forget", False, False)
        return ("BUG_BARE_NO_RET_EXC", "safe_gather_fire_and_forget", True, False)

    if isinstance(stmt, ast.Assign):
        if has_re:
            return ("ASSIGN_WITH_RET_EXC", "safe_gather_ok", False, False)
        return ("ASSIGN_NO_RET_EXC_BUG", "safe_gather_ok", True, False)

    if isinstance(stmt, ast.Return):
        if has_re:
            return ("RETURN_WITH_RET_EXC", "safe_gather_ok", False, False)
        return ("RETURN_NO_RET_EXC_BUG", "safe_gather_ok", True, False)

    if isinstance(stmt, ast.AugAssign):
        # e.g. `results.extend(await asyncio.gather(...))`
        # — uncommon, treat as nested for safety
        return ("NESTED", "safe_gather_ok", not has_re, True)

    return ("NESTED", "safe_gather_ok", not has_re, True)


def find_gather_sites(path: str) -> list[GatherSite]:
    """Parse `path` and return all gather call sites (sorted by position)."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return []

    parent_map = _build_parent_map(tree)
    sites: list[GatherSite] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_gather_call(node):
            continue
        stmt = _enclosing_statement(node, parent_map)
        pattern, replacement, is_bug, is_nested = _classify(node, stmt)

        sites.append(GatherSite(
            file=path,
            line=node.lineno,
            col=node.col_offset,
            end_line=node.end_lineno or node.lineno,
            end_col=node.end_col_offset or 0,
            n_coros=len(node.args),
            has_return_exceptions=any(
                kw.arg == "return_exceptions" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            ),
            pattern=pattern,
            replacement=replacement,
            is_bug=is_bug,
            is_nested=is_nested,
        ))

    # Sort by position (descending — apply from end to start to preserve offsets)
    sites.sort(key=lambda s: (s.line, s.col), reverse=True)
    return sites


# ---------------------------------------------------------------------------
# Source-level transform
# ---------------------------------------------------------------------------


def _arg_to_source(node: ast.AST) -> str:
    """Best-effort serialization of an AST node back to source code."""
    try:
        return ast.unparse(node)
    except Exception:
        return "<unparseable>"


def _kwargs_to_source(kwargs: list[ast.keyword], drop: set[str] | frozenset[str] = frozenset()) -> str:
    """Serialize kwargs, optionally dropping some by name."""
    parts: list[str] = []
    for kw in kwargs:
        if kw.arg in drop:
            continue
        if kw.arg is None:
            # **kwargs spread
            parts.append(f"**{_arg_to_source(kw.value)}")
        else:
            parts.append(f"{kw.arg}={_arg_to_source(kw.value)}")
    return ", ".join(parts)


def _build_replacement(site: GatherSite, node: ast.Call) -> str:
    """Build the source text that replaces `asyncio.gather(...)` at `site`.

    Returns the right-hand side of the new call, e.g. `safe_gather_ok(coro1(), coro2(), label="foo")`.
    """
    func_name = site.replacement
    args = [_arg_to_source(a) for a in node.args]
    # Drop return_exceptions=True — safe_gather_* always use it internally
    kwargs_str = _kwargs_to_source(node.keywords, drop={"return_exceptions"})

    all_parts = args[:]
    if kwargs_str:
        all_parts.append(kwargs_str)
    # Add a default label so debug logs are useful
    if not any(kw.arg == "label" for kw in node.keywords):
        all_parts.append(f'label="{Path(site.file).stem}:{site.line}"')

    return f"{func_name}({', '.join(all_parts)})"


def _find_call_node(path: str, line: int, col: int) -> ast.Call | None:
    """Re-parse and return the ast.Call at the given (line, col)."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return None
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == line and node.col_offset == col:
            return node
    return None


def _replace_gather_calls(path: str, sites: list[GatherSite]) -> tuple[str, list[str]]:
    """Apply replacements to the file. Returns (new_source, applied_descriptions).

    Robust strategy: re-parse the (already-modified) source, find the gather
    call by its textual representation (`asyncio.gather(...)` or
    `_asyncio.gather(...)`), and replace that exact span. This handles
    multi-line calls correctly without relying on line/col offsets.
    """
    with open(path, encoding="utf-8") as f:
        source = f.read()

    # Collect which safe_gather_* names we need to import
    needed_imports: set[str] = set()
    for site in sites:
        if not site.is_nested:
            needed_imports.add(site.replacement)

    # Ensure imports — add a single `from utils.async_helpers import ...` if missing
    if needed_imports:
        source = _ensure_imports(source, needed_imports)

    # Sites are sorted descending by position
    applied: list[str] = []
    for site in sites:
        if site.is_nested:
            # Skip nested — leave for manual review
            continue

        # Find the actual call by searching the source for the full
        # `asyncio.gather(...)` text. We do this on the CURRENT source (which
        # may have been modified by previous replacements in this loop) and
        # limit the search to the file region near the original line.
        node = _find_call_node(path, site.line, site.col)
        if node is None:
            continue

        # Get the original call's text via ast.unparse()
        try:
            # We need the FULL call including `asyncio.gather`
            full_call = ast.unparse(node)
        except Exception:
            continue

        # Determine the replacement
        replacement = _build_replacement(site, node)

        # Compute the start offset of the call (anchor by line/col).
        # Sites are processed in REVERSE position order, so earlier offsets
        # are still valid for later replacements.
        lines = source.splitlines(keepends=True)

        def to_offset(ln: int, co: int) -> int:
            offset = 0
            for i in range(ln - 1):
                if i >= len(lines):  # noqa: B023
                    break
                offset += len(lines[i])  # noqa: B023
            offset += co
            return offset

        start_offset = to_offset(site.line, site.col)

        # Search FORWARD from start_offset for the full_call text. This
        # anchors the replacement to the correct call site even when
        # there are multiple identical-looking calls in the file.
        idx = source.find(full_call, start_offset)
        if idx < 0:
            # Try with `_asyncio` form
            if "asyncio.gather" in full_call:
                alt = full_call.replace("asyncio.gather", "_asyncio.gather")
                idx = source.find(alt, start_offset)
                if idx >= 0:
                    full_call = alt
        if idx < 0:
            # Fallback: locate "asyncio.gather(" at the start_offset
            if source[start_offset:start_offset + 14] == "asyncio.gather":
                paren_start = start_offset + 13
            elif source[start_offset:start_offset + 15] == "_asyncio.gather":
                paren_start = start_offset + 14
            else:
                continue
            paren_end = _find_matching_paren(source, paren_start)
            if paren_end < 0:
                continue
            idx = start_offset
            end = paren_end + 1
        else:
            end = idx + len(full_call)

        # Splice
        source = source[:idx] + replacement + source[end:]
        applied.append(f"L{site.line}: {site.pattern} → {site.replacement}")

    return source, applied


def _find_matching_paren(text: str, start: int) -> int:
    """Return index of matching `)`, or -1 if unbalanced.

    Skips over string literals and char literals.
    """
    if start < 0 or start >= len(text) or text[start] != "(":
        return -1
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        elif c in ('"', "'"):
            quote = c
            if i + 2 < len(text) and text[i + 1] == quote and text[i + 2] == quote:
                # Triple-quoted string — find next triple-quote
                triple = quote * 3
                end = text.find(triple, i + 3)
                if end < 0:
                    return -1
                i = end + 2
            else:
                # Single-line string
                i += 1
                while i < len(text) and text[i] != quote:
                    if text[i] == "\\":
                        i += 1
                    i += 1
        i += 1
    return -1


# ---------------------------------------------------------------------------
# Import management
# ---------------------------------------------------------------------------

# Regex for an existing `from utils.async_helpers import (...)` line
# We want to expand the import list to include any missing safe_gather_* names.
_RE_FROM_ASYNC_HELPERS = re.compile(
    r"^(\s*from\s+utils\.async_helpers\s+import\s+)(?P<names>[^\n]+)$",
    re.MULTILINE,
)
# Alternative: `from hledac.universal.utils.async_helpers import ...` (rare in this repo)
_RE_FROM_HLEDAC_ASYNC_HELPERS = re.compile(
    r"^(\s*from\s+hledac\.universal\.utils\.async_helpers\s+import\s+)(?P<names>[^\n]+)$",
    re.MULTILINE,
)


def _parse_existing_names(names_text: str) -> set[str]:
    """Extract import names from the RHS of `from x import (...)`."""
    # Strip parens, split on comma
    text = names_text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    parts = [p.strip() for p in text.replace("\n", " ").split(",")]
    # Filter out empty / multiline continuations
    return {p for p in parts if p and not p.startswith("#")}


def _ensure_imports(source: str, needed: set[str]) -> str:
    """Add a `from utils.async_helpers import ...` line if not present.

    Idempotent: re-running is a no-op.
    """
    # 1) Check for existing import from utils.async_helpers
    for pattern in (_RE_FROM_ASYNC_HELPERS, _RE_FROM_HLEDAC_ASYNC_HELPERS):
        m = pattern.search(source)
        if m:
            existing = _parse_existing_names(m.group("names"))
            missing = needed - existing
            if not missing:
                return source
            # Extend the import
            new_names = sorted(existing | needed)
            new_line = m.group(1) + ", ".join(new_names)
            return source[:m.start()] + new_line + source[m.end():]

    # 2) No existing import — add one after the last TOP-LEVEL import line.
    # We use AST to find the last top-level Import / ImportFrom node, so we
    # never accidentally insert inside a function (which has its own imports).
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    last_top_import_end_lineno = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_top_import_end_lineno = node.end_lineno or node.lineno

    # If we also see a "
" at the top, we
    # want to keep our new import AFTER that (idiomatic Python).
    lines = source.splitlines(keepends=True)

    import_line = f"from utils.async_helpers import {', '.join(sorted(needed))}\n"

    if last_top_import_end_lineno > 0:
        # Insert AFTER the last top-level import (1-based lineno → 0-based idx+1)
        insert_at = last_top_import_end_lineno
        # Check the next line — if it's a blank, keep the blank, insert after
        # If the next non-blank line is at column 0, insert with a blank line
        # before it (PEP 8 spacing).
        if insert_at < len(lines) and lines[insert_at].strip() == "":
            # Next line is blank — insert immediately after it
            insert_at += 1
        lines.insert(insert_at, import_line)
        return "".join(lines)

    # No top-level imports — insert at top of module (after shebang + docstring)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    # Skip module docstring
    if insert_at < len(lines):
        stripped = lines[insert_at].lstrip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            if stripped.count(quote) >= 2 and len(stripped) > 3 and not stripped[3:].startswith(quote):
                # Single-line docstring
                insert_at += 1
            else:
                for j in range(insert_at + 1, len(lines)):
                    if quote in lines[j]:
                        insert_at = j + 1
                        break
    lines.insert(insert_at, import_line)
    return "".join(lines)


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def _should_skip(path: str) -> bool:
    # Match against individual path parts AND against full substrings of the
    # normalized POSIX path (so callers can pass either `utils/async_helpers.py`
    # or an absolute path).
    parts = set(Path(path).parts)
    posix = Path(path).as_posix()
    if parts & SKIP_PATH_PARTS:
        return True
    for sp in SKIP_PATH_PARTS:
        if "/" in sp and sp in posix:
            return True
    return False


def iter_python_files(targets: list[str], all_files: bool) -> list[str]:
    """Return the list of .py files to process."""
    if not all_files:
        return [t for t in targets if t.endswith(".py") and not _should_skip(t)]

    repo_root = Path(__file__).resolve().parent.parent
    out: list[str] = []
    for root, dirs, files in os.walk(repo_root):
        # Prune skipped dirs
        dirs[:] = [d for d in dirs if d not in SKIP_PATH_PARTS]
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(root, f)
                if not _should_skip(full):
                    out.append(full)
    return sorted(out)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


@dataclass
class Report:
    files_scanned: int = 0
    files_changed: int = 0
    sites_total: int = 0
    sites_migrated: int = 0
    sites_bugs_fixed: int = 0
    sites_nested_skipped: int = 0
    by_pattern: Counter = field(default_factory=Counter)
    by_replacement: Counter = field(default_factory=Counter)
    bugs: list[str] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_changed": self.files_changed,
            "sites_total": self.sites_total,
            "sites_migrated": self.sites_migrated,
            "sites_bugs_fixed": self.sites_bugs_fixed,
            "sites_nested_skipped": self.sites_nested_skipped,
            "by_pattern": dict(self.by_pattern),
            "by_replacement": dict(self.by_replacement),
            "bugs": self.bugs,
            "nested": self.nested,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate asyncio.gather(...) → safe_gather_*(...) using AST."
    )
    parser.add_argument("files", nargs="*", help="Python files to process (default: --all)")
    parser.add_argument("--all", action="store_true", help="Process entire repo (excl. vendored)")
    parser.add_argument("--dry-run", action="store_true", help="Print what would change, don't write")
    parser.add_argument("--report", action="store_true", help="Print JSON report at end")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-file output")
    args = parser.parse_args(argv)

    if not args.files and not args.all:
        parser.error("Provide files or --all")

    targets = iter_python_files(args.files, all_files=args.all)
    if not targets:
        print("No Python files matched", file=sys.stderr)
        return 0

    report = Report()
    exit_code = 0

    for path in targets:
        report.files_scanned += 1
        sites = find_gather_sites(path)
        if not sites:
            continue

        for site in sites:
            report.sites_total += 1
            report.by_pattern[site.pattern] += 1
            report.by_replacement[site.replacement] += 1
            if site.is_bug:
                report.sites_bugs_fixed += 1
                report.bugs.append(f"{path}:{site.line} [{site.pattern}]")
            if site.is_nested:
                report.sites_nested_skipped += 1
                report.nested.append(f"{path}:{site.line}")

        # Filter to non-nested for migration
        migratable = [s for s in sites if not s.is_nested]
        if not migratable:
            continue

        if args.dry_run:
            for site in migratable:
                if not args.quiet:
                    print(f"[DRY] {path}:{site.line}  {site.pattern} → {site.replacement}")
            continue

        # Apply changes
        new_source, applied = _replace_gather_calls(path, migratable)
        if applied:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_source)
            report.files_changed += 1
            report.sites_migrated += len(applied)
            if not args.quiet:
                for desc in applied:
                    print(f"[FIX] {path}:{desc}")

    if args.report:
        print("\n=== MIGRATION REPORT ===")
        print(json.dumps(report.to_dict(), indent=2))

    if report.sites_bugs_fixed > 0:
        exit_code = 1  # bugs were found & fixed — caller may want to know

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
