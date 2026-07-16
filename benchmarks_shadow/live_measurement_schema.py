"""
F227A LIVE MEASUREMENT SCHEMA

Schema-only extractions from benchmarks/live_sprint_measurement.py:
  - RunMode (enum)
  - MeasurementStatus (enum)
  - RunQualityVerdict (enum)
  - LiveMeasurementResult (dataclass + to_dict + to_json)

No runtime import side effects — only schema definitions.
"""
import json
from dataclasses import dataclass, field
import msgspec
from enum import Enum
from hledac.universal.utils.serialization import _safe_dataclass_to_dict

class RunMode(Enum):
    DRY_RUN = 'dry_run'
    LIVE = 'live'
    PREFLIGHT = 'preflight'

class MeasurementStatus(Enum):
    PLANNED = 'planned'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    ABORTED = 'aborted'

class RunQualityVerdict(Enum):
    """Run quality verdict — tells us whether a completed run is hardware-tainted."""
    PASS_VALID_CAPABILITY_RUN = 'PASS_VALID_CAPABILITY_RUN'
    PASS_HARDWARE_CONSTRAINED = 'PASS_HARDWARE_CONSTRAINED'
    ENTRY_SMOKE_ONLY = 'ENTRY_SMOKE_ONLY'
    FAIL_RUNTIME_ERROR = 'FAIL_RUNTIME_ERROR'
    FAIL_MEASUREMENT_ERROR = 'FAIL_MEASUREMENT_ERROR'
    ABORTED_MEMORY_GATE = 'ABORTED_MEMORY_GATE'
    FAIL_TERMINALITY_NOT_CHECKED = 'FAIL_TERMINALITY_NOT_CHECKED'
    FAIL_TERMINALITY_UNSATISFIED = 'FAIL_TERMINALITY_UNSATISFIED'
    FAIL_MISSING_SOURCE_OUTCOMES = 'FAIL_MISSING_SOURCE_OUTCOMES'
    FAIL_SCHEDULER_EXIT_MISSING = 'FAIL_SCHEDULER_EXIT_MISSING'
    FAIL_WALLCLOCK_BUDGET_EXCEEDED = 'FAIL_WALLCLOCK_BUDGET_EXCEEDED'

