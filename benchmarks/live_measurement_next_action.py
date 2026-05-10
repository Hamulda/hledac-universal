"""
F229A: NEXT ACTION MODULE — pure extracted from live_sprint_measurement.py.

Owns: NextActionInput, _derive_next_action, all _rule_* helpers, _was_family_attempted.

Pure: no runtime/scheduler/core/network/MLX imports.
Only imports MeasurementStatus from live_measurement_schema.
"""

from __future__ import annotations

__all__ = ["NextActionInput", "_derive_next_action", "_was_family_attempted"]

from dataclasses import dataclass

from benchmarks.live_measurement_schema import MeasurementStatus, RunQualityVerdict


def _was_family_attempted(runtime_truth: dict, family: str) -> bool:
    """Return True if a source family was attempted (not just present in branch_mix)."""
    branch_mix = runtime_truth.get("branch_mix", {})
    family_findings = branch_mix.get(f"{family}_findings", 0)
    if family_findings > 0:
        return True
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
    if has_barrier_telemetry and inp.runtime_truth.get("cycles_started", 0) > 0:
        if inp.prewindup_barrier_checked is False:
            return ("fix_prewindup_barrier_not_called", None)
        if inp.prewindup_barrier_checked is True and not inp.prewindup_barrier_satisfied:
            missing = [lane for lane in prewindup_required_lanes if lane not in prewindup_attempted_lanes]
            if missing:
                return (f"fix_prewindup_barrier_missing_lane:{missing[0]}", f"missing:{','.join(missing)}")
            return ("fix_prewindup_barrier_not_satisfied", None)
    return None


def _rule_profile_propagation(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules -1, -1b, -1d, -1e: Return guard and scheduler exit path rules (F207T, F207V-B, F208M)."""
    wg = inp.windup_guard_observation or {}
    wg_supplied = wg.get("callback_supplied_count", 0)
    wg_exec = wg.get("callback_executed_count", 0)
    if wg_supplied > 0 and wg_exec == 0:
        return ("fix_callback_execution", None)
    rg = inp.return_guard_observation or {}
    has_rg_telemetry = bool(rg)
    rg_checked: bool = bool(rg.get("checked")) if has_rg_telemetry else False
    rg_satisfied: bool = rg.get("satisfied", False)
    if has_rg_telemetry and not rg_checked and inp.runtime_truth.get("cycles_started", 0) > 0 and inp.runtime_truth.get("primary_signal_source") and inp.feed_findings > 0 and inp.ct_findings == 0 and inp.public_findings == 0:
        return ("fix_scheduler_return_guard_not_called", None)
    if has_rg_telemetry and rg_checked and not rg_satisfied:
        return ("fix_return_guard_terminal_state", None)
    wg_call_count = wg.get("windup_guard_call_count", 0)
    if wg_supplied == 0 and wg_call_count > 0:
        return ("fix_callback_wiring", None)
    se = inp.scheduler_exit
    se_path = se.get("exit_path") if se is not None else None
    if se is not None and not se_path:
        return ("add_scheduler_exit_tracer", None)
    if se is not None and se_path and se_path != "run_complete" and not rg_checked and inp.runtime_truth.get("cycles_started", 0) > 0 and inp.runtime_truth.get("primary_signal_source") and inp.feed_findings > 0 and inp.ct_findings == 0 and inp.public_findings == 0:
        return (f"patch_scheduler_exit_path:{se_path}", None)
    return None


def _rule_terminality(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 0e, 0f: Acquisition terminality wiring (F208F, F208M)."""
    if inp.acquisition_terminality_checked is False and inp.runtime_truth.get("cycles_started", 0) > 0 and inp.runtime_truth.get("primary_signal_source") and (inp.ct_findings > 0 or inp.public_findings > 0):
        return ("fix_terminality_wiring", None)
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
    if _is_domain and _has_nonfeed and inp.acquisition_prelude_checked is not True:
        if inp.acquisition_prelude_ran is False:
            return ("fix_acquisition_prelude_not_run", None)
        return ("fix_acquisition_prelude_not_checked", None)
    if _is_domain and _has_nonfeed and inp.acquisition_prelude_ran is True and inp.acquisition_prelude_checked is not True:
        return ("fix_acquisition_prelude_checked_wiring", None)
    return None


def _rule_quality_gate(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 1 (starvation), 1 (memory gate): Critical system quality gates."""
    if inp.nonfeed_starvation_suspected:
        return ("fix_nonfeed_scheduler_order", None)
    if inp.is_memory_gate_abort:
        return ("clean_memory", None)
    return None


def _rule_default(inp: NextActionInput) -> tuple[str, str | None] | None:
    """Rules 3, 2, 5, 4, 6, 7, default: Feed/public/ct/quality default rules."""
    if inp.public_fetch_attempted and inp.nonfeed_accepted_findings == 0 and inp.feed_findings == inp.total_findings and not _was_family_attempted(inp.runtime_truth, "ct"):
        return ("inspect_public_reject_reasons", inp.top_public_reject_reason)
    if inp.feed_dominance_score is not None and inp.feed_dominance_score >= 0.7:
        if inp.nonfeed_accepted_findings == 0:
            both_attempted = inp.public_fetch_attempted and _was_family_attempted(inp.runtime_truth, "ct")
            if both_attempted:
                return ("inspect_nonfeed_rejection_or_raw_counts", None)
            return ("improve_nonfeed_lanes", None)
    if inp.ct_findings == 0 and _was_family_attempted(inp.runtime_truth, "ct"):
        return ("inspect_ct_query_domain", None)
    if inp.total_findings > 0 and inp.feed_findings == inp.total_findings and _was_family_attempted(inp.runtime_truth, "ct") and inp.public_findings > 0:
        return ("improve_nonfeed_lanes", None)
    if inp.nonfeed_accepted_findings > 0:
        return ("run_active600_or_targeted_query", None)
    if inp.total_findings == 0 and inp.status == MeasurementStatus.COMPLETED:
        return ("inspect_empty_run", None)
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
    for helper in [
        _rule_wallclock_enforcement,
        _rule0g_prewindup_barrier,
        _rule_profile_propagation,
        _rule_provider_surface,
        _rule_terminality,
        _rule_quality_gate,
        _rule0b_memory_or_swap_gate,
        _rule_default,
    ]:
        result = helper(inp)
        if result is not None:
            return result
    return ("unknown", None)
