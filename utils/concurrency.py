"""
Utils Concurrency — Centralized asyncio synchronization primitives
================================================================

Single source of truth for shared asyncio primitives.
Import from here — never from __init__.py for synchronization primitives.

P19: Created to break circular import between __init__.py and public_fetcher.py.

F330-R2: AtomicAdaptiveSemaphore replaces unsafe _value mutation of asyncio.Semaphore.
In Python 3.14+ the internal _waiters deque means direct _value mutation can cause
deadlocks or race conditions. AtomicAdaptiveSemaphore uses asyncio.Lock + Condition
to provide a thread-safe, event-loop-safe adaptive semaphore with O(1) resize.
"""


import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable
    from core.resource_governor import GovernorDecision

logger = logging.getLogger(__name__)

from core.psutil_shim import psutil  # noqa: E402


class AtomicAdaptiveSemaphore:
    """
    Thread-safe adaptive semaphore with dynamic bound resizing.

    Replaces unsafe asyncio.Semaphore._value mutation which is racy in Python 3.14+
    where _waiters deque means external _value writes can cause:
      1. Deadlock — if _value is raised but _waiters has pending acquire() callers
      2. Overflow  — if _value >> actual available permits
      3. GIL race — between _value check and _waiters append in 3.14

    This implementation uses asyncio.Lock + asyncio.Condition to provide:
      - O(1) resize() for growth (delta > 0), no permit loss
      - Natural drain for shrink (delta < 0), no artificial lowering
      - Clear available() and waiters() introspection
      - Backward-compatible acquire()/release() API

    Usage:
        sem = AtomicAdaptiveSemaphore(initial=25)
        await sem.acquire()
        sem.release()
        await sem.resize(50)   # safe — notifies waiters
    """

    __slots__ = ("_lock", "_cond", "_value", "_max", "_waiters")

    def __init__(self, initial: int) -> None:
        self._lock = asyncio.Lock()
        self._cond = asyncio.Condition(self._lock)
        self._value: int = initial  # available permits
        self._max: int = initial    # current bound (resize target)
        self._waiters: deque["asyncio.Future[None]"] = deque()

    async def acquire(self) -> None:
        """Acquire one permit, yielding to event loop if none available."""
        async with self._cond:
            while self._value <= 0:
                try:
                    await self._cond.wait()
                except asyncio.CancelledError:
                    raise
            self._value -= 1

    async def release(self) -> None:
        """Release one permit, waking a waiter if any are blocked."""
        async with self._cond:
            self._value += 1
            if self._waiters:
                fut = self._waiters.popleft()
                if not fut.done():
                    self._value -= 1
                    fut.set_result(None)

    async def resize(self, new_max: int) -> None:
        """
        Resize the semaphore bound to new_max.

        Grow (delta > 0): immediately adds delta permits + notifies waiters.
        Shrink (delta < 0): natural drain — bound unchanged, new acquires
        are capped by _value going down as releases happen.

        This is safe because:
          - Lock held during entire update — no GIL race
          - Growth notifies all waiters so they can re-check
          - Shrink does NOT lower _value artificially (would lose permits)
        """
        if new_max == self._max:
            return

        async with self._cond:
            delta = new_max - self._max
            self._max = new_max

            if delta > 0:
                self._value += delta
                # Wake up to delta waiters so they can consume the new permits
                notified = 0
                while self._waiters and notified < delta:
                    fut = self._waiters.popleft()
                    if not fut.done():
                        self._value -= 1
                        fut.set_result(None)
                        notified += 1
                self._cond.notify(min(notified, len(self._waiters)))
            # delta < 0: natural drain handles the rest

    @property
    def available(self) -> int:
        """Return current available permits (approximate, not guaranteed atomic)."""
        return self._value

    @property
    def max(self) -> int:
        """Return current semaphore bound."""
        return self._max

    @property
    def waiters(self) -> int:
        """Return approximate number of waiters (not guaranteed atomic)."""
        return len(self._waiters)

    def stats(self) -> dict[str, int]:
        """Return diagnostic stats dict."""
        return {
            "value": self._value,
            "max": self._max,
            "waiters": len(self._waiters),
        }


