"""
Backpressure Monitor for FetchCoordinator
==========================================

ROLE: Memory-pressure-driven concurrency governor for the fetch pipeline.

Provides a feedback loop between M1 UMA memory state and AIMD concurrency
in FetchCoordinator. Unlike the general-purpose ResourceGovernor.evaluate()
(which is read-by-scheduler for acquisition planning), this module focuses
exclusively on the fetch lane and translates UMA state directly into
AIMD semaphore limits.

HOW IT WIRES:
    SprintScheduler.__init__
        → creates BackpressureMonitor(governor)
        → passes monitor.backpressure_provider to FetchCoordinator
        → FetchCoordinator._aimd_acquire() calls provider() each time
        → AIMD concurrency window is clamped to backpressure ceiling

SIGNALS:
    - clearnet_max: max concurrent clearnet fetches (clamped by memory pressure)
    - stealth_max:   max concurrent darknet fetches (fixed ratio to clearnet)
    - uma_state:    raw uma state from GovernorDecision
    - io_only:      from GovernorDecision (I/O-only mode hint)

INVARIANTS:
    - Always-on, fail-safe (returns safe defaults on any error)
    - Bounded: min=1, max=AIMD_MAX_CONCURRENCY
    - No new threads or background tasks — synchronous provider callable
    - Telemetry emitted on every state change
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default limits (match AIMD constants in fetch_coordinator.py)
_DEFAULT_CLEARNET_MAX = 5
_DEFAULT_STEALTH_MAX = 3
_MIN_CLEARNET = 1
_MAX_CLEARNET_FROM_GOVERNOR = 20

# Sprint F265B + F289: Adaptive cache TTL by UMA state — faster feedback for M1 8GB
# F289: Replaced _TTL_BY_STATE dict with ConcurrencyPreset.cache_ttl_seconds (SSOT)


@dataclass(frozen=True, slots=True)
class BackpressureDecision:
    """
    Backpressure decision for the fetch lane.
    Derived from GovernorDecision but scoped to fetch concurrency only.
    """
    clearnet_max: int   # effective ceiling for AIMD concurrency
    stealth_max: int    # max darknet fetches (tor/i2p)
    uma_state: str      # "ok"|"soft_warn"|"warn"|"critical"|"emergency"
    io_only: bool       # I/O-only hint


class BackpressureMonitor:
    """
    Memory-pressure monitor that translates GovernorDecision into fetch lane limits.

    Lives in the scheduler; called by FetchCoordinator on every _aimd_acquire().
    The provider callable is the seam — FetchCoordinator never imports this module.
    """

    def __init__(
        self,
        governor: object,  # Duck-typed: any object with async evaluate() → GovernorDecision
        min_clearnet: int = _MIN_CLEARNET,
        max_clearnet: int = _DEFAULT_CLEARNET_MAX,
    ):
        self._governor = governor
        self._min_clearnet = min_clearnet
        self._max_clearnet = max_clearnet

        # Cached decision (refreshed on each evaluate)
        self._decision: BackpressureDecision = BackpressureDecision(
            clearnet_max=max_clearnet,
            stealth_max=_DEFAULT_STEALTH_MAX,
            uma_state="ok",
            io_only=False,
        )
        self._last_evaluate: float = 0.0
        self._lock = asyncio.Lock()

        # Telemetry
        self._state_changes: int = 0
        self._last_state: str = "ok"

    async def evaluate(self) -> BackpressureDecision:
        """
        Re-evaluate backpressure from GovernorDecision.
        Caches result; subsequent calls within adaptive TTL return cached value.
        F289: TTL sourced from ConcurrencyPreset.cache_ttl_seconds (SSOT).
        """
        now = time.monotonic()
        # F289: Dynamic TTL from ConcurrencyPreset (SSOT for all state-derived values)
        from core.resource_governor import ConcurrencyPreset

        cache_ttl = ConcurrencyPreset.from_state(self._decision.uma_state).cache_ttl_seconds
        if now - self._last_evaluate < cache_ttl:
            return self._decision

        async with self._lock:
            # Double-check after acquiring lock
            if now - self._last_evaluate < cache_ttl:
                return self._decision

            try:
                governor_decision = await self._governor.evaluate()
            except Exception:
                # Fail-open: safe defaults
                self._decision = BackpressureDecision(
                    clearnet_max=self._max_clearnet,
                    stealth_max=_DEFAULT_STEALTH_MAX,
                    uma_state="ok",
                    io_only=False,
                )
                self._last_evaluate = now
                return self._decision

            # Map GovernorDecision.fetch_limit to BackpressureDecision
            governor_cap = governor_decision.fetch_limit

            # Clamp to our operational bounds
            clearnet_max = max(self._min_clearnet, min(governor_cap, self._max_clearnet))

            # Stealth follows clearnet at fixed ratio (tor/i2p are subset)
            # No stealth_max in GovernorDecision — derive from clearnet
            stealth_max = max(1, clearnet_max - 1)

            # io_only: not in runtime GovernorDecision — derive from uma_state
            io_only = governor_decision.uma_state in ("critical", "emergency")

            new_decision = BackpressureDecision(
                clearnet_max=clearnet_max,
                stealth_max=stealth_max,
                uma_state=governor_decision.uma_state,
                io_only=io_only,
            )

            # Log state transitions
            if new_decision.uma_state != self._last_state:
                logger.info(
                    f"[BACKPRESSURE] uma_state: {self._last_state} → {new_decision.uma_state} "
                    f"(clearnet_max={clearnet_max}, stealth_max={stealth_max})"
                )
                self._state_changes += 1
                self._last_state = new_decision.uma_state

            self._decision = new_decision
            self._last_evaluate = now
            return self._decision

    def get_decision(self) -> BackpressureDecision:
        """
        Synchronous read of cached decision.
        Returns safe defaults if never evaluated.
        """
        return self._decision

    # ── Provider callable for FetchCoordinator ────────────────────────────────

    def backpressure_provider(self) -> tuple[int, int, str, bool]:
        """
        Returns (clearnet_max, stealth_max, uma_state, io_only).
        Callable signature — no async, no self consumption.
        Used as `concurrency_provider` kwarg to FetchCoordinator.
        """
        d = self._decision
        return d.clearnet_max, d.stealth_max, d.uma_state, d.io_only

    # ── Telemetry ────────────────────────────────────────────────────────────

    def get_telemetry(self) -> dict:
        """For diagnostics and dashboard."""
        return {
            "clearnet_max": self._decision.clearnet_max,
            "stealth_max": self._decision.stealth_max,
            "uma_state": self._decision.uma_state,
            "io_only": self._decision.io_only,
            "state_changes": self._state_changes,
        }
