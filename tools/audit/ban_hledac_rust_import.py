#!/usr/bin/env python3
"""
RUST-161 CI checker — ban direct hledac_rust_extensions imports.

ISSUE-02: 101 modules bypass the mandated core.rust_backend facade.
Direct imports skip ABI/capability scoring, force-override, and the prober —
so a broken/nonexistent extension crashes instead of falling back.

Allowed patterns:
  ✅ from hledac.universal.core.rust_backend import rust
  ✅ from hledac.universal.core.rust_backend import rust; rust.raw.X
  ✅ from hledac.universal.core.rust_backend import get_accel
  ✅ from hledac_rust_extensions import hledac_rust_extensions  (TYPE_CHECKING only in rust_backend/)
  ✅ rust_extensions/ (benchmarks/verify_build.py)
  ✅ tests/ (test files need direct access for compatibility testing)
  ✅ core/rust_backend/ submodules (they're the facade internals)

Violation: `from hledac_rust_extensions import X` or `import hledac_rust_extensions`
outside allowed directories without going through the facade.

Fix: Replace with:
    from hledac.universal.core.rust_backend import rust
    X = rust.raw.X  # None if unavailable

Run: python tools/audit/ban_hledac_rust_import.py [--fix]
"""
from __future__ import annotations

import ast
import argparse
import os
import sys
from pathlib import Path
from dataclasses import dataclass
from collections.abc import Iterator
from core import aclose


# Directories/files that are always allowed to import hledac_rust_extensions directly
ALLOWED_PATHS: frozenset[str] = frozenset({
    "core/rust_backend/",           # Facade internals
    "rust_extensions/",             # The extension itself
    "rust_extensions/benchmarks/",  # Benchmark scripts
    "rust_extensions/tests/",       # Extension tests
    "tests/",                       # Test files need direct access
    "tests/archive/",               # Archived tests
    ".venv/",                       # Virtual environment
    ".venv-test/",                  # Test venv
    "__pycache__/",                 # Cache
    ".git/",                        # Git
    "tools/migrate/",               # Migration scripts skip themselves
    "tools/audit/",                 # Audit scripts skip themselves
    "tools/probe_",                 # Probe scripts
    "probe/",                       # Probe directories
    "benchmarks_shadow/",           # Benchmark shadow scripts
})


def _should_skip_dir(dirname: str) -> bool:
    """Fast check for directories to skip during traversal."""
    skip_dirs = {
        ".venv", "venv", "__pycache__", "node_modules", ".git", "build", "dist",
        ".venv-test", "site-packages", ".cache", "target", ".tox", ".eggs",
        "probe_", "archive",
    }
    return dirname in skip_dirs

# Files that are always allowed (e.g., CI stubs, verify scripts)
ALLOWED_FILES: frozenset[str] = frozenset({
    "rust_extensions/verify_build.py",
    "rust_extensions/circuit_breaker_python.py",
    "benchmarks_shadow/",
    "core/preflight_diagnostics.py",  # Pre-flight needs direct access for diagnostics
})


@dataclass(frozen=True, slots=True)
class ImportViolation:
    """One hledac_rust_extensions import violation."""
    file: Path
    line: int
    col: int
    import_type: str  # "import" | "from"
    names: tuple[str, ...]  # imported names
    message: str


