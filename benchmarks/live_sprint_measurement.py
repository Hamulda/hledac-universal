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
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------

PROFILE_DURATION: dict[str, int] = {
    "smoke180": 180,
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

MIN_DURATION_S = 180

# Memory-gate operator action text
_MEMORY_GATE_OPERATOR_ACTION = (
    "restart or close heavy apps; rerun active300 with --require-memory-ok"
)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

class RunMode(Enum):
    DRY_RUN = "dry_run"
    LIVE = "live"
    PREFLIGHT = "preflight"


class MeasurementStatus(Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"


class RunQualityVerdict(Enum):
    """Run quality verdict — tells us whether a completed run is hardware-tainted."""
    PASS_VALID_CAPABILITY_RUN = "PASS_VALID_CAPABILITY_RUN"
    PASS_HARDWARE_CONSTRAINED = "PASS_HARDWARE_CONSTRAINED"
    ENTRY_SMOKE_ONLY = "ENTRY_SMOKE_ONLY"
    FAIL_RUNTIME_ERROR = "FAIL_RUNTIME_ERROR"
    FAIL_MEASUREMENT_ERROR = "FAIL_MEASUREMENT_ERROR"
    ABORTED_MEMORY_GATE = "ABORTED_MEMORY_GATE"


@dataclass
class LiveMeasurementResult:
    # Identity
    measurement_id: str
    sprint_id: str | None
    mode: RunMode
    status: MeasurementStatus

    # Timing
    start_time_iso: str | None
    end_time_iso: str | None
    planned_duration_s: float | None
    actual_duration_s: float | None

    # Config
    query: str
    profile: str
    duration_s: int = 0
    aggressive_mode: bool = False
    deep_probe: bool = False

    # UMA
    uma_pre_used_gib: float | None = None
    uma_pre_swap_gib: float | None = None
    uma_pre_state: str | None = None
    uma_post_used_gib: float | None = None
    uma_post_swap_gib: float | None = None
    uma_post_state: str | None = None

    # Sprint results (live mode only)
    findings_count: int | None = None
    cycles_completed: int | None = None
    cycles_started: int | None = None
    accepted_findings: int | None = None
    runtime_truth: dict | None = None
    timing_truth: dict | None = None
    checkpoint_zero_category: str | None = None

    # Signal
    primary_signal_source: str | None = None

    # Export
    export_paths: list[str] = field(default_factory=list)
    report_json_path: str | None = None

    # Error
    error: str | None = None

    # Readiness artifacts
    stabilization_seal_present: bool = False
    hermetic_regression_manifest_present: bool = False
    transport_authority_status_present: bool = False
    mlx_wired_limit_seal_present: bool = False

    # Profile truthfulness metadata
    active_runtime_expected: bool = False
    expected_windup_lead_s: int | None = None
    expected_active_window_s: int | None = None
    profile_verdict: str | None = None   # "ENTRY_SMOKE_ONLY" or "ACTIVE_SPRINT"

    # Run quality verdict (F207G/F207H)
    run_quality_verdict: str | None = None
    hardware_constrained: bool | None = None
    memory_state_pre: str | None = None
    memory_state_post: str | None = None
    swap_warning: bool | None = None
    recommended_next_profile: str | None = None
    recommended_operator_action: str | None = None

    # Live KPI (F207J)
    live_kpi: dict | None = None

    # Public pipeline acceptance telemetry (F207K)
    public_pipeline: dict | None = None

    # Acquisition strategy telemetry (F207Q)
    acquisition_strategy: dict | None = None

    # Windup guard observation telemetry (F207S)
    windup_guard_observation: dict | None = None

    # Return guard observation telemetry (F207T)
    return_guard_observation: dict | None = None

    # Scheduler exit path telemetry (F207V-B)
    scheduler_exit: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        d["status"] = self.status.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


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


def _derive_run_quality_verdict(
    status: MeasurementStatus,
    profile_verdict: str | None,
    uma_pre_state: str | None,
    runtime_truth: dict | None,
    swap_pre_gib: float | None,
    is_memory_gate_abort: bool = False,
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
    return verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next_profile, recommended_operator_action


def _parse_sprint_report(report_path: str | None) -> dict | None:
    """
    Parse sprint JSON report for measurement metrics.

    Robust extraction strategy — tries multiple schema locations:
    1. runtime_truth (top-level dict) for cycles, accepted_findings, primary_signal_source
    2. timing_truth (top-level dict) for timing data
    3. canonical_run_summary for checkpoint_zero_category and any missing fields

    Fail-soft: returns partial dict if some fields are missing.
    """
    if not report_path:
        return None
    try:
        with open(report_path) as f:
            data = json.load(f)

        # Primary source: runtime_truth (top-level dict) — authoritative for sprint metrics
        rt = data.get("runtime_truth") or {}
        tt = data.get("timing_truth") or {}
        summary = data.get("canonical_run_summary") or {}

        result: dict = {}

        # findings_count: try canonical_run_summary, then top-level, then derive from branch_mix
        branch_mix = rt.get("branch_mix", {})
        result["findings_count"] = (
            summary.get("findings_count")
            or data.get("findings_count")
            or branch_mix.get("feed_findings", 0)
            + branch_mix.get("public_findings", 0)
            + branch_mix.get("ct_findings", 0)
        )

        # cycles_completed / cycles_started: runtime_truth is authoritative
        result["cycles_completed"] = rt.get("cycles_completed")
        result["cycles_started"] = rt.get("cycles_started")

        # accepted_findings: runtime_truth is authoritative
        result["accepted_findings"] = rt.get("accepted_findings")

        # runtime_truth: return the dict directly (LiveMeasurementResult now holds dict | None)
        result["runtime_truth"] = rt if isinstance(rt, dict) else None

        # timing_truth: return the dict directly
        result["timing_truth"] = tt if isinstance(tt, dict) else None

        # checkpoint_zero_category: canonical_run_summary
        result["checkpoint_zero_category"] = summary.get("checkpoint_zero_category")

        # primary_signal_source: runtime_truth is authoritative
        result["primary_signal_source"] = rt.get("primary_signal_source") or summary.get("primary_signal_source")

        # public_pipeline: public branch acceptance telemetry (F207K)
        pp = data.get("public_pipeline") or {}
        result["public_pipeline"] = pp if isinstance(pp, dict) else None

        # acquisition_strategy: prewindup barrier + lane plan telemetry (F207Q)
        acq = data.get("acquisition_strategy") or {}
        result["acquisition_strategy"] = acq if isinstance(acq, dict) else None

        # F207R: Promote prewindup_barrier sub-object fields to acquisition_strategy top-level
        # so live KPI parser finds barrier telemetry that was written under prewindup_barrier key
        # in scheduler diagnostic report (sprint_scheduler.py:5143).
        # If live_kpi has barrier fields but acquisition_strategy doesn't → report mapping bug.
        prewindup_barrier = acq.get("prewindup_barrier") if isinstance(acq, dict) else None
        if prewindup_barrier and isinstance(prewindup_barrier, dict):
            barrier = prewindup_barrier
            acq = dict(acq)  # always copy to avoid mutating source dict
            acq["prewindup_barrier_checked"] = bool(getattr(barrier, "checked", False) or barrier.get("checked", False) or barrier.get("satisfied") is not None)
            acq["prewindup_barrier_satisfied"] = bool(barrier.get("satisfied", False))
            acq["prewindup_required_lanes"] = barrier.get("required_lanes", [])
            acq["prewindup_attempted_lanes"] = barrier.get("attempted_lanes", [])
            acq["prewindup_skipped_lanes"] = barrier.get("skipped_lanes", {})
            acq["windup_delayed_for_nonfeed"] = bool(barrier.get("windup_delayed", False))
            acq["nonfeed_scheduler_gap_resolved"] = barrier.get("nonfeed_scheduler_gap_resolved")
            result["acquisition_strategy"] = acq

        # F207S: Extract windup_guard_observation from acquisition_strategy report
        # Maps scheduler result fields → live KPI windup guard telemetry.
        # Also check runtime_truth level (windup_guard_call_count lives there, not under acquisition_strategy).
        wg_obs = None
        wg_from_acq = isinstance(acq, dict) and acq.get("windup_guard_observation")
        wg_from_rt = rt.get("windup_guard_observation")
        wg_raw = wg_from_acq if wg_from_acq else wg_from_rt
        if wg_raw and isinstance(wg_raw, dict):
            wg_obs = {
                "call_count": wg_raw.get("call_count", 0),
                "callback_supplied_count": wg_raw.get("callback_supplied_count", 0),
                "callback_executed_count": wg_raw.get("callback_executed_count", 0),
                "last_reason": wg_raw.get("last_reason", ""),
                "last_phase": wg_raw.get("last_phase", ""),
                "last_allowed": wg_raw.get("last_allowed"),
            }
        # F207S: Also check runtime_truth for windup_guard_call_count directly
        # (scheduler writes windup_guard_call_count as sibling field in runtime_truth)
        if wg_obs is None:
            wg_call_count = rt.get("windup_guard_call_count")
            if wg_call_count is not None:
                wg_obs = {
                    "call_count": wg_call_count,
                    "callback_supplied_count": rt.get("windup_guard_callback_supplied_count", 0),
                    "callback_executed_count": rt.get("windup_guard_callback_executed_count", 0),
                    "last_reason": rt.get("windup_guard_last_reason", ""),
                    "last_phase": rt.get("windup_guard_last_phase", ""),
                    "last_allowed": rt.get("windup_guard_last_allowed"),
                }
        result["windup_guard_observation"] = wg_obs

        # F207T: Extract return_guard from multiple possible report layouts.
        # The scheduler can store return_guard telemetry in any of:
        #   1. acquisition_strategy.return_guard         (preferred canonical path)
        #   2. return_guard                                (top-level sibling of windup_guard_call_count)
        #   3. runtime_truth.return_guard_checked/etc.     (F207U truth-diff discovered path)
        #   4. diagnostics.acquisition_strategy.return_guard
        #   5. scheduler.return_guard
        #   6. acquisition_strategy.windup_guard_observation (nested alongside prewindup_barrier)
        # The parser tries each path in order; first populated dict wins.
        rg_obs = None
        rg_candidates = [
            (isinstance(acq, dict) and acq.get("return_guard")),
            data.get("return_guard"),
            data.get("diagnostics", {}).get("acquisition_strategy", {}).get("return_guard"),
            data.get("scheduler", {}).get("return_guard"),
            data.get("acquisition_strategy", {}).get("windup_guard_observation"),
        ]
        for candidate in rg_candidates:
            if candidate and isinstance(candidate, dict):
                checked = candidate.get("checked", False)
                if checked is not None:
                    rg_obs = {
                        "checked": bool(checked),
                        "required_lanes": candidate.get("required_lanes", []),
                        "satisfied": bool(candidate.get("satisfied", False)),
                        "delayed_for_nonfeed": bool(candidate.get("delayed_for_nonfeed", False)),
                        "block_reason": candidate.get("block_reason", ""),
                        "attempted_lanes": candidate.get("attempted_lanes", []),
                        "skipped_lanes": candidate.get("skipped_lanes", {}),
                        "errors": candidate.get("errors", []),
                    }
                    break
        # F207U: Also try direct runtime_truth sibling fields
        # (raw report: runtime_truth.return_guard_checked, runtime_truth.return_guard_satisfied, etc.)
        if rg_obs is None:
            rt_rg_checked = rt.get("return_guard_checked")
            if rt_rg_checked is not None:
                rg_obs = {
                    "checked": bool(rt_rg_checked),
                    "required_lanes": rt.get("return_guard_required_lanes", []),
                    "satisfied": bool(rt.get("return_guard_satisfied", False)),
                    "delayed_for_nonfeed": bool(rt.get("return_guard_delayed_for_nonfeed", False)),
                    "block_reason": rt.get("return_guard_block_reason", ""),
                    "attempted_lanes": rt.get("return_guard_attempted_lanes", []),
                    "skipped_lanes": rt.get("return_guard_skipped_lanes", {}),
                    "errors": rt.get("return_guard_errors", []),
                }
        result["return_guard_observation"] = rg_obs

        # F207V-B: Extract scheduler_exit from raw report
        # Tries multiple locations where scheduler writes exit path telemetry:
        #   1. scheduler_exit (top-level key written by F207V-A)
        #   2. runtime_truth.scheduler_exit (nested)
        #   3. diagnostics.scheduler_exit (legacy nesting)
        se = (
            data.get("scheduler_exit")
            or rt.get("scheduler_exit")
            or (data.get("diagnostics") or {}).get("scheduler_exit")
        )
        if isinstance(se, dict):
            result["scheduler_exit"] = se

        return result
    except Exception:
        return None


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
) -> dict:
    """
    Compute live KPI dict from parsed sprint report.

    Returns a dict with:
      - total_findings
      - accepted_findings
      - cycles_completed
      - findings_per_min
      - primary_signal_source
      - source_family_counts
      - nonfeed_attempted_families
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

    Feed telemetry preference (F207K-C):
    - If runtime_truth contains rich feed_telemetry (F207I path), use it.
    - Otherwise fall back to branch_mix ratio for feed_dominance_score.
    """
    rt = runtime_truth or {}
    branch_mix = rt.get("branch_mix", {})

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

    # source_family_counts
    source_family_counts: dict[str, int] = {}
    if feed_findings > 0:
        source_family_counts["feed"] = feed_findings
    if public_findings > 0:
        source_family_counts["public"] = public_findings
    if ct_findings > 0:
        source_family_counts["ct"] = ct_findings

    # nonfeed_attempted_families: families with 0 findings (they were attempted)
    nonfeed_attempted_families: list[str] = []
    if public_findings == 0 and _was_family_attempted(rt, "public"):
        nonfeed_attempted_families.append("public")
    if ct_findings == 0 and _was_family_attempted(rt, "ct"):
        nonfeed_attempted_families.append("ct")

    # nonfeed_accepted_findings
    nonfeed_accepted_findings = accepted_findings - feed_findings if accepted_findings else 0
    nonfeed_accepted_findings = max(0, nonfeed_accepted_findings)

    # public_fetch_attempted
    public_fetch_attempted = _was_family_attempted(rt, "public")

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
    source_family_outcomes: list[dict] = as_dict.get("source_family_outcomes", [])

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

    # next_action + next_action_detail (F207K, F207M, F207Q)
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
    )

    return {
        "total_findings": total_findings,
        "accepted_findings": accepted_findings,
        "cycles_completed": cycles_completed,
        "findings_per_min": findings_per_min,
        "primary_signal_source": primary_signal_source,
        "source_family_counts": source_family_counts,
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
        "next_action": next_action,
        "next_action_detail": next_action_detail,
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
        "source_family_outcomes": source_family_outcomes,
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
    }


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
) -> tuple[str, str | None]:
    """Derive (next_action, next_action_detail) based on sprint outcome rules.

    Returns a (action, detail) tuple where detail may be None.
    Rule order matters — public rejection (Rule 3) checked BEFORE feed-dominance (Rule 2)
    so operators see WHY public_findings=0 before generic nonfeed recommendations.
    F207M: starvation rule fires before most other rules to surface scheduler-order fixes.
    F207Q: prewindup barrier rules fire before starvation to surface barrier-not-called issues.
    """
    prewindup_required_lanes = prewindup_required_lanes or []
    prewindup_attempted_lanes = prewindup_attempted_lanes or []

    # F207T: Return guard rules — fire BEFORE prewindup barrier rules
    # because return_guard_checked=false with windup_guard_call_count=0 means
    # the scheduler return path guard was never reached (distinct from windup_guard miss).
    # Return guard fires at ACTIVE→WINDUP transition; windup_guard fires at windup_lead boundary.
    # Rule -1 ONLY fires when return_guard_observation telemetry is PRESENT (not None/empty).
    # If return_guard_observation=None, we cannot determine whether return guard was called — fall through.
    rg = return_guard_observation or {}
    has_rg_telemetry = bool(rg)
    rg_checked: bool = bool(rg.get("checked")) if has_rg_telemetry else False
    rg_satisfied: bool = rg.get("satisfied", False) if has_rg_telemetry else False
    rg_required_lanes: list[str] = rg.get("required_lanes", []) if has_rg_telemetry else []
    rg_attempted_lanes: list[str] = rg.get("attempted_lanes", []) if has_rg_telemetry else []
    rg_nonfeed_attempted: bool = any(lane in rg_attempted_lanes for lane in ("public", "ct"))

    # Rule -1 (F207T): Return guard never checked — scheduler return guard not called
    # ONLY when we have return_guard telemetry AND checked=False.
    # This means the scheduler's return guard was reached but never called (not that we don't know).
    # Conditions: return_guard telemetry present, cycles_started > 0, primary_signal_source present,
    # feed-only (windup_guard_call_count=0 proxy), nonfeed not dispatched.
    if (has_rg_telemetry
            and rg_checked is False
            and runtime_truth.get("cycles_started", 0) > 0
            and runtime_truth.get("primary_signal_source")
            and feed_findings > 0
            and ct_findings == 0
            and public_findings == 0):
        return ("fix_scheduler_return_guard_not_called", None)

    # Rule -1b (F207T): Return guard checked but NOT satisfied — terminal state issue
    if has_rg_telemetry and rg_checked and not rg_satisfied:
        return ("fix_return_guard_terminal_state", None)

    # Rule -1c (F207T): Return guard checked and satisfied but nonfeed lanes never attempted
    # "satisfied" but no nonfeed dispatched despite feed-only run → report mapping gap
    if (has_rg_telemetry
            and rg_checked
            and rg_satisfied
            and rg_required_lanes
            and not rg_nonfeed_attempted
            and ct_findings == 0
            and public_findings == 0):
        return ("fix_return_guard_report_mapping", None)

    # F207V-B: Scheduler exit path rules — fire before return guard rules
    # so operators see which exit path was taken when guard was bypassed.
    # Rule -1d: scheduler_exit dict is present but exit_path is missing/empty
    # This means the F207V-A tracer has not been wired up yet.
    # Only fire when scheduler_exit was explicitly provided (not None).
    # If scheduler_exit is None (no telemetry available), fall through.
    se = scheduler_exit
    if se is not None and not se.get("exit_path"):
        return ("add_scheduler_exit_tracer", None)

    # Rule -1e (F207V-B): feed-only + nonfeed eligible + return_guard not checked
    # The scheduler exit path bypassed the return guard. Include the specific path.
    if (se is not None
            and se.get("exit_path")
            and not rg_checked
            and runtime_truth.get("cycles_started", 0) > 0
            and runtime_truth.get("primary_signal_source")
            and feed_findings > 0
            and ct_findings == 0
            and public_findings == 0):
        exit_path = se.get("exit_path", "unknown")
        return (f"patch_scheduler_exit_path:{exit_path}", None)

    # Rule 0: Pre-windup barrier not called (F207Q)
    # Only fire when acquisition_strategy telemetry is PRESENT (not None/empty).
    # If no barrier telemetry exists, we cannot determine whether barrier was called —
    # fall through to other rules so existing tests are not broken.
    has_barrier_telemetry = acquisition_strategy is not None and bool(acquisition_strategy)
    if (has_barrier_telemetry
            and runtime_truth.get("cycles_started", 0) > 0
            and runtime_truth.get("primary_signal_source")
            and not prewindup_barrier_checked):
        return ("fix_prewindup_barrier_not_called", None)

    # Rule 0b: Pre-windup barrier checked but NOT satisfied (F207Q)
    if prewindup_barrier_checked and not prewindup_barrier_satisfied:
        return ("fix_required_lane_terminal_state", None)

    # Rule 0c: Barrier checked and satisfied but ALL required lanes are missing from attempted_lanes (F207Q)
    # "satisfied" but nothing was actually dispatched → report mapping gap
    # Condition: barrier checked+satisfied, required lanes exist, attempted_lanes doesn't include any required lanes,
    #           AND both ct_findings and public_findings are 0 (no nonfeed findings despite barrier satisfied)
    if (prewindup_barrier_checked
            and prewindup_barrier_satisfied
            and prewindup_required_lanes
            and not any(lane in prewindup_attempted_lanes for lane in prewindup_required_lanes)
            and ct_findings == 0
            and public_findings == 0):
        return ("fix_report_mapping", None)

    # Rule 0d: Barrier checked and satisfied, no starvation (F207Q)
    # If barrier was checked AND satisfied AND ct/public were attempted/skipped → starvation is false
    if prewindup_barrier_checked and prewindup_barrier_satisfied:
        # starvation was false positive — barrier did its job
        pass  # fall through to other rules

    # Rule 1: nonfeed starvation (F207M) — must fire early so operators see it
    if nonfeed_starvation_suspected:
        return ("fix_nonfeed_scheduler_order", None)

    # Rule 1: memory gate abort
    if is_memory_gate_abort:
        return ("clean_memory", None)

    # Rule 3: public attempted but accepted=0 (only when ct was NOT attempted)
    # F207K-B: include top rejection reason in detail
    # Must come BEFORE Rule 2 so public rejection reason surfaces even when feed dominance >= 0.7
    if (public_fetch_attempted and nonfeed_accepted_findings == 0
            and feed_findings == total_findings
            and not _was_family_attempted(runtime_truth, "ct")):
        return ("inspect_public_reject_reasons", top_public_reject_reason)

    # Rule 2: feed dominance high, BOTH public AND ct were attempted, nonfeed accepted=0 → inspect
    # NOTE: Rule 5 and Rule 4 must fire BEFORE this rule so specific CT/public actions take priority
    if feed_dominance_score is not None and feed_dominance_score >= 0.7:
        if nonfeed_accepted_findings == 0:
            # BOTH nonfeed families must have run (not just one)
            both_attempted = public_fetch_attempted and _was_family_attempted(runtime_truth, "ct")
            if both_attempted:
                return ("inspect_nonfeed_rejection_or_raw_counts", None)
            return ("improve_nonfeed_lanes", None)

    # Rule 5: ct attempted but raw=0 (covers ct-only and feed+ct scenarios)
    if ct_findings == 0 and _was_family_attempted(runtime_truth, "ct"):
        return ("inspect_ct_query_domain", None)

    # Rule 4: feed-only with public findings present AND ct was attempted but zero ct findings
    # Only fires when public had findings (public_findings > 0) — distinguishes from Rule 5
    if (total_findings > 0 and feed_findings == total_findings
            and _was_family_attempted(runtime_truth, "ct")
            and public_findings > 0):
        return ("improve_nonfeed_lanes", None)

    # Rule 6: valid multi-source yield
    if nonfeed_accepted_findings > 0:
        return ("run_active600_or_targeted_query", None)

    # Rule 7: no findings but sprint completed — inspect
    if total_findings == 0 and status == MeasurementStatus.COMPLETED:
        return ("inspect_empty_run", None)

    # Default: no action determinable
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
    )
    result.live_kpi = kpi


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
    )

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
        result.memory_state_pre = result.uma_pre_state
        result.swap_warning = result.uma_pre_swap_gib is not None and result.uma_pre_swap_gib > 0
        result.recommended_next_profile = "none_until_memory_ok"
        result.recommended_operator_action = _MEMORY_GATE_OPERATOR_ACTION
        result.run_quality_verdict = RunQualityVerdict.ABORTED_MEMORY_GATE.value
        logging.warning("[PREFLIGHT] [MEMORY GATE] Memory critical: %s", result.uma_pre_state)
    else:
        result.hardware_constrained = False
        result.memory_state_pre = result.uma_pre_state
        result.swap_warning = uma_pre.get("swap_gib", 0) > 0
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

    # Capture pre-sprint UMA for memory gate
    uma_pre = await _capture_uma() if require_memory_ok else {"used_gib": None, "swap_gib": None, "state": None}

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
    )

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
            # Thread is_memory_gate_abort=True so verdict = ABORTED_MEMORY_GATE
            _stamp_run_quality_verdict(result, is_memory_gate_abort=True)
            logging.error("[DRY-RUN] [MEMORY GATE] Aborted: %s", result.error)
            return result
        else:
            logging.info(
                "[DRY-RUN] [MEMORY GATE] Pre-state=%s — memory OK for live execution",
                result.uma_pre_state
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
) -> LiveMeasurementResult:
    """Run canonical sprint and capture metrics."""
    measurement_id = _make_measurement_id()
    start_time_iso = _now_iso()
    start_ts = time.monotonic()

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
            logging.error("[LIVE] [MEMORY GATE] Aborted: %s", result.error)
            return result

    # Import canonical sprint entry — outside try so we can restore in finally
    from hledac.universal.core import __main__ as core_main
    from hledac.universal.paths import get_sprint_json_report_path

    # Generate harness-side sprint_id for tracking
    import uuid
    ts = time.time_ns() // 1_000_000
    harness_sprint_id = f"8sa_{ts}_{uuid.uuid4().hex[:6]}"
    result.sprint_id = harness_sprint_id

    # Patch _make_sprint_id so run_sprint uses our harness_sprint_id.
    # This ensures get_sprint_json_report_path(harness_sprint_id) finds the report.
    _original_make_sprint_id = core_main._make_sprint_id
    _patched_sprint_ids = [harness_sprint_id]

    def _patched_make_sprint_id() -> str:
        return _patched_sprint_ids.pop(0) if _patched_sprint_ids else _original_make_sprint_id()

    core_main._make_sprint_id = _patched_make_sprint_id

    try:
        logging.info(
            "[LIVE] Starting sprint measurement_id=%s sprint_id=%s profile=%s duration=%ds",
            measurement_id, harness_sprint_id, profile, duration_s
        )

        # Run canonical sprint
        await core_main.run_sprint(
            query=query,
            duration_s=float(duration_s),
            export_dir=export_dir,
            aggressive_mode=aggressive_mode,
            deep_probe_enabled=deep_probe,
            ui_mode=False,
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
        "--print-preflight-only",
        action="store_true",
        help="Sample readiness/memory/profile metadata without running sprint. "
             "Never calls run_sprint. Useful for checking readiness after restart.",
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
    """Render measurement result as markdown."""
    verdict_badge = result.run_quality_verdict or "UNKNOWN"
    lines = [
        f"# Live Sprint Measurement: {result.measurement_id}",
        "",
        f"**Mode:** {result.mode.value}",
        f"**Status:** {result.status.value}",
        f"**Quality Verdict:** `{verdict_badge}`",
        f"**Profile:** {result.profile}",
        "",
        "## Timing",
        "",
        f"- Duration (planned): {result.planned_duration_s}s",
        f"- Actual duration: {result.actual_duration_s}s" if result.actual_duration_s else "- Actual duration: N/A",
        f"- Start: {result.start_time_iso}",
        f"- End: {result.end_time_iso}",
        "",
        "## Configuration",
        "",
        f"- Aggressive mode: {result.aggressive_mode}",
        f"- Deep probe: {result.deep_probe}",
        "",
        "## Memory Gate",
        "",
        f"- Pre-sprint state: {result.memory_state_pre}",
        f"- Post-sprint state: {result.memory_state_post}",
        f"- Swap warning: {result.swap_warning}",
        f"- Hardware constrained: {result.hardware_constrained}",
        f"- Recommended next profile: {result.recommended_next_profile or 'N/A'}",
    ]

    if result.recommended_operator_action:
        lines.extend([
            f"- Recommended operator action: {result.recommended_operator_action}",
        ])

    if result.query and result.query != "(preflight-only)":
        lines.extend([
            "",
            "## Query",
            "",
            f"`{result.query}`",
        ])

    lines.extend([
        "",
        "## UMA Memory",
        "",
        f"- Pre-sprint: {result.uma_pre_used_gib} GiB used, {result.uma_pre_swap_gib} GiB swap, state={result.uma_pre_state}",
        f"- Post-sprint: {result.uma_post_used_gib} GiB used, {result.uma_post_swap_gib} GiB swap, state={result.uma_post_state}",
    ])

    if result.mode == RunMode.LIVE:
        lines.extend([
            "",
            "## Sprint Results",
            "",
            f"- Findings count: {result.findings_count}",
            f"- Cycles completed: {result.cycles_completed}",
            f"- Cycles started: {result.cycles_started}",
            f"- Accepted findings: {result.accepted_findings}",
            f"- Runtime truth: {json.dumps(result.runtime_truth, default=str) if isinstance(result.runtime_truth, dict) else result.runtime_truth}",
            f"- Timing truth: {json.dumps(result.timing_truth, default=str) if isinstance(result.timing_truth, dict) else result.timing_truth}",
            f"- Checkpoint zero: {result.checkpoint_zero_category}",
            f"- Primary signal source: {result.primary_signal_source}",
            f"- Report: {result.report_json_path}",
        ])
    else:
        lines.extend([
            "",
            "## Sprint Results (not executed in this mode)",
        ])

    lines.extend([
        "",
        "## Readiness Artifacts",
        "",
        f"- stabilization_seal.json: {'PRESENT' if result.stabilization_seal_present else 'MISSING'}",
        f"- hermetic_regression_manifest.json: {'PRESENT' if result.hermetic_regression_manifest_present else 'MISSING'}",
        f"- transport_authority_status_refreshed.json: {'PRESENT' if result.transport_authority_status_present else 'MISSING'}",
        f"- mlx_wired_limit_seal.json: {'PRESENT' if result.mlx_wired_limit_seal_present else 'MISSING'}",
        "",
        "## Profile Truthfulness",
        "",
        f"- Verdict: **{result.profile_verdict or 'UNKNOWN'}**",
        f"- active_runtime_expected: {result.active_runtime_expected}",
        f"- expected_windup_lead_s: {result.expected_windup_lead_s}s",
        f"- expected_active_window_s: {result.expected_active_window_s}s",
    ])

    if result.error:
        lines.extend(["", "## Error", "", f"```\n{result.error}\n```"])

    # Live KPI section (F207J)
    if result.live_kpi is not None:
        kpi = result.live_kpi
        lines.extend([
            "",
            "## Live KPI",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Total findings | {kpi.get('total_findings', 'N/A')} |",
            f"| Accepted findings | {kpi.get('accepted_findings', 'N/A')} |",
            f"| Cycles completed | {kpi.get('cycles_completed', 'N/A')} |",
            f"| Findings/min | {kpi.get('findings_per_min', 'N/A')} |",
            f"| Primary signal | {kpi.get('primary_signal_source', 'N/A')} |",
            f"| Feed dominance | {kpi.get('feed_dominance_score', 'N/A')} |",
            f"| Source families | {json.dumps(kpi.get('source_family_counts', {}))} |",
            f"| Nonfeed attempted | {kpi.get('nonfeed_attempted_families', [])} |",
            f"| Nonfeed accepted | {kpi.get('nonfeed_accepted_findings', 'N/A')} |",
            f"| Public attempted | {kpi.get('public_fetch_attempted', 'N/A')} |",
            f"| Public accepted (pages) | {kpi.get('public_acceptance_attempted', 0)} |",
            f"| Public accepted (findings) | {kpi.get('public_acceptance_accepted', 0)} |",
            f"| Public rejected (pages) | {kpi.get('public_acceptance_rejected', 0)} |",
            f"| Top reject reason | {kpi.get('top_public_reject_reason', 'N/A')} |",
            f"| Quality verdict | {kpi.get('run_quality_verdict', 'N/A')} |",
            f"| Hardware constrained | {kpi.get('hardware_constrained', 'N/A')} |",
            f"| **Next action** | **{kpi.get('next_action', 'unknown')}** |",
        ])

        # Non-feed Starvation section (F207M)
        # Renders whenever timing data or eligible families are present
        if kpi.get('nonfeed_eligible_families') or kpi.get('windup_lead_observed_s') is not None:
            suspected = kpi.get('nonfeed_starvation_suspected', False)
            starvation_rows = [
                "",
                "## Non-feed Starvation",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Nonfeed starvation suspected | {suspected} |",
                f"| Windup lead requested | {kpi.get('windup_lead_requested_s', 'N/A')}s |",
                f"| Windup lead observed | {kpi.get('windup_lead_observed_s', 'N/A')}s |",
                f"| Active window budget | {kpi.get('active_window_budget_s', 'N/A')}s |",
                f"| Nonfeed eligible families | {kpi.get('nonfeed_eligible_families', [])} |",
            ]
            if suspected:
                starvation_rows.insert(7, f"| Starvation reason | {kpi.get('nonfeed_starvation_reason', 'N/A')} |")
            lines.extend(starvation_rows)

        # Pre-windup Barrier section (F207Q)
        barrier_checked = kpi.get('prewindup_barrier_checked', False)
        if barrier_checked or kpi.get('prewindup_required_lanes') or kpi.get('prewindup_skipped_lanes'):
            barrier_rows = [
                "",
                "## Pre-windup Barrier",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Barrier checked | {barrier_checked} |",
                f"| Barrier satisfied | {kpi.get('prewindup_barrier_satisfied', 'N/A')} |",
                f"| Required lanes | {kpi.get('prewindup_required_lanes', [])} |",
                f"| Attempted lanes | {kpi.get('prewindup_attempted_lanes', [])} |",
            ]
            skipped = kpi.get('prewindup_skipped_lanes', {})
            if skipped:
                barrier_rows.append(f"| Skipped lanes | {json.dumps(skipped)} |")
            windup_delayed = kpi.get('windup_delayed_for_nonfeed')
            if windup_delayed is not None:
                barrier_rows.append(f"| Windup delayed for nonfeed | {windup_delayed} |")
            gap_resolved = kpi.get('nonfeed_scheduler_gap_resolved')
            if gap_resolved is not None:
                barrier_rows.append(f"| Nonfeed scheduler gap resolved | {gap_resolved} |")
            lines.extend(barrier_rows)

    # Windup Guard Observation section (F207S)
    kpi = result.live_kpi
    if kpi is not None:
        wg_call = kpi.get('windup_guard_call_count', 0)
        wg_supplied = kpi.get('windup_guard_callback_supplied_count', 0)
        wg_exec = kpi.get('windup_guard_callback_executed_count', 0)
        wg_reason = kpi.get('windup_guard_last_reason', '')
        wg_phase = kpi.get('windup_guard_last_phase', '')
        wg_allowed = kpi.get('windup_guard_last_allowed')
        # Only render if we have evidence of any windup guard activity
        if wg_call > 0 or wg_supplied > 0 or wg_exec > 0:
            lines.extend([
                "",
                "## Windup Guard Observation",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Call count | {wg_call} |",
                f"| Callback supplied | {wg_supplied} |",
                f"| Callback executed | {wg_exec} |",
                f"| Last reason | {wg_reason or 'N/A'} |",
                f"| Last phase | {wg_phase or 'N/A'} |",
                f"| Last allowed | {wg_allowed} |",
            ])
            # Diagnostic next-action based on windup guard chain
            if wg_call == 0:
                lines.append(f"| **Next action** | **fix_scheduler_windup_callsite** |")
            elif wg_supplied == 0:
                lines.append(f"| **Next action** | **fix_callback_wiring** |")
            elif wg_exec == 0:
                lines.append(f"| **Next action** | **fix_callback_execution** |")
            elif wg_allowed is True:
                # barrier=true, allowed=true → windup proceeded; starvation check in next section
                lines.append(f"| **Next action** | **no_windup_starvation** |")
            elif wg_allowed is False:
                lines.append(f"| **Next action** | **fix_barrier_semantics** |")

    # Scheduler Return Guard section (F207T)
    kpi = result.live_kpi
    if kpi is not None:
        rg_checked = kpi.get('return_guard_checked', False)
        rg_satisfied = kpi.get('return_guard_satisfied', False)
        rg_block = kpi.get('return_guard_block_reason', '')
        rg_required = kpi.get('return_guard_required_lanes', [])
        rg_attempted = kpi.get('return_guard_attempted_lanes', [])
        rg_skipped = kpi.get('return_guard_skipped_lanes', {})
        rg_errors = kpi.get('return_guard_errors', [])
        # Only render if we have evidence of return guard activity
        if rg_checked or rg_required or rg_block or rg_attempted or rg_skipped or rg_errors:
            lines.extend([
                "",
                "## Scheduler Return Guard",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Return guard checked | {rg_checked} |",
                f"| Return guard satisfied | {rg_satisfied} |",
                f"| Block reason | {rg_block or 'N/A'} |",
                f"| Required lanes | {rg_required or 'N/A'} |",
                f"| Attempted lanes | {rg_attempted or 'N/A'} |",
            ])
            if rg_skipped:
                lines.append(f"| Skipped lanes | {json.dumps(rg_skipped)} |")
            if rg_errors:
                lines.append(f"| Errors | {json.dumps(rg_errors)} |")
            # Diagnostic next-action based on return guard chain
            if not rg_checked:
                lines.append(f"| **Next action** | **fix_scheduler_return_guard_not_called** |")
            elif rg_checked and not rg_satisfied:
                lines.append(f"| **Next action** | **fix_return_guard_terminal_state** |")
            elif rg_checked and rg_satisfied and not any(lane in rg_attempted for lane in ("public", "ct")):
                lines.append(f"| **Next action** | **fix_return_guard_report_mapping** |")

    # Scheduler Exit Path section (F207V-B)
    kpi = result.live_kpi
    if kpi is not None:
        se_path = kpi.get('scheduler_exit_path', '')
        se_reason = kpi.get('scheduler_exit_reason', '')
        se_phase = kpi.get('scheduler_exit_phase', '')
        se_cycle = kpi.get('scheduler_exit_cycle', '')
        se_elapsed = kpi.get('scheduler_exit_elapsed_s', '')
        se_guard_checked = kpi.get('scheduler_exit_guard_checked', '')
        se_guard_required = kpi.get('scheduler_exit_guard_required', '')
        se_guard_satisfied = kpi.get('scheduler_exit_guard_satisfied', '')
        # Always render the section when kpi is present — even when exit_path is missing,
        # operators need to see N/A values and the next_action.
        lines.extend([
            "",
            "## Scheduler Exit Path",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Exit path | {se_path or 'N/A'} |",
            f"| Exit reason | {se_reason or 'N/A'} |",
            f"| Exit phase | {se_phase or 'N/A'} |",
            f"| Exit cycle | {se_cycle or 'N/A'} |",
            f"| Elapsed (s) | {se_elapsed or 'N/A'} |",
            f"| Guard checked | {se_guard_checked or 'N/A'} |",
            f"| Guard required | {se_guard_required or 'N/A'} |",
            f"| Guard satisfied | {se_guard_satisfied or 'N/A'} |",
        ])
        if not se_path:
            lines.append(f"| **Next action** | **add_scheduler_exit_tracer** |")
        elif not se_guard_checked:
            lines.append(f"| **Next action** | **patch_scheduler_exit_path:{se_path}** |")

    # PUBLIC Acceptance section (F207K) — detailed breakdown
    kpi = result.live_kpi
    if kpi is not None and kpi.get('public_fetch_attempted'):
        reject_reasons = kpi.get('public_acceptance_reject_reasons', {})
        rejected_urls = kpi.get('public_rejected_url_sample', ())
        # Cap URL sample display at 3
        url_sample_display = list(rejected_urls[:3])
        top_reason = kpi.get('top_public_reject_reason', 'N/A')

        lines.extend([
            "",
            "## PUBLIC Acceptance",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Pages attempted | {kpi.get('public_acceptance_attempted', 0)} |",
            f"| Pages accepted | {kpi.get('public_acceptance_accepted', 0)} |",
            f"| Pages rejected | {kpi.get('public_acceptance_rejected', 0)} |",
            f"| Top reject reason | {top_reason} |",
        ])
        if reject_reasons:
            lines.append("")
            lines.append("**Rejection reasons:**")
            for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"- {reason}: {count}")
        if url_sample_display:
            lines.append("")
            lines.append("**Rejected URL sample (max 3):**")
            for url in url_sample_display:
                lines.append(f"- {url}")

    # Feed Balance section (F207K-C) — only when feed telemetry available
    kpi = result.live_kpi
    if kpi is not None and kpi.get('feed_dominance_score') is not None:
        dom_source = kpi.get('dominant_feed_source', 'N/A')
        dom_pct = kpi.get('dominant_feed_share_pct')
        dom_pct_str = f"{round(dom_pct, 1)}%" if dom_pct is not None else 'N/A'
        soft_cap = kpi.get('estimated_per_source_soft_cap', 'N/A')
        recommendation = kpi.get('feed_balance_recommendation', 'N/A')
        lines.extend([
            "",
            "## Feed Balance",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Feed dominance score | {kpi.get('feed_dominance_score')} |",
            f"| Dominant source | {dom_source} |",
            f"| Dominant share | {dom_pct_str} |",
            f"| Soft cap (est.) | {soft_cap} |",
            f"| Recommendation | {recommendation} |",
        ])

    return "\n".join(lines)


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

    # Preflight mode — no query required, never calls run_sprint
    if args.print_preflight_only:
        result = await _run_preflight()

        if args.output_json:
            out_path = Path(args.output_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w") as f:
                f.write(result.to_json())
            logging.info("JSON result written to %s", out_path)

        if args.output_md:
            md_path = Path(args.output_md)
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
        )
    else:
        result = await _run_dry_run(
            query=args.query,
            profile=args.profile,
            duration_s=duration_s,
            aggressive_mode=args.aggressive,
            deep_probe=args.deep_probe,
            require_memory_ok=args.require_memory_ok,
        )

    # Write outputs
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(result.to_json())
        logging.info("JSON result written to %s", out_path)

    if args.output_md:
        md_path = Path(args.output_md)
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
