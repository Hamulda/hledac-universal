#!/usr/bin/env python3
"""
BLE001 Audit Tool — Issue D5



Replaces static per-file-ignores for BLE001 (bare except) in pyproject.toml
with a smart AST-based audit that allows:
  1. Exception tuples:  except (X, Y, Z):
  2. Logged exceptions:  except Exception as e: with logger.* within 5 lines
  3. Explicit suppressions: # noqa: BLE001 comment on the except line

Everything else is a violation and CI will fail.

Configuration: .ble-audit.toml
Baseline:   .ble-audit-baseline.json (auto-generated, auto-updated)
CI mode:   uv run python tools/ble_audit.py --ci
Update:    uv run python tools/ble_audit.py --update-baseline
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import NamedTuple
from _core import aclose

# TOML config — lazy import to avoid hard dependency
try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]


class Violation(NamedTuple):
    file: Path
    line: int
    kind: str  # "bare_except" | "broad_exception" | "exception_pass"
    code_snippet: str
    reason: str


class BLEAuditConfig(NamedTuple):
    exclude_dirs: tuple[str, ...]
    exclude_files: tuple[str, ...]
    allow_exception_tuples: bool
    allow_logged_exceptions: bool
    allow_noqa_comments: bool
    logger_lookahead_lines: int
    ci_threshold: int


DEFAULT_CONFIG = BLEAuditConfig(
    exclude_dirs=(
        "__pycache__", ".venv", ".venv-test", "probe_", ".claude", ".git",
        ".mypy_cache", ".pytest_cache", ".hypothesis", "archive", "stubs",
        "tests/.archive", "tests/probe_f", "tests/probe_p",
    ),
    exclude_files=(),
    allow_exception_tuples=True,
    allow_logged_exceptions=True,
    allow_noqa_comments=True,
    logger_lookahead_lines=5,
    ci_threshold=0,
    )


def load_config(config_path: Path) -> BLEAuditConfig:
    """Load configuration from .ble-audit.toml."""
    if not config_path.exists():
        return DEFAULT_CONFIG

    with config_path.open("rb") as f:
        data = tomllib.load(f)

    cfg = data.get("ble-audit", {})
    return BLEAuditConfig(
        exclude_dirs=tuple(cfg.get("exclude_dirs", list(DEFAULT_CONFIG.exclude_dirs))),
        exclude_files=tuple(cfg.get("exclude_files", list(DEFAULT_CONFIG.exclude_files))),
        allow_exception_tuples=cfg.get("allow_exception_tuples", DEFAULT_CONFIG.allow_exception_tuples),
        allow_logged_exceptions=cfg.get("allow_logged_exceptions", DEFAULT_CONFIG.allow_logged_exceptions),
        allow_noqa_comments=cfg.get("allow_noqa_comments", DEFAULT_CONFIG.allow_noqa_comments),
        logger_lookahead_lines=cfg.get("logger_lookahead_lines", DEFAULT_CONFIG.logger_lookahead_lines),
        ci_threshold=cfg.get("ci_threshold", DEFAULT_CONFIG.ci_threshold),
    )


def is_excluded(path: Path, config: BLEAuditConfig) -> bool:
    """Check if a path should be excluded from auditing."""
    s = str(path)
    # Check file-level exclusions
    for excl in config.exclude_files:
        if excl in s:
            return True
    # Check directory exclusions — works for both relative and absolute paths
    for exdir in config.exclude_dirs:
        if (f"/{exdir}/" in s or s.endswith(f"/{exdir}") or
                s.startswith(f"{exdir}/") or f"/{exdir}/" in s.replace("\\", "/")):
            return True
    return False


def get_source_lines(path: Path) -> list[str]:
    """Read source file lines for context analysis."""
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return []


def has_noqa_comment(source_lines: list[str], lineno: int, config: BLEAuditConfig) -> bool:
    """Check if the except line has a # noqa: BLE001 comment."""
    if not config.allow_noqa_comments:
        return False
    if lineno <= 0 or lineno > len(source_lines):
        return False
    line = source_lines[lineno - 1]
    return "# noqa: BLE001" in line


