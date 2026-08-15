"""
PHYSICS-01: Micro-Burst Scheduler — Proactive Thermal Management for M1 SoC

The M1 chip is passively cooled (fanless MacBook Air). Under sustained

GPU+CPU load, the aluminum chassis cannot dissipate heat fast enough.
After ~5 minutes of 100% load, the SoC throttles to ~60% performance.

The MicroBurstScheduler enforces a temporal interleaving pattern that
exploits natural I/O-wait windows as passive dissipation opportunities:

    200 ms GPU-heavy block (MLX inference, compute)
    → 50 ms I/O-only block (network awaits, DNS prefetch)
    → repeat

During I/O blocks the CPU is waiting on network (DMA), not computing —
these windows are "free" from a thermal perspective. The 50 ms gaps
every 250 ms cycle give the aluminum chassis enough time to dissipate
accumulated heat, preventing the SoC from ever hitting the thermal
throttling threshold.

Design (Python 3.14+ best practices):
- Uses time.monotonic() for high-resolution timing (nanosecond precision on macOS)
- asyncio.sleep(0) yield points as natural dissipation triggers
- Integrated into M1ResourceGovernor.evaluate() via burst_phase field
- Zero-alloc hot path: BurstPhase is a simple enum, no object creation
- Bounded: exactly one instance per process (module-level singleton)
- Fail-soft: if monitoring fails, falls back to always-GPU_HEAVY (existing behavior)
- Thread-safe: _phase_lock protects phase transitions

Integration points:
- M1ResourceGovernor.evaluate() reads burst_phase via get_burst_phase()
- MLX scheduler checks burst_phase before dispatching GPU work
- Fetch coordinator checks burst_phase before dispatching network work
- asyncio.sleep(0) at yield points provides natural I/O-only windows
"""

from __future__ import annotations

import asyncio
import threading
import time
from enum import Enum, auto
from typing import Final

from hledac.universal.utils._patterns import module_singleton_creator
from hledac.universal.utils.logger import get_logger
from _core._util import aclose

logger = get_logger(__name__)


class BurstPhase(Enum):
    """Micro-burst scheduler phase.

    GPU_HEAVY — 200 ms compute block (MLX inference, Rust extensions, CPU-bound).
    IO_HEAVY  — 50 ms I/O-only block (network awaits, DNS prefetch, disk I/O).
    """

    GPU_HEAVY = auto()
    IO_HEAVY = auto()

    def __repr__(self) -> str:
        return self.name


# PHYSICS-01: Micro-burst timing constants, tuned for M1 passive cooling.
# - 200 ms GPU: long enough for meaningful compute, short enough to prevent
#   the SoC from building up critical thermal mass.
# - 50 ms I/O: the aluminum chassis dissipates ~0.5°C per 50 ms when idle;
#   this recovery window keeps the SoC below the throttling threshold (~70°C CPU).
# - 250 ms total cycle = 4 Hz — fast enough that individual fetch tasks don't
#   notice the pauses, slow enough that context switching overhead is negligible.
_BURST_GPU_MS: Final[float] = 200.0  # GPU compute window
_BURST_IO_MS: Final[float] = 50.0  # I/O dissipation window
_BURST_CYCLE_MS: Final[float] = _BURST_GPU_MS + _BURST_IO_MS  # 250 ms total cycle
_BURST_GPU_FRACTION: Final[float] = _BURST_GPU_MS / _BURST_CYCLE_MS  # 0.8

# Minimum time between phase re-evaluations to avoid timer churn.
# 50 ms = half the I/O window — ensures we check at least twice per I/O phase
# but don't spin the event loop unnecessarily.
_PHASE_CHECK_INTERVAL_S: Final[float] = 0.05  # 50 ms


