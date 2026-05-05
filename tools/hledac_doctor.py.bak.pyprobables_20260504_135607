#!/usr/bin/env python3
"""
hledac_doctor.py — Dependency availability checker
==================================================

Sprint F207N-B: Build Truth

Purpose:
  Import-availability checks only. No network, no model loading.
  Outputs JSON or Markdown report labeled by extra.
  Mirrors the existing platform_info.py probe pattern.

Usage:
  python tools/hledac_doctor.py                # default markdown table
  python tools/hledac_doctor.py --json         # JSON output
  python tools/hledac_doctor.py --extra light  # show only light deps
  python tools/hledac_doctor.py --verbose      # include version strings

Exit codes:
  0  — always (availability check is non-failing)
  1  — only on invalid argument
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class DepCategory(Enum):
    """Mirror of platform_info.DepCategory for self-contained diagnostics."""
    BASELINE_REQUIRED = "baseline_required"
    OPTIONAL_AVAILABLE = "optional_available"
    OPTIONAL_MISSING = "optional_missing"
    PLATFORM_GUARDED = "platform_guarded"


class OutputFormat(Enum):
    MARKDOWN = "markdown"
    JSON = "json"


@dataclass
class DepStatus:
    """Status for a single dependency."""
    name: str
    import_name: str
    available: bool
    category: str
    version: Optional[str]
    install_hint: Optional[str]
    extra: Optional[str]


@dataclass
class DoctorReport:
    """Full doctor report."""
    python_version: str
    platform: str
    statuses: List[DepStatus]
    missing_by_extra: Dict[str, List[str]]


# ---------------------------------------------------------------------------
# Dependency registry
# ---------------------------------------------------------------------------

# (import_name, pip_specifier, extra_group, is_baseline_required)
# Listed in dependency order (most foundational first).
DEPENDENCY_REGISTRY: List[Dict] = [
    # --- baseline ---
    {"name": "aiosqlite",    "import": "aiosqlite",         "spec": "aiosqlite>=0.19.0",      "extra": None,         "baseline": True},
    {"name": "aiohttp",       "import": "aiohttp",           "spec": "aiohttp>=3.9.0",          "extra": None,         "baseline": True},
    {"name": "aiohttp-socks", "import": "aiohttp_socks",     "spec": "aiohttp-socks>=0.8.0",    "extra": None,         "baseline": True},
    {"name": "httpx",         "import": "httpx",             "spec": "httpx>=0.27.0",          "extra": None,         "baseline": True},
    {"name": "lancedb",       "import": "lancedb",           "spec": "lancedb>=0.2.5",         "extra": None,         "baseline": True},
    {"name": "duckdb",        "import": "duckdb",            "spec": "duckdb>=1.2.0",          "extra": None,         "baseline": True},
    {"name": "orjson",        "import": "orjson",            "spec": "orjson>=3.9.0",           "extra": None,         "baseline": True},
    {"name": "camoufox",      "import": "camoufox",          "spec": "camoufox[geoip]>=1.0.0",  "extra": None,         "baseline": True},
    {"name": "duckduckgo-search","import": "duckduckgo_search", "spec": "duckduckgo-search>=8.0.0", "extra": None, "baseline": True},
    {"name": "beautifulsoup4","import": "bs4",               "spec": "beautifulsoup4>=4.12.0", "extra": None,         "baseline": True},
    {"name": "pytesseract",   "import": "pytesseract",      "spec": "pytesseract>=0.3.10",    "extra": None,         "baseline": True},
    {"name": "dnspython",     "import": "dns",               "spec": "dnspython>=2.4.0",        "extra": None,         "baseline": True},
    {"name": "stem",          "import": "stem",              "spec": "stem>=1.8.0",             "extra": None,         "baseline": True},
    {"name": "aiobtcdht",     "import": "aiobtcdht",         "spec": "aiobtcdht>=0.1.0",        "extra": None,         "baseline": True},
    {"name": "pydantic",      "import": "pydantic",          "spec": "pydantic>=2.0.0",        "extra": None,         "baseline": True},
    {"name": "pyprobables",   "import": "probables",         "spec": "pyprobables>=4.0.0",      "extra": None,         "baseline": True},
    {"name": "pyzipper",      "import": "pyzipper",          "spec": "pyzipper>=1.4.0",        "extra": None,         "baseline": True},
    {"name": "psutil",        "import": "psutil",            "spec": "psutil>=5.9.0",          "extra": None,         "baseline": True},
    # --- light ---
    {"name": "fast-langdetect","import": "fast_langdetect",  "spec": "fast-langdetect>=1.0.0", "extra": "light",      "baseline": False},
    {"name": "datasketch",     "import": "datasketch",        "spec": "datasketch>=1.6.0",      "extra": "light",      "baseline": False},
    # --- apple-accel ---
    {"name": "mlx",           "import": "mlx.core",          "spec": "mlx>=0.16.0",            "extra": "apple-accel", "baseline": False},
    {"name": "uvloop",        "import": "uvloop",             "spec": "uvloop>=0.21.0",         "extra": "apple-accel", "baseline": False},
    # --- osint-html ---
    {"name": "selectolax",    "import": "selectolax",        "spec": "selectolax>=0.3.21",     "extra": "osint-html",  "baseline": False},
    {"name": "xxhash",        "import": "xxhash",             "spec": "xxhash>=3.4.0",          "extra": "osint-html",  "baseline": False},
    {"name": "curl_cffi",     "import": "curl_cffi",          "spec": "curl_cffi>=0.7.0",       "extra": "osint-html",  "baseline": False},
    {"name": "h2",            "import": "h2",                 "spec": "h2>=4.1.0",             "extra": "osint-html",  "baseline": False},
    # --- graph-storage ---
    {"name": "pyarrow",      "import": "pyarrow",            "spec": "pyarrow>=16.0.0",        "extra": "graph-storage","baseline": False},
    {"name": "polars",        "import": "polars",             "spec": "polars>=1.0.0",          "extra": "graph-storage","baseline": False},
    # --- torch ---
    {"name": "torch",         "import": "torch",              "spec": "torch>=2.1.0",          "extra": "torch",       "baseline": False},
    {"name": "torchvision",   "import": "torchvision",        "spec": "torchvision>=0.16.0",   "extra": "torch",       "baseline": False},
    # --- dev ---
    {"name": "pytest",        "import": "pytest",             "spec": "pytest>=8.0.0",         "extra": "dev",         "baseline": False},
    {"name": "ruff",          "import": "ruff",               "spec": "ruff>=0.1.0",           "extra": "dev",         "baseline": False},
    {"name": "mypy",          "import": "mypy",               "spec": "mypy>=1.9.0",           "extra": "dev",         "baseline": False},
]

EXTRA_GROUPS = {
    "light": "light",
    "apple-accel": "apple-accel",
    "osint-html": "osint-html",
    "graph-storage": "graph-storage",
    "torch": "torch",
    "dev": "dev",
}


# ---------------------------------------------------------------------------
# Probe logic
# ---------------------------------------------------------------------------

def probe_import(dep: Dict) -> DepStatus:
    """Probe a single dependency's import availability."""
    import_name = dep["import"]
    version = None
    available = False
    category = "optional_missing"
    install_hint = dep["spec"]

    try:
        mod = importlib.import_module(import_name)
        available = True
        category = "optional_available"
        # Try to get version
        version = getattr(mod, "__version__", None) or getattr(mod, "version", None)
        if version is None and hasattr(mod, "__version__"):
            version = getattr(mod, "__version__")
        install_hint = None
    except ImportError:
        available = False
        # Check if it's a platform-guarded extra
        if dep["extra"] in ("apple-accel",):
            # mlx/uvloop are platform-specific — not missing, just not applicable
            category = "platform_guarded"
        else:
            category = "optional_missing"

    return DepStatus(
        name=dep["name"],
        import_name=import_name,
        available=available,
        category=category,
        version=version,
        install_hint=install_hint,
        extra=dep["extra"],
    )


