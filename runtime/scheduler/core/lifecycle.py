"""
SprintLifecycleAdapter — Normalizes lifecycle API differences between runtime/ and utils/ versions.
================================================================================================


Extracted from runtime/sprint_scheduler.py (Phase 1 of modular decomposition).
Lines: 1071-1374 in original.

runtime/sprint_lifecycle: start(), tick(), remaining_time(),
    is_terminal(), should_enter_windup(), _current_phase,
    recommended_tool_mode(), request_abort(), _abort_requested

Adapter ensures begin_sprint() on any lifecycle object maps to start()
for runtime objects, and bridges property vs method access patterns.
"""



from typing import TYPE_CHECKING, Any
from core import aclose

if TYPE_CHECKING:
    pass


class SprintLifecycleAdapter:
    """
    Normalizes lifecycle API differences between runtime/ and utils/ versions.

    runtime/sprint_lifecycle: start(), tick(), remaining_time(),
        is_terminal(), should_enter_windup(), _current_phase,
        recommended_tool_mode(), request_abort(), _abort_requested

    Adapter ensures begin_sprint() on any lifecycle object maps to start()
    for runtime objects, and bridges property vs method access patterns.
    """

    __slots__ = ("_lc",)

    def __init__(self, lifecycle: Any) -> None:
        self._lc = lifecycle

    # ── start / begin_sprint ───────────────────────────────────────────────

    def start(self) -> None:
        """runtime: start() -- transitions BOOT->WARMUP."""
        lc = self._lc
        if hasattr(lc, "start"):
            lc.start()
        elif hasattr(lc, "begin_sprint"):
            lc.begin_sprint()

    # ── tick ──────────────────────────────────────────────────────────────

    def tick(self, now_monotonic: float | None = None) -> Any:
        """runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string."""
        lc = self._lc
        if hasattr(lc, "tick"):
            return lc.tick(now_monotonic)
        # Fallback: return phase-like 'UNKNOWN' string, not float.
        # Callers compare phase != _current_phase -- requires str.
        return "UNKNOWN"

    # ── remaining_time ───────────────────────────────────────────────────

    def remaining_time(self, now_monotonic: float | None = None) -> float:
        """runtime: remaining_time(). utils: remaining_time property."""
        lc = self._lc
        if hasattr(lc, "remaining_time"):
            val = lc.remaining_time
            return float(val() if callable(val) else val)
        return 0.0

    # ── is_terminal ──────────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        """runtime: is_terminal(). Returns True when phase is TEARDOWN."""
        lc = self._lc
        if hasattr(lc, "is_terminal"):
            val = lc.is_terminal
            return bool(val() if callable(val) else val)
        # Fallback: check phase name
        phase = self._current_phase
        return phase == "TEARDOWN"

    # ── should_enter_windup ──────────────────────────────────────────────

    def should_enter_windup(self, now_monotonic: float | None = None) -> bool:
        """runtime: should_enter_windup(). utils: is_windup_phase()."""
        lc = self._lc
        if hasattr(lc, "should_enter_windup"):
            val = lc.should_enter_windup
            return bool(val(now_monotonic) if callable(val) else val)
        if hasattr(lc, "is_windup_phase"):
            val = lc.is_windup_phase
            return bool(val() if callable(val) else val)
        return False

    # ── pre_loop_cost_s ──────────────────────────────────────────────────

    def set_pre_loop_cost_s(self, value: float) -> None:
        """F288: Set pre_loop_cost_s on the underlying lifecycle if supported."""
        lc = self._lc
        if hasattr(lc, "pre_loop_cost_s"):
            lc.pre_loop_cost_s = value

    def set_first_cycle_ran(self) -> None:
        """F290: Signal that first acquisition cycle has completed."""
        lc = self._lc
        if hasattr(lc, "first_cycle_ran"):
            lc.first_cycle_ran = True

    def set_deadline_expired_pre_cycle(self) -> None:
        """
        F290-Deadline: Signal that hard deadline expired before first cycle.

        Called when _check_hard_deadline() detects expiry with cycles_started == 0.
        Allows windup for cleanup even though first_cycle_ran=False.
        """
        lc = self._lc
        if hasattr(lc, "set_deadline_expired_pre_cycle"):
            lc.set_deadline_expired_pre_cycle()

    # ── _current_phase ───────────────────────────────────────────────────

    @property
    def _current_phase(self) -> str:
        """runtime: _current_phase (SprintPhase enum). utils: state (SprintLifecycleState)."""
        lc = self._lc
        for attr in ("_current_phase", "phase", "state", "current_phase"):
            if hasattr(lc, attr):
                val = getattr(lc, attr)
                v = val() if callable(val) else val
                return str(v.name if hasattr(v, "name") else v)
        return "UNKNOWN"

    # ── mark_warmup_done ─────────────────────────────────────────────────

    def mark_warmup_done(self) -> None:
        """
        F184A: Canonical public API for WARMUP->ACTIVE transition.

        F184A: Replaces direct adapter._lc.mark_warmup_done() bypass in run().
        """
        lc = self._lc
        if hasattr(lc, "mark_warmup_done"):
            lc.mark_warmup_done()
        elif hasattr(lc, "transition_to"):
            from hledac.universal.runtime.sprint_lifecycle import SprintPhase
            lc.transition_to(SprintPhase.ACTIVE)

    # ── recommended_tool_mode ────────────────────────────────────────────

    def recommended_tool_mode(self, now_monotonic: float | None = None) -> str:
        """runtime: recommended_tool_mode(). Returns 'normal'/'prune'/'panic'."""
        lc = self._lc
        if hasattr(lc, "recommended_tool_mode"):
            val = lc.recommended_tool_mode
            return str(val(now_monotonic) if callable(val) else val)
        return "normal"

    # ── request_abort ────────────────────────────────────────────────────

    def request_abort(self, reason: str = "") -> None:
        """runtime: request_abort(reason)."""
        lc = self._lc
        if hasattr(lc, "request_abort"):
            lc.request_abort(reason)
        elif hasattr(lc, "_abort_requested"):
            lc._abort_requested = True
            if hasattr(lc, "_abort_reason"):
                lc._abort_reason = reason

    # ── _abort_requested ─────────────────────────────────────────────────

    @property
    def _abort_requested(self) -> bool:
        lc = self._lc
        if hasattr(lc, "_abort_requested"):
            val = lc._abort_requested
            return bool(val() if callable(val) else val)
        return False

    @property
    def _abort_reason(self) -> str:
        lc = self._lc
        if hasattr(lc, "_abort_reason"):
            val = lc._abort_reason
            return str(val() if callable(val) else val)
        return ""
