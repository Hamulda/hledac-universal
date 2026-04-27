"""
SprintLifecycleRunner — lifecycle orchestration helper extracted from SprintScheduler.

Responsibilities (what this runner OWNS):
- LifecycleAdapter creation and lifecycle start
- WARMUP→ACTIVE transition
- Periodic tick() call
- Wind-down guard (should_enter_windup check)
- Post-sleep windup gate
- Sleep with lifecycle tick (sleep_or_abort)
- Final phase teardown transitions
- Partial export trigger signal

What stays in SprintScheduler (canonical owner):
- Branch execution (_run_one_cycle)
- Sidecar dispatch
- Advisory evaluation
- Export execution
- Dedup/forensics flush (called by runner before windup break)
- All result bookkeeping

No new behavior. No intelligence. Pure mechanical extraction.
GHOST_INVARIANTS: gather(return_exceptions=True) + _check_gathered(), no asyncio.run().
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from typing import Any, Optional

__all__ = ["SprintLifecycleRunner"]

log = logging.getLogger(__name__)


class SprintLifecycleRunner:
    """
    Lifecycle orchestration helper for SprintScheduler.

    Encapsulates lifecycle adapter, tick/windup/sleep/teardown logic.
    Scheduler remains canonical owner for branches, sidecars, advisory, export.
    """

    __slots__ = ("_lc", "_adapter", "_wall_clock_start")

    def __init__(self, lifecycle: Any, adapter: Any) -> None:
        self._lc = lifecycle
        self._adapter = adapter
        self._wall_clock_start: Optional[float] = None

    # ── Setup ────────────────────────────────────────────────────────────────

    def setup(self) -> None:
        """
        Start lifecycle via adapter (BOOT→WARMUP).
        Called once at sprint start before the main loop.
        """
        self._adapter.start()
        self._wall_clock_start = _time.monotonic()

    # ── Lifecycle tick ───────────────────────────────────────────────────────

    def tick(self, now_monotonic: Optional[float] = None) -> Any:
        """
        Advance the lifecycle phase machine.
        Returns the current phase after ticking.
        """
        return self._adapter.tick(now_monotonic)

    # ── WARMUP → ACTIVE ─────────────────────────────────────────────────────

    def ensure_active(self, now_monotonic: Optional[float] = None) -> None:
        """
        If lifecycle is in WARMUP, transition to ACTIVE.
        Handles the WARMUP→ACTIVE transition that follows initial setup.
        """
        phase = self._adapter.tick(now_monotonic)
        phase_str = str(phase)
        if phase_str == "SprintPhase.WARMUP" or phase_str.endswith(".WARMUP"):
            try:
                self._adapter.mark_warmup_done()
            except Exception:
                pass  # best-effort

    # ── Wind-down guard ─────────────────────────────────────────────────────

    def windup_guard(
        self,
        now_monotonic: Optional[float] = None,
    ) -> bool:
        """
        Check if lifecycle should enter wind-down.

        Returns True if the scheduler should break out of the main loop
        to begin teardown. Returns False to continue the work loop.

        Call this BEFORE scheduler-specific pre-windup operations
        (flush dedup, flush forensics, advisory gate, partial export).
        """
        if self._adapter.should_enter_windup(now_monotonic):
            return True
        return False

    # ── Post-sleep windup gate ──────────────────────────────────────────────

    def post_sleep_gate(self, now_monotonic: Optional[float] = None) -> bool:
        """
        Check if lifecycle should enter wind-down after a sleep interval.

        Returns True if the scheduler should break out of the main loop.
        Called immediately after sleep_or_abort() returns.
        """
        if self._adapter.should_enter_windup(now_monotonic):
            log.debug("[SprintLifecycleRunner] Windup requested after sleep — exiting.")
            return True
        return False

    # ── Abort ────────────────────────────────────────────────────────────────

    def abort(self, reason: str) -> None:
        """Signal abort on the lifecycle."""
        self._adapter.request_abort(reason)

    # ── Abort check ─────────────────────────────────────────────────────────

    @property
    def abort_requested(self) -> bool:
        """True if the lifecycle has been asked to abort."""
        return self._adapter._abort_requested

    @property
    def abort_reason(self) -> str:
        """Reason for abort, if any."""
        return self._adapter._abort_reason or ""

    # ── Terminal check ───────────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        """True when the lifecycle has reached a terminal phase."""
        return self._adapter.is_terminal()

    # ── Sleep with lifecycle tick ──────────────────────────────────────────

    async def sleep_or_abort(self, seconds: float) -> None:
        """
        Sleep in short chunks so wind-down can be detected promptly.
        Calls adapter.tick() during sleep to advance the phase machine.
        """
        elapsed = 0.0
        step = min(seconds, 1.0)
        while elapsed < seconds:
            await asyncio.sleep(step)
            elapsed += step
            self._adapter.tick()
            if self._adapter._abort_requested or self._adapter.is_terminal():
                return

    # ── Teardown / final phase ──────────────────────────────────────────────

    def teardown(self) -> None:
        """
        Handle final phase transitions for teardown.

        If in WINDUP → EXPORT → TEARDOWN.
        If in ACTIVE/WARMUP → TEARDOWN (abort).
        """
        try:
            from hledac.universal.runtime.sprint_lifecycle import SprintPhase
            phase = self._lc.current_phase
            if phase == SprintPhase.WINDUP:
                self._lc.mark_export_started()
                self._lc.mark_teardown_started()
            elif phase not in (SprintPhase.EXPORT, SprintPhase.TEARDOWN):
                self._lc.request_abort("scheduler_final_phase")
                self._lc.mark_teardown_started()
        except Exception:
            pass  # teardown is best-effort

    # ── Partial export signal ───────────────────────────────────────────────

    @property
    def current_phase(self) -> str:
        """Current phase as string for scheduler callbacks."""
        return self._adapter._current_phase

    # ── Wall clock ───────────────────────────────────────────────────────────

    @property
    def wall_clock_start(self) -> Optional[float]:
        return self._wall_clock_start
