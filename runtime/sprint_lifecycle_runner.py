"""
SprintLifecycleRunner — mechanical lifecycle boundary extracted from SprintScheduler.

This runner is a PURE MECHANICAL SEAM. It does NOT own policy. It does NOT make
decisions. It translates lifecycle state into phase transitions and tick/sleep calls.

What this runner OWNS (mechanical only):
- LifecycleAdapter creation and lifecycle start
- WARMUP→ACTIVE transition
- Periodic tick() call
- Wind-down guard (should_enter_windup check)
- Post-sleep windup gate
- Sleep with lifecycle tick (sleep_or_abort)
- Final phase teardown transitions (phase markers only)

What stays in SprintScheduler (canonical policy owner):
- Branch execution (_run_one_cycle)
- Acquisition lane planning (AcquisitionLane, nonfeed lanes, etc.)
- Sidecar dispatch and orchestration
- Advisory evaluation
- Export execution (any export_* logic, DuckDB write seams, STIX, Markdown)
- Dedup/forensics flush
- All result bookkeeping, confidence scoring, hypothesis planning

PRE_WINDUP_BARRIER CALLBACK SEAM:
  pre_windup_barrier is the ONLY permitted scheduler callback seam into this runner.
  It is called from windup_guard() to allow the scheduler to inject lane-terminality
  checks or other policy before the windup break. No other callback seams exist.

INVARIANTS:
  - This module must NOT import: SprintScheduler, AcquisitionLane, any sidecar module,
    duckdb_store, export, or any module that owns policy beyond lifecycle mechanics.
  - pre_windup_barrier is the only external callback seam.
  - GHOST_INVARIANTS: gather(return_exceptions=True) + _check_gathered(), no asyncio.run().

No new behavior. No intelligence. Pure mechanical boundary extraction.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable
from typing import Any

__all__ = ["SprintLifecycleRunner"]

log = logging.getLogger(__name__)


class SprintLifecycleRunner:
    """
    Mechanical lifecycle boundary for SprintScheduler.

    Encapsulates lifecycle adapter, tick/windup/sleep/teardown mechanics.
    Scheduler (SprintScheduler) is the canonical policy owner — this runner
    only translates state into phase transitions. No intelligence, no policy.
    """

    __slots__ = ("_lc", "_adapter", "_wall_clock_start", "_pre_windup_barrier", "_guard_observation")

    def __init__(
        self,
        lifecycle: Any,
        adapter: Any,
        pre_windup_barrier: Callable[[], bool] | None = None,
    ) -> None:
        self._lc = lifecycle
        self._adapter = adapter
        self._wall_clock_start: float | None = None
        self._pre_windup_barrier: Callable[[], bool] | None = pre_windup_barrier
        self._guard_observation: dict = {}

    # ── Setup ────────────────────────────────────────────────────────────────

    def setup(self) -> None:
        """
        Start lifecycle via adapter (BOOT→WARMUP).
        Called once at sprint start before the main loop.
        """
        self._adapter.start()
        self._wall_clock_start = _time.monotonic()

    # ── Lifecycle tick ───────────────────────────────────────────────────────

    def tick(self, now_monotonic: float | None = None) -> Any:
        """
        Advance the lifecycle phase machine.
        Returns the current phase after ticking.
        """
        return self._adapter.tick(now_monotonic)

    @property
    def last_guard_observation(self) -> dict:
        """Return the last windup_guard() observation dict (sprint F207S-A)."""
        return self._guard_observation

    # ── WARMUP → ACTIVE ─────────────────────────────────────────────────────

    def ensure_active(self, now_monotonic: float | None = None) -> None:
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
        now_monotonic: float | None = None,
        pre_windup_barrier: Callable[[], bool] | None = None,
    ) -> bool:
        """
        Check if lifecycle should enter wind-down.

        Returns True if the scheduler should break out of the main loop
        to begin teardown. Returns False to continue the work loop.

        If a pre_windup_barrier callback is provided it is called to check
        whether required lanes (PUBLIC, CT) have reached terminal state.
        If the callback returns False the windup is blocked — returns False.
        If the callback raises or returns True the windup is allowed.

        Call this BEFORE scheduler-specific pre-windup operations
        (flush dedup, flush forensics, advisory gate, partial export).

        Sprint F207S-A: Writes observation to _guard_observation dict
        readable via last_guard_observation property.
        """
        phase = str(self._adapter._current_phase) if hasattr(self._adapter, "_current_phase") else "UNKNOWN"
        self._guard_observation = {
            "phase": phase,
            "should_enter_windup": False,
            "callback_supplied": False,
            "callback_executed": False,
            "barrier_ok": None,
            "reason": "not_windup_time",
            "allowed": False,
            "callback_not_executed_reason": "callback_not_executed_guard_not_reached",
        }

        # Sprint F208N-A: Always evaluate the prewindup barrier callback when provided,
        # even when should_enter_windup() is False. The callback checks lane terminality
        # and may dispatch bounded nonfeed lanes. Setting callback_supplied=True
        # regardless of should_enter_windup() ensures the scheduler telemetry accurately
        # reflects callback reachability.
        _callback = pre_windup_barrier if pre_windup_barrier is not None else self._pre_windup_barrier

        if not self._adapter.should_enter_windup(now_monotonic):
            # Sprint F208N-A: Even when windup is not yet triggered, call the callback
            # if provided so it can check/dispatch required lanes. Mark supplied=True.
            if _callback is not None:
                self._guard_observation["callback_supplied"] = True
                try:
                    barrier_ok = _callback()
                    self._guard_observation["callback_executed"] = True
                    self._guard_observation["callback_not_executed_reason"] = ""
                    self._guard_observation["barrier_ok"] = barrier_ok
                except Exception as exc:
                    self._guard_observation["callback_executed"] = True
                    self._guard_observation["callback_not_executed_reason"] = "callback_not_executed_exception"
                    self._guard_observation["barrier_ok"] = None
                    self._guard_observation["reason"] = f"callback_exception:{type(exc).__name__}"
                    log.debug(
                        "[SprintLifecycleRunner] prewindup barrier callback error (allowing windup): %s",
                        exc,
                    )
                    return False
            else:
                self._guard_observation["callback_supplied"] = False
                self._guard_observation["callback_not_executed_reason"] = "callback_not_executed_no_callback"
            self._guard_observation["reason"] = "not_windup_time"
            return False

        self._guard_observation["should_enter_windup"] = True
        self._guard_observation["reason"] = "allowed_by_adapter"

        # Check pre-windup barrier if provided
        if _callback is not None:
            self._guard_observation["callback_supplied"] = True
            try:
                barrier_ok = _callback()
                self._guard_observation["callback_executed"] = True
                self._guard_observation["callback_not_executed_reason"] = ""
                self._guard_observation["barrier_ok"] = barrier_ok
                if not barrier_ok:
                    self._guard_observation["reason"] = "barrier_blocked"
                    self._guard_observation["allowed"] = False
                    log.debug(
                        "[SprintLifecycleRunner] Windup blocked by pre-windup barrier"
                    )
                    return False
                else:
                    self._guard_observation["reason"] = "barrier_passed"
            except Exception as exc:
                self._guard_observation["callback_executed"] = True
                self._guard_observation["callback_not_executed_reason"] = "callback_not_executed_exception"
                self._guard_observation["barrier_ok"] = None
                self._guard_observation["reason"] = f"callback_exception:{type(exc).__name__}"
                # fail-soft: allow windup on callback error
                self._guard_observation["allowed"] = True
                return True
        else:
            # No callback available — skip execution, record explicit reason
            self._guard_observation["callback_supplied"] = False
            self._guard_observation["callback_executed"] = False
            self._guard_observation["callback_not_executed_reason"] = "callback_not_executed_no_callback"
            self._guard_observation["reason"] = "barrier_passed"
            self._guard_observation["allowed"] = True
            return True

        self._guard_observation["allowed"] = True
        return True

    # ── Post-sleep windup gate ──────────────────────────────────────────────

    def post_sleep_gate(self, now_monotonic: float | None = None) -> bool:
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

    # ── Phase / wall clock accessors ────────────────────────────────────────

    @property
    def current_phase(self) -> str:
        """Current phase as string for scheduler callbacks."""
        return self._adapter._current_phase

    # ── Wall clock ───────────────────────────────────────────────────────────

    @property
    def wall_clock_start(self) -> float | None:
        return self._wall_clock_start
