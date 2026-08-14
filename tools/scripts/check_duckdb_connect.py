#!/usr/bin/env python3
"""
CI Guard: Check for unauthorized duckdb.connect() usage in production code

ISSUE-04: This script detects duckdb.connect() calls outside authorized modules
in production code (excludes tests and scripts).

Production code MUST use:
  - duckdb_ro_acquire() / duckdb_ro_connection() for reads
  - DuckDBShadowStore.async_ingest_findings_batch() for canonical writes

Authorized modules for duckdb.connect():
  - knowledge/duckdb_store.py           (DuckDBShadowStore - canonical store)
  - knowledge/duckdb_wal_manager.py    (WAL manager)
  - knowledge/duckdb_base.py           (Base class)
  - core/duckdb_pool.py                (Canonical pool)
  - core/resource_pool.py              (Legacy pool, refactored)
  - core/resource_lifecycle.py         (Lifecycle management)
  - core/lazy_imports.py              (Import verification)
  - core/rust_backend/async_query.py   (Rust FFI fallback)
  - graph/quantum_pathfinder.py        (Graph operations)
  - runtime/cti/db/duckdb_domain_mv.py (DuckDB domain)
  - discovery/duckdb_fts_store.py     (FTS store)
  - evidence_log.py                    (Evidence logging)
  - export/parquet_writer.py           (Export operations)
  - knowledge/duckdb_cve_matrix.py    (CVE matrix)
  - knowledge/duckdb_migrator.py      (Migrations)
  - knowledge/hot_edges_cache.py      (Hot edges cache)
  - knowledge/link_prediction.py       (Link prediction)
  - brain/gnn_node_mapper.py          (GNN mapper)
  - brain/synthesis_runner.py          (Synthesis)
  - utils/optional_imports.py          (Import verification)
  - otel/_setup.py                    (OTEL setup)

Tests are EXCLUDED from this check (tests use raw connections for isolation).

Usage:
    python tools/scripts/check_duckdb_connect.py

Exit codes:
    0 = Clean (no violations)
    1 = Violations found

Sprint ISSUE-04 (2026-08-14)
"""

from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

# Authorized modules that may use duckdb.connect directly
AUTHORIZED_MODULES: frozenset[str] = frozenset([
    # Canonical store
    "knowledge/duckdb_store.py",
    "knowledge/duckdb_wal_manager.py",
    "knowledge/duckdb_base.py",
    # Canonical pool
    "core/duckdb_pool.py",
    # Legacy pools (to be refactored)
    "core/resource_pool.py",
    "core/resource_lifecycle.py",
    "core/lazy_imports.py",
    "core/rust_backend/async_query.py",
    # Domain-specific stores
    "graph/quantum_pathfinder.py",
    "runtime/cti/db/duckdb_domain_mv.py",
    "discovery/duckdb_fts_store.py",
    # Infrastructure
    "evidence_log.py",
    "export/parquet_writer.py",
    # Knowledge stores
    "knowledge/duckdb_cve_matrix.py",
    "knowledge/duckdb_migrator.py",
    "knowledge/hot_edges_cache.py",
    "knowledge/link_prediction.py",
    "knowledge/cve_data_loader.py",
    # Brain modules
    "brain/gnn_node_mapper.py",
    "brain/synthesis_runner.py",
    # Utilities
    "utils/optional_imports.py",
    "otel/_setup.py",
    # This script
    "tools/scripts/check_duckdb_connect.py",
])

# Pattern for detecting duckdb.connect( usage
DUCKDB_CONNECT_PATTERN = re.compile(r'duckdb\.connect\s*\(')

# Skip these directories and files
SKIP_PATTERNS: frozenset[str] = frozenset([
    ".git",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".tox",
    ".scratch",
    "benchmarks_shadow",
    # All test directories and files (use raw connections for isolation)
    "/tests/",
    "/test_",
    "test_",  # Prefix match for test files
])


def is_authorized(file_path: str) -> bool:
    """Check if file is in authorized module list."""
    normalized = file_path.replace("\\", "/")
    for auth in AUTHORIZED_MODULES:
        if auth in normalized:
            return True
    return False


def should_skip(file_path: str) -> bool:
    """Check if file should be skipped (tests, etc.)."""
    normalized = file_path.replace("\\", "/")
    parts = normalized.split("/")
    for skip in SKIP_PATTERNS:
        if skip in parts:
            return True
    return False


def find_violations(base_dir: str | None = None) -> list[tuple[str, list[str]]]:
    """
    Find all unauthorized duckdb.connect() usages.

    Args:
        base_dir: Directory to search (default: project root)

    Returns:
        List of (file_path, violations) tuples
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        base_dir = os.path.join(base_dir, "..", "..")

    violations = []

    # Find all Python files
    for pattern in ["**/*.py"]:
        for file_path in glob.glob(os.path.join(base_dir, pattern), recursive=True):
            if should_skip(file_path):
                continue
            if is_authorized(file_path):
                continue

            file_violations = check_file(file_path)
            if file_violations:
                violations.append((file_path, file_violations))

    return violations


def check_file(file_path: str) -> list[str]:
    """
    Check a single file for unauthorized duckdb.connect() usage.

    Args:
        file_path: Path to Python file

    Returns:
        List of violation descriptions (empty if clean)
    """
    violations = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return [f"Error reading file: {e}"]

    lines = content.split("\n")
    for i, line in enumerate(lines, 1):
        # Skip comments
        stripped = line.strip()
        if stripped.startswith("#"):
            continue

        # Skip strings (basic check)
        in_string = False
        for char in stripped:
            if char in ('"', "'"):
                in_string = not in_string

        if DUCKDB_CONNECT_PATTERN.search(line):
            # Check if it's in a comment
            code_part = line.split("#")[0]
            if DUCKDB_CONNECT_PATTERN.search(code_part):
                violations.append(f"  Line {i}: {line.strip()}")

    return violations


def run_check(base_dir: str | None = None) -> int:
    """
    Run the CI guard check.

    Args:
        base_dir: Directory to search

    Returns:
        Exit code (0 = clean, 1 = violations)
    """
    print("[ISSUE-04 CI Guard] Checking for unauthorized duckdb.connect() usage...")
    print()

    violations = find_violations(base_dir)

    if not violations:
        print("[PASS] No unauthorized duckdb.connect() usage found.")
        print()
        return 0

    print(f"[FAIL] Found {len(violations)} file(s) with unauthorized duckdb.connect():")
    print()

    for file_path, file_violations in violations:
        rel_path = os.path.relpath(file_path, os.getcwd())
        print(f"  {rel_path}:")
        for violation in file_violations:
            print(f"    {violation}")
        print()

    print("Authorized modules for duckdb.connect():")
    for mod in sorted(AUTHORIZED_MODULES):
        print(f"  - {mod}")
    print()
    print("All other code must use:")
    print("  - duckdb_ro_acquire() / duckdb_ro_connection() for reads")
    print("  - DuckDBShadowStore.async_ingest_findings_batch() for canonical writes")
    print()

    return 1


if __name__ == "__main__":
    sys.exit(run_check())
