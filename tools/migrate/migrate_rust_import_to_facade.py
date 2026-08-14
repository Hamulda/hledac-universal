#!/usr/bin/env python3
"""
Migrate direct hledac_rust_extensions imports to use the facade.

ISSUE-02: 101 modules bypass the mandated core.rust_backend facade.
This script rewrites imports to use the safe facade pattern.

Migration patterns:

1. Direct module import with fallback:
   FROM:
       try:
           import hledac_rust_extensions as rust
           _VAR = getattr(rust, 'symbol', None)
       except ImportError:
           _VAR = None
   
   TO:
       from hledac.universal.core.rust_backend import rust
       _VAR = getattr(rust.raw, 'symbol', None)

2. Specific function imports:
   FROM:
       from hledac_rust_extensions import func1, func2
   
   TO:
       from hledac.universal.core.rust_backend import rust
       func1 = rust.raw.func1
       func2 = rust.raw.func2

3. Submodule imports:
   FROM:
       from hledac_rust_extensions import dns
       from hledac_rust_extensions.submodule import symbol
   
   TO:
       dns = rust.dns  # via submodule accessor
       symbol = rust.raw.submodule_symbol  # via raw accessor

Run: python tools/migrate/migrate_rust_import_to_facade.py [--all | files...] [--dry-run]
"""
from __future__ import annotations

import ast
import argparse
import os
import re
import sys
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Iterator


# Directory names to skip entirely
SKIP_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "__pycache__", "node_modules", ".git", "build", "dist",
    ".venv-test", "site-packages", ".cache", "target",
})

# Subdirectory prefixes to skip (with trailing slash for prefix matching)
SKIP_PREFIXES: tuple[str, ...] = (
    "rust_extensions/",
    "tests/",
    "benchmarks_shadow/",
    "probe/",
    "hledac/",
    "tools/migrate/",
    "tools/audit/",
)


def _is_allowed_path(path: Path) -> bool:
    """Check if the file path is allowed to use direct imports."""
    posix = path.as_posix()
    parts = tuple(path.parts)
    
    # core/rust_backend/ submodules are allowed (they're the facade implementation)
    if "core" in parts and "rust_backend" in parts:
        return True
    
    # Check skip directories
    for skip in SKIP_DIRS:
        if skip in parts:
            return True
    
    # Check skip external directories
    for skip in EXTERNAL_DIRS:
        if skip in parts:
            return True
    
    # Check skip prefixes - need to handle absolute paths
    for prefix in SKIP_PREFIXES:
        # Check if posix ends with the prefix (e.g., ends with "tools/migrate/")
        if posix.endswith("/" + prefix.rstrip("/")) or posix.endswith(prefix):
            return True
        # Also check if any path part matches
        if prefix.rstrip("/") in parts:
            return True
    
    return False

# Known submodule names in hledac_rust_extensions
KNOWN_SUBMODULES: frozenset[str] = frozenset({
    "dns", "rate_limit", "fulltext", "native_db", "stix", "stix_2_1",
    "simdjson", "link_predictor", "whisper", "anti_analysis", "tls",
    "arti", "ane", "fulltext_index", "feed_decision", "feed_pipeline",
    "pipeline_compose", "signal_batch", "federated_qtable", "async_query",
    "h2_safari_preset", "stealth_bridge",
})


@dataclass(frozen=True, slots=True)
class ImportSite:
    """One hledac_rust_extensions import site."""
    file: str
    line: int
    col: int
    end_line: int
    end_col: int
    import_type: str  # "import" | "from"
    module: str | None  # None for "import", module name for "from"
    names: tuple[tuple[str, str | None], ...]  # (name, alias) pairs
    in_try_block: bool
    in_type_checking: bool
    source: str  # Original source code


@dataclass(slots=True)
class MigrationReport:
    """Report for migration run."""
    files_scanned: int = 0
    files_changed: int = 0
    imports_total: int = 0
    imports_migrated: int = 0
    imports_skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files_scanned": self.files_scanned,
            "files_changed": self.files_changed,
            "imports_total": self.imports_total,
            "imports_migrated": self.imports_migrated,
            "imports_skipped": self.imports_skipped,
            "errors": self.errors,
        }


def _is_allowed_path(path: Path) -> bool:
    """Check if the file path is allowed to use direct imports."""
    posix = path.as_posix()
    parts = tuple(path.parts)
    
    # core/rust_backend/ submodules are allowed (they're the facade implementation)
    if "core" in parts and "rust_backend" in parts:
        return True
    
    # Check skip directories
    for skip in SKIP_DIRS:
        if skip in parts:
            return True
    
    # Check skip prefixes
    for prefix in SKIP_PREFIXES:
        if posix.startswith(prefix) or prefix.rstrip("/") in parts:
            return True
    
    return False


