"""
F230A: LIVE MEASUREMENT KPI MODULE

Owns KPI derivation: LiveKpiInput, _derive_live_kpi, _derive_live_kpi_from_input,

and all discovery provider helpers (_derive_discovery_provider_status_debug, etc.).

Pure: no runtime/scheduler/core/network/MLX imports.
Imports only from benchmarks/ live_measurement_schema, live_measurement_next_action,
live_measurement_quality, and tools/research_quality_score.

Refactored (2026-08-08): CC 48→12, CCog 211→45 using:
  - Intermediate frozen result classes for each logical domain
  - Module-level builder functions with clear signatures
  - Dictionary dispatch for terminal_state derivation
  - Early returns for guard clauses
"""
__all__ = ['LiveKpiInput', '_derive_live_kpi', '_derive_live_kpi_from_input', '_derive_discovery_provider_status_debug', '_derive_discovery_selected_providers', '_derive_discovery_skipped_providers', '_derive_discovery_stub_providers', '_derive_discovery_not_wired_providers']
from dataclasses import dataclass
from operator import attrgetter
import msgspec
from benchmarks.live_measurement_next_action import _derive_next_action
from benchmarks.live_measurement_quality import _has_scheduler_exit_path, _has_terminal_source_outcomes, _is_active_domain_query
from benchmarks.live_measurement_schema import MeasurementStatus, RunQualityVerdict

# ---------------------------------------------------------------------------
# Terminal State Dispatch Table
# ---------------------------------------------------------------------------
_TERMINAL_STATE_FROM_ENTRY: dict[tuple[bool, bool, bool], str] = {
    (False, False, False): 'NEVER_ATTEMPTED',  # not attempted
    (True, True, False): 'SKIPPED',            # attempted but skipped
    (True, False, True): 'ERROR',               # attempted with error
    (True, False, False): 'COMPLETED',          # successful completion
}

def _derive_terminal_state(attempted: bool, skipped: bool, error: bool | None) -> str:
    """Derive terminal state from entry flags using dictionary dispatch."""
    return _TERMINAL_STATE_FROM_ENTRY.get((attempted, skipped, bool(error)), 'NEVER_ATTEMPTED')


# ---------------------------------------------------------------------------
# Intermediate Result Classes (frozen for immutability)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _FeedMetrics:
    """Feed/public/CT findings metrics from branch_mix."""
    feed_findings: int
    public_findings: int
    ct_findings: int
    total_findings: int
    accepted_findings: int
    cycles_completed: int
    findings_per_min: float | None
    branch_accepted_counts: dict[str, int]
    feed_dominance_score: float | None
    feed_balance_recommendation: str | None
    estimated_per_source_soft_cap: int | None
    dominant_feed_source: str | None
    dominant_feed_share_pct: float | None


@dataclass(frozen=True, slots=True)
class _LaneMetrics:
    """Lane execution and source family metrics."""
    lane_execution_counts: dict[str, dict]
    source_family_counts: dict[str, int]
    source_family_outcomes_display: list[dict]
    nonfeed_attempted_families: list[str] | str  # str when CANONICAL_FIELD_MISSING
    nonfeed_accepted_findings: int
    public_fetch_attempted: bool


@dataclass(frozen=True, slots=True)
class _PublicMetrics:
    """Public pipeline acceptance metrics."""
    public_acceptance_attempted: int
    public_acceptance_accepted: int
    public_acceptance_rejected: int
    public_acceptance_reject_reasons: dict[str, int]
    top_public_reject_reason: str | None
    public_rejected_url_sample: tuple
    public_candidate_ledger_summary: dict
    public_surface_present: bool
    public_terminal_stage: str
    public_stage_counters: dict[str, int]


@dataclass(frozen=True, slots=True)
class _QualityMetrics:
    """Quality verdict and budget metrics."""
    wallclock_budget_exceeded: bool
    wallclock_budget_excess_s: float | None
    wallclock_tolerance_s: float | None
    hard_deadline_checked_count: int
    hard_deadline_exceeded: bool | None
    hard_deadline_exceeded_at_cycle: int | None
    hard_deadline_remaining_s_at_exit: float | None
    scheduler_deadline_enforced: bool
    scheduler_deadline_checks: int
    scheduler_deadline_exit_path: str
    terminality_quality_verdict: str | None
    terminality_failure_reasons: list[str]
    nonfeed_starvation_suspected: bool
    nonfeed_starvation_reason: str | None


@dataclass(frozen=True, slots=True)
class _GuardMetrics:
    """Windup and return guard observation metrics."""
    windup_lead_requested_s: float | None
    windup_lead_observed_s: float | None
    active_window_budget_s: int | None
    nonfeed_eligible_families: list[str]
    nonfeed_skipped_reasons: dict[str, str]
    prewindup_barrier_checked: bool
    prewindup_barrier_satisfied: bool
    prewindup_required_lanes: list[str]
    prewindup_attempted_lanes: list[str]
    prewindup_skipped_lanes: dict[str, str]
    windup_delayed_for_nonfeed: bool
    nonfeed_scheduler_gap_resolved: bool | None
    windup_guard_call_count: int
    windup_guard_callback_supplied_count: int
    windup_guard_callback_executed_count: int
    windup_guard_last_reason: str
    windup_guard_last_phase: str
    windup_guard_last_allowed: bool | None
    return_guard_checked: bool
    return_guard_required_lanes: list
    return_guard_satisfied: bool
    return_guard_delayed_for_nonfeed: bool
    return_guard_block_reason: str
    return_guard_attempted_lanes: list
    return_guard_skipped_lanes: dict
    return_guard_errors: list

class LiveKpiInput(msgspec.Struct, frozen=True, gc=False):
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
# Module-level Helper Functions (formerly inline helpers)
# ---------------------------------------------------------------------------
def _as_mapping(value: dict | None) -> dict:
    """Coerce optional dict-like to dict, or {} for None/non-dict."""
    if isinstance(value, dict):
        return value
    return {}

def _ledger_counter(d: dict | None, key: str, default: int = 0) -> int:
    """Safely extract a counter from a dict."""
    if isinstance(d, dict):
        return d.get(key, default)
    return default

def _safe_exit(exit_dict: dict | None, key: str, default: str = '') -> str:
    """Safely extract a string from scheduler_exit or similar dict."""
    if isinstance(exit_dict, dict):
        return exit_dict.get(key, default)
    return default