def has_logger_in_block(
    source_lines: list[str], except_lineno: int, config: BLEAuditConfig
) -> bool:
    """Check if there's a logger.* call within N lines after the except handler."""
    if not config.allow_logged_exceptions:
        return False

    # Check lines AFTER the except handler (not the handler line itself)
    start = except_lineno + 1
    end = min(except_lineno + 1 + config.logger_lookahead_lines, len(source_lines) + 1)

    for i in range(start, end):
        line = source_lines[i - 1] if i > 0 else ""
        stripped = line.strip()
        # Skip blank lines and comments
        if not stripped or stripped.startswith("#"):
            continue
        if "logger." in line and any(
            lvl in line for lvl in ("warning", "error", "debug", "info", "exception")
        ):
            return True
        # Also check for _logger (private loggers)
        if "_logger." in line and any(
            lvl in line for lvl in ("warning", "error", "debug", "info", "exception")
        ):
            return True
    return False


def is_exception_tuple(node: ast.ExceptHandler) -> bool:
    """Check if handler uses a tuple of specific exceptions: except (X, Y):"""
    if node.type is None:
        return False
    return isinstance(node.type, ast.Tuple)


def is_bare_exception(node: ast.ExceptHandler) -> bool:
    """Check if this is a bare except: (no type specified)."""
    return node.type is None


def is_broad_exception(node: ast.ExceptHandler) -> bool:
    """Check if this is except Exception: (not a specific exception tuple)."""
    if node.type is None:
        return False
    if isinstance(node.type, ast.Tuple):
        return False  # Tuple of specific exceptions - allowed
    if isinstance(node.type, ast.Name) and node.type.id == "Exception":
        return True
    return False


def audit_file(path: Path, config: BLEAuditConfig) -> list[Violation]:
    """Audit a single Python file for BLE001 violations."""
    violations = []

    try:
        source = path.read_text(encoding="utf-8")
        source_lines = source.splitlines(keepends=True)
        tree = ast.parse(source)
    except (SyntaxError, OSError):
        return []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            lineno = handler.lineno or 0

            # Check noqa first
            if has_noqa_comment(source_lines, lineno, config):
                continue

            # Bare except: - always a violation
            if is_bare_exception(handler):
                violations.append(Violation(
                    path, lineno, "bare_except",
                    source_lines[lineno - 1].strip() if lineno > 0 else "",
                    "bare except clause — must specify exception type(s)"
                ))
                continue

            # Exception tuple (X, Y): - allowed
            if is_exception_tuple(handler) and config.allow_exception_tuples:
                continue

            # except Exception: with logger in block - allowed
            if is_broad_exception(handler) and config.allow_logged_exceptions:
                if has_logger_in_block(source_lines, lineno, config):
                    continue
                # except Exception: pass - violation
                if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                    violations.append(Violation(
                        path, lineno, "exception_pass",
                        source_lines[lineno - 1].strip() if lineno > 0 else "",
                        "except Exception: pass — add logging or use specific exception"
                    ))
                    continue
                # except Exception: without logger - violation
                violations.append(Violation(
                    path, lineno, "broad_exception",
                    source_lines[lineno - 1].strip() if lineno > 0 else "",
                    "except Exception: without logging — use specific exception or add logger.*"
                ))
                continue

            # Specific exception (single, not Exception base class) - allowed
            # (anything not caught above is a specific exception)

    return violations


def audit_tree(root: Path, config: BLEAuditConfig) -> tuple[list[Violation], int]:
    """Audit all Python files in a directory tree."""
    all_violations = []
    files_audited = 0

    for py_file in root.rglob("*.py"):
        if is_excluded(py_file, config):
            continue
        violations = audit_file(py_file, config)
        if violations:
            all_violations.extend(violations)
        files_audited += 1

    return all_violations, files_audited


def format_violation(v: Violation) -> str:
    """Format a violation for display."""
    return f"  {v.file}:{v.line}: BLE001 {v.kind}: {v.reason}\n    {v.code_snippet}"