def run_diagnostics(requested_extra: Optional[str] = None) -> DoctorReport:
    """
    Run diagnostics on all (or filtered) dependencies.

    Args:
        requested_extra: If set, only probe deps in that extra group.

    Returns:
        DoctorReport with per-dep statuses and missing-by-extra grouping.
    """
    python_v = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    platform = sys.platform

    statuses: List[DepStatus] = []
    for dep in DEPENDENCY_REGISTRY:
        if requested_extra and dep["extra"] != requested_extra and dep["extra"] is not None:
            continue
        statuses.append(probe_import(dep))

    # Build missing-by-extra
    missing_by_extra: Dict[str, List[str]] = {}
    for dep in DEPENDENCY_REGISTRY:
        if requested_extra and dep["extra"] != requested_extra:
            continue
        if dep["extra"] is not None:
            status = next((s for s in statuses if s.name == dep["name"]), None)
            if status and not status.available:
                missing_by_extra.setdefault(dep["extra"], []).append(dep["name"])

    return DoctorReport(
        python_version=python_v,
        platform=platform,
        statuses=statuses,
        missing_by_extra=missing_by_extra,
    )


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------

def format_markdown(report: DoctorReport, verbose: bool = False) -> str:
    """Format report as Markdown."""
    lines = [
        "# Hledac Doctor — Dependency Availability",
        "",
        f"- **Python**: `{report.python_version}`",
        f"- **Platform**: `{report.platform}`",
        "",
    ]

    if report.missing_by_extra:
        lines.append("## Missing by Extra")
        for extra, pkgs in sorted(report.missing_by_extra.items()):
            lines.append(f"### `{extra}`")
            for pkg in sorted(pkgs):
                dep = next(d for d in DEPENDENCY_REGISTRY if d["name"] == pkg)
                lines.append(f"- `{dep['spec']}`")
            lines.append("")
    else:
        lines.append("**All dependencies available.**")

    lines.append("## Full Status Table")
    lines.append("")
    if verbose:
        lines.append("| Package | Import | Available | Category | Version | Install Hint |")
        lines.append("|---------|--------|-----------|----------|---------|--------------|")
        for s in report.statuses:
            avail = "✅" if s.available else "❌"
            version_str = s.version or "—"
            hint_str = s.install_hint or "—"
            lines.append(f"| `{s.name}` | `{s.import_name}` | {avail} | {s.category} | `{version_str}` | `{hint_str}` |")
    else:
        lines.append("| Package | Available | Category |")
        lines.append("|---------|-----------|----------|")
        for s in report.statuses:
            avail = "✅" if s.available else "❌"
            lines.append(f"| `{s.name}` | {avail} | {s.category} |")

    return "\n".join(lines)


def format_json(report: DoctorReport, verbose: bool = False) -> str:
    """Format report as JSON."""
    payload = {
        "python_version": report.python_version,
        "platform": report.platform,
        "missing_by_extra": report.missing_by_extra,
        "statuses": [asdict(s) for s in report.statuses] if verbose else [
            {"name": s.name, "available": s.available, "category": s.category}
            for s in report.statuses
        ],
    }
    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hledac_doctor.py",
        description="Check Hledac dependency availability. No network, no model load.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of Markdown")
    parser.add_argument("--extra", metavar="EXTRA", choices=sorted(EXTRA_GROUPS.keys()),
                        help="Only check deps for this extra group")
    parser.add_argument("--verbose", "-v", action="store_true", help="Include versions and install hints")
    parser.add_argument("--output", "-o", metavar="FILE", help="Write output to FILE (default: stdout)")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    report = run_diagnostics(requested_extra=args.extra)

    if args.json:
        output = format_json(report, verbose=args.verbose)
    else:
        output = format_markdown(report, verbose=args.verbose)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output + "\n")
        print(f"Doctor report written to {args.output}", file=sys.stderr)
    else:
        print(output)

    return 0


if __name__ == "__main__":
    sys.exit(main())