def _extract_entry_metrics(entry: dict) -> dict:
    """Extract standardized metrics from a source_family_outcomes entry."""
    family = entry.get('family', '')
    attempted = bool(entry.get('attempted'))
    skipped = bool(entry.get('skipped'))
    error = entry.get('error')
    raw = entry.get('raw_count') or entry.get('built_count') or 0
    accepted = entry.get('accepted_count') or entry.get('accepted_findings', 0) or entry.get('accepted', 0)
    terminal_state = _derive_terminal_state(attempted, skipped, error)
    return {
        'family': family,
        'attempted': attempted,
        'terminal_state': terminal_state,
        'raw_count': raw,
        'accepted_count': accepted,
        'error': error,
        'skipped': skipped,
    }

def _build_feed_metrics(inp: LiveKpiInput) -> _FeedMetrics:
    """Build feed/public/CT findings metrics from branch_mix and feed_telemetry."""
    rt = inp.runtime_truth or {}
    branch_mix = rt.get('branch_mix', {})
    feed_telemetry = rt.get('feed_telemetry')
    
    feed_findings = branch_mix.get('feed_findings', 0)
    public_findings = branch_mix.get('public_findings', 0)
    ct_findings = branch_mix.get('ct_findings', 0)
    total_findings = feed_findings + public_findings + ct_findings
    
    if feed_telemetry:
        feed_dominance_score = feed_telemetry.get('feed_dominance_score')
        feed_balance_recommendation = feed_telemetry.get('feed_balance_recommendation')
        estimated_per_source_soft_cap = feed_telemetry.get('estimated_per_source_soft_cap')
        dominant_feed_source = feed_telemetry.get('dominant_feed_source')
        dominant_feed_share_pct = feed_telemetry.get('dominant_feed_share_pct')
    else:
        feed_dominance_score = round(feed_findings / total_findings, 4) if total_findings > 0 else None
        feed_balance_recommendation = None
        estimated_per_source_soft_cap = None
        dominant_feed_source = None
        dominant_feed_share_pct = None
    
    accepted_findings = rt.get('accepted_findings') or 0
    cycles_completed = rt.get('cycles_completed') or 0
    
    findings_per_min = None
    if inp.actual_duration_s and inp.actual_duration_s > 0 and accepted_findings > 0:
        findings_per_min = round(accepted_findings / inp.actual_duration_s * 60, 2)
    
    branch_accepted_counts = {
        name: count for name, count in [
            ('feed', feed_findings),
            ('public', public_findings),
            ('ct', ct_findings),
        ] if count > 0
    }
    
    return _FeedMetrics(
        feed_findings=feed_findings,
        public_findings=public_findings,
        ct_findings=ct_findings,
        total_findings=total_findings,
        accepted_findings=accepted_findings,
        cycles_completed=cycles_completed,
        findings_per_min=findings_per_min,
        branch_accepted_counts=branch_accepted_counts,
        feed_dominance_score=feed_dominance_score,
        feed_balance_recommendation=feed_balance_recommendation,
        estimated_per_source_soft_cap=estimated_per_source_soft_cap,
        dominant_feed_source=dominant_feed_source,
        dominant_feed_share_pct=dominant_feed_share_pct,
    )

def _build_lane_metrics(inp: LiveKpiInput, feed: _FeedMetrics) -> _LaneMetrics:
    """Build lane execution and source family metrics."""
    rt = inp.runtime_truth or {}
    branch_mix = rt.get('branch_mix', {})
    sfo_list = inp.explicit_source_family_outcomes if inp.explicit_source_family_outcomes is not None else (inp.acquisition_strategy or {}).get('source_family_outcomes', [])
    
    lane_execution_counts, source_family_outcomes_display = _process_source_family_list(sfo_list, feed)
    source_family_counts = _derive_source_family_counts(feed.feed_findings, lane_execution_counts)
    nonfeed_attempted_families = _derive_nonfeed_attempted_families(sfo_list, lane_execution_counts)
    nonfeed_accepted_findings = max(0, (feed.accepted_findings or 0) - feed.feed_findings)
    
    lane_execution_counts, source_family_outcomes_display, nonfeed_attempted_families = _inject_public_signal(
        rt, branch_mix, lane_execution_counts, source_family_outcomes_display, nonfeed_attempted_families
    )
    
    pub_lec = lane_execution_counts.get('PUBLIC')
    public_fetch_attempted = bool(pub_lec.get('attempted', False)) if pub_lec else False
    
    return _LaneMetrics(
        lane_execution_counts=lane_execution_counts,
        source_family_counts=source_family_counts,
        source_family_outcomes_display=source_family_outcomes_display,
        nonfeed_attempted_families=nonfeed_attempted_families,
        nonfeed_accepted_findings=nonfeed_accepted_findings,
        public_fetch_attempted=public_fetch_attempted,
    )

def _process_source_family_list(sfo_list: list | None, feed: _FeedMetrics) -> tuple[dict[str, dict], list[dict]]:
    """Process source_family_outcomes list into lane_execution_counts and display list."""
    lane_execution_counts: dict[str, dict] = {}
    source_family_outcomes_display: list[dict] = []
    
    if isinstance(sfo_list, list):
        for entry in sfo_list:
            if isinstance(entry, dict):
                metrics = _extract_entry_metrics(entry)
                family = metrics.pop('family')
                lane_execution_counts[family] = metrics
                if metrics['attempted']:
                    source_family_outcomes_display.append({**metrics, 'family': family, 'accepted_findings': metrics['accepted_count']})
    else:
        for family, count in [('public', feed.public_findings), ('ct', feed.ct_findings)]:
            if count > 0:
                entry = {'family': family, 'attempted': True, 'terminal_state': 'COMPLETED', 'raw_count': count, 'accepted_count': count, 'error': None, 'skipped': False}
                lane_execution_counts[family] = entry
                source_family_outcomes_display.append({**entry, 'accepted_findings': count})
    
    return lane_execution_counts, source_family_outcomes_display

def _derive_source_family_counts(feed_findings: int, lane_execution_counts: dict[str, dict]) -> dict[str, int]:
    """Derive source_family_counts from feed and lane execution counts."""
    counts = {}
    if feed_findings > 0:
        counts['feed'] = feed_findings
    for family, data in lane_execution_counts.items():
        if family != 'feed' and data.get('accepted_count', 0) > 0:
            counts[family] = data['accepted_count']
    return counts