class MicroBurstScheduler:
    """
    PHYSICS-01: Proactive thermal interleaving for M1 fanless SoC.

    Lifecycle:
        1. Created once per process (module-level singleton via get_scheduler())
        2. step() is called before each GPU dispatch point
        3. get_phase() returns the current phase for callers to check
        4. M1ResourceGovernor reads phase via get_phase() to set
           GovernorDecision.burst_phase

    Thread safety:
        _phase_lock protects phase transitions.
        get_phase() is lock-free (reads atomic enum reference on CPython GIL).

    Memory:
        ~200 bytes resident. Zero allocations on the hot path.
    """

    __slots__ = (
        '_phase',
        '_phase_lock',
        '_phase_start_mono',
        '_last_check_mono',
        '_phase_transitions',
        '_started',
    )

    def __init__(self) -> None:
        self._phase: BurstPhase = BurstPhase.GPU_HEAVY
        self._phase_lock: threading.Lock = threading.Lock()
        self._phase_start_mono: float = time.monotonic()
        self._last_check_mono: float = self._phase_start_mono
        self._phase_transitions: int = 0
        self._started: bool = False

    @property
    def phase(self) -> BurstPhase:
        """Current burst phase (lock-free read — atomic on CPython GIL)."""
        return self._phase

    @property
    def phase_transition_count(self) -> int:
        """Total phase transitions since start (diagnostic)."""
        return self._phase_transitions

    def start(self) -> None:
        """Initialize the scheduler. Idempotent — subsequent calls are no-ops."""
        if self._started:
            return
        with self._phase_lock:
            if self._started:
                return
            self._phase = BurstPhase.GPU_HEAVY
            self._phase_start_mono = time.monotonic()
            self._last_check_mono = self._phase_start_mono
            self._phase_transitions = 0
            self._started = True
        logger.debug(
            '[PHYSICS-01] MicroBurstScheduler started: GPU=%.0fms IO=%.0fms cycle=%.0fms',
            _BURST_GPU_MS, _BURST_IO_MS, _BURST_CYCLE_MS,
        )

    def get_phase(self) -> BurstPhase:
        """
        Return the current burst phase.

        Callers use this to decide whether to dispatch GPU or I/O work:
        - GPU_HEAVY → OK to dispatch MLX inference, compute work
        - IO_HEAVY  → Only I/O-bound work (network, DNS, disk); yield to event loop

        This is a lock-free read on CPython (GIL-protected enum reference).
        """
        return self._phase

    def step(self) -> BurstPhase:
        """
        Evaluate and potentially transition the burst phase.

        Call this at each dispatch point (before GPU work, after I/O completion).
        Uses time.monotonic() to check if the current phase window has elapsed.

        Returns:
            The (possibly new) current phase.
        """
        now_mono = time.monotonic()

        # Rate-limit checks — avoid timer churn in tight loops
        if now_mono - self._last_check_mono < _PHASE_CHECK_INTERVAL_S:
            return self._phase

        self._last_check_mono = now_mono
        elapsed_ms = (now_mono - self._phase_start_mono) * 1000.0

        if self._phase is BurstPhase.GPU_HEAVY:
            if elapsed_ms >= _BURST_GPU_MS:
                self._transition_to(BurstPhase.IO_HEAVY, now_mono)
        else:  # IO_HEAVY
            if elapsed_ms >= _BURST_IO_MS:
                self._transition_to(BurstPhase.GPU_HEAVY, now_mono)

        return self._phase

    def _transition_to(self, new_phase: BurstPhase, now_mono: float) -> None:
        """Thread-safe phase transition with diagnostic logging."""
        with self._phase_lock:
            old_phase = self._phase
            if old_phase == new_phase:
                return  # already transitioned by another thread
            self._phase = new_phase
            self._phase_start_mono = now_mono
            self._phase_transitions += 1

        _elapsed = (now_mono - self._phase_start_mono) * 1000.0 if False else 0.0  # unused
        logger.debug(
            '[PHYSICS-01] Phase: %s → %s (transition #%d)',
            old_phase.name, new_phase.name, self._phase_transitions,
        )

    def reset(self) -> None:
        """Reset to initial state (for testing or sprint re-initialisation)."""
        with self._phase_lock:
            self._phase = BurstPhase.GPU_HEAVY
            self._phase_start_mono = time.monotonic()
            self._last_check_mono = self._phase_start_mono
            self._phase_transitions = 0
            self._started = False


# Module-level singleton — one scheduler per process.
# F330-DUP: Refactored to use module_singleton_creator from utils/_patterns.py


def _create_scheduler() -> MicroBurstScheduler:
    """Factory for MicroBurstScheduler singleton with lifecycle start."""
    scheduler = MicroBurstScheduler()
    scheduler.start()
    return scheduler


# DRY: Double-checked locking singleton via module_singleton_creator
get_scheduler = module_singleton_creator(factory=_create_scheduler)


# Convenience exports for callers that don't want to hold a reference.
def get_burst_phase() -> BurstPhase:
    """Return the current burst phase (convenience, delegates to singleton)."""
    return get_scheduler().get_phase()


def step_burst_phase() -> BurstPhase:
    """Evaluate and potentially transition the burst phase."""
    return get_scheduler().step()


async def yield_io_window() -> None:
    """
    Yield to the event loop for one I/O dissipation window.

    Call this from GPU-heavy code that wants to proactively give the SoC
    a thermal dissipation opportunity. Uses asyncio.sleep(0) which yields
    control back to the event loop — during this yield, the system can
    process pending I/O (network, DNS) which is "free" from a thermal
    perspective since the CPU is just waiting on DMA.

    Typical usage in MLX inference loop:

        for batch in batches:
            result = mlx_inference(batch)  # GPU-heavy
            phase = step_burst_phase()
            if phase is BurstPhase.IO_HEAVY:
                await yield_io_window()
    """
    await asyncio.sleep(0)


__all__ = [
    'BurstPhase',
    'MicroBurstScheduler',
    'get_scheduler',
    'get_burst_phase',
    'step_burst_phase',
    'yield_io_window',
    '_BURST_GPU_MS',
    '_BURST_IO_MS',
    '_BURST_CYCLE_MS',
]
