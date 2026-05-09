#!/usr/bin/env python3
"""
F206BH LIVE SPRINT MEASUREMENT HARNESS

Canonical live sprint measurement: run 180s/300s/600s measured sprints and capture
metrics reproducibly.

Safety invariants:
- Default is --dry-run (no live sprint)
- Live execution requires explicit --live flag
- No stealth default, no aggressive default
- Duration < 180s blocked unless --allow-smoke passed
- No live network during tests
- No MLX model load during tests

Profiles:
  smoke180  → 180s sprint (smoke test)
  active300 → 300s sprint (standard)
  active600 → 600s sprint (extended)

Usage:
  # Dry-run (default, hermetic)
  python benchmarks/live_sprint_measurement.py --profile smoke180 --query "LockBit ransomware"
  python benchmarks/live_sprint_measurement.py --profile active300 --query "APT29" --dry-run

  # Live execution (requires --live)
  python benchmarks/live_sprint_measurement.py --profile active300 --query "LockBit" --live
  python benchmarks/live_sprint_measurement.py --profile active600 --query "ransomware" --live --output-json /tmp/live_measure.json

  # Preflight check (no sprint execution)
  python benchmarks/live_sprint_measurement.py --print-preflight-only
  python benchmarks/live_sprint_measurement.py --print-preflight-only --output-json /tmp/preflight.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

# F223C: Fix namespace package collision for direct script execution.
#
# The editable install creates a _NamespacePath __path_hook__ that intercepts
# package lookups BEFORE sys.path is consulted. The hook is registered as a
# meta_path finder and path_hook.
#
# Problem: the editable install's _EditableNamespaceFinder._path_hook intercepts
# 'hledac' lookups and returns the editable site-packages path instead of the
# project root. This causes hledac.__path__ to be [_NamespacePath(...)] pointing
# to site-packages rather than the project root.
#
# Solution: Pre-create a hledac stub module in sys.modules with __path__ pointing
# to the project root. This must be done BEFORE any import of hledac or its
# subpackages. We do NOT add _project_root to sys.path because that causes
# 'tools' to resolve at the project-root tools/ (wrong) instead of the
# universal/tools/ (correct).
from pathlib import Path as _P

_project_root = str(_P(__file__).resolve().parent.parent.parent)  # 3 levels: benchmarks/ → universal/ → hledac/ (project root)

import sys as _sys

# F223C: Add universal dir to sys.path BEFORE hledac stub creation so that
# _universal is defined when we reference it in __path__
_universal = str(_P(__file__).resolve().parent.parent)  # benchmarks/ → universal/ → hledac/universal/
if _universal not in _sys.path:
    _sys.path.insert(0, _universal)

# F223C: Pre-emptively inject a stub hledac module into sys.modules BEFORE the
# standard import machinery resolves hledac via the editable install's namespace
# package hook. This ensures hledac.__path__ points to both project root and
# universal dir so that:
#   - hledac.universal.core imports resolve (via project_root/hledac/universal/)
#   - tools.research_quality_score imports resolve (via universal/tools/)
import types as _types
_hledac_stub = _types.ModuleType('hledac')
_hledac_stub.__path__ = [_project_root, _universal]  # Both locations!
_hledac_stub.__file__ = f'{_project_root}/hledac/__init__.py'
_hledac_stub.__package__ = 'hledac'
_hledac_stub.__spec__ = None
_sys.modules['hledac'] = _hledac_stub

# Now import via standard mechanism
from hledac.universal.core import __main__ as core_main
from hledac.universal.paths import get_sprint_json_report_path
from benchmarks.live_measurement_parser import parse_sprint_report as _parse_sprint_report_impl

# Backward-compatibility re-exports (may be used by external callers)
from benchmarks.live_measurement_parser import _has_terminal_source_outcomes as _has_terminal_source_outcomes
from benchmarks.live_measurement_parser import _has_scheduler_exit_path as _has_scheduler_exit_path

# NOW LAZY: moved to lazy-import inside _stamp_live_kpi()
# F214C: research_quality integration (no network, no MLX — pure scoring)
# F224B: Removed top-level import. Heavy modules deferred to call site only.
# -----------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILE_DURATION: dict[str, int] = {
    "smoke180": 180,
    "nonfeed_diagnostic180": 180,
    "active300": 300,
    "active600": 600,
}

# Profile metadata — makes profiles truthful and self-documenting
# - planned_duration_s: total wall-clock duration
# - expected_windup_lead_s: how long lead/windup occupies before active runtime
# - expected_active_window_s: meaningful active runtime window (>0 = active profile)
# - active_runtime_expected: whether profile produces active runtime cycles
PROFILE_META: dict[str, dict] = {
    "smoke180": {
        "planned_duration_s": 180,
        "expected_windup_lead_s": 180,   # full duration is lead/windup — no active window
        "expected_active_window_s": 0,   # zero → smoke180 is ENTRY_SMOKE_ONLY
        "active_runtime_expected": False,
    },
    "nonfeed_diagnostic180": {
        "planned_duration_s": 180,
        "expected_windup_lead_s": 0,     # diagnostic: immediate entry, no windup phase
        "expected_active_window_s": 180,  # full 180s is active runtime for domain queries
        "active_runtime_expected": True,  # diagnostic profiles produce active runtime
        "acquisition_profile": "nonfeed_diagnostic",  # F223A: maps to nonfeed_diagnostic acquisition profile
    },
    "active300": {
        "planned_duration_s": 300,
        "expected_windup_lead_s": 180,   # windup consumes ~180s
        "expected_active_window_s": 120, # ~120s of active runtime remains
        "active_runtime_expected": True,
    },
    "active600": {
        "planned_duration_s": 600,
        "expected_windup_lead_s": 180,   # windup consumes ~180s
        "expected_active_window_s": 420, # ~420s of active runtime remains
        "active_runtime_expected": True,
    },
}

_CANONICAL_ACQUISITION_PROFILES = frozenset(["default", "nonfeed_diagnostic"])


def _resolve_acquisition_profile(profile: str) -> str:
    """
    F228A: Resolve benchmark profile → canonical acquisition profile.

    Reads PROFILE_META[profile]["acquisition_profile"] when present,
    falls back to "default". Validates returned value is a canonical
    acquisition profile name — never returns benchmark-specific aliases
    like "nonfeed_diagnostic180" as an acquisition_profile.
    """
    resolved = PROFILE_META.get(profile, {}).get("acquisition_profile", "default")
    if resolved not in _CANONICAL_ACQUISITION_PROFILES:
        resolved = "default"
    return resolved


MIN_DURATION_S = 180

# F222B: Namespace Reality Guard — diagnostic without heavy imports
def get_invocation_reality() -> dict:
    """
    Return a hermetic diagnostic dict about the invocation namespace.
    Does NOT import heavy MLX/model deps — only stdlib + already-imported locals.
    """
    import hledac as _hledac

    _live_sprint_measurement_file = str(_P(__file__).resolve())

    # core/__main__ resolution (already imported as core_main)
    _core_main_file = str(_P(core_main.__file__).resolve()) if hasattr(core_main, "__file__") else "N/A"

    # sprint_scheduler resolution — use sys.modules to avoid re-import
    _ss_mod = sys.modules.get("hledac.universal.runtime.sprint_scheduler")
    _sprint_scheduler_file = str(_P(_ss_mod.__file__).resolve()) if _ss_mod and hasattr(_ss_mod, "__file__") else "N/A"

    # acquisition_strategy resolution
    _as_mod = sys.modules.get("hledac.universal.runtime.acquisition_strategy")
    _acquisition_strategy_file = str(_P(_as_mod.__file__).resolve()) if _as_mod and hasattr(_as_mod, "__file__") else "N/A"

    # Profile names
    _profile_names = list(PROFILE_DURATION.keys())

    # nonfeed_diagnostic180 profile metadata
    _nonfeed_meta = PROFILE_META.get("nonfeed_diagnostic180", {})
    _nonfeed_present = "nonfeed_diagnostic180" in PROFILE_DURATION
    _nonfeed_duration = PROFILE_DURATION.get("nonfeed_diagnostic180", 0)
    _nonfeed_acquisition_profile = _nonfeed_meta.get("acquisition_profile", "nonfeed_diagnostic") if _nonfeed_meta else "N/A"

    # F223H: Repo-root reality — detect wrong CWD before artifact scans
    _repo_root_reality = get_repo_root_reality()

    return {
        "live_sprint_measurement_file": _live_sprint_measurement_file,
        "core_main_file": _core_main_file,
        "sprint_scheduler_file": _sprint_scheduler_file,
        "acquisition_strategy_file": _acquisition_strategy_file,
        "cwd": os.getcwd(),
        "sys_path_head": sys.path[0] if sys.path else "",
        "hledac_path_type": type(_hledac.__path__).__name__,
        "hledac_path_entries": list(_hledac.__path__),
        "profile_names": _profile_names,
        "nonfeed_diagnostic180_present": _nonfeed_present,
        "nonfeed_diagnostic180_duration": _nonfeed_duration,
        "nonfeed_diagnostic180_acquisition_profile": _nonfeed_acquisition_profile,
        # F223H: Repo-root reality
        "cwd_is_repo_parent": _repo_root_reality["cwd_is_repo_parent"],
        "cwd_is_universal_root": _repo_root_reality["cwd_is_universal_root"],
        "artifact_scan_root": _repo_root_reality["resolved_repo_root"],
        "cwd_warning": _repo_root_reality["cwd_warning"],
    }


# F223H: Repo-root reality guard
_EXPECTED_REPO_ROOT = "/Users/vojtechhamada/PycharmProjects/Hledac"
_UNIVERSAL_ROOT = f"{_EXPECTED_REPO_ROOT}/hledac/universal"


def get_repo_root_reality() -> dict:
    """
    Return a hermetic diagnostic dict about the current working directory
    vs. the expected repo-root reality. Detects when operator tools are
    running from a temp/context sandbox CWD instead of actual repo root.

    No live run. No network. No MLX. No model load.
    """
    _cwd = os.getcwd()
    _resolved = str(_P(_cwd).resolve())

    # Expected canonical roots
    _expected = _EXPECTED_REPO_ROOT
    _universal = _UNIVERSAL_ROOT

    # Check positions
    _is_repo_parent = _resolved == _expected or _resolved.startswith(f"{_expected}/")
    _is_universal_root = _resolved == _universal or _resolved.startswith(f"{_universal}/")

    # Check existence of key directories
    _universal_exists = _P(_universal).exists()
    _tests_probe_exists = _P(f"{_universal}/tests/probe_f223h_cwd_invocation_guard").exists()

    # Artifact scan root is always the universal root for probe artifact scans
    _artifact_scan_root = _universal

    # Warning only when cwd is outside the repo tree
    _cwd_warning = ""
    if not _is_repo_parent:
        _cwd_warning = (
            f"WARNING: CWD={_cwd} is outside expected repo root "
            f"({_expected}). Artifact scans may glob wrong directory. "
            f"Use --repo-root {_universal} or run from {_universal}."
        )

    return {
        "cwd": _cwd,
        "resolved_cwd": _resolved,
        "resolved_repo_root": _artifact_scan_root,
        "expected_repo_root": _expected,
        "universal_root": _universal,
        "is_actual_repo_root": _is_universal_root,
        "cwd_is_repo_parent": _is_repo_parent,
        "cwd_is_universal_root": _is_universal_root,
        "universal_root_exists": _universal_exists,
        "tests_probe_dir_exists": _tests_probe_exists,
        "artifact_scan_root": _artifact_scan_root,
        "cwd_warning": _cwd_warning,
    }


# Memory-gate operator action text
_MEMORY_GATE_OPERATOR_ACTION = (
    "restart or close heavy apps; rerun active300 with --require-memory-ok"
)

# F215D: Swap gate threshold — active300/600 must be swap-clean for comparable results
# Changed from 3.0 to 1.0 GiB: user observed 4.8 GiB swap during F214/F214R2 still produced
# "completed" results that were hardware-constrained but not marked non-comparable.
# 1.0 GiB provides clean headroom while still allowing tiny background swap.
#
# F220F: This is the ACTIVE PROFILE threshold for result comparability.
# The prelive decision gate (prelive_decision_gate.py) uses tiered policy:
#   - clean: swap <= 2.0 GiB → READY_TO_RUN_NOW
#   - diagnostic: 2.0 < swap <= 4.0 GiB → READY_FOR_LIVE_HARDWARE_TAINTED
#   - hard_block: swap > 4.0 GiB → BLOCKED_BY_MEMORY
# For measurement quality, active300/600 enforces stricter 1.0 GiB threshold
# to ensure comparable results to clean-swap baseline.
_SWAP_GATE_THRESHOLD_GIB = 1.0
_SWAP_GATE_OPERATOR_ACTION = (
    "restart to clear swap; or use --allow-high-swap to run anyway (results will be non-comparable)"
)

# Schema types extracted to live_measurement_schema.py
from hledac.universal.benchmarks.live_measurement_schema import (
    RunMode,
    MeasurementStatus,
    RunQualityVerdict,
    LiveMeasurementResult,
)

# ---------------------------------------------------------------------------
# Readiness artifact paths (READ-ONLY)
# ---------------------------------------------------------------------------

READINESS_ARTIFACTS = {
    "stabilization_seal": Path(__file__).parent.parent / "probe_f206an_stabilization" / "stabilization_seal.json",
    "hermetic_regression_manifest": Path(__file__).parent.parent / "probe_f206aq_hermetic_regression" / "hermetic_regression_manifest.json",
    "transport_authority_status": Path(__file__).parent.parent / "probe_transport_authority_f206bc" / "transport_authority_status_refreshed.json",
    "mlx_wired_limit_seal": Path(__file__).parent.parent / "probe_f206ao_mlx_wired_limit" / "mlx_wired_limit_seal.json",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_readiness_artifacts() -> dict[str, bool]:
    """Fail-soft check for readiness artifacts. Returns dict of artifact → present."""
    results = {}
    for name, path in READINESS_ARTIFACTS.items():
        results[name] = path.exists()
    return results


def _make_measurement_id() -> str:
    ts = time.time_ns() // 1_000_000
    uid = uuid.uuid4().hex[:6]
    return f"lsm_{ts}_{uid}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _capture_uma() -> dict:
    """Capture UMA status. Fail-soft: returns None on error."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        s = sample_uma_status()
        return {
            "used_gib": round(s.system_used_gib, 3),
            "swap_gib": round(s.swap_used_gib, 3),
            "state": s.state,
        }
    except Exception:
        return {"used_gib": None, "swap_gib": None, "state": None}


def _uma_state_is_critical_or_emergency(state: str | None) -> bool:
    """Return True if UMA state indicates critical or emergency memory pressure."""
    if not state:
        return False
    return state in ("critical", "emergency")


def _is_active_domain_query(runtime_truth: dict | None, profile_verdict: str | None) -> bool:
    """
    Detect whether the run was an active300/active600 domain query.

    A domain query has meaningful runtime (cycles_started > 0) OR explicit ct/public
    findings in branch_mix, indicating the non-feed acquisition lanes were targeted.
    smoke180 profiles are NOT domain queries (they are just entry smoke checks).
    """
    if profile_verdict == "ENTRY_SMOKE_ONLY":
        return False
    rt = runtime_truth or {}
    cycles = rt.get("cycles_started", 0)
    branch_mix = rt.get("branch_mix", {})
    ct_findings = branch_mix.get("ct_findings", 0) if isinstance(branch_mix, dict) else 0
    public_findings = branch_mix.get("public_findings", 0) if isinstance(branch_mix, dict) else 0
    return cycles > 0 or ct_findings > 0 or public_findings > 0