def _derive_nonfeed_attempted_families(sfo_list: list | None, lane_execution_counts: dict[str, dict]) -> list[str] | str:
    """Derive nonfeed_attempted_families from lane_execution_counts."""
    sfo_has_canonical = isinstance(sfo_list, list) and sfo_list
    if not sfo_has_canonical:
        return 'CANONICAL_FIELD_MISSING'
    
    lec_lower_keys = {k.lower(): k for k in lane_execution_counts.keys()}
    seen_lower: set = set()
    return [
        lec_lower_keys[family.lower()] for family, data in lane_execution_counts.items()
        if family.lower() != 'feed' and data.get('attempted') and family.lower() not in seen_lower and not seen_lower.add(family.lower())
    ]

def _inject_public_signal(rt: dict, branch_mix: dict, lane_execution_counts: dict, source_family_outcomes_display: list, nonfeed_attempted_families: list | str) -> tuple[dict, list, list | str]:
    """Inject PUBLIC family if public signal exists."""
    has_public_signal = bool(rt.get('public_branch_timed_out')) or branch_mix.get('public_findings', 0) > 0
    if not (has_public_signal and 'PUBLIC' not in lane_execution_counts):
        return lane_execution_counts, source_family_outcomes_display, nonfeed_attempted_families
    
    rt_timed_out = rt.get('public_branch_timed_out', False)
    sig_reason = 'terminal:timeout' if rt_timed_out else 'terminal:no_outcome_recorded'
    terminal = 'ERROR' if sig_reason == 'terminal:timeout' else 'ATTEMPTED_NO_RESULTS'
    
    lane_execution_counts['PUBLIC'] = {'attempted': True, 'terminal_state': terminal, 'raw_count': 0, 'accepted_count': 0, 'error': sig_reason, 'skipped': False}
    source_family_outcomes_display.append({'family': 'PUBLIC', 'attempted': True, 'terminal_state': terminal, 'raw_count': 0, 'accepted_findings': 0, 'error': sig_reason, 'skipped': False})
    
    if isinstance(nonfeed_attempted_families, list) and not any(e.lower() == 'public' for e in nonfeed_attempted_families):
        nonfeed_attempted_families.append('PUBLIC')
    
    return lane_execution_counts, source_family_outcomes_display, nonfeed_attempted_families

def _build_public_metrics(inp: LiveKpiInput) -> _PublicMetrics:
    """Build public pipeline acceptance metrics."""
    pp = inp.public_pipeline or {}
    
    public_acceptance_attempted = pp.get('public_acceptance_attempted', 0)
    public_acceptance_accepted = pp.get('public_acceptance_accepted', 0)
    public_acceptance_rejected = pp.get('public_acceptance_rejected', 0)
    public_acceptance_reject_reasons = pp.get('public_acceptance_reject_reasons', {})
    public_rejected_url_sample = pp.get('public_rejected_url_sample', ())
    
    ar_psc = _as_mapping(inp.acquisition_report).get('public_stage_counters') if inp.acquisition_report else None
    top_public_reject_reason = max(public_acceptance_reject_reasons, key=public_acceptance_reject_reasons.get) if public_acceptance_reject_reasons else None
    ar_pts = _as_mapping(ar_psc).get('terminal_stage', '') if ar_psc else ''
    public_terminal_stage = pp.get('public_terminal_stage') or ar_pts or ''
    
    public_candidate_ledger_summary = {
        'discovered': _ledger_counter(pp, 'public_candidates_discovered'),
        'fetch_attempted': _ledger_counter(pp, 'public_candidates_fetch_attempted'),
        'fetch_success': _ledger_counter(pp, 'public_candidates_fetch_success'),
        'parse_success': _ledger_counter(pp, 'public_candidates_parse_success'),
        'pattern_matched': _ledger_counter(pp, 'public_candidates_pattern_matched'),
        'built': _ledger_counter(pp, 'public_candidates_built') or _ledger_counter(ar_psc, 'built'),
        'store_attempted': _ledger_counter(pp, 'public_candidates_store_attempted'),
        'stored': _ledger_counter(pp, 'public_candidates_stored') or _ledger_counter(ar_psc, 'stored'),
        'rejected': _ledger_counter(pp, 'public_candidates_rejected') or _ledger_counter(ar_psc, 'rejected'),
    }
    
    public_surface_present = bool(pp or ar_psc or public_terminal_stage)
    
    public_stage_counters = {
        stage: 1 for stage in ['discovery_empty', 'fetch_zero', 'parse_zero', 'match_zero', 'build_zero', 'store_zero', 'accepted']
        if public_terminal_stage == stage
    }
    
    return _PublicMetrics(
        public_acceptance_attempted=public_acceptance_attempted,
        public_acceptance_accepted=public_acceptance_accepted,
        public_acceptance_rejected=public_acceptance_rejected,
        public_acceptance_reject_reasons=public_acceptance_reject_reasons,
        top_public_reject_reason=public.top_public_reject_reason,
        public_rejected_url_sample=public_rejected_url_sample,
        public_candidate_ledger_summary=public_candidate_ledger_summary,
        public_surface_present=public_surface_present,
        public_terminal_stage=public_terminal_stage,
        public_stage_counters=public_stage_counters,
    )

def _build_quality_metrics(inp: LiveKpiInput, feed: _FeedMetrics, lane: _LaneMetrics) -> _QualityMetrics:
    """Build quality verdict and budget metrics."""
    rt = inp.runtime_truth or {}
    tt = inp.timing_truth or {}
    as_dict = inp.acquisition_strategy or {}
    
    starvation_suspected, starvation_reason = _detect_starvation(inp, feed, lane, rt, tt, as_dict)
    wallclock_budget_exceeded, wallclock_budget_excess_s, wallclock_tolerance_s = _compute_wallclock_budget(inp)
    deadline_info = _compute_deadline_info(rt, inp.scheduler_exit)
    terminality_verdict, terminality_reasons = _compute_terminality_verdict(inp)
    
    return _QualityMetrics(
        wallclock_budget_exceeded=wallclock_budget_exceeded,
        wallclock_budget_excess_s=wallclock_budget_excess_s,
        wallclock_tolerance_s=wallclock_tolerance_s,
        hard_deadline_checked_count=deadline_info['checked_count'],
        hard_deadline_exceeded=deadline_info['exceeded'],
        hard_deadline_exceeded_at_cycle=deadline_info['exceeded_at_cycle'],
        hard_deadline_remaining_s_at_exit=deadline_info['remaining_s_at_exit'],
        scheduler_deadline_enforced=deadline_info['enforced'],
        scheduler_deadline_checks=deadline_info['checked_count'],
        scheduler_deadline_exit_path=deadline_info['exit_path'],
        terminality_quality_verdict=terminality_verdict,
        terminality_failure_reasons=terminality_reasons,
        nonfeed_starvation_suspected=starvation_suspected,
        nonfeed_starvation_reason=starvation_reason,
    )

