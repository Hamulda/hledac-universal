"""
F230A: LIVE MEASUREMENT KPI MODULE

Owns KPI derivation: LiveKpiInput, _derive_live_kpi, _derive_live_kpi_from_input,
and all discovery provider helpers (_derive_discovery_provider_status_debug, etc.).

Pure: no runtime/scheduler/core/network/MLX imports.
Imports only from benchmarks/ live_measurement_schema, live_measurement_next_action,
live_measurement_quality, and tools/research_quality_score.
"""

from __future__ import annotations

__all__ = [
    "LiveKpiInput",
    "_derive_live_kpi",
    "_derive_live_kpi_from_input",
    # Discovery helpers
    "_derive_discovery_provider_status_debug",
    "_derive_discovery_selected_providers",
    "_derive_discovery_skipped_providers",
    "_derive_discovery_stub_providers",
    "_derive_discovery_not_wired_providers",
]

from dataclasses import dataclass

from benchmarks.live_measurement_schema import MeasurementStatus, RunQualityVerdict
from benchmarks.live_measurement_next_action import (
    NextActionInput,
    _derive_next_action,
)
from benchmarks.live_measurement_quality import (
    _is_active_domain_query,
    _has_terminal_source_outcomes,
    _has_scheduler_exit_path,
)


# ---------------------------------------------------------------------------
# LiveKpiInput
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LiveKpiInput:
    """All inputs needed by _derive_live_kpi_from_input.

    Frozen dataclass ensures rule helpers are pure and cannot mutate inputs.
    All fields have explicit defaults so callers can pass by name.
    """

    status: MeasurementStatus
    is_memory_gate_abort: bool
    runtime_truth: dict | None
    actual_duration_s: float | None
    primary_signal_source: str | None
    run_quality_verdict: str | None
    hardware_constrained: bool | None
    public_pipeline: dict | None = None
    timing_truth: dict | None = None
    acquisition_strategy: dict | None = None
    windup_guard_observation: dict | None = None
    return_guard_observation: dict | None = None
    scheduler_exit: dict | None = None
    acquisition_report: dict | None = None
    profile_verdict: str | None = None
    acquisition_terminality_checked: bool | None = None
    acquisition_terminality_satisfied: bool | None = None
    acquisition_terminality_missing_lanes: tuple[str, ...] | None = None
    acquisition_terminality_report: dict | None = None
    explicit_source_family_outcomes: list[dict] | None = None
    acquisition_prelude_checked: bool | None = None
    acquisition_prelude_ran: bool | None = None
    acquisition_prelude_required_lanes: tuple[str, ...] | None = None
    acquisition_prelude_terminal_lanes: tuple[str, ...] | None = None
    acquisition_prelude_missing_lanes: tuple[str, ...] | None = None
    acquisition_prelude_skipped_lanes: dict | None = None
    acquisition_prelude_errors: dict | None = None
    acquisition_prelude_duration_s: float | None = None
    acquisition_prelude_reason: str | None = None
    planned_duration_s: float | None = None
    claims_runtime_status: dict | None = None


# ---------------------------------------------------------------------------
# Compatibility wrapper (old flat param list)
# ---------------------------------------------------------------------------

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
    explicit_source_family_outcomes: list[dict] | None = None,
    acquisition_prelude_checked: bool | None = None,
    acquisition_prelude_ran: bool | None = None,
    acquisition_prelude_required_lanes: tuple[str, ...] | None = None,
    acquisition_prelude_terminal_lanes: tuple[str, ...] | None = None,
    acquisition_prelude_missing_lanes: tuple[str, ...] | None = None,
    acquisition_prelude_skipped_lanes: dict | None = None,
    acquisition_prelude_errors: dict | None = None,
    acquisition_prelude_duration_s: float | None = None,
    acquisition_prelude_reason: str | None = None,
    planned_duration_s: float | None = None,
    claims_runtime_status: dict | None = None,
) -> dict:
    """
    Compatibility wrapper: accepts 31 explicit parameters, constructs LiveKpiInput,
    and delegates to _derive_live_kpi_from_input.

    Preserves the old 31-argument calling convention for backward compatibility
    with any direct callers outside this module.
    """
    inp = LiveKpiInput(
        status=status,
        is_memory_gate_abort=is_memory_gate_abort,
        runtime_truth=runtime_truth,
        actual_duration_s=actual_duration_s,
        primary_signal_source=primary_signal_source,
        run_quality_verdict=run_quality_verdict,
        hardware_constrained=hardware_constrained,
        public_pipeline=public_pipeline,
        timing_truth=timing_truth,
        acquisition_strategy=acquisition_strategy,
        windup_guard_observation=windup_guard_observation,
        return_guard_observation=return_guard_observation,
        scheduler_exit=scheduler_exit,
        acquisition_report=acquisition_report,
        profile_verdict=profile_verdict,
        acquisition_terminality_checked=acquisition_terminality_checked,
        acquisition_terminality_satisfied=acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=acquisition_terminality_missing_lanes,
        acquisition_terminality_report=acquisition_terminality_report,
        explicit_source_family_outcomes=explicit_source_family_outcomes,
        acquisition_prelude_checked=acquisition_prelude_checked,
        acquisition_prelude_ran=acquisition_prelude_ran,
        acquisition_prelude_required_lanes=acquisition_prelude_required_lanes,
        acquisition_prelude_terminal_lanes=acquisition_prelude_terminal_lanes,
        acquisition_prelude_missing_lanes=acquisition_prelude_missing_lanes,
        acquisition_prelude_skipped_lanes=acquisition_prelude_skipped_lanes,
        acquisition_prelude_errors=acquisition_prelude_errors,
        acquisition_prelude_duration_s=acquisition_prelude_duration_s,
        acquisition_prelude_reason=acquisition_prelude_reason,
        planned_duration_s=planned_duration_s,
        claims_runtime_status=claims_runtime_status,
    )
    return _derive_live_kpi_from_input(inp)


