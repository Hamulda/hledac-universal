#!/usr/bin/env python3
"""
Migrate Test Files to Spec-Based Mocks — Issue 1.2

Usage:
    # Preview changes (dry-run)
    python tools/migrate_test_mocks.py --dry-run tests/test_storage_router.py

    # Apply changes
    python tools/migrate_test_mocks.py tests/test_storage_router.py

    # Apply to all mock-heavy files
    python tools/migrate_test_mocks.py --all

    python tools/migrate_test_mocks.py --status

Patterns this handles:
    MagicMock()                    → MagicMock(spec=ClassName)
    AsyncMock()                    → AsyncMock()
    mock_*.sample_uma_status      → spec=ResourceGovernor
    _duckdb_store.async_ingest    → spec=DuckDBStoreProtocol
    mock_backend.get/put/delete   → spec=StorageRouter
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

MOCK_SPEC_MAP: dict[str, tuple[str, str]] = {
    # NOTE: mock_governor intentionally NO spec= because M1ResourceGovernor has no Protocol
    # Use make_governor_mock() helper from tests.utils.spec_mocks instead
    # NOTE: mock_lmdb uses put/delete but LMDBStoreProtocol only has put_many/get
    # Use make_lmdb_mock() helper instead
    "_duckdb_store": (
        "from hledac.universal._core.protocols import DuckDBStoreProtocol",
        "DuckDBStoreProtocol",
    ),
    "duckdb_store": (
        "from hledac.universal._core.protocols import DuckDBStoreProtocol",
        "DuckDBStoreProtocol",
    ),
    "store": (
        "from hledac.universal._core.protocols import DuckDBStoreProtocol",
        "DuckDBStoreProtocol",
    ),
}

# Variable prefix → spec class for storage mocks
# Note: LMDBStoreProtocol only has put_many/get, NOT put/delete
STORAGE_MOCK_PATTERNS = [
    "mock_backend",
    "mock_cold",
    "mock_hot",
    "mock_warm",
    "hot_backend",
    "cold_backend",
    "warm_backend",
]


class MigrationResult(NamedTuple):
    file: Path
    changes: int
    spec_added: list[str]
    imports_added: list[str]


def find_mock_creations(content: str) -> list[dict]:
    """Find all MagicMock() and AsyncMock() creations in content."""
    results = []

    # Find MagicMock() creations
    for match in re.finditer(r"(\w+)\s*=\s*MagicMock\s*\(\s*\)", content):
        var_name = match.group(1)
        line_no = content[: match.start()].count("\n") + 1
        results.append(
            {
                "type": "MagicMock",
                "var": var_name,
                "line": line_no,
                "pos": match.start(),
                "end": match.end(),
            }
        )

    # Find AsyncMock() creations
    for match in re.finditer(r"(\w+)\s*=\s*AsyncMock\s*\(\s*\)", content):
        var_name = match.group(1)
        line_no = content[: match.start()].count("\n") + 1
        results.append(
            {
                "type": "AsyncMock",
                "var": var_name,
                "line": line_no,
                "pos": match.start(),
                "end": match.end(),
            }
        )

    return results


def find_spec_class_for_var(var_name: str) -> str | None:
    """Find the spec class for a given variable name."""
    for prefix, (_, spec_class) in MOCK_SPEC_MAP.items():
        if var_name.startswith(prefix):
            return spec_class
    return None


def add_spec_to_mock(line: str, var_name: str, spec_class: str) -> str:
    """Add spec= to a MagicMock() call."""
    # Handle MagicMock()
    if "MagicMock()" in line:
        return line.replace("MagicMock()", f"MagicMock(spec={spec_class})")
    # Handle AsyncMock()
    if "AsyncMock()" in line:
        return line.replace("AsyncMock()", f"AsyncMock(spec={spec_class})")
    return line


def get_import_for_spec(spec_class: str) -> str:
    """Get the import statement for a spec class."""
    for _prefix, (import_stmt, spec) in MOCK_SPEC_MAP.items():
        if spec == spec_class:
            return import_stmt
    return None


def migrate_file(path: Path, dry_run: bool = False) -> MigrationResult:
    """Migrate a single test file to spec-based mocks."""
    content = path.read_text()
    original = content

    changes: list[tuple[int, str, str]] = []  # (line_no, old, new)
    specs_added: list[str] = []
    imports_needed: set[str] = set()

    # Find all mock creations
    mock_creations = find_mock_creations(content)

    for mock in mock_creations:
        spec_class = find_spec_class_for_var(mock["var"])
        if spec_class:
            import_stmt = get_import_for_spec(spec_class)
            if import_stmt:
                imports_needed.add(import_stmt)

            # Find the line and add spec=
            lines = content.split("\n")
            line_idx = mock["line"] - 1
            old_line = lines[line_idx]
            new_line = add_spec_to_mock(old_line, mock["var"], spec_class)

            if new_line != old_line:
                changes.append((mock["line"], old_line, new_line))
                specs_added.append(f"{mock['var']} → spec={spec_class}")

    # Apply changes
    if changes:
        lines = content.split("\n")
        # Apply in reverse order to preserve line numbers
        for line_no, old_line, new_line in reversed(changes):
            lines[line_no - 1] = new_line
        content = "\n".join(lines)

    # Add necessary imports (if not already present)
    if imports_needed:
        import_lines = []
        for imp in sorted(imports_needed):
            if imp not in content:
                import_lines.append(imp)

        if import_lines:
            # Find the best place to add imports (after existing imports)
            import_section_end = 0
            for i, line in enumerate(lines[:50]):
                if line.startswith("from ") or line.startswith("import "):
                    import_section_end = i + 1

            # Add after existing imports
            if import_section_end > 0:
                new_imports = "\n".join(import_lines)
                lines = content.split("\n")
                lines.insert(import_section_end, new_imports)
                content = "\n".join(lines)

    if not dry_run and content != original:
        path.write_text(content)

    return MigrationResult(
        file=path,
        changes=len(changes),
        spec_added=specs_added,
        imports_added=sorted(imports_needed),
    )


def status_report() -> None:
    """Print migration status for all test files."""
    test_files = []
    for root, dirs, files in os.walk("tests"):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ["__pycache__", "archive"]]
        for f in files:
            if f.startswith("test_") and f.endswith(".py"):
                test_files.append(Path(root) / f)

    print("Mock Migration Status Report")
    print("=" * 70)

    with_spec = 0
    without_spec = 0
    mock_counts = []

    for tf in sorted(test_files):
        try:
            content = tf.read_text()
            mocks = find_mock_creations(content)
            specs = content.count("spec=")

            total = len(mocks)
            if total > 0:
                no_spec = total - specs
                if specs > 0:
                    with_spec += 1
                else:
                    without_spec += 1
                mock_counts.append((tf, total, specs, no_spec))
        except Exception:  # noqa: BLE001
            pass

    print(f"\nFiles with mocks: {len(mock_counts)}")
    print(f"Files with spec=: {with_spec}")
    print(f"Files without spec=: {without_spec}")

    print(f"\n{'File':<50} {'Total':>6} {'Spec':>5} {'NoSpec':>7}")
    print("-" * 70)

    for tf, total, specs, no_spec in sorted(mock_counts, key=lambda x: -x[3])[:20]:
        flag = " ⚠" if no_spec > 10 else ""
        print(f"{str(tf):<50} {total:>6} {specs:>5} {no_spec:>7}{flag}")

    if len(mock_counts) > 20:
        print(f"... and {len(mock_counts) - 20} more files")


if __name__ == "__main__":
    import os

    parser = argparse.ArgumentParser(description="Migrate test mocks to spec-based")
    parser.add_argument("files", nargs="*", help="Test files to migrate")
    parser.add_argument("--dry-run", "-n", action="store_true", help="Preview changes")
    parser.add_argument("--all", "-a", action="store_true", help="Migrate all test files")
    parser.add_argument("--status", "-s", action="store_true", help="Show migration status")

    args = parser.parse_args()

    if args.status:
        status_report()
        sys.exit(0)

    files = args.files
    if args.all:
        test_files = []
        for root, dirs, filenames in os.walk("tests"):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ["__pycache__", "archive"]]
            for f in filenames:
                if f.startswith("test_") and f.endswith(".py"):
                    test_files.append(Path(root) / f)
        files = [str(f) for f in test_files]

    if not files:
        print("No files specified. Use --status to see current state.")
        print("Or specify files: tools/migrate_test_mocks.py tests/test_storage_router.py")
        sys.exit(1)

    total_changes = 0
    for file_path in files:
        path = Path(file_path)
        if not path.exists():
            print(f"Skipping (not found): {file_path}")
            continue

        result = migrate_file(path, dry_run=args.dry_run)

        if result.changes > 0:
            status = "DRY-RUN" if args.dry_run else "MIGRATED"
            print(f"[{status}] {result.file}: {result.changes} mock(s) updated")
            for spec in result.spec_added:
                print(f"  + {spec}")
            total_changes += result.changes
        else:
            print(f"[SKIP] {result.file}: no changes needed")

    print(
        f"\n{'Total changes: ' + str(total_changes) if args.dry_run else 'Done: ' + str(total_changes) + ' mock(s) updated'}"
    )