class LiveMeasurementResult(msgspec.Struct):
    measurement_id: str
    sprint_id: str | None
    mode: RunMode
    status: MeasurementStatus
    start_time_iso: str | None
    end_time_iso: str | None
    planned_duration_s: float | None
    actual_duration_s: float | None
    query: str
    profile: str
    duration_s: int = 0
    aggressive_mode: bool = False
    deep_probe: bool = False
    uma_pre_used_gib: float | None = None
    uma_pre_swap_gib: float | None = None
    uma_pre_state: str | None = None
    uma_post_used_gib: float | None = None
    uma_post_swap_gib: float | None = None
    uma_post_state: str | None = None
    findings_count: int | None = None
    cycles_completed: int | None = None
    cycles_started: int | None = None
    accepted_findings: int | None = None
    runtime_truth: dict | None = None
    timing_truth: dict | None = None
    checkpoint_zero_category: str | None = None
    early_exit_class: str | None = None
    primary_signal_source: str | None = None
    export_paths: list[str] = field(default_factory=list)
    report_json_path: str | None = None
    error: str | None = None
    stabilization_seal_present: bool = False
    hermetic_regression_manifest_present: bool = False
    transport_authority_status_present: bool = False
    mlx_wired_limit_seal_present: bool = False
    active_runtime_expected: bool = False
    expected_windup_lead_s: int | None = None
    expected_active_window_s: int | None = None
    profile_verdict: str | None = None
    run_quality_verdict: str | None = None
    hardware_constrained: bool | None = None
    memory_state_pre: str | None = None
    memory_state_post: str | None = None
    swap_warning: bool | None = None
    recommended_next_profile: str | None = None
    recommended_operator_action: str | None = None
    swap_gate_triggered: bool | None = None
    swap_policy_tier: str | None = None
    swap_gate_reason: str | None = None
    comparable_result: bool | None = None
    taint_reason: str | None = None
    live_kpi: dict | None = None
    public_pipeline: dict | None = None
    acquisition_strategy: dict | None = None
    acquisition_profile: str | None = None
    nonfeed_priority_enabled: bool = False
    nonfeed_profile_expected_lanes: tuple[str, ...] = ()
    windup_guard_observation: dict | None = None
    return_guard_observation: dict | None = None
    scheduler_exit: dict | None = None
    acquisition_terminality_checked: bool | None = None
    acquisition_terminality_satisfied: bool | None = None
    acquisition_terminality_missing_lanes: tuple[str, ...] | None = None
    acquisition_terminality_report: dict | None = None
    runtime_authority_path: str | None = None
    runtime_authority_module: str | None = None
    runtime_authority_function: str | None = None
    runtime_authority_is_canonical: bool | None = None
    runtime_authority_evidence: dict | None = None
    core_run_sprint_module_file: str | None = None
    core_run_sprint_function_qualname: str | None = None
    sprint_scheduler_module_file: str | None = None
    live_sprint_measurement_module_file: str | None = None
    python_executable: str | None = None
    runtime_cwd: str | None = None
    sys_path_head: str | None = None
    core_main_mtime: float | None = None
    sprint_scheduler_mtime: float | None = None
    acquisition_prelude_checked: bool | None = None
    acquisition_prelude_ran: bool | None = None
    acquisition_prelude_required_lanes: tuple[str, ...] | None = None
    acquisition_prelude_terminal_lanes: tuple[str, ...] | None = None
    acquisition_prelude_missing_lanes: tuple[str, ...] | None = None
    acquisition_prelude_skipped_lanes: dict | None = None
    acquisition_prelude_errors: dict | None = None
    acquisition_prelude_duration_s: float | None = None
    acquisition_prelude_reason: str | None = None
    nonfeed_mission_active: bool | None = None
    nonfeed_required_families: tuple[str, ...] | None = None
    nonfeed_optional_families: tuple[str, ...] | None = None
    nonfeed_family_status: dict | None = None
    nonfeed_all_required_terminal: bool | None = None
    nonfeed_any_accepted: bool | None = None
    nonfeed_provider_failures: tuple[str, ...] | None = None
    nonfeed_memory_skips: tuple[str, ...] | None = None
    nonfeed_mission_exit_reason: str | None = None
    acquisition_report: dict | None = None
    claims_runtime_status: dict | None = None
    resolved_output_json: str | None = None
    resolved_output_md: str | None = None

    def to_dict(self) -> dict:
        d = _safe_dataclass_to_dict(self)
        d['mode'] = self.mode.value
        d['status'] = self.status.value
        d['live_run_status'] = self.status.value
        if self.runtime_truth and isinstance(self.runtime_truth, dict):
            _bm = self.runtime_truth.get('branch_mix', {})
            if isinstance(_bm, dict):
                _d_bm = dict(_bm)
                if 'feed' not in _d_bm and 'feed_findings' in _d_bm:
                    _d_bm['feed'] = _d_bm['feed_findings']
                d['branch_mix'] = _d_bm
        _lk = self.live_kpi or {}
        _sfo = _lk.get('source_family_outcomes', [])
        if isinstance(_sfo, list) and _sfo:
            _pub = next((x for x in _sfo if isinstance(x, dict) and x.get('family') == 'public'), None)
            _ct = next((x for x in _sfo if isinstance(x, dict) and x.get('family') == 'ct'), None)
            if _pub is not None:
                d['public_terminal_state'] = 'COMPLETED' if _pub.get('attempted') and (not _pub.get('skipped')) else 'NEVER_ATTEMPTED'
            if _ct is not None:
                d['ct_terminal_state'] = 'COMPLETED' if _ct.get('attempted') and (not _ct.get('skipped')) else 'NEVER_ATTEMPTED'
        elif isinstance(_sfo, dict) and _sfo:
            d['public_terminal_state'] = 'COMPLETED' if _sfo.get('public', {}).get('attempted') else 'NEVER_ATTEMPTED'
            d['ct_terminal_state'] = 'COMPLETED' if _sfo.get('ct', {}).get('attempted') else 'NEVER_ATTEMPTED'
        _rq = _lk.get('research_quality', {})
        if isinstance(_rq, dict):
            d['research_quality_grade'] = _rq.get('grade')
            d['research_quality_score'] = _rq.get('total_quality_score')
            d['research_quality_comparable'] = _rq.get('research_quality_comparable')
        _mcf = _lk.get('missing_canonical_fields', [])
        _sfo_canonical = _lk.get('source_family_outcomes')
        _sfo_present = _sfo_canonical is not None
        d['canonical_report_snapshot'] = {'source_family_outcomes': _sfo_canonical if _sfo_present else None, 'source_family_outcomes_present': _sfo_present, 'missing_canonical_fields': _mcf if isinstance(_mcf, list) else [], 'sprint_id': self.sprint_id, 'checkpoint_zero_category': self.checkpoint_zero_category, 'primary_signal_source': self.primary_signal_source, 'planned_duration_s': self.planned_duration_s, 'actual_duration_s': self.actual_duration_s, 'findings_count': self.findings_count, 'accepted_findings': self.accepted_findings, 'cycles_completed': self.cycles_completed, 'cycles_started': self.cycles_started}
        d['measurement_metadata'] = {'measurement_id': self.measurement_id, 'mode': self.mode.value, 'status': self.status.value, 'profile': self.profile, 'query': self.query, 'aggressive_mode': self.aggressive_mode, 'deep_probe': self.deep_probe, 'runtime_authority_path': self.runtime_authority_path, 'runtime_authority_is_canonical': self.runtime_authority_is_canonical, 'start_time_iso': self.start_time_iso, 'end_time_iso': self.end_time_iso, 'planned_duration_s': self.planned_duration_s, 'actual_duration_s': self.actual_duration_s, 'uma_pre_used_gib': self.uma_pre_used_gib, 'uma_pre_swap_gib': self.uma_pre_swap_gib, 'uma_pre_state': self.uma_pre_state, 'uma_post_used_gib': self.uma_post_used_gib, 'uma_post_swap_gib': self.uma_post_swap_gib, 'uma_post_state': self.uma_post_state, 'active_runtime_expected': self.active_runtime_expected, 'expected_windup_lead_s': self.expected_windup_lead_s, 'expected_active_window_s': self.expected_active_window_s, 'profile_verdict': self.profile_verdict, 'run_quality_verdict': self.run_quality_verdict, 'hardware_constrained': self.hardware_constrained, 'swap_warning': self.swap_warning, 'swap_gate_triggered': self.swap_gate_triggered, 'swap_policy_tier': self.swap_policy_tier, 'swap_gate_reason': self.swap_gate_reason, 'resolved_output_json': self.resolved_output_json, 'resolved_output_md': self.resolved_output_md, 'report_json_path': self.report_json_path, 'stabilization_seal_present': self.stabilization_seal_present, 'hermetic_regression_manifest_present': self.hermetic_regression_manifest_present, 'transport_authority_status_present': self.transport_authority_status_present, 'mlx_wired_limit_seal_present': self.mlx_wired_limit_seal_present}
        d['derived_checks'] = {'note': 'These fields are DERIVED by the benchmark, not copied from canonical report.', 'live_kpi_lane_execution_counts': _lk.get('lane_execution_counts', {}), 'live_kpi_source_family_counts': _lk.get('source_family_counts', {}), 'live_kpi_nonfeed_attempted_families': _lk.get('nonfeed_attempted_families', []), 'live_kpi_public_fetch_attempted': _lk.get('public_fetch_attempted', False), 'live_kpi_nonfeed_accepted_findings': _lk.get('nonfeed_accepted_findings', 0), 'live_kpi_findings_per_min': _lk.get('findings_per_min', 0.0)}
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)