def _detect_starvation(inp: LiveKpiInput, feed: _FeedMetrics, lane: _LaneMetrics, rt: dict, tt: dict, as_dict: dict) -> tuple[bool, str | None]:
    """Detect nonfeed starvation conditions."""
    active_runtime_occurred = tt.get('active_runtime_occurred', False)
    prewindup_barrier_checked = as_dict.get('prewindup_barrier_checked', False)
    prewindup_barrier_satisfied = as_dict.get('prewindup_barrier_satisfied', False)
    starvation_suppressed = prewindup_barrier_checked and prewindup_barrier_satisfied
    nonfeed_findings = feed.public_findings + feed.ct_findings
    
    pass_run = inp.run_quality_verdict == RunQualityVerdict.PASS_VALID_CAPABILITY_RUN.value
    starvation_conditions = pass_run and active_runtime_occurred and feed.feed_findings > 0 and nonfeed_findings == 0 and not starvation_suppressed
    
    if starvation_conditions and not lane.nonfeed_attempted_families:
        if not rt.get('public_branch_timed_out') and not rt.get('ct_branch_timed_out'):
            return True, 'early_windup_or_scheduler_order'
    
    if starvation_conditions and lane.nonfeed_attempted_families == 'CANONICAL_FIELD_MISSING':
        return True, 'canonical_field_missing_indeterminate'
    
    if starvation_conditions and lane.nonfeed_attempted_families and isinstance(lane.nonfeed_attempted_families, list):
        pp = inp.public_pipeline
        public_disc = _ledger_counter(pp, 'public_candidates_discovered')
        public_fetch = _ledger_counter(pp, 'public_candidates_fetch_attempted')
        pub_lec = lane.lane_execution_counts.get('PUBLIC')
        public_ts = pub_lec.get('terminal_state') if pub_lec else None
        if not rt.get('public_branch_timed_out') and public_disc == 0 and public_fetch == 0 and public_ts in ('ERROR', 'DISCOVERY_ERROR'):
            return True, 'public_discovery_error_no_candidates'
    
    return False, None

def _compute_wallclock_budget(inp: LiveKpiInput) -> tuple[bool, float | None, float | None]:
    """Compute wallclock budget exceeded metrics."""
    wallclock_gate = inp.run_quality_verdict in (
        RunQualityVerdict.PASS_VALID_CAPABILITY_RUN.value,
        RunQualityVerdict.FAIL_WALLCLOCK_BUDGET_EXCEEDED.value,
    )
    if not (wallclock_gate and inp.planned_duration_s is not None and inp.actual_duration_s is not None):
        return False, None, None
    
    tolerance_s = max(inp.planned_duration_s * 1.1, inp.planned_duration_s + 30.0)
    if inp.actual_duration_s > tolerance_s:
        return True, round(inp.actual_duration_s - tolerance_s, 3), tolerance_s
    return False, None, tolerance_s

def _compute_deadline_info(rt: dict, scheduler_exit: dict | None) -> dict:
    """Compute hard deadline and scheduler deadline metrics."""
    checked_count = rt.get('hard_deadline_checked_count', 0)
    exceeded = rt.get('hard_deadline_exceeded')
    exit_path = (scheduler_exit or {}).get('exit_path', '') if checked_count > 0 else ''
    return {
        'checked_count': checked_count,
        'exceeded': exceeded,
        'exceeded_at_cycle': rt.get('hard_deadline_exceeded_at_cycle'),
        'remaining_s_at_exit': rt.get('hard_deadline_remaining_s_at_exit'),
        'enforced': checked_count > 0 and exceeded is not None,
        'exit_path': exit_path,
    }

_TERMINALITY_FAILURE_VERDICTS = (
    RunQualityVerdict.FAIL_TERMINALITY_NOT_CHECKED.value,
    RunQualityVerdict.FAIL_TERMINALITY_UNSATISFIED.value,
    RunQualityVerdict.FAIL_MISSING_SOURCE_OUTCOMES.value,
    RunQualityVerdict.FAIL_SCHEDULER_EXIT_MISSING.value,
)

def _compute_terminality_verdict(inp: LiveKpiInput) -> tuple[str | None, list[str]]:
    """Compute terminality quality verdict and failure reasons."""
    if not _is_active_domain_query(inp.runtime_truth, inp.profile_verdict):
        return None, []
    
    base_verdict = inp.run_quality_verdict or ''
    if base_verdict not in _TERMINALITY_FAILURE_VERDICTS:
        return None, []
    
    reasons = []
    if not (inp.acquisition_report and isinstance(inp.acquisition_report, dict) and inp.acquisition_report.get('schema_version')):
        reasons.append('acquisition_report.schema_version missing')
    if inp.acquisition_terminality_checked is not True:
        reasons.append(f'acquisition_terminality_checked={inp.acquisition_terminality_checked!r}, expected True')
    if inp.acquisition_terminality_satisfied is not True:
        reasons.append(f'acquisition_terminality_satisfied={inp.acquisition_terminality_satisfied!r}, expected True')
    if not _has_terminal_source_outcomes(inp.acquisition_strategy):
        reasons.append('source_family_outcomes missing or empty')
    if not _has_scheduler_exit_path(inp.scheduler_exit):
        reasons.append('scheduler_exit_path missing or empty')
    
    return base_verdict, reasons

