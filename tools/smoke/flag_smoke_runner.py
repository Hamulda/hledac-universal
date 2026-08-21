"""
Per-flag smoke test runner (F4.3).

For each `HLEDAC_ENABLE_*` environment flag, this script:

1. Imports the orchestrator entry point with the flag set
2. Verifies the flag is *visible* (i.e. something in the orchestrator actually
   consults `os.environ["HLEDAC_ENABLE_X"]` or the equivalent in code)
3. Reports PASS / SILENT_NOOP / DEAD_FLAG / IMPORT_FAIL

This is NOT a full sprint — it only checks that toggling a flag changes
something observable, not that the feature works end-to-end (those are the
existing tests in tests/).

M1-safe: no MLX model load, no browser launch, no network I/O. Each flag test
takes <1s and <50MB RAM.

Phase 3: the probe target is the lightweight ``utils.flag_registry``
(stdlib-only) rather than the heavy ``coordinators.catalog`` chain
(DuckDB/MLX). This keeps the M1-safe invariant true AND aligns with the
canonical flag resolver established by F-FLAG-1/2.

Usage:
    uv run --project . python tools/flag_smoke_runner.py
    uv run --project . python tools/flag_smoke_runner.py --only HLEDAC_ENABLE_DSPY
    uv run --project . python tools/flag_smoke_runner.py --json
"""

import argparse
import json
import os
import re
import sys
import time
import tracemalloc
from dataclasses import field
from pathlib import Path

from compat.msgspec_gc_compat import Struct

_THIS = Path(__file__).resolve()
# ~/PycharmProjects/Hledac/ — parent of hledac/, needed for `hledac.*` imports.
_REPO_ROOT = _THIS.parent.parent.parent
# ~/PycharmProjects/Hledac/hledac/universal/ — needed for `utils.*` imports.
_UNIVERSAL_ROOT = _THIS.parent.parent

for _p in (str(_REPO_ROOT), str(_UNIVERSAL_ROOT)):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

# Stable project root (Hledac/hledac/universal/) — computed AFTER bootstrap
# so relative paths inside the runner resolve correctly.
PROJECT_ROOT = _UNIVERSAL_ROOT
SRC_ROOT = PROJECT_ROOT

# Heuristic: any flag of form HLEDAC_ENABLE_<NAME>. We use findall on the
# whole text, so MULTILINE is not needed — every occurrence is collected.
# Trailing `_` is explicitly excluded because Python identifiers like
# `def test_HLEDAC_ENABLE_DSPY_defaults_to_false():` would otherwise match.
_FLAG_PATTERN = re.compile(r"HLEDAC_ENABLE_[A-Z0-9_]+[A-Z0-9]")

# Skip pseudo-paths inside PROJECT_ROOT that don't contain real source.
_SKIP_PATH_FRAGMENTS = (
    "/_deprecated/",
    "/.venv/",
    "/build/",
    "/dist/",
    "/.git/",
    "/.mypy_cache/",
    "/.pytest_cache/",
    "/.ruff_cache/",
    "/node_modules/",
    "/.tox/",
    "/__pycache__/",
    "/site-packages/",
    "/.hledac/",
)


class FlagReport(Struct):
    """Per-flag result.

    ``slots=True`` keeps the report footprint bounded — each instance
    occupies a fixed C-level layout on M1 UMA (no per-instance ``__dict__``).
    """

    name: str
    status: str  # PASS | SILENT_NOOP | DEAD_FLAG | IMPORT_FAIL
    detail: str
    duration_s: float = 0.0
    peak_mem_mb: float = 0.0
    referenced_in: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "duration_s": round(self.duration_s, 3),
            "peak_mem_mb": round(self.peak_mem_mb, 2),
            "referenced_in": self.referenced_in[:5],
        }


def _discover_flags() -> list[str]:
    """Find HLEDAC_ENABLE_* names referenced in code (static scan)."""
    flags: set[str] = set()
    for py in PROJECT_ROOT.rglob("*.py"):
        pstr = str(py)
        if any(frag in pstr for frag in _SKIP_PATH_FRAGMENTS):
            continue
        if py.name == "flag_smoke_runner.py":
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _FLAG_PATTERN.findall(text):
            flags.add(match)
    return sorted(flags)


