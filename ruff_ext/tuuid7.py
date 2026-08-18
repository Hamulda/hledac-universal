"""
TUUID7: Detect uuid.uuid4() in hot paths and suggest uuid.uuid7().

Python 3.14+ provides uuid.uuid7() which is:
- Time-ordered (sortable by creation time)
- Built-in (no external dependency)
- Ideal for log/trace correlation

This rule detects direct use of uuid.uuid4() in:
- Write paths (evidence creation, storage)
- Async futures (correlation IDs)
- Session/rpc IDs

Rule ID: TUUID7
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    file: Path
    line: int
    col: int
    name: str
    message: str


# Hot path patterns - files/functions that should use uuid7
HOT_PATH_PATTERNS = frozenset({
    # Write paths
    "evidence",
    "sink",
    "store",
    "write",
    "insert",
    "append",
    # Async paths
    "future",
    "async",
    "await",
    # Session/correlation
    "session",
    "correlation",
    "trace",
    "pivot",
    # Dedup/content addressing
    "dedup",
    "fingerprint",
    "hash",
})

# Files/patterns that MUST use uuid.uuid4() (standard compliance)
# WARC ISO 28500 requires <urn:uuid:UUID> format - random UUIDs required
WARC_EXEMPT_PATTERNS = frozenset({
    "evidence/_archiver",
    "evidence_log",
    "warc",
})


def _is_uuid4_call(node: ast.expr) -> bool:
    """Check if node is uuid.uuid4() call."""
    if not isinstance(node, ast.Call):
        return False
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "uuid4":
        return False
    # Check if it's uuid.uuid4() (module attribute)
    if isinstance(node.func.value, ast.Name) and node.func.value.id == "uuid":
        return True
    return False


def _is_in_hot_path(func_name: str | None, file_path: Path) -> bool:
    """Check if function/file is a hot path that should use uuid7."""
    file_lower = str(file_path).lower()

    # Check for WARC exemption first (must use uuid4 for ISO compliance)
    for pattern in WARC_EXEMPT_PATTERNS:
        if pattern in file_lower:
            return False

    if func_name:
        func_lower = func_name.lower()
        for pattern in HOT_PATH_PATTERNS:
            if pattern in func_lower:
                return True

    for pattern in HOT_PATH_PATTERNS:
        if pattern in file_lower:
            return True
    return False


def _check_node(node: ast.AST, file_path: Path, current_function: str | None) -> list[Violation]:
    """Recursively check AST node for uuid.uuid4() calls."""
    violations = []

    # Check for uuid.uuid4() call
    if _is_uuid4_call(node):
        # Check if it's in a hot path
        if _is_in_hot_path(current_function, file_path):
            violations.append(Violation(
                file=file_path,
                line=node.lineno or 0,
                col=node.col_offset or 0,
                name="TUUID7",
                message=f"TUUID7: Use uuid.uuid7() instead of uuid.uuid4() for time-ordered IDs in hot paths. "
                        f"uuid.uuid7() is built-in in Python 3.14+, provides sortable IDs for log correlation.",
            ))
        return violations

    # Recurse into child nodes
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef):
            violations.extend(_check_node(child, file_path, child.name))
        else:
            violations.extend(_check_node(child, file_path, current_function))

    return violations


def check_file(path: Path) -> list[Violation]:
    """Check a single Python file for uuid.uuid4() in hot paths."""
    violations = []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    try:
        tree = ast.parse(content, filename=str(path))
    except SyntaxError:
        return violations

    for node in ast.walk(tree):
        if _is_uuid4_call(node):
            if _is_in_hot_path(None, path):
                violations.append(Violation(
                    file=path,
                    line=node.lineno or 0,
                    col=node.col_offset or 0,
                    name="TUUID7",
                    message=f"TUUID7: Use uuid.uuid7() instead of uuid.uuid4() for time-ordered IDs in hot paths. "
                            f"uuid.uuid7() is built-in in Python 3.14+, provides sortable IDs for log correlation.",
                ))

    return violations


def check_directory(root: Path, exclude_dirs: frozenset[str] | None = None) -> list[Violation]:
    """Recursively check a directory for uuid.uuid4() in hot paths."""
    if exclude_dirs is None:
        exclude_dirs = frozenset({
            "__pycache__", ".venv", ".venv-test", ".git", ".claude",
            "archive", ".mypy_cache", ".pytest_cache", "stubs",
            ".ruff_cache", "tools/audit",
        })

    # Substring-based exclusions
    EXCLUDE_SUBSTRINGS: frozenset[str] = frozenset({
        "tools/probe/", "tools/_archive/", "tools/probe_",
        "probe/",  # probe/ subdirectories
    })

    all_violations = []
    for py_file in root.rglob("*.py"):
        rel = str(py_file.relative_to(root))
        # Exact part-match exclusions
        if any(excluded in py_file.parts for excluded in exclude_dirs):
            continue
        # Substring exclusions
        if any(excluded in rel for excluded in EXCLUDE_SUBSTRINGS):
            continue
        violations = check_file(py_file)
        all_violations.extend(violations)
    return all_violations


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="TUUID7: uuid.uuid4() in hot paths detector")
    parser.add_argument("--root", type=Path, default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"))
    parser.add_argument("--ci", action="store_true", help="CI mode: exit 1 on violations")
    args = parser.parse_args()

    violations = check_directory(args.root)

    if not violations:
        print("TUUID7: 0 violations — no uuid.uuid4() found in hot paths")
        sys.exit(0)

    print(f"TUUID7: {len(violations)} violation(s) found:")
    for v in violations:
        rel = v.file.relative_to(args.root)
        print(f"  {rel}:{v.line}: {v.message}")

    print("\nFix: Replace uuid.uuid4() with uuid.uuid7() for time-ordered IDs.")
    print("Python 3.14+: uuid.uuid7() is built-in.")
    print("Alternatively: from hledac.universal.utils.uuid7 import new_runtime_id")

    if args.ci:
        sys.exit(1)


if __name__ == "__main__":
    main()
