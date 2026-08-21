"""
AIMD Window Service — Additive Increase/Multiplicative Decrease Controller
============================================================================

Provides adaptive concurrency control for fetch operations.

Features:
- AIMD window management (addditive increase, multiplicative decrease)
- Blitz boost mode for rapid scaling during low-latency periods
- PyAIMDController with Python fallback

M1 8GB: Uses __slots__ for memory efficiency.

ISSUE-FOUND-1: Replaced asyncio.Semaphore with AtomicAdaptiveSemaphore for:
- PEP 789: Lazy initialization in async context (no __post_init__ creation)
- Python 3.14+ safe resize() instead of unsafe _value mutation
- O(1) resize with lock protection
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from hledac.universal.compat.msgspec_gc_compat import Struct
from hledac.universal.utils.concurrency import AtomicAdaptiveSemaphore

logger = logging.getLogger(__name__)


class AIMDConfig(Struct, frozen=True):
    """AIMD configuration. M1 8GB: msgspec.Struct for fast init."""

    initial_window: int = 4
    min_window: int = 1
    max_window: int = 256
    ai_step: int = 1
    md_factor: float = 0.5
    blitz_rtt_threshold_ms: float = 50.0
    blitz_window_boost: int = 4
    blitz_cooldown_s: float = 10.0


@dataclass(slots=True)
class AIMDWindowService:
    """
    AIMD (Additive Increase/Multiplicative Decrease) Window Controller.

    Implements adaptive concurrency control:
    - Additive increase: +1 per successful request
    - Multiplicative decrease: *0.5 on failure

    Blitz boost: During low-latency periods (RTT < threshold), window
    multiplies by boost factor for rapid scaling.

    M1 8GB: Uses __slots__ for memory efficiency (~40B/instance).

    ISSUE-FOUND-1: Uses AtomicAdaptiveSemaphore for Python 3.14+ safe resize.
    """

    config: AIMDConfig = field(default_factory=AIMDConfig)

    _window: int = field(default=4, init=False)
    _semaphore: AtomicAdaptiveSemaphore | None = field(default=None, init=False)
    _sem_init_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _last_blitz: float = field(default=0.0, init=False)
    _success_count: int = field(default=0, init=False)
    _failure_count: int = field(default=0, init=False)
    _total_rtt_ms: float = field(default=0.0, init=False)
    _request_count: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._window = self.config.initial_window
        # ISSUE-FOUND-1: PEP 789 - semaphore created lazily in async context
        # self._semaphore is None here, created by _ensure_semaphore()

    async def _ensure_semaphore(self) -> AtomicAdaptiveSemaphore:
        """PEP 789: Create AtomicAdaptiveSemaphore lazily in event loop context."""
        if self._semaphore is not None:
            return self._semaphore
        async with self._sem_init_lock:
            if self._semaphore is not None:
                return self._semaphore
            self._semaphore = AtomicAdaptiveSemaphore(initial=self._window)
            return self._semaphore

    @property
    def window(self) -> int:
        """Current AIMD window size."""
        return self._window

    @property
    def semaphore(self) -> AtomicAdaptiveSemaphore | None:
        """Semaphore for concurrency control (lazy init)."""
        return self._semaphore

    async def acquire(self) -> None:
        """Acquire a slot from the AIMD window."""
        sem = await self._ensure_semaphore()
        await sem.acquire()

    async def release(self) -> None:
        """Release a slot back to the AIMD window."""
        if self._semaphore is not None:
            await self._semaphore.release()

    async def record_success(self, rtt_ms: float | None = None) -> None:
        """
        Record successful request.

        Triggers additive increase and potentially blitz boost.
        """
        async with self._lock:
            self._success_count += 1
            self._total_rtt_ms += rtt_ms or 0
            self._request_count += 1

            # Additive increase
            new_window = min(self._window + self.config.ai_step, self.config.max_window)

            if rtt_ms is not None and rtt_ms < self.config.blitz_rtt_threshold_ms:
                now = time.monotonic()
                if now - self._last_blitz >= self.config.blitz_cooldown_s:
                    new_window = min(new_window * self.config.blitz_window_boost, self.config.max_window)
                    self._last_blitz = now
                    logger.debug(f"AIMD blitz boost: {self._window} -> {new_window} (RTT={rtt_ms:.1f}ms)")

            if new_window != self._window:
                self._window = new_window
                # ISSUE-FOUND-1: Safe resize with asyncio.Lock
                await self._resize_semaphore(new_window)

    async def record_failure(self) -> None:
        """
        Record failed request.

        Triggers multiplicative decrease.
        """
        async with self._lock:
            self._failure_count += 1

            # Multiplicative decrease
            new_window = max(int(self._window * self.config.md_factor), self.config.min_window)

            if new_window != self._window:
                self._window = new_window
                # ISSUE-FOUND-1: Safe resize with asyncio.Lock
                await self._resize_semaphore(new_window)
                logger.debug(f"AIMD MD: window reduced to {new_window}")

    async def _resize_semaphore(self, new_limit: int) -> None:
        """
        Resize semaphore to new limit using AtomicAdaptiveSemaphore.resize().

        ISSUE-FOUND-1: Python 3.14+ safe - uses lock-protected resize()
        instead of unsafe _value mutation on asyncio.Semaphore.
        """
        if self._semaphore is not None:
            await self._semaphore.resize(new_limit)

    def get_stats(self) -> dict:
        """Get AIMD statistics."""
        avg_rtt = self._total_rtt_ms / self._request_count if self._request_count > 0 else 0.0
        return {
            "window": self._window,
            "success_count": self._success_count,
            "failure_count": self._failure_count,
            "success_rate": (
                self._success_count / (self._success_count + self._failure_count)
                if (self._success_count + self._failure_count) > 0
                else 0.0
            ),
            "avg_rtt_ms": avg_rtt,
        }

    def reset(self) -> None:
        """Reset AIMD window to initial state."""
        self._window = self.config.initial_window
        # ISSUE-FOUND-1: Recreate semaphore at initial window
        self._semaphore = None  # Will be lazily recreated by _ensure_semaphore()
        self._last_blitz = 0.0
        self._success_count = 0
        self._failure_count = 0
        self._total_rtt_ms = 0.0
        self._request_count = 0

    async def aclose(self) -> None:
        """Close AIMD window service and release resources."""
        self._semaphore = None
        logger.debug("AIMDWindowService closed")


class PyAIMDController:
    """
    Bridge to PyAIMD library for advanced AIMD control.

    Falls back to Python AIMDWindowService if PyAIMD is not available.
    M1 8GB: Lazy import only when feature is enabled.
    """

    def __init__(self, config: AIMDConfig | None = None) -> None:
        self._config = config or AIMDConfig()
        self._controller = None
        self._available = False
        self._initialize()

    def _initialize(self) -> None:
        """Initialize PyAIMD controller with fallback."""
        try:
            from PyAIMD import AIMDController as PyAIMDClass

            self._controller = PyAIMDClass(
                initial_cwnd=self._config.initial_window,
                min_cwnd=self._config.min_window,
                max_cwnd=self._config.max_window,
                ai_step=self._config.ai_step,
                md_factor=self._config.md_factor,
            )
            self._available = True
            logger.info("PyAIMD controller loaded")
        except ImportError:
            # Fall back to Python implementation
            self._controller = AIMDWindowService(config=self._config)
            self._available = False
            logger.info("PyAIMD not available, using Python fallback")

    @property
    def available(self) -> bool:
        """Check if PyAIMD is available."""
        return self._available

    async def acquire(self) -> None:
        """Acquire a slot."""
        if self._available:
            await self._controller.acquire()
        else:
            await self._controller.acquire()

    def release(self) -> None:
        """Release a slot."""
        if self._available:
            self._controller.release()
        else:
            self._controller.release()

    async def record_success(self, rtt_ms: float | None = None) -> None:
        """Record successful request."""
        await self._controller.record_success(rtt_ms)

    async def record_failure(self) -> None:
        """Record failed request."""
        await self._controller.record_failure()

    @property
    def window(self) -> int:
        """Current window size."""
        return self._controller.window

    def get_stats(self) -> dict:
        """Get statistics."""
        return self._controller.get_stats()


__all__ = [
    "AIMDConfig",
    "AIMDWindowService",
    "PyAIMDController",
]
