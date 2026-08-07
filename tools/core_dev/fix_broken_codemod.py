#!/usr/bin/env python3
# hledac/universal/tools/fix_broken_codemod.py
#
# Repair files broken by the first codemod run. The bug was that
# node.end_col_offset was unreliable for multi-line calls, so the
# replacement text was spliced into the wrong place and leftover characters
# stayed behind.
#
# Patterns seen in broken output:
#   - "awaitsafe_gather_*(...)" — `await` concatenated with replacement
#   - "...safe_gather_*(...)  extra_text" — leftover chars from original call
#   - "...safe_gather_*(...))" — extra `)` because end_col included the `)`
#   - "tasks = [...safe_gather_*(...)]" — replacement in middle of list comp
#
# Strategy: regex over text, find safe_gather_*(...) and reconstruct from
# available info. The label="file:line" gives us the original line number;
# the args inside parens are mostly preserved.

import re
import sys

FUNC_NAMES = ("safe_gather_ok", "safe_gather_fire_and_forget", "safe_gather_strict", "safe_gather")
FUNC_PATTERN = "|".join(re.escape(f) for f in FUNC_NAMES)
# No boundary — `awaitsafe_gather_dropin` is a valid target
SAFE_GATHER_CALL = re.compile(rf"({FUNC_PATTERN})\s*\(", re.MULTILINE)


def find_balanced(text: str, start: int) -> int | None:
    """Return index of matching `)` for the `(` at `text[start]`, or None."""
    if text[start] != "(":
        return None
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
            i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                i += 1
        i += 1
    return None


def drop_label_arg(args: str) -> str:
    """Remove `, label="..."` or `label="...",` from inside the parens."""
    args = re.sub(
        r",\s*label\s*=\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\)|[^,()]+)\s*",
        "",
        args,
    )
    args = re.sub(
        r"^\s*label\s*=\s*(?:\"[^\"]*\"|'[^']*'|\([^)]*\)|[^,()]+)\s*,\s*",
        "",
        args,
    )
    return args.strip().rstrip(",")


def fix_broken_file(path: str) -> tuple[bool, str]:
    """Try to repair the broken file. Returns (ok, message)."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
    except OSError as e:
        return False, f"cannot read: {e}"

    try:
        import ast
        ast.parse(source)
        return True, "already parses"
    except SyntaxError:  # noqa: BLE001
        pass

    n_fixed = 0
    matches = list(SAFE_GATHER_CALL.finditer(source))
    matches.reverse()

    for m in matches:
        paren_start = m.end() - 1
        paren_end = find_balanced(source, paren_start)
        if paren_end is None:
            continue

        prefix_start = m.start()
        prefix_window = source[max(0, prefix_start - 10):prefix_start]
        await_concat = ""
        if prefix_window.endswith("await"):
            await_concat = "await "
            prefix_start -= len("await")
        elif prefix_window.endswith("with"):
            await_concat = "with "
            prefix_start -= len("with")

        args_text = source[paren_start + 1:paren_end]
        args_clean = drop_label_arg(args_text)

        after = source[paren_end + 1:paren_end + 200]
        leftover_match = re.match(
            r"(\s+[A-Za-z_][\w., ]*?return_exceptions\s*=\s*True\s*\))",
            after,
        )
        leftover_end = paren_end + 1
        if leftover_match:
            leftover_end += len(leftover_match.group(1))
        elif re.match(r"\s*\)", after):
            leftover_match = re.match(r"(\s*\))", after)
            if leftover_match:
                leftover_end += len(leftover_match.group(1))

        replacement = f"{await_concat}asyncio.gather({args_clean}, return_exceptions=True)"

        source = source[:prefix_start] + replacement + source[leftover_end:]
        n_fixed += 1

    try:
        import ast
        ast.parse(source)
    except SyntaxError as e:
        return False, f"still broken after {n_fixed} fixes: {e}"

    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return True, f"fixed {n_fixed} calls"


def main(argv):
    if not argv:
        print("Usage: fix_broken_codemod.py <file.py> [...]", file=sys.stderr)
        return 2
    for path in argv:
        ok, msg = fix_broken_file(path)
        status = "✓" if ok else "✗"
        print(f"{status} {path}: {msg}")


if __name__ == "__main__":
    main(sys.argv[1:])