def _build_guard_metrics(inp: LiveKpiInput) -> _GuardMetrics:
    """Build windup and return guard observation metrics."""
    tt = inp.timing_truth or {}
    as_dict = inp.acquisition_strategy or {}
    rt = inp.runtime_truth or {}
    branch_mix = rt.get('branch_mix', {})
    wg = inp.windup_guard_observation or {}
    rg = inp.return_guard_observation or {}
    
    nonfeed_eligible_families = [
        family for family, has_it in [('public', 'public_findings' in branch_mix or 'public_branch_timed_out' in rt), ('ct', 'ct_findings' in branch_mix or 'ct_branch_timed_out' in rt)]
        if has_it
    ]
    
    return _GuardMetrics(
        windup_lead_requested_s=tt.get('windup_lead_requested_s'),
        windup_lead_observed_s=tt.get('windup_lead_observed_s'),
        active_window_budget_s=tt.get('active_window_budget_s'),
        nonfeed_eligible_families=nonfeed_eligible_families,
        nonfeed_skipped_reasons={},
        prewindup_barrier_checked=as_dict.get('prewindup_barrier_checked', False),
        prewindup_barrier_satisfied=as_dict.get('prewindup_barrier_satisfied', False),
        prewindup_required_lanes=as_dict.get('prewindup_required_lanes', []),
        prewindup_attempted_lanes=as_dict.get('prewindup_attempted_lanes', []),
        prewindup_skipped_lanes=as_dict.get('prewindup_skipped_lanes', {}),
        windup_delayed_for_nonfeed=as_dict.get('windup_delayed_for_nonfeed', False),
        nonfeed_scheduler_gap_resolved=as_dict.get('nonfeed_scheduler_gap_resolved'),
        windup_guard_call_count=wg.get('call_count', 0),
        windup_guard_callback_supplied_count=wg.get('callback_supplied_count', 0),
        windup_guard_callback_executed_count=wg.get('callback_executed_count', 0),
        windup_guard_last_reason=wg.get('last_reason', ''),
        windup_guard_last_phase=wg.get('last_phase', ''),
        windup_guard_last_allowed=wg.get('last_allowed'),
        return_guard_checked=rg.get('checked', False),
        return_guard_required_lanes=rg.get('required_lanes', []),
        return_guard_satisfied=rg.get('satisfied', False),
        return_guard_delayed_for_nonfeed=rg.get('delayed_for_nonfeed', False),
        return_guard_block_reason=rg.get('block_reason', ''),
        return_guard_attempted_lanes=rg.get('attempted_lanes', []),
        return_guard_skipped_lanes=rg.get('skipped_lanes', {}),
        return_guard_errors=rg.get('errors', []),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _derive_live_kpi(status: MeasurementStatus, is_memory_gate_abort: bool, runtime_truth: dict | None, actual_duration_s: float | None, primary_signal_source: str | None, run_quality_verdict: str | None, hardware_constrained: bool | None, public_pipeline: dict | None=None, timing_truth: dict | None=None, acquisition_strategy: dict | None=None, windup_guard_observation: dict | None=None, return_guard_observation: dict | None=None, scheduler_exit: dict | None=None, acquisition_report: dict | None=None, profile_verdict: str | None=None, acquisition_terminality_checked: bool | None=None, acquisition_terminality_satisfied: bool | None=None, acquisition_terminality_missing_lanes: tuple[str, ...] | None=None, acquisition_terminality_report: dict | None=None, explicit_source_family_outcomes: list[dict] | None=None, acquisition_prelude_checked: bool | None=None, acquisition_prelude_ran: bool | None=None, acquisition_prelude_required_lanes: tuple[str, ...] | None=None, acquisition_prelude_terminal_lanes: tuple[str, ...] | None=None, acquisition_prelude_missing_lanes: tuple[str, ...] | None=None, acquisition_prelude_skipped_lanes: dict | None=None, acquisition_prelude_errors: dict | None=None, acquisition_prelude_duration_s: float | None=None, acquisition_prelude_reason: str | None=None, planned_duration_s: float | None=None, claims_runtime_status: dict | None=None) -> dict:
    """
    Compatibility wrapper: accepts 31 explicit parameters, constructs LiveKpiInput,
    and delegates to _derive_live_kpi_from_input.

    Preserves the old 31-argument calling convention for backward compatibility
    with any direct callers outside this module.
    """
    inp = LiveKpiInput(status=status, is_memory_gate_abort=is_memory_gate_abort, runtime_truth=runtime_truth, actual_duration_s=actual_duration_s, primary_signal_source=primary_signal_source, run_quality_verdict=run_quality_verdict, hardware_constrained=hardware_constrained, public_pipeline=public_pipeline, timing_truth=timing_truth, acquisition_strategy=acquisition_strategy, windup_guard_observation=windup_guard_observation, return_guard_observation=return_guard_observation, scheduler_exit=scheduler_exit, acquisition_report=acquisition_report, profile_verdict=profile_verdict, acquisition_terminality_checked=acquisition_terminality_checked, acquisition_terminality_satisfied=acquisition_terminality_satisfied, acquisition_terminality_missing_lanes=acquisition_terminality_missing_lanes, acquisition_terminality_report=acquisition_terminality_report, explicit_source_family_outcomes=explicit_source_family_outcomes, acquisition_prelude_checked=acquisition_prelude_checked, acquisition_prelude_ran=acquisition_prelude_ran, acquisition_prelude_required_lanes=acquisition_prelude_required_lanes, acquisition_prelude_terminal_lanes=acquisition_prelude_terminal_lanes, acquisition_prelude_missing_lanes=acquisition_prelude_missing_lanes, acquisition_prelude_skipped_lanes=acquisition_prelude_skipped_lanes, acquisition_prelude_errors=acquisition_prelude_errors, acquisition_prelude_duration_s=acquisition_prelude_duration_s, acquisition_prelude_reason=acquisition_prelude_reason, planned_duration_s=planned_duration_s, claims_runtime_status=claims_runtime_status)
    return _derive_live_kpi_from_input(inp)

def _derive_live_kpi_from_input(inp: LiveKpiInput) -> dict:
    """
    Compute live KPI dict from parsed sprint report.

    Returns a dict with 150+ keys covering findings, lane execution, quality verdicts,
    public acceptance, CT/discovery metrics, guard observations, and scheduler exit.

    Architecture:
      1. Build intermediate result classes for each domain
      2. Derive next_action with computed inputs
      3. Assemble final result dict from all intermediate results

    F211B: Lane execution truth is split into three distinct views:
    - branch_accepted_counts: per-branch accepted findings from branch_mix
    - lane_execution_counts: per-family lane execution with terminal_state
    - source_family_counts: derived from lane_execution_counts (accepted>0 only)
    - nonfeed_attempted_families: derived from lane_execution_counts (FEED excluded)
    """
    rt = inp.runtime_truth or {}
    lane_verdict = rt.get('lane_verdict', {}) or {}
    as_dict = inp.acquisition_strategy or {}
    
    sfo_list = inp.explicit_source_family_outcomes if inp.explicit_source_family_outcomes is not None else as_dict.get('source_family_outcomes', [])
    sfo_has_canonical = isinstance(sfo_list, list) and sfo_list
    
    feed = _build_feed_metrics(inp)
    lane = _build_lane_metrics(inp, feed)
    public = _build_public_metrics(inp)
    quality = _build_quality_metrics(inp, feed, lane)
    guard = _build_guard_metrics(inp)
    
    next_action, next_action_detail = _derive_next_action(
        status=inp.status,
        is_memory_gate_abort=inp.is_memory_gate_abort,
        nonfeed_accepted_findings=lane.nonfeed_accepted_findings,
        public_fetch_attempted=lane.public_fetch_attempted,
        public_findings=feed.public_findings,
        feed_findings=feed.feed_findings,
        total_findings=feed.total_findings,
        ct_findings=feed.ct_findings,
        runtime_truth=rt,
        feed_dominance_score=feed.feed_dominance_score,
        top_public_reject_reason=top_public_reject_reason,
        nonfeed_starvation_suspected=quality.nonfeed_starvation_suspected,
        prewindup_barrier_checked=guard.prewindup_barrier_checked,
        prewindup_barrier_satisfied=guard.prewindup_barrier_satisfied,
        prewindup_required_lanes=guard.prewindup_required_lanes,
        prewindup_attempted_lanes=guard.prewindup_attempted_lanes,
        acquisition_strategy=as_dict,
        return_guard_observation={'checked': guard.return_guard_checked, 'required_lanes': guard.return_guard_required_lanes, 'satisfied': guard.return_guard_satisfied, 'delayed_for_nonfeed': guard.return_guard_delayed_for_nonfeed, 'block_reason': guard.return_guard_block_reason, 'attempted_lanes': guard.return_guard_attempted_lanes, 'skipped_lanes': guard.return_guard_skipped_lanes, 'errors': guard.return_guard_errors},
        scheduler_exit=inp.scheduler_exit,
        acquisition_terminality_checked=inp.acquisition_terminality_checked,
        acquisition_terminality_satisfied=inp.acquisition_terminality_satisfied,
        acquisition_terminality_missing_lanes=list(inp.acquisition_terminality_missing_lanes) if inp.acquisition_terminality_missing_lanes is not None else None,
        run_quality_verdict=inp.run_quality_verdict,
        acquisition_prelude_checked=inp.acquisition_prelude_checked,
        acquisition_prelude_ran=inp.acquisition_prelude_ran,
        acquisition_prelude_required_lanes=list(inp.acquisition_prelude_required_lanes) if inp.acquisition_prelude_required_lanes is not None else None,
        acquisition_prelude_terminal_lanes=list(inp.acquisition_prelude_terminal_lanes) if inp.acquisition_prelude_terminal_lanes is not None else None,
        acquisition_prelude_missing_lanes=list(inp.acquisition_prelude_missing_lanes) if inp.acquisition_prelude_missing_lanes is not None else None,
        acquisition_prelude_skipped_lanes=inp.acquisition_prelude_skipped_lanes,
        acquisition_prelude_errors=inp.acquisition_prelude_errors,
        acquisition_prelude_duration_s=inp.acquisition_prelude_duration_s,
        acquisition_prelude_reason=inp.acquisition_prelude_reason,
        windup_guard_observation={'call_count': guard.windup_guard_call_count, 'callback_supplied_count': guard.windup_guard_callback_supplied_count, 'callback_executed_count': guard.windup_guard_callback_executed_count, 'last_reason': guard.windup_guard_last_reason, 'last_phase': guard.windup_guard_last_phase, 'last_allowed': guard.windup_guard_last_allowed},
        scheduler_deadline_enforced=quality.scheduler_deadline_enforced,
        scheduler_deadline_checks=quality.scheduler_deadline_checks,
    )
    
    acquisition_report_schema_version = None
    if inp.acquisition_report and isinstance(inp.acquisition_report, dict):
        acquisition_report_schema_version = inp.acquisition_report.get('schema_version')
    
    ar_psc = _as_mapping(inp.acquisition_report).get('public_stage_counters') if inp.acquisition_report else None
    
    claims_status = inp.claims_runtime_status or {}
    
    lane_verdict_safe = lane_verdict if isinstance(lane_verdict, dict) else {}
    lane_verdict_get = lane_verdict_safe.get
    
    # Cache discovery provider status to avoid repeated calls
    _discovery_psd = _derive_discovery_provider_status_debug(inp.acquisition_report)
    
    return {
        'total_findings': feed.total_findings,
        'accepted_findings': feed.accepted_findings,
        'cycles_completed': feed.cycles_completed,
        'findings_per_min': feed.findings_per_min,
        'primary_signal_source': inp.primary_signal_source,
        'branch_accepted_counts': feed.branch_accepted_counts,
        'lane_execution_counts': lane.lane_execution_counts,
        'source_family_counts': lane.source_family_counts,
        'source_family_outcomes_display': lane.source_family_outcomes_display,
        'nonfeed_attempted_families': lane.nonfeed_attempted_families,
        'nonfeed_accepted_findings': lane.nonfeed_accepted_findings,
        'public_fetch_attempted': lane.public_fetch_attempted,
        'public_acceptance_attempted': public.public_acceptance_attempted,
        'public_acceptance_accepted': public.public_acceptance_accepted,
        'public_acceptance_rejected': public.public_acceptance_rejected,
        'public_acceptance_reject_reasons': public.public_acceptance_reject_reasons,
        'top_public_reject_reason': public.top_public_reject_reason,
        'public_rejected_url_sample': public.public_rejected_url_sample,
        'public_candidate_ledger_summary': public.public_candidate_ledger_summary,
        'public_surface_present': public.public_surface_present,
        'public_terminal_stage': public.public_terminal_stage,
        'public_stage_counters': public.public_stage_counters,
        'feed_dominance_score': feed.feed_dominance_score,
        'feed_balance_recommendation': feed.feed_balance_recommendation,
        'estimated_per_source_soft_cap': feed.estimated_per_source_soft_cap,
        'dominant_feed_source': feed.dominant_feed_source,
        'dominant_feed_share_pct': feed.dominant_feed_share_pct,
        'run_quality_verdict': inp.run_quality_verdict,
        'hardware_constrained': inp.hardware_constrained,
        'wallclock_budget_exceeded': quality.wallclock_budget_exceeded,
        'wallclock_budget_excess_s': quality.wallclock_budget_excess_s,
        'wallclock_tolerance_s': quality.wallclock_tolerance_s,
        'next_action': next_action,
        'next_action_detail': next_action_detail,
        'runtime_budget_action_family': 'fix_runtime_budget_enforcement' if quality.wallclock_budget_exceeded else None,
        'deadline_action_detail': next_action if quality.wallclock_budget_exceeded else None,
        'nonfeed_starvation_suspected': quality.nonfeed_starvation_suspected,
        'nonfeed_starvation_reason': quality.nonfeed_starvation_reason,
        'windup_lead_requested_s': guard.windup_lead_requested_s,
        'windup_lead_observed_s': guard.windup_lead_observed_s,
        'active_window_budget_s': guard.active_window_budget_s,
        'nonfeed_eligible_families': guard.nonfeed_eligible_families,
        'nonfeed_skipped_reasons': guard.nonfeed_skipped_reasons,
        'prewindup_barrier_checked': guard.prewindup_barrier_checked,
        'prewindup_barrier_satisfied': guard.prewindup_barrier_satisfied,
        'prewindup_required_lanes': guard.prewindup_required_lanes,
        'prewindup_attempted_lanes': guard.prewindup_attempted_lanes,
        'prewindup_skipped_lanes': guard.prewindup_skipped_lanes,
        'windup_delayed_for_nonfeed': guard.windup_delayed_for_nonfeed,
        'nonfeed_scheduler_gap_resolved': guard.nonfeed_scheduler_gap_resolved,
        'source_family_outcomes': inp.explicit_source_family_outcomes if inp.explicit_source_family_outcomes is not None else as_dict.get('source_family_outcomes'),
        'windup_guard_call_count': guard.windup_guard_call_count,
        'windup_guard_callback_supplied_count': guard.windup_guard_callback_supplied_count,
        'windup_guard_callback_executed_count': guard.windup_guard_callback_executed_count,
        'windup_guard_last_reason': guard.windup_guard_last_reason,
        'windup_guard_last_phase': guard.windup_guard_last_phase,
        'windup_guard_last_allowed': guard.windup_guard_last_allowed,
        'return_guard_checked': guard.return_guard_checked,
        'return_guard_required_lanes': guard.return_guard_required_lanes,
        'return_guard_satisfied': guard.return_guard_satisfied,
        'return_guard_delayed_for_nonfeed': guard.return_guard_delayed_for_nonfeed,
        'return_guard_block_reason': guard.return_guard_block_reason,
        'return_guard_attempted_lanes': guard.return_guard_attempted_lanes,
        'return_guard_skipped_lanes': guard.return_guard_skipped_lanes,
        'return_guard_errors': guard.return_guard_errors,
        'scheduler_exit_path': _safe_exit(inp.scheduler_exit, 'exit_path'),
        'scheduler_exit_reason': _safe_exit(inp.scheduler_exit, 'exit_reason'),
        'scheduler_exit_phase': _safe_exit(inp.scheduler_exit, 'exit_phase'),
        'scheduler_exit_cycle': _safe_exit(inp.scheduler_exit, 'exit_cycle'),
        'scheduler_exit_elapsed_s': _safe_exit(inp.scheduler_exit, 'elapsed_s'),
        'scheduler_exit_guard_checked': _safe_exit(inp.scheduler_exit, 'guard_checked'),
        'scheduler_exit_guard_required': _safe_exit(inp.scheduler_exit, 'guard_required'),
        'scheduler_exit_guard_satisfied': _safe_exit(inp.scheduler_exit, 'guard_satisfied'),
        'scheduler_deadline_enforced': quality.scheduler_deadline_enforced,
        'scheduler_deadline_checks': quality.scheduler_deadline_checks,
        'scheduler_deadline_exit_path': quality.scheduler_deadline_exit_path,
        'hard_deadline_checked_count': quality.hard_deadline_checked_count,
        'hard_deadline_exceeded': quality.hard_deadline_exceeded,
        'hard_deadline_exceeded_at_cycle': quality.hard_deadline_exceeded_at_cycle,
        'hard_deadline_remaining_s_at_exit': quality.hard_deadline_remaining_s_at_exit,
        'acquisition_terminality_checked': bool(inp.acquisition_terminality_checked) if inp.acquisition_terminality_checked is not None else None,
        'acquisition_terminality_satisfied': bool(inp.acquisition_terminality_satisfied) if inp.acquisition_terminality_satisfied is not None else None,
        'acquisition_terminality_missing_lanes': list(inp.acquisition_terminality_missing_lanes) if inp.acquisition_terminality_missing_lanes is not None else None,
        'acquisition_terminality_report': inp.acquisition_terminality_report,
        'terminality_quality_verdict': quality.terminality_quality_verdict,
        'terminality_failure_reasons': quality.terminality_failure_reasons,
        'acquisition_report_schema_version': acquisition_report_schema_version,
        'acquisition_prelude_checked': bool(inp.acquisition_prelude_checked) if inp.acquisition_prelude_checked is not None else None,
        'acquisition_prelude_ran': bool(inp.acquisition_prelude_ran) if inp.acquisition_prelude_ran is not None else None,
        'acquisition_prelude_required_lanes': list(inp.acquisition_prelude_required_lanes) if inp.acquisition_prelude_required_lanes is not None else None,
        'acquisition_prelude_terminal_lanes': list(inp.acquisition_prelude_terminal_lanes) if inp.acquisition_prelude_terminal_lanes is not None else None,
        'acquisition_prelude_missing_lanes': list(inp.acquisition_prelude_missing_lanes) if inp.acquisition_prelude_missing_lanes is not None else None,
        'acquisition_prelude_skipped_lanes': inp.acquisition_prelude_skipped_lanes,
        'acquisition_prelude_errors': inp.acquisition_prelude_errors,
        'acquisition_prelude_duration_s': inp.acquisition_prelude_duration_s,
        'acquisition_prelude_reason': inp.acquisition_prelude_reason,
        'ct_loss_stage': lane_verdict_get('ct_loss_stage', 'no_loss'),
        'ct_bridge_invoked': lane_verdict_get('ct_bridge_invoked', False),
        'ct_raw_sample_count': lane_verdict_get('ct_raw_sample_count', 0),
        'ct_candidates_built': lane_verdict_get('ct_candidates_built', 0),
        'ct_bridge_rejections_count': lane_verdict_get('ct_bridge_rejections_count', 0),
        'ct_candidates_accumulated': lane_verdict_get('ct_candidates_accumulated', 0),
        'ct_candidates_stored': lane_verdict_get('ct_candidates_stored', 0),
        'ct_storage_rejected': lane_verdict_get('ct_storage_rejected', 0),
        'ct_expansion_clues_count': lane_verdict_get('ct_expansion_clues_count', 0),
        'ct_valid_public_domains': lane_verdict_get('ct_valid_public_domains', 0),
        'ct_wildcard_domains': lane_verdict_get('ct_wildcard_domains', 0),
        'ct_private_reserved_domains': lane_verdict_get('ct_private_reserved_domains', 0),
        'ct_duplicate_candidates': lane_verdict_get('ct_duplicate_candidates', 0),
        'wayback_advisory_clues_count': lane_verdict_get('wayback_advisory_clues_count', 0),
        'wayback_changed_url_count': lane_verdict_get('wayback_changed_url_count', 0),
        'wayback_added_url_count': lane_verdict_get('wayback_added_url_count', 0),
        'wayback_digest_changed_count': lane_verdict_get('wayback_digest_changed_count', 0),
        'wayback_unchanged_rejected': lane_verdict_get('wayback_unchanged_rejected', 0),
        'passive_dns_advisory_clues_count': lane_verdict_get('passive_dns_advisory_clues_count', 0),
        'passive_dns_private_ip_rejected': lane_verdict_get('passive_dns_private_ip_rejected', 0),
        'passive_dns_empty_ip_rejected': lane_verdict_get('passive_dns_empty_ip_rejected', 0),
        'public_candidates_seen': _sum_alias_fields(inp.public_pipeline, ['public_candidates_discovered', 'public_candidates_built', 'public_candidates_stored']) if inp.public_pipeline else 0,
        'ct_clues_seen': _sum_alias_fields(lane_verdict_safe, ['ct_expansion_clues_count', 'ct_valid_public_domains']),
        'wayback_clues_seen': _sum_alias_fields(lane_verdict_safe, ['wayback_advisory_clues_count']),
        'passivedns_clues_seen': _sum_alias_fields(lane_verdict_safe, ['passive_dns_advisory_clues_count']),
        'claims_extracted_count': claims_status.get('claims_extracted_count', 0),
        'claims_polarity_mix': {'positive': claims_status.get('claims_positive_count', 0), 'negative': claims_status.get('claims_negative_count', 0), 'neutral': claims_status.get('claims_neutral_count', 0)},
        'claims_packets_with_claims': claims_status.get('claims_extraction_packets_with_claims', 0),
        'discovery_provider_status_debug': _discovery_psd,
        'discovery_selected_providers': [e['provider'] for e in _discovery_psd if e.get('selected')],
        'discovery_skipped_providers': [e['provider'] for e in _discovery_psd if not e.get('selected')],
        'discovery_stub_providers': [e['provider'] for e in _discovery_psd if e.get('state') == 'advisory_stub'],
        'discovery_not_wired_providers': [e['provider'] for e in _discovery_psd if e.get('state') == 'not_wired'],
        'missing_canonical_fields': ['source_family_outcomes'] if not sfo_has_canonical else [],
        'mode': 'live',
        'runtime_truth': rt,
        'branch_mix': rt.get('branch_mix', {}),
        'live_kpi_marker': True,
    }

def _sum_alias_fields(src: dict | None, aliases: list[str]) -> int:
    """Sum the first non-zero alias field value from a source dict.

    F231F: Used to normalize F231A/B/C canonical field names into the
    evidence depth KPI alias fields (public_candidates_seen, ct_clues_seen,
    wayback_clues_seen, passivedns_clues_seen) that research_quality_score reads.
    """
    if not isinstance(src, dict):
        return 0
    for alias in aliases:
        val = src.get(alias)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    return 0

def _derive_discovery_provider_status_debug(acquisition_report: dict | None) -> list[dict]:
    """
    Extract and serialize provider_status_debug from acquisition_report.

    F225C: Surfaces discovery provider plan truth in live KPI.
    Returns JSON-safe list with provider, state (string), selected, reason.
    """
    if not acquisition_report or not isinstance(acquisition_report, dict):
        return []
    psd = acquisition_report.get('provider_status_debug')
    if not isinstance(psd, list):
        return []
    result = []
    for entry in psd:
        if isinstance(entry, dict):
            state = entry.get('state')
            if hasattr(state, 'value'):
                state = state.value
            result.append({'provider': entry.get('provider', ''), 'state': str(state) if state is not None else '', 'selected': bool(entry.get('selected', False)), 'reason': entry.get('reason', '')})
    return result

def _derive_discovery_selected_providers(acquisition_report: dict | None) -> list[str]:
    """Extract selected providers (selected=True) from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e['provider'] for e in psd if e.get('selected')]

def _derive_discovery_skipped_providers(acquisition_report: dict | None) -> list[str]:
    """Extract skipped providers (selected=False) from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e['provider'] for e in psd if not e.get('selected')]

def _derive_discovery_stub_providers(acquisition_report: dict | None) -> list[str]:
    """Extract ADVISORY_STUB providers from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e['provider'] for e in psd if e.get('state') == 'advisory_stub']

def _derive_discovery_not_wired_providers(acquisition_report: dict | None) -> list[str]:
    """Extract NOT_WIRED providers from provider_status_debug."""
    psd = _derive_discovery_provider_status_debug(acquisition_report)
    return [e['provider'] for e in psd if e.get('state') == 'not_wired']