def _has_terminal_source_outcomes(acquisition_strategy: dict | None) -> bool:
    """
    Return True if acquisition_strategy has non-empty source_family_outcomes.

    source_family_outcomes is the canonical record of which lanes were
    dispatched and their terminal state. An empty/missing dict means the
    acquisition never reached the point of recording lane outcomes.
    """
    if not acquisition_strategy or not isinstance(acquisition_strategy, dict):
        return False
    sf_outcomes = acquisition_strategy.get("source_family_outcomes")
    if isinstance(sf_outcomes, dict):
        return bool(sf_outcomes)
    if isinstance(sf_outcomes, list):
        return bool(sf_outcomes)
    return False


def _has_scheduler_exit_path(scheduler_exit: dict | None) -> bool:
    """
    Return True if scheduler_exit contains a non-empty path string.

    scheduler_exit_path is the canonical record of how the scheduler exited
    (which guard/condition triggered windup). An empty/missing path means
    the scheduler exit was never recorded.
    """
    if not scheduler_exit or not isinstance(scheduler_exit, dict):
        return False
    path = (
        scheduler_exit.get("path")
        or scheduler_exit.get("exit_path")  # canonical field in scheduler_exit dict
        or scheduler_exit.get("scheduler_exit_path")  # legacy live_kpi alias
        or ""
    )
    return bool(str(path).strip())


def _derive_run_quality_verdict(
    status: MeasurementStatus,
    profile_verdict: str | None,
    uma_pre_state: str | None,
    runtime_truth: dict | None,
    swap_pre_gib: float | None,
    is_memory_gate_abort: bool = False,
    swap_gate_triggered: bool = False,
    acquisition_report: dict | None = None,
    acquisition_terminality_checked: bool | None = None,
    acquisition_terminality_satisfied: bool | None = None,
    acquisition_terminality_missing_lanes: tuple[str, ...] | None = None,
    acquisition_strategy: dict | None = None,
    scheduler_exit: dict | None = None,
    # F210D: wallclock budget enforcement
    planned_duration_s: float | None = None,
    actual_duration_s: float | None = None,
) -> tuple[RunQualityVerdict | None, bool, str | None, bool, str | None, str | None]:
    """
    Derive run quality verdict from measurement state.

    Returns:
        (verdict, hardware_constrained, memory_state_pre, swap_warning,
         recommended_next_profile, recommended_operator_action)

    Verdict is None when no runtime execution occurred (PLANNED/RUNNING with no runtime_truth).
    """
    verdict: RunQualityVerdict | None = None
    hardware_constrained = False
    memory_state_pre = uma_pre_state
    swap_warning = swap_pre_gib is not None and swap_pre_gib > 0
    recommended_next_profile: str | None = None
    recommended_operator_action: str | None = None

    # Rule 0: ABORTED_MEMORY_GATE — specific memory-gate abort, not generic FAIL
    if is_memory_gate_abort:
        verdict = RunQualityVerdict.ABORTED_MEMORY_GATE
        hardware_constrained = True
        recommended_next_profile = "none_until_memory_ok"
        recommended_operator_action = _MEMORY_GATE_OPERATOR_ACTION
        return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action

    # Rule 0b: SWAP_GATE — active profile with high swap, proceeding anyway (allow_high_swap)
    # F212D: when swap_gate_triggered=True, mark hardware_constrained so results are flagged
    # Check this BEFORE status checks so it fires regardless of PLANNED/COMPLETED/RUNNING status.
    if swap_gate_triggered:
        verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED
        hardware_constrained = True
        recommended_next_profile = "smoke180 or active300_after_restart"
        recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
        return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action

    # Rule 1: ENTRY_SMOKE_ONLY for smoke profiles (always determinable)
    if profile_verdict == "ENTRY_SMOKE_ONLY":
        verdict = RunQualityVerdict.ENTRY_SMOKE_ONLY
        recommended_next_profile = "active300"
        return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action

    # Rule 2: FAIL_RUNTIME_ERROR / FAIL_MEASUREMENT_ERROR for failed/aborted runs
    if status == MeasurementStatus.FAILED:
        verdict = RunQualityVerdict.FAIL_RUNTIME_ERROR
        return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action

    if status == MeasurementStatus.ABORTED:
        verdict = RunQualityVerdict.FAIL_MEASUREMENT_ERROR
        return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action

    # Rule 3: COMPLETED — requires runtime_truth to derive meaningful verdict
    if status == MeasurementStatus.COMPLETED:
        if runtime_truth is None:
            # Completed but no runtime data — cannot determine meaningful verdict
            verdict = None
            return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action

        is_critical_uma = _uma_state_is_critical_or_emergency(uma_pre_state)
        runtime_meaningful = runtime_truth.get("cycles_started", 0) > 0

        if is_critical_uma and runtime_meaningful:
            verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED
            hardware_constrained = True
            recommended_next_profile = None  # requires human review
        elif is_critical_uma and not runtime_meaningful:
            verdict = RunQualityVerdict.FAIL_MEASUREMENT_ERROR
        else:
            verdict = RunQualityVerdict.PASS_VALID_CAPABILITY_RUN
            if uma_pre_state in ("warn",):
                recommended_next_profile = "active300"

    # PLANNED/RUNNING with no runtime_truth → verdict stays None (no execution occurred)
    # F211D: Wallclock budget enforcement — BEFORE terminality downgrade
    # Priority: memory abort / entry failure > hardware constrained > wallclock budget > terminality failures
    # Wallclock check is unconditional on PASS_VALID_CAPABILITY_RUN so it can override
    # terminality downgrade (FAIL_TERMINALITY_UNSATISFIED cannot mask wallclock overrun).
    if verdict == RunQualityVerdict.PASS_VALID_CAPABILITY_RUN:
        if planned_duration_s is not None and actual_duration_s is not None:
            tolerance_s = max(planned_duration_s * 1.10, planned_duration_s + 30.0)
            if actual_duration_s > tolerance_s:
                verdict = RunQualityVerdict.FAIL_WALLCLOCK_BUDGET_EXCEEDED

    # F208I Rule 4: Terminality downgrade for active300/active600 domain queries
    # Downgrades PASS_VALID_CAPABILITY_RUN to specific FAIL when terminality contract is missing/unsatisfied.
    # smoke180 (ENTRY_SMOKE_ONLY) is already handled in Rule 1 and takes priority.
    # Hardware-constrained verdict takes priority over terminality downgrade.
    if verdict == RunQualityVerdict.PASS_VALID_CAPABILITY_RUN:
        _is_domain_query = _is_active_domain_query(runtime_truth, profile_verdict)
        if _is_domain_query:
            # 4a: acquisition_report.schema_version must be present
            if acquisition_report is None or not isinstance(acquisition_report, dict) or not acquisition_report.get("schema_version"):
                verdict = RunQualityVerdict.FAIL_TERMINALITY_NOT_CHECKED
            # 4b: acquisition_terminality_checked must be True
            elif acquisition_terminality_checked is not True:
                verdict = RunQualityVerdict.FAIL_TERMINALITY_NOT_CHECKED
            # 4c: acquisition_terminality_satisfied must be True AND missing_lanes must be empty
            # F224A: fixed — missing_lanes alone must NOT override satisfied=True;
            # non-empty missing_lanes is a diagnostic signal only, not a verdict override.
            elif acquisition_terminality_satisfied is not True and (
                acquisition_terminality_missing_lanes is not None
                and acquisition_terminality_missing_lanes != ()
            ):
                verdict = RunQualityVerdict.FAIL_TERMINALITY_UNSATISFIED
            # 4d: source_family_outcomes must be present and non-empty
            elif not _has_terminal_source_outcomes(acquisition_strategy):
                verdict = RunQualityVerdict.FAIL_MISSING_SOURCE_OUTCOMES
            # 4e: scheduler_exit_path must be non-empty
            elif not _has_scheduler_exit_path(scheduler_exit):
                verdict = RunQualityVerdict.FAIL_SCHEDULER_EXIT_MISSING

    return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action


def _parse_sprint_report(report_path: str | None) -> dict | None:
    """
    Parse sprint JSON report for measurement metrics.

    F208C: Canonical acquisition report path checked FIRST.
    Legacy fallback paths preserved for backward compatibility.

    Canonical extraction strategy (F208C):
    1. acquisition_report (top-level key from build_acquisition_report()) — canonical
    2. runtime_truth (top-level dict) for cycles, accepted_findings, primary_signal_source
    3. timing_truth (top-level dict) for timing data
    4. canonical_run_summary for checkpoint_zero_category and any missing fields

    Fail-soft: returns partial dict if some fields are missing.
    """
    return _parse_sprint_report_impl(report_path)




def _get_profile_verdict(profile: str) -> tuple[bool, int | None, int | None, str]:
    """Derive profile truthfulness tuple from PROFILE_META. Returns (active_expected, windup, window, verdict)."""
    meta = PROFILE_META.get(profile, {})
    active_expected = meta.get("active_runtime_expected", False)
    windup_lead_s = meta.get("expected_windup_lead_s")
    active_window_s = meta.get("expected_active_window_s")
    verdict = "ENTRY_SMOKE_ONLY" if not active_expected else "ACTIVE_SPRINT"
    return active_expected, windup_lead_s, active_window_s, verdict


def _stamp_profile_meta(result: LiveMeasurementResult, profile: str) -> None:
    """Stamp profile truthfulness metadata onto result."""
    active_expected, windup_lead_s, active_window_s, verdict = _get_profile_verdict(profile)
    result.active_runtime_expected = active_expected
    result.expected_windup_lead_s = windup_lead_s
    result.expected_active_window_s = active_window_s
    result.profile_verdict = verdict


def _stamp_run_quality_verdict(
    result: LiveMeasurementResult,
    is_memory_gate_abort: bool = False,
) -> None:
    """Derive and stamp run quality verdict onto result."""
    verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next, operator_action = _derive_run_quality_verdict(
        status=result.status,
        profile_verdict=result.profile_verdict,
        uma_pre_state=result.uma_pre_state,
        runtime_truth=result.runtime_truth,
        swap_pre_gib=result.uma_pre_swap_gib,
        is_memory_gate_abort=is_memory_gate_abort,
        swap_gate_triggered=result.swap_gate_triggered or False,
        acquisition_report=result.acquisition_report,
        acquisition_terminality_checked=result.acquisition_terminality_checked,
        acquisition_terminality_satisfied=result.acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=result.acquisition_terminality_missing_lanes,
        acquisition_strategy=result.acquisition_strategy,
        scheduler_exit=result.scheduler_exit,
        planned_duration_s=result.planned_duration_s,
        actual_duration_s=result.actual_duration_s,
    )
    result.run_quality_verdict = verdict.value if verdict is not None else None
    result.hardware_constrained = hardware_constrained
    result.memory_state_pre = memory_state_pre
    result.memory_state_post = result.uma_post_state
    result.swap_warning = swap_warning
    result.recommended_next_profile = recommended_next
    result.recommended_operator_action = operator_action


