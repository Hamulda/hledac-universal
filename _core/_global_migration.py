"""
_core._global_migration — Tools for migrating from global state to ModuleState.

This module provides utilities to:
1. Identify global state patterns across the codebase
2. Generate migration suggestions
3. Track migration progress

Usage:
    from _core._global_migration import (
        find_global_patterns,
        generate_migration_plan,
        report_global_usage,
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class GlobalPattern:
    """Represents a discovered global state pattern."""

    file_path: str
    line_number: int
    variable_name: str
    pattern_type: str
    raw_code: str

    def __hash__(self) -> int:
        return hash((self.file_path, self.line_number, self.variable_name))


@dataclass(slots=True)
class MigrationTarget:
    """A global variable that should be migrated to ModuleState."""

    pattern: GlobalPattern
    suggested_key: str
    migration_priority: int
    reason: str


@dataclass(slots=True)
class MigrationReport:
    """Report of global state patterns found."""

    patterns: list[GlobalPattern] = field(default_factory=list)
    by_type: dict[str, list[GlobalPattern]] = field(default_factory=dict)
    by_file: dict[str, list[GlobalPattern]] = field(default_factory=dict)

    @property
    def total_count(self) -> int:
        return len(self.patterns)

    def add_pattern(self, pattern: GlobalPattern) -> None:
        self.patterns.append(pattern)
        self.by_type.setdefault(pattern.pattern_type, []).append(pattern)
        self.by_file.setdefault(pattern.file_path, []).append(pattern)


_GLOBAL_VAR_PATTERNS = [
    ("(_[a-z]+_cache)\\s*:", "lazy_cache"),
    ("(_[a-z]+_index)\\s*:", "lazy_cache"),
    ("(_[a-z]+_instance)\\s*:", "singleton"),
    ("(_[a-z]+_singleton)\\s*:", "singleton"),
    ("(_[A-Z]+_AVAILABLE)\\s*=", "feature_flag"),
    ("(_[A-Z]+_CHECKED)\\s*=", "feature_flag"),
    ("(_[a-z]+_client)\\s*:", "resource"),
    ("(_[a-z]+_pool)\\s*:", "resource"),
    ("(_httpx_[a-z]+)\\s*:", "resource"),
    ("(_MLX_[A-Z]+)\\s*:", "resource"),
    ("(_[a-z]+_lock)\\s*=", "resource"),
]
_GLOBAL_DECL_RE = re.compile("^\\s*global\\s+(_[a-zA-Z_][a-zA-Z0-9_]*)")


def find_global_declarations(content: str) -> list[tuple[int, str]]:
    """Find all global variable declarations in a file."""
    results = []
    for i, line in enumerate(content.splitlines(), 1):
        match = _GLOBAL_DECL_RE.search(line)
        if match:
            results.append((i, match.group(1)))
    return results


def classify_global_var(name: str) -> str:
    """Classify a global variable by its name pattern."""
    name_lower = name.lower()
    name_upper = name.upper()
    if "cache" in name_lower or "index" in name_lower:
        return "lazy_cache"
    if "instance" in name_lower or "singleton" in name_lower:
        return "singleton"
    if name_upper.endswith("_AVAILABLE") or name_upper.endswith("_CHECKED"):
        return "feature_flag"
    if "client" in name_lower or "pool" in name_lower or "lock" in name_lower:
        return "resource"
    if name.startswith("_httpx") or name.startswith("_mlx"):
        return "resource"
    return "unknown"


def find_global_patterns_in_file(file_path: Path) -> list[GlobalPattern]:
    """Find all global state patterns in a single file."""
    patterns = []
    try:
        content = file_path.read_text()
    except UnicodeDecodeError, PermissionError, OSError:
        return patterns
    for line_num, var_name in find_global_declarations(content):
        lines = content.splitlines()
        raw_code = lines[line_num - 1] if line_num <= len(lines) else ""
        pattern = GlobalPattern(
            file_path=str(file_path),
            line_number=line_num,
            variable_name=var_name,
            pattern_type=classify_global_var(var_name),
            raw_code=raw_code.strip(),
        )
        patterns.append(pattern)
    return patterns


def find_global_patterns(
    root: Path, exclude_dirs: list[str] | None = None, exclude_patterns: list[str] | None = None
) -> MigrationReport:
    """
    Find all global state patterns in a directory tree.

    Args:
        root: Root directory to search
        exclude_dirs: Directories to exclude (e.g., [".venv", "__pycache__"])
        exclude_patterns: File patterns to exclude (e.g., ["*.bak", "test_*.py"])

    Returns:
        MigrationReport with all discovered patterns
    """
    exclude_dirs = exclude_dirs or [".venv", "__pycache__", ".git", ".archive"]
    exclude_patterns = exclude_patterns or ["*.bak", "*.py.bak"]
    report = MigrationReport()
    for path in root.rglob("*.py"):
        if any(ex in path.parts for ex in exclude_dirs):
            continue
        if any(path.match(pat) for pat in exclude_patterns):
            continue
        patterns = find_global_patterns_in_file(path)
        for pattern in patterns:
            report.add_pattern(pattern)
    return report


def suggest_migration_key(pattern: GlobalPattern) -> tuple[str, int, str]:
    """
    Suggest a migration key and priority for a pattern.

    Returns:
        Tuple of (key, priority, reason)
    """
    var = pattern.variable_name.lstrip("_")
    if pattern.pattern_type == "resource":
        return (f"resource.{var}", 1, "Heavy resource - should be managed")
    if pattern.pattern_type == "lazy_cache":
        return (f"cache.{var}", 2, "Lazy cache - memory leak risk")
    if pattern.pattern_type == "singleton":
        return (f"singleton.{var}", 3, "Singleton - could use DI instead")
    if pattern.pattern_type == "feature_flag":
        return (f"feature.{var}", 4, "Feature flag - low priority")
    return (f"misc.{var}", 5, "Unknown pattern type")


def generate_migration_plan(report: MigrationReport) -> list[MigrationTarget]:
    """Generate prioritized migration targets from a report."""
    targets = []
    for pattern in report.patterns:
        key, priority, reason = suggest_migration_key(pattern)
        target = MigrationTarget(pattern=pattern, suggested_key=key, migration_priority=priority, reason=reason)
        targets.append(target)
    targets.sort(key=lambda t: (t.migration_priority, t.pattern.file_path))
    return targets


def format_report(report: MigrationReport) -> str:
    """Format a migration report as a human-readable string."""
    lines = [
        "=" * 70,
        "GLOBAL STATE MIGRATION REPORT",
        "=" * 70,
        f"\nTotal patterns found: {report.total_count}",
        "\nBy type:",
    ]
    for ptype, patterns in sorted(report.by_type.items()):
        lines.append(f"  {ptype}: {len(patterns)}")
    lines.extend(["\nBy file (top 10):", ""])
    file_counts = sorted(report.by_file.items(), key=lambda x: len(x[1]), reverse=True)[:10]
    for fpath, patterns in file_counts:
        lines.append(f"  {len(patterns):3d}  {fpath}")
    return "\n".join(lines)


def format_migration_plan(targets: list[MigrationTarget]) -> str:
    """Format a migration plan as a human-readable string."""
    lines = [
        "=" * 70,
        "GLOBAL STATE MIGRATION PLAN",
        "=" * 70,
        f"\nTotal targets: {len(targets)}",
        "\nHigh priority (1-2):",
    ]
    for target in targets[:20]:
        if target.migration_priority <= 2:
            lines.append(f"\n  [{target.migration_priority}] {target.pattern.variable_name}")
            lines.append(f"      File: {target.pattern.file_path}:{target.pattern.line_number}")
            lines.append(f"      Key:  {target.suggested_key}")
            lines.append(f"      {target.reason}")
    return "\n".join(lines)


def report_global_usage(root: Path | str | None = None) -> MigrationReport:
    """
    Quick function to report all global state usage.

    Args:
        root: Root directory to search. Defaults to current directory.

    Returns:
        MigrationReport with all discovered patterns.
    """
    import os

    if root is None:
        root = Path(os.getcwd())
    elif isinstance(root, str):
        root = Path(root)
    return find_global_patterns(root)


if __name__ == "__main__":
    import sys

    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    print(f"Scanning: {root}")
    report = report_global_usage(root)
    print(format_report(report))
    print()
    targets = generate_migration_plan(report)
    print(format_migration_plan(targets))
