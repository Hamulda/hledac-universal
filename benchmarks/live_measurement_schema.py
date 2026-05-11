"""
F227A LIVE MEASUREMENT SCHEMA

Schema-only extractions from benchmarks/live_sprint_measurement.py:
  - RunMode (enum)
  - MeasurementStatus (enum)
  - RunQualityVerdict (enum)
  - LiveMeasurementResult (dataclass + to_dict + to_json)

No runtime import side effects — only schema definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json

from hledac.universal.utils.serialization import _safe_dataclass_to_dict


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
    # F208I: active300/active600 domain query terminality downgrade verdicts
    FAIL_TERMINALITY_NOT_CHECKED = "FAIL_TERMINALITY_NOT_CHECKED"
    FAIL_TERMINALITY_UNSATISFIED = "FAIL_TERMINALITY_UNSATISFIED"
    FAIL_MISSING_SOURCE_OUTCOMES = "FAIL_MISSING_SOURCE_OUTCOMES"
    FAIL_SCHEDULER_EXIT_MISSING = "FAIL_SCHEDULER_EXIT_MISSING"
    # F210D: active300 wallclock budget enforcement
    FAIL_WALLCLOCK_BUDGET_EXCEEDED = "FAIL_WALLCLOCK_BUDGET_EXCEEDED"


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
    # Sprint F215D: Canonical early exit classification
    early_exit_class: str | None = None

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

    # F212D: Swap gate — high swap detected during preflight/live gate
    swap_gate_triggered: bool | None = None

    # F220F: Swap tiered policy telemetry
    swap_policy_tier: str | None = None  # "clean" | "diagnostic" | "hard_block"
    swap_gate_reason: str | None = None

    # F215D: Comparable result — whether run is comparable to clean-swap baseline
    # False when hardware_constrained=True or swap_gate_triggered=True
    comparable_result: bool | None = None

    # F215D: Taint reason — why comparable_result=False (if applicable)
    # Set to "high_swap" when running with --allow-high-swap
    taint_reason: str | None = None

    # Live KPI (F207J)
    live_kpi: dict | None = None

    # Public pipeline acceptance telemetry (F207K)
    public_pipeline: dict | None = None

    # Acquisition strategy telemetry (F207Q)
    acquisition_strategy: dict | None = None

    # F216B: Acquisition profile telemetry
    acquisition_profile: str | None = None  # "default" | "nonfeed_diagnostic"
    nonfeed_priority_enabled: bool = False  # F223A: from acquisition_report.nonfeed_priority_enabled
    nonfeed_profile_expected_lanes: tuple[str, ...] = ()  # F223A: from acquisition_report.nonfeed_profile_expected_lanes

    # Windup guard observation telemetry (F207S)
    windup_guard_observation: dict | None = None

    # Return guard observation telemetry (F207T)
    return_guard_observation: dict | None = None

    # Scheduler exit path telemetry (F207V-B)
    scheduler_exit: dict | None = None

    # F208F: active300 acquisition terminality wiring
    acquisition_terminality_checked: bool | None = None
    acquisition_terminality_satisfied: bool | None = None
    acquisition_terminality_missing_lanes: tuple[str, ...] | None = None
    acquisition_terminality_report: dict | None = None

    # F2130: Runtime Authority — which execution path was used to run the sprint
    # Canonical: calls core.__main__.run_sprint() directly
    # CLI: invokes `python -m hledac.universal` shell command
    # Noncanonical: manually constructs scheduler/pipeline pieces
    # Dry-run: validates command construction without running sprint
    runtime_authority_path: str | None = None  # "canonical_core_run_sprint" | "canonical_cli_sprint" | "noncanonical_manual_scheduler" | "dry_run_no_runtime"
    runtime_authority_module: str | None = None  # e.g. "hledac.universal.core.__main__"
    runtime_authority_function: str | None = None  # e.g. "run_sprint"
    runtime_authority_is_canonical: bool | None = None
    runtime_authority_evidence: dict | None = None  # extra context for audit

    # F215G: Runtime provenance — exact source files and environment used for this run
    core_run_sprint_module_file: str | None = None  # e.g. ".../hledac/universal/core/__main__.py"
    core_run_sprint_function_qualname: str | None = None  # "run_sprint"
    sprint_scheduler_module_file: str | None = None  # e.g. ".../hledac/universal/runtime/sprint_scheduler.py"
    live_sprint_measurement_module_file: str | None = None  # e.g. ".../hledac/universal/benchmarks/live_sprint_measurement.py"
    python_executable: str | None = None  # sys.executable
    runtime_cwd: str | None = None  # os.getcwd()
    sys_path_head: str | None = None  # sys.path[0] if not empty
    core_main_mtime: float | None = None  # source file mtime for core/__main__.py
    sprint_scheduler_mtime: float | None = None  # source file mtime for runtime/sprint_scheduler.py

    # F209B: Acquisition prelude telemetry
    acquisition_prelude_checked: bool | None = None
    acquisition_prelude_ran: bool | None = None
    acquisition_prelude_required_lanes: tuple[str, ...] | None = None
    acquisition_prelude_terminal_lanes: tuple[str, ...] | None = None
    acquisition_prelude_missing_lanes: tuple[str, ...] | None = None
    acquisition_prelude_skipped_lanes: dict | None = None
    acquisition_prelude_errors: dict | None = None
    acquisition_prelude_duration_s: float | None = None
    acquisition_prelude_reason: str | None = None

    # F217B: Nonfeed mission controller telemetry (copied from canonical SprintSchedulerResult)
    nonfeed_mission_active: bool | None = None
    nonfeed_required_families: tuple[str, ...] | None = None
    nonfeed_optional_families: tuple[str, ...] | None = None
    nonfeed_family_status: dict | None = None
    nonfeed_all_required_terminal: bool | None = None
    nonfeed_any_accepted: bool | None = None
    nonfeed_provider_failures: tuple[str, ...] | None = None
    nonfeed_memory_skips: tuple[str, ...] | None = None
    nonfeed_mission_exit_reason: str | None = None

    # F208H: Full acquisition report (canonical, not reconstructed)
    # Stored at top-level for validator self-containment
    acquisition_report: dict | None = None

    # F225A: Claims runtime surface status from ClaimsCoordinator.get_claims_runtime_status()
    claims_runtime_status: dict | None = None

    # F208N: Resolved output paths (absolute, resolved before write)
    resolved_output_json: str | None = None
    resolved_output_md: str | None = None

    def to_dict(self) -> dict:
        # CHANGED: replaced asdict(self) with _safe_dataclass_to_dict(self)
        # to prevent RecursionError when live_kpi or acquisition_report contain
        # nested dataclass instances or arbitrary structured data at runtime.
        d = _safe_dataclass_to_dict(self)
        d["mode"] = self.mode.value
        d["status"] = self.status.value
        # F208H: Alias for validator compatibility — maps internal status to live_run_status
        d["live_run_status"] = self.status.value
        # F208H: branch_mix alias for validator compatibility (looks for feed count at top level)
        # Validator checks branch_mix.get("feed", 0) — ensure feed key exists if feed_findings does
        if self.runtime_truth and isinstance(self.runtime_truth, dict):
            _bm = self.runtime_truth.get("branch_mix", {})
            if isinstance(_bm, dict):
                _d_bm = dict(_bm)
                # Map feed_findings → feed for validator compatibility
                if "feed" not in _d_bm and "feed_findings" in _d_bm:
                    _d_bm["feed"] = _d_bm["feed_findings"]
                d["branch_mix"] = _d_bm
        # F208H: public_terminal_state and ct_terminal_state derived from source_family_outcomes
        # These are read by validator for active300 domain queries
        _lk = self.live_kpi or {}
        _sfo = _lk.get("source_family_outcomes", [])
        if isinstance(_sfo, list) and _sfo:
            # Normalize list of dicts to determine terminal state
            _pub = next((x for x in _sfo if isinstance(x, dict) and x.get("family") == "public"), None)
            _ct = next((x for x in _sfo if isinstance(x, dict) and x.get("family") == "ct"), None)
            if _pub is not None:
                d["public_terminal_state"] = "COMPLETED" if (_pub.get("attempted") and not _pub.get("skipped")) else "NEVER_ATTEMPTED"
            if _ct is not None:
                d["ct_terminal_state"] = "COMPLETED" if (_ct.get("attempted") and not _ct.get("skipped")) else "NEVER_ATTEMPTED"
        elif isinstance(_sfo, dict) and _sfo:
            # Dict form: {"public": {...}, "ct": {...}}
            d["public_terminal_state"] = "COMPLETED" if _sfo.get("public", {}).get("attempted") else "NEVER_ATTEMPTED"
            d["ct_terminal_state"] = "COMPLETED" if _sfo.get("ct", {}).get("attempted") else "NEVER_ATTEMPTED"
        # F214F: Research quality top-level fields extracted from live_kpi["research_quality"]
        _rq = _lk.get("research_quality", {})
        if isinstance(_rq, dict):
            d["research_quality_grade"] = _rq.get("grade")
            d["research_quality_score"] = _rq.get("total_quality_score")
            d["research_quality_comparable"] = _rq.get("research_quality_comparable")

        # F215A: CANONICAL MEASUREMENT BOUNDARY — Explicit report sections
        # canonical_report_snapshot: fields copied verbatim from canonical sprint report.
        # These are the ONE source of truth for sprint outcome. Benchmark does NOT reconstruct these.
        # missing_canonical_fields: list of fields that were absent in the canonical report.
        _mcf = _lk.get("missing_canonical_fields", [])
        _sfo_canonical = _lk.get("source_family_outcomes")
        # None = absent; [] (empty list) = present but zero families (valid canonical state)
        _sfo_present = _sfo_canonical is not None
        d["canonical_report_snapshot"] = {
            "source_family_outcomes": _sfo_canonical if _sfo_present else None,
            "source_family_outcomes_present": _sfo_present,
            "missing_canonical_fields": _mcf if isinstance(_mcf, list) else [],
            # Canonical sprint identity
            "sprint_id": self.sprint_id,
            "checkpoint_zero_category": self.checkpoint_zero_category,
            "primary_signal_source": self.primary_signal_source,
            # Canonical timing
            "planned_duration_s": self.planned_duration_s,
            "actual_duration_s": self.actual_duration_s,
            # Canonical counts
            "findings_count": self.findings_count,
            "accepted_findings": self.accepted_findings,
            "cycles_completed": self.cycles_completed,
            "cycles_started": self.cycles_started,
        }

        # measurement_metadata: fields produced BY THE BENCHMARK, not by the canonical sprint.
        # These are measurement artifacts, not sprint truth. They document the measurement context.
        d["measurement_metadata"] = {
            "measurement_id": self.measurement_id,
            "mode": self.mode.value,
            "status": self.status.value,
            "profile": self.profile,
            "query": self.query,
            "aggressive_mode": self.aggressive_mode,
            "deep_probe": self.deep_probe,
            # Runtime authority
            "runtime_authority_path": self.runtime_authority_path,
            "runtime_authority_is_canonical": self.runtime_authority_is_canonical,
            # Benchmark timing (wallclock)
            "start_time_iso": self.start_time_iso,
            "end_time_iso": self.end_time_iso,
            "planned_duration_s": self.planned_duration_s,
            "actual_duration_s": self.actual_duration_s,
            # Memory measurement
            "uma_pre_used_gib": self.uma_pre_used_gib,
            "uma_pre_swap_gib": self.uma_pre_swap_gib,
            "uma_pre_state": self.uma_pre_state,
            "uma_post_used_gib": self.uma_post_used_gib,
            "uma_post_swap_gib": self.uma_post_swap_gib,
            "uma_post_state": self.uma_post_state,
            # Profile truthfulness
            "active_runtime_expected": self.active_runtime_expected,
            "expected_windup_lead_s": self.expected_windup_lead_s,
            "expected_active_window_s": self.expected_active_window_s,
            "profile_verdict": self.profile_verdict,
            # Run quality
            "run_quality_verdict": self.run_quality_verdict,
            "hardware_constrained": self.hardware_constrained,
            "swap_warning": self.swap_warning,
            "swap_gate_triggered": self.swap_gate_triggered,
            # F220F: tiered swap policy telemetry
            "swap_policy_tier": self.swap_policy_tier,
            "swap_gate_reason": self.swap_gate_reason,
            # Output paths
            "resolved_output_json": self.resolved_output_json,
            "resolved_output_md": self.resolved_output_md,
            "report_json_path": self.report_json_path,
            # Readiness artifacts
            "stabilization_seal_present": self.stabilization_seal_present,
            "hermetic_regression_manifest_present": self.hermetic_regression_manifest_present,
            "transport_authority_status_present": self.transport_authority_status_present,
            "mlx_wired_limit_seal_present": self.mlx_wired_limit_seal_present,
        }

        # derived_checks: benchmark-internal derivations that reconstruct sprint state from available data.
        # These are INFERRED, not copied from canonical report. Use with caution.
        # NOTE: lane_execution_counts, nonfeed_attempted_families, source_family_counts,
        # and public_fetch_attempted appear in live_kpi as derived fields.
        # They are marked as DERIVED_CHECK to distinguish from canonical_report_snapshot fields.
        d["derived_checks"] = {
            "note": "These fields are DERIVED by the benchmark, not copied from canonical report.",
            "live_kpi_lane_execution_counts": _lk.get("lane_execution_counts", {}),
            "live_kpi_source_family_counts": _lk.get("source_family_counts", {}),
            "live_kpi_nonfeed_attempted_families": _lk.get("nonfeed_attempted_families", []),
            "live_kpi_public_fetch_attempted": _lk.get("public_fetch_attempted", False),
            "live_kpi_nonfeed_accepted_findings": _lk.get("nonfeed_accepted_findings", 0),
            "live_kpi_findings_per_min": _lk.get("findings_per_min", 0.0),
        }
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)