def _derive_live_kpi(
    status: MeasurementStatus,
    is_memory_gate_abort: bool,
    runtime_truth: dict | None,
    actual_duration_s: float | None,
    primary_signal_source: str | None,
    run_quality_verdict: str | None,
    hardware_constrained: bool | None,
    public_pipeline: dict | None = None,
    timing_truth: dict | None = None,
    acquisition_strategy: dict | None = None,
    windup_guard_observation: dict | None = None,
    return_guard_observation: dict | None = None,
    scheduler_exit: dict | None = None,
    acquisition_report: dict | None = None,
    profile_verdict: str | None = None,
    acquisition_terminality_checked: bool | None = None,
    acquisition_terminality_satisfied: bool | None = None,
    acquisition_terminality_missing_lanes: tuple[str, ...] | None = None,
    acquisition_terminality_report: dict | None = None,
    # F210B: Explicit source_family_outcomes from acquisition_report for lane-truth derivation
    # (distinct from acquisition_strategy["source_family_outcomes"] which is a sanitized subset)
    # When provided, overrides the acquisition_strategy["source_family_outcomes"] for all
    # live_kpi derivations (source_family_counts, nonfeed_attempted_families).
    explicit_source_family_outcomes: list[dict] | None = None,
    # F209B: Acquisition prelude telemetry
    acquisition_prelude_checked: bool | None = None,
    acquisition_prelude_ran: bool | None = None,
    acquisition_prelude_required_lanes: tuple[str, ...] | None = None,
    acquisition_prelude_terminal_lanes: tuple[str, ...] | None = None,
    acquisition_prelude_missing_lanes: tuple[str, ...] | None = None,
    acquisition_prelude_skipped_lanes: dict | None = None,
    acquisition_prelude_errors: dict | None = None,
    acquisition_prelude_duration_s: float | None = None,
    acquisition_prelude_reason: str | None = None,
    # F210D: wallclock budget enforcement
    planned_duration_s: float | None = None,
    # F225A: Claims runtime surface status from ClaimsCoordinator
    claims_runtime_status: dict | None = None,
) -> dict:
    """
    Compute live KPI dict from parsed sprint report.

    Returns a dict with:
      - total_findings
      - wallclock_budget_exceeded            (F210D)
      - wallclock_budget_excess_s            (F210D)
      - wallclock_tolerance_s                (F210D)
      - accepted_findings
      - cycles_completed
      - findings_per_min
      - primary_signal_source
      - branch_accepted_counts           (F211B)  # from branch_mix: {family: accepted_count}
      - lane_execution_counts            (F211B)  # from source_family_outcomes: {family: {attempted, terminal_state, raw_count, accepted_count, error, skipped}}
      - source_family_counts             # from lane_execution_counts: {family: accepted_count} (accepted>0 only; FEED from branch_mix)
      - source_family_outcomes_display   (F211B)  # lane execution list including 0-accepted families
      - nonfeed_attempted_families       # families with attempted=True from lane_execution_counts (FEED excluded)
      - nonfeed_accepted_findings
      - public_fetch_attempted
      - public_acceptance_attempted       (F207K)
      - public_acceptance_accepted        (F207K)
      - public_acceptance_rejected       (F207K)
      - public_acceptance_reject_reasons  (F207K)
      - top_public_reject_reason          (F207K)
      - public_rejected_url_sample        (F207K)
      - feed_dominance_score
      - feed_balance_recommendation
      - estimated_per_source_soft_cap
      - dominant_feed_source
      - dominant_feed_share_pct
      - run_quality_verdict
      - hardware_constrained
      - next_action
      - next_action_detail                (F207K)
      - nonfeed_starvation_suspected      (F207M)
      - nonfeed_starvation_reason         (F207M)
      - windup_lead_requested_s           (F207M)
      - windup_lead_observed_s            (F207M)
      - active_window_budget_s            (F207M)
      - nonfeed_eligible_families         (F207M)
      - nonfeed_skipped_reasons           (F207M)
      - return_guard_checked               (F207T)
      - return_guard_required_lanes        (F207T)
      - return_guard_satisfied              (F207T)
      - return_guard_delayed_for_nonfeed    (F207T)
      - return_guard_block_reason           (F207T)
      - return_guard_attempted_lanes        (F207T)
      - return_guard_skipped_lanes          (F207T)
      - return_guard_errors                 (F207T)
      - scheduler_exit_path                  (F207V-B)
      - scheduler_exit_reason                 (F207V-B)
      - scheduler_exit_phase                  (F207V-B)
      - scheduler_exit_cycle                  (F207V-B)
      - scheduler_exit_elapsed_s              (F207V-B)
      - scheduler_exit_guard_checked          (F207V-B)
      - scheduler_exit_guard_required         (F207V-B)
      - scheduler_exit_guard_satisfied        (F207V-B)
      - scheduler_deadline_enforced           (F212C)  # True if scheduler checked and enforced hard deadline
      - scheduler_deadline_checks             (F212C)  # hard_deadline_checked_count from runtime_truth
      - scheduler_deadline_exit_path         (F212C)  # exit_path when deadline was enforced
      - hard_deadline_checked_count          (F212C)
      - hard_deadline_exceeded               (F212C)
      - hard_deadline_exceeded_at_cycle      (F212C)
      - hard_deadline_remaining_s_at_exit    (F212C)
      - terminality_quality_verdict           (F208I)
      - terminality_failure_reasons           (F208I)
      - acquisition_report_schema_version      (F208I)
      - explicit_source_family_outcomes        (F210B)
      # F214D: CT Live Bridge Loss Telemetry
      - ct_loss_stage                       (F214D)  # from lane_verdict
      - ct_bridge_invoked                  (F214D)  # from lane_verdict
      - ct_raw_sample_count                (F214D)  # from lane_verdict
      - ct_candidates_built                (F214D)  # from lane_verdict
      - ct_bridge_rejections_count         (F214D)  # from lane_verdict
      - ct_candidates_accumulated          (F214D)  # from lane_verdict
      - ct_candidates_stored               (F214D)  # from lane_verdict
      - ct_storage_rejected                (F214D)  # from lane_verdict

    Feed telemetry preference (F207K-C):
    - If runtime_truth contains rich feed_telemetry (F207I path), use it.
    - Otherwise fall back to branch_mix ratio for feed_dominance_score.

    F211B: Lane execution truth is split into three distinct views:
    - branch_accepted_counts: per-branch accepted findings from branch_mix (unchanged truth)
    - lane_execution_counts: per-family lane execution with terminal_state from source_family_outcomes
    - source_family_counts: derived from lane_execution_counts (accepted>0 only, FEED from branch_mix)
    - nonfeed_attempted_families: derived from lane_execution_counts (FEED always excluded)
    This ensures accepted findings (branch truth) never conflates with lane execution state.
    """
    rt = runtime_truth or {}
    branch_mix = rt.get("branch_mix", {})
    # F214D: CT Live Bridge Loss — lane_verdict carries all ct_loss_* telemetry
    lane_verdict = rt.get("lane_verdict", {}) or {}

    # Rich feed telemetry from feed path (F207I), if present
    feed_telemetry = rt.get("feed_telemetry")
    if feed_telemetry:
        feed_dominance_score: float | None = feed_telemetry.get("feed_dominance_score")
        feed_balance_recommendation: str | None = feed_telemetry.get("feed_balance_recommendation")
        estimated_per_source_soft_cap: int | None = feed_telemetry.get("estimated_per_source_soft_cap")
        dominant_feed_source: str | None = feed_telemetry.get("dominant_feed_source")
        dominant_feed_share_pct: float | None = feed_telemetry.get("dominant_feed_share_pct")
    else:
        # Basic counts from branch_mix (fallback)
        feed_findings = branch_mix.get("feed_findings", 0)
        public_findings = branch_mix.get("public_findings", 0)
        ct_findings = branch_mix.get("ct_findings", 0)
        total_findings = feed_findings + public_findings + ct_findings

        feed_dominance_score = round(feed_findings / total_findings, 4) if total_findings > 0 else None
        feed_balance_recommendation = None
        estimated_per_source_soft_cap = None
        dominant_feed_source = None
        dominant_feed_share_pct = None

    # Basic counts (needed for nonfeed fields regardless of path)
    feed_findings = branch_mix.get("feed_findings", 0)
    public_findings = branch_mix.get("public_findings", 0)
    ct_findings = branch_mix.get("ct_findings", 0)
    total_findings = feed_findings + public_findings + ct_findings
    accepted_findings = rt.get("accepted_findings") or 0
    cycles_completed = rt.get("cycles_completed") or 0

    # findings_per_min: compute from actual_duration_s
    findings_per_min: float | None = None
    if actual_duration_s and actual_duration_s > 0:
        findings_per_min = round((total_findings / actual_duration_s) * 60, 2)

    # F211B: Split lane truth into three distinct views:
    # 1. branch_accepted_counts — per-branch accepted findings from branch_mix
    # 2. lane_execution_counts — per-family lane execution from source_family_outcomes
    #    (attempted, terminal_state, raw_count, accepted_count, error, skipped)
    # 3. source_family_outcomes_display — source_family_outcomes with 0-accepted families
    #    included so PUBLIC/CT appear even when accepted=0 (per F211B task 3)
    #
    # Nonfeed attempted families are derived from lane_execution_counts.

    # branch_accepted_counts: always from branch_mix (accepted findings per branch)
    branch_accepted_counts: dict[str, int] = {}
    if feed_findings > 0:
        branch_accepted_counts["feed"] = feed_findings
    if public_findings > 0:
        branch_accepted_counts["public"] = public_findings
    if ct_findings > 0:
        branch_accepted_counts["ct"] = ct_findings

    # lane_execution_counts: per-family lane execution truth from source_family_outcomes
    # F211B: Each entry has family, attempted, terminal_state, raw_count, accepted_count,
    # error, skipped. Families with accepted=0 but attempted=True are INCLUDED.
    _sfo_list = explicit_source_family_outcomes if explicit_source_family_outcomes is not None else (acquisition_strategy or {}).get("source_family_outcomes", [])
    lane_execution_counts: dict[str, dict] = {}
    source_family_outcomes_display: list[dict] = []
    if isinstance(_sfo_list, list):
        for _entry in _sfo_list:
            if isinstance(_entry, dict):
                _fam = _entry.get("family", "")
                _attempted = bool(_entry.get("attempted"))
                _skipped = bool(_entry.get("skipped"))
                _error = _entry.get("error")
                _raw = _entry.get("raw_count") or _entry.get("built_count") or 0
                _accepted = _entry.get("accepted_count") or _entry.get("accepted_findings", 0) or _entry.get("accepted", 0)
                # terminal_state: COMPLETED, SKIPPED, NEVER_ATTEMPTED, ERROR
                if not _attempted:
                    _terminal_state = "NEVER_ATTEMPTED"
                elif _skipped:
                    _terminal_state = "SKIPPED"
                elif _error:
                    _terminal_state = "ERROR"
                else:
                    _terminal_state = "COMPLETED"
                lane_execution_counts[_fam] = {
                    "attempted": _attempted,
                    "terminal_state": _terminal_state,
                    "raw_count": _raw,
                    "accepted_count": _accepted,
                    "error": _error,
                    "skipped": _skipped,
                }
                # F211B task 3: include in display even when accepted=0 (PUBLIC/CT with 0 accepted)
                if _attempted:
                    source_family_outcomes_display.append({
                        "family": _fam,
                        "attempted": _attempted,
                        "terminal_state": _terminal_state,
                        "raw_count": _raw,
                        "accepted_findings": _accepted,
                        "error": _error,
                        "skipped": _skipped,
                    })
    else:
        # Legacy fallback: use branch_mix as lane execution proxy
        for _fam, _count in [("public", public_findings), ("ct", ct_findings)]:
            if _count > 0:
                lane_execution_counts[_fam] = {
                    "attempted": True,
                    "terminal_state": "COMPLETED",
                    "raw_count": _count,
                    "accepted_count": _count,
                    "error": None,
                    "skipped": False,
                }
                source_family_outcomes_display.append({
                    "family": _fam,
                    "attempted": True,
                    "terminal_state": "COMPLETED",
                    "raw_count": _count,
                    "accepted_findings": _count,
                    "error": None,
                    "skipped": False,
                })

    # source_family_counts: derived from lane_execution_counts for backward compatibility
    # F211B: renamed from old source_family_counts — now only includes families with
    # accepted_count > 0 (FEED from branch_mix is included if feed_findings > 0)
    source_family_counts: dict[str, int] = {}
    if feed_findings > 0:
        source_family_counts["feed"] = feed_findings
    for _fam, _data in lane_execution_counts.items():
        if _fam != "feed" and _data.get("accepted_count", 0) > 0:
            source_family_counts[_fam] = _data["accepted_count"]

    # nonfeed_attempted_families: F215A CANONICAL BOUNDARY
    # Only populate from lane_execution_counts when built from source_family_outcomes (canonical).
    # When source_family_outcomes is absent (legacy), emit CANONICAL_FIELD_MISSING.
    _sfo_has_canonical = isinstance(_sfo_list, list) and len(_sfo_list) > 0
    if _sfo_has_canonical:
        _lec_lower_keys = {k.lower(): k for k in lane_execution_counts.keys()}
        _seen_lower = set()
        nonfeed_attempted_families = [
            _lec_lower_keys[_fam.lower()]
            for _fam, _data in lane_execution_counts.items()
            if (
                _fam.lower() != "feed"
                and _data.get("attempted")
                and _fam.lower() not in _seen_lower
                and not (_seen_lower.add(_fam.lower()) or False)
            )
        ]
    else:
        nonfeed_attempted_families = "CANONICAL_FIELD_MISSING"

    # nonfeed_accepted_findings
    nonfeed_accepted_findings = accepted_findings - feed_findings if accepted_findings else 0
    nonfeed_accepted_findings = max(0, nonfeed_accepted_findings)

    # F215B: PUBLIC Stage Source of Truth
    # If any public stage field indicates activity or timeout, ensure PUBLIC lane_execution_counts entry exists.
    # live_kpi_public_fetch_attempted reads from lane_execution_counts["PUBLIC"]["attempted"].
    # When PUBLIC is absent from source_family_outcomes (the sfo_list), lane_execution_counts["PUBLIC"]
    # never gets created, so public_fetch_attempted is False even though the public stage ran.
    # Fix: synthesize a diagnostic PUBLIC entry in lane_execution_counts when public stage signals exist.
    _has_public_signal = (
        (runtime_truth and runtime_truth.get("public_branch_timed_out"))
        or (runtime_truth and runtime_truth.get("branch_mix", {}).get("public_findings", 0) > 0)
    )
    # F215E: Synthesis must happen whenever _has_public_signal is True, regardless
    # of _sfo_has_canonical. When _sfo_has_canonical is False (legacy report), we still
    # need to document PUBLIC activity via synthesis so sanity checks don't falsely fail.
    if _has_public_signal:
        # Check if PUBLIC already present in lane_execution_counts
        _pub_already_in_lec = "PUBLIC" in lane_execution_counts
        if not _pub_already_in_lec:
            # F215B: Synthesize diagnostic PUBLIC outcome for lane_execution_counts SSOT.
            # This is benchmark-only diagnostic — does NOT invent findings, only documents activity.
            _rt_timed_out = runtime_truth.get("public_branch_timed_out") if runtime_truth else False
            _sig_reason = "terminal:timeout" if _rt_timed_out else "terminal:no_outcome_recorded"
            lane_execution_counts["PUBLIC"] = {
                "attempted": True,
                "terminal_state": "ERROR" if _sig_reason == "terminal:timeout" else "ATTEMPTED_NO_RESULTS",
                "raw_count": 0,
                "accepted_count": 0,
                "error": _sig_reason,
                "skipped": False,
            }
            # F215B: Also inject into source_family_outcomes_display so sfo_display matches lec
            source_family_outcomes_display.append({
                "family": "PUBLIC",
                "attempted": True,
                "terminal_state": lane_execution_counts["PUBLIC"]["terminal_state"],
                "raw_count": 0,
                "accepted_findings": 0,
                "error": _sig_reason,
                "skipped": False,
            })
            # F215E: After synthesis, nonfeed_attempted_families must include PUBLIC.
            # Deduplicate against existing entries case-insensitively.
            if isinstance(nonfeed_attempted_families, list):
                _has_pub_lower = any(_e.lower() == "public" for _e in nonfeed_attempted_families)
                if not _has_pub_lower:
                    nonfeed_attempted_families.append("PUBLIC")

    # public_fetch_attempted: F215A CANONICAL MEASUREMENT BOUNDARY
    # F215B SSOT fix: Must read from lane_execution_counts["PUBLIC"]["attempted"] directly.
    # F215E: Now always reads from lane_execution_counts after synthesis (synthesis happens
    # whenever _has_public_signal is True, regardless of _sfo_has_canonical).
    _pub_lec = lane_execution_counts.get("PUBLIC")
    if _pub_lec is not None:
        public_fetch_attempted = bool(_pub_lec.get("attempted", False))
    else:
        # No PUBLIC in lane_execution_counts — conservatively False
        public_fetch_attempted = False

    # Public pipeline telemetry from sprint report (F207K)
    pp = public_pipeline or {}
    public_acceptance_attempted: int = pp.get("public_acceptance_attempted", 0)
    public_acceptance_accepted: int = pp.get("public_acceptance_accepted", 0)
    public_acceptance_rejected_count: int = pp.get("public_acceptance_rejected", 0)
    public_acceptance_reject_reasons: dict[str, int] = pp.get("public_acceptance_reject_reasons", {})
    public_rejected_url_sample: tuple = pp.get("public_rejected_url_sample", ())

    # top_public_reject_reason: most common rejection reason by count
    top_public_reject_reason: str | None = None
    if public_acceptance_reject_reasons:
        top_public_reject_reason = max(
            public_acceptance_reject_reasons,
            key=lambda k: public_acceptance_reject_reasons[k]
        )

    # F207M: Nonfeed starvation detection
    # Extract timing fields
    tt = timing_truth or {}
    windup_lead_requested_s: float | None = tt.get("windup_lead_requested_s")
    windup_lead_observed_s: float | None = tt.get("windup_lead_observed_s")
    active_window_budget_s: int | None = tt.get("active_window_budget_s")
    active_runtime_occurred: bool = tt.get("active_runtime_occurred", False)

    # F207Q: Pre-windup barrier telemetry from acquisition_strategy
    as_dict = acquisition_strategy or {}
    prewindup_barrier_checked: bool = as_dict.get("prewindup_barrier_checked", False)
    prewindup_barrier_satisfied: bool = as_dict.get("prewindup_barrier_satisfied", False)
    prewindup_required_lanes: list[str] = as_dict.get("prewindup_required_lanes", [])
    prewindup_attempted_lanes: list[str] = as_dict.get("prewindup_attempted_lanes", [])
    prewindup_skipped_lanes: dict[str, str] = as_dict.get("prewindup_skipped_lanes", {})
    windup_delayed_for_nonfeed: bool = as_dict.get("windup_delayed_for_nonfeed", False)
    nonfeed_scheduler_gap_resolved: bool | None = as_dict.get("nonfeed_scheduler_gap_resolved", None)
    source_family_outcomes: list[dict] | None = as_dict.get("source_family_outcomes")

    # F207S: Windup guard observation telemetry
    wg = windup_guard_observation or {}

    # F207T: Return guard observation telemetry
    # Return guard fires at scheduler return path (ACTIVE→WINDUP), distinct from windup_guard
    # which fires at the windup_lead boundary. The key distinction:
    # - windup_guard_call_count=0 → windup callsite never reached (windup_guard not called)
    # - return_guard_checked=false → scheduler return guard never reached (return_guard not called)
    # Both must be checked to distinguish windup miss from return-guard miss.
    rg = return_guard_observation or {}

    # nonfeed_eligible_families: nonfeed families that exist in branch_mix
    nonfeed_eligible_families: list[str] = []
    nonfeed_skipped_reasons: dict[str, str] = {}
    if "public_findings" in branch_mix or "public_branch_timed_out" in rt:
        nonfeed_eligible_families.append("public")
    if "ct_findings" in branch_mix or "ct_branch_timed_out" in rt:
        nonfeed_eligible_families.append("ct")

    # Detection rule (F207M):
    # If PASS_VALID_CAPABILITY_RUN AND nonfeed_attempted_families empty AND
    # active_runtime_occurred true AND feed_findings > 0 AND both public/ct NOT timed out
    # THEN starvation suspected — early windup consumed the window before nonfeed dispatch
    # F207Q: suppress starvation false positive when barrier was checked and satisfied
    nonfeed_starvation_suspected: bool = False
    nonfeed_starvation_reason: str | None = None
    nonfeed_findings = public_findings + ct_findings
    starvation_suppressed = prewindup_barrier_checked and prewindup_barrier_satisfied
    if (
        run_quality_verdict == RunQualityVerdict.PASS_VALID_CAPABILITY_RUN.value
        and not nonfeed_attempted_families
        and active_runtime_occurred
        and feed_findings > 0
        and nonfeed_findings == 0
        and not starvation_suppressed
    ):
        public_not_timed = not rt.get("public_branch_timed_out", False)
        ct_not_timed = not rt.get("ct_branch_timed_out", False)
        if public_not_timed and ct_not_timed:
            nonfeed_starvation_suspected = True
            nonfeed_starvation_reason = "early_windup_or_scheduler_order"

    # F210D: Wallclock budget enforcement
    # Tolerance: max(planned * 1.10, planned + 30s).
    # Fires when verdict is PASS_VALID_CAPABILITY_RUN (preserve hardware_constrained priority)
    # OR when verdict is already FAIL_WALLCLOCK_BUDGET_EXCEEDED (show excess in KPI).
    wallclock_budget_exceeded = False
    wallclock_budget_excess_s: float | None = None
    wallclock_tolerance_s: float | None = None
    _wallclock_gate = run_quality_verdict in (
        RunQualityVerdict.PASS_VALID_CAPABILITY_RUN.value,
        RunQualityVerdict.FAIL_WALLCLOCK_BUDGET_EXCEEDED.value,
    )
    if (
        _wallclock_gate
        and planned_duration_s is not None
        and actual_duration_s is not None
    ):
        tolerance_s = max(planned_duration_s * 1.10, planned_duration_s + 30.0)
        wallclock_tolerance_s = tolerance_s
        if actual_duration_s > tolerance_s:
            wallclock_budget_exceeded = True
            wallclock_budget_excess_s = round(actual_duration_s - tolerance_s, 3)

    # F212C: Scheduler hard deadline enforcement telemetry
    # Parsed from runtime_truth to distinguish scheduler-enforced deadline from benchmark-observed overrun
    hard_deadline_checked_count: int = rt.get("hard_deadline_checked_count", 0)
    hard_deadline_exceeded: bool | None = rt.get("hard_deadline_exceeded", None)
    hard_deadline_exceeded_at_cycle: int | None = rt.get("hard_deadline_exceeded_at_cycle", None)
    hard_deadline_remaining_s_at_exit: float | None = rt.get("hard_deadline_remaining_s_at_exit", None)
    scheduler_deadline_checks: int = hard_deadline_checked_count
    scheduler_deadline_enforced: bool = hard_deadline_checked_count > 0 and hard_deadline_exceeded is not None
    scheduler_deadline_exit_path: str = (scheduler_exit or {}).get("exit_path", "") if hard_deadline_checked_count > 0 else ""

    # next_action + next_action_detail (F207K, F207M, F207Q, F210D, F212C)
    next_action, next_action_detail = _derive_next_action(
        status=status,
        is_memory_gate_abort=is_memory_gate_abort,
        nonfeed_accepted_findings=nonfeed_accepted_findings,
        public_fetch_attempted=public_fetch_attempted,
        public_findings=public_findings,
        feed_findings=feed_findings,
        total_findings=total_findings,
        ct_findings=ct_findings,
        runtime_truth=rt,
        feed_dominance_score=feed_dominance_score,
        top_public_reject_reason=top_public_reject_reason,
        nonfeed_starvation_suspected=nonfeed_starvation_suspected,
        prewindup_barrier_checked=prewindup_barrier_checked,
        prewindup_barrier_satisfied=prewindup_barrier_satisfied,
        prewindup_required_lanes=prewindup_required_lanes,
        prewindup_attempted_lanes=prewindup_attempted_lanes,
        acquisition_strategy=as_dict,
        return_guard_observation=rg,
        scheduler_exit=scheduler_exit,
        acquisition_terminality_checked=acquisition_terminality_checked,
        acquisition_terminality_satisfied=acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=list(acquisition_terminality_missing_lanes) if acquisition_terminality_missing_lanes is not None else None,
        run_quality_verdict=run_quality_verdict,
        # F209B: Acquisition prelude telemetry
        acquisition_prelude_checked=acquisition_prelude_checked,
        acquisition_prelude_ran=acquisition_prelude_ran,
        acquisition_prelude_required_lanes=list(acquisition_prelude_required_lanes) if acquisition_prelude_required_lanes is not None else None,
        acquisition_prelude_terminal_lanes=list(acquisition_prelude_terminal_lanes) if acquisition_prelude_terminal_lanes is not None else None,
        acquisition_prelude_missing_lanes=list(acquisition_prelude_missing_lanes) if acquisition_prelude_missing_lanes is not None else None,
        acquisition_prelude_skipped_lanes=acquisition_prelude_skipped_lanes,
        acquisition_prelude_errors=acquisition_prelude_errors,
        acquisition_prelude_duration_s=acquisition_prelude_duration_s,
        acquisition_prelude_reason=acquisition_prelude_reason,
        windup_guard_observation=wg,
        # F212C: Scheduler deadline enforcement telemetry
        scheduler_deadline_enforced=scheduler_deadline_enforced,
        scheduler_deadline_checks=scheduler_deadline_checks,
    )

    # F208I: terminality_quality_verdict + failure reasons for domain queries
    # terminality_failure_reasons explains WHY a terminality verdict was assigned
    terminality_quality_verdict: str | None = None
    terminality_failure_reasons: list[str] = []
    _is_domain = _is_active_domain_query(runtime_truth, profile_verdict)
    if _is_domain:
        _base_verdict = run_quality_verdict or ""
        if _base_verdict in (
            RunQualityVerdict.FAIL_TERMINALITY_NOT_CHECKED.value,
            RunQualityVerdict.FAIL_TERMINALITY_UNSATISFIED.value,
            RunQualityVerdict.FAIL_MISSING_SOURCE_OUTCOMES.value,
            RunQualityVerdict.FAIL_SCHEDULER_EXIT_MISSING.value,
        ):
            terminality_quality_verdict = _base_verdict
            # Collect specific failure reasons
            if not (acquisition_report and isinstance(acquisition_report, dict) and acquisition_report.get("schema_version")):
                terminality_failure_reasons.append("acquisition_report.schema_version missing")
            if acquisition_terminality_checked is not True:
                terminality_failure_reasons.append(
                    f"acquisition_terminality_checked={acquisition_terminality_checked!r}, expected True"
                )
            if acquisition_terminality_satisfied is not True:
                terminality_failure_reasons.append(
                    f"acquisition_terminality_satisfied={acquisition_terminality_satisfied!r}, expected True"
                )
            if not _has_terminal_source_outcomes(acquisition_strategy):
                terminality_failure_reasons.append("source_family_outcomes missing or empty")
            if not _has_scheduler_exit_path(scheduler_exit):
                terminality_failure_reasons.append("scheduler_exit_path missing or empty")

    acquisition_report_schema_version: str | None = None
    if acquisition_report and isinstance(acquisition_report, dict):
        acquisition_report_schema_version = acquisition_report.get("schema_version")

    return {
        "total_findings": total_findings,
        "accepted_findings": accepted_findings,
        "cycles_completed": cycles_completed,
        "findings_per_min": findings_per_min,
        "primary_signal_source": primary_signal_source,
        "branch_accepted_counts": branch_accepted_counts,
        "lane_execution_counts": lane_execution_counts,
        "source_family_counts": source_family_counts,
        "source_family_outcomes_display": source_family_outcomes_display,
        "nonfeed_attempted_families": nonfeed_attempted_families,
        "nonfeed_accepted_findings": nonfeed_accepted_findings,
        "public_fetch_attempted": public_fetch_attempted,
        "public_acceptance_attempted": public_acceptance_attempted,
        "public_acceptance_accepted": public_acceptance_accepted,
        "public_acceptance_rejected": public_acceptance_rejected_count,
        "public_acceptance_reject_reasons": public_acceptance_reject_reasons,
        "top_public_reject_reason": top_public_reject_reason,
        "public_rejected_url_sample": public_rejected_url_sample,
        "feed_dominance_score": feed_dominance_score,
        "feed_balance_recommendation": feed_balance_recommendation,
        "estimated_per_source_soft_cap": estimated_per_source_soft_cap,
        "dominant_feed_source": dominant_feed_source,
        "dominant_feed_share_pct": dominant_feed_share_pct,
        "run_quality_verdict": run_quality_verdict,
        "hardware_constrained": hardware_constrained,
        # F210D: Wallclock budget enforcement
        "wallclock_budget_exceeded": wallclock_budget_exceeded,
        "wallclock_budget_excess_s": wallclock_budget_excess_s,
        "wallclock_tolerance_s": wallclock_tolerance_s,
        "next_action": next_action,
        "next_action_detail": next_action_detail,
        # F212R: Action family for runtime budget issues (backward-compat for F210D/F211D)
        # Reuses wallclock_budget_exceeded computed at line 1273
        "runtime_budget_action_family": (
            "fix_runtime_budget_enforcement"
            if wallclock_budget_exceeded
            else None
        ),
        # F212R: Detailed deadline action when wallclock exceeded
        # Reuses wallclock_budget_exceeded computed at line 1273
        "deadline_action_detail": (
            next_action
            if wallclock_budget_exceeded
            else None
        ),
        # F207M: Nonfeed starvation
        "nonfeed_starvation_suspected": nonfeed_starvation_suspected,
        "nonfeed_starvation_reason": nonfeed_starvation_reason,
        "windup_lead_requested_s": windup_lead_requested_s,
        "windup_lead_observed_s": windup_lead_observed_s,
        "active_window_budget_s": active_window_budget_s,
        "nonfeed_eligible_families": nonfeed_eligible_families,
        "nonfeed_skipped_reasons": nonfeed_skipped_reasons,
        # F207Q: Pre-windup barrier
        "prewindup_barrier_checked": prewindup_barrier_checked,
        "prewindup_barrier_satisfied": prewindup_barrier_satisfied,
        "prewindup_required_lanes": prewindup_required_lanes,
        "prewindup_attempted_lanes": prewindup_attempted_lanes,
        "prewindup_skipped_lanes": prewindup_skipped_lanes,
        "windup_delayed_for_nonfeed": windup_delayed_for_nonfeed,
        "nonfeed_scheduler_gap_resolved": nonfeed_scheduler_gap_resolved,
        # F210B: Prefer explicit_source_family_outcomes (ground truth) over the
        # acquisition_strategy local variable (sanitized subset).
        "source_family_outcomes": (
            explicit_source_family_outcomes
            if explicit_source_family_outcomes is not None
            else source_family_outcomes
        ),
        # F207S: Windup guard observation
        "windup_guard_call_count": wg.get("call_count", 0),
        "windup_guard_callback_supplied_count": wg.get("callback_supplied_count", 0),
        "windup_guard_callback_executed_count": wg.get("callback_executed_count", 0),
        "windup_guard_last_reason": wg.get("last_reason", ""),
        "windup_guard_last_phase": wg.get("last_phase", ""),
        "windup_guard_last_allowed": wg.get("last_allowed"),
        # F207T: Return guard observation
        "return_guard_checked": rg.get("checked", False),
        "return_guard_required_lanes": rg.get("required_lanes", []),
        "return_guard_satisfied": rg.get("satisfied", False),
        "return_guard_delayed_for_nonfeed": rg.get("delayed_for_nonfeed", False),
        "return_guard_block_reason": rg.get("block_reason", ""),
        "return_guard_attempted_lanes": rg.get("attempted_lanes", []),
        "return_guard_skipped_lanes": rg.get("skipped_lanes", {}),
        "return_guard_errors": rg.get("errors", []),
        # F207V-B: Scheduler exit path
        "scheduler_exit_path": (scheduler_exit or {}).get("exit_path", ""),
        "scheduler_exit_reason": (scheduler_exit or {}).get("exit_reason", ""),
        "scheduler_exit_phase": (scheduler_exit or {}).get("exit_phase", ""),
        "scheduler_exit_cycle": (scheduler_exit or {}).get("exit_cycle", ""),
        "scheduler_exit_elapsed_s": (scheduler_exit or {}).get("elapsed_s", ""),
        "scheduler_exit_guard_checked": (scheduler_exit or {}).get("guard_checked", ""),
        "scheduler_exit_guard_required": (scheduler_exit or {}).get("guard_required", ""),
        "scheduler_exit_guard_satisfied": (scheduler_exit or {}).get("guard_satisfied", ""),
        # F212C: Scheduler hard deadline enforcement
        "scheduler_deadline_enforced": scheduler_deadline_enforced,
        "scheduler_deadline_checks": scheduler_deadline_checks,
        "scheduler_deadline_exit_path": scheduler_deadline_exit_path,
        "hard_deadline_checked_count": hard_deadline_checked_count,
        "hard_deadline_exceeded": hard_deadline_exceeded,
        "hard_deadline_exceeded_at_cycle": hard_deadline_exceeded_at_cycle,
        "hard_deadline_remaining_s_at_exit": hard_deadline_remaining_s_at_exit,
        # F208F: active300 acquisition terminality wiring
        "acquisition_terminality_checked": bool(acquisition_terminality_checked) if acquisition_terminality_checked is not None else None,
        "acquisition_terminality_satisfied": bool(acquisition_terminality_satisfied) if acquisition_terminality_satisfied is not None else None,
        "acquisition_terminality_missing_lanes": list(acquisition_terminality_missing_lanes) if acquisition_terminality_missing_lanes is not None else None,
        "acquisition_terminality_report": acquisition_terminality_report,
        # F208I: terminality quality verdict and failure reasons for domain queries
        "terminality_quality_verdict": terminality_quality_verdict,
        "terminality_failure_reasons": terminality_failure_reasons,
        "acquisition_report_schema_version": acquisition_report_schema_version,
        # F209B: Acquisition prelude telemetry
        "acquisition_prelude_checked": bool(acquisition_prelude_checked) if acquisition_prelude_checked is not None else None,
        "acquisition_prelude_ran": bool(acquisition_prelude_ran) if acquisition_prelude_ran is not None else None,
        "acquisition_prelude_required_lanes": list(acquisition_prelude_required_lanes) if acquisition_prelude_required_lanes is not None else None,
        "acquisition_prelude_terminal_lanes": list(acquisition_prelude_terminal_lanes) if acquisition_prelude_terminal_lanes is not None else None,
        "acquisition_prelude_missing_lanes": list(acquisition_prelude_missing_lanes) if acquisition_prelude_missing_lanes is not None else None,
        "acquisition_prelude_skipped_lanes": acquisition_prelude_skipped_lanes,
        "acquisition_prelude_errors": acquisition_prelude_errors,
        "acquisition_prelude_duration_s": acquisition_prelude_duration_s,
        "acquisition_prelude_reason": acquisition_prelude_reason,
        # F214D: CT Live Bridge Loss Telemetry
        "ct_loss_stage": lane_verdict.get("ct_loss_stage", "no_loss") if isinstance(lane_verdict, dict) else "no_loss",
        "ct_bridge_invoked": lane_verdict.get("ct_bridge_invoked", False) if isinstance(lane_verdict, dict) else False,
        "ct_raw_sample_count": lane_verdict.get("ct_raw_sample_count", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_candidates_built": lane_verdict.get("ct_candidates_built", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_bridge_rejections_count": lane_verdict.get("ct_bridge_rejections_count", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_candidates_accumulated": lane_verdict.get("ct_candidates_accumulated", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_candidates_stored": lane_verdict.get("ct_candidates_stored", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_storage_rejected": lane_verdict.get("ct_storage_rejected", 0) if isinstance(lane_verdict, dict) else 0,
        # F225A: Claims runtime surface telemetry
        "claims_extracted_count": (claims_runtime_status or {}).get("claims_extracted_count", 0) if claims_runtime_status else 0,
        "claims_polarity_mix": {
            "positive": (claims_runtime_status or {}).get("claims_positive_count", 0) if claims_runtime_status else 0,
            "negative": (claims_runtime_status or {}).get("claims_negative_count", 0) if claims_runtime_status else 0,
            "neutral": (claims_runtime_status or {}).get("claims_neutral_count", 0) if claims_runtime_status else 0,
        },
        "claims_packets_with_claims": (claims_runtime_status or {}).get("claims_extraction_packets_with_claims", 0) if claims_runtime_status else 0,
        # F225C: Discovery Provider Debug Surface
        # Surfaced from acquisition_report.provider_status_debug if present
        "discovery_provider_status_debug": _derive_discovery_provider_status_debug(acquisition_report),
        "discovery_selected_providers": _derive_discovery_selected_providers(acquisition_report),
        "discovery_skipped_providers": _derive_discovery_skipped_providers(acquisition_report),
        "discovery_stub_providers": _derive_discovery_stub_providers(acquisition_report),
        "discovery_not_wired_providers": _derive_discovery_not_wired_providers(acquisition_report),
        # F215A: Missing canonical fields — tracks which canonical fields were absent
        # External tools should check this list to know when benchmark used fallback heuristics
        "missing_canonical_fields": (
            ["source_family_outcomes"] if not _sfo_has_canonical else []
        ),
    }


def _derive_discovery_provider_status_debug(acquisition_report: dict | None) -> list[dict]:
    """
    Extract and serialize provider_status_debug from acquisition_report.

    F225C: Surfaces discovery provider plan truth in live KPI.
    Returns JSON-safe list with provider, state (string), selected, reason.
    """
    if not acquisition_report or not isinstance(acquisition_report, dict):
        return []

    psd = acquisition_report.get("provider_status_debug")
    if not isinstance(psd, list):
        return []

    result = []
    for entry in psd:
        if isinstance(entry, dict):
            state = entry.get("state")
            if hasattr(state, "value"):
                state = state.value
            result.append({
                "provider": entry.get("provider", ""),
                "state": str(state) if state is not None else "",
                "selected": bool(entry.get("selected", False)),
                "reason": entry.get("reason", ""),
            })
    return result


def _derive_discovery_selected_providers(acquisition_report: dict | None) -> list[str]:
    """Extract selected providers (selected=True) from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e["provider"] for e in psd if e.get("selected")]


def _derive_discovery_skipped_providers(acquisition_report: dict | None) -> list[str]:
    """Extract skipped providers (selected=False) from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e["provider"] for e in psd if not e.get("selected")]


def _derive_discovery_stub_providers(acquisition_report: dict | None) -> list[str]:
    """Extract ADVISORY_STUB providers from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e["provider"] for e in psd if e.get("state") == "advisory_stub"]


def _derive_discovery_not_wired_providers(acquisition_report: dict | None) -> list[str]:
    """Extract NOT_WIRED providers from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e["provider"] for e in psd if e.get("state") == "not_wired"]


def _was_family_attempted(runtime_truth: dict, family: str) -> bool:
    """Return True if a source family was attempted (not just present in branch_mix)."""
    # Detection heuristic: family has >0 findings OR branch timed out (indicates it ran)
    branch_mix = runtime_truth.get("branch_mix", {})
    family_findings = branch_mix.get(f"{family}_findings", 0)
    if family_findings > 0:
        return True
    # Also true if branch timed out for this family
    timed_out = runtime_truth.get(f"{family}_branch_timed_out", False)
    return timed_out


@dataclass(frozen=True)
class NextActionInput:
    """All inputs needed by _derive_next_action rule helpers.

    Frozen dataclass ensures rule helpers are pure and cannot mutate inputs.
    All fields have explicit defaults so callers can pass by name.
    """
    status: MeasurementStatus
    is_memory_gate_abort: bool
    nonfeed_accepted_findings: int
    public_fetch_attempted: bool
    public_findings: int
    feed_findings: int
    total_findings: int
    ct_findings: int
    runtime_truth: dict
    # Optional fields with defaults
    feed_dominance_score: float | None = None
    top_public_reject_reason: str | None = None
    nonfeed_starvation_suspected: bool = False
    prewindup_barrier_checked: bool = False
    prewindup_barrier_satisfied: bool = False
    prewindup_required_lanes: list[str] | None = None
    prewindup_attempted_lanes: list[str] | None = None
    acquisition_strategy: dict | None = None
    return_guard_observation: dict | None = None
    scheduler_exit: dict | None = None
    acquisition_terminality_checked: bool | None = None
    acquisition_terminality_satisfied: bool | None = None
    acquisition_terminality_missing_lanes: list[str] | None = None
    run_quality_verdict: str | None = None
    acquisition_prelude_checked: bool | None = None
    acquisition_prelude_ran: bool | None = None
    acquisition_prelude_required_lanes: list[str] | None = None
    acquisition_prelude_terminal_lanes: list[str] | None = None
    acquisition_prelude_missing_lanes: list[str] | None = None
    acquisition_prelude_skipped_lanes: dict | None = None
    acquisition_prelude_errors: dict | None = None
    acquisition_prelude_duration_s: float | None = None
    acquisition_prelude_reason: str | None = None
    windup_guard_observation: dict | None = None
    scheduler_deadline_enforced: bool = False
    scheduler_deadline_checks: int = 0


# ----------------------------------------------------------------------
# Rule helpers — each returns tuple[str, str] | None
# Order matches the priority dispatch in _derive_next_action
# ----------------------------------------------------------------------


def _rule_wallclock_enforcement(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rule 0: Wallclock budget exceeded — distinguish scheduler enforcement vs benchmark observation."""
    if inp.run_quality_verdict == RunQualityVerdict.FAIL_WALLCLOCK_BUDGET_EXCEEDED.value:
        if not inp.scheduler_deadline_enforced:
            return ("fix_scheduler_deadline_enforcement", None)
        return ("fix_branch_tail_timeout", None)
    return None


def _rule0b_memory_or_swap_gate(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rule 1 (memory gate): Critical system state — clean memory."""
    if inp.is_memory_gate_abort:
        return ("clean_memory", None)
    return None


def _rule0g_prewindup_barrier(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rule 0 / 0b / 0c / 0d: Pre-windup barrier rules (F207Q)."""
    prewindup_required_lanes = inp.prewindup_required_lanes or []
    prewindup_attempted_lanes = inp.prewindup_attempted_lanes or []

    has_barrier_telemetry = inp.acquisition_strategy is not None and bool(inp.acquisition_strategy)

    # Rule 0: barrier telemetry present but never checked
    if (has_barrier_telemetry
            and inp.runtime_truth.get("cycles_started", 0) > 0
            and inp.runtime_truth.get("primary_signal_source")
            and not inp.prewindup_barrier_checked):
        return ("fix_prewindup_barrier_not_called", None)

    # Rule 0b: barrier checked but NOT satisfied
    if inp.prewindup_barrier_checked and not inp.prewindup_barrier_satisfied:
        return ("fix_required_lane_terminal_state", None)

    # Rule 0c: barrier checked+satisfied but ALL required lanes missing from attempted_lanes
    if (inp.prewindup_barrier_checked
            and inp.prewindup_barrier_satisfied
            and prewindup_required_lanes
            and not any(lane in prewindup_attempted_lanes for lane in prewindup_required_lanes)
            and inp.ct_findings == 0
            and inp.public_findings == 0):
        return ("fix_report_mapping", None)

    # Rule 0d: barrier checked+satisfied — starvation was false positive, fall through
    return None


def _rule_profile_propagation(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules -1, -1b, -1d, -1e: Return guard and scheduler exit path rules (F207T, F207V-B, F208M)."""
    wg = inp.windup_guard_observation or {}
    wg_supplied = wg.get("callback_supplied_count", 0)
    wg_exec = wg.get("callback_executed_count", 0)

    # Windup callback wired but never executed
    if wg_supplied > 0 and wg_exec == 0:
        return ("fix_callback_execution", None)

    rg = inp.return_guard_observation or {}
    has_rg_telemetry = bool(rg)
    rg_checked: bool = bool(rg.get("checked")) if has_rg_telemetry else False
    rg_satisfied: bool = rg.get("satisfied", False) if has_rg_telemetry else False

    # Rule -1: return guard telemetry present, checked=False
    if (has_rg_telemetry
            and rg_checked is False
            and inp.runtime_truth.get("cycles_started", 0) > 0
            and inp.runtime_truth.get("primary_signal_source")
            and inp.feed_findings > 0
            and inp.ct_findings == 0
            and inp.public_findings == 0):
        return ("fix_scheduler_return_guard_not_called", None)

    # Rule -1b: return guard checked but NOT satisfied
    if has_rg_telemetry and rg_checked and not rg_satisfied:
        return ("fix_return_guard_terminal_state", None)

    # Windup callback NOT supplied but guard was called
    wg_call_count = wg.get("windup_guard_call_count", 0)
    if wg_supplied == 0 and wg_call_count > 0:
        return ("fix_callback_wiring", None)

    # Rule -1d: scheduler_exit dict present but exit_path missing/empty
    se = inp.scheduler_exit
    se_path = se.get("exit_path") if se is not None else None
    if se is not None and not se_path:
        return ("add_scheduler_exit_tracer", None)

    # Rule -1e: feed-only + nonfeed eligible + return_guard not checked + non-run_complete path
    if (se is not None
            and se_path
            and se_path != "run_complete"
            and not rg_checked
            and inp.runtime_truth.get("cycles_started", 0) > 0
            and inp.runtime_truth.get("primary_signal_source")
            and inp.feed_findings > 0
            and inp.ct_findings == 0
            and inp.public_findings == 0):
        return (f"patch_scheduler_exit_path:{se_path}", None)

    return None


def _rule_terminality(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 0e, 0f: Acquisition terminality wiring (F208F, F208M)."""
    # Rule 0e: active300 domain query has eligible PUBLIC or CT but terminality never checked
    if (inp.acquisition_terminality_checked is False
            and inp.runtime_truth.get("cycles_started", 0) > 0
            and inp.runtime_truth.get("primary_signal_source")
            and (inp.ct_findings > 0 or inp.public_findings > 0)):
        return ("fix_terminality_wiring", None)

    # Rule 0f: terminality checked but NOT satisfied — mandatory lanes missing terminal state
    if inp.acquisition_terminality_checked is True and inp.acquisition_terminality_satisfied is False:
        missing = inp.acquisition_terminality_missing_lanes or []
        if missing:
            return (f"fix_terminality_missing_lane:{missing[0]}", f"missing:{','.join(missing)}")
        return ("fix_terminality_wiring", None)

    return None


def _rule_provider_surface(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 0g, 0h, 0i: Acquisition prelude telemetry rules (F209B)."""
    _is_domain = inp.runtime_truth.get("cycles_started", 0) > 0 and inp.runtime_truth.get("primary_signal_source")
    _has_nonfeed = inp.ct_findings > 0 or inp.public_findings > 0

    # Rule 0g: active300 domain but acquisition_prelude never checked
    if (_is_domain and _has_nonfeed and inp.acquisition_prelude_checked is not True):
        if inp.is_memory_gate_abort:
            return ("clean_memory", None)
        return ("wire_acquisition_prelude_into_run_path", None)

    # Rule 0h: prelude checked but required lanes are missing from terminal lanes
    if inp.acquisition_prelude_checked is True and inp.acquisition_prelude_missing_lanes:
        missing = list(inp.acquisition_prelude_missing_lanes) if inp.acquisition_prelude_missing_lanes else []
        if missing:
            if inp.nonfeed_starvation_suspected:
                return ("fix_nonfeed_scheduler_order", None)
            return (f"fix_acquisition_prelude_missing_lane:{missing[0]}", f"missing:{','.join(missing)}")

    # Rule 0i: terminality not checked but prelude terminal lanes are present
    _prelude_terminal = list(inp.acquisition_prelude_terminal_lanes) if inp.acquisition_prelude_terminal_lanes else []
    if (_is_domain and _has_nonfeed and inp.acquisition_terminality_checked is not True and _prelude_terminal):
        if inp.is_memory_gate_abort:
            return ("clean_memory", None)
        return ("fix_terminality_report_from_prelude", None)

    return None


def _rule_quality_gate(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 1 (starvation), 1 (memory gate): Critical system quality gates."""
    # Rule 1: nonfeed starvation — must fire early so operators see it
    if inp.nonfeed_starvation_suspected:
        return ("fix_nonfeed_scheduler_order", None)
    # Rule 1: memory gate abort (already handled in _rule0b_memory_or_swap_gate,
    # but kept here for priority order — memory gate fires before public rejection)
    if inp.is_memory_gate_abort:
        return ("clean_memory", None)
    return None


def _rule_default(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 3, 2, 5, 4, 6, 7, default: Feed/public/ct/quality default rules."""
    # Rule 3: public attempted but accepted=0 (only when ct was NOT attempted)
    # Must come BEFORE Rule 2 so public rejection reason surfaces even when feed dominance >= 0.7
    if (inp.public_fetch_attempted and inp.nonfeed_accepted_findings == 0
            and inp.feed_findings == inp.total_findings
            and not _was_family_attempted(inp.runtime_truth, "ct")):
        return ("inspect_public_reject_reasons", inp.top_public_reject_reason)

    # Rule 2: feed dominance high, BOTH public AND ct were attempted, nonfeed accepted=0 → inspect
    if inp.feed_dominance_score is not None and inp.feed_dominance_score >= 0.7:
        if inp.nonfeed_accepted_findings == 0:
            both_attempted = inp.public_fetch_attempted and _was_family_attempted(inp.runtime_truth, "ct")
            if both_attempted:
                return ("inspect_nonfeed_rejection_or_raw_counts", None)
            return ("improve_nonfeed_lanes", None)

    # Rule 5: ct attempted but raw=0
    if inp.ct_findings == 0 and _was_family_attempted(inp.runtime_truth, "ct"):
        return ("inspect_ct_query_domain", None)

    # Rule 4: feed-only with public findings present AND ct was attempted but zero ct findings
    if (inp.total_findings > 0 and inp.feed_findings == inp.total_findings
            and _was_family_attempted(inp.runtime_truth, "ct")
            and inp.public_findings > 0):
        return ("improve_nonfeed_lanes", None)

    # Rule 6: valid multi-source yield
    if inp.nonfeed_accepted_findings > 0:
        return ("run_active600_or_targeted_query", None)

    # Rule 7: no findings but sprint completed — inspect
    if inp.total_findings == 0 and inp.status == MeasurementStatus.COMPLETED:
        return ("inspect_empty_run", None)

    # Default: no action determinable
    return None


def _derive_next_action(
    status: MeasurementStatus,
    is_memory_gate_abort: bool,
    nonfeed_accepted_findings: int,
    public_fetch_attempted: bool,
    public_findings: int,
    feed_findings: int,
    total_findings: int,
    ct_findings: int,
    runtime_truth: dict,
    feed_dominance_score: float | None = None,
    top_public_reject_reason: str | None = None,
    nonfeed_starvation_suspected: bool = False,
    prewindup_barrier_checked: bool = False,
    prewindup_barrier_satisfied: bool = False,
    prewindup_required_lanes: list[str] | None = None,
    prewindup_attempted_lanes: list[str] | None = None,
    acquisition_strategy: dict | None = None,
    return_guard_observation: dict | None = None,
    scheduler_exit: dict | None = None,
    acquisition_terminality_checked: bool | None = None,
    acquisition_terminality_satisfied: bool | None = None,
    acquisition_terminality_missing_lanes: list[str] | None = None,
    run_quality_verdict: str | None = None,
    acquisition_prelude_checked: bool | None = None,
    acquisition_prelude_ran: bool | None = None,
    acquisition_prelude_required_lanes: list[str] | None = None,
    acquisition_prelude_terminal_lanes: list[str] | None = None,
    acquisition_prelude_missing_lanes: list[str] | None = None,
    acquisition_prelude_skipped_lanes: dict | None = None,
    acquisition_prelude_errors: dict | None = None,
    acquisition_prelude_duration_s: float | None = None,
    acquisition_prelude_reason: str | None = None,
    windup_guard_observation: dict | None = None,
    scheduler_deadline_enforced: bool = False,
    scheduler_deadline_checks: int = 0,
) -> tuple[str, str | None]:
    """Derive (next_action, next_action_detail) based on sprint outcome rules.

    Thin priority dispatcher — delegates to NextActionInput + rule helpers.
    Rule order matters — public rejection (Rule 3) checked BEFORE feed-dominance (Rule 2)
    so operators see WHY public_findings=0 before generic nonfeed recommendations.
    F207M: starvation rule fires before most other rules to surface scheduler-order fixes.
    F207Q: prewindup barrier rules fire before starvation to surface barrier-not-called issues.
    F208M: terminality missing lanes => fix_terminality_missing_lane:<lane>
    F208M: windup callback supplied>0 executed=0 => fix_callback_execution
    F208M: return guard checked+satisfied => never suggest fix_return_guard_report_mapping
    F208M: scheduler_exit run_complete => never suggest add_scheduler_exit_tracer
    F212C: scheduler deadline enforcement distinguishes scheduler-enforced deadline from benchmark-observed overrun.
    """
    inp = NextActionInput(
        status=status,
        is_memory_gate_abort=is_memory_gate_abort,
        nonfeed_accepted_findings=nonfeed_accepted_findings,
        public_fetch_attempted=public_fetch_attempted,
        public_findings=public_findings,
        feed_findings=feed_findings,
        total_findings=total_findings,
        ct_findings=ct_findings,
        runtime_truth=runtime_truth,
        feed_dominance_score=feed_dominance_score,
        top_public_reject_reason=top_public_reject_reason,
        nonfeed_starvation_suspected=nonfeed_starvation_suspected,
        prewindup_barrier_checked=prewindup_barrier_checked,
        prewindup_barrier_satisfied=prewindup_barrier_satisfied,
        prewindup_required_lanes=prewindup_required_lanes,
        prewindup_attempted_lanes=prewindup_attempted_lanes,
        acquisition_strategy=acquisition_strategy,
        return_guard_observation=return_guard_observation,
        scheduler_exit=scheduler_exit,
        acquisition_terminality_checked=acquisition_terminality_checked,
        acquisition_terminality_satisfied=acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=acquisition_terminality_missing_lanes,
        run_quality_verdict=run_quality_verdict,
        acquisition_prelude_checked=acquisition_prelude_checked,
        acquisition_prelude_ran=acquisition_prelude_ran,
        acquisition_prelude_required_lanes=acquisition_prelude_required_lanes,
        acquisition_prelude_terminal_lanes=acquisition_prelude_terminal_lanes,
        acquisition_prelude_missing_lanes=acquisition_prelude_missing_lanes,
        acquisition_prelude_skipped_lanes=acquisition_prelude_skipped_lanes,
        acquisition_prelude_errors=acquisition_prelude_errors,
        acquisition_prelude_duration_s=acquisition_prelude_duration_s,
        acquisition_prelude_reason=acquisition_prelude_reason,
        windup_guard_observation=windup_guard_observation,
        scheduler_deadline_enforced=scheduler_deadline_enforced,
        scheduler_deadline_checks=scheduler_deadline_checks,
    )

    # Strict priority order — first non-None result wins
    for helper in [
        _rule_wallclock_enforcement,
        _rule0g_prewindup_barrier,
        _rule_profile_propagation,
        _rule_provider_surface,
        _rule_terminality,
        _rule_quality_gate,   # includes starvation + memory gate (starvation first in old code)
        _rule0b_memory_or_swap_gate,
        _rule_default,
    ]:
        result = helper(inp)
        if result is not None:
            return result

    return ("unknown", None)


def _stamp_live_kpi(result: LiveMeasurementResult) -> None:
    """Compute and stamp live_kpi onto result."""
    kpi = _derive_live_kpi(
        status=result.status,
        is_memory_gate_abort=(
            result.run_quality_verdict == RunQualityVerdict.ABORTED_MEMORY_GATE.value
        ),
        runtime_truth=result.runtime_truth,
        actual_duration_s=result.actual_duration_s,
        primary_signal_source=result.primary_signal_source,
        run_quality_verdict=result.run_quality_verdict,
        hardware_constrained=result.hardware_constrained,
        public_pipeline=result.public_pipeline,
        timing_truth=result.timing_truth,
        acquisition_strategy=result.acquisition_strategy,
        windup_guard_observation=getattr(result, "windup_guard_observation", None),
        return_guard_observation=getattr(result, "return_guard_observation", None),
        scheduler_exit=getattr(result, "scheduler_exit", None),
        acquisition_report=result.acquisition_report,
        profile_verdict=result.profile_verdict,
        acquisition_terminality_checked=getattr(result, "acquisition_terminality_checked", None),
        acquisition_terminality_satisfied=getattr(result, "acquisition_terminality_satisfied", None),
        acquisition_terminality_missing_lanes=getattr(result, "acquisition_terminality_missing_lanes", None),
        acquisition_terminality_report=getattr(result, "acquisition_terminality_report", None),
        # F210B: Explicit source_family_outcomes from full acquisition_report for lane-truth
        explicit_source_family_outcomes=(
            result.acquisition_report.get("source_family_outcomes")
            if result.acquisition_report and isinstance(result.acquisition_report, dict)
            else None
        ),
        # F209B: Acquisition prelude telemetry
        acquisition_prelude_checked=getattr(result, "acquisition_prelude_checked", None),
        acquisition_prelude_ran=getattr(result, "acquisition_prelude_ran", None),
        acquisition_prelude_required_lanes=getattr(result, "acquisition_prelude_required_lanes", None),
        acquisition_prelude_terminal_lanes=getattr(result, "acquisition_prelude_terminal_lanes", None),
        acquisition_prelude_missing_lanes=getattr(result, "acquisition_prelude_missing_lanes", None),
        acquisition_prelude_skipped_lanes=getattr(result, "acquisition_prelude_skipped_lanes", None),
        acquisition_prelude_errors=getattr(result, "acquisition_prelude_errors", None),
        acquisition_prelude_duration_s=getattr(result, "acquisition_prelude_duration_s", None),
        acquisition_prelude_reason=getattr(result, "acquisition_prelude_reason", None),
        # F210D: wallclock budget enforcement
        planned_duration_s=getattr(result, "planned_duration_s", None),
        # F225A: Claims runtime surface status
        claims_runtime_status=getattr(result, "claims_runtime_status", None),
    )
    result.live_kpi = kpi

    # F224B: Lazy import — only load research_quality_score when live_kpi is stamped.
    # This keeps --print-invocation-reality free of this heavy-ish module.
    from tools.research_quality_score import score_research_quality

    # F214C: Stamp research quality score into live_kpi
    # score_research_quality expects a dict with runtime_truth / live_kpi fields
    # F215E: Use actual result.hardware_constrained for scoring (not forced True).
    _rq_data = {
        "mode": "live",
        "findings_count": result.findings_count,
        "runtime_truth": result.runtime_truth or {},
        "live_kpi": kpi,
        "uma_post_swap_gib": result.uma_post_swap_gib,
    }
    _rq = score_research_quality(_rq_data)
    result.live_kpi["research_quality"] = _rq

    # F215A: Surface contract — quality_gate and research_quality_comparable at live_kpi top level
    result.live_kpi["quality_gate"] = _rq["quality_gate"]
    result.live_kpi["research_quality_comparable"] = _rq["research_quality_comparable"]


# ---------------------------------------------------------------------------
# Preflight mode
# ---------------------------------------------------------------------------

async def _run_preflight() -> LiveMeasurementResult:
    """
    Sample readiness/memory/profile metadata without calling run_sprint.
    Useful for checking whether Mac is ready after restart.
    """
    readiness = _check_readiness_artifacts()
    uma_pre = await _capture_uma()

    result = LiveMeasurementResult(
        measurement_id=_make_measurement_id(),
        sprint_id=None,
        mode=RunMode.PREFLIGHT,
        status=MeasurementStatus.PLANNED,
        start_time_iso=_now_iso(),
        end_time_iso=None,
        planned_duration_s=None,
        actual_duration_s=None,
        query="(preflight-only)",
        profile="preflight",
        uma_pre_used_gib=uma_pre.get("used_gib"),
        uma_pre_swap_gib=uma_pre.get("swap_gib"),
        uma_pre_state=uma_pre.get("state"),
        stabilization_seal_present=readiness.get("stabilization_seal", False),
        hermetic_regression_manifest_present=readiness.get("hermetic_regression_manifest", False),
        transport_authority_status_present=readiness.get("transport_authority_status", False),
        mlx_wired_limit_seal_present=readiness.get("mlx_wired_limit_seal", False),
        acquisition_profile=None,
    )

    # F2130: Runtime Authority — preflight does not invoke any sprint runtime
    result.runtime_authority_path = "dry_run_no_runtime"
    result.runtime_authority_module = None
    result.runtime_authority_function = None
    result.runtime_authority_is_canonical = False
    result.runtime_authority_evidence = {
        "mode": "preflight",
    }

    # Memory gate signal for preflight
    is_critical = _uma_state_is_critical_or_emergency(result.uma_pre_state)
    if is_critical:
        result.status = MeasurementStatus.ABORTED
        result.error = (
            f"[MEMORY GATE] UMA pre-state is '{result.uma_pre_state}' — "
            f"aborting live execution before sprint starts. "
            f"Resolve memory pressure and retry."
        )
        result.hardware_constrained = True
        result.comparable_result = False
        result.memory_state_pre = result.uma_pre_state
        result.swap_warning = result.uma_pre_swap_gib is not None and result.uma_pre_swap_gib > 0
        result.swap_gate_triggered = True
        # F220F: tiered swap telemetry
        result.swap_policy_tier = "hard_block"
        result.swap_gate_reason = f"memory gate abort: uma_state={result.uma_pre_state}, swap={result.uma_pre_swap_gib}GiB"
        result.recommended_next_profile = "none_until_memory_ok"
        result.recommended_operator_action = _MEMORY_GATE_OPERATOR_ACTION
        result.run_quality_verdict = RunQualityVerdict.ABORTED_MEMORY_GATE.value
        logging.warning("[PREFLIGHT] [MEMORY GATE] Memory critical: %s", result.uma_pre_state)
    else:
        result.hardware_constrained = False
        result.memory_state_pre = result.uma_pre_state
        result.swap_warning = uma_pre.get("swap_gib", 0) > 0
        # F212D: Swap gate — active300/active600 with high swap are hardware-constrained
        # smoke180 has no active runtime so swap doesn't distort wallclock comparability
        swap_gib = uma_pre.get("swap_gib", 0) or 0
        result.swap_gate_triggered = False
        # F220F: tiered swap policy — determine tier
        if swap_gib <= 2.0:
            result.swap_policy_tier = "clean"
            result.swap_gate_reason = f"swap={swap_gib:.2f}GiB <= 2.0GiB threshold"
        elif swap_gib <= 4.0:
            result.swap_policy_tier = "diagnostic"
            result.swap_gate_reason = f"swap={swap_gib:.2f}GiB in (2.0GiB, 4.0GiB] — hardware taint"
        else:
            result.swap_policy_tier = "hard_block"
            result.swap_gate_reason = f"swap={swap_gib:.2f}GiB > 4.0GiB — restart required"
        # F215D: any profile with swap >= 1.0 GiB → hardware_constrained, comparable_result=False
        # smoke180 allows higher swap (>3.0) but still marks comparable=false below
        if swap_gib >= _SWAP_GATE_THRESHOLD_GIB:
            result.swap_gate_triggered = True
            result.hardware_constrained = True
            result.comparable_result = False
            result.taint_reason = "high_swap"
            result.recommended_next_profile = "smoke180 or active300_after_restart"
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            logging.warning(
                "[PREFLIGHT] [SWAP GATE] swap=%.1f GiB >= %.1f GiB threshold — hardware_constrained=True, comparable_result=False",
                swap_gib, _SWAP_GATE_THRESHOLD_GIB
            )
        else:
            logging.info("[PREFLIGHT] Memory state=%s — preflight OK", result.uma_pre_state)

    return result


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------

async def _run_dry_run(
    query: str,
    profile: str,
    duration_s: int,
    aggressive_mode: bool,
    deep_probe: bool,
    require_memory_ok: bool = False,
    allow_high_swap: bool = False,
) -> LiveMeasurementResult:
    """Validate command construction without running sprint."""

    # Check readiness artifacts
    readiness = _check_readiness_artifacts()

    # Build planned command
    export_dir = str(Path.home() / ".hledac" / "reports")
    planned_cmd = [
        sys.executable, "-m", "hledac.universal.core",
        "--sprint",
        f"--query={query}",
        f"--duration={duration_s}",
        f"--export-dir={export_dir}",
    ]
    if aggressive_mode:
        planned_cmd.append("--aggressive")
    if deep_probe:
        planned_cmd.append("--deep-probe")

    # Capture pre-sprint UMA for memory gate and swap gate
    uma_pre = await _capture_uma()

    result = LiveMeasurementResult(
        measurement_id=_make_measurement_id(),
        sprint_id=None,
        mode=RunMode.DRY_RUN,
        status=MeasurementStatus.PLANNED,
        start_time_iso=_now_iso(),
        end_time_iso=None,
        planned_duration_s=float(duration_s),
        actual_duration_s=None,
        query=query,
        profile=profile,
        aggressive_mode=aggressive_mode,
        deep_probe=deep_probe,
        uma_pre_used_gib=uma_pre.get("used_gib"),
        uma_pre_swap_gib=uma_pre.get("swap_gib"),
        uma_pre_state=uma_pre.get("state"),
        uma_post_used_gib=None,
        uma_post_swap_gib=None,
        uma_post_state=None,
        findings_count=None,
        cycles_completed=None,
        cycles_started=None,
        accepted_findings=None,
        runtime_truth=None,
        timing_truth=None,
        checkpoint_zero_category=None,
        primary_signal_source=None,
        error=None,
        stabilization_seal_present=readiness.get("stabilization_seal", False),
        hermetic_regression_manifest_present=readiness.get("hermetic_regression_manifest", False),
        transport_authority_status_present=readiness.get("transport_authority_status", False),
        mlx_wired_limit_seal_present=readiness.get("mlx_wired_limit_seal", False),
        acquisition_profile=os.environ.get("HLEDAC_ACQUISITION_PROFILE"),
    )

    # F2130: Runtime Authority — dry-run does not invoke any sprint runtime
    result.runtime_authority_path = "dry_run_no_runtime"
    result.runtime_authority_module = None
    result.runtime_authority_function = None
    result.runtime_authority_is_canonical = False
    result.runtime_authority_evidence = {
        "mode": "dry_run",
        "planned_cmd": [sys.executable, "-m", "hledac.universal.core", "--sprint"],
    }

    # Stamp profile truthfulness metadata
    _stamp_profile_meta(result, profile)

    # Memory gate: check if memory is OK for live execution
    if require_memory_ok:
        is_critical = _uma_state_is_critical_or_emergency(result.uma_pre_state)
        if is_critical:
            result.status = MeasurementStatus.ABORTED
            result.error = (
                f"[MEMORY GATE] UMA pre-state is '{result.uma_pre_state}' — "
                f"requires ok/warn state for live execution. "
                f"Use without --require-memory-ok or address memory pressure first."
            )
            result.swap_gate_triggered = True
            # F220F: tiered swap telemetry
            result.swap_policy_tier = "hard_block"
            result.swap_gate_reason = f"memory gate abort: uma_state={result.uma_pre_state}"
            # Thread is_memory_gate_abort=True so verdict = ABORTED_MEMORY_GATE
            _stamp_run_quality_verdict(result, is_memory_gate_abort=True)
            logging.error("[DRY-RUN] [MEMORY GATE] Aborted: %s", result.error)
            return result
        else:
            logging.info(
                "[DRY-RUN] [MEMORY GATE] Pre-state=%s — memory OK for live execution",
                result.uma_pre_state
            )

    # F215D: Swap gate — active profiles with high swap are hardware-constrained
    swap_gib = result.uma_pre_swap_gib or 0
    is_active_profile = profile in ("active300", "active600")
    # F220F: tiered swap policy
    if swap_gib <= 2.0:
        result.swap_policy_tier = "clean"
        result.swap_gate_reason = f"swap={swap_gib:.2f}GiB <= 2.0GiB threshold"
    elif swap_gib <= 4.0:
        result.swap_policy_tier = "diagnostic"
        result.swap_gate_reason = f"swap={swap_gib:.2f}GiB in (2.0GiB, 4.0GiB]"
    else:
        result.swap_policy_tier = "hard_block"
        result.swap_gate_reason = f"swap={swap_gib:.2f}GiB > 4.0GiB — restart required"
    if is_active_profile and swap_gib >= _SWAP_GATE_THRESHOLD_GIB:
        result.swap_gate_triggered = True
        if not allow_high_swap:
            result.status = MeasurementStatus.ABORTED
            result.comparable_result = False
            result.taint_reason = "high_swap"
            result.error = (
                f"[SWAP GATE] swap={swap_gib:.1f} GiB >= {_SWAP_GATE_THRESHOLD_GIB:.1f} GiB threshold "
                f"for active profile '{profile}' — aborting dry-run. "
                f"Restart to clear swap, or use --allow-high-swap to run anyway (results non-comparable)."
            )
            result.hardware_constrained = True
            result.recommended_next_profile = "smoke180 or active300_after_restart"
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            _stamp_profile_meta(result, profile)
            logging.error("[DRY-RUN] [SWAP GATE] Aborted: %s", result.error)
            return result
        else:
            result.hardware_constrained = True
            result.comparable_result = False
            result.taint_reason = "high_swap"
            result.recommended_next_profile = "smoke180 or active300_after_restart"
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            logging.warning(
                "[DRY-RUN] [SWAP GATE] swap=%.1f GiB >= %.1f GiB — proceeding with --allow-high-swap (hardware_constrained=True, comparable_result=False)",
                swap_gib, _SWAP_GATE_THRESHOLD_GIB
            )

    # Log planned command
    logging.info("[DRY-RUN] Planned command: %s", " ".join(planned_cmd))

    # Log readiness artifact status
    for name, present in readiness.items():
        status_str = "PRESENT" if present else "MISSING"
        logging.info("[DRY-RUN] Readiness artifact [%s]: %s", name, status_str)

    # Inject profile truthfulness metadata
    active_expected, windup_lead_s, active_window_s, verdict = _get_profile_verdict(profile)

    # Warn in dry-run log if profile has no active window
    if not active_expected:
        logging.warning(
            "[DRY-RUN] Profile %s is ENTRY_SMOKE_ONLY — no active runtime window. "
            "Use active300 (or active600) for meaningful active sprint measurement.",
            profile
        )
    else:
        logging.info(
            "[DRY-RUN] Profile %s verdict=%s windup_lead=%ds active_window=%ds "
            "(active_runtime_expected=True)",
            profile, verdict, windup_lead_s, active_window_s
        )

    # Simulate validation
    errors: list[str] = []
    if duration_s < MIN_DURATION_S:
        errors.append(f"Duration {duration_s}s < minimum {MIN_DURATION_S}s (use --allow-smoke to override)")

    if errors:
        result.status = MeasurementStatus.ABORTED
        result.error = "; ".join(errors)
    else:
        result.status = MeasurementStatus.PLANNED
        logging.info(
            "[DRY-RUN] Validation PASSED — ready for live execution with --live flag"
        )

    # Stamp run quality verdict (is_memory_gate_abort=False — already handled above if True)
    _stamp_run_quality_verdict(result, is_memory_gate_abort=False)
    _stamp_live_kpi(result)

    return result


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------

async def _run_live_sprint(
    query: str,
    profile: str,
    duration_s: int,
    aggressive_mode: bool,
    deep_probe: bool,
    export_dir: str,
    require_memory_ok: bool = False,
    allow_high_swap: bool = False,
) -> LiveMeasurementResult:
    """Run canonical sprint and capture metrics."""
    import uuid
    measurement_id = _make_measurement_id()
    start_time_iso = _now_iso()
    start_ts = time.monotonic()
    ts = time.time_ns() // 1_000_000
    harness_sprint_id = f"8sa_{ts}_{uuid.uuid4().hex[:6]}"

    # Capture pre-sprint UMA
    uma_pre = await _capture_uma()

    result = LiveMeasurementResult(
        measurement_id=measurement_id,
        sprint_id=None,
        mode=RunMode.LIVE,
        status=MeasurementStatus.RUNNING,
        start_time_iso=start_time_iso,
        end_time_iso=None,
        planned_duration_s=float(duration_s),
        actual_duration_s=None,
        query=query,
        profile=profile,
        aggressive_mode=aggressive_mode,
        deep_probe=deep_probe,
        uma_pre_used_gib=uma_pre.get("used_gib"),
        uma_pre_swap_gib=uma_pre.get("swap_gib"),
        uma_pre_state=uma_pre.get("state"),
        uma_post_used_gib=None,
        uma_post_swap_gib=None,
        uma_post_state=None,
        findings_count=None,
        cycles_completed=None,
        cycles_started=None,
        accepted_findings=None,
        runtime_truth=None,
        timing_truth=None,
        checkpoint_zero_category=None,
        primary_signal_source=None,
        error=None,
        stabilization_seal_present=READINESS_ARTIFACTS["stabilization_seal"].exists(),
        hermetic_regression_manifest_present=READINESS_ARTIFACTS["hermetic_regression_manifest"].exists(),
        transport_authority_status_present=READINESS_ARTIFACTS["transport_authority_status"].exists(),
        mlx_wired_limit_seal_present=READINESS_ARTIFACTS["mlx_wired_limit_seal"].exists(),
        # F216B: Acquisition profile telemetry
        acquisition_profile=os.environ.get("HLEDAC_ACQUISITION_PROFILE"),
    )

    # Memory gate: abort before live execution if memory is critical
    if require_memory_ok:
        is_critical = _uma_state_is_critical_or_emergency(result.uma_pre_state)
        if is_critical:
            result.status = MeasurementStatus.ABORTED
            result.end_time_iso = _now_iso()
            result.error = (
                f"[MEMORY GATE] UMA pre-state is '{result.uma_pre_state}' — "
                f"aborting live execution before sprint starts. "
                f"Resolve memory pressure and retry."
            )
            _stamp_profile_meta(result, profile)
            # Thread is_memory_gate_abort=True so verdict = ABORTED_MEMORY_GATE
            _stamp_run_quality_verdict(result, is_memory_gate_abort=True)
            result.swap_gate_triggered = True
            # F220F: tiered swap telemetry
            result.swap_policy_tier = "hard_block"
            result.swap_gate_reason = f"memory gate abort: uma_state={result.uma_pre_state}"
            logging.error("[LIVE] [MEMORY GATE] Aborted: %s", result.error)
            # F2130: Runtime Authority — canonical path was checked but sprint never ran
            result.runtime_authority_path = "canonical_core_run_sprint"
            result.runtime_authority_module = "hledac.universal.core.__main__"
            result.runtime_authority_function = "run_sprint"
            result.runtime_authority_is_canonical = None  # path is canonical, but sprint didn't run
            result.runtime_authority_evidence = {
                "sprint_id": harness_sprint_id,
                "measurement_id": measurement_id,
                "entry_via": "benchmarks/live_sprint_measurement._run_live_sprint",
                "aborted": True,
                "abort_reason": "memory_gate",
            }
            return result

    # F215D: Swap gate — active profiles with high swap are hardware-constrained
    # smoke180 has no active runtime so swap doesn't distort comparability
    swap_gib = result.uma_pre_swap_gib or 0
    is_active_profile = profile in ("active300", "active600")
    # F220F: tiered swap policy
    if swap_gib <= 2.0:
        result.swap_policy_tier = "clean"
        result.swap_gate_reason = f"swap={swap_gib:.2f}GiB <= 2.0GiB threshold"
    elif swap_gib <= 4.0:
        result.swap_policy_tier = "diagnostic"
        result.swap_gate_reason = f"swap={swap_gib:.2f}GiB in (2.0GiB, 4.0GiB]"
    else:
        result.swap_policy_tier = "hard_block"
        result.swap_gate_reason = f"swap={swap_gib:.2f}GiB > 4.0GiB — restart required"
    if is_active_profile and swap_gib >= _SWAP_GATE_THRESHOLD_GIB:
        result.swap_gate_triggered = True
        if not allow_high_swap:
            result.status = MeasurementStatus.ABORTED
            result.end_time_iso = _now_iso()
            result.comparable_result = False
            result.taint_reason = "high_swap"
            result.error = (
                f"[SWAP GATE] swap={swap_gib:.1f} GiB >= {_SWAP_GATE_THRESHOLD_GIB:.1f} GiB threshold "
                f"for active profile '{profile}' — aborting. "
                f"Restart to clear swap, or use --allow-high-swap to run anyway (results non-comparable)."
            )
            result.hardware_constrained = True
            result.recommended_next_profile = "smoke180 or active300_after_restart"
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            _stamp_profile_meta(result, profile)
            logging.error("[LIVE] [SWAP GATE] Aborted: %s", result.error)
            # F2130: Runtime Authority — canonical path was checked but sprint never ran
            result.runtime_authority_path = "canonical_core_run_sprint"
            result.runtime_authority_module = "hledac.universal.core.__main__"
            result.runtime_authority_function = "run_sprint"
            result.runtime_authority_is_canonical = None  # path is canonical, but sprint didn't run
            result.runtime_authority_evidence = {
                "sprint_id": harness_sprint_id,
                "measurement_id": measurement_id,
                "entry_via": "benchmarks/live_sprint_measurement._run_live_sprint",
                "aborted": True,
                "abort_reason": "swap_gate",
            }
            return result
        else:
            # allow_high_swap=True: run but mark as hardware-constrained (no abort)
            # NOTE: Do NOT call _stamp_profile_meta here — it calls _stamp_run_quality_verdict
            # internally which derives hardware_constrained=False (for status=RUNNING/no-runtime_truth)
            # and would clobber the True we just set. Fields are set directly; _stamp_profile_meta
            # is called again after the sprint completes (line ~2270) for profile-specific metadata.
            result.hardware_constrained = True
            result.comparable_result = False
            result.taint_reason = "high_swap"
            result.recommended_next_profile = "smoke180 or active300_after_restart"
            result.recommended_operator_action = _SWAP_GATE_OPERATOR_ACTION
            result.run_quality_verdict = RunQualityVerdict.PASS_HARDWARE_CONSTRAINED.value
            logging.warning(
                "[LIVE] [SWAP GATE] swap=%.1f GiB >= %.1f GiB — proceeding with --allow-high-swap (hardware_constrained=True, comparable_result=False)",
                swap_gib, _SWAP_GATE_THRESHOLD_GIB
            )


    # harness_sprint_id already generated at function start
    result.sprint_id = harness_sprint_id

    # Patch _make_sprint_id so run_sprint uses our harness_sprint_id.
    # This ensures get_sprint_json_report_path(harness_sprint_id) finds the report.
    _original_make_sprint_id = core_main._make_sprint_id
    _patched_sprint_ids = [harness_sprint_id]

    def _patched_make_sprint_id() -> str:
        return _patched_sprint_ids.pop(0) if _patched_sprint_ids else _original_make_sprint_id()

    core_main._make_sprint_id = _patched_make_sprint_id

    # F215G: Capture runtime provenance before sprint execution
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    result.core_run_sprint_module_file = str(_Path(core_main.__file__).resolve()) if hasattr(core_main, '__file__') else None
    result.core_run_sprint_function_qualname = "run_sprint"
    result.sprint_scheduler_module_file = str(_Path(__file__).resolve().parent.parent / "runtime" / "sprint_scheduler.py")
    result.live_sprint_measurement_module_file = str(_Path(__file__).resolve())
    result.python_executable = _sys.executable
    result.runtime_cwd = _os.getcwd()
    result.sys_path_head = _sys.path[0] if _sys.path else None
    _core_main_path = _Path(core_main.__file__) if hasattr(core_main, '__file__') else None
    result.core_main_mtime = _core_main_path.stat().st_mtime if _core_main_path and _core_main_path.exists() else None
    result.sprint_scheduler_mtime = _Path(__file__).resolve().parent.parent.joinpath("runtime", "sprint_scheduler.py").stat().st_mtime

    try:
        logging.info(
            "[LIVE] Starting sprint measurement_id=%s sprint_id=%s profile=%s duration=%ds",
            measurement_id, harness_sprint_id, profile, duration_s
        )

        # Run canonical sprint
        # F228A: Resolve benchmark profile → canonical acquisition profile.
        # Also pass windup_lead_s from profile metadata so diagnostic profiles
        # (nonfeed_diagnostic180) enter active immediately instead of 180s windup delay
        _windup_lead_s = PROFILE_META.get(profile, {}).get("expected_windup_lead_s")
        _acquisition_profile = _resolve_acquisition_profile(profile)
        os.environ["HLEDAC_ACQUISITION_PROFILE"] = _acquisition_profile
        await core_main.run_sprint(
            query=query,
            duration_s=float(duration_s),
            export_dir=export_dir,
            aggressive_mode=aggressive_mode,
            deep_probe_enabled=deep_probe,
            ui_mode=False,
            windup_lead_s=_windup_lead_s,
            acquisition_profile=_acquisition_profile,
        )

        end_time_iso = _now_iso()
        actual_duration_s = time.monotonic() - start_ts

        # Capture post-sprint UMA
        uma_post = await _capture_uma()
        result.uma_post_used_gib = uma_post.get("used_gib")
        result.uma_post_swap_gib = uma_post.get("swap_gib")
        result.uma_post_state = uma_post.get("state")

        result.end_time_iso = end_time_iso
        result.actual_duration_s = round(actual_duration_s, 1)
        result.status = MeasurementStatus.COMPLETED

        # F2130: Runtime Authority — canonical sprint execution path
        result.runtime_authority_path = "canonical_core_run_sprint"
        result.runtime_authority_module = "hledac.universal.core.__main__"
        result.runtime_authority_function = "run_sprint"
        result.runtime_authority_is_canonical = True
        result.runtime_authority_evidence = {
            "sprint_id": harness_sprint_id,
            "measurement_id": measurement_id,
            "entry_via": "benchmarks/live_sprint_measurement._run_live_sprint",
        }

        # Parse sprint report — path uses harness_sprint_id (patched into run_sprint)
        report_path = get_sprint_json_report_path(harness_sprint_id)
        if report_path.exists():
            result.report_json_path = str(report_path)
            parsed = _parse_sprint_report(str(report_path))
            if parsed:
                result.findings_count = parsed.get("findings_count")
                result.cycles_completed = parsed.get("cycles_completed")
                result.cycles_started = parsed.get("cycles_started")
                result.accepted_findings = parsed.get("accepted_findings")
                result.runtime_truth = parsed.get("runtime_truth")
                result.timing_truth = parsed.get("timing_truth")
                result.checkpoint_zero_category = parsed.get("checkpoint_zero_category")
                result.primary_signal_source = parsed.get("primary_signal_source")
                result.public_pipeline = parsed.get("public_pipeline")
                result.acquisition_strategy = parsed.get("acquisition_strategy")
                result.windup_guard_observation = parsed.get("windup_guard_observation")
                result.return_guard_observation = parsed.get("return_guard_observation")
                result.scheduler_exit = parsed.get("scheduler_exit")
                # F208F: active300 acquisition terminality wiring
                # [F209B-FIX] acquisition_terminality_* are top-level keys in the
                # canonical report (written by _extract_acquisition_telemetry in
                # core/__main__.py), NOT inside acquisition_strategy dict which is None.
                result.acquisition_terminality_checked = parsed.get(
                    "acquisition_terminality_checked"
                )
                result.acquisition_terminality_satisfied = parsed.get(
                    "acquisition_terminality_satisfied"
                )
                result.acquisition_terminality_missing_lanes = parsed.get(
                    "acquisition_terminality_missing_lanes"
                )
                result.acquisition_terminality_report = parsed.get(
                    "acquisition_terminality_report"
                )
                # F209B: Acquisition prelude telemetry from parsed report
                result.acquisition_prelude_checked = parsed.get("acquisition_prelude_checked")
                result.acquisition_prelude_ran = parsed.get("acquisition_prelude_ran")
                result.acquisition_prelude_required_lanes = parsed.get("acquisition_prelude_required_lanes")
                result.acquisition_prelude_terminal_lanes = parsed.get("acquisition_prelude_terminal_lanes")
                result.acquisition_prelude_missing_lanes = parsed.get("acquisition_prelude_missing_lanes")
                result.acquisition_prelude_skipped_lanes = parsed.get("acquisition_prelude_skipped_lanes")
                result.acquisition_prelude_errors = parsed.get("acquisition_prelude_errors")
                result.acquisition_prelude_duration_s = parsed.get("acquisition_prelude_duration_s")
                result.acquisition_prelude_reason = parsed.get("acquisition_prelude_reason")
                # F217B: Nonfeed mission controller telemetry — copied from canonical report
                result.nonfeed_mission_active = parsed.get("nonfeed_mission_active")
                result.nonfeed_required_families = parsed.get("nonfeed_required_families")
                result.nonfeed_optional_families = parsed.get("nonfeed_optional_families")
                result.nonfeed_family_status = parsed.get("nonfeed_family_status")
                result.nonfeed_all_required_terminal = parsed.get("nonfeed_all_required_terminal")
                result.nonfeed_any_accepted = parsed.get("nonfeed_any_accepted")
                result.nonfeed_provider_failures = parsed.get("nonfeed_provider_failures")
                result.nonfeed_memory_skips = parsed.get("nonfeed_memory_skips")
                result.nonfeed_mission_exit_reason = parsed.get("nonfeed_mission_exit_reason")
                # F225A: Extract claims_runtime_status from parsed report
                result.claims_runtime_status = parsed.get("claims_runtime_status")
                # F223A: Propagate actual acquisition_profile from canonical acquisition_report
                # (runtime already has the correct value; env var was just initialization hedge)
                if result.acquisition_report and isinstance(result.acquisition_report, dict):
                    _ap_from_report = result.acquisition_report.get("acquisition_profile")
                    if _ap_from_report:
                        result.acquisition_profile = _ap_from_report
                # F208H: Read full canonical acquisition_report from sprint JSON
                # for validator self-containment (not just the sanitized subset)
                try:
                    with open(report_path) as _ar_f:
                        _ar_data = json.load(_ar_f)
                    result.acquisition_report = _ar_data.get("acquisition_report")
                except Exception:
                    result.acquisition_report = None

        # F223A: Propagate actual nonfeed fields from canonical acquisition_report
                if result.acquisition_report and isinstance(result.acquisition_report, dict):
                    result.nonfeed_priority_enabled = bool(
                        result.acquisition_report.get("nonfeed_priority_enabled", False)
                    )
                    _lanes = result.acquisition_report.get("nonfeed_profile_expected_lanes", [])
                    result.nonfeed_profile_expected_lanes = tuple(_lanes) if _lanes else ()

        logging.info(
            "[LIVE] Completed measurement_id=%s findings=%s cycles=%s duration=%.1fs",
            measurement_id, result.findings_count, result.cycles_completed, actual_duration_s
        )

        # Stamp profile truthfulness
        _stamp_profile_meta(result, profile)

    except Exception as exc:
        result.status = MeasurementStatus.FAILED
        result.end_time_iso = _now_iso()
        result.error = f"{type(exc).__name__}: {exc}"
        logging.error("[LIVE] Failed measurement_id=%s: %s", measurement_id, exc, exc_info=True)
        # F2130: Runtime Authority — canonical path was used even though it failed
        result.runtime_authority_path = "canonical_core_run_sprint"
        result.runtime_authority_module = "hledac.universal.core.__main__"
        result.runtime_authority_function = "run_sprint"
        result.runtime_authority_is_canonical = True
        result.runtime_authority_evidence = {
            "sprint_id": harness_sprint_id,
            "measurement_id": measurement_id,
            "entry_via": "benchmarks/live_sprint_measurement._run_live_sprint",
            "failed": True,
        }
        _stamp_profile_meta(result, profile)

    finally:
        # Restore original _make_sprint_id — critical for test isolation
        core_main._make_sprint_id = _original_make_sprint_id

    # Always stamp run quality verdict (is_memory_gate_abort=False — handled above if True)
    _stamp_run_quality_verdict(result, is_memory_gate_abort=False)
    _stamp_live_kpi(result)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="F206BH Live Sprint Measurement Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Profiles:
  smoke180  180s sprint (smoke test)
  active300 300s sprint (standard)
  active600 600s sprint (extended)

Safety:
  Default is --dry-run (no live sprint execution).
  Live execution requires explicit --live flag.
  No stealth or aggressive mode by default.

Examples:
  python benchmarks/live_sprint_measurement.py --profile smoke180 --query "LockBit ransomware"
  python benchmarks/live_sprint_measurement.py --profile nonfeed_diagnostic180 --query "mozilla.org certificate transparency subdomains" --live
  python benchmarks/live_sprint_measurement.py --profile active300 --query "APT29" --live
  python benchmarks/live_sprint_measurement.py --profile active600 --query "ransomware" --live --output-json /tmp/measure.json
  python benchmarks/live_sprint_measurement.py --print-preflight-only --output-json /tmp/preflight.json
        """,
    )

    parser.add_argument(
        "--profile",
        type=str,
        choices=list(PROFILE_DURATION.keys()),
        default="active300",
        help="Measurement profile (determines duration). Default: active300",
    )
    parser.add_argument(
        "--query",
        type=str,
        required=False,
        help="Sprint query string",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="Override profile duration (seconds). Use with --allow-smoke for <180s",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Enable aggressive mode (8s branch budgets, parallel branches)",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Enable deep probe research post-sprint",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute live sprint (default is --dry-run)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate command construction without running sprint (default)",
    )
    parser.add_argument(
        "--allow-smoke",
        action="store_true",
        help="Allow duration < 180s (smoke profile override)",
    )
    parser.add_argument(
        "--require-memory-ok",
        action="store_true",
        help="Abort if UMA pre-state is critical/emergency before live execution. "
             "Dry-run reports the gate; live execution aborts before sprint starts.",
    )
    parser.add_argument(
        "--allow-high-swap",
        action="store_true",
        help="Allow active300/active600 run even when swap >= 3 GiB. "
             "Results will be marked non-comparable (hardware_constrained=True) but will proceed. "
             "Use after restart to clear swap.",
    )
    parser.add_argument(
        "--print-preflight-only",
        action="store_true",
        help="Sample readiness/memory/profile metadata without running sprint. "
             "Never calls run_sprint. Useful for checking readiness after restart.",
    )
    parser.add_argument(
        "--print-invocation-reality",
        action="store_true",
        help="F222B: Print namespace/path reality diagnostic and exit. "
             "Never runs live sprint or instantiates SprintScheduler.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=None,
        help="Path to write JSON measurement result",
    )
    parser.add_argument(
        "--output-md",
        type=str,
        default=None,
        help="Path to write markdown summary",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser

def _render_md(result: LiveMeasurementResult) -> str:
    """Render measurement result as markdown. Delegates to extracted module."""
    from hledac.universal.benchmarks.live_measurement_markdown import render_live_measurement_markdown
    return render_live_measurement_markdown(result)


async def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # F222B: Invocation reality — pure diagnostic, no live run, no SprintScheduler
    if args.print_invocation_reality:
        reality = get_invocation_reality()
        print(json.dumps(reality, indent=2))
        return 0

    # Preflight mode — no query required, never calls run_sprint
    if args.print_preflight_only:
        result = await _run_preflight()

        if args.output_json:
            out_path = Path(args.output_json).resolve()
            result.resolved_output_json = str(out_path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w") as f:
                f.write(result.to_json())
            logging.info("JSON result written to %s", out_path)

        if args.output_md:
            md_path = Path(args.output_md).resolve()
            result.resolved_output_md = str(md_path)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            with open(md_path, "w") as f:
                f.write(_render_md(result))
            logging.info("Markdown summary written to %s", md_path)

        print(f"[PREFLIGHT] measurement_id={result.measurement_id} status={result.status.value}")
        print(f"  verdict={result.run_quality_verdict}")
        print(f"  uma_pre_state={result.uma_pre_state} uma_pre_used={result.uma_pre_used_gib} GiB")
        print(f"  uma_pre_swap={result.uma_pre_swap_gib} GiB")
        if result.error:
            print(f"  ERROR: {result.error}")
        if result.recommended_operator_action:
            print(f"  OPERATOR ACTION: {result.recommended_operator_action}")
        if result.status == MeasurementStatus.ABORTED:
            return 2
        return 0

    # Require query for non-preflight modes
    if not args.query:
        logging.error("--query is required (use --print-preflight-only for preflight check without query)")
        return 1

    # Resolve duration
    duration_s = args.duration or PROFILE_DURATION[args.profile]

    # Safety: duration < 180 requires --allow-smoke
    if duration_s < MIN_DURATION_S and not args.allow_smoke:
        logging.error(
            "Duration %ds < minimum %ds. Pass --allow-smoke to override.",
            duration_s, MIN_DURATION_S
        )
        return 1

    # Determine mode
    is_live = args.live and not args.dry_run
    mode_str = "LIVE" if is_live else "DRY-RUN"
    logging.info("[%s] Profile=%s duration=%ds query=%r aggressive=%s",
                 mode_str, args.profile, duration_s, args.query, args.aggressive)

    # Execute
    if is_live:
        export_dir = str(Path.home() / ".hledac" / "reports")
        result = await _run_live_sprint(
            query=args.query,
            profile=args.profile,
            duration_s=duration_s,
            aggressive_mode=args.aggressive,
            deep_probe=args.deep_probe,
            export_dir=export_dir,
            require_memory_ok=args.require_memory_ok,
            allow_high_swap=args.allow_high_swap,
        )
    else:
        result = await _run_dry_run(
            query=args.query,
            profile=args.profile,
            duration_s=duration_s,
            aggressive_mode=args.aggressive,
            deep_probe=args.deep_probe,
            require_memory_ok=args.require_memory_ok,
            allow_high_swap=args.allow_high_swap,
        )

    # Write outputs
    if args.output_json:
        out_path = Path(args.output_json).resolve()
        result.resolved_output_json = str(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(result.to_json())
        logging.info("JSON result written to %s", out_path)

    if args.output_md:
        md_path = Path(args.output_md).resolve()
        result.resolved_output_md = str(md_path)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        with open(md_path, "w") as f:
            f.write(_render_md(result))
        logging.info("Markdown summary written to %s", md_path)

    # Print summary to stdout
    print(f"[{mode_str}] measurement_id={result.measurement_id} status={result.status.value}")
    print(f"  verdict={result.run_quality_verdict}")
    if result.error:
        print(f"  ERROR: {result.error}")
    elif result.status == MeasurementStatus.PLANNED:
        print(f"  Validated — ready for live execution. Use --live to run sprint.")

    # Exit code
    if result.status in (MeasurementStatus.COMPLETED, MeasurementStatus.PLANNED):
        return 0
    elif result.status == MeasurementStatus.ABORTED:
        return 2
    else:
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
