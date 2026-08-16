"""
runtime/state — Canonical Runtime & Research State

Single source of truth for global runtime flags, sprint-start snapshots,




and cross-component state. All state classes are frozen msgspec.Struct
for M1 8GB RAM efficiency (no __dict__, no GC overhead).

CLASSES:
    RuntimeState(msgspec.Struct, frozen=True):
        Canonical uvloop state (set once at boot).
        Consumed by session_runtime for get_session_runtime_status()["uvloop_enabled"].

    ResearchContextSnapshot(msgspec.Struct, frozen=True):
        Immutable snapshot of ResearchContext at sprint start.
        Carries metadata for EvidenceLog correlation handoff.

    SprintMetrics(msgspec.Struct, frozen=True):
        Per-sprint atomic counters — replaces mutable SprintSchedulerResult fields.
        Uses copy-on-write semantics via ContextVar for task isolation.

    SprintRunSnapshot(msgspec.Struct, frozen=True):
        Immutable snapshot of all sprint state at a point in time.
        Created at sprint start, committable at phase transitions.

MODULE INVARIANTS:
    [1] RuntimeState.uvloop_installed is set ONCE at boot via mark_uvloop_installed()
    [2] All state classes use msgspec.Struct (frozen=True) — M1 RAM efficiency
    [3] Cross-component state passes via ContextVar + immutable copy
    [4] SprintMetrics uses copy-on-write: .copy() returns new instance, .set() commits

BC COMPATIBILITY:
    All frozen structs provide .copy() method that returns mutable dict equivalent
    for code that depends on mutable access patterns.

INTENT:
    Centralized state management replacing scattered mutable state in
    sprint_scheduler.py, evidence_log.py, and __main__.py.
"""
from __future__ import annotations


import contextvars
import msgspec
from typing import Any
from _core import aclose


# ─────────────────────────────────────────────────────────────────────────────
# RuntimeState — Canonical Runtime Flags (set once at boot)
# ─────────────────────────────────────────────────────────────────────────────


class RuntimeState(msgspec.Struct, frozen=True, gc=False):
    """
    Canonical runtime state — single source of truth for global runtime flags.

    F266-UVLOOP: Unified uvloop state resolution.
    - uvloop_installed: set by __main__.py after successful uvloop.install()
    - Consumed by network/session_runtime.get_session_runtime_status()["uvloop_enabled"]

    INVARIANTS:
        [1] uvloop_installed is written ONCE at boot — mark_uvloop_installed()
        [2] uvloop_installed is READ by session_runtime at runtime
        [3] No other mutable global state — everything else is task-local via ContextVar

    M1 8GB: frozen=True + msgspec.Struct = no __dict__, no GC overhead, ~50 bytes/instance.
    """

    uvloop_installed: bool = False

    def mark_uvloop_installed(self) -> RuntimeState:
        """
        Return a NEW RuntimeState with uvloop_installed=True.

        Because RuntimeState is frozen, we return a new instance rather than mutating.
        This preserves the frozen invariant while allowing one-time initialization.
        """
        return RuntimeState(uvloop_installed=True)


# Canonical singleton — set once at boot, read by session_runtime
_RUNTIME_STATE: RuntimeState = RuntimeState()


def get_runtime_state() -> RuntimeState:
    """Get the current RuntimeState singleton."""
    return _RUNTIME_STATE


def mark_uvloop_installed() -> None:
    """
    Mark uvloop as installed in the canonical RuntimeState.

    Called by __main__.py after successful uvloop.install().
    This is the ONLY write path for RuntimeState.uvloop_installed.

    Idempotent: calling multiple times has no additional effect.
    """
    global _RUNTIME_STATE
    _RUNTIME_STATE = _RUNTIME_STATE.mark_uvloop_installed()


# ─────────────────────────────────────────────────────────────────────────────
# ResearchContextSnapshot — Sprint-Start Snapshot
# ─────────────────────────────────────────────────────────────────────────────


