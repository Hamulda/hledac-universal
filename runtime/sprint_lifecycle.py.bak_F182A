"""
SprintLifecycleManager — canonical sprint state machine.

Phases: BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN

Hard invariant: T-3min wind-down.
All timing uses time.monotonic().
No async. No threads. No I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ── Phase enum ───────────────────────────────────────────────────────────────

class SprintPhase(Enum):
    BOOT = auto()
    WARMUP = auto()
    ACTIVE = auto()
    WINDUP = auto()
    EXPORT = auto()
    TEARDOWN = auto()


# ── Exceptions ────────────────────────────────────────────────────────────────

class SprintLifecycleError(Exception):
    """Base exception for sprint lifecycle errors."""


class InvalidPhaseTransitionError(SprintLifecycleError):
    """Raised when a non-monotonic phase transition is attempted."""


# ── Phase ordering ────────────────────────────────────────────────────────────

_PHASE_ORDER = [
    SprintPhase.BOOT,
    SprintPhase.WARMUP,
    SprintPhase.ACTIVE,
    SprintPhase.WINDUP,
    SprintPhase.EXPORT,
    SprintPhase.TEARDOWN,
]


# ── Manager ──────────────────────────────────────────────────────────────────

@dataclass
class SprintLifecycleManager:
    """
    Lightweight sprint lifecycle state machine.

    All methods accept an optional ``now_monotonic`` parameter to allow
    deterministic testing with a fake clock. When omitted the call uses
    ``time.monotonic()`` at runtime.
    """

    sprint_duration_s: float = 1800.0          # 30 minutes
    windup_lead_s: float = 180.0               # T-3min before trigger
    checkpoint_interval_s: float = 60.0          # lightweight checkpoint hint
    checkpoint_path: str = ""                    # metadata only — no I/O here

    # Mutable state
    _started_at: Optional[float] = field(default=None, repr=False)
    _current_phase: SprintPhase = field(default=SprintPhase.BOOT, repr=False)
    _entered_phase_at: Optional[float] = field(default=None, repr=False)
    _phase_history: dict = field(default_factory=dict, repr=False)  # phase→entered_at timestamp
    _export_started: bool = field(default=False, repr=False)
    _teardown_started: bool = field(default=False, repr=False)
    _abort_requested: bool = field(default=False, repr=False)
    _abort_reason: str = field(default="", repr=False)
    _last_checkpoint_at: Optional[float] = field(default=None, repr=False)

    # ── start ────────────────────────────────────────────────────────────────

    def start(self, now_monotonic: Optional[float] = None) -> None:
        """Transition from BOOT → WARMUP and record start time."""
        if self._started_at is not None:
            raise SprintLifecycleError("Sprint has already been started.")
        now = _now(now_monotonic)
        self._started_at = now
        self._transition_to_unlocked(SprintPhase.WARMUP, now)

    # ── transition_to ────────────────────────────────────────────────────────

    def transition_to(self, phase: SprintPhase, now_monotonic: Optional[float] = None) -> None:
        """Transition to the given phase if it respects monotonic ordering."""
        now = _now(now_monotonic)
        if self._started_at is None:
            raise SprintLifecycleError("Sprint has not been started. Call start() first.")
        if phase == SprintPhase.TEARDOWN and self._abort_requested:
            # Abort shortcut: TEARDOWN is always reachable from any phase when abort requested
            self._transition_to_unlocked(phase, now)
            return
        if not self._is_valid_transition(self._current_phase, phase):
            raise InvalidPhaseTransitionError(
                f"Cannot transition from {self._current_phase.name} to {phase.name}. "
                f"Phases must advance monotonically."
            )
        self._transition_to_unlocked(phase, now)

    # ── tick ─────────────────────────────────────────────────────────────────

    def tick(self, now_monotonic: Optional[float] = None) -> SprintPhase:
        """
        Advance the state machine.

        Automatically enters WINDUP when remaining_time <= windup_lead_s.
        Returns the current phase after ticking.
        """
        now = _now(now_monotonic)

        # Auto WINDUP guard — only if we are in ACTIVE and time has run down
        if self._current_phase == SprintPhase.ACTIVE:
            remaining = self._remaining_time_unlocked(now)
            if remaining <= self.windup_lead_s:
                self._transition_to_unlocked(SprintPhase.WINDUP, now)

        return self._current_phase

    # ── remaining_time ───────────────────────────────────────────────────────

    def remaining_time(self, now_monotonic: Optional[float] = None) -> float:
        """Seconds remaining in the sprint (0 if elapsed)."""
        now = _now(now_monotonic)
        return self._remaining_time_unlocked(now)

    def _remaining_time_unlocked(self, now: float) -> float:
        if self._started_at is None:
            return self.sprint_duration_s
        return max(0.0, self._started_at + self.sprint_duration_s - now)

    # ── should_enter_windup ───────────────────────────────────────────────────

    def should_enter_windup(self, now_monotonic: Optional[float] = None) -> bool:
        """True when remaining time is at or below the windup lead threshold."""
        now = _now(now_monotonic)
        remaining = self._remaining_time_unlocked(now)
        return remaining <= self.windup_lead_s

    # ── request_abort ───────────────────────────────────────────────────────

    def request_abort(self, reason: str = "") -> None:
        """
        Signal that the sprint should abort.

        Does NOT add a new phase — abort flags are tracked separately.
        The manager can transition directly to TEARDOWN via transition_to.
        """
        self._abort_requested = True
        self._abort_reason = reason

    # ── mark_export_started / mark_teardown_started ─────────────────────────

    def mark_export_started(self, now_monotonic: Optional[float] = None) -> None:
        now = _now(now_monotonic)
        if self._current_phase != SprintPhase.WINDUP:
            raise InvalidPhaseTransitionError(
                f"EXPORT may only follow WINDUP, not {self._current_phase.name}."
            )
        self._export_started = True
        self._transition_to_unlocked(SprintPhase.EXPORT, now)

    def mark_teardown_started(self, now_monotonic: Optional[float] = None) -> None:
        now = _now(now_monotonic)
        if self._current_phase not in (SprintPhase.EXPORT, SprintPhase.WINDUP):
            raise InvalidPhaseTransitionError(
                f"TEARDOWN may only follow EXPORT or WINDUP (abort), "
                f"not {self._current_phase.name}."
            )
        self._teardown_started = True
        self._transition_to_unlocked(SprintPhase.TEARDOWN, now)

    # ── snapshot ────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """
        Return a JSON-serializable dict representing the current state.

        DIAGNOSTIC ONLY — this is a read-only snapshot for monitoring,
        not a second authority. The authoritative state is the live
        _current_phase field and current_phase property.

        No Path objects, no open handles — recovery-safe.
        """
        return {
            "sprint_duration_s": self.sprint_duration_s,
            "windup_lead_s": self.windup_lead_s,
            "checkpoint_interval_s": self.checkpoint_interval_s,
            "checkpoint_path": self.checkpoint_path,
            "started_at_monotonic": self._started_at,
            "current_phase": self._current_phase.name,
            "entered_phase_at": self._entered_phase_at,
            "phase_history": {ph.name: ts for ph, ts in self._phase_history.items()},
            "export_started": self._export_started,
            "teardown_started": self._teardown_started,
            "abort_requested": self._abort_requested,
            "abort_reason": self._abort_reason,
            "last_checkpoint_at": self._last_checkpoint_at,
        }

    # ── recommended_tool_mode ────────────────────────────────────────────────

    def recommended_tool_mode(
        self,
        now_monotonic: Optional[float] = None,
        thermal_state: str = "nominal",
    ) -> str:
        """
        Returns one of: 'normal' | 'prune' | 'panic'.

        Decision tree:
        - panic : abort requested OR remaining <= 30s OR thermal == 'critical'
        - prune : remaining <= windup_lead_s OR thermal in ('throttled', 'fair')
        - normal: everything else
        """
        now = _now(now_monotonic)
        remaining = self._remaining_time_unlocked(now)

        if self._abort_requested or remaining <= 30.0 or thermal_state == "critical":
            return "panic"
        if remaining <= self.windup_lead_s or thermal_state in ("throttled", "fair"):
            return "prune"
        return "normal"

    # ── is_terminal ──────────────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        """True when the manager has reached TEARDOWN or has aborted and completed."""
        if self._current_phase == SprintPhase.TEARDOWN:
            return True
        if self._abort_requested and self._teardown_started:
            return True
        return False

    # =============================================================================
    # Sprint F4: COMPAT ALIASES — sealed with metadata
    # Each alias carries: future_owner, caller_class, removal_condition, why_still_needed.
    # These forward to the canonical API. Labeled as COMPAT so they are clearly
    # NOT co-equal public API — they exist to make __main__.py cutover safe.
    # Sprint F4: All alias metadata sealed — no new co-equal authority created.
    # =============================================================================

    # ── COMPAT: begin_sprint ─────────────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: __main__.py
    # caller_class: __main__.py (line ~2420), legacy autonomous_orchestrator (line ~11723)
    # removal_condition: All call-sites migrated to .start() — requires isolated sprint for validation
    # why_still_needed: 2 active call-sites across 2 production modules; cutover deferred to avoid behavior risk

    def begin_sprint(self) -> None:
        """
        COMPAT ALIAS — forwards to start().

        Canonical: use start() directly.
        NOTE: start() transitions BOOT→WARMUP only (not to ACTIVE).
        Full activation requires: start() then mark_warmup_done() or transition_to(ACTIVE).
        This alias exists to support __main__.py cutover without rewriting call-sites.

        F4 metadata:
          future_owner: __main__.py
          removal_condition: All call-sites migrated to .start()
        """
        self.start()

    # ── COMPAT: mark_warmup_done ────────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: __main__.py
    # caller_class: __main__.py (line ~2904)
    # removal_condition: __main__.py uses transition_to(ACTIVE) directly — WARMUP→ACTIVE handled by start()
    # why_still_needed: 1 call-site in __main__.py; start() already transitions to WARMUP, so this is redundant but still wired

    def mark_warmup_done(self) -> None:
        """
        COMPAT ALIAS — transitions WARMUP→ACTIVE.

        Canonical: use transition_to(SprintPhase.ACTIVE) directly.
        NOTE: start() goes BOOT→WARMUP only. WARMUP→ACTIVE requires this alias
        or explicit transition_to(ACTIVE). __main__.py uses this alias directly.

        F4 metadata:
          future_owner: __main__.py
          removal_condition: __main__.py uses transition_to(ACTIVE) directly; or start() gains WARMUP→ACTIVE
        """
        self.transition_to(SprintPhase.ACTIVE)

    # ── COMPAT: request_windup ──────────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: __main__.py
    # caller_class: __main__.py (lines ~2502, ~2526)
    # removal_condition: All call-sites migrated to transition_to(SprintPhase.WINDUP) — requires isolated sprint
    # why_still_needed: 2 production call-sites; idempotent behavior is same as transition_to

    def request_windup(self) -> None:
        """
        COMPAT ALIAS — forwards to transition_to(WINDUP).

        Canonical: use transition_to(SprintPhase.WINDUP).
        Idempotent: skips if already in WINDUP or beyond (matching utils behavior).

        F4 metadata:
          future_owner: __main__.py
          removal_condition: All call-sites use transition_to(WINDUP)
        """
        # Idempotent: don't re-trigger if already winding down
        if self._current_phase in (
            SprintPhase.WINDUP,
            SprintPhase.EXPORT,
            SprintPhase.TEARDOWN,
        ):
            return
        self.transition_to(SprintPhase.WINDUP)

    # ── COMPAT: request_export ───────────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: __main__.py
    # caller_class: __main__.py (lines ~2604, ~2617), legacy autonomous_orchestrator (line ~12357)
    # removal_condition: All call-sites migrated to mark_export_started()
    # why_still_needed: 2 call-sites in __main__.py + 1 in legacy AO; idempotent same as mark_export_started

    def request_export(self) -> None:
        """
        COMPAT ALIAS — forwards to mark_export_started().

        Canonical: use mark_export_started() directly.
        Idempotent: skips if already in EXPORT or TEARDOWN (matching utils behavior).

        F4 metadata:
          future_owner: __main__.py
          removal_condition: All call-sites use mark_export_started()
        """
        if self._current_phase in (SprintPhase.EXPORT, SprintPhase.TEARDOWN):
            return
        self.mark_export_started()

    # ── COMPAT: request_teardown ─────────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: __main__.py
    # caller_class: __main__.py (none currently — wired but not called), legacy autonomous_orchestrator (line ~12690)
    # removal_condition: All call-sites migrated to mark_teardown_started()
    # why_still_needed: 1 call-site in legacy AO; __main__.py wires but does not call this method

    def request_teardown(self) -> None:
        """
        COMPAT ALIAS — forwards to mark_teardown_started().

        Canonical: use mark_teardown_started() directly.
        Idempotent: skips if already in TEARDOWN (matching request_export/request_windup).

        F4 metadata:
          future_owner: __main__.py
          removal_condition: All call-sites use mark_teardown_started()
        """
        if self._current_phase == SprintPhase.TEARDOWN:
            return
        self.mark_teardown_started()

    # ── COMPAT: is_windup_phase ─────────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: synthesis_runner.py
    # caller_class: synthesis_runner (Path 3 compat fallback, line ~881)
    # removal_condition: synthesis_runner fully migrates to runtime path — requires windup gate injection verified
    # why_still_needed: synthesis_runner Path 3 (compat fallback) is still active; runtime path preferred but compat still reachable

    def is_windup_phase(self) -> bool:
        """
        COMPAT ALIAS — forwards to should_enter_windup().

        Canonical: use should_enter_windup() directly.

        NOTE: This is a time-based heuristic (remaining <= windup_lead_s),
        NOT a phase-state check. Use in_phase(SprintPhase.WINDUP) for phase-state.

        DIAGNOSTIC ONLY — for read-only shadow paths only.

        F4 metadata:
          future_owner: synthesis_runner.py
          removal_condition: synthesis_runner uses should_enter_windup() from runtime path
        """
        return self.should_enter_windup()

    # ── COMPAT: is_active property ───────────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: callers (runtime shadow_* modules)
    # caller_class: runtime shadow_pre_decision (line ~1276), shadow_pre_decision tests
    # removal_condition: All callers use in_phase(SprintPhase.ACTIVE) — low urgency, used in shadow/readonly paths
    # why_still_needed: Read-only property used by shadow modules; low risk to keep

    @property
    def is_active(self) -> bool:
        """
        COMPAT PROPERTY — True when in ACTIVE phase.

        Canonical: use in_phase(SprintPhase.ACTIVE) or current_phase == SprintPhase.ACTIVE.

        DIAGNOSTIC ONLY — this property is intended for read-only shadow paths.
        Do NOT use for runtime dispatch or path decisions.

        F4 metadata:
          future_owner: callers (shadow_* modules)
          removal_condition: Callers use in_phase(SprintPhase.ACTIVE)
        """
        return self._current_phase == SprintPhase.ACTIVE

    # ── COMPAT: is_winding_down property ─────────────────────────────────────
    # Sprint F4: Metadata sealed.
    # future_owner: callers (runtime shadow_* modules)
    # caller_class: runtime shadow_pre_decision (line ~1276)
    # removal_condition: All callers use in_phase() checks — low urgency, used in shadow/readonly paths
    # why_still_needed: Read-only property used by shadow modules; low risk to keep

    @property
    def is_winding_down(self) -> bool:
        """
        COMPAT PROPERTY — True when in WINDUP, EXPORT, or TEARDOWN.

        Canonical: use in_phase(SprintPhase.WINDUP) or current_phase in (WINDUP, EXPORT, TEARDOWN).

        DIAGNOSTIC ONLY — this property is intended for read-only shadow paths.
        Do NOT use for runtime dispatch or path decisions.

        F4 metadata:
          future_owner: callers (shadow_* modules)
          removal_condition: Callers use in_phase() checks
        """
        return self._current_phase in (
            SprintPhase.WINDUP,
            SprintPhase.EXPORT,
            SprintPhase.TEARDOWN,
        )

    # ── Public read-only surface ─────────────────────────────────────────────

    @property
    def current_phase(self) -> SprintPhase:
        """
        Public read-only access to current phase.

        Canonical alternative to direct _current_phase field access.
        """
        return self._current_phase

    def in_phase(self, phase: SprintPhase) -> bool:
        """
        True when manager is in the given phase.

        Convenience helper — equivalent to current_phase == phase.
        """
        return self._current_phase == phase

    # ── Read-only phase-entry seams (F166D) ─────────────────────────────────

    def has_reached_phase(self, phase: SprintPhase) -> bool:
        """
        True when the given phase has ever been entered (including current).

        DIAGNOSTIC ONLY — read-only seam for observability.
        Does NOT mutate state. Does not check ordering.
        """
        return phase in self._phase_history

    def entered_phase_at(self, phase: SprintPhase) -> Optional[float]:
        """
        Monotonic timestamp when the given phase was first entered.

        Returns None if the phase has never been reached.

        DIAGNOSTIC ONLY — read-only seam for observability.
        """
        return self._phase_history.get(phase)

    def phase_durations_so_far(
        self,
        now_monotonic: Optional[float] = None,
    ) -> dict[str, Optional[float]]:
        """
        Seconds each phase has been active so far.

        Returns a dict mapping phase name → duration in seconds (or None if
        the phase has never been reached, or is currently active).

        For the current phase, duration is computed as:
            now - entered_phase_at(current)

        DIAGNOSTIC ONLY — read-only seam for observability.
        """
        now = _now(now_monotonic)
        result: dict[str, Optional[float]] = {}
        for ph in _PHASE_ORDER:
            entered = self._phase_history.get(ph)
            if entered is None:
                result[ph.name] = None
            elif ph == self._current_phase:
                result[ph.name] = now - entered
            else:
                # Phase was exited — duration already recorded.
                # We don't track exit times, so return None (observability only).
                result[ph.name] = None
        return result

    # ── Private helpers ─────────────────────────────────────────────────────

    def _transition_to_unlocked(self, phase: SprintPhase, now: Optional[float] = None) -> None:
        if now is None:
            now = _now(None)
        self._current_phase = phase
        self._entered_phase_at = now
        self._phase_history[phase] = now

    def _is_valid_transition(self, from_phase: SprintPhase, to_phase: SprintPhase) -> bool:
        """Allow TEARDOWN from any phase (abort path)."""
        if to_phase == SprintPhase.TEARDOWN:
            return True
        from_idx = _PHASE_ORDER.index(from_phase)
        to_idx = _PHASE_ORDER.index(to_phase)
        return to_idx == from_idx + 1


# ── Clock helper ─────────────────────────────────────────────────────────────

def _now(m: Optional[float]) -> float:
    if m is not None:
        return m
    return time.monotonic()