def load_baseline(baseline_path: Path) -> set[tuple[str, int]]:
    """Load baseline violations as a set of (relative_path, line) tuples."""
    if not baseline_path.exists():
        return set()
    with baseline_path.open() as f:
        data = json.load(f)
    return {(v["file"], v["line"]) for v in data}


def save_baseline(violations: list[Violation], root: Path, baseline_path: Path) -> None:
    """Save current violations as new baseline."""
    baseline = [
        {
            "file": str(v.file.relative_to(root)),
            "line": v.line,
            "kind": v.kind,
            "snippet": v.code_snippet,
        }
        for v in violations
    ]
    baseline_path.write_text(json.dumps(baseline, indent=2))
    print(f"Baseline updated: {len(baseline)} violations -> {baseline_path}")


def main() -> int:
    """Main entry point for the BLE audit tool."""
    import argparse

    parser = argparse.ArgumentParser(description="BLE001 Audit Tool — Issue D5")
    parser.add_argument(
        "--ci", action="store_true",
        help="CI mode: compare against baseline, fail on NEW violations"
    )
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="Update the baseline file with current violations"
    )
    parser.add_argument(
        "--generate-baseline", action="store_true",
        help="Generate baseline if it doesn't exist"
    )
    parser.add_argument(
        "root", nargs="?", default=".",
        help="Root directory to audit (default: .)"
    )
    args = parser.parse_args()

    root = Path(args.root)
    config_path = root / ".ble-audit.toml"
    baseline_path = root / ".ble-audit-baseline.json"

    config = load_config(config_path)

    print(f"BLE Audit (Issue D5)")
    print(f"  Config: {config_path if config_path.exists() else 'default'}")
    print(f"  Root: {root}")
    print(f"  CI threshold: {config.ci_threshold}")
    print()

    violations, files_audited = audit_tree(root, config)
    print(f"Audited {files_audited} files, found {len(violations)} violations")

    if not violations:
        print("\n✓ No BLE001 violations found")
        if args.update_baseline:
            save_baseline([], root, baseline_path)
        return 0

    # --update-baseline: save current state as new baseline
    if args.update_baseline:
        save_baseline(violations, root, baseline_path)
        return 0

    # Load baseline for CI comparison
    baseline = load_baseline(baseline_path)
    if baseline and not args.generate_baseline:
        current = {(str(v.file.relative_to(root)), v.line) for v in violations}
        new_violations = current - baseline
        if new_violations:
            print(f"\n❌ CI FAIL: {len(new_violations)} NEW violations (not in baseline)")
            print("  Run with --update-baseline to accept current state as new baseline")
            # Show new violations
            new_viols = [v for v in violations if (str(v.file.relative_to(root)), v.line) in new_violations]
            by_file: dict[str, list[Violation]] = {}
            for v in new_viols:
                key = str(v.file)
                if key not in by_file:
                    by_file[key] = []
                by_file[key].append(v)
            for fpath, viols in sorted(by_file.items()):
                print(f"\n  {fpath} ({len(viols)} new):")
                for v in viols:
                    print(f"    {v.file}:{v.line}: {v.kind}")
            return 1
        else:
            print(f"\n✓ CI PASS: 0 new violations (baseline: {len(baseline)})")
            return 0

    # Default: show all violations
    by_file: dict[str, list[Violation]] = {}
    for v in violations:
        key = str(v.file)
        if key not in by_file:
            by_file[key] = []
        by_file[key].append(v)

    print(f"\nViolations by file:")
    for fpath, viols in sorted(by_file.items()):
        print(f"\n{fpath} ({len(viols)} violations):")
        for v in viols:
            print(format_violation(v))

    # CI gate (simple threshold)
    if len(violations) > config.ci_threshold:
        print(f"\n❌ CI FAIL: {len(violations)} violations (threshold: {config.ci_threshold})")
        return 1
    else:
        print(f"\n✓ CI PASS: {len(violations)} violations (threshold: {config.ci_threshold})")
        return 0


if __name__ == "__main__":
    sys.exit(main())