# P3: FETCH_SEMAPHORE — shared semaphore for fetch concurrency control
_FETCH_SEMAPHORE: AtomicAdaptiveSemaphore | None = None


def get_fetch_semaphore(initial_limit: int = 25) -> AtomicAdaptiveSemaphore:
    """
    Get or create the shared FETCH_SEMAPHORE.

    This is a lazy singleton — semaphore is created on first call within event loop.

    Args:
        initial_limit: Initial semaphore limit (default 25)

    Returns:
        The shared FETCH_SEMAPHORE instance
    """
    global _FETCH_SEMAPHORE
    if _FETCH_SEMAPHORE is None:
        _FETCH_SEMAPHORE = AtomicAdaptiveSemaphore(initial_limit)
        logger.debug(f"[FETCH_SEMAPHORE] Created with limit={initial_limit}")
    return _FETCH_SEMAPHORE


# For backward compatibility — module-level binding
# Usage: from utils.concurrency import FETCH_SEMAPHORE
# This creates the semaphore on first access
class _FetchSemaphoreProxy:
    """Proxy object that lazily initializes the semaphore on first attribute access."""

    def __getattr__(self, name: str):
        sem = get_fetch_semaphore()
        return getattr(sem, name)

    def limit(self) -> int:
        """Return current available permits (delegates to underlying semaphore)."""
        return get_fetch_semaphore().available

    def __repr__(self):
        sem = get_fetch_semaphore()
        return f"FetchSemaphore(available={sem.available}, max={sem.max})"


FETCH_SEMAPHORE = _FetchSemaphoreProxy()


_last_adjust_time: float = 0.0
_last_adjust_value: int = -1  # sentinel: unknown


