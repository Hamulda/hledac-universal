"""
Live Memory Preflight Wrapper — Sprint F207N-E
==============================================

In-boundary wrapper for memory preflight checks.

Verdicts:
    READY_FOR_ACTIVE300   — memory state is ok/warn with no active swap
    CLOSE_APPS_OR_RESTART — memory state is warn AND swap is active
    CRITICAL_DO_NOT_RUN    — memory state is critical or emergency

Constraints (F207N-E boundary):
    - NO live sprint execution
    - NO network I/O
    - NO process killing or sudo
    - NO external file access beyond this module's own read-only imports

In-boundary imports used:
    - core.resource_governor.sample_uma_status  — UMA snapshot
    - utils.uma_budget.get_uma_snapshot          — detailed memory metrics

Usage:
    python -m tools.live_memory_preflight          # JSON to stdout
    python -m tools.live_memory_preflight --md    # Markdown to stdout
    python -m tools.live_memory_preflight --json   # explicit JSON (default)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import Optional

# In-boundary only — no external network, no process kill, no sudo
try:
    from core.resource_governor import sample_uma_status
except Exception:
    # Fail-open: treat as unknown state
    def sample_uma_status():
        class _DummyStatus:
            system_used_gib = 0.0
            system_available_gib = 0.0
            swap_used_gib = 0.0
            state = "ok"
            io_only = False
            swap_detected = False
            rss_gib = 0.0
            metal_cache_limit_bytes = None
            metal_wired_limit_bytes = None
            last_error = "sample_uma_status unavailable"
        return _DummyStatus()

try:
    from utils.uma_budget import (
        get_uma_snapshot,
        UMA_WARN_GIB,
        UMA_CRITICAL_GIB,
        UMA_EMERGENCY_GIB,
    )
except Exception:
    # Fail-open defaults
    UMA_WARN_GIB = 6.0
    UMA_CRITICAL_GIB = 6.5
    UMA_EMERGENCY_GIB = 7.0

    def get_uma_snapshot():
        return {
            "uma_total_mb": 8192,
            "system_used_mb": 0,
            "system_available_mb": 0,
            "mlx_active_mb": 0,
            "uma_pressure_level": "ok",
            "is_warn": False,
            "is_critical": False,
            "is_emergency": False,
            "uma_usage_pct": 0,
        }


# =============================================================================
# Verdict constants
# =============================================================================

VERDICT_READY_FOR_ACTIVE300 = "READY_FOR_ACTIVE300"
VERDICT_CLOSE_APPS_OR_RESTART = "CLOSE_APPS_OR_RESTART"
VERDICT_CRITICAL_DO_NOT_RUN = "CRITICAL_DO_NOT_RUN"


def _derive_verdict(state: str, swap_detected: bool) -> str:
    """
    Derive preflight verdict from UMA state and swap signal.

    Decision matrix:
        emergency          → CRITICAL_DO_NOT_RUN
        critical           → CRITICAL_DO_NOT_RUN
        warn + swap        → CLOSE_APPS_OR_RESTART
        warn (no swap)     → READY_FOR_ACTIVE300
        ok                 → READY_FOR_ACTIVE300
    """
    if state in ("critical", "emergency"):
        return VERDICT_CRITICAL_DO_NOT_RUN
    if state == "warn" and swap_detected:
        return VERDICT_CLOSE_APPS_OR_RESTART
    return VERDICT_READY_FOR_ACTIVE300


def _operator_action_for_verdict(verdict: str) -> str:
    """Human-readable recommended action for each verdict."""
    if verdict == VERDICT_CRITICAL_DO_NOT_RUN:
        return "restart or close heavy apps; rerun with --require-memory-ok"
    if verdict == VERDICT_CLOSE_APPS_OR_RESTART:
        return "close some apps to clear swap; rerun preflight"
    return "memory state OK — ready for active300 sprint"


# =============================================================================
# Result dataclass
# =============================================================================

@dataclass
class PreflightResult:
    verdict: str
    uma_state: str
    system_used_gib: float
    system_available_gib: float
    swap_used_gib: float
    swap_detected: bool
    io_only: bool
    mlx_active_mb: int
    uma_usage_pct: int
    pressure_level: str
    thresholds_warn_gib: float
    thresholds_critical_gib: float
    thresholds_emergency_gib: float
    recommended_action: str
    sample_time_iso: str
    error: Optional[str] = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items()}


# =============================================================================
# Core preflight check
# =============================================================================

def run_preflight() -> PreflightResult:
    """
    Run memory preflight check and return structured result.

    No network I/O. No process killing. No sudo.
    Reads only in-boundary memory samplers.
    """
    sample_time_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    error: Optional[str] = None

    # ── UMA snapshot via resource_governor (hysteresis-aware) ──────────────
    uma = sample_uma_status()
    state = uma.state if uma.state else "ok"
    swap_detected = getattr(uma, "swap_detected", False)
    swap_used_gib = getattr(uma, "swap_used_gib", 0.0)
    system_used_gib = getattr(uma, "system_used_gib", 0.0)
    system_available_gib = getattr(uma, "system_available_gib", 0.0)
    io_only = getattr(uma, "io_only", False)

    # ── Detailed snapshot via uma_budget ───────────────────────────────────
    snap = get_uma_snapshot()
    mlx_active_mb = snap.get("mlx_active_mb", 0)
    uma_usage_pct = snap.get("uma_usage_pct", 0)
    pressure_level = snap.get("uma_pressure_level", "ok")

    # ── Derive verdict ─────────────────────────────────────────────────────
    verdict = _derive_verdict(state, swap_detected)
    recommended_action = _operator_action_for_verdict(verdict)

    # ── Capture errors ─────────────────────────────────────────────────────
    last_error = getattr(uma, "last_error", None)
    if last_error:
        error = f"uma_sample: {last_error}"

    return PreflightResult(
        verdict=verdict,
        uma_state=state,
        system_used_gib=round(system_used_gib, 3),
        system_available_gib=round(system_available_gib, 3),
        swap_used_gib=round(swap_used_gib, 3),
        swap_detected=swap_detected,
        io_only=io_only,
        mlx_active_mb=mlx_active_mb,
        uma_usage_pct=uma_usage_pct,
        pressure_level=pressure_level,
        thresholds_warn_gib=UMA_WARN_GIB,
        thresholds_critical_gib=UMA_CRITICAL_GIB,
        thresholds_emergency_gib=UMA_EMERGENCY_GIB,
        recommended_action=recommended_action,
        sample_time_iso=sample_time_iso,
        error=error,
    )


# =============================================================================
# Output formatters
# =============================================================================

def format_json(result: PreflightResult) -> str:
    """Serialize PreflightResult as JSON to stdout."""
    return json.dumps(result.as_dict(), indent=2)


def format_markdown(result: PreflightResult) -> str:
    """Format PreflightResult as Markdown to stdout."""
    d = result.as_dict()
    verdict = d["verdict"]
    emoji = {
        VERDICT_READY_FOR_ACTIVE300: "🟢",
        VERDICT_CLOSE_APPS_OR_RESTART: "🟡",
        VERDICT_CRITICAL_DO_NOT_RUN: "🔴",
    }.get(verdict, "⚪")

    lines = [
        "# Memory Preflight Report",
        "",
        f"**Verdict:** {emoji} `{verdict}`",
        "",
        "## Memory State",
        "",
        f"- UMA state: `{d['uma_state']}`",
        f"- System used: {d['system_used_gib']} GiB",
        f"- System available: {d['system_available_gib']} GiB",
        f"- Swap used: {d['swap_used_gib']} GiB",
        f"- Swap detected: `{d['swap_detected']}`",
        f"- I/O-only mode: `{d['io_only']}`",
        "",
        "## MLX Memory",
        "",
        f"- MLX active: {d['mlx_active_mb']} MB",
        "",
        "## UMA Pressure",
        "",
        f"- Usage: {d['uma_usage_pct']}%",
        f"- Pressure level: `{d['pressure_level']}`",
        f"- Thresholds: warn={d['thresholds_warn_gib']} GiB, "
        f"critical={d['thresholds_critical_gib']} GiB, "
        f"emergency={d['thresholds_emergency_gib']} GiB",
        "",
        "## Recommendation",
        "",
        f"- **Action:** {d['recommended_action']}",
    ]

    if d["error"]:
        lines.extend(["", "## Errors", "", f"- {d['error']}"])

    lines.extend(["", f"_Sample time: {d['sample_time_iso']}_"])
    return "\n".join(lines)


# =============================================================================
# CLI
# =============================================================================

def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="tools.live_memory_preflight",
        description="In-boundary memory preflight check. No network, no process kill, no sudo.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON (default)",
    )
    parser.add_argument(
        "--md",
        action="store_true",
        help="Output Markdown instead of JSON",
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args(sys.argv[1:])

    result = run_preflight()

    if args.md:
        output = format_markdown(result)
    else:
        output = format_json(result)

    print(output)

    # Exit code reflects verdict
    if result.verdict == VERDICT_CRITICAL_DO_NOT_RUN:
        sys.exit(2)
    if result.verdict == VERDICT_CLOSE_APPS_OR_RESTART:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