# ---------------------------------------------------------------------------
# Core KPI derivation
# ---------------------------------------------------------------------------

def _derive_live_kpi_from_input(inp: LiveKpiInput) -> dict:
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
      - branch_accepted_counts           (F211B)
      - lane_execution_counts            (F211B)
      - source_family_counts             (F211B)
      - source_family_outcomes_display   (F211B)
      - nonfeed_attempted_families       (F211B)
      - nonfeed_accepted_findings
      - public_fetch_attempted
      - public_acceptance_attempted       (F207K)
      - public_acceptance_accepted        (F207K)
      - public_acceptance_rejected        (F207K)
      - public_acceptance_reject_reasons  (F207K)
      - top_public_reject_reason          (F207K)
      - public_rejected_url_sample       (F207K)
      - feed_dominance_score
      - feed_balance_recommendation
      - estimated_per_source_soft_cap
      - dominant_feed_source
      - dominant_feed_share_pct
      - run_quality_verdict
      - hardware_constrained
      - next_action
      - next_action_detail               (F207K)
      - nonfeed_starvation_suspected      (F207M)
      - nonfeed_starvation_reason         (F207M)
      - windup_lead_requested_s          (F207M)
      - windup_lead_observed_s           (F207M)
      - active_window_budget_s           (F207M)
      - nonfeed_eligible_families        (F207M)
      - nonfeed_skipped_reasons          (F207M)
      - return_guard_checked             (F207T)
      - return_guard_required_lanes      (F207T)
      - return_guard_satisfied             (F207T)
      - return_guard_delayed_for_nonfeed  (F207T)
      - return_guard_block_reason         (F207T)
      - return_guard_attempted_lanes      (F207T)
      - return_guard_skipped_lanes        (F207T)
      - return_guard_errors               (F207T)
      - scheduler_exit_path              (F207V-B)
      - scheduler_exit_reason            (F207V-B)
      - scheduler_exit_phase              (F207V-B)
      - scheduler_exit_cycle             (F207V-B)
      - scheduler_exit_elapsed_s         (F207V-B)
      - scheduler_exit_guard_checked     (F207V-B)
      - scheduler_exit_guard_required    (F207V-B)
      - scheduler_exit_guard_satisfied    (F207V-B)
      - scheduler_deadline_enforced       (F212C)
      - scheduler_deadline_checks        (F212C)
      - scheduler_deadline_exit_path     (F212C)
      - hard_deadline_checked_count       (F212C)
      - hard_deadline_exceeded            (F212C)
      - hard_deadline_exceeded_at_cycle   (F212C)
      - hard_deadline_remaining_s_at_exit (F212C)
      - terminality_quality_verdict       (F208I)
      - terminality_failure_reasons       (F208I)
      - acquisition_report_schema_version (F208I)
      - explicit_source_family_outcomes   (F210B)
      - ct_loss_stage                     (F214D)
      - ct_bridge_invoked                 (F214D)
      - ct_raw_sample_count               (F214D)
      - ct_candidates_built               (F214D)
      - ct_bridge_rejections_count        (F214D)
      - ct_candidates_accumulated         (F214D)
      - ct_candidates_stored              (F214D)
      - ct_storage_rejected               (F214D)
      - claims_extracted_count            (F225A)
      - claims_polarity_mix               (F225A)
      - claims_packets_with_claims        (F225A)
      - discovery_provider_status_debug   (F225C)
      - discovery_selected_providers      (F225C)
      - discovery_skipped_providers       (F225C)
      - discovery_stub_providers          (F225C)
      - discovery_not_wired_providers     (F225C)
      - missing_canonical_fields

    F211B: Lane execution truth is split into three distinct views:
    - branch_accepted_counts: per-branch accepted findings from branch_mix (unchanged truth)
    - lane_execution_counts: per-family lane execution with terminal_state from source_family_outcomes
    - source_family_counts: derived from lane_execution_counts (accepted>0 only, FEED from branch_mix)
    - nonfeed_attempted_families: derived from lane_execution_counts (FEED always excluded)
    """
    rt = inp.runtime_truth or {}
    branch_mix = rt.get("branch_mix", {})
    lane_verdict = rt.get("lane_verdict", {}) or {}
    feed_telemetry = rt.get("feed_telemetry")

    if feed_telemetry:
        feed_dominance_score: float | None = feed_telemetry.get("feed_dominance_score")
        feed_balance_recommendation: str | None = feed_telemetry.get("feed_balance_recommendation")
        estimated_per_source_soft_cap: int | None = feed_telemetry.get("estimated_per_source_soft_cap")
        dominant_feed_source: str | None = feed_telemetry.get("dominant_feed_source")
        dominant_feed_share_pct: float | None = feed_telemetry.get("dominant_feed_share_pct")
    else:
        feed_findings = branch_mix.get("feed_findings", 0)
        public_findings = branch_mix.get("public_findings", 0)
        ct_findings = branch_mix.get("ct_findings", 0)
        total_findings = feed_findings + public_findings + ct_findings
        feed_dominance_score = round(feed_findings / total_findings, 4) if total_findings > 0 else None
        feed_balance_recommendation = None
        estimated_per_source_soft_cap = None
        dominant_feed_source = None
        dominant_feed_share_pct = None

    feed_findings = branch_mix.get("feed_findings", 0)
    public_findings = branch_mix.get("public_findings", 0)
    ct_findings = branch_mix.get("ct_findings", 0)
    total_findings = feed_findings + public_findings + ct_findings
    accepted_findings = rt.get("accepted_findings") or 0
    cycles_completed = rt.get("cycles_completed") or 0
    findings_per_min: float | None = None
    if inp.actual_duration_s and inp.actual_duration_s > 0:
        findings_per_min = round(total_findings / inp.actual_duration_s * 60, 2)

    branch_accepted_counts: dict[str, int] = {}
    if feed_findings > 0:
        branch_accepted_counts["feed"] = feed_findings
    if public_findings > 0:
        branch_accepted_counts["public"] = public_findings
    if ct_findings > 0:
        branch_accepted_counts["ct"] = ct_findings

    _sfo_list = (
        inp.explicit_source_family_outcomes
        if inp.explicit_source_family_outcomes is not None
        else (inp.acquisition_strategy or {}).get("source_family_outcomes", [])
    )
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
                _accepted = (
                    _entry.get("accepted_count")
                    or _entry.get("accepted_findings", 0)
                    or _entry.get("accepted", 0)
                )
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
                if _attempted:
                    source_family_outcomes_display.append(
                        {
                            "family": _fam,
                            "attempted": _attempted,
                            "terminal_state": _terminal_state,
                            "raw_count": _raw,
                            "accepted_findings": _accepted,
                            "error": _error,
                            "skipped": _skipped,
                        }
                    )
    else:
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
                source_family_outcomes_display.append(
                    {
                        "family": _fam,
                        "attempted": True,
                        "terminal_state": "COMPLETED",
                        "raw_count": _count,
                        "accepted_findings": _count,
                        "error": None,
                        "skipped": False,
                    }
                )

    source_family_counts: dict[str, int] = {}
    if feed_findings > 0:
        source_family_counts["feed"] = feed_findings
    for _fam, _data in lane_execution_counts.items():
        if _fam != "feed" and _data.get("accepted_count", 0) > 0:
            source_family_counts[_fam] = _data["accepted_count"]

    _sfo_has_canonical = isinstance(_sfo_list, list) and len(_sfo_list) > 0
    if _sfo_has_canonical:
        _lec_lower_keys = {k.lower(): k for k in lane_execution_counts.keys()}
        _seen_lower = set()
        nonfeed_attempted_families = [
            _lec_lower_keys[_fam.lower()]
            for _fam, _data in lane_execution_counts.items()
            if _fam.lower() != "feed"
            and _data.get("attempted")
            and (_fam.lower() not in _seen_lower)
            and not (_seen_lower.add(_fam.lower()) or False)
        ]
    else:
        nonfeed_attempted_families = "CANONICAL_FIELD_MISSING"

    nonfeed_accepted_findings = accepted_findings - feed_findings if accepted_findings else 0
    nonfeed_accepted_findings = max(0, nonfeed_accepted_findings)

    _has_public_signal = (
        inp.runtime_truth
        and inp.runtime_truth.get("public_branch_timed_out")
        or (
            inp.runtime_truth
            and inp.runtime_truth.get("branch_mix", {}).get("public_findings", 0) > 0
        )
    )
    if _has_public_signal:
        _pub_already_in_lec = "PUBLIC" in lane_execution_counts
        if not _pub_already_in_lec:
            _rt_timed_out = inp.runtime_truth.get("public_branch_timed_out") if inp.runtime_truth else False
            _sig_reason = "terminal:timeout" if _rt_timed_out else "terminal:no_outcome_recorded"
            lane_execution_counts["PUBLIC"] = {
                "attempted": True,
                "terminal_state": "ERROR" if _sig_reason == "terminal:timeout" else "ATTEMPTED_NO_RESULTS",
                "raw_count": 0,
                "accepted_count": 0,
                "error": _sig_reason,
                "skipped": False,
            }
            source_family_outcomes_display.append(
                {
                    "family": "PUBLIC",
                    "attempted": True,
                    "terminal_state": lane_execution_counts["PUBLIC"]["terminal_state"],
                    "raw_count": 0,
                    "accepted_findings": 0,
                    "error": _sig_reason,
                    "skipped": False,
                }
            )
            if isinstance(nonfeed_attempted_families, list):
                _has_pub_lower = any((_e.lower() == "public" for _e in nonfeed_attempted_families))
                if not _has_pub_lower:
                    nonfeed_attempted_families.append("PUBLIC")

    _pub_lec = lane_execution_counts.get("PUBLIC")
    if _pub_lec is not None:
        public_fetch_attempted = bool(_pub_lec.get("attempted", False))
    else:
        public_fetch_attempted = False

    pp = inp.public_pipeline or {}
    public_acceptance_attempted: int = pp.get("public_acceptance_attempted", 0)
    public_acceptance_accepted: int = pp.get("public_acceptance_accepted", 0)
    public_acceptance_rejected_count: int = pp.get("public_acceptance_rejected", 0)
    public_acceptance_reject_reasons: dict[str, int] = pp.get("public_acceptance_reject_reasons", {})
    public_rejected_url_sample: tuple = pp.get("public_rejected_url_sample", ())
    top_public_reject_reason: str | None = None
    if public_acceptance_reject_reasons:
        top_public_reject_reason = max(
            public_acceptance_reject_reasons, key=lambda k: public_acceptance_reject_reasons[k]
        )

    tt = inp.timing_truth or {}
    windup_lead_requested_s: float | None = tt.get("windup_lead_requested_s")
    windup_lead_observed_s: float | None = tt.get("windup_lead_observed_s")
    active_window_budget_s: int | None = tt.get("active_window_budget_s")
    active_runtime_occurred: bool = tt.get("active_runtime_occurred", False)

    as_dict = inp.acquisition_strategy or {}
    prewindup_barrier_checked: bool = as_dict.get("prewindup_barrier_checked", False)
    prewindup_barrier_satisfied: bool = as_dict.get("prewindup_barrier_satisfied", False)
    prewindup_required_lanes: list[str] = as_dict.get("prewindup_required_lanes", [])
    prewindup_attempted_lanes: list[str] = as_dict.get("prewindup_attempted_lanes", [])
    prewindup_skipped_lanes: dict[str, str] = as_dict.get("prewindup_skipped_lanes", {})
    windup_delayed_for_nonfeed: bool = as_dict.get("windup_delayed_for_nonfeed", False)
    nonfeed_scheduler_gap_resolved: bool | None = as_dict.get("nonfeed_scheduler_gap_resolved", None)
    source_family_outcomes: list[dict] | None = as_dict.get("source_family_outcomes")

    wg = inp.windup_guard_observation or {}
    rg = inp.return_guard_observation or {}

    nonfeed_eligible_families: list[str] = []
    nonfeed_skipped_reasons: dict[str, str] = {}
    if "public_findings" in branch_mix or "public_branch_timed_out" in rt:
        nonfeed_eligible_families.append("public")
    if "ct_findings" in branch_mix or "ct_branch_timed_out" in rt:
        nonfeed_eligible_families.append("ct")

    nonfeed_starvation_suspected: bool = False
    nonfeed_starvation_reason: str | None = None
    nonfeed_findings = public_findings + ct_findings
    starvation_suppressed = prewindup_barrier_checked and prewindup_barrier_satisfied
    if (
        inp.run_quality_verdict == RunQualityVerdict.PASS_VALID_CAPABILITY_RUN.value
        and not nonfeed_attempted_families
        and active_runtime_occurred
        and (feed_findings > 0)
        and (nonfeed_findings == 0)
        and (not starvation_suppressed)
    ):
        public_not_timed = not rt.get("public_branch_timed_out", False)
        ct_not_timed = not rt.get("ct_branch_timed_out", False)
        if public_not_timed and ct_not_timed:
            nonfeed_starvation_suspected = True
            nonfeed_starvation_reason = "early_windup_or_scheduler_order"

    wallclock_budget_exceeded = False
    wallclock_budget_excess_s: float | None = None
    wallclock_tolerance_s: float | None = None
    _wallclock_gate = inp.run_quality_verdict in (
        RunQualityVerdict.PASS_VALID_CAPABILITY_RUN.value,
        RunQualityVerdict.FAIL_WALLCLOCK_BUDGET_EXCEEDED.value,
    )
    if _wallclock_gate and inp.planned_duration_s is not None and (inp.actual_duration_s is not None):
        tolerance_s = max(inp.planned_duration_s * 1.1, inp.planned_duration_s + 30.0)
        wallclock_tolerance_s = tolerance_s
        if inp.actual_duration_s > tolerance_s:
            wallclock_budget_exceeded = True
            wallclock_budget_excess_s = round(inp.actual_duration_s - tolerance_s, 3)

    hard_deadline_checked_count: int = rt.get("hard_deadline_checked_count", 0)
    hard_deadline_exceeded: bool | None = rt.get("hard_deadline_exceeded", None)
    hard_deadline_exceeded_at_cycle: int | None = rt.get("hard_deadline_exceeded_at_cycle", None)
    hard_deadline_remaining_s_at_exit: float | None = rt.get("hard_deadline_remaining_s_at_exit", None)
    scheduler_deadline_checks: int = hard_deadline_checked_count
    scheduler_deadline_enforced: bool = hard_deadline_checked_count > 0 and hard_deadline_exceeded is not None
    scheduler_deadline_exit_path: str = (
        (inp.scheduler_exit or {}).get("exit_path", "") if hard_deadline_checked_count > 0 else ""
    )

    next_action, next_action_detail = _derive_next_action(
        status=inp.status,
        is_memory_gate_abort=inp.is_memory_gate_abort,
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
        scheduler_exit=inp.scheduler_exit,
        acquisition_terminality_checked=inp.acquisition_terminality_checked,
        acquisition_terminality_satisfied=inp.acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=(
            list(inp.acquisition_terminality_missing_lanes)
            if inp.acquisition_terminality_missing_lanes is not None
            else None
        ),
        run_quality_verdict=inp.run_quality_verdict,
        acquisition_prelude_checked=inp.acquisition_prelude_checked,
        acquisition_prelude_ran=inp.acquisition_prelude_ran,
        acquisition_prelude_required_lanes=(
            list(inp.acquisition_prelude_required_lanes)
            if inp.acquisition_prelude_required_lanes is not None
            else None
        ),
        acquisition_prelude_terminal_lanes=(
            list(inp.acquisition_prelude_terminal_lanes)
            if inp.acquisition_prelude_terminal_lanes is not None
            else None
        ),
        acquisition_prelude_missing_lanes=(
            list(inp.acquisition_prelude_missing_lanes)
            if inp.acquisition_prelude_missing_lanes is not None
            else None
        ),
        acquisition_prelude_skipped_lanes=inp.acquisition_prelude_skipped_lanes,
        acquisition_prelude_errors=inp.acquisition_prelude_errors,
        acquisition_prelude_duration_s=inp.acquisition_prelude_duration_s,
        acquisition_prelude_reason=inp.acquisition_prelude_reason,
        windup_guard_observation=wg,
        scheduler_deadline_enforced=scheduler_deadline_enforced,
        scheduler_deadline_checks=scheduler_deadline_checks,
    )

    terminality_quality_verdict: str | None = None
    terminality_failure_reasons: list[str] = []
    _is_domain = _is_active_domain_query(inp.runtime_truth, inp.profile_verdict)
    if _is_domain:
        _base_verdict = inp.run_quality_verdict or ""
        if _base_verdict in (
            RunQualityVerdict.FAIL_TERMINALITY_NOT_CHECKED.value,
            RunQualityVerdict.FAIL_TERMINALITY_UNSATISFIED.value,
            RunQualityVerdict.FAIL_MISSING_SOURCE_OUTCOMES.value,
            RunQualityVerdict.FAIL_SCHEDULER_EXIT_MISSING.value,
        ):
            terminality_quality_verdict = _base_verdict
            if not (
                inp.acquisition_report
                and isinstance(inp.acquisition_report, dict)
                and inp.acquisition_report.get("schema_version")
            ):
                terminality_failure_reasons.append("acquisition_report.schema_version missing")
            if inp.acquisition_terminality_checked is not True:
                terminality_failure_reasons.append(
                    f"acquisition_terminality_checked={inp.acquisition_terminality_checked!r}, expected True"
                )
            if inp.acquisition_terminality_satisfied is not True:
                terminality_failure_reasons.append(
                    f"acquisition_terminality_satisfied={inp.acquisition_terminality_satisfied!r}, expected True"
                )
            if not _has_terminal_source_outcomes(inp.acquisition_strategy):
                terminality_failure_reasons.append("source_family_outcomes missing or empty")
            if not _has_scheduler_exit_path(inp.scheduler_exit):
                terminality_failure_reasons.append("scheduler_exit_path missing or empty")

    acquisition_report_schema_version: str | None = None
    if inp.acquisition_report and isinstance(inp.acquisition_report, dict):
        acquisition_report_schema_version = inp.acquisition_report.get("schema_version")

    return {
        "total_findings": total_findings,
        "accepted_findings": accepted_findings,
        "cycles_completed": cycles_completed,
        "findings_per_min": findings_per_min,
        "primary_signal_source": inp.primary_signal_source,
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
        # F231A: PUBLIC Candidate Ledger
        "public_candidate_ledger_summary": {
            "discovered": inp.public_pipeline.get("public_candidates_discovered", 0) if inp.public_pipeline else 0,
            "fetch_attempted": inp.public_pipeline.get("public_candidates_fetch_attempted", 0) if inp.public_pipeline else 0,
            "fetch_success": inp.public_pipeline.get("public_candidates_fetch_success", 0) if inp.public_pipeline else 0,
            "parse_success": inp.public_pipeline.get("public_candidates_parse_success", 0) if inp.public_pipeline else 0,
            "pattern_matched": inp.public_pipeline.get("public_candidates_pattern_matched", 0) if inp.public_pipeline else 0,
            "built": inp.public_pipeline.get("public_candidates_built", 0) if inp.public_pipeline else 0,
            "store_attempted": inp.public_pipeline.get("public_candidates_store_attempted", 0) if inp.public_pipeline else 0,
            "stored": inp.public_pipeline.get("public_candidates_stored", 0) if inp.public_pipeline else 0,
            "rejected": inp.public_pipeline.get("public_candidates_rejected", 0) if inp.public_pipeline else 0,
        },
        "public_terminal_stage": inp.public_pipeline.get("public_terminal_stage", "") if inp.public_pipeline else "",
        "public_stage_counters": {
            "discovery_empty": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "discovery_empty") else 0,
            "fetch_zero": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "fetch_zero") else 0,
            "parse_zero": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "parse_zero") else 0,
            "match_zero": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "match_zero") else 0,
            "build_zero": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "build_zero") else 0,
            "store_zero": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "store_zero") else 0,
            "accepted": 1 if (inp.public_pipeline.get("public_terminal_stage", "") == "accepted") else 0,
        },
        "feed_dominance_score": feed_dominance_score,
        "feed_balance_recommendation": feed_balance_recommendation,
        "estimated_per_source_soft_cap": estimated_per_source_soft_cap,
        "dominant_feed_source": dominant_feed_source,
        "dominant_feed_share_pct": dominant_feed_share_pct,
        "run_quality_verdict": inp.run_quality_verdict,
        "hardware_constrained": inp.hardware_constrained,
        "wallclock_budget_exceeded": wallclock_budget_exceeded,
        "wallclock_budget_excess_s": wallclock_budget_excess_s,
        "wallclock_tolerance_s": wallclock_tolerance_s,
        "next_action": next_action,
        "next_action_detail": next_action_detail,
        "runtime_budget_action_family": "fix_runtime_budget_enforcement" if wallclock_budget_exceeded else None,
        "deadline_action_detail": next_action if wallclock_budget_exceeded else None,
        "nonfeed_starvation_suspected": nonfeed_starvation_suspected,
        "nonfeed_starvation_reason": nonfeed_starvation_reason,
        "windup_lead_requested_s": windup_lead_requested_s,
        "windup_lead_observed_s": windup_lead_observed_s,
        "active_window_budget_s": active_window_budget_s,
        "nonfeed_eligible_families": nonfeed_eligible_families,
        "nonfeed_skipped_reasons": nonfeed_skipped_reasons,
        "prewindup_barrier_checked": prewindup_barrier_checked,
        "prewindup_barrier_satisfied": prewindup_barrier_satisfied,
        "prewindup_required_lanes": prewindup_required_lanes,
        "prewindup_attempted_lanes": prewindup_attempted_lanes,
        "prewindup_skipped_lanes": prewindup_skipped_lanes,
        "windup_delayed_for_nonfeed": windup_delayed_for_nonfeed,
        "nonfeed_scheduler_gap_resolved": nonfeed_scheduler_gap_resolved,
        "source_family_outcomes": (
            inp.explicit_source_family_outcomes if inp.explicit_source_family_outcomes is not None else source_family_outcomes
        ),
        "windup_guard_call_count": wg.get("call_count", 0),
        "windup_guard_callback_supplied_count": wg.get("callback_supplied_count", 0),
        "windup_guard_callback_executed_count": wg.get("callback_executed_count", 0),
        "windup_guard_last_reason": wg.get("last_reason", ""),
        "windup_guard_last_phase": wg.get("last_phase", ""),
        "windup_guard_last_allowed": wg.get("last_allowed"),
        "return_guard_checked": rg.get("checked", False),
        "return_guard_required_lanes": rg.get("required_lanes", []),
        "return_guard_satisfied": rg.get("satisfied", False),
        "return_guard_delayed_for_nonfeed": rg.get("delayed_for_nonfeed", False),
        "return_guard_block_reason": rg.get("block_reason", ""),
        "return_guard_attempted_lanes": rg.get("attempted_lanes", []),
        "return_guard_skipped_lanes": rg.get("skipped_lanes", {}),
        "return_guard_errors": rg.get("errors", []),
        "scheduler_exit_path": (inp.scheduler_exit or {}).get("exit_path", ""),
        "scheduler_exit_reason": (inp.scheduler_exit or {}).get("exit_reason", ""),
        "scheduler_exit_phase": (inp.scheduler_exit or {}).get("exit_phase", ""),
        "scheduler_exit_cycle": (inp.scheduler_exit or {}).get("exit_cycle", ""),
        "scheduler_exit_elapsed_s": (inp.scheduler_exit or {}).get("elapsed_s", ""),
        "scheduler_exit_guard_checked": (inp.scheduler_exit or {}).get("guard_checked", ""),
        "scheduler_exit_guard_required": (inp.scheduler_exit or {}).get("guard_required", ""),
        "scheduler_exit_guard_satisfied": (inp.scheduler_exit or {}).get("guard_satisfied", ""),
        "scheduler_deadline_enforced": scheduler_deadline_enforced,
        "scheduler_deadline_checks": scheduler_deadline_checks,
        "scheduler_deadline_exit_path": scheduler_deadline_exit_path,
        "hard_deadline_checked_count": hard_deadline_checked_count,
        "hard_deadline_exceeded": hard_deadline_exceeded,
        "hard_deadline_exceeded_at_cycle": hard_deadline_exceeded_at_cycle,
        "hard_deadline_remaining_s_at_exit": hard_deadline_remaining_s_at_exit,
        "acquisition_terminality_checked": (
            bool(inp.acquisition_terminality_checked) if inp.acquisition_terminality_checked is not None else None
        ),
        "acquisition_terminality_satisfied": (
            bool(inp.acquisition_terminality_satisfied) if inp.acquisition_terminality_satisfied is not None else None
        ),
        "acquisition_terminality_missing_lanes": (
            list(inp.acquisition_terminality_missing_lanes)
            if inp.acquisition_terminality_missing_lanes is not None
            else None
        ),
        "acquisition_terminality_report": inp.acquisition_terminality_report,
        "terminality_quality_verdict": terminality_quality_verdict,
        "terminality_failure_reasons": terminality_failure_reasons,
        "acquisition_report_schema_version": acquisition_report_schema_version,
        "acquisition_prelude_checked": (
            bool(inp.acquisition_prelude_checked) if inp.acquisition_prelude_checked is not None else None
        ),
        "acquisition_prelude_ran": (
            bool(inp.acquisition_prelude_ran) if inp.acquisition_prelude_ran is not None else None
        ),
        "acquisition_prelude_required_lanes": (
            list(inp.acquisition_prelude_required_lanes)
            if inp.acquisition_prelude_required_lanes is not None
            else None
        ),
        "acquisition_prelude_terminal_lanes": (
            list(inp.acquisition_prelude_terminal_lanes)
            if inp.acquisition_prelude_terminal_lanes is not None
            else None
        ),
        "acquisition_prelude_missing_lanes": (
            list(inp.acquisition_prelude_missing_lanes)
            if inp.acquisition_prelude_missing_lanes is not None
            else None
        ),
        "acquisition_prelude_skipped_lanes": inp.acquisition_prelude_skipped_lanes,
        "acquisition_prelude_errors": inp.acquisition_prelude_errors,
        "acquisition_prelude_duration_s": inp.acquisition_prelude_duration_s,
        "acquisition_prelude_reason": inp.acquisition_prelude_reason,
        "ct_loss_stage": lane_verdict.get("ct_loss_stage", "no_loss") if isinstance(lane_verdict, dict) else "no_loss",
        "ct_bridge_invoked": lane_verdict.get("ct_bridge_invoked", False) if isinstance(lane_verdict, dict) else False,
        "ct_raw_sample_count": lane_verdict.get("ct_raw_sample_count", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_candidates_built": lane_verdict.get("ct_candidates_built", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_bridge_rejections_count": lane_verdict.get("ct_bridge_rejections_count", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_candidates_accumulated": lane_verdict.get("ct_candidates_accumulated", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_candidates_stored": lane_verdict.get("ct_candidates_stored", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_storage_rejected": lane_verdict.get("ct_storage_rejected", 0) if isinstance(lane_verdict, dict) else 0,
        # F231B: CT expansion clue summary — domain expansion evidence visible even when accepted=0
        "ct_expansion_clues_count": lane_verdict.get("ct_expansion_clues_count", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_valid_public_domains": lane_verdict.get("ct_valid_public_domains", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_wildcard_domains": lane_verdict.get("ct_wildcard_domains", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_private_reserved_domains": lane_verdict.get("ct_private_reserved_domains", 0) if isinstance(lane_verdict, dict) else 0,
        "ct_duplicate_candidates": lane_verdict.get("ct_duplicate_candidates", 0) if isinstance(lane_verdict, dict) else 0,
        # F231C: Wayback advisory evidence surface
        "wayback_advisory_clues_count": lane_verdict.get("wayback_advisory_clues_count", 0) if isinstance(lane_verdict, dict) else 0,
        "wayback_changed_url_count": lane_verdict.get("wayback_changed_url_count", 0) if isinstance(lane_verdict, dict) else 0,
        "wayback_added_url_count": lane_verdict.get("wayback_added_url_count", 0) if isinstance(lane_verdict, dict) else 0,
        "wayback_digest_changed_count": lane_verdict.get("wayback_digest_changed_count", 0) if isinstance(lane_verdict, dict) else 0,
        "wayback_unchanged_rejected": lane_verdict.get("wayback_unchanged_rejected", 0) if isinstance(lane_verdict, dict) else 0,
        # F231C: PassiveDNS advisory evidence surface
        "passive_dns_advisory_clues_count": lane_verdict.get("passive_dns_advisory_clues_count", 0) if isinstance(lane_verdict, dict) else 0,
        "passive_dns_private_ip_rejected": lane_verdict.get("passive_dns_private_ip_rejected", 0) if isinstance(lane_verdict, dict) else 0,
        "passive_dns_empty_ip_rejected": lane_verdict.get("passive_dns_empty_ip_rejected", 0) if isinstance(lane_verdict, dict) else 0,
        "claims_extracted_count": (inp.claims_runtime_status or {}).get("claims_extracted_count", 0)
        if inp.claims_runtime_status
        else 0,
        "claims_polarity_mix": {
            "positive": (inp.claims_runtime_status or {}).get("claims_positive_count", 0)
            if inp.claims_runtime_status
            else 0,
            "negative": (inp.claims_runtime_status or {}).get("claims_negative_count", 0)
            if inp.claims_runtime_status
            else 0,
            "neutral": (inp.claims_runtime_status or {}).get("claims_neutral_count", 0)
            if inp.claims_runtime_status
            else 0,
        },
        "claims_packets_with_claims": (inp.claims_runtime_status or {}).get("claims_extraction_packets_with_claims", 0)
        if inp.claims_runtime_status
        else 0,
        "discovery_provider_status_debug": _derive_discovery_provider_status_debug(inp.acquisition_report),
        "discovery_selected_providers": _derive_discovery_selected_providers(inp.acquisition_report),
        "discovery_skipped_providers": _derive_discovery_skipped_providers(inp.acquisition_report),
        "discovery_stub_providers": _derive_discovery_stub_providers(inp.acquisition_report),
        "discovery_not_wired_providers": _derive_discovery_not_wired_providers(inp.acquisition_report),
        "missing_canonical_fields": ["source_family_outcomes"] if not _sfo_has_canonical else [],
    }


# ---------------------------------------------------------------------------
# Discovery provider helpers
# ---------------------------------------------------------------------------

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
            result.append(
                {
                    "provider": entry.get("provider", ""),
                    "state": str(state) if state is not None else "",
                    "selected": bool(entry.get("selected", False)),
                    "reason": entry.get("reason", ""),
                }
            )
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