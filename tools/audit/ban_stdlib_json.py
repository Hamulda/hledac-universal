#!/usr/bin/env python3
"""
G4: CI checker — ban stdlib json outside except-blocks.

Ban `import json` / `from json import` outside of:
  - `except ImportError` as final fallback (orjson unavailable)
  - `except Exception` as last-resort fallback
  - `import json as _stdlib_json` (explicit aliased imports are intentional fallbacks)

Allowed directories (specific requirements):
  - tools/ - CLI tools with specific output formatting
  - security/ - Security audit tools
  - config/ - Configuration files
  - benchmarks/ - Performance comparison benchmarks (json vs orjson)
  - tests/ - Test files
  - rust_extensions/ - Rust FFI (uses orjson internally)

Allowed files:
  - tools/serialization.py - Hash-chain canonical serialization (byte-for-byte determinism)
  - utils/codec.py - Canonical codec with proper fallback
  - _core/rust_backend/*.py - Rust backend fallbacks

Preferred alternatives:
  - orjson: faster, modern stdlib json replacement
  - msgspec: fastest for typed structs with known schema
  - utils/codec.py: canonical JSON interface (wraps orjson/msgspec)

Run: python tools/audit/ban_stdlib_json.py [--fix]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


def find_violations(root: Path, fix: bool = False) -> list[tuple[Path, int, str]]:
    """Find stdlib json imports outside except-blocks."""
    violations = []
    # Directories with specific requirements - SKIP entirely
    skip_prefixes = (
        "tests/",
        "benchmarks_shadow/",
        "archive/",
        ".venv",
        ".git",
        "tools/",
        "security/",
        "config/",
        "benchmarks/",
        "rust_extensions/",
        ".hypothesis",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
    )
    # Individual files that are allowed to use stdlib json
    skip_files = {
        "tools/serialization.py",  # Hash-chain canonical serialization
        "utils/codec.py",  # Canonical codec with proper fallback
    }
    # Files in _core that use stdlib json for Rust backend fallbacks
    skip_in_patterns = ("_core/rust_backend/",)

    for py_file in root.rglob("*.py"):
        path_str = str(py_file)
        if any(path_str.startswith(prefix) for prefix in skip_prefixes):
            continue
        if any(py_file.name.endswith(s) for s in skip_files):
            continue
        if any(p in path_str for p in skip_in_patterns):
            continue
        try:
            content = py_file.read_text()
        except OSError, UnicodeDecodeError:
            continue

        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        # Track if we're inside an except block
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue

            # Check if this is a json import
            is_json_import = False
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "json":
                        is_json_import = True
                        break
            elif isinstance(node, ast.ImportFrom):
                if node.module == "json":
                    is_json_import = True

            if not is_json_import:
                continue

            # Check if this import is inside an except block
            if _is_inside_except_block(node, tree):
                continue  # Legitimate fallback

            # Violation: stdlib json import outside except block
            lineno = getattr(node, "lineno", 0)
            if isinstance(node, ast.Import):
                violations.append((py_file, lineno, "`import json` outside except-block (use orjson/msgspec instead)"))
            else:
                violations.append(
                    (py_file, lineno, "`from json import ...` outside except-block (use orjson/msgspec instead)")
                )

    return violations


def _is_inside_except_block(node: ast.AST, tree: ast.AST) -> bool:
    """Check if node is inside an except handler."""
    for parent_node in ast.walk(tree):
        if isinstance(parent_node, ast.ExceptHandler):
            # Check if our node is in the body of this except handler
            if _node_in_list(node, parent_node.body):
                return True
            # Check if it's in the type expression (rare but possible)
            if parent_node.type and _node_in_list(node, [parent_node.type]):
                return True
    return False


def _node_in_list(node: ast.AST, nodes: list[ast.AST]) -> bool:
    """Check if node is anywhere in the list (including nested)."""
    for n in nodes:
        if n is node:
            return True
        if isinstance(n, ast.AST):
            for child in ast.walk(n):
                if child is node:
                    return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="G4: Ban stdlib json outside except-blocks")
    parser.add_argument("--fix", action="store_true", help="Auto-fix violations (not implemented)")
    parser.add_argument("--root", type=Path, default=None, help="Root directory to scan")
    args = parser.parse_args()

    # Default to hledac/universal subdirectory for production code
    if args.root is None:
        root = Path("hledac/universal").resolve()
    else:
        root = args.root.resolve()

    violations = find_violations(root, fix=args.fix)

    if violations:
        print("G4 VIOLATIONS: stdlib json imports outside except-blocks:")
        for path, lineno, msg in violations:
            print(f"  {path}:{lineno}: {msg}")
        print(f"\nTotal: {len(violations)} violations")
        return 1
    else:
        print("✓ No stdlib json violations found")
        return 0


if __name__ == "__main__":
    sys.exit(main())
