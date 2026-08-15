#!/usr/bin/env python3
# hledac/universal/tools/revert_gather_migration.py
#
# Reverse the codemod applied by migrate_gather_to_safe_gather.py when it
# produced broken output. Uses ast.unparse() to robustly find call sites.
#
# Pattern: safe_gather_*(<args>, label="<file>:<line>") → asyncio.gather(<args>, return_exceptions=True)
# Removes the `from utils.async_helpers import ...` line if it only contains
# safe_gather_* names.


import ast
import re
import sys
from _core import aclose

REVERT_FUNCS = {"safe_gather", "safe_gather_ok", "safe_gather_fire_and_forget", "safe_gather_strict"}


def find_revertable_calls(path: str) -> list[ast.Call]:
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # If the file doesn't even parse, skip — it's broken
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id not in REVERT_FUNCS:
            continue
        calls.append(node)
    return calls


def revert_file(path: str) -> tuple[bool, str]:
    """Revert safe_gather_*() calls back to asyncio.gather() in `path`."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return False, "cannot read"

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return False, f"syntax error: {e}"

    # Collect all revertable calls
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name) or func.id not in REVERT_FUNCS:
            continue
        calls.append(node)

    if not calls:
        return False, "no calls"

    # Sort calls descending by position so we can splice from end to start
    calls.sort(key=lambda n: (n.end_lineno or 0, n.end_col_offset or 0), reverse=True)

    lines = source.splitlines(keepends=True)

    def to_offset(ln: int, co: int) -> int:
        offset = 0
        for i in range(ln - 1):
            offset += len(lines[i])
        offset += co
        return offset

    n_reverted = 0
    for call in calls:
        # Build the original `asyncio.gather(*args, return_exceptions=True)` text
        func = call.func
        original_name = "asyncio.gather"
        args_text = [ast.unparse(a) for a in call.args]
        # Filter out the `label=...` kwarg
        kept_kwargs = [kw for kw in call.keywords if kw.arg != "label"]
        kwargs_text = [ast.unparse(kw) for kw in kept_kwargs]
        # Ensure return_exceptions=True is present
        has_re = any(kw.arg == "return_exceptions" for kw in kept_kwargs)
        all_parts = args_text + kwargs_text
        if not has_re:
            all_parts.append("return_exceptions=True")
        replacement = f"{original_name}({', '.join(all_parts)})"

        # Splice
        start = to_offset(call.lineno, call.col_offset)
        end = to_offset(call.end_lineno, call.end_col_offset)
        if start == end:
            continue
        source = source[:start] + replacement + source[end:]
        # Rebuild lines in case multi-line call crossed boundaries
        lines = source.splitlines(keepends=True)
        n_reverted += 1

    # Now clean up the import line
    source = _strip_safe_gather_imports(source)

    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return True, f"reverted {n_reverted} calls"


def _strip_safe_gather_imports(source: str) -> str:
    """Remove `from utils.async_helpers import safe_gather_*, ...` lines, keeping
    other names if present."""
    new_lines = []
    for line in source.splitlines(keepends=True):
        stripped = line.strip()
        # Match `from utils.async_helpers import ...`
        m = re.match(
            r"^from\s+utils\.async_helpers\s+import\s+(?P<names>.+?)$",
            stripped,
        )
        if not m:
            new_lines.append(line)
            continue
        names_text = m.group("names")
        # Strip parens if present
        if names_text.startswith("(") and names_text.endswith(")"):
            names_text = names_text[1:-1]
        names = [n.strip() for n in names_text.replace("\n", " ").split(",") if n.strip()]
        kept = [n for n in names if n not in REVERT_FUNCS]
        if not kept:
            # Drop the entire line
            continue
        # Rewrite with only kept names
        new_lines.append(f"from utils.async_helpers import {', '.join(kept)}\n")
    return "".join(new_lines)


def main(argv: list[str] | None = None) -> int:
    if not argv:
        print("Usage: revert_gather_migration.py <file.py> [file2.py ...]", file=sys.stderr)
        return 2
    for path in argv:
        ok, msg = revert_file(path)
        status = "✓" if ok else "✗"
        print(f"{status} {path}: {msg}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