def _iter_imports(tree: ast.AST) -> Iterator[tuple[int, int, str, tuple[str, ...]]]:
    """Yield (lineno, col_offset, "import" | "from", (names,)) for all relevant imports."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "hledac_rust_extensions" for alias in node.names):
                yield (node.lineno, node.col_offset, "import", tuple(a.name for a in node.names))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "hledac_rust_extensions":
                names = tuple(a.name for a in node.names)
                yield (node.lineno, node.col_offset, "from", names)
            # Also check submodule imports: from hledac_rust_extensions.foo import bar
            elif node.module and node.module.startswith("hledac_rust_extensions."):
                names = tuple(a.name for a in node.names)
                yield (node.lineno, node.col_offset, "from", names)


def _is_allowed_path(path: Path, root: Path | None = None) -> bool:
    """Check if the file path is in an allowed directory.
    
    If root is provided, use it to make the path relative.
    """
    parts = tuple(path.parts)
    
    # Get the relative path from root for matching
    if root is not None:
        try:
            rel_path = path.relative_to(root)
            rel_posix = rel_path.as_posix()
        except ValueError:
            rel_posix = path.as_posix()
    else:
        rel_posix = path.as_posix()
    
    # Check allowed directories first (they're prefixes like "core/rust_backend/")
    for allowed in ALLOWED_PATHS:
        allowed_clean = allowed.rstrip("/")
        if rel_posix.startswith(allowed_clean + "/") or rel_posix == allowed_clean:
            return True
        # Also check by path parts
        allowed_parts = allowed_clean.split("/")
        if len(allowed_parts) <= len(parts):
            if parts[:len(allowed_parts)] == tuple(allowed_parts):
                return True
    
    # Check allowed files/directories in ALLOWED_FILES
    for allowed in ALLOWED_FILES:
        if allowed.endswith("/"):
            # Directory prefix
            allowed_clean = allowed.rstrip("/")
            if rel_posix.startswith(allowed_clean + "/") or rel_posix == allowed_clean:
                return True
            # Check if path starts with this directory
            allowed_parts = allowed_clean.split("/")
            if len(allowed_parts) <= len(parts):
                if parts[:len(allowed_parts)] == tuple(allowed_parts):
                    return True
        else:
            # Exact file match or part of path
            if rel_posix == allowed or rel_posix.endswith("/" + allowed):
                return True
    
    return False


def find_violations(root: Path, fix: bool = False) -> list[ImportViolation]:
    """Find direct hledac_rust_extensions imports that bypass the facade."""
    violations: list[ImportViolation] = []
    
    # Use os.walk with skip list for faster traversal
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip excluded directories in-place
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            
            py_file = Path(dirpath) / filename
            
            # Skip allowed paths
            if _is_allowed_path(py_file, root):
                continue
            
            # Skip migration and audit tools themselves
            rel_str = py_file.as_posix()
            if "tools/migrate/" in rel_str or "tools/audit/" in rel_str:
                continue
            
            try:
                content = py_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            
            try:
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError:
                continue
            
            for lineno, col, import_type, names in _iter_imports(tree):
                violations.append(ImportViolation(
                    file=py_file,
                    line=lineno,
                    col=col,
                    import_type=import_type,
                    names=names,
                    message=f"Direct hledac_rust_extensions import bypasses facade"
                ))
    
    return violations


def generate_fix(import_type: str, names: tuple[str, ...], alias: str | None = None) -> tuple[str, str]:
    """Generate the fixed import code.
    
    Returns (import_snippet, usage_note)
    """
    if import_type == "import":
        return (
            "from hledac.universal.core.rust_backend import rust",
            f"  # Replace: import hledac_rust_extensions → use rust.raw"
        )
    else:
        # from hledac_rust_extensions import X, Y
        if len(names) == 1:
            symbol = names[0]
            return (
                f"from hledac.universal.core.rust_backend import rust\n"
                f"{symbol} = rust.raw.{symbol}  # None if unavailable",
                f"  # Replace: {symbol} = rust.raw.{symbol}"
            )
        else:
            # Multiple imports - need multiple lines
            lines = ["from hledac.universal.core.rust_backend import rust"]
            for name in names:
                lines.append(f"{name} = rust.raw.{name}  # None if unavailable")
            return ("\n".join(lines), "  # Multiple symbols migrated to rust.raw")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ban direct hledac_rust_extensions imports (ISSUE-02)"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix violations (rewrite imports)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON list of violating files for migration script"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"),
        help="Root directory to scan"
    )
    args = parser.parse_args()

    violations = find_violations(args.root)

    if not violations:
        print("RUST-161: 0 violations — all hledac_rust_extensions imports go through facade")
        sys.exit(0)

    # If --json, output machine-readable list
    if args.json:
        import json
        by_file = {}
        for v in violations:
            rel = v.file.relative_to(args.root).as_posix()
            if rel not in by_file:
                by_file[rel] = []
            by_file[rel].append({
                "line": v.line,
                "import_type": v.import_type,
                "names": list(v.names),
            })
        print(json.dumps(by_file, indent=2))
        sys.exit(1)

    print(f"RUST-161: {len(violations)} violation(s) found:")
    
    # Group by file for cleaner output
    by_file: dict[Path, list[ImportViolation]] = {}
    for v in violations:
        by_file.setdefault(v.file, []).append(v)
    
    for file, file_violations in sorted(by_file.items()):
        rel = file.relative_to(args.root)
        print(f"\n  {rel}:")
        for v in file_violations:
            print(f"    L{v.line}: {v.import_type} hledac_rust_extensions import {v.names}")

    print("\n" + "=" * 60)
    print("Fix: Replace direct imports with facade pattern:")
    print("  from hledac.universal.core.rust_backend import rust")
    print("  X = rust.raw.X  # None if unavailable")
    print("")
    print("For module-level imports (TYPE_CHECKING), use:")
    print("  if TYPE_CHECKING:")
    print("      from hledac.universal.core.rust_backend import rust")
    print("      # type alias: MyType = rust.raw.MyClass")
    print("=" * 60)

    if args.fix:
        print("\n[RUST-161] Auto-fix not yet implemented.")
        print("Use tools/migrate/migrate_rust_import_to_facade.py for full migration.")
        print(f"\n{len(violations)} violations need manual review.")

    sys.exit(1)


if __name__ == "__main__":
    main()
