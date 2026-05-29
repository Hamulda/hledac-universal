"""
Sprint report parsing for live measurement.

Pure module: no live sprint, no scheduler import, no MLX, no network.
F227C extraction from benchmarks/live_sprint_measurement.py.
F228D: terminality predicates delegated to live_measurement_terminality.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.live_measurement_terminality import (
    has_scheduler_exit_path as _has_scheduler_exit_path_impl,
)
from benchmarks.live_measurement_terminality import (
    has_terminal_source_outcomes as _has_terminal_source_outcomes_impl,
)

# ---------------------------------------------------------------------------
# Backward-compatibility aliases — tests expect underscore names
# ---------------------------------------------------------------------------

def _has_terminal_source_outcomes(acquisition_strategy: dict | None) -> bool:
    return _has_terminal_source_outcomes_impl(acquisition_strategy)


def _has_scheduler_exit_path(scheduler_exit: dict | None) -> bool:
    return _has_scheduler_exit_path_impl(scheduler_exit)


# ---------------------------------------------------------------------------
# Canonical acquisition report parsing
# ---------------------------------------------------------------------------

def _parse_canonical_sprint_report(data: dict) -> dict | None:
    """
    Parse sprint JSON report using canonical acquisition_report schema.

    F208C: Canonical acquisition_report path — checked FIRST before legacy.
    Fail-soft: returns partial dict if some fields are missing.
    """
    acq_report = data.get("acquisition_report")
    if not isinstance(acq_report, dict):
        return None

    result: dict = {}
    rt = data.get("runtime_truth") or {}
    tt = data.get("timing_truth") or {}
    summary = data.get("canonical_run_summary") or {}

    # findings_count
    branch_mix = rt.get("branch_mix", {})
    lane_verdict = rt.get("lane_verdict", {}) or {}
    _lane_ct_findings = lane_verdict.get("ct_findings", 0) if isinstance(lane_verdict, dict) else 0
    result["findings_count"] = (
        summary.get("findings_count")
        or data.get("findings_count")
        or branch_mix.get("feed_findings", 0)
        + branch_mix.get("public_findings", 0)
        + _lane_ct_findings
    )

    # F214D: CT bridge loss telemetry
    if isinstance(lane_verdict, dict):
        result["ct_loss_stage"] = lane_verdict.get("ct_loss_stage", "no_loss")
        result["ct_bridge_invoked"] = lane_verdict.get("ct_bridge_invoked", False)
        result["ct_raw_sample_count"] = lane_verdict.get("ct_raw_sample_count", 0)
        result["ct_candidates_built"] = lane_verdict.get("ct_candidates_built", 0)
        result["ct_bridge_rejections_count"] = lane_verdict.get("ct_bridge_rejections_count", 0)
        result["ct_candidates_accumulated"] = lane_verdict.get("ct_candidates_accumulated", 0)
        result["ct_candidates_stored"] = lane_verdict.get("ct_candidates_stored", 0)
        result["ct_storage_rejected"] = lane_verdict.get("ct_storage_rejected", 0)
    result["cycles_completed"] = rt.get("cycles_completed")
    result["cycles_started"] = rt.get("cycles_started")
    result["accepted_findings"] = rt.get("accepted_findings")
    result["runtime_truth"] = rt if isinstance(rt, dict) else None
    result["timing_truth"] = tt if isinstance(tt, dict) else None
    result["checkpoint_zero_category"] = summary.get("checkpoint_zero_category")
    result["early_exit_class"] = summary.get("early_exit_class") or "CANONICAL_EARLY_EXIT_CLASS_MISSING"
    result["primary_signal_source"] = (
        rt.get("primary_signal_source") or summary.get("primary_signal_source")
    )
    result["canonical_run_summary"] = summary if isinstance(summary, dict) else None
    result["acquisition_report"] = acq_report
    # F225A: claims_runtime_status
    claims_rs = acq_report.get("claims_runtime_status")
    result["claims_runtime_status"] = claims_rs if isinstance(claims_rs, dict) else None

    # public_pipeline
    pp = data.get("public_pipeline") or {}
    result["public_pipeline"] = pp if isinstance(pp, dict) else None

    # F208C: acquisition telemetry from canonical acquisition_report
    result["acquisition_strategy"] = {
        "schema_version": acq_report.get("schema_version"),
        "plan": acq_report.get("plan", []),
        "terminality": acq_report.get("terminality"),
        "nonfeed_plan_debug": acq_report.get("nonfeed_plan_debug"),
        "source_family_outcomes": acq_report.get("source_family_outcomes", []),
    }

    # prewindup_barrier
    pwb = acq_report.get("prewindup_barrier")
    if isinstance(pwb, dict):
        acq_strat = result["acquisition_strategy"]
        acq_strat["prewindup_barrier_checked"] = bool(
            pwb.get("checked", False) or pwb.get("satisfied") is not None
        )
        acq_strat["prewindup_barrier_satisfied"] = bool(pwb.get("satisfied", False))
        acq_strat["prewindup_required_lanes"] = pwb.get("required_lanes", [])
        acq_strat["prewindup_attempted_lanes"] = pwb.get("attempted_lanes", [])
        acq_strat["prewindup_skipped_lanes"] = pwb.get("skipped_lanes", {})
        acq_strat["windup_delayed_for_nonfeed"] = bool(pwb.get("windup_delayed", False))
        acq_strat["nonfeed_scheduler_gap_resolved"] = pwb.get("nonfeed_scheduler_gap_resolved")

    # windup_guard_observation
    wg_obs = acq_report.get("windup_guard_observation")
    if isinstance(wg_obs, dict):
        result["windup_guard_observation"] = {
            "call_count": (
                wg_obs.get("windup_guard_call_count")
                if wg_obs.get("windup_guard_call_count") is not None
                else wg_obs.get("call_count", 0)
            ),
            "callback_supplied_count": (
                wg_obs.get("windup_guard_callback_supplied_count")
                if wg_obs.get("windup_guard_callback_supplied_count") is not None
                else wg_obs.get("callback_supplied_count", 0)
            ),
            "callback_executed_count": (
                wg_obs.get("windup_guard_callback_executed_count")
                if wg_obs.get("windup_guard_callback_executed_count") is not None
                else wg_obs.get("callback_executed_count", 0)
            ),
            "last_reason": wg_obs.get("last_reason", ""),
            "last_phase": wg_obs.get("last_phase", ""),
            "last_allowed": wg_obs.get("last_allowed"),
        }
    else:
        wg_call_count = rt.get("windup_guard_call_count")
        if wg_call_count is not None:
            result["windup_guard_observation"] = {
                "call_count": wg_call_count,
                "callback_supplied_count": rt.get("windup_guard_callback_supplied_count", 0),
                "callback_executed_count": rt.get("windup_guard_callback_executed_count", 0),
                "last_reason": rt.get("windup_guard_last_reason", ""),
                "last_phase": rt.get("windup_guard_last_phase", ""),
                "last_allowed": rt.get("windup_guard_last_allowed"),
            }
        else:
            result["windup_guard_observation"] = None

    # return_guard
    rg = acq_report.get("return_guard")
    if isinstance(rg, dict):
        checked = (
            rg.get("return_guard_checked")
            if rg.get("return_guard_checked") is not None
            else rg.get("checked", False)
        )
        result["return_guard_observation"] = {
            "checked": bool(checked),
            "required_lanes": rg.get("required_lanes") or rg.get("return_guard_required_lanes", []),
            "satisfied": bool(rg.get("satisfied") or rg.get("return_guard_satisfied", False)),
            "delayed_for_nonfeed": bool(
                rg.get("delayed_for_nonfeed") if rg.get("delayed_for_nonfeed") is not None
                else rg.get("return_guard_delayed_for_nonfeed", False)
            ),
            "block_reason": rg.get("block_reason") or rg.get("return_guard_block_reason", ""),
            "attempted_lanes": rg.get("attempted_lanes") or rg.get("return_guard_attempted_lanes", []),
            "skipped_lanes": rg.get("skipped_lanes") or rg.get("return_guard_skipped_lanes", {}),
            "errors": rg.get("errors") or rg.get("return_guard_errors", []),
        }
    else:
        rt_rg_checked = rt.get("return_guard_checked")
        if rt_rg_checked is not None:
            result["return_guard_observation"] = {
                "checked": bool(rt_rg_checked),
                "required_lanes": rt.get("return_guard_required_lanes", []),
                "satisfied": bool(rt.get("return_guard_satisfied", False)),
                "delayed_for_nonfeed": bool(rt.get("return_guard_delayed_for_nonfeed", False)),
                "block_reason": rt.get("return_guard_block_reason", ""),
                "attempted_lanes": rt.get("return_guard_attempted_lanes", []),
                "skipped_lanes": rt.get("return_guard_skipped_lanes", {}),
                "errors": rt.get("return_guard_errors", []),
            }
        else:
            result["return_guard_observation"] = None

    # scheduler_exit
    se = acq_report.get("scheduler_exit")
    result["scheduler_exit"] = se if isinstance(se, dict) else None
    # acquisition_prelude
    apl = acq_report.get("acquisition_prelude")
    if isinstance(apl, dict):
        result["acquisition_prelude_checked"] = bool(apl.get("checked", False))
        result["acquisition_prelude_ran"] = bool(apl.get("ran", False))
        result["acquisition_prelude_required_lanes"] = tuple(apl.get("required_lanes") or [])
        result["acquisition_prelude_terminal_lanes"] = tuple(apl.get("terminal_lanes") or [])
        result["acquisition_prelude_missing_lanes"] = tuple(apl.get("missing_lanes") or [])
        result["acquisition_prelude_skipped_lanes"] = apl.get("skipped_lanes") or {}
        result["acquisition_prelude_errors"] = apl.get("errors") or {}
        result["acquisition_prelude_duration_s"] = apl.get("duration_s")
        result["acquisition_prelude_reason"] = apl.get("reason")
    else:
        result["acquisition_prelude_checked"] = None
        result["acquisition_prelude_ran"] = None
        result["acquisition_prelude_required_lanes"] = None
        result["acquisition_prelude_terminal_lanes"] = None
        result["acquisition_prelude_missing_lanes"] = None
        result["acquisition_prelude_skipped_lanes"] = None
        result["acquisition_prelude_errors"] = None
        result["acquisition_prelude_duration_s"] = None
        result["acquisition_prelude_reason"] = None
    # acquisition_terminality_* — top-level keys in canonical report
    result["acquisition_terminality_checked"] = data.get("acquisition_terminality_checked")
    result["acquisition_terminality_satisfied"] = data.get("acquisition_terminality_satisfied")
    result["acquisition_terminality_missing_lanes"] = data.get("acquisition_terminality_missing_lanes")
    result["acquisition_terminality_report"] = data.get("acquisition_terminality_report")

    return result


# ---------------------------------------------------------------------------
# Legacy fallback parsing (no acquisition_report)
# ---------------------------------------------------------------------------
def _parse_legacy_sprint_report(data: dict) -> dict:
    """
    Parse sprint JSON report using legacy multi-path extraction.

    Used when acquisition_report is absent (pre-F208C reports).
    Fail-soft: returns partial dict if some fields are missing.
    """
    rt = data.get("runtime_truth") or {}
    tt = data.get("timing_truth") or {}
    summary = data.get("canonical_run_summary") or {}

    result: dict = {}
    branch_mix = rt.get("branch_mix", {})
    result["findings_count"] = (
        summary.get("findings_count")
        or data.get("findings_count")
        or branch_mix.get("feed_findings", 0)
        + branch_mix.get("public_findings", 0)
        + branch_mix.get("ct_findings", 0)
    )

    result["cycles_completed"] = rt.get("cycles_completed")
    result["cycles_started"] = rt.get("cycles_started")
    result["accepted_findings"] = rt.get("accepted_findings")
    result["runtime_truth"] = rt if isinstance(rt, dict) else None
    result["timing_truth"] = tt if isinstance(tt, dict) else None
    result["checkpoint_zero_category"] = summary.get("checkpoint_zero_category")
    result["primary_signal_source"] = (
        rt.get("primary_signal_source") or summary.get("primary_signal_source")
    )

    # F214D: lane_verdict contains CT bridge loss telemetry
    lane_verdict = rt.get("lane_verdict", {}) or {}
    if isinstance(lane_verdict, dict):
        result["ct_loss_stage"] = lane_verdict.get("ct_loss_stage", "no_loss")
        result["ct_bridge_invoked"] = lane_verdict.get("ct_bridge_invoked", False)
        result["ct_raw_sample_count"] = lane_verdict.get("ct_raw_sample_count", 0)
        result["ct_candidates_built"] = lane_verdict.get("ct_candidates_built", 0)
        result["ct_bridge_rejections_count"] = lane_verdict.get("ct_bridge_rejections_count", 0)
        result["ct_candidates_accumulated"] = lane_verdict.get("ct_candidates_accumulated", 0)
        result["ct_candidates_stored"] = lane_verdict.get("ct_candidates_stored", 0)
        result["ct_storage_rejected"] = lane_verdict.get("ct_storage_rejected", 0)

    pp = data.get("public_pipeline") or {}
    result["public_pipeline"] = pp if isinstance(pp, dict) else None

    acq = data.get("acquisition_strategy") or {}
    result["acquisition_strategy"] = acq if isinstance(acq, dict) else None

    prewindup_barrier = acq.get("prewindup_barrier") if isinstance(acq, dict) else None
    if prewindup_barrier and isinstance(prewindup_barrier, dict):
        barrier = prewindup_barrier
        acq = dict(acq)
        acq["prewindup_barrier_checked"] = bool(
            getattr(barrier, "checked", False)
            or barrier.get("checked", False)
            or barrier.get("satisfied") is not None
        )
        acq["prewindup_barrier_satisfied"] = bool(barrier.get("satisfied", False))
        acq["prewindup_required_lanes"] = barrier.get("required_lanes", [])
        acq["prewindup_attempted_lanes"] = barrier.get("attempted_lanes", [])
        acq["prewindup_skipped_lanes"] = barrier.get("skipped_lanes", {})
        acq["windup_delayed_for_nonfeed"] = bool(barrier.get("windup_delayed", False))
        acq["nonfeed_scheduler_gap_resolved"] = barrier.get("nonfeed_scheduler_gap_resolved")
        result["acquisition_strategy"] = acq

    # windup_guard_observation
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

    # return_guard
    rg_obs = None
    rg_candidates = [
        (isinstance(acq, dict) and acq.get("return_guard")),
        data.get("return_guard"),
        (data.get("diagnostics") or {}).get("acquisition_strategy", {}).get("return_guard"),
        (data.get("scheduler") or {}).get("return_guard"),
        (data.get("acquisition_strategy") or {}).get("windup_guard_observation"),
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

    # scheduler_exit
    se = (
        data.get("scheduler_exit")
        or rt.get("scheduler_exit")
        or (data.get("diagnostics") or {}).get("scheduler_exit")
    )
    result["scheduler_exit"] = se if isinstance(se, dict) else None

    # F224/F209B: acquisition_prelude fields — top-level keys injected by core.__main__.py
    # These are NOT inside acquisition_report; they live at JSON root level.
    result["acquisition_prelude_checked"] = data.get("acquisition_prelude_checked")
    result["acquisition_prelude_ran"] = data.get("acquisition_prelude_ran")
    result["acquisition_prelude_required_lanes"] = data.get("acquisition_prelude_required_lanes")
    result["acquisition_prelude_terminal_lanes"] = data.get("acquisition_prelude_terminal_lanes")
    result["acquisition_prelude_missing_lanes"] = data.get("acquisition_prelude_missing_lanes")
    result["acquisition_prelude_skipped_lanes"] = data.get("acquisition_prelude_skipped_lanes")
    result["acquisition_prelude_errors"] = data.get("acquisition_prelude_errors")
    result["acquisition_prelude_duration_s"] = data.get("acquisition_prelude_duration_s")
    result["acquisition_prelude_reason"] = data.get("acquisition_prelude_reason")

    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_sprint_report(report_path: str | Path | None) -> dict | None:
    """
    Parse sprint JSON report for measurement metrics.

    F208C: Canonical acquisition_report path checked FIRST.
    Legacy fallback paths preserved for backward compatibility.

    Fail-soft: returns partial dict if some fields are missing.
    Returns None if report_path is None or parsing fails.
    """
    if not report_path:
        return None
    try:
        path = Path(report_path) if isinstance(report_path, str) else report_path
        with path.open() as f:
            data = json.load(f)

        # F208C: Canonical acquisition_report — checked FIRST before legacy paths
        canonical = _parse_canonical_sprint_report(data)
        if canonical is not None:
            return canonical

        # Legacy fallback: no acquisition_report
        return _parse_legacy_sprint_report(data)

    except Exception:
        return None
