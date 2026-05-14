"""
Sprint Lifecycle Manager — COMPAT SHIM ONLY.

================================================================
DEPRECATED — DO NOT USE IN NEW CODE
Canonical lifecycle authority: runtime/sprint_lifecycle.SprintLifecycleManager
================================================================

This module is kept for backward compatibility with existing call-sites
(__main__.py, synthesis_runner, htn_planner).

New code must use: hledac.universal.runtime.sprint_lifecycle.SprintLifecycleManager

This module contains:
- 15% COMPAT ALIASES → runtime/sprint_lifecycle canonical methods
- 85% ORCHESTRATION HELPERS → not lifecycle authority (hooks, signals, watchdog)

Lifecycle authority: BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN
Canonical: hledac.universal.runtime.sprint_lifecycle.SprintPhase enum
Checkpoint seam: maybe_resume() free function (LMDB)

Sprint F4: Module-level metadata:
  - status: COMPAT SHIM ONLY
  - canonical_authority: runtime/sprint_lifecycle.SprintLifecycleManager
  - active_importers:
      - legacy/autonomous_orchestrator.py (utils import)
      - planning/htn_planner.py (TYPE_CHECKING import only)
      - brain/synthesis_runner.py (Path 3 compat fallback)
  - future_owner: TBD — requires legacy AO refactor + htn_planner cutover
  - removal_condition: All active_importers migrated to runtime version
  - why_still_needed: 2 production modules still import this; cutover deferred to avoid behavior risk
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# =============================================================================
# SprintLifecycleState — public enum
# =============================================================================


class SprintLifecycleState(Enum):
    BOOT = "boot"
    WARMUP = "warmup"
    ACTIVE = "active"
    WINDUP = "windup"
    EXPORT = "export"
    TEARDOWN = "teardown"


# =============================================================================
# Helpers
# =============================================================================

def _get_sprint_duration_seconds() -> float:
    """Read sprint duration from env, default 30 min."""
    try:
        return float(os.environ.get("HLEDAC_SPRINT_DURATION_SECONDS", "1800"))
    except (ValueError, TypeError):
        return 1800.0


def _get_windup_lead_seconds() -> float:
    """Read T-3min wind-down lead time from env, default 180 s."""
    try:
        return float(os.environ.get("HLEDAC_WINDUP_LEAD_SECONDS", "180"))
    except (ValueError, TypeError):
        return 180.0


# =============================================================================
# SprintLifecycleManager
# =============================================================================


class SprintLifecycleManager:
    """
    Manages sprint lifecycle state machine with fail-open design.

    State transitions:
        BOOT → WARMUP → ACTIVE → WINDUP → EXPORT → TEARDOWN

    The manager:
    - Tracks sprint start time and duration
    - Fires wind-down hook T-3min before sprint end
    - Provides remaining_time read-only signal
    - Registers SIGINT/SIGTERM handlers pointing to unified shutdown
    - All methods are async-safe and fail-open
    """

    _instance: Optional["SprintLifecycleManager"] = None

    def __init__(self) -> None:
        self._state = SprintLifecycleState.BOOT
        self._sprint_start: Optional[float] = None
        self._sprint_duration: float = _get_sprint_duration_seconds()
        self._windup_lead: float = _get_windup_lead_seconds()
        self._windup_fired: bool = False
        self._shutdown_requested: bool = False
        self._shutdown_event: Optional[asyncio.Event] = None

        # Hooks — set by owner (orchestrator)
        self._on_windup: Optional[Callable[[], None]] = None
        self._on_export: Optional[Callable[[], None]] = None
        self._on_teardown: Optional[Callable[[], None]] = None

        # Signal registration flag
        self._signals_registered: bool = False

        # BG task tracking for this manager itself
        self._bg_tasks: set = set()

        # Checkpoint seam — prepared for Sprint 1B wiring
        self._checkpoint_seam_ready: bool = True  # CheckpointManager exists in tools/checkpoint.py

        # Wind-down polling task
        self._windown_task: Optional[asyncio.Task] = None

        # Sprint 7H: UMA Watchdog — started in ACTIVE, stopped on wind-down
        self._uma_watchdog: Optional["UmaWatchdog"] = None
        self._uma_watchdog_task: Optional[asyncio.Task] = None

    # ---- singleton (optional, for convenience) ----

    @classmethod
    def get_instance(cls) -> "SprintLifecycleManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- state ----

    @property
    def state(self) -> SprintLifecycleState:
        return self._state

    @property
    def is_active(self) -> bool:
        """True when in ACTIVE state (normal operations)."""
        return self._state == SprintLifecycleState.ACTIVE

    @property
    def is_winding_down(self) -> bool:
        """True when in WINDUP, EXPORT, or TEARDOWN states."""
        return self._state in (
            SprintLifecycleState.WINDUP,
            SprintLifecycleState.EXPORT,
            SprintLifecycleState.TEARDOWN,
        )

    @property
    def remaining_time(self) -> float:
        """
        Estimated seconds remaining in sprint. Returns 0.0 if not started.
        This is a read-only signal — never blocks.
        """
        if self._sprint_start is None:
            return 0.0
        elapsed = time.monotonic() - self._sprint_start
        return max(0.0, self._sprint_duration - elapsed)

    def is_windup_phase(self) -> bool:
        """
        Sprint 8PC: True when remaining_time < 180 seconds.
        Used by concurrency matrix to apply windup multiplier.
        """
        return self.remaining_time < 180.0

    @property
    def sprint_duration(self) -> float:
        return self._sprint_duration

    @property
    def shutdown_requested(self) -> bool:
        """True when SIGINT/SIGTERM has been received."""
        return self._shutdown_requested

    @property
    def windup_fired(self) -> bool:
        """True when wind-down has been triggered (always True once fired)."""
        return self._windup_fired

    # ---- state transitions ----

    def transition_to(self, new_state: SprintLifecycleState) -> None:
        """
        Transition to a new state. Idempotent — same-state transition is a no-op.
        Logs all transitions.
        """
        if new_state == self._state:
            return
        old = self._state
        self._state = new_state
        logger.info(f"[LIFECYCLE] {old.value} → {new_state.value}")

    def begin_sprint(self) -> None:
        """
        Mark sprint as started, transition to WARMUP.

        Sprint F4 metadata:
          alias_for: runtime.SprintLifecycleManager.start()
          future_owner: __main__.py, legacy autonomous_orchestrator
          caller_class: legacy autonomous_orchestrator (line ~11723)
          removal_condition: All callers migrated to runtime version
          why_still_needed: legacy AO still imports utils version directly
        """
        if self._sprint_start is not None:
            return  # Already begun
        self._sprint_start = time.monotonic()
        # Respect pre-configured duration if set; otherwise use env default
        if self._sprint_duration <= 0.0:
            self._sprint_duration = _get_sprint_duration_seconds()
        if self._windup_lead <= 0.0:
            self._windup_lead = _get_windup_lead_seconds()
        self._windup_fired = False
        self.transition_to(SprintLifecycleState.WARMUP)
        logger.info(f"[LIFECYCLE] Sprint started — duration={self._sprint_duration}s, windup_lead={self._windup_lead}s")

    def mark_warmup_done(self) -> None:
        """
        Transition from WARMUP to ACTIVE. Idempotent.

        Sprint F4 metadata:
          alias_for: runtime.SprintLifecycleManager.transition_to(ACTIVE)
          future_owner: __main__.py
          caller_class: legacy autonomous_orchestrator (not currently called per grep)
          removal_condition: All callers migrated to runtime version
          why_still_needed: legacy AO imports this module; no active call-sites in legacy AO per current grep
        """
        if self._state == SprintLifecycleState.WARMUP:
            self.transition_to(SprintLifecycleState.ACTIVE)
            # Start wind-down polling task
            self._start_windown_monitor()
            # Start UMA watchdog (Sprint 7H)
            self._start_uma_watchdog()

    def request_windup(self) -> None:
        """
        Request wind-down. Can be called from timer, SIGINT/SIGTERM, or manual trigger.
        Idempotent — only fires once.

        Sprint F4 metadata:
          alias_for: runtime.SprintLifecycleManager.transition_to(WINDUP)
          future_owner: __main__.py
          caller_class: legacy autonomous_orchestrator (not called per grep)
          removal_condition: All callers migrated to runtime version
          why_still_needed: legacy AO has this as available method but no active call-sites
        """
        if self._windup_fired:
            return
        self._windup_fired = True
        # Stop UMA watchdog on wind-down
        self._stop_uma_watchdog()
        self.transition_to(SprintLifecycleState.WINDUP)
        logger.info("[LIFECYCLE] Wind-down requested")

    def request_export(self) -> None:
        """
        Transition from WINDUP to EXPORT. Called after synthesis phase.

        Sprint F4 metadata:
          alias_for: runtime.SprintLifecycleManager.mark_export_started()
          future_owner: __main__.py
          caller_class: legacy autonomous_orchestrator (line ~12357)
          removal_condition: All callers migrated to runtime version
          why_still_needed: legacy AO still calls this; __main__.py uses canonical directly
        """
        if self._state == SprintLifecycleState.WINDUP:
            self.transition_to(SprintLifecycleState.EXPORT)
            logger.info("[LIFECYCLE] Export phase")
            if self._on_export is not None:
                try:
                    self._on_export()
                except Exception as e:
                    logger.warning(f"[LIFECYCLE] export hook error: {e}")

    def request_teardown(self) -> None:
        """
        Transition from any winding-down state to TEARDOWN.

        Sprint F4 metadata:
          alias_for: runtime.SprintLifecycleManager.mark_teardown_started()
          future_owner: __main__.py
          caller_class: legacy autonomous_orchestrator (line ~12690)
          removal_condition: All callers migrated to runtime version
          why_still_needed: legacy AO still calls this; __main__.py does not call this method
        """
        if self._state == SprintLifecycleState.TEARDOWN:
            return
        old_state = self._state
        self.transition_to(SprintLifecycleState.TEARDOWN)
        logger.info("[LIFECYCLE] Teardown phase")
        if old_state != SprintLifecycleState.TEARDOWN and self._on_teardown is not None:
            try:
                self._on_teardown()
            except Exception as e:
                logger.warning(f"[LIFECYCLE] teardown hook error: {e}")

    # ---- UMA watchdog (Sprint 7H) ----

    def _start_uma_watchdog(self) -> None:
        """
        Start UmaWatchdog when entering ACTIVE state.
        Fails silently if no event loop or watchdog import fails.
        Watchdog is tracked via track_task() for lifecycle management.
        """
        # Lazy import to avoid heavy import on boot path
        try:
            from .uma_budget import UmaWatchdog, UmaWatchdogCallbacks
            from ..brain.model_lifecycle import request_emergency_unload, clear_emergency_unload_request
        except Exception as e:
            logger.debug(f"[LIFECYCLE] UmaWatchdog import failed: {e}")
            return

        # Define lifecycle-aware callbacks
        # Capture reference to outer manager to call request_windup
        manager_ref = self

        class _LifecycleWatchdogCallbacks(UmaWatchdogCallbacks):
            def on_warn(self, snapshot: dict) -> None:
                logger.warning(
                    f"[LIFECYCLE-WATCHDOG] WARN: "
                    f"{snapshot.get('uma_used_mb', 0):,} MB "
                    f"({snapshot.get('uma_usage_pct', 0)}%)"
                )

            def on_critical(self, snapshot: dict) -> None:
                logger.error(
                    f"[LIFECYCLE-WATCHDOG] CRITICAL: "
                    f"{snapshot.get('uma_used_mb', 0):,} MB "
                    f"({snapshot.get('uma_usage_pct', 0)}%)"
                )
                # Trigger wind-up via lifecycle request_windup
                manager_ref.request_windup()

            def on_emergency(self, snapshot: dict) -> None:
                logger.error(
                    f"[LIFECYCLE-WATCHDOG] EMERGENCY: "
                    f"{snapshot.get('uma_used_mb', 0):,} MB "
                    f"({snapshot.get('uma_usage_pct', 0)}%)"
                )
                # Set safe emergency flag — never direct unload
                request_emergency_unload()

        callbacks = _LifecycleWatchdogCallbacks()
        self._uma_watchdog = UmaWatchdog(callbacks=callbacks, interval=0.5)

        try:
            task = self._uma_watchdog.start()
            # Track via lifecycle so cancel() stops it properly
            self.track_task(task)
            self._uma_watchdog_task = task
            logger.info("[LIFECYCLE] UmaWatchdog started (ACTIVE phase)")
        except RuntimeError as e:
            logger.debug(f"[LIFECYCLE] UmaWatchdog start failed: {e}")

    def _stop_uma_watchdog(self) -> None:
        """Stop UMA watchdog. Called when exiting ACTIVE state."""
        if self._uma_watchdog is not None:
            self._uma_watchdog.stop()
            self._uma_watchdog = None
        if self._uma_watchdog_task is not None and not self._uma_watchdog_task.done():
            self._uma_watchdog_task.cancel()
            self._uma_watchdog_task = None
        logger.info("[LIFECYCLE] UmaWatchdog stopped")

    # ---- wind-down monitor ----

    def _start_windown_monitor(self) -> None:
        """Start background task that fires wind-up at T-3min. Fail-open if no event loop."""
        if self._windown_task is not None and not self._windown_task.done():
            return

        async def _monitor():
            while True:
                await asyncio.sleep(10)  # Poll every 10s
                if self._state != SprintLifecycleState.ACTIVE:
                    break
                remaining = self.remaining_time
                if remaining <= self._windup_lead:
                    self.request_windup()
                    # Fire wind-up hook
                    if self._on_windup is not None:
                        try:
                            self._on_windup()
                        except Exception as e:
                            logger.warning(f"[LIFECYCLE] windup hook error: {e}")
                    break

        try:
            self._windown_task = asyncio.create_task(_monitor(), name="sprint_lifecycle:winddown_monitor")
            self._bg_tasks.add(self._windown_task)
            self._windown_task.add_done_callback(self._bg_tasks.discard)
        except RuntimeError:
            # No running event loop — fail-open (wind-down won't auto-trigger but state machine works)
            logger.debug("[LIFECYCLE] No event loop, wind-down monitor disabled")

    # ---- hooks ----

    def set_windup_hook(self, fn: Callable[[], None]) -> None:
        """Set callback to run when wind-down is triggered."""
        self._on_windup = fn

    def set_export_hook(self, fn: Callable[[], None]) -> None:
        """Set callback to run when export phase begins."""
        self._on_export = fn

    def set_teardown_hook(self, fn: Callable[[], None]) -> None:
        """Set callback to run when teardown is triggered."""
        self._on_teardown = fn

    # ---- SIGINT / SIGTERM ----

    def register_signal_handlers(self, shutdown_coro: Callable[[], Any]) -> None:
        """
        Register SIGINT/SIGTERM handlers that call shutdown_coro.
        Must be called from the main thread / before asyncio loop is created.
        Idempotent.

        Args:
            shutdown_coro: async callable that initiates graceful shutdown
                           (e.g., orchestrator.shutdown_all)
        """
        if self._signals_registered:
            return
        self._signals_registered = True

        def _handler(signum, *_):
            sig_name = signal.Signals(signum).name
            logger.info(f"[LIFECYCLE] Received {sig_name}")
            self._shutdown_requested = True
            # Schedule shutdown in the asyncio event loop
            try:
                loop = asyncio.get_running_loop()
                coro = shutdown_coro()
                if coro is not None:
                    loop.call_soon_threadsafe(lambda c=coro: asyncio.create_task(c))  # type: ignore[arg-type]
            except RuntimeError:
                # No running loop yet — defer
                pass

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
            logger.info("[LIFECYCLE] SIGINT/SIGTERM handlers registered")
        except (ValueError, OSError) as e:
            logger.warning(f"[LIFECYCLE] Could not register signal handlers: {e}")

    # ---- bg_tasks helper (systematic tracking) ----

    def track_task(self, task: asyncio.Task) -> None:
        """Add task to internal registry with done-callback that logs exceptions."""
        self._bg_tasks.add(task)
        task.add_done_callback(self._on_task_done)

    @staticmethod
    def _on_task_done(task: asyncio.Task) -> None:
        """Done-callback: log exception if task failed, then discard."""
        try:
            if not task.cancelled():
                exc = task.exception()
                if exc is not None:
                    logger.warning(f"[LIFECYCLE] Background task {task.get_name()} failed: {exc}")
        except asyncio.InvalidStateError:
            pass  # Task may have been garbage collected

    # ---- checkpoint seam (prepared, not wired) ----

    @property
    def checkpoint_seam_ready(self) -> bool:
        """
        True when checkpoint save/load is safe to call.
        Always True in this implementation — checkpoint.py exists.
        Wiring to CheckpointManager is Sprint 1B scope.
        """
        return self._checkpoint_seam_ready

    def get_checkpoint_seam(self) -> dict:
        """
        Return a minimal checkpoint payload for this layer.
        Sprint 1B will wire this into CheckpointManager.save().
        """
        return {
            "lifecycle_state": self._state.value,
            "sprint_start": self._sprint_start,
            "sprint_duration": self._sprint_duration,
            "windup_fired": self._windup_fired,
        }

    def load_from_checkpoint(self, data: dict) -> None:
        """
        Restore lifecycle state from checkpoint payload.
        Sprint 1B will call this in CheckpointManager.load().
        """
        self._sprint_start = data.get("sprint_start")
        self._sprint_duration = data.get("sprint_duration", 1800.0)
        self._windup_fired = data.get("windup_fired", False)
        state_val = data.get("lifecycle_state")
        if state_val:
            try:
                self._state = SprintLifecycleState(state_val)
            except ValueError:
                pass

    # ---- cancel / cleanup ----

    async def cancel(self) -> None:
        """Cancel all internal background tasks."""
        if self._windown_task and not self._windown_task.done():
            self._windown_task.cancel()
            try:
                await self._windown_task
            except asyncio.CancelledError:
                pass
        for t in list(self._bg_tasks):
            t.cancel()
        self._bg_tasks.clear()


# =============================================================================
# maybe_resume() — fail-open checkpoint seam
# =============================================================================


def maybe_resume(lmdb_env=None) -> bool:
    """
    Return True if an unfinished sprint can be resumed from checkpoint.

    Canonical LMDB keys read:
        b"sprint:last_phase"   — phase string
        b"sprint:current_id"    — sprint id string

    Unfinished means phase exists and is NOT "export" nor "teardown".

    Fail-open: any error (MissingError, AttributeError, OSError) → returns False.

    Args:
        lmdb_env: optional LMDB.Environment instance. If None, returns False.

    Returns:
        True if sprint is resumable, False otherwise.
    """
    if lmdb_env is None:
        return False

    try:
        with lmdb_env.begin() as txn:
            phase_bytes = txn.get(b"sprint:last_phase")
            if phase_bytes is None:
                return False
            phase = phase_bytes.decode("utf-8", errors="replace")
            return phase not in ("export", "teardown")
    except Exception:
        # fail-open: any error → not resumable
        return False


__all__ = [
    "SprintLifecycleManager",
    "SprintLifecycleState",
    "maybe_resume",
]
