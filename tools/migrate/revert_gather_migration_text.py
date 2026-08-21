#!/usr/bin/env python3
# hledac/universal/tools/revert_gather_migration_text.py
#
# Text-based revert for files broken by a previous codemod run. Uses regex
# to find `safe_gather_*(...)` patterns without requiring the file to parse.

import re
import sys

REVERT_FUNCS = r"safe_gather(?:_dropin|_fire_and_forget|_strict)?"

# Match: `safe_gather_X(...balanced parens...)` (multi-line aware)
PATTERN = re.compile(
    rf"\b({REVERT_FUNCS})\s*\(",
    re.MULTILINE,
)

# Match: `from utils.asyncx import ...`
IMPORT_PATTERN = re.compile(
    r"^from\s+utils\.asyncx\s+import\s+[^\n]+$",
    re.MULTILINE,
)


def find_balanced_paren(text: str, start: int) -> int | None:
    """Find the matching `)` for the `(` at `text[start]`. Returns the index
    of the matching `)`, or None if unbalanced."""
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
            # Skip string literal
            quote = c
            i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == "\\":
                    i += 1
                i += 1
        i += 1
    return None


def revert_text(source: str) -> tuple[str, int]:
    """Revert `safe_gather_*(args, label="x:y")` back to
    `asyncio.gather(args, return_exceptions=True)`. Returns (new_source, n_reverted).
    """
    n_reverted = 0
    # Process matches in REVERSE order (so we don't invalidate later indices)
    matches = list(PATTERN.finditer(source))
    matches.reverse()

    for m in matches:
        m.group(1)
        paren_start = m.end() - 1  # the `(` is at end-1
        paren_end = find_balanced_paren(source, paren_start)
        if paren_end is None:
            continue

        # Extract args (between `(` and `)`)
        args_text = source[paren_start + 1 : paren_end]

        # Drop the `label="..."` kwarg
        # Simple regex: remove `,?\s*label\s*=\s*("..."|'...'|...)`
        args_text = re.sub(
            r",\s*label\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^,)]+)\s*",
            "",
            args_text,
        )
        args_text = re.sub(
            r"^\s*label\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^,)]+)\s*,\s*",
            "",
            args_text,
        )
        args_text = args_text.strip().rstrip(",")

        replacement = f"asyncio.gather({args_text}, return_exceptions=True)"

        # Splice
        start = m.start()
        end = paren_end + 1
        source = source[:start] + replacement + source[end:]
        n_reverted += 1

    # Strip safe_gather_* from imports
    def fix_import(m) -> str:
        text = m.group(0)
        body = re.sub(r"^from\s+utils\.asyncx\s+import\s+", "", text).strip()
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
        names = [n.strip() for n in body.split(",") if n.strip()]
        kept = [n for n in names if not re.fullmatch(REVERT_FUNCS, n)]
        if not kept:
            return ""
        return f"from utils.asyncx import {', '.join(kept)}"

    source = IMPORT_PATTERN.sub(fix_import, source)

    source = re.sub(r"\n\n\n+", "\n\n", source)

    return source, n_reverted


def main(argv) -> int | None:
    if not argv:
        print("Usage: revert_gather_migration_text.py <file.py> [...]", file=sys.stderr)
        return 2
    for path in argv:
        try:
            with open(path, encoding="utf-8") as f:
                source = f.read()
        except OSError as e:
            print(f"✗ {path}: cannot read ({e})")
            continue
        new_source, n = revert_text(source)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_source)
        # Verify it parses now
        try:
            import ast

            ast.parse(new_source)
            print(f"✓ {path}: reverted {n} calls, parses OK")
        except SyntaxError as e:
            print(f"⚠ {path}: reverted {n} calls, but still has syntax error: {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
