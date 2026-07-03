"""
runtime/state — Canonical Runtime & Research State

Single source of truth for global runtime flags and sprint-start snapshots.

CLASSES:
    RuntimeState(msgspec.Struct, frozen=True):
        Canonical uvloop state (set once at boot).
        Consumed by session_runtime for get_session_runtime_status()["uvloop_enabled"].

    ResearchContextSnapshot(msgspec.Struct, frozen=True):
        Immutable snapshot of ResearchContext at sprint start.
        Carries metadata for EvidenceLog correlation handoff.

MODULE INVARIANTS:
    [1] RuntimeState.uvloop_installed is set ONCE at boot via mark_uvloop_installed()
    [2] ResearchContextSnapshot is frozen after creation — no side effects
    [3] Both classes use msgspec.Struct (frozen=True, gc=False) for M1 RAM efficiency

INTENT:
    One runtime/state.py with RuntimeState (msgspec.Struct, frozen) and
    ResearchContext (msgspec.Struct, frozen). The latter is a snapshot of
    the active ResearchContext at sprint start.
"""
from __future__ import annotations


import msgspec

# ─────────────────────────────────────────────────────────────────────────────
# RuntimeState — Canonical Runtime Flags
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

    M1 8GB: frozen=True + gc=False = no __dict__, no GC overhead, ~50 bytes/instance.
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

    M1 8GB: frozen=True + gc=False = no __dict__, no GC overhead.
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