def _grep_references(flag: str) -> list[str]:
    """Find files that mention this flag."""
    hits: list[str] = []
    for py in PROJECT_ROOT.rglob("*.py"):
        pstr = str(py)
        if any(frag in pstr for frag in _SKIP_PATH_FRAGMENTS):
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if flag in text:
            hits.append(str(py.relative_to(PROJECT_ROOT)))
    return hits


def _check_flag(flag: str) -> FlagReport:
    """
    Verify a single flag. Strategy:
    1. Find code references via grep.
    2. Set the env var.
    3. Try a cheap probe (import a coordinator or read a config) — anything
       that fails fast if the flag's downstream code is broken.
    4. Report PASS if the flag is referenced AND something observable changed.
       SILENT_NOOP if the flag is referenced but the downstream has no
       observable side effect under our cheap probe.
       DEAD_FLAG if the flag is not referenced anywhere.
    """
    started = time.monotonic()
    tracemalloc.start()
    report = FlagReport(name=flag, status="UNKNOWN", detail="")
    report.referenced_in = _grep_references(flag)

    if not report.referenced_in:
        report.status = "DEAD_FLAG"
        report.detail = "no references in active source tree"
        report.peak_mem_mb = _stop_mem()
        report.duration_s = time.monotonic() - started
        return report

    os.environ[flag] = "1"
    try:
        from hledac.universal.utils.flag_registry import get_spec, is_flag_active

        spec = get_spec(flag)
        observed = is_flag_active(flag)
        if not observed:
            report.status = "SILENT_NOOP"
            report.detail = f"set in env ({os.environ.get(flag)!r}) but is_flag_active returned False"
        else:
            in_registry = " (in FLAG_REGISTRY)" if spec is not None else " (no spec)"
            report.status = "PASS"
            report.detail = f"flag visible in {len(report.referenced_in)} files{in_registry}"
    except Exception as exc:  # pragma: no cover — defensive
        report.status = "IMPORT_FAIL"
        report.detail = f"{type(exc).__name__}: {exc}"
    finally:
        os.environ.pop(flag, None)

    report.peak_mem_mb = _stop_mem()
    report.duration_s = time.monotonic() - started
    return report


def _stop_mem() -> float:
    """Return peak MB since tracemalloc.start(); stop tracking."""
    try:
        current, peak = tracemalloc.get_traced_memory()
    except Exception:
        return 0.0
    tracemalloc.stop()
    return peak / (1024 * 1024)


def _print_table(reports: list[FlagReport]) -> None:
    by_status: dict[str, int] = {}
    for r in reports:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    total = len(reports)
    print(f"Per-flag smoke runner — {total} flags scanned")
    print()
    for status, count in sorted(by_status.items()):
        print(f"  {status:14} {count:>4}")
    print()
    print(f"{'FLAG':38} {'STATUS':14} {'FILES':>6} {'PEAK_MB':>8}  DETAIL")
    print("-" * 100)
    for r in sorted(reports, key=lambda x: (x.status, x.name)):
        files = len(r.referenced_in)
        print(f"{r.name:38} {r.status:14} {files:>6} {r.peak_mem_mb:>7.1f}  {r.detail[:60]}")


def main(argv: list[str] | None = None) -> int:
    # ty: `__doc__` is `str | None` at module level. Fall back to empty
    # string so the ArgumentParser description is always a valid str.
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--only", help="Check a single flag name (e.g. HLEDAC_ENABLE_DSPY)")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    parser.add_argument(
        "--exit-on",
        choices=["PASS", "SILENT_NOOP", "DEAD_FLAG", "IMPORT_FAIL"],
        help="Exit non-zero if any flag matches this status (useful for CI)",
    )
    args = parser.parse_args(argv)

    flags = _discover_flags()
    if args.only:
        if args.only not in flags:
            print(f"flag {args.only!r} not found in source scan", file=sys.stderr)
            return 2
        flags = [args.only]

    reports = [_check_flag(f) for f in flags]

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2))
    else:
        _print_table(reports)

    if args.exit_on:
        for r in reports:
            if r.status == args.exit_on:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
