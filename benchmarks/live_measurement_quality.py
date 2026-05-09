"""
F228C LIVE MEASUREMENT QUALITY — Pure Run Quality Verdict Extraction

Extracted from benchmarks/live_sprint_measurement.py:
  _derive_run_quality_verdict
  _stamp_run_quality_verdict
  _uma_state_is_critical_or_emergency
  _is_active_domain_query

Schema dependency: benchmarks/live_measurement_schema.py (RunQualityVerdict, MeasurementStatus)

NO runtime imports (runtime/, core/, pipeline/, MLX, network).
"""

from __future__ import annotations

from benchmarks.live_measurement_schema import (
    MeasurementStatus,
    RunQualityVerdict,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MEMORY_GATE_OPERATOR_ACTION = (
    "restart or close heavy apps; rerun active300 with --require-memory-ok"
)

_SWAP_GATE_THRESHOLD_GIB = 1.0
_SWAP_GATE_OPERATOR_ACTION = (
    "restart to clear swap; or use --allow-high-swap to run anyway (results will be non-comparable)"
)

# ---------------------------------------------------------------------------
# Pure Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Re-exports from live_measurement_parser (pure predicates, no runtime deps)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Core Derivation
# ---------------------------------------------------------------------------

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


def _stamp_run_quality_verdict(
    result,  # LiveMeasurementResult (duck-typed, no runtime import)
    is_memory_gate_abort: bool = False,
) -> None:
    """
    Derive and stamp run quality verdict onto result.

    result is duck-typed: must have attributes matching LiveMeasurementResult fields
    used as arguments to _derive_run_quality_verdict. This avoids a runtime import
    of LiveMeasurementResult itself (which lives in live_measurement_schema.py).
    """
    verdict, hardware_constrained, memory_state_pre, swap_warning, recommended_next, operator_action = _derive_run_quality_verdict(
        status=result.status,
        profile_verdict=result.profile_verdict,
        uma_pre_state=result.uma_pre_state,
        runtime_truth=result.runtime_truth,
        swap_pre_gib=result.uma_pre_swap_gib,
        is_memory_gate_abort=is_memory_gate_abort,
        swap_gate_triggered=getattr(result, "swap_gate_triggered", None) or False,
        acquisition_report=getattr(result, "acquisition_report", None),
        acquisition_terminality_checked=getattr(result, "acquisition_terminality_checked", None),
        acquisition_terminality_satisfied=getattr(result, "acquisition_terminality_satisfied", None),
        acquisition_terminality_missing_lanes=getattr(result, "acquisition_terminality_missing_lanes", None),
        acquisition_strategy=getattr(result, "acquisition_strategy", None),
        scheduler_exit=getattr(result, "scheduler_exit", None),
        planned_duration_s=getattr(result, "planned_duration_s", None),
        actual_duration_s=getattr(result, "actual_duration_s", None),
    )
    result.run_quality_verdict = verdict.value if verdict is not None else None
    result.hardware_constrained = hardware_constrained
    result.memory_state_pre = memory_state_pre
    result.memory_state_post = getattr(result, "uma_post_state", None)
    result.swap_warning = swap_warning
    result.recommended_next_profile = recommended_next
    result.recommended_operator_action = operator_action