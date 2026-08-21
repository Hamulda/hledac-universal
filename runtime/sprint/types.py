"""
runtime/sprint/types.py — Sprint type definitions and dataclass bundles

F350M-R: Centralized type definitions for sprint phases.
Reduces function signatures from 11-23 parameters to single dataclass bundles.

M1 8GB: All dataclasses use slots=True for reduced memory footprint.
msgspec.Struct used where frozen=True is appropriate for GC pressure reduction.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# Optional: orjson for optimized serialization
try:
    import orjson
except ImportError:
    orjson = None  # type: ignore[assignment]

# msgspec for AcqReportPayload (Issue #9)
# Import lazily via compat layer for msgspec 0.22+ compatibility
try:
    from compat.msgspec_gc_compat import Struct
except ImportError:
    import msgspec as _msgspec

    Struct = _msgspec.Struct  # type: ignore[assignment,misc]


if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    from hledac.universal.runtime.scheduler_v2 import SprintSchedulerV2 as SprintScheduler


class SprintFlags:
    """
    F221-ABORT + F26X-3 + F260: Bounded, immutable view of the CLI flags
    that gate pre-flight guards and layer-injection opt-outs.

    M1 memory friendly: frozen + __slots__ — removes GC tracking + boxing
    (~40 bytes/instance vs ~80 for dataclass).
    """

    __slots__ = (
        "force",
        "no_communication",
        "no_stealth",
        "no_ghost",
        "no_coordination",
        "production",
        "hermes_force",
        "blitz_mode",
    )

    def __init__(
        self,
        force: bool = False,
        no_communication: bool = False,
        no_stealth: bool = False,
        no_ghost: bool = False,
        no_coordination: bool = False,
        production: bool = False,
        hermes_force: bool = False,
        blitz_mode: bool = False,
    ) -> None:
        self.force = force
        self.no_communication = no_communication
        self.no_stealth = no_stealth
        self.no_ghost = no_ghost
        self.no_coordination = no_coordination
        self.production = production
        self.hermes_force = hermes_force
        self.blitz_mode = blitz_mode


@dataclass(slots=True)
class SprintRunContext:
    """
    PHASE REFACTORING F350M-R: Centralized state container for run_sprint phases.

    Replaces ~40+ local variables with a structured dataclass for better
    code organization, testability, and reduced cognitive load.

    MODERN-35: Per-sprint state for previously global resources:
    - denorm_buffer: SprintDenormBuffer from hot_edges_cache
    - session_tracker: SessionTracker from darknet_session_provider
    - duckpgq_graph: DuckPGQGraph from graph_service

    Usage:
        ctx = SprintRunContext(sprint_id="...", phase_times={...})
        await _run_sprint_boot(ctx, query, ...)
        await _run_sprint_execute(ctx, query)
        await _run_sprint_windup(ctx, query)
        await _run_sprint_teardown(ctx)
    """

    # Phase timing
    phase_times: dict[str, float] = field(default_factory=dict)

    # Control flow
    cancel_event: asyncio.Event | None = None

    # Identity
    sprint_id: str = ""
    query_hash: str = ""

    # Core resources
    store: DuckDBShadowStore | None = None
    scheduler: SprintScheduler | None = None

    # Per-sprint resources (MODERN-35)
    power_assertion: Any = field(default=None)
    denorm_buffer: Any = field(default=None)
    session_tracker: Any = field(default=None)
    duckpgq_graph: Any = field(default=None)

    # Memory tracking
    uma_baseline_gib: float = 0.0
    swap_detected_pre: bool = False
    uma_state_pre: str = "ok"

    # Timing
    effective_windup_s: float = 180.0

    # Recovery
    resume_from: dict | None = None
    resume_step: int = 0
    seed_state: Any = field(default=None)

    # Result
    result: Any = field(default=None)

    # Intelligence
    intel: dict = field(default_factory=dict)

    # Optional resources
    evidence_log: Any = field(default=None)
    sprint_lock_mgr: Any = field(default=None)
    sprint_lock_path: pathlib.Path | None = None
    report_path: pathlib.Path | None = None

    # Feeds
    live_feed_urls: list[str] = field(default_factory=list)
    ct_log_client: Any = field(default=None)
    dashboard: Any = field(default=None)

    # State flags
    duckdb_init_ok: bool = False

    # Parameters
    query: str = ""
    duration_s: float = 1800.0
    actual_duration: float = 0.0


@dataclass(frozen=True, slots=True)
class VerdictHintInput:
    """
    Sprint F350M-R: Input bundle for _compute_verdict_and_hint.

    Reduces function signature from 11 parameters to 1.
    """

    aborted: bool
    accepted_findings: int
    dup_rate: float
    public_pct: float
    feed_fnd: int
    hardware_limited: bool
    public_backend_degraded: bool
    public_discovered: int
    total_pattern_hits: int
    public_fetched: int
    stop_requested: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "aborted": self.aborted,
            "accepted_findings": self.accepted_findings,
            "dup_rate": self.dup_rate,
            "public_pct": self.public_pct,
            "feed_fnd": self.feed_fnd,
            "hardware_limited": self.hardware_limited,
            "public_backend_degraded": self.public_backend_degraded,
            "public_discovered": self.public_discovered,
            "total_pattern_hits": self.total_pattern_hits,
            "public_fetched": self.public_fetched,
            "stop_requested": self.stop_requested,
        }


@dataclass(frozen=True, slots=True)
class CheckpointInput:
    """
    Sprint F350M-R: Input bundle for _compute_checkpoint_priority and _compute_checkpoint_category.

    Reduces function signatures from 13/14 parameters to 1.
    """

    accepted_findings: int
    total_pattern_hits: int
    public_error: str | None
    public_discovered: int
    public_backend: bool
    feed_zero_check: bool
    cross_branch_fail_check: bool
    is_pre_active_mem_starved: bool
    is_hardware_limited: bool
    is_meaningful: bool
    uma_state_pre: str
    feed_fnd: int
    phase_times: dict


@dataclass(frozen=True, slots=True)
class RuntimeTruthInput:
    """
    Sprint F350M-R: Input bundle for _runtime_truth.

    Reduces function signature from 15 parameters to 1.
    """

    actual_duration_s: float
    query: str
    duration_s: float
    cycles_completed: int
    cycles_started: int
    accepted_findings: int
    total_pattern_hits: int
    public_accepted_findings: int
    feed_findings: int
    ct_findings: int = 0
    swap_detected: bool = False
    uma_state: str = "ok"
    branch_timeout_count: int = 0
    public_branch_timed_out: bool = False
    ct_branch_timed_out: bool = False


@dataclass(frozen=True, slots=True)
class ReportBuildInput:
    """
    Sprint F350M-R: Input bundle for _build_report_dict.

    Reduces function signature from 23 parameters to 1.
    Bundles context, result, metrics, and classifications computed in windup phase.
    """

    query: str
    duration_s: float
    actual_duration: float
    feed_fnd: int
    dup_rate: float
    findings_per_min: float
    public_pct: float
    src_mix_str: str
    verdict: str
    next_hint: str
    phase_durations: dict
    runtime_truth: dict
    timing_truth: dict
    runtime_truth_level: str
    observed_run_tuple: tuple
    ckpt_category: str
    checkpoint_zero_reason: str
    export_finish_status: str
    uma_peak_gib: float
    ctx: SprintRunContext
    result: Any
    acq_payload_filtered: dict


@dataclass(slots=True)
class ExportHandoffInput:
    """
    Sprint F350M-R: Input bundle for _build_export_handoff.

    Reduces function signature from 17 parameters to 1.
    Bundles context, result, and pre-computed classifications.
    """

    query: str
    duration_s: float
    actual_duration: float
    runtime_truth: dict
    timing_truth: dict
    runtime_truth_level: str
    observed_run_tuple: tuple
    src_mix_str: str
    ckpt_category: str
    checkpoint_zero_reason: str
    export_finish_status: str
    phase_durations: dict
    ctx: SprintRunContext
    result: Any
    top_seed_nodes: list
    live_feed_urls: list
    acq_payload: dict


class _CheckpointPriority:
    """
    Priority constants for checkpoint category branching.

    Used by _compute_checkpoint_priority() to determine which condition
    matched first in the checkpoint category chain.
    """

    SIGNAL_REACHES_FINDINGS = 1
    PRE_ACTIVE_MEMORY_STARVATION = 2
    SURVIVAL_ACTIVE_MINIMAL = 3
    HARDWARE_LIMITED_SMOKE = 4
    PUBLIC_BACKEND_DEGRADED = 5
    DEGRADED_PUBLIC_BLOCKER = 6
    MEANINGFUL_EMPTY_RUN = 7
    FEED_INGRESS_BLOCKER = 8
    FEED_SOURCE_INACCESSIBLE = 9
    SHORT_SIGNAL = 10
    TRUE_DEPLETED_QUERY = 11
    CROSS_BRANCH_SOURCE_INACCESSIBLE = 12
    WINDUP_EXPORT_FAIL_SOFT = 13
    DEPLETED = 14


_CHECKPOINT_PRIORITY_MAP: dict[int, tuple[str, str]] = {
    _CheckpointPriority.SIGNAL_REACHES_FINDINGS: ("signal_reaches_findings", "static"),
    _CheckpointPriority.PRE_ACTIVE_MEMORY_STARVATION: ("pre_active_memory_starvation", "static"),
    _CheckpointPriority.SURVIVAL_ACTIVE_MINIMAL: ("survival_active_minimal", "evidence_note"),
    _CheckpointPriority.HARDWARE_LIMITED_SMOKE: ("hardware_limited_smoke", "evidence_note"),
    _CheckpointPriority.PUBLIC_BACKEND_DEGRADED: ("public_backend_degraded", "public_error_degraded"),
    _CheckpointPriority.DEGRADED_PUBLIC_BLOCKER: ("degraded_public_blocker", "public_error_blocked"),
    _CheckpointPriority.MEANINGFUL_EMPTY_RUN: ("meaningful_empty_run", "static"),
    _CheckpointPriority.FEED_INGRESS_BLOCKER: ("feed_ingress_blocker", "public_discovered"),
    _CheckpointPriority.FEED_SOURCE_INACCESSIBLE: ("feed_source_inaccessible", "static"),
    _CheckpointPriority.SHORT_SIGNAL: ("short_signal", "static"),
    _CheckpointPriority.TRUE_DEPLETED_QUERY: ("true_depleted_query", "static"),
    _CheckpointPriority.CROSS_BRANCH_SOURCE_INACCESSIBLE: ("cross_branch_source_inaccessible", "static"),
    _CheckpointPriority.WINDUP_EXPORT_FAIL_SOFT: ("windup_export_fail_soft", "evidence_note"),
    _CheckpointPriority.DEPLETED: ("depleted", "static"),
}

_CHECKPOINT_REASON_TEMPLATES: dict[int, str] = {
    _CheckpointPriority.SIGNAL_REACHES_FINDINGS: "signal_reaches_findings",
    _CheckpointPriority.PRE_ACTIVE_MEMORY_STARVATION: "pre_active_memory_starvation",
    _CheckpointPriority.MEANINGFUL_EMPTY_RUN: "meaningful_empty_run",
    _CheckpointPriority.FEED_SOURCE_INACCESSIBLE: "feed_source_inaccessible",
    _CheckpointPriority.SHORT_SIGNAL: "short_signal_no_findings",
    _CheckpointPriority.TRUE_DEPLETED_QUERY: "true_depleted_query:hits_without_acceptance",
    _CheckpointPriority.CROSS_BRANCH_SOURCE_INACCESSIBLE: "cross_branch_source_inaccessible",
    _CheckpointPriority.DEPLETED: "depleted_no_pattern_hits",
}


_VERDICT_TABLE: list[tuple[tuple, str]] = [
    (("aborted", True, "accepted_findings", lambda v: v > 0), "ABORTED_PARTIAL"),
    (("aborted", True), "ABORTED_HARD"),
    (("hardware_limited", True), "HARDWARE_LIMITED"),
    (("public_backend_degraded", True), "DEGRADED"),
    (("accepted_findings", 0, "public_discovered", lambda v: v > 0), "NOVELTY"),
    (("accepted_findings", 0, "total_pattern_hits", 0), "DEPLETED"),
    (("accepted_findings", 0), "SILENT"),
    (("dup_rate", lambda v: v > 85), "NOISE_HEAVY"),
    (("public_pct", lambda v: v > 60), "PUBLIC_LED"),
    (("public_pct", lambda v: v > 25), "MIXED"),
    (("feed_fnd", lambda v: v > 0), "FEED_LED"),
]

_VERDICT_TEMPLATES: dict[str, str] = {
    "ABORTED_PARTIAL": "⚠️  ABORTED (partial) — {base}",
    "ABORTED_HARD": "⚠️  ABORTED: hard stop, no signal collected",
    "HARDWARE_LIMITED": "💾  HARDWARE-LIMITED: swap/memory pressure blocked entry",
    "DEGRADED": "🌐  DEGRADED: public backend/network error — check TOR/proxy/config",
    "NOVELTY": "🔍  NOVELTY: public found hits, feed accepted nothing",
    "DEPLETED": "🗿  DEPLETED: no pattern hits anywhere",
    "SILENT": "🤷  SILENT: pattern hits but no accepted findings",
    "NOISE_HEAVY": "📦  NOISE-HEAVY: duplicated heavily",
    "PUBLIC_LED": "🌐  PUBLIC-LED: public discovery dominated",
    "MIXED": "⚖️  MIXED: public contributed meaningfully",
    "FEED_LED": "✅  FEED-LED: feed sources strong",
    "SIGNAL": "✅  SIGNAL: good feed performance",
}

_HINT_TABLE: list[tuple[tuple, str]] = [
    (("hardware_limited", True), "hardware memory pressure — free RAM or restart before next run"),
    (("accepted_findings", 0, "total_pattern_hits", 0), "query may be too narrow — broaden terms or switch seed"),
    (("dup_rate", lambda v: v > 80), "high dup rate — consider narrowing query scope"),
    (("public_pct", lambda v: v > 60), "public discovery effective — let it run longer next time"),
    (
        ("public_pct", lambda v: v < 10, "feed_fnd", 0),
        "feed yield low — check if sources still alive (urlhaus, threatfox)",
    ),
    (
        ("public_pct", lambda v: v < 10, "feed_fnd", lambda v: v > 0),
        "feed performing — rely on feed-first, use public as supplemental",
    ),
    (
        ("public_discovered", lambda v: v > 0, "public_fetched", 0),
        "public discovered but not fetched — check network/TOR",
    ),
    (("stop_requested", True), "early stop triggered — lower threshold or widen query"),
]


def _match_condition(value: Any, expected: Any) -> bool:
    """Match a condition value against expected (supports lambdas)."""
    if callable(expected):
        return expected(value)
    return value == expected


def _find_table_match(table: list[tuple[tuple, Any]], ctx: dict[str, Any], default: Any) -> Any:
    """
    Sprint F350M-R: Generic decision table matcher.

    Iterates through table rows of ((field, expected, field, expected, ...), result)
    and returns the first matching result, or default if no match.
    """
    for conditions, result in table:
        matched = True
        for i in range(0, len(conditions), 2):
            field = conditions[i]
            expected = conditions[i + 1]
            if not _match_condition(ctx[field], expected):
                matched = False
                break
        if matched:
            return result
    return default


def _get_aborted_base_verdict(dup_rate: float, public_pct: float, feed_fnd: int) -> str:
    """Get verdict base for aborted sprint with partial results."""
    if dup_rate > 85:
        return "📦  NOISE-HEAVY: duplicated heavily"
    if public_pct > 60:
        return "🌐  PUBLIC-LED: public discovery dominated"
    if public_pct > 25:
        return "⚖️  MIXED: public contributed meaningfully"
    if feed_fnd > 0:
        return "✅  FEED-LED: feed sources strong"
    return "✅  SIGNAL: good feed performance"


def _compute_verdict_and_hint(inp: VerdictHintInput) -> tuple[str, str]:
    """
    Sprint F350M-R: Extracted verdict + next_hint heuristics using decision tables.

    Reduces run_sprint cyclomatic complexity by ~25 points.
    Pure function — no side effects, no external dependencies.
    """
    ctx = inp.to_dict()
    verdict_key = _find_table_match(_VERDICT_TABLE, ctx, "SIGNAL")

    if verdict_key == "ABORTED_PARTIAL":
        base = _get_aborted_base_verdict(inp.dup_rate, inp.public_pct, inp.feed_fnd)
        verdict = _VERDICT_TEMPLATES["ABORTED_PARTIAL"].format(base=base)
    else:
        verdict = _VERDICT_TEMPLATES.get(verdict_key, "✅  SIGNAL: good feed performance")

    next_hint = _find_table_match(_HINT_TABLE, ctx, "current query and source mix working — continue as-is")
    return (verdict, next_hint)


def _compute_checkpoint_priority(inp: CheckpointInput) -> int:
    """
    Sprint F350M-R: Compute checkpoint priority from conditions.

    Extracts the branching logic into a single function, eliminating
    duplicate condition evaluation between _ckpt_category and _checkpoint_zero_reason.

    Returns priority integer (lower = higher priority, checked first).
    """
    if inp.accepted_findings > 0:
        return _CheckpointPriority.SIGNAL_REACHES_FINDINGS
    if inp.is_pre_active_mem_starved:
        return _CheckpointPriority.PRE_ACTIVE_MEMORY_STARVATION
    if inp.is_meaningful and inp.uma_state_pre in ("warn", "critical", "emergency"):
        return _CheckpointPriority.SURVIVAL_ACTIVE_MINIMAL
    if inp.is_hardware_limited:
        return _CheckpointPriority.HARDWARE_LIMITED_SMOKE
    if inp.public_backend:
        return _CheckpointPriority.PUBLIC_BACKEND_DEGRADED
    if inp.public_error:
        return _CheckpointPriority.DEGRADED_PUBLIC_BLOCKER
    if inp.is_meaningful and inp.total_pattern_hits == 0 and inp.accepted_findings == 0:
        return _CheckpointPriority.MEANINGFUL_EMPTY_RUN
    if inp.feed_zero_check and inp.public_discovered > 0:
        return _CheckpointPriority.FEED_INGRESS_BLOCKER
    if inp.feed_zero_check and inp.total_pattern_hits == 0 and not inp.public_error:
        return _CheckpointPriority.FEED_SOURCE_INACCESSIBLE
    if inp.is_meaningful and inp.total_pattern_hits > 0 and inp.accepted_findings == 0:
        return _CheckpointPriority.SHORT_SIGNAL
    if inp.accepted_findings == 0 and inp.total_pattern_hits > 0 and not inp.public_backend:
        return _CheckpointPriority.TRUE_DEPLETED_QUERY
    if inp.cross_branch_fail_check:
        return _CheckpointPriority.CROSS_BRANCH_SOURCE_INACCESSIBLE
    if inp.accepted_findings == 0 and inp.phase_times.get("WINDUP", 0) > 0 and inp.is_meaningful:
        return _CheckpointPriority.WINDUP_EXPORT_FAIL_SOFT
    return _CheckpointPriority.DEPLETED


def _compute_checkpoint_category(inp: CheckpointInput, evidence_note: str) -> tuple[str, str]:
    """
    Sprint F350M-R: Extracted checkpoint category + reason taxonomy.

    Reduces run_sprint cyclomatic complexity by ~30 points.
    Pure function — no side effects, no external dependencies.

    Bucket set:
      signal_reaches_findings, pre_active_memory_starvation, survival_active_minimal,
      hardware_limited_smoke, public_backend_degraded, degraded_public_blocker,
      meaningful_empty_run, feed_ingress_blocker, feed_source_inaccessible,
      true_depleted_query, short_signal, cross_branch_source_inaccessible,
      windup_export_fail_soft, depleted

    Returns (_ckpt_category: str, _checkpoint_zero_reason: str)
    """
    priority = _compute_checkpoint_priority(inp)
    _ckpt_category, reason_type = _CHECKPOINT_PRIORITY_MAP[priority]

    if reason_type == "static":
        _checkpoint_zero_reason = _CHECKPOINT_REASON_TEMPLATES[priority]
    elif reason_type == "evidence_note":
        _checkpoint_zero_reason = evidence_note if evidence_note else "unknown_checkpoint_reason"
    elif reason_type == "public_error_degraded":
        _checkpoint_zero_reason = inp.public_error or "public_backend_degraded"
    elif reason_type == "public_error_blocked":
        _checkpoint_zero_reason = inp.public_error or "public_backend_blocked"
    elif reason_type == "public_discovered":
        _checkpoint_zero_reason = f"public_discovered={inp.public_discovered}"
    else:
        _checkpoint_zero_reason = "unknown_reason"

    return (_ckpt_category, _checkpoint_zero_reason)


_PLATFORM_INFO: dict[str, str] = {"python_version": sys.version.split()[0], "macos_version": None}

try:
    import platform as _platform_mod

    _PLATFORM_INFO["macos_version"] = _platform_mod.mac_ver()[0] or "unknown"
except Exception:
    _PLATFORM_INFO["macos_version"] = "unknown"


# Type aliases for complex field types (matches SprintSchedulerResult)
_ANN = dict[str, Any]
_TUP_STR = tuple[str, ...]
_TUP_TUP_STR_INT = tuple[tuple[str, int], ...]
_TUP_TUP_STR_STR = tuple[tuple[str, str], ...]
_TUP_ANY = tuple[Any, ...]


class AcqReportPayload(Struct, frozen=True, gc=False):
    """
    ISSUE #9 FIX: msgspec.Struct for acquisition report payload.

    Mirrors SprintSchedulerResult fields accessed in _scheduler_result_acquisition_payload.
    Uses frozen=True for immutability and gc=False for M1 8GB memory optimization.

    Conversion from SprintSchedulerResult (dataclass) via msgspec.convert():
        r = msgspec.convert(result, AcqReportPayload)

    Fields are a subset of SprintSchedulerResult (100+ fields) filtered to those
    actually accessed in windup phase report generation.
    """

    accepted_findings: int = 0
    total_pattern_hits: int = 0

    public_discovered: int = 0
    public_accepted_findings: int = 0
    public_error: str = ""
    public_terminal_stage: str = ""
    public_stage_counters: _ANN = field(default_factory=dict)
    public_provider_selection_debug: _ANN = field(default_factory=dict)

    ct_log_discovered: int = 0
    ct_log_stored: int = 0
    ct_log_accepted_findings: int = 0
    ct_log_error: str = ""
    ct_terminal_stage: str = ""
    ct_planned: bool = False
    ct_scheduled: bool = False
    ct_request_attempted: bool = False
    ct_provider_status: str = ""
    ct_cache_used: bool = False
    ct_cache_stale: bool = False
    ct_cache_age_s: float = 0.0
    ct_quarantine_count: int = 0
    ct_quarantine_samples: _TUP_STR = ()
    ct_raw_count: int = 0
    ct_bridge_invoked: bool = False
    ct_candidates_built: int = 0
    ct_storage_attempted: bool = False
    ct_storage_accepted: bool = False
    ct_provider_selected: str = ""
    ct_request_timeout: bool = False
    ct_prelude_missing_but_final_attempted: bool = False

    quality_rejection_summary_by_family: _ANN = field(default_factory=dict)
    duplicate_rejection_summary_by_family: _ANN = field(default_factory=dict)
    low_information_by_family: _ANN = field(default_factory=dict)
    nonfeed_candidate_ledger_summary: _ANN = field(default_factory=dict)

    doh_planned: bool = False
    doh_scheduled: bool = False
    doh_request_attempted: bool = False
    doh_domains_attempted: int = 0
    doh_raw_count: int = 0
    doh_accepted_findings: int = 0
    doh_terminal_stage: str = ""
    doh_provider_errors: _TUP_STR = ()
    doh_cache_used: bool = False

    nonfeed_prelude_enabled: bool = False
    nonfeed_prelude_expected_lanes: _TUP_STR = ()
    nonfeed_prelude_attempted_lanes: _TUP_STR = ()
    nonfeed_prelude_terminal_lanes: _TUP_STR = ()
    nonfeed_prelude_missing_lanes: _TUP_STR = ()
    nonfeed_prelude_error_by_lane: _ANN = field(default_factory=dict)
    nonfeed_prelude_accepted_by_lane: _ANN = field(default_factory=dict)
    nonfeed_prelude_duration_s: float = 0.0
    nonfeed_prelude_feed_blocked_until_complete: bool = False
    nonfeed_expected_lanes: _TUP_STR = ()
    nonfeed_missing_expected_lanes: _TUP_STR = ()

    pivot_seed_domains: _TUP_STR = ()
    pivot_seed_ips: _TUP_STR = ()
    pivot_seed_urls: _TUP_STR = ()
    pivot_seed_hashes: _TUP_STR = ()
    pivot_seed_cves: _TUP_STR = ()
    seed_context_propagated: bool = False
    lanes_unlocked_by_seed_context: list[str] = field(default_factory=list)

    acquisition_plan_build_failed: bool = False
    acquisition_plan_build_error_type: str = ""
    acquisition_plan_build_error: str = ""
    acquisition_plan_present_for_prelude: bool = False
    acquisition_plan_lanes_for_prelude: _TUP_STR = ()
    acquisition_plan_enabled_lanes_for_prelude: _TUP_STR = ()
    acquisition_plan_profile_for_prelude: str = ""
    acquisition_plan_build_error_for_prelude: str = ""

    return_guard_checked: bool = False
    return_guard_satisfied: bool = False
    return_guard_block_reason: str = ""
    return_guard_attempted_lanes: _TUP_STR = ()
    return_guard_skipped_lanes: _ANN = field(default_factory=dict)
    return_guard_errors: _ANN = field(default_factory=dict)
    return_guard_delayed_for_nonfeed: bool = False
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    windup_guard_required_lanes: _TUP_STR = ()
    windup_guard_not_applicable: bool = False
    windup_guard_last_reason: str = ""
    windup_guard_last_allowed: bool | None = None
    windup_guard_last_callback_not_executed_reason: str = ""

    prewindup_barrier_checked: bool = False
    prewindup_barrier_satisfied: bool = False
    prewindup_barrier_required_lanes: _TUP_STR = ()
    prewindup_barrier_attempted_lanes: _TUP_STR = ()
    prewindup_barrier_skipped_lanes: _ANN = field(default_factory=dict)
    prewindup_barrier_errors: _ANN = field(default_factory=dict)
    prewindup_barrier_duration_s: float = 0.0
    windup_delayed_for_nonfeed: bool = False

    scheduler_exit_path: str | None = None
    scheduler_exit_reason: str | None = None
    scheduler_exit_phase: str | None = None
    scheduler_exit_cycle: int | None = None
    scheduler_exit_elapsed_s: float | None = None
    scheduler_exit_guard_checked: bool = False
    scheduler_exit_guard_satisfied: bool | None = None

    acquisition_terminality_checked: bool = False
    acquisition_terminality_satisfied: bool = False
    acquisition_terminality_missing_lanes: _TUP_STR = ()
    acquisition_terminality_report: _ANN = field(default_factory=dict)

    acquisition_prelude_checked: bool = False
    acquisition_prelude_ran: bool = False
    acquisition_prelude_required_lanes: _TUP_STR = ()
    acquisition_prelude_terminal_lanes: _TUP_STR = ()
    acquisition_prelude_missing_lanes: _TUP_STR = ()
    acquisition_prelude_skipped_lanes: _ANN = field(default_factory=dict)
    acquisition_prelude_errors: _ANN = field(default_factory=dict)
    acquisition_prelude_duration_s: float = 0.0
    acquisition_prelude_reason: str = ""

    early_exit_class: str = ""
    early_exit_reason: str = ""

    requested_duration_s: float = 0.0
    actual_duration_s: float = 0.0
    elapsed_pct: float = 0.0
    active_window_budget_s: float = 0.0
    active_window_elapsed_s: float = 0.0

    budget_violations: int = 0

    wayback_terminal_state: str = ""
    passive_dns_terminal_state: str = ""

    acquisition_lane_outcomes: _TUP_ANY = ()


def _get_report_serialize_options() -> int:
    """
    Lazy evaluation of orjson serialization options.

    Handles the case where orjson is not installed (returns 0).
    """
    if orjson is None:
        return 0
    if os.environ.get("HLEDAC_REPORT_PRETTY_PRINT", "0") == "1":
        return orjson.OPT_INDENT_2
    return orjson.OPT_APPEND_NEWLINE


def _serialize_report(data: dict[str, Any]) -> bytes:
    """
    Optimized report serialization using orjson.

    - No indentation in production (faster, smaller files)
    - Appends newline for POSIX compliance
    - Uses OPT_SERIALIZE_NUMPY if numpy arrays present (auto-detected)
    """
    if orjson is None:
        # Fallback to standard json if orjson not available
        import json

        return (json.dumps(data) + "\n").encode("utf-8")

    options = _get_report_serialize_options()
    try:
        import numpy

        options |= orjson.OPT_SERIALIZE_NUMPY
    except ImportError:
        pass
    return orjson.dumps(data, option=options)


def _is_meaningful_run(
    actual_duration_s: float,
    cycles_completed: int,
    cycles_started: int,
    accepted_findings: int,
    total_pattern_hits: int,
    swap_detected: bool = False,
    uma_state: str = "ok",
) -> tuple[bool, str]:
    """
    Distinguish smoke from meaningful active evidence.

    Returns (is_meaningful, evidence_note).
    Smoke: too short, too few cycles, no signal whatsoever.
    Meaningful: enough runtime or evidence of real work.

    F176A: Hardware-limited smoke detection — swap/memory pressure + zero cycles
    is a distinct hardware-limited classification, NOT depleted query.
    """
    if cycles_started == 0:
        if swap_detected or uma_state in ("critical", "emergency"):
            return (False, "hardware_limited_smoke: zero cycles, memory pressure detected")
        return (False, "zero cycles started — entry only, no active work")

    if accepted_findings > 0:
        return (True, f"found {accepted_findings} findings despite short runtime")

    if total_pattern_hits > 0 and actual_duration_s >= 15:
        return (True, f"pattern activity ({total_pattern_hits} hits) despite short run")

    if actual_duration_s < 30 and cycles_completed < 3:
        return (False, f"runtime {actual_duration_s:.0f}s and {cycles_completed} cycles below minimum")

    if actual_duration_s < 10:
        return (False, f"runtime {actual_duration_s:.1f}s — entry/import only")

    if actual_duration_s < 180 and accepted_findings == 0 and total_pattern_hits == 0:
        return (
            False,
            f"runtime {actual_duration_s:.0f}s < 180s floor, no findings, no pattern hits — below meaningful threshold",
        )

    return (
        True,
        f"{actual_duration_s:.0f}s runtime, {cycles_completed}/{cycles_started} cycles completed, no findings but within normal parameters",
    )