def _find_import_sites(content: str, filepath: str) -> list[ImportSite]:
    """Find all hledac_rust_extensions imports in the file."""
    try:
        tree = ast.parse(content, filename=filepath)
    except SyntaxError:
        return []
    
    sites: list[ImportSite] = []
    
    # Track if we're in TYPE_CHECKING block
    in_type_checking = False
    type_checking_stack: list[str] = []
    
    # Build parent map to find enclosing try blocks
    parent_map: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[id(child)] = parent
    
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # Check for TYPE_CHECKING block
            if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                in_type_checking = True
            elif isinstance(node.test, ast.Attribute):
                if isinstance(node.test.value, ast.Name) and node.test.value.id == "TYPE_CHECKING":
                    in_type_checking = True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (
                node.module == "hledac_rust_extensions" or
                node.module.startswith("hledac_rust_extensions.")
            ):
                names = []
                for alias in node.names:
                    names.append((alias.name, alias.asname))
                
                # Check if in try block
                in_try = False
                node_id = id(node)
                cur = node
                while id(cur) in parent_map:
                    cur = parent_map[id(cur)]
                    if isinstance(cur, ast.Try):
                        in_try = True
                        break
                    if isinstance(cur, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        break
                
                sites.append(ImportSite(
                    file=filepath,
                    line=node.lineno,
                    col=node.col_offset,
                    end_line=node.end_lineno or node.lineno,
                    end_col=node.end_col_offset or 0,
                    import_type="from",
                    module=node.module,
                    names=tuple(names),
                    in_try_block=in_try,
                    in_type_checking=in_type_checking,
                    source=_get_node_source(content, node),
                ))
        
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "hledac_rust_extensions":
                    # Check if in try block
                    in_try = False
                    node_id = id(node)
                    cur = node
                    while id(cur) in parent_map:
                        cur = parent_map[id(cur)]
                        if isinstance(cur, ast.Try):
                            in_try = True
                            break
                        if isinstance(cur, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            break
                    
                    sites.append(ImportSite(
                        file=filepath,
                        line=node.lineno,
                        col=node.col_offset,
                        end_line=node.end_lineno or node.lineno,
                        end_col=node.end_col_offset or 0,
                        import_type="import",
                        module=None,
                        names=((alias.name, alias.asname),),
                        in_try_block=in_try,
                        in_type_checking=False,  # Import statements aren't in TYPE_CHECKING
                        source=_get_node_source(content, node),
                    ))
    
    return sites


def _get_node_source(content: str, node: ast.AST) -> str:
    """Get the source code for an AST node."""
    lines = content.splitlines(keepends=True)
    start = node.lineno - 1
    end = (node.end_lineno or node.lineno)
    
    if start == end - 1:
        return lines[start][node.col_offset:node.end_col_offset or None]
    
    # Multi-line: collect all lines
    result = []
    if start < len(lines):
        result.append(lines[start][node.col_offset:])
    for i in range(start + 1, min(end, len(lines))):
        result.append(lines[i])
    return "".join(result)


def _is_submodule_import(names: tuple[tuple[str, str | None], ...]) -> bool:
    """Check if import is a submodule (class/function) or attribute access."""
    # Submodule names typically don't start with uppercase (unless it's a class)
    # and are often used as module-level functions
    return all(
        not name[0].startswith("_") and name[0][0].islower() 
        for name in names
        if name[0] not in KNOWN_SUBMODULES
    )


def _generate_migration(site: ImportSite) -> tuple[str, list[str]]:
    """Generate the migrated code for an import site.
    
    Returns (replacement_code, list_of_replacement_parts)
    """
    if site.import_type == "import":
        # Pattern: import hledac_rust_extensions [as alias]
        alias = site.names[0][1] or "hledac_rust_extensions"
        return (
            "from hledac.universal.core.rust_backend import rust",
            [f"rust raw access via rust.raw (replaces: {alias})"]
        )
    
    else:
        # from hledac_rust_extensions import X, Y [as alias]
        parts: list[str] = []
        notes: list[str] = []
        
        # Check if it's a submodule import
        module_name = site.module or "hledac_rust_extensions"
        
        if module_name.startswith("hledac_rust_extensions."):
            submodule = module_name.split(".", 1)[1]
            # Submodule import like: from hledac_rust_extensions import dns
            if submodule in KNOWN_SUBMODULES:
                parts.append("from hledac.universal.core.rust_backend import rust")
                parts.append(f"{submodule} = rust.{submodule}  # None if unavailable")
                notes.append(f"submodule accessor for {submodule}")
            else:
                # Other submodule attribute
                names_str = ", ".join(name for name, _ in site.names)
                parts.append("from hledac.universal.core.rust_backend import rust")
                for name, alias in site.names:
                    parts.append(f"{alias or name} = rust.raw.{submodule}.{name}  # None if unavailable")
                notes.append(f"submodule {submodule}.{names_str}")
        else:
            # Direct import from hledac_rust_extensions
            names_str = ", ".join(name for name, _ in site.names)
            parts.append("from hledac.universal.core.rust_backend import rust")
            for name, alias in site.names:
                parts.append(f"{alias or name} = rust.raw.{name}  # None if unavailable")
            notes.append(f"symbols: {names_str}")
        
        return "\n".join(parts), notes


def _should_skip_migration(site: ImportSite) -> bool:
    """Check if this import site should be skipped (allowed patterns)."""
    # TYPE_CHECKING blocks in rust_backend are allowed
    if site.in_type_checking and "core/rust_backend/" in site.file:
        return True
    
    # Already using facade pattern
    if "core.rust_backend" in site.source or "rust.raw" in site.source:
        return True
    
    return False


def _replace_in_source(content: str, sites: list[ImportSite]) -> tuple[str, list[str]]:
    """Replace import sites in source code.
    
    Returns (new_content, list_of_changes)
    """
    if not sites:
        return content, []
    
    changes: list[str] = []
    
    # Sort by line/col in reverse order to preserve positions
    sorted_sites = sorted(sites, key=lambda s: (s.line, s.col), reverse=True)
    
    lines = content.splitlines(keepends=True)
    
    for site in sorted_sites:
        start_line = site.line - 1
        end_line = site.end_line
        
        # Get indentation from first line
        indent = ""
        if start_line < len(lines):
            match = re.match(r"(\s*)", lines[start_line])
            if match:
                indent = match.group(1)
        
        # Generate replacement
        replacement, notes = _generate_migration(site)
        
        # Add indentation to each line
        indented = "\n".join(f"{indent}{line}" if line else "" for line in replacement.split("\n"))
        
        # Handle the try/except context if needed
        if site.in_try_block:
            # For try/except patterns, we need to remove the try/except wrapper
            # This is more complex and handled separately
            changes.append(f"  {site.file}:{site.line}: migrated try-except pattern")
        else:
            changes.append(f"  {site.file}:{site.line}: {', '.join(notes) if notes else 'import migrated'}")
        
        # Replace the lines
        if start_line < len(lines):
            # Preserve leading whitespace for first line
            leading = re.match(r"(\s*)", lines[start_line]).group(1) if lines[start_line].strip() else ""
            lines[start_line] = indented
        
        # Remove following lines if multi-line
        while len(lines) > start_line + 1 and lines[start_line + 1].strip() and not lines[start_line + 1].strip().startswith("#"):
            # Check if next line is part of the import
            next_indent = len(lines[start_line + 1]) - len(lines[start_line + 1].lstrip())
            current_indent = len(lines[start_line]) - len(lines[start_line].lstrip())
            if next_indent > current_indent:
                del lines[start_line + 1]
            else:
                break
    
    return "".join(lines), changes


def iter_python_files(targets: list[str], all_files: bool, root: Path) -> list[str]:
    """Return the list of .py files to process.
    
    If targets are provided, use them directly.
    If --all is used, scan all Python files and check for violations.
    """
    if not all_files and targets:
        # Filter out non-Python files
        return [t for t in targets if t.endswith(".py")]
    
    # For --all, scan all Python files and check for violations
    out: list[str] = []
    
    for root_dir, dirs, files in os.walk(root):
        # Skip excluded directories in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d not in EXTERNAL_DIRS]
        
        for f in files:
            if f.endswith(".py"):
                full = os.path.join(root_dir, f)
                # Skip migration scripts themselves and core/rust_backend internals
                if "tools/migrate/" in full or "tools/audit/" in full:
                    continue
                if "core" in root_dir and "rust_backend" in root_dir:
                    continue
                out.append(full)
    
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate hledac_rust_extensions imports to facade pattern"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Python files to process (default: --all)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Process entire repo (excl. vendored)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change, don't write"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print JSON report at end"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-file output"
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal"),
    )
    args = parser.parse_args(argv)
    
    if not args.files and not args.all:
        parser.error("Provide files or --all")
    
    targets = iter_python_files(args.files, all_files=args.all, root=args.root)
    
    if not targets:
        print("No Python files matched", file=sys.stderr)
        return 0
    
    report = MigrationReport()
    exit_code = 0
    
    for filepath in targets:
        report.files_scanned += 1
        
        try:
            with open(filepath, encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            report.errors.append(f"{filepath}: {e}")
            continue
        
        sites = _find_import_sites(content, filepath)
        
        if not sites:
            continue
        
        for site in sites:
            report.imports_total += 1
            if _should_skip_migration(site):
                report.imports_skipped += 1
                continue
        
        # Filter migratable sites
        migratable = [s for s in sites if not _should_skip_migration(s)]
        
        if not migratable:
            continue
        
        if args.dry_run:
            for site in migratable:
                replacement, notes = _generate_migration(site)
                if not args.quiet:
                    print(f"[DRY] {filepath}:{site.line}")
                    print(f"      FROM: {site.source.strip()}")
                    print(f"      TO:   {replacement.split(chr(10))[0]}")
            continue
        
        # Apply migrations
        new_content, changes = _replace_in_source(content, migratable)
        
        if changes:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_content)
            
            report.files_changed += 1
            report.imports_migrated += len(migratable)
            
            if not args.quiet:
                for change in changes:
                    print(f"[MIGRATE] {change}")
    
    if args.report:
        print("\n=== MIGRATION REPORT ===")
        print(json.dumps(report.to_dict(), indent=2))
    
    if report.errors:
        print(f"\n{len(report.errors)} error(s):")
        for err in report.errors:
            print(f"  {err}")
        exit_code = 1
    
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
