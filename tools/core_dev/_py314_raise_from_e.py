"""
Helper: Add `from e` to bare `raise XError(...)` inside except blocks.

Rules:
- Only add `from e` when:
  * The raise is inside an except block
  * The except binds the exception to a variable (e.g. `except X as e:`)
  * The raise target name matches the variable name OR the raise has no `from` already
- Skip if raise already has `from Y` (don't double-chain)
- Skip if raise is outside any except block (no source to chain)
- Skip bare `raise` (re-raise) - already implicit
- Skip `raise X from None` (explicit suppression)
- Skip inside `finally` blocks (chained from finally is misleading)

Usage: python tools/_py314_raise_from_e.py <file> [file ...]
"""

import ast
import sys
from pathlib import Path


def find_enclosing_except(node: ast.AST) -> ast.ExceptHandler | None:
    """Find the except block this node is inside, by walking parents."""
    # We can't walk parents from child. Caller must pass a path.
    return None


def find_exception_var(handler: ast.ExceptHandler) -> str | None:
    """Return the name bound by `except X as e:` or None."""
    if handler.name and isinstance(handler.name, str):
        return handler.name
    return None


def collect_except_handlers(tree: ast.AST) -> dict[int, ast.ExceptHandler]:
    """Map node id → enclosing except handler (for all nodes inside any except)."""
    result: dict[int, ast.ExceptHandler] = {}

    def visit(node: ast.AST, handler: ast.ExceptHandler | None) -> None:
        if isinstance(node, ast.ExceptHandler):
            handler = node
        # ty: only assign when handler is concrete — None means "not inside
        # any except block" and is a valid sentinel for downstream callers
        # (they skip emit on None).
        if handler is not None:
            result[id(node)] = handler
        for child in ast.iter_child_nodes(node):
            visit(child, handler)

    visit(tree, None)
    return result


def is_in_finally(node: ast.AST, tree: ast.AST) -> bool:
    """Check if node is inside a finally block of a try statement."""
    parents: dict[int, ast.AST] = {}

    def build_parents(n: ast.AST) -> None:
        for child in ast.iter_child_nodes(n):
            parents[id(child)] = n
            build_parents(child)

    build_parents(tree)
    cur = parents.get(id(node))
    while cur is not None:
        if isinstance(cur, ast.Try) and cur.finalbody:
            # Check if node is in finalbody
            for n in cur.finalbody:
                # Walk descendants
                for sub in ast.walk(n):
                    if id(sub) == id(node):
                        return True
        cur = parents.get(id(cur))
    return False


def process_file(path: Path, verbose: bool = True) -> tuple[int, int]:
    """Returns (added_count, skipped_count)."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        if verbose:
            print(f"  SKIP {path}: syntax error {e}")
        return 0, 0

    handler_map = collect_except_handlers(tree)
    rewrites: dict[int, str] = {}  # line number → new raise line
    added = 0
    skipped = 0

    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue
        if node.exc is None:
            # bare `raise` - already implicit re-raise
            skipped += 1
            continue
        if node.cause is not None:
            # already has `from X`
            skipped += 1
            continue

        handler = handler_map.get(id(node))
        if handler is None:
            # not in any except block
            skipped += 1
            continue

        var_name = find_exception_var(handler)
        if var_name is None:
            # except X: (no variable) - can't reference, skip
            skipped += 1
            continue

        if is_in_finally(node, tree):
            # Don't add from in finally (semantic mismatch)
            skipped += 1
            continue

        # Skip bare re-raise: `raise e` where e is the same variable as the except
        if isinstance(node.exc, ast.Name) and node.exc.id == var_name:
            # `raise e` is already an explicit re-raise of the current exception
            skipped += 1
            continue

        # Find the line text
        lines = src.splitlines(keepends=True)
        line_text = lines[node.lineno - 1]
        stripped = line_text.rstrip("\n")
        new_line = f"{stripped} from {var_name}\n"
        rewrites[node.lineno] = new_line
        added += 1

    if not rewrites:
        return 0, skipped

    lines = src.splitlines(keepends=True)
    new_lines = []
    for i, line in enumerate(lines, start=1):
        if i in rewrites:
            new_lines.append(rewrites[i])
        else:
            new_lines.append(line)

    new_src = "".join(new_lines)
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        if verbose:
            print(f"  ABORT {path}: re-parse failed {e}")
        return 0, skipped

    path.write_text(new_src, encoding="utf-8")
    if verbose:
        print(f"  {path}: added={added} skipped={skipped}")
    return added, skipped


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python tools/_py314_raise_from_e.py <file> [file ...]")
        sys.exit(1)
    total_added = 0
    total_skipped = 0
    for arg in sys.argv[1:]:
        path = Path(arg)
        if not path.exists():
            print(f"MISSING: {path}")
            continue
        added, skipped = process_file(path)
        total_added += added
        total_skipped += skipped
    print(f"\nTotal: added={total_added} skipped={total_skipped}")


if __name__ == "__main__":
    main()