class ResearchContextSnapshot(msgspec.Struct, frozen=True, gc=False):
    """
    Immutable snapshot of ResearchContext at sprint start.

    ROLE: Carrier between tiers at sprint boundary.
        ResearchContextSnapshot (carrier) --handoff metadata--> EvidenceLog (ledger)

    This is NOT the active ResearchContext used during the sprint.
    It is a frozen snapshot taken at sprint start for post-sprint analysis
    and EvidenceLog correlation.

    The active ResearchContext lives in coordinators/research_coordinator.py
    (msgspec.Struct, frozen=True) and is used during sprint execution.

    AUTHORITY BOUNDARY:
        - ResearchContextSnapshot carries state but does NOT sample, govern,
          or budget resources (same boundary as ResearchContext).
        - For UMA sampling use: utils/uma_budget.py (sampler)
        - For UMA governance use: core/resource_governor.py (governor)
        - For request budgeting use: resource_allocator.py (allocator)

    FIELDS:
        query: Original sprint query
        research_id: Unique research identifier
        iteration: Current iteration count
        frontiers_count: Number of active research frontiers at snapshot time
        handoff_metadata: Optional typed handoff metadata for EvidenceLog correlation

    M1 8GB: frozen=True + msgspec.Struct = no __dict__, no GC overhead.
    """

    query: str = ""
    research_id: str = ""
    iteration: int = 0
    frontiers_count: int = 0
    handoff_metadata: dict | None = None  # Forward-compat: typed ContextHandoffMetadata

    def to_correlation_dict(self) -> dict[str, str | None]:
        """
        Convert handoff_metadata to RunCorrelation-compatible dict for EvidenceLog.

        STABLE CORRELATION GRAMMAR (always 4 keys, values may be None):
            run_id     = research_id (run context propagation)
            branch_id  = None       (set by branch layer, not carrier)
            provider_id = None      (set by provider layer, not carrier)
            action_id  = None       (set by action layer, not carrier)

        Returns:
            dict with exactly 4 keys suitable for EvidenceLog.create_event(correlation=...)
        """
        return {
            "run_id": self.research_id,
            "branch_id": None,
            "provider_id": None,
            "action_id": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SprintMetrics — Per-Sprint Atomic Counters (frozen, copy-on-write)
# ─────────────────────────────────────────────────────────────────────────────


class SprintMetrics(msgspec.Struct, frozen=True, gc=False):
    """
    Per-sprint atomic counters — replaces mutable SprintSchedulerResult fields.

    ISSUE #22: Modernized state management with frozen msgspec.Struct + ContextVar.

    All fields are immutable (frozen=True). Updates use copy-on-write pattern:
        new_metrics = current_metrics.copy(cycles_started=current_metrics.cycles_started + 1)
        _sprint_metrics_var.set(new_metrics)

    This ensures:
    - Task isolation: each TaskGroup child gets consistent snapshot
    - No race conditions: atomic replacement instead of in-place mutation
    - GC efficiency: frozen msgspec.Struct has no __dict__, ~50 bytes/counter

    BC COMPATIBILITY: .copy() returns a dict with all field values for code
    that depends on mutable access patterns.

    COUNTER FIELDS (all int, atomic via copy-on-write):
        cycles_started, cycles_completed, consecutive_empty_cycles,
        max_consecutive_empty_cycles, unique_entry_hashes_seen,
        duplicate_entry_hashes_skipped, hard_deadline_checked_count,
        windup_guard_call_count, windup_guard_callback_supplied_count,
        windup_guard_callback_executed_count, policy_quality_feedback_calls,
        policy_quality_feedback_errors, ipfs_cids_attempted,
        multimodal_enriched_findings, feed_suppression_count,
        forensics_enriched_ct_findings, acquisition_lanes_skipped

    ACCUMULATOR FIELDS (dict, copy-on-write):
        entries_per_source, hits_per_source, entries_seen, entries_scanned,
        entries_with_hits, findings_built_pre_store

    META FIELDS:
        signal_stage, accepted_findings, final_phase, aborted, abort_reason,
        stop_requested, pre_loop_elapsed_s, pre_active_starved

    M1 8GB: frozen=True + gc=False = optimal for high-frequency counter updates.
    """

    # Counter fields (int)
    cycles_started: int = 0
    cycles_completed: int = 0
    consecutive_empty_cycles: int = 0
    max_consecutive_empty_cycles: int = 0
    unique_entry_hashes_seen: int = 0
    duplicate_entry_hashes_skipped: int = 0
    total_pattern_hits: int = 0
    entries_seen: int = 0
    entries_scanned: int = 0
    entries_with_hits: int = 0
    findings_built_pre_store: int = 0
    accepted_findings: int = 0
    hard_deadline_checked_count: int = 0
    windup_guard_call_count: int = 0
    windup_guard_callback_supplied_count: int = 0
    windup_guard_callback_executed_count: int = 0
    policy_quality_feedback_calls: int = 0
    policy_quality_feedback_errors: int = 0
    ipfs_cids_attempted: int = 0
    multimodal_enriched_findings: int = 0
    feed_suppression_count: int = 0
    forensics_enriched_ct_findings: int = 0
    acquisition_lanes_skipped: int = 0

    # Dict fields (copy-on-write)
    entries_per_source: dict[str, int] = msgspec.field(default_factory=dict)
    hits_per_source: dict[str, int] = msgspec.field(default_factory=dict)

    # String fields
    signal_stage: str = "unknown"
    final_phase: str = ""
    aborted: bool = False
    abort_reason: str = ""
    stop_requested: bool = False
    pre_loop_elapsed_s: float = 0.0
    pre_active_starved: bool = False
    entered_active_at_monotonic: float = 0.0
    first_cycle_started_at_monotonic: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """
        BC compatibility: return dict with all field values.

        Allows code that depends on mutable access patterns to continue working:
            metrics_dict = sprint_metrics.to_dict()
            metrics_dict["cycles_started"] += 1
        """
        return {
            "cycles_started": self.cycles_started,
            "cycles_completed": self.cycles_completed,
            "consecutive_empty_cycles": self.consecutive_empty_cycles,
            "max_consecutive_empty_cycles": self.max_consecutive_empty_cycles,
            "unique_entry_hashes_seen": self.unique_entry_hashes_seen,
            "duplicate_entry_hashes_skipped": self.duplicate_entry_hashes_skipped,
            "total_pattern_hits": self.total_pattern_hits,
            "entries_seen": self.entries_seen,
            "entries_scanned": self.entries_scanned,
            "entries_with_hits": self.entries_with_hits,
            "findings_built_pre_store": self.findings_built_pre_store,
            "accepted_findings": self.accepted_findings,
            "hard_deadline_checked_count": self.hard_deadline_checked_count,
            "windup_guard_call_count": self.windup_guard_call_count,
            "windup_guard_callback_supplied_count": self.windup_guard_callback_supplied_count,
            "windup_guard_callback_executed_count": self.windup_guard_callback_executed_count,
            "policy_quality_feedback_calls": self.policy_quality_feedback_calls,
            "policy_quality_feedback_errors": self.policy_quality_feedback_errors,
            "ipfs_cids_attempted": self.ipfs_cids_attempted,
            "multimodal_enriched_findings": self.multimodal_enriched_findings,
            "feed_suppression_count": self.feed_suppression_count,
            "forensics_enriched_ct_findings": self.forensics_enriched_ct_findings,
            "acquisition_lanes_skipped": self.acquisition_lanes_skipped,
            "entries_per_source": dict(self.entries_per_source),
            "hits_per_source": dict(self.hits_per_source),
            "signal_stage": self.signal_stage,
            "final_phase": self.final_phase,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
            "stop_requested": self.stop_requested,
            "pre_loop_elapsed_s": self.pre_loop_elapsed_s,
            "pre_active_starved": self.pre_active_starved,
            "entered_active_at_monotonic": self.entered_active_at_monotonic,
            "first_cycle_started_at_monotonic": self.first_cycle_started_at_monotonic,
        }


# SprintMetrics ContextVar — per-sprint copy-on-write state
_sprint_metrics_var: contextvars.ContextVar[SprintMetrics] = contextvars.ContextVar(
    "sprint_metrics", default=SprintMetrics()
    )


def get_sprint_metrics() -> SprintMetrics:
    """Get current SprintMetrics snapshot."""
    return _sprint_metrics_var.get()


def set_sprint_metrics(metrics: SprintMetrics) -> None:
    """
    Commit new SprintMetrics (copy-on-write).

    Called after building a new metrics instance with updated counters.
    This is the ONLY write path for SprintMetrics.
    """
    _sprint_metrics_var.set(metrics)


def reset_sprint_metrics() -> None:
    """Reset to empty metrics (call between sprints)."""
    _sprint_metrics_var.set(SprintMetrics())


# ─────────────────────────────────────────────────────────────────────────────
# SprintRunSnapshot — Immutable Per-Sprint Snapshot
# ─────────────────────────────────────────────────────────────────────────────


class SprintRunSnapshot(msgspec.Struct, frozen=True, gc=False):
    """
    Immutable snapshot of all sprint state at a point in time.

    ISSUE #22: Per-sprint snapshot + commit pattern for immutable state machine.

    Created at sprint start, updated at phase transitions, committed at sprint end.
    All fields are frozen — updates create new snapshots via .evolve().

    This replaces scattered mutable state in sprint_scheduler.py with a
    centralized, immutable, ContextVar-backed state machine.

    ROLES:
        1. Sprint start: create initial snapshot with all state fields
        2. Phase transitions: evolve snapshot with new phase/flags
        3. Sprint end: commit final snapshot for post-processing

    EVOLVE PATTERN:
        new_snapshot = current_snapshot.evolve(
            phase="ACTIVE",
            metrics=current_metrics.copy()
    )
        _sprint_snapshot_var.set(new_snapshot)

    M1 8GB: frozen=True + gc=False = no __dict__, no GC overhead, ~100 bytes/snapshot.
    """

    # Identity
    sprint_id: str = ""
    query: str = ""

    # Phase state
    phase: str = "BOOT"
    phase_entered_at: float = 0.0

    # Lifecycle flags
    windup_complete: bool = False
    first_cycle_ran: bool = False
    deadline_expired_pre_cycle: bool = False
    abort_requested: bool = False
    abort_reason: str = ""

    # Resource snapshots
    uma_state: str = "unknown"
    uma_pressure: float = 0.0

    # Metrics reference (SprintMetrics already frozen)
    metrics: SprintMetrics | None = None

    def evolve(self, **kwargs: Any) -> "SprintRunSnapshot":
        """
        Return NEW snapshot with updated fields (copy-on-write).

        This is the ONLY way to update SprintRunSnapshot — no in-place mutation.
        """
        current = {
            "sprint_id": self.sprint_id,
            "query": self.query,
            "phase": self.phase,
            "phase_entered_at": self.phase_entered_at,
            "windup_complete": self.windup_complete,
            "first_cycle_ran": self.first_cycle_ran,
            "deadline_expired_pre_cycle": self.deadline_expired_pre_cycle,
            "abort_requested": self.abort_requested,
            "abort_reason": self.abort_reason,
            "uma_state": self.uma_state,
            "uma_pressure": self.uma_pressure,
            "metrics": self.metrics,
        }
        current.update(kwargs)
        return SprintRunSnapshot(**current)


# SprintRunSnapshot ContextVar — per-sprint immutable state machine
_sprint_snapshot_var: contextvars.ContextVar[SprintRunSnapshot] = contextvars.ContextVar(
    "sprint_snapshot", default=SprintRunSnapshot()
    )


def get_sprint_snapshot() -> SprintRunSnapshot:
    """Get current SprintRunSnapshot."""
    return _sprint_snapshot_var.get()


def set_sprint_snapshot(snapshot: SprintRunSnapshot) -> None:
    """
    Commit new SprintRunSnapshot (immutable state machine transition).

    Called at phase transitions and sprint lifecycle events.
    """
    _sprint_snapshot_var.set(snapshot)


def reset_sprint_snapshot() -> None:
    """Reset to empty snapshot (call between sprints)."""
    _sprint_snapshot_var.set(SprintRunSnapshot())