async def adjust_fetch_workers(new_limit: int) -> None:
    """
    Adjust BOTH _FETCH_SEMAPHORE and _clearnet_semaphore to new_limit atomically.

    Fix F265: Modifies existing semaphore _value in-place instead of replacing
    the object. Previous pattern created a new asyncio.Semaphore object which
    caused reference split — existing code holding the old semaphore reference
    never saw the updated limit. Now we modify _value on the existing semaphore
    instance so all existing references see the change immediately.

    M1 8GB constraint: cap at 12 when swap > 2 GiB.

    F3.2: Rate-limited + deduplicated — skip if value unchanged or called
    within 1 second to prevent log spam from rapid model load/unload cycles.
    F3.2b: Also adjusts _tor_semaphore (fixed 1/5 ratio), matching the
    invariant from AdaptiveWorkerPool._apply_fetch_limit for consistency.
    """
    global _FETCH_SEMAPHORE, _clearnet_semaphore, _tor_semaphore, _last_adjust_time, _last_adjust_value

    # F3.2: Deduplication — skip if value hasn't changed
    if new_limit == _last_adjust_value:
        return

    # F3.2: Rate limiting — max once per second
    now = time.monotonic()
    if now - _last_adjust_time < 1.0:
        return

    _last_adjust_time = now
    _last_adjust_value = new_limit

    try:
        _psutil = psutil
        if _psutil is not None:
            swap_gib = _psutil.swap_memory().used / 1e9
        else:
            swap_gib = 0.0
        if swap_gib > 2.0:
            new_limit = min(new_limit, 12)
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # fail-open: use new_limit as-is

    old_fetch = _FETCH_SEMAPHORE.max if _FETCH_SEMAPHORE else 0
    old_clearnet = _clearnet_semaphore.max if _clearnet_semaphore else 0
    old_tor = _tor_semaphore.max if _tor_semaphore else 0
    tor_limit = max(1, new_limit // 5)

    # Use safe resize() — holds lock, updates atomically, notifies waiters on grow
    if _FETCH_SEMAPHORE is not None:
        await _FETCH_SEMAPHORE.resize(new_limit)
    if _clearnet_semaphore is not None:
        await _clearnet_semaphore.resize(max(1, new_limit))
    if _tor_semaphore is not None:
        await _tor_semaphore.resize(tor_limit)

    logger.info(
        f"[FETCH_WORKERS] Adjusted fetch {old_fetch}→{new_limit}, "
        f"clearnet {old_clearnet}→{max(1, new_limit)}, tor {old_tor}→{tor_limit}"
    )


# =============================================================================
# F191B: Separate semaphore pools for clearnet vs Tor — no head-of-line blocking
# =============================================================================
# Sprint F191B: clearnet/Tor separate pools prevent Tor latency starving clearnet
# clearnet: 25 concurrent (fast, parallelizable)
# Tor: 5 concurrent (slow by design, circuit setup)
# M1 8GB adaptive: reduce when RAM > 5.5 GB

_clearnet_semaphore: AtomicAdaptiveSemaphore | None = None
_tor_semaphore: AtomicAdaptiveSemaphore | None = None

CLEARNET_CONCURRENCY: int = 25
TOR_CONCURRENCY: int = 5


def get_clearnet_semaphore() -> AtomicAdaptiveSemaphore:
    """Get or create the shared clearnet semaphore (lazy singleton)."""
    global _clearnet_semaphore
    if _clearnet_semaphore is None:
        adaptive = get_adaptive_limit()
        _clearnet_semaphore = AtomicAdaptiveSemaphore(adaptive)
        logger.debug(f"[CLEARNET_SEMAPHORE] Created with limit={adaptive}")
    return _clearnet_semaphore


def get_tor_semaphore() -> AtomicAdaptiveSemaphore:
    """Get or create the shared Tor semaphore (lazy singleton)."""
    global _tor_semaphore
    if _tor_semaphore is None:
        _tor_semaphore = AtomicAdaptiveSemaphore(TOR_CONCURRENCY)
        logger.debug(f"[TOR_SEMAPHORE] Created with limit={TOR_CONCURRENCY}")
    return _tor_semaphore


def get_adaptive_limit() -> int:
    """
    Reduce concurrency limit when RAM > 5.5 GB (M1 8GB constraint).

    Returns adaptive clearnet concurrency based on memory pressure:
    - RSS > 5.5 GB: 3 (critical — LLM + orchestrator active)
    - RSS > 4.5 GB: 10 (moderate — LLM loaded)
    - otherwise: CLEARNET_CONCURRENCY (25)
    """
    try:
        from core.psutil_shim import process as _psutil_process
        p = _psutil_process()
        if p is None:
            return CLEARNET_CONCURRENCY
        rss_gb = p.memory_info().rss / 1e9
    except Exception:
        return CLEARNET_CONCURRENCY
    if rss_gb > 5.5:
        return 3
    elif rss_gb > 4.5:
        return 10
    return CLEARNET_CONCURRENCY


async def adjust_clearnet_workers(new_limit: int) -> None:
    """
    Dynamically adjust clearnet semaphore limit via resize().

    Uses AtomicAdaptiveSemaphore.resize() which is safe — holds lock,
    updates _max, and notifies waiters on growth.
    """
    global _clearnet_semaphore
    if _clearnet_semaphore is None:
        return
    old_limit = _clearnet_semaphore.max
    await _clearnet_semaphore.resize(max(1, new_limit))
    logger.info(f"[CLEARNET_WORKERS] Adjusted from {old_limit} to {new_limit}")


# ── AdaptiveWorkerPool: Unified worker scaling via M1ResourceGovernor ──────────

class AdaptiveWorkerPool:
    """
    Unified adaptive worker pool — single source of truth for fetch + ML workers.

    Derives BOTH fetch_limit and max_workers (ML_JOBS) from M1ResourceGovernor
    evaluation, ensuring consistent memory-pressure-driven scaling across all
    concurrency primitives.

    M1 8GB calibrated via ConcurrencyPreset:
        emergency:  0 workers, 1 fetch
        critical:   1 worker,  2 fetch
        warn:       3 workers, 5 fetch
        soft_warn:  5 workers, 10 fetch
        ok:         5 workers, 20 fetch

    Usage:
        pool = AdaptiveWorkerPool()
        decision = await pool.evaluate()  # samples UMA, applies atomically
        fetch_limit = pool.get_fetch_limit()
        max_workers = pool.get_max_workers()
    """

    _instance: AdaptiveWorkerPool | None = None
    _instance_lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._fetch_limit: int = 25
        self._max_workers: int = 5
        self._uma_state: str = "ok"
        self._io_only: bool = False
        self._last_evaluate: float = 0.0
        self._lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> AdaptiveWorkerPool:
        """Get or create the singleton instance (async-safe)."""
        if cls._instance is None:
            async with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def evaluate(self) -> GovernorDecision:
        """
        Sample UMA state via M1ResourceGovernor and apply scaling decisions.

        Applies atomically to all semaphore pools:
        - _FETCH_SEMAPHORE (clearnet + generic fetch)
        - _clearnet_semaphore (dedicated clearnet pool)
        - _tor_semaphore (tor pool — fixed ratio vs clearnet)

        Returns GovernorDecision for caller convenience.
        """
        async with self._lock:
            from core.resource_governor import M1ResourceGovernor

            governor = M1ResourceGovernor(cache_ttl_s=2.0)
            decision = await governor.evaluate()

            self._uma_state = decision.uma_state
            self._io_only = decision.io_only
            self._fetch_limit = decision.fetch_limit

            # Derive max_workers from ConcurrencyPreset (already computed by governor)
            from core.resource_governor import ConcurrencyPreset
            preset = ConcurrencyPreset.from_state(decision.uma_state)
            self._max_workers = preset.max_workers

            # Apply fetch limit to semaphore pools
            await self._apply_fetch_limit(decision.fetch_limit)

            self._last_evaluate = time.monotonic()
            logger.debug(
                f"[AdaptiveWorkerPool] state={decision.uma_state} "
                f"fetch={decision.fetch_limit} workers={self._max_workers} "
                f"io_only={decision.io_only}"
            )

            return decision

    async def _apply_fetch_limit(self, new_limit: int) -> None:
        """Apply fetch_limit to all semaphore pools atomically via resize()."""
        global _FETCH_SEMAPHORE, _clearnet_semaphore, _tor_semaphore

        old_fetch = _FETCH_SEMAPHORE.max if _FETCH_SEMAPHORE else 0
        old_clearnet = _clearnet_semaphore.max if _clearnet_semaphore else 0

        # Tor semaphore: fixed 1/5 ratio vs clearnet (F191B invariant)
        tor_limit = max(1, new_limit // 5)

        # Use safe resize() — holds lock, updates atomically, notifies waiters on grow
        if _FETCH_SEMAPHORE is not None:
            await _FETCH_SEMAPHORE.resize(new_limit)
        if _clearnet_semaphore is not None:
            await _clearnet_semaphore.resize(new_limit)
        if _tor_semaphore is not None:
            await _tor_semaphore.resize(tor_limit)

        logger.info(
            f"[AdaptiveWorkerPool] Applied fetch {old_fetch}→{new_limit}, "
            f"clearnet {old_clearnet}→{new_limit}, tor→{tor_limit}"
        )

    def get_fetch_limit(self) -> int:
        """Current fetch_limit (from last evaluate, or default 25)."""
        return self._fetch_limit

    def get_max_workers(self) -> int:
        """
        Current max_workers for ML job parallelism.
        Returns 0 when in io_only or emergency state (no ML jobs allowed).
        """
        if self._io_only or self._uma_state == "emergency":
            return 0
        return self._max_workers

    def get_uma_state(self) -> str:
        """Last observed UMA state string."""
        return self._uma_state

    def is_io_only(self) -> bool:
        """True if I/O-only mode active (no CPU-intensive ML work)."""
        return self._io_only

    def get_adjusted_ml_jobs(self, requested: int) -> int:
        """
        Adjust requested ML job count down to fit current UMA constraints.

        Args:
            requested: Caller's desired job count (e.g., batch_size from config)

        Returns:
            Adjusted job count bounded by get_max_workers()
        """
        return min(requested, self.get_max_workers())
