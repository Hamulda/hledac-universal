"""
FetchCoordinator - Delegates fetch/crawl pipeline to coordinator
================================================================

Implements the stable coordinator interface (start/step/shutdown) for:
- URL frontier selection
- Network fetch with security checks
- Evidence creation and storage

This enables the orchestrator to become a thin "spine" that delegates
fetch logic to this coordinator.
"""
from __future__ import annotations



import asyncio
import ipaddress
import json
import logging
import os
import random

from runtime.logging_setup import get_logger  # Issue #33: structlog hot-path
import socket
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

import msgspec

import httpx  # F3XX: httpx.URL replaces urllib.parse.urlparse for URL parsing
from tenacity import wait_exponential_jitter

# F350M-R: Centralized optional dependency registry — core/capabilities.py
from core.capabilities import CAPS, OTEL, ZSTD, AIOHTTP, LIGHTPANDA, SESSION, HINTS, PAYWALL_BYPASS, DARKNET_CONNECTOR, ZERO_ATTR, STEALTH_MANAGER

# Resolve each capability once at module load; results cached in CAPS
_otel_mod = CAPS.require(OTEL)
if _otel_mod is not None:
    _otel_instrumented = _otel_mod
else:
    from hledac.universal.telemetry import instrumented as _otel_instrumented  # type: ignore[no-redef]

_zstd_mod = CAPS.require(ZSTD)  # zstandard.zstd or None
_aiomod = CAPS.require(AIOHTTP)  # aiohttp module or None
_lp_manager_cls = CAPS.require(LIGHTPANDA)  # LightpandaManager class or None
_session_mgr_cls = CAPS.require(SESSION)  # SessionManager class or None
_hints_extractor_cls = CAPS.require(HINTS)  # DeepWebHintsExtractor class or None

# Backward-compat availability flags — existing code uses these names
ZSTD_AVAILABLE = CAPS.is_available("zstd")
AIOHTTP_AVAILABLE = CAPS.is_available("aiohttp")
LIGHTPANDA_AVAILABLE = CAPS.is_available("lightpanda")
SESSION_AVAILABLE = CAPS.is_available("session")
HINTS_AVAILABLE = CAPS.is_available("deep_web_hints")

# Sprint 41: zstd compression — re-exported from tools/zstd_compressor
# F281: Privacy compute budget allocator
from hledac.universal.runtime.privacy_budget import (  # noqa: E402
    PrivacyBudgetAllocator,
    make_privacy_allocator,
)
from hledac.universal.tools.zstd_compressor import ZstdCompressor  # noqa: E402
from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok  # noqa: E402

# F270: Canonical constants for magic numbers
from hledac.universal.core.constants import RATIOS, HTTP  # noqa: E402

from ..tools.url_dedup import DeduplicationStrategy  # noqa: E402
from .base import UniversalCoordinator  # noqa: E402

# Sprint F214: Zero-attribution HTTP header randomization
# F350M-R: centralized via CapabilityRegistry
_zero_attr_cls = CAPS.require(ZERO_ATTR)
_ZERO_ATTR_ENGINE = _zero_attr_cls


# Sprint F214Q: Cover traffic probabilistic inline injection
# Pattern: probabilistic inline (not background task — too complex for M1)
# Rate: 15% chance after each successful real fetch
# Limit: max 2 cover traffic requests per sprint (M1 RAM protection)
# Invariant: cover traffic MUST use identical transport as real request
_COVER_RATE = float(os.environ.get("HLEDAC_COVER_TRAFFIC_RATE", "0.05"))
_COVER_RATE = min(max(_COVER_RATE, 0.0), 1.0)  # rate guard: clamp to [0.0, 1.0]
_COVER_MAX = 2  # max cover traffic fires per sprint


def _create_dedup_strategy():
    # type: () -> DeduplicationStrategy
    """Create the dedup strategy used by FetchCoordinator.

    P1-3: Uses create_rotating_bloom_filter() which prefers MmapBloomFilter
    (file-backed, cross-restart persistence) when Rust extension available.
    Falls back to in-memory Rust BloomFilter then probables.
    The mmap filter persists across restarts so re-fetching the same URL
    in a later sprint doesn't re-download it.
    """
    from ..tools.url_dedup import create_rotating_bloom_filter
    return create_rotating_bloom_filter(est_elements=200_000)


# Issue #19: Two-sentinel pattern for _batch_cp_result:
#   _CP_NOT_CALLED    — provider has not been called yet this batch
#   _CP_RETURNED_NONE — provider was called and returned None (valid "inactive" signal)
_CP_NOT_CALLED = object()
_CP_RETURNED_NONE = object()
# Sprint F-A5: Pre-fetch URL dedup gate. Runs the dedup ONCE on the
# candidate list before submission so the fetch loop only sees unique
# URLs (eliminates the per-URL Bloom check overhead inside the loop).
from ..tools.url_dedup import dedupe_url_list  # noqa: E402
from ..utils.async_helpers import async_getaddrinfo, BoundedPerHostGate  # noqa: E402

# Sprint F-A4: Bounded batch DNS resolver (LRU + parallel via c-ares).
# Used in run_step to pre-resolve all unique hostnames for the batch
# so _validate_fetch_target can skip the per-URL getaddrinfo call.
from ..utils.batch_dns import get_batch_dns_resolver  # noqa: E402

# Sprint 8C1: Flow trace
from ..utils.flow_trace import (  # noqa: E402
    is_enabled,
    trace_counter,
    trace_dedup_decision,
    trace_fetch_end,
    trace_fetch_start,
)

# Sprint 80: TokenBucketController — inline fallback when stealth manager unavailable
# Sprint 80: TokenBucketController — inline fallback when stealth manager unavailable
# F350M-R: centralized via CapabilityRegistry with inline fallback
_stealth_tbc = CAPS.require(STEALTH_MANAGER)
if _stealth_tbc is None:

    class TokenBucketController:  # type: ignore[no-redef,misc]
        """Token Bucket pro řízení concurrency (inline fallback)."""

        __slots__ = ("_rate", "_capacity", "_tokens", "_last_refill", "_cond")

        def __init__(self, rate: int = 5, capacity: int = 10):
            self._rate = rate
            self._capacity = capacity
            self._tokens = capacity
            self._last_refill = time.time()
            self._cond = asyncio.Condition()

        async def acquire(self):
            async with self._cond:
                while True:
                    now = time.time()
                    elapsed = now - self._last_refill
                    new_tokens = int(elapsed * self._rate)
                    if new_tokens > 0:
                        self._tokens = min(self._capacity, self._tokens + new_tokens)
                        self._last_refill = now
                    if self._tokens >= 1:
                        self._tokens -= 1
                        return
                    await self._cond.wait()

        async def release(self):
            pass
else:
    TokenBucketController = _stealth_tbc

logger = get_logger(__name__)


# =============================================================================
# Sprint 4B: TIMEOUT MATRIX
# Canonical timeouts for fetch runtime — imported from core.constants (SSOT)
# Issue #48: moved to core/constants.py NetworkTimeouts
# =============================================================================
from core.constants import NETWORK  # noqa: E402

# Aliasy pro backward-compat s existujícími call sites
# NETWORK je lazy singleton factory — musíme zavolat ()
_nw = NETWORK()
TIMEOUT_CLEARNET_API = _nw.clearnet_api
TIMEOUT_CLEARNET_HTML = _nw.clearnet_html
TIMEOUT_TOR = _nw.tor
TIMEOUT_I2P = _nw.i2p
TIMEOUT_GOPHER = _nw.gopher

# =============================================================================
# Sprint 4B: CONCURRENCY MATRIX
# Explicit limits per transport class
# =============================================================================
CONCURRENCY_TOR = 4          # concurrent Tor requests
CONCURRENCY_CLEARNET = 12   # concurrent clearnet requests
CONCURRENCY_API = 5          # concurrent API requests
CONCURRENCY_GLOBAL_MAX = 25  # absolute global cap

# =============================================================================
# Sprint 4B: AIMD PARAMETERS
# Additive Increase / Multiplicative Decrease for adaptive concurrency
# =============================================================================
AIMD_ADDITIVE_INCREMENT = 2    # F290: was 1 — faster ramp-up, M1 8GB handles it
AIMD_DECREASE_FACTOR = 0.75    # multiply by this on failure (25% reduction)
AIMD_MIN_CONCURRENCY = 1      # floor
AIMD_MAX_CONCURRENCY = 25     # ceiling (matches GLOBAL_MAX)
AIMD_SUCCESS_THRESHOLD = 2    # F290: was 3 — faster ramp-up, reach useful concurrency sooner

# Sprint F265B + F289: S3 — State-differentiated decrease factors
# F289: SSOT moved to ConcurrencyPreset.aimd_decrease_factor (core/resource_governor.py)
# Keeping dict alias for backward compat with existing call sites

AIMD_DECREASE_BY_STATE = {
    "ok": 1.0,
    "soft_warn": 0.75,
    "warn": 0.5,
    "critical": 0.25,
    "emergency": 0.0,
}
# F289: Preferred access — ConcurrencyPreset.from_state(state).aimd_decrease_factor


class AIMDWindow:
    """
    Thread-safe AIMD window controller with atomic counter semantics.

    Replaces raw _aimd_successes / _aimd_failures / _aimd_concurrency fields
    in FetchCoordinator. All state mutations happen under a single asyncio.Lock
    to prevent race conditions when 100+ coroutines complete simultaneously.

    Key invariants:
    - on_success() and on_failure() are mutually exclusive under the same lock.
    - window increase happens exactly once per threshold crossing.
    - Telemetry counters are updated atomically with window changes.

    M1 8GB: ~0 bytes extra RAM (replaces 3 fields with 1 object).
    """

    # Lock-free fast path: separate window update lock from counter ops
    __slots__ = ("_window", "_successes", "_failures", "_stats", "_lock", "_window_lock")

    def __init__(self, initial: float) -> None:
        self._window = float(initial)
        self._successes = 0
        self._failures = 0
        self._stats: dict[str, int] = {
            "increases": 0,
            "decreases": 0,
            "window_changes": 0,
        }
        self._lock = asyncio.Lock()  # guards _successes, _failures
        self._window_lock = asyncio.Lock()  # guards window updates

    # -------------------------------------------------------------------------
    # Lock-free fast path: counter increment without lock acquisition.
    # Python GIL makes plain int assign atomic — CAS loop is safe.
    # Only the winner coroutine (threshold crossed) acquires _window_lock.
    # -------------------------------------------------------------------------

    def _cas_successes(self, expected: int) -> tuple[int, bool]:
        """
        Compare-and-swap for _successes counter.
        Returns (current_value_after_attempt, swapped: bool).

        Lock-free: under GIL, reading self._successes and assigning to it
        are atomic for plain int objects. The CAS loop handles concurrent
        modifications by other coroutines.
        """
        if self._successes == expected:
            self._successes = expected + 1
            return expected + 1, True
        return self._successes, False

    async def on_success(self, multiplier: float = 1.0) -> tuple[float, int]:
        """
        Record one success, potentially increasing the window.

        Fast path (no lock): 99% of calls — counter increment + threshold check
        only. Lock is NOT acquired unless threshold is crossed and window update
        is needed.

        Lock-free CAS loop avoids 50-100 µs lock acquisition overhead per call
        when 100+ coroutines complete simultaneously (Issue #15 partial fix).
        """
        # Phase 1: Lock-free counter increment via CAS loop
        # Retry up to 5 times; fallback to lock on persistent contention
        new_successes: int
        for _ in range(5):
            current = self._successes
            new_successes, swapped = self._cas_successes(current)
            if swapped:
                break
        else:
            # Contention fallback: acquire counter lock briefly
            async with self._lock:
                self._successes += 1
                new_successes = self._successes

        # Phase 2: Threshold check — lock-free read of _successes
        # If threshold NOT crossed (99% case), return immediately.
        # No lock acquired — counter is stable, window unchanged.
        if new_successes < AIMD_SUCCESS_THRESHOLD:
            return self._window, new_successes

        # Phase 3: Threshold crossed — exactly one winner acquires window lock.
        # Other coroutines see successes >= threshold but window is already
        # being updated; they get the updated window in the return.
        async with self._window_lock:
            # Re-check inside lock: another coroutine may have already updated
            if self._successes < AIMD_SUCCESS_THRESHOLD:
                return self._window, self._successes

            self._successes = 0
            old = self._window
            self._window = min(
                self._window + AIMD_ADDITIVE_INCREMENT * multiplier,
                AIMD_MAX_CONCURRENCY,
            )
            if self._window != old:
                self._stats["increases"] += 1
                self._stats["window_changes"] += 1
            return self._window, 0

    async def on_failure(self, uma_state: str = "ok") -> tuple[float, int]:
        """
        Record one failure, decreasing the window multiplicatively.

        Uses _lock for counter, _window_lock for window update (both needed
        since _successes is also touched here to reset the counter).
        """
        async with self._lock:
            self._failures += 1
            new_failures = self._failures

        async with self._window_lock:
            decrease_factor = AIMD_DECREASE_BY_STATE.get(uma_state, 1.0)
            old = self._window
            self._window = max(
                self._window * decrease_factor,
                AIMD_MIN_CONCURRENCY,
            )
            if self._window != old:
                self._stats["decreases"] += 1
                self._stats["window_changes"] += 1
                # Reset success counter — now under _window_lock for atomicity
                # with window update (on_success also touches _successes)
                self._successes = 0

        return self._window, new_failures

    @property
    def window(self) -> float:
        return self._window

    @property
    def successes(self) -> int:
        return self._successes

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def stats(self) -> dict[str, int]:
        return self._stats.copy()

    async def set_window(self, new_window: float) -> None:
        """Set window directly (for backpressure clamping)."""
        async with self._window_lock:
            self._window = float(new_window)
            self._stats["window_changes"] += 1

    def reset_successes(self) -> None:
        """Reset success counter (called externally after window increase)."""
        self._successes = 0


# ============================================================================
# AIMD Slot Controller — asyncio.Semaphore + dynamic bound (Issue #5, 2026-07-07)
# ============================================================================
# Replaces: (1) broken spin-CAS loop, (2) semaphore-swap permit-leak bug.
# Window updates are O(1): we raise the semaphore bound instead of swapping
# the semaphore object, so held permits are never lost.
#
# semantics:
#   _sem  : asyncio.Semaphore — slot allocator (acquire/release)
#   _cond : asyncio.Condition  — window-change notification
#   window: maximum total slots (active + queued)
#
# window shrink : do NOT lower the bound — permits drain naturally via release()
# window grow   : raise _sem limit by delta (via _sem._value write)


class _AIMDSlotController:
    """
    AIMD slot controller with asyncio.Semaphore and dynamic bound updates.

    Uses a single asyncio.Semaphore for slot allocation and a separate
    asyncio.Condition for window-change notification.  This replaces the
    broken spin-CAS loop (which blocked the event loop) and the
    semaphore-swap pattern (which caused permit leaks on window changes).

    Python 3.11+ asyncio.Semaphore is internally lock-free for the fast
    path (available permit), so the fast path never blocks the event loop.

    Window updates are O(1): we raise the semaphore bound instead of
    replacing the semaphore object, so held permits are never lost.
    """

    __slots__ = ("_sem", "_cond", "_window", "_stats")

    def __init__(self, initial_window: int) -> None:
        self._sem: asyncio.Semaphore = asyncio.Semaphore(initial_window)
        self._cond: asyncio.Condition = asyncio.Condition()
        self._window: int = initial_window
        self._stats: dict[str, int] = {
            "acquired": 0,
            "released": 0,
            "waiters_peak": 0,
            "window_updates": 0,
        }

    async def acquire(self) -> None:
        """Acquire one slot. Blocks (yields) if window is full."""
        await self._sem.acquire()
        self._stats["acquired"] += 1

    def release(self) -> None:
        """Release one slot, waking a waiter if one is blocked."""
        self._sem.release()
        self._stats["released"] += 1

    async def update_window(self, new_window: int) -> None:
        """
        Adjust AIMD window bound atomically.

        Grow  (delta > 0): raise semaphore limit; waiters wake naturally via release()
        Shrink (delta < 0): do NOT lower the semaphore bound — doing so would
                             cause held permits to vanish (semaphore semantics).
                             Instead, the semaphore "drains" passively as active
                             holders call release() and new acquirers are capped
                             by the new, lower window.

        This is safe because:
        - Old permits drain through natural release() calls
        - New acquire() calls are immediately capped at new_window
        - The semaphore internal counter only ever goes up (never artificially lowered)
        """
        if new_window == self._window:
            return

        delta = new_window - self._window
        self._window = new_window
        self._stats["window_updates"] += 1

        if delta > 0:
            # Grow: raise the semaphore bound via release() for delta permits.
            # This is safe — release() adds to _value and wakes waiters.
            for _ in range(delta):
                self._sem.release()
            # Wake waiters so they can re-check after the bound change.
            async with self._cond:
                wc = self._cond.waiter_count() if hasattr(self._cond, "waiter_count") else 0
                self._cond.notify(min(delta, wc))  # type: ignore[arg-type]
        # delta < 0: semaphore bound unchanged — natural drain handles the rest.

    @property
    def stats(self) -> dict[str, int]:
        return self._stats

    @property
    def window(self) -> int:
        return self._window

    @property
    def available(self) -> int:
        """Approximate available slots (not guaranteed atomic)."""
        # Use _window - acquired = window - (window - remaining) = remaining
        # Since _sem._value is internal and may not reflect true state after resize,
        # we approximate using _window minus acquired count
        acquired = self._window - (self._sem._value if hasattr(self._sem, "_value") else 0)
        return max(0, self._window - acquired)

    @property
    def waiters(self) -> int:
        """Approximate waiter count (not guaranteed atomic)."""
        return self._cond.waiter_count() if hasattr(self._cond, "waiter_count") else 0  # type: ignore[attr-defined]


# LOW-2 fix: URL priority constants (lower = higher priority)
_PRIORITY_API = 0           # API endpoints (highest priority)
_PRIORITY_JSON = 5           # Structured data (JSON/XML/RSS)
_PRIORITY_CLEARNET_HTML = 15 # Standard clearnet HTML
_PRIORITY_TOR = 30           # Tor hidden services
_PRIORITY_I2P = 40           # I2P hidden services
_PRIORITY_OTHER = 50         # Fallback for exotic TLDs

# Maximum evidence IDs to return per step (bounded output)
MAX_EVIDENCE_IDS_PER_STEP = 10

# Darwin F_NOCACHE constants for large file downloads (>50MB)
# F_NOCACHE = 48 tells the kernel not to cache the file data (optimization for large downloads)
# LOW-6/LOW-7 fix: moved fcntl import to module level with platform check
import platform  # noqa: E402

NOCACHE_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50MB
F_NOCACHE = 48 if platform.system() == "Darwin" else None

# Re-exported from tools/file_cache.py for backward compatibility
from hledac.universal.tools.file_cache import apply_fcntl_nocache as _apply_fcntl_nocache  # noqa: E402


def apply_fcntl_nocache(fd: int, content_length: int | None) -> None:
    """Wrapper for backward compatibility — delegates to tools/file_cache.py."""
    _apply_fcntl_nocache(fd, content_length)


class FetchCoordinatorConfig(msgspec.Struct, frozen=True, gc=False):
    """Configuration for FetchCoordinator."""
    max_urls_per_step: int = 5
    max_evidence_per_step: int = 10
    enable_security_check: bool = True
    enable_domain_limiter: bool = True
    budget_network_calls: int = 50
    budget_snapshots: int = 20


# Sprint 45: Lightpanda Pool — re-exported from tools/lightpanda_pool
from hledac.universal.tools.lightpanda_pool import LightpandaPool  # noqa: E402


class FetchCoordinator(UniversalCoordinator):
    """
    Coordinator for fetch/crawl pipeline delegation.

    Responsibilities:
    - Pop URLs from frontier (bounded)
    - Run fetch pipeline with security checks
    - Create evidence packets
    - Return bounded outputs (IDs, counts, stop signals)
    """

    def __init__(
        self,
        config: FetchCoordinatorConfig | None = None,
        max_concurrent: int = 3,
        # Sprint F-EXTRACT-2: pivot queue + hypothesis state providers.
        # All optional with safe defaults; SprintScheduler wires real providers.
        pivot_queue_provider: Callable[[], Any] = lambda: None,
        pivot_stats_provider: Callable[[], dict] | None = None,
        hypothesis_query_count_provider: Callable[[], int] = lambda: 0,
        hypothesis_query_count_setter: Callable[[int], None] = lambda v: None,
        hypothesis_depth_provider: Callable[[], int] = lambda: 0,
        hypothesis_depth_setter: Callable[[int], None] = lambda v: None,
        sprint_config_provider: Callable[[], Any] = lambda: None,
        adaptive_priority_provider: Callable[[str, float], float] = lambda tt, base: base,
        # Sprint F-EXTRACT-2: callback to SprintScheduler.enqueue_pivot.
        # Bound to `self` (SprintScheduler) at construction so test-patch
        # pattern `scheduler.enqueue_pivot = mock` resolves correctly.
        enqueue_pivot_provider: Callable[..., Any] = lambda **kw: None,
        # Sprint 6.4: Backpressure provider — returns (clearnet_max, stealth_max, uma_state, io_only).
        # None means backpressure is inactive (AIMD governs itself).
        concurrency_provider: Callable[[], tuple[int, int, str, bool] | None] | None = None,
        # P0-FIX: Sprint-budget-aware circuit breaker — provides remaining sprint time
        # so record_failure() can ceiling recovery_timeout to min(sprint_remaining/2, MAX).
        # This prevents 420s blocking during a 300s sprint.
        sprint_remaining_provider: Callable[[], float | None] = lambda: None,
    ):
        super().__init__(name="FetchCoordinator", max_concurrent=max_concurrent)
        self._config = config or FetchCoordinatorConfig()
        # Sprint F-EXTRACT-2: store provider callbacks
        self._pivot_queue_provider = pivot_queue_provider
        self._pivot_stats_provider = pivot_stats_provider
        self._hypothesis_query_count_provider = hypothesis_query_count_provider
        self._hypothesis_query_count_setter = hypothesis_query_count_setter
        self._hypothesis_depth_provider = hypothesis_depth_provider
        self._hypothesis_depth_setter = hypothesis_depth_setter
        self._sprint_config_provider = sprint_config_provider
        self._adaptive_priority_provider = adaptive_priority_provider
        # P0-FIX: Sprint-budget-aware circuit breaker provider
        self._sprint_remaining_provider = sprint_remaining_provider
        self._enqueue_pivot_provider = enqueue_pivot_provider
        # Sprint 6.4: Backpressure provider — called on each _aimd_acquire() to clamp window
        self._concurrency_provider = concurrency_provider
        # Issue #19: Batch-local memoization cache for _concurrency_provider() result
        self._batch_cp_result = _CP_NOT_CALLED

        # State
        self._frontier: deque = deque(maxlen=1000)
        self._processed_urls: DeduplicationStrategy = _create_dedup_strategy()
        self._evidence_ids: deque = deque(maxlen=500)
        self._urls_fetched_count: int = 0
        self._stop_reason: str | None = None

        # Sprint F-A4: per-batch host→IPs cache. Populated by run_step
        # via the batch DNS resolver, consumed by _validate_fetch_target.
        # Reset at the start of every run_step so stale entries from a
        # prior batch never leak through (DNS rebinding defense requires
        # fresh resolution per fetch).
        self._host_ips_cache: dict[str, list[str]] = {}

        # Per-domain circuit breaker (Sprint F195C)
        self._domain_failures: dict[str, int] = {}
        self._domain_failure_timestamps: dict[str, float] = {}  # for eviction (P2-1)
        self._domain_blocked_until: dict[str, float] = {}
        self._failure_threshold = 3
        self._cooldown_seconds = 60
        self._base_retry_delay = 1.0  # Sprint F195C: circuit breaker retry config
        self._max_retries = 3
        self._max_backoff_delay = 30.0

        # Orchestrator reference (set via start)
        self._orchestrator: Any | None = None
        self._ctx: dict[str, Any] = {}

        # Sprint 39: Deep web hints extractor (F350M-R: via CAPS)
        self._hints_extractor = _hints_extractor_cls() if _hints_extractor_cls else None

        # Sprint 41: zstd compression
        self._zstd = ZstdCompressor()

        # Sprint 44/45: Lightpanda manager for JS-heavy pages (F350M-R: via CAPS)
        # Note: LightpandaManager is a single-process manager, not a pool.
        # size=2 was removed — LightpandaManager.__init__() takes no arguments.
        self._lightpanda_pool = _lp_manager_cls() if _lp_manager_cls else None
        self._lightpanda_pool_started = False
        self._lightpanda_lock = asyncio.Lock()  # P1-1: thread-safe pool init
        self._geo_proxies = self._load_geo_proxies()
        self._current_geo_context = None  # set by caller

        # Sprint 46: Session management (F350M-R: via CAPS)
        # Note: SessionManager is created in init_session_manager() with lmdb_env (line ~948)
        self._session_lmdb_env: Any = None
        self._session_manager: Any = None  # created in init_session_manager()
        self._session_checkpoint_task: asyncio.Task | None = None  # Issue #20: periodic LMDB sync
        self._running: bool = False  # Issue #20: checkpoint loop gate
        _paywall_cls = CAPS.require(PAYWALL_BYPASS)
        _darknet_cls = CAPS.require(DARKNET_CONNECTOR)
        self._paywall_bypass = _paywall_cls() if _paywall_cls else None
        self._darknet_connector = _darknet_cls() if _darknet_cls else None

        # F274: Tor session lifecycle now via darknet_session_provider (singleton)
        # _tor_sessions, _tor_last_used, _tor_lock removed — owned by transport layer

        # F281: Privacy compute budget allocator — 15% of AIMD window for privacy lanes
        self._privacy_allocator: PrivacyBudgetAllocator | None = None
        self._privacy_lock = asyncio.Lock()

        # Sprint F214: TorTransport opt-in backend (HLEDAC_ENABLE_TOR=1)
        self._tor_transport: Any = None
        self._tor_transport_enabled: bool = False
        if os.environ.get("HLEDAC_ENABLE_TOR") == "1":
            try:
                from ..transport.tor_transport import TorTransport
                self._tor_transport = TorTransport()
                self._tor_transport_enabled = self._tor_transport.available
                if self._tor_transport_enabled:
                    logger.info("TorTransport enabled via HLEDAC_ENABLE_TOR=1")
                    logger.info("  Circuit rotation after {self._tor_transport._max_circuit_requests} requests", _max_circuit_requests=self._tor_transport._max_circuit_requests)
            except Exception as e:
                logger.warning("TorTransport init failed: {e}", e=e)
                self._tor_transport_enabled = False

        # Sprint F214: GopherTransport opt-in backend (HLEDAC_ENABLE_GOPHER=1)
        self._gopher_transport: Any = None
        self._gopher_transport_enabled: bool = False
        if os.environ.get("HLEDAC_ENABLE_GOPHER") == "1":
            try:
                from ..transport.gopher_transport import GopherTransport
                self._gopher_transport = GopherTransport()
                self._gopher_transport_enabled = True
                logger.info("GopherTransport enabled via HLEDAC_ENABLE_GOPHER=1")
            except Exception as e:
                logger.warning("GopherTransport init failed: {e}", e=e)
                self._gopher_transport_enabled = False

        # F-HTTP-CACHE: hishel HTTP response cache, opt-out via HLEDAC_HTTP_CACHE=0.
        # Wraps httpx-compatible transports; aiohttp paths bypass it (hishel is
        # httpx-only). Built lazily in _do_initialize (build_cache_transport is
        # async); __init__ only declares state + the env-gate decision.
        # Fail-soft: missing hishel → transport stays None and httpx paths
        # operate uncached. URL canonicalisation is the caller's job
        # (use tools/url_dedup.normalize_url before lookup).
        self._http_cache_transport: Any = None
        self._http_cache_enabled: bool = (
            os.environ.get("HLEDAC_HTTP_CACHE", "1") != "0"
        )

        # Sprint P3: CAPTCHA pre-filter (gated, PIL-only heuristics)
        self._captcha_detector: Any | None = None
        self._captcha_detections: int = 0
        if os.environ.get("HLEDAC_ENABLE_CAPTCHA_DETECTION") == "1":
            try:
                from ..security.captcha_detector import CaptchaDetector
                self._captcha_detector = CaptchaDetector()
                logger.info("CaptchaDetector enabled via HLEDAC_ENABLE_CAPTCHA_DETECTION=1")
            except Exception as e:
                logger.warning("CaptchaDetector init failed: {e}", e=e)
                self._captcha_detector = None

        # Sprint F214AD: Race condition guard for dedup check+add
        self._dedup_lock = asyncio.Lock()

        # F274: I2P session lifecycle now via darknet_session_provider (singleton)
        # _i2p_sessions, _i2p_last_used, _i2p_lock removed — owned by transport layer

        # Sprint 80: Token bucket concurrency (still kept for compatibility)
        self._concurrency = TokenBucketController(rate=5, capacity=10)

        # Sprint 4B + Issue #15: AIMD Adaptive Concurrency Controller with thread-safe counter
        # AIMDWindow replaces raw _aimd_successes/_aimd_failures/_aimd_concurrency fields.
        # All mutations happen under a single lock to prevent race conditions when
        # 100+ coroutines complete simultaneously and would otherwise all increment
        # _aimd_successes without seeing each other's updates → spurious window increases.
        self._aimd_window = AIMDWindow(initial=float(CONCURRENCY_CLEARNET))
        self._aimd_slot: _AIMDSlotController = _AIMDSlotController(initial_window=int(CONCURRENCY_CLEARNET))

        # Sprint F320: Per-host circuit breaker — limits concurrent fetches per hostname
        # to prevent overwhelming specific servers while maintaining global AIMD window.
        # BoundedPerHostGate replaces unbounded defaultdict(Semaphore) — LRU evict
        # when over max_hosts, preventing 100k-host sprints from creating 100k
        # Semaphore objects (~25 MB RAM on M1 8GB).
        self._per_host_limit = 4  # max concurrent fetches per unique hostname
        self._per_host_gate = BoundedPerHostGate(max_hosts=512, per_host_limit=self._per_host_limit)

        # Sprint 4B + Issue #15: Telemetry state
        # aimd_concurrency is now read from _aimd_window.window (not stored separately)
        self._telemetry: dict[str, Any] = {
            'aimd_concurrency': self._aimd_window.window,
            'active_fetches': 0,
            'total_successes': 0,
            'total_failures': 0,
            # P1-13: Circuit breaker metrics
            'circuit_breaker_blocks': 0,
            'circuit_breaker_active': 0,
            # S3: State-differentiated decrease telemetry
            'uma_state': 'ok',
            'decrease_factor_used': 1.0,
            # S7: Backpressure clamp counter
            'backpressure_clamp_events': 0,
        }

        # Sprint F214Q: Cover traffic counter (reset each sprint via reset_sprint_state())
        self._cover_count: int = 0

        # F206AS: Canonical circuit breaker adapter (lazy, fail-soft)
        # References canonical transport/circuit_breaker.py domain_breaker_check / record functions
        self._canonical_breaker: Any = None
        self._canonical_breaker_available: bool = False
        self._canonical_breaker_checked: int = 0
        self._canonical_breaker_blocks: int = 0
        self._canonical_breaker_fallback_used: int = 0
        self._canonical_breaker_lock = __import__('threading').Lock()

        # F272: Auto-init session manager on construction so health check
        # (_session_manager being None) doesn't return not_initialized when
        # sessions are actually available. Idempotent — safe to call.
        self.init_session_manager()

    def _ensure_canonical_breaker(self) -> tuple[bool, Any, str]:
        """
        Lazily import canonical circuit breaker from transport/circuit_breaker.py.

        Returns:
            (available, breaker_module, fallback_reason)
            - available: True if canonical breaker is importable and loaded
            - breaker_module: the imported module (or None)
            - fallback_reason: why canonical was not used (empty if available)
        """
        if self._canonical_breaker_checked:
            return (
                self._canonical_breaker_available,
                self._canonical_breaker,
                getattr(self, '_canonical_breaker_fallback_reason', 'already_checked'),
            )

        with self._canonical_breaker_lock:
            # Double-check after acquiring lock (another thread may have initialized)
            if self._canonical_breaker_checked:
                return (
                    self._canonical_breaker_available,
                    self._canonical_breaker,
                    getattr(self, '_canonical_breaker_fallback_reason', 'already_checked'),
                )

            self._canonical_breaker_checked = True

            try:
                from transport import circuit_breaker
                # Verify it has the canonical API we need
                if hasattr(circuit_breaker, 'domain_breaker_check') and hasattr(circuit_breaker, 'get_breaker'):
                    self._canonical_breaker = circuit_breaker
                    self._canonical_breaker_available = True
                    return (True, circuit_breaker, '')
                else:
                    self._canonical_breaker_fallback_reason = 'missing_canonical_api'
                    return (False, None, 'missing_canonical_api')
            except ImportError:
                self._canonical_breaker_fallback_reason = 'import_failed'
                return (False, None, 'import_failed')
            except Exception as e:
                self._canonical_breaker_fallback_reason = f'unexpected_error:{e}'
                return (False, None, f'unexpected_error:{e}')

    def _check_canonical_breaker(self, domain: str) -> tuple[bool, str, float]:
        """
        Check canonical circuit breaker for a domain.

        Returns:
            (allowed, reason, retry_after_s)
            - allowed: True if fetch is allowed (circuit closed or half-open)
            - reason: human-readable reason for the decision
            - retry_after_s: seconds to wait if circuit is open
        """
        available, cb_module, fallback_reason = self._ensure_canonical_breaker()

        if not available:
            return (True, f'canonical_unavailable:{fallback_reason}', 0.0)

        try:
            decision = cb_module.domain_breaker_check(domain)
            if not decision.allowed:
                self._canonical_breaker_blocks += 1
            return (decision.allowed, decision.reason, decision.retry_after_s)
        except Exception as e:
            self._canonical_breaker_fallback_used += 1
            return (True, f'canonical_check_error:{e}', 0.0)

    def _record_canonical_success(self, domain: str) -> None:
        """Record fetch success to canonical circuit breaker if available."""
        available, cb_module, _ = self._ensure_canonical_breaker()
        if not available:
            return
        try:
            cb_module.get_breaker(domain).record_success()
        except Exception:
            self._canonical_breaker_fallback_used += 1

    def _record_canonical_failure(self, domain: str, is_timeout: bool = False, failure_kind: str = '') -> None:
        """Record fetch failure to canonical circuit breaker if available."""
        available, cb_module, _ = self._ensure_canonical_breaker()
        if not available:
            return
        try:
            # P0-FIX: Pass sprint_remaining_s so circuit breaker can ceiling
            # recovery_timeout to min(sprint_remaining/2, MAX) — prevents 420s
            # blocking during a 300s sprint.
            sprint_remaining = None
            if self._sprint_remaining_provider is not None:
                try:
                    sprint_remaining = self._sprint_remaining_provider()
                except Exception:  # noqa: BLE001
                    pass  # fail-soft: provider may not be available
            cb_module.get_breaker(domain).record_failure(
                is_timeout=is_timeout,
                failure_kind=failure_kind or 'fetch_error',
                sprint_remaining_s=sprint_remaining,
            )
        except Exception:
            self._canonical_breaker_fallback_used += 1

    async def _record_domain_failure(self, domain: str) -> None:
        """Record a failure for a domain; block it after _failure_threshold failures."""
        # P2-1: Evict stale entries if dict grows too large
        if len(self._domain_failures) > 1000:
            cutoff = time.time() - (24 * 3600)  # 24 hours
            stale_domains = [d for d, ts in self._domain_failure_timestamps.items() if ts < cutoff and d not in self._domain_blocked_until]  # noqa: E501
            for d in stale_domains[:len(stale_domains) // 2]:
                self._domain_failures.pop(d, None)
                self._domain_failure_timestamps.pop(d, None)

        failures = self._domain_failures.get(domain, 0) + 1
        self._domain_failures[domain] = failures
        self._domain_failure_timestamps[domain] = time.time()

        if failures >= self._failure_threshold:
            # P1-13: Check if NEW block (not refresh of existing blocked domain)
            is_new_block = domain not in self._domain_blocked_until
            backoff = min(60.0 * (2 ** (failures - self._failure_threshold)), 3600.0)
            self._domain_blocked_until[domain] = time.time() + backoff
            if is_new_block:
                # Use .get() for backward compat with manually-constructed objects
                self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
            self._telemetry['circuit_breaker_active'] = len(self.get_blocked_domains())
            logger.warning(
                f"[CIRCUIT] Domain {domain} blocked after {failures} failures "
                f"for {backoff:.0f}s (until {self._domain_blocked_until[domain]:.0f})"
            )

    def get_blocked_domains(self) -> dict[str, float]:
        """Returns {domain: unblock_timestamp} for currently blocked domains."""
        now = time.time()
        return {d: t for d, t in self._domain_blocked_until.items() if t > now}

    def get_captcha_stats(self) -> dict[str, Any]:
        """Sprint P3: Return CAPTCHA detection stats for RL telemetry."""
        return {
            'captcha_detections_total': self._captcha_detections,
            'captcha_detector_enabled': self._captcha_detector is not None,
        }

    def get_canonical_breaker_stats(self) -> dict[str, Any]:
        """
        F206AS: Return canonical circuit breaker integration stats.

        Returns telemetry about whether canonical breaker was consulted,
        whether it blocked requests, and fallback usage.
        """
        available, _, fallback_reason = self._ensure_canonical_breaker()
        result = {
            'canonical_available': available,
            'canonical_checked_count': self._canonical_breaker_checked,
            'canonical_blocks': self._canonical_breaker_blocks,
            'fallback_used_count': getattr(self, '_canonical_breaker_fallback_used', 0),
            'fallback_reason': fallback_reason if not available else '',
            'canonical_breaker_states': {},
        }
        if available and self._canonical_breaker:
            try:
                result['canonical_breaker_states'] = self._canonical_breaker.get_all_breaker_states()
            except Exception:  # noqa: BLE001
                pass  # noqa: BLE001  # fail-soft: return empty states on error
        return result

    def init_session_manager(self, lmdb_path: str | None = None):
        """Initialize session manager with LMDB persistence (idempotent)."""
        if not SESSION_AVAILABLE:
            return
        # F300M: Idempotent — early return if already initialized (prevents repeated-init leak)
        if self._session_manager is not None and self._session_lmdb_env is not None:
            return
        if lmdb_path is None:
            from hledac.universal.paths import LMDB_ROOT
            lmdb_path = str(LMDB_ROOT / 'session.lmdb')
        Path(lmdb_path).parent.mkdir(parents=True, exist_ok=True)
        # Issue #20: session LMDB uses critical=True for durable writes.
        # Session cache contains Tor circuits, cookies, auth tokens — NOT recoverable
        # from DuckDB. sync=True guarantees no >5s auth state loss on crash.
        # Periodic checkpoint (30s) ensures bounded data loss window.
        # F272: fail-soft LMDB init — on open error, session persistence disabled
        # but fetch coordinator stays functional (health check uses _session_manager)
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            self._session_lmdb_env = open_lmdb_with_guard(
                lmdb_path,
                map_size=10*1024*1024,
                readahead=False,
                critical=True,  # durable writes — session auth is not recoverable
            )
            self._session_manager = SessionManager(self._session_lmdb_env)
            # Issue #20: start periodic checkpoint loop for bounded data loss
            self._start_checkpoint_loop()
        except Exception as e:
            logger.warning("[FETCH] LMDB session init failed: {e} — session persistence disabled", e=e)
            self._session_lmdb_env = None
            self._session_manager = None

    def _load_geo_proxies(self) -> dict[str, str]:
        """Load proxy servers for different regions from configuration."""
        from hledac.universal.paths import DB_ROOT
        proxy_file = DB_ROOT / 'config' / 'proxies.json'
        if proxy_file.exists():
            try:
                with open(proxy_file) as f:
                    return json.load(f)
            except Exception:  # noqa: BLE001
                pass
        return {}

    # Sprint 71E: DNS Rebinding Defense
    _PRIVATE_NETS = [ipaddress.ip_network(n) for n in [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10"
    ]]

    async def _resolve_host_ips_async(self, host: str) -> list[str]:
        """Resolve hostname to IPs (deterministically sorted) using async DNS."""
        try:
            results = await async_getaddrinfo(host, 0, proto=socket.IPPROTO_TCP)
            ips = sorted({str(r[4][0]) for r in results})
            return ips
        except Exception:
            return []

    def _resolve_host_ips(self, host: str) -> list[str]:
        """Resolve hostname to IPs synchronously using blocking socket.getaddrinfo."""
        try:
            results = socket.getaddrinfo(host, 0, proto=socket.IPPROTO_TCP)
            ips = sorted({str(r[4][0]) for r in results})
            return ips
        except Exception:
            return []

    def _is_ip_public(self, ip_str: str) -> bool:
        """Check if IP is public (not private/reserved)."""
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in self._PRIVATE_NETS:
                if ip in net:
                    return False
            if ip.is_multicast:
                return False
            if ip.is_unspecified:
                return False
            if ip.is_loopback:
                return False
            return True
        except Exception:
            return False

    async def _validate_fetch_target(self, url: str) -> tuple[bool, dict[str, Any]]:
        """
        Validate fetch target: resolve and check for private IPs.

        NOTE (P3-8): This provides DNS rebinding protection but has a residual
        TOCTOU window between validation and fetch. The actual aiohttp fetch
        resolves DNS independently. For HTTPS, certificate validation provides
        secondary protection. For HTTP, the risk is acknowledged but the
        performance cost of binding to pre-validated IPs is prohibitive.

        Sprint F-A4: consults ``self._host_ips_cache`` first (populated
        by ``run_step`` via the batch DNS resolver) and falls through
        to a per-fetch ``async_getaddrinfo`` on miss. Cache is reset
        every batch so freshness is preserved.
        """
        # F3XX: httpx.URL is now module-level import
        try:
            parsed = httpx.URL(url)
            hostname = parsed.host
            if not hostname:
                return False, {"blocked_reason": "no_hostname"}

            # Check if hostname is already an IP
            try:
                ip = ipaddress.ip_address(hostname)
                if not self._is_ip_public(str(ip)):
                    return False, {"resolved_ips": [str(ip)], "blocked_reason": "private_ip_literal"}
                return True, {"resolved_ips": [str(ip)]}
            except ValueError:
                pass  # It's a domain, not an IP

            # Sprint F-A4: check the per-batch host→IPs cache first.
            # On hit we skip the getaddrinfo round-trip entirely.
            cache_key = hostname.lower()
            cached_ips = self._host_ips_cache.get(cache_key)
            if cached_ips is not None:
                if not cached_ips:
                    return False, {"resolved_ips": [], "blocked_reason": "dns_resolution_failed"}
                for ip_str in cached_ips:
                    if not self._is_ip_public(ip_str):
                        return False, {
                            "resolved_ips": list(cached_ips),
                            "blocked_reason": "private_ip_resolved",
                            "blocked_ip": ip_str,
                        }
                return True, {"resolved_ips": list(cached_ips)}

            # Cache miss — resolve DNS via BoundedPerHostGate to enforce
            # per-host concurrency limits (Issue #14).
            sem, _op_id = await self._per_host_gate.acquire(hostname)
            try:
                raw_results = await async_getaddrinfo(hostname, 0, proto=socket.IPPROTO_TCP)
            finally:
                self._per_host_gate.release(sem)
            ips = sorted({str(r[4][0]) for r in raw_results})
            if not ips:
                return False, {"resolved_ips": [], "blocked_reason": "dns_resolution_failed"}

            for ip_str in ips:
                if not self._is_ip_public(ip_str):
                    return False, {
                        "resolved_ips": ips,
                        "blocked_reason": "private_ip_resolved",
                        "blocked_ip": ip_str
                    }

            return True, {"resolved_ips": ips}
        except Exception as e:
            # Fail-safe: block on exception
            return False, {"blocked_reason": f"validation_error: {e}"}

    def _is_js_heavy(self, url: str, html_preview: str = "") -> bool:
        """Detect JS-heavy pages by URL and HTML preview."""
        # By URL - modern frameworks
        js_indicators = ['react', 'vue', 'angular', 'next', 'nuxt', 'svelte']
        if any(ind in url.lower() for ind in js_indicators):
            return True

        # By HTML preview
        if html_preview:
            if '<script' in html_preview.lower() and len(html_preview) < 5000:
                return True
            if 'data-reactroot' in html_preview or 'ng-version' in html_preview:
                return True

        return False

    # =============================================================================
    # Sprint 4B: AIMD Controller
    # =============================================================================

    async def _aimd_acquire(self) -> tuple[float, None]:
        """
        Acquire AIMD slot, returns (concurrency_window, None).

        Release is always via self._aimd_slot.release() — no captured reference
        needed because the controller never rebuilds state (no semaphore swap).

        Sprint 6.4 + Issue #15: Backpressure clamping now uses AIMDWindow.set_window()
        under its internal lock to avoid race conditions during concurrent updates.
        """
        # Sprint 6.4: Backpressure clamp — read provider outside the lock
        # Issue #19: Memoize _concurrency_provider() result for the batch lifetime.
        # The result is cached in self._batch_cp_result at batch start and re-used
        # across all N _aimd_acquire() calls within the same safe_gather_ok batch,
        # avoiding N redundant provider calls when _concurrency_provider is expensive
        # (e.g., ResourceGovernor.can_afford_sync with psutil/Metal probes).
        _bp_clearing: float | None = None
        _bp_uma_state = 'ok'
        # Issue #19: use `is not` (identity) to distinguish:
        #   _CP_NOT_CALLED     — provider never called this batch → call it now
        #   _CP_RETURNED_NONE  — provider called, returned None → skip (window unchanged)
        #   tuple              — provider returned a valid result → use it
        if self._batch_cp_result is _CP_RETURNED_NONE:
            pass  # provider was inactive; leave _bp_clearing=None, _bp_uma_state='ok'
        elif self._batch_cp_result is not _CP_NOT_CALLED:
            _bp_clearing, _bp_stealth, _bp_uma_state, _ = self._batch_cp_result  # type: ignore[assignment]
        elif self._concurrency_provider is not None:
            try:
                _bp_result = self._concurrency_provider()
                if _bp_result is not None:
                    _bp_clearing, _bp_stealth, _bp_uma_state, _ = _bp_result
            except Exception:  # noqa: BLE001
                pass  # Fail-soft

        # Backpressure ceiling — apply via AIMDWindow.set_window() for atomicity
        if _bp_clearing is not None and _bp_clearing < self._aimd_window.window:
            await self._aimd_window.set_window(_bp_clearing)
            self._telemetry['aimd_concurrency'] = _bp_clearing
            self._telemetry['backpressure_clamp_events'] += 1
        self._telemetry['uma_state'] = _bp_uma_state

        # Update slot window to match current AIMD window
        current_window = self._aimd_window.window
        if current_window != self._aimd_slot.window:
            await self._aimd_slot.update_window(int(current_window))

        # Acquire slot — fast path (lock-free when slots available)
        await self._aimd_slot.acquire()
        self._telemetry['active_fetches'] += 1
        return current_window, None

    async def _aimd_release_success(self) -> float:
        """
        Release AIMD slot after success.
        Returns new concurrency window.

        Issue #15 fix: All counter and window mutations are now atomic under
        AIMDWindow's internal lock, preventing the race condition where 100+
        simultaneous completions would each see successes >= threshold and all
        trigger window increases independently.
        """
        self._telemetry['active_fetches'] -= 1

        # Fast recovery multiplier: 2x when healthy (ok) and below slot cap
        uma_state = self._telemetry.get('uma_state', 'ok')
        multiplier = 2.0 if uma_state == 'ok' else 1.0

        new_window, _ = await self._aimd_window.on_success(multiplier=multiplier)
        self._telemetry['total_successes'] += 1
        self._telemetry['aimd_concurrency'] = new_window

        if new_window != self._aimd_slot.window:
            await self._aimd_slot.update_window(int(new_window))

        return new_window

    async def _aimd_release_failure(self) -> float:
        """
        Release AIMD slot after failure (timeout/throttling/pressure).
        Returns new concurrency window.

        Issue #15 fix: All counter and window mutations are now atomic under
        AIMDWindow's internal lock, preventing the race condition where 100+
        simultaneous failures would each see stale _aimd_concurrency and all
        independently trigger multiplicative decreases.
        """
        self._telemetry['active_fetches'] -= 1
        uma_state = self._telemetry.get('uma_state', 'ok')

        new_window, new_failures = await self._aimd_window.on_failure(uma_state=uma_state)
        self._telemetry['total_failures'] += 1
        self._telemetry['aimd_concurrency'] = new_window

        decrease_factor = AIMD_DECREASE_BY_STATE.get(uma_state, 1.0)
        self._telemetry['decrease_factor_used'] = decrease_factor

        if new_window != self._aimd_slot.window:
            await self._aimd_slot.update_window(int(new_window))
            logger.warning(
                f"[AIMD] failure #{new_failures} uma_state={uma_state} "
                f"factor={decrease_factor} → window→{new_window:.1f}"
            )

        return new_window

    # -------------------------------------------------------------------------
    # F281: Privacy lane semaphore helpers
    # -------------------------------------------------------------------------

    def _get_privacy_semaphore(self, url: str) -> tuple[asyncio.Semaphore | None, str]:
        """
        Get the privacy semaphore for a URL's transport class.

        Returns (semaphore, lane). lane="clearnet" means no privacy lane needed.
        """
        if self._privacy_allocator is None:
            return None, "clearnet"
        lane = self._privacy_allocator.get_lane_for_url(url)
        if lane == "clearnet":
            return None, "clearnet"
        sem = self._privacy_allocator.get_semaphore(lane)
        return sem, lane

    async def _privacy_acquire_for_url(self, url: str) -> tuple[str, bool]:
        """
        Acquire privacy lane slot for URL if applicable.

        Returns (lane, acquired). If lane is "clearnet" or privacy lane unavailable,
        returns ("clearnet", True) so caller falls back to AIMD path.

        Fail-soft: any error → fall back to clearnet.
        """
        if self._privacy_allocator is None:
            return "clearnet", True
        lane = self._privacy_allocator.get_lane_for_url(url)
        if lane == "clearnet":
            return "clearnet", True
        sem = self._privacy_allocator.get_semaphore(lane)
        if sem is None:
            # Privacy lane not available (env disabled) → fall back to clearnet
            return "clearnet", True
        try:
            async with self._privacy_lock:
                await sem.acquire()
            return lane, True
        except Exception:
            # Fail-soft: treat as clearnet
            return "clearnet", True

    def _privacy_release(self, lane: str) -> None:
        """Release privacy lane slot. No-op for clearnet."""
        if lane == "clearnet" or self._privacy_allocator is None:
            return
        sem = self._privacy_allocator.get_semaphore(lane)
        if sem is not None:
            try:
                sem.release()
            except ValueError:
                pass  # already released

    async def _fetch_with_lightpanda(self, url: str, proxy: str | None = None):
        """Fetch URL with Lightpanda using pool (JS rendering)."""
        try:
            # P1-1: Start pool on first use (lazy initialization) - thread-safe with double-check
            if not self._lightpanda_pool_started:
                async with self._lightpanda_lock:
                    if not self._lightpanda_pool_started:
                        await self._lightpanda_pool.start()
                        self._lightpanda_pool_started = True

            # Get instance from pool
            lp = await self._lightpanda_pool.get_instance()
            try:
                content = await lp.fetch_js(url, proxy)
                return {'url': url, 'content': content, 'js_rendered': True}
            finally:
                await self._lightpanda_pool.release(lp)
        except Exception as e:
            logger.warning("[LIGHTPANDA] Failed: {e}, falling back to curl_cffi", e=e)
            return None

    # =============================================================================
    # Sprint 76: Tor Connection Pooling
    # =============================================================================

    @staticmethod
    def _mask_cookies_for_log(cookies: dict[str, str] | None) -> dict[str, str]:
        """
        P3-5 fix: Mask cookie values for safe logging.

        Args:
            cookies: Raw cookie dict {name: value}

        Returns:
            Masked dict {name: '***'} preserving structure but hiding values
        """
        if not cookies:
            return {}
        return dict.fromkeys(cookies, '***')

    async def _get_tor_session(self, domain: str) -> Any | None:
        """F274: Delegate to darknet_session_provider (transport layer owns sessions)."""
        from ..transport.darknet_session_provider import get_session, mark_used

        session = await get_session("tor", domain)
        if session is not None:
            await mark_used("tor", domain)
        return session

    async def _fetch_with_tor(self, url: str, session: Any | None = None) -> dict[str, Any] | None:
        """Fetch .onion URL using Tor connection pool.

        Args:
            url: The .onion URL to fetch.
            session: Pre-acquired Tor session (from _get_tor_session). If None, acquires one.
                     Pre-acquiring outside the retry loop saves ~2s per retry on SOCKS handshake.
        """
        # Sprint 4B: Use TIMEOUT_TOR matrix constant (passed to session at creation)
        # F3XX: httpx.URL is module-level import
        try:
            domain = httpx.URL(url).host
            if session is None:
                session = await self._get_tor_session(domain)
            if not session:
                return None

            # Sprint 4B: Timeout already set at session creation (TIMEOUT_TOR=75s)
            # The session timeout is authoritative; no per-request override needed
            async with session.get(url) as resp:
                return {
                    'status': resp.status,
                    'headers': dict(resp.headers),
                    'content': await resp.read()
                }
        except TimeoutError:
            logger.debug("[TOR] Timeout for {url}", url=url)
            # Trigger AIMD failure
            await self._aimd_release_failure()
            return None
        except Exception as e:
            logger.warning("Tor fetch failed: {e}", e=e)
            await self._aimd_release_failure()
            return None

    # =============================================================================
    # I2P Connection Pooling (F274: now via darknet_session_provider)
    # =============================================================================

    async def _get_i2p_session(self, domain: str) -> Any | None:
        """F274: Delegate to darknet_session_provider (transport layer owns sessions)."""
        from ..transport.darknet_session_provider import get_session, mark_used

        session = await get_session("i2p", domain)
        if session is not None:
            await mark_used("i2p", domain)
        return session

    async def _fetch_with_i2p(self, url: str, session: Any | None = None) -> dict[str, Any] | None:
        """Fetch .i2p URL using I2P connection pool.

        Args:
            url: The .i2p URL to fetch.
            session: Pre-acquired I2P session (from _get_i2p_session). If None, acquires one.
                     Pre-acquiring outside the retry loop saves ~2s per retry on SOCKS handshake.
        """
        # F3XX: httpx.URL is module-level import
        try:
            domain = httpx.URL(url).host
            if session is None:
                session = await self._get_i2p_session(domain)
            if not session:
                return None

            async with session.get(url) as resp:
                content = await resp.read()
                return {
                    'url': url,
                    'content': content,
                    'status': resp.status,
                    'headers': dict(resp.headers),
                    'content_type': resp.content_type,
                }
        except TimeoutError:
            logger.debug("[I2P] Timeout for {url}", url=url)
            await self._aimd_release_failure()
            return None
        except Exception as e:
            logger.warning("I2P fetch failed: {e}", e=e)
            await self._aimd_release_failure()
            return None

    async def _fetch_with_curl(self, url: str, proxy: str | None = None):
        """Fetch URL via curl_cffi with HTTP/3 Alt-Svc support (F265C).

        Replaced StealthWebScraper (aiohttp) with public_fetcher's
        fetch_via_curl_cffi_cached() which has full H3 Alt-Svc LRU priming,
        conditional cache (ETag/Last-Modified), and prewarm pool support.
        """
        try:
            from hledac.universal.fetching.public_fetcher import (
                _altsvc_extract_host,
                _altsvc_http_version_for,
                _altsvc_record_from_result,
                fetch_via_curl_cffi_cached,
            )
            # F265C: Probe Alt-Svc speculatively (fire-and-forget, primes H3 LRU)
            try:
                from hledac.universal.transport.http3_lane import probe_altsvc_speculative
                probe_altsvc_speculative(url)
            except Exception:  # noqa: BLE001
                pass
            # Determine HTTP/3 version hint from Alt-Svc cache
            _curl_http_version = _altsvc_http_version_for(_altsvc_extract_host(url))
            _curl_result = await fetch_via_curl_cffi_cached(
                url=url,
                headers=None,
                timeout_s=30.0,
                max_bytes=10 * 1024 * 1024,
                profile="chrome110",
                http_version=_curl_http_version,
                _pre_probe=False,
            )
            # Record Alt-Svc h3 advertisement for future fetches
            _altsvc_record_from_result(url, _curl_result.get("headers"))
            _curl_bytes = _curl_result.get("content", b"")
            _curl_error = _curl_result.get("error", None)
            if _curl_bytes:
                _curl_text = _curl_bytes.decode("utf-8", errors="replace")
            else:
                _curl_text = None
            return {
                'url': url,
                'final_url': _curl_result.get("final_url", url),
                'content': _curl_bytes,
                'text': _curl_text,
                'status_code': _curl_result.get("status_code", 0),
                'content_type': _curl_result.get("content_type", ""),
                'headers': _curl_result.get("headers", {}),
                'js_rendered': False,
                'success': _curl_error is None,
                'error': _curl_error,
            }
        except TimeoutError:
            logger.debug("[CURL] Timeout for {url}", url=url)
            await self._aimd_release_failure()
            return {'url': url, 'content': b'', 'error': 'timeout'}
        except Exception as e:
            logger.warning("[CURL] Failed: {e}", e=e)
            return {'url': url, 'content': b'', 'error': str(e)}

    def get_supported_operations(self) -> list[Any]:
        """Return supported operation types."""
        from .base import OperationType
        return [OperationType.RESEARCH]

    async def handle_request(
        self,
        operation_ref: str,
        decision: Any
    ) -> Any:
        """
        Handle a decision request (required by UniversalCoordinator base).

        For spine pattern, we use start/step/shutdown instead.
        This is a compatibility method.
        """
        # Delegate to step with decision as context
        result = await self.step({'decision': decision})
        return result

    async def _do_initialize(self) -> bool:
        """Initialize coordinator."""
        logger.info("FetchCoordinator initialized")

        # F-HTTP-CACHE: lazy build the hishel cache transport (opt-out).
        # Fail-soft on every failure mode: env disabled, hishel missing, build
        # error → self._http_cache_transport stays None and httpx fetch paths
        # operate uncached.
        if self._http_cache_enabled and self._http_cache_transport is None:
            try:
                from ..transport.http_cache import build_cache_transport
                self._http_cache_transport = await build_cache_transport(None)
                if self._http_cache_transport is not None:
                    logger.info(
                        "FetchCoordinator HTTP cache active "
                        "(opt-out via HLEDAC_HTTP_CACHE=0)"
                    )
                else:
                    logger.info(
                        "FetchCoordinator HTTP cache requested but unavailable "
                        "(install: 'uv pip install \".[osint-cache]\"')"
                    )
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.warning("HTTP cache init failed: %s", exc)
                self._http_cache_transport = None
        elif not self._http_cache_enabled:
            logger.info("FetchCoordinator HTTP cache disabled via HLEDAC_HTTP_CACHE=0")

        return True

    async def _do_start(self, ctx: dict[str, Any]) -> None:
        """
        Start coordinator with context from orchestrator.

        Expected ctx keys:
        - frontier: list[str] - URLs to fetch
        - orchestrator: reference to orchestrator instance
        - budget_manager: BudgetManager for limits
        """
        self._ctx = ctx
        self._orchestrator = ctx.get('orchestrator')

        # F281: Initialize privacy budget allocator from AIMD concurrency window.
        # Lazy: created on first start so that subclasses can override max_concurrent.
        if self._privacy_allocator is None:
            _target = int(self._aimd_concurrency)
            self._privacy_allocator = make_privacy_allocator(_target)
            logger.info(
                f"[F281] PrivacyBudgetAllocator: {self._privacy_allocator.get_budget_summary()}"
            )

        # Load frontier if provided
        if 'frontier' in ctx:
            self._frontier = deque(ctx['frontier'], maxlen=1000)

        _ev0 = len(self._frontier)
        logger.info("FetchCoordinator started with {len(self._frontier)} URLs in frontier", _ev0=len(self._frontier))

    def _url_priority(self, url: str) -> int:
        """
        Sprint 5B: Lightweight priority scoring for frontier intake.
        Lower score = higher priority (processed first).
        Priority: API > JSON > HTML > Tor > I2P

        LOW-2 fix: Use named constants instead of magic numbers.
        """
        lower = url.lower()
        # Tor hidden services (lower priority than clearnet)
        if '.onion' in lower:
            return _PRIORITY_TOR
        if '.i2p' in lower:
            return _PRIORITY_I2P
        # API endpoints (highest priority)
        if '/api/' in lower or 'api.' in lower or lower.endswith('/json'):
            return _PRIORITY_API
        # Structured data (JSON/XML/RSS)
        if lower.endswith('.json') or lower.endswith('.xml') or lower.endswith('.rss'):
            return _PRIORITY_JSON
        # Standard clearnet HTML
        if '.onion' not in lower and '.i2p' not in lower:
            return _PRIORITY_CLEARNET_HTML
        # Fallback for other exotic TLDs
        return _PRIORITY_OTHER

    async def _do_step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one fetch step with batch parallel fetch.

        Sprint 5B: Process up to max_urls_per_step from frontier using
        controlled parallel batch fetch that respects:
        - timeout matrix
        - concurrency matrix
        - AIMD window
        """
        # Update context
        self._ctx.update(ctx)

        # Get budget manager
        budget_mgr = ctx.get('budget_manager')

        # Check network budget
        if budget_mgr:
            allowed, reason = budget_mgr.check_network_allowed()
            if not allowed:
                self._stop_reason = reason
                return self._get_step_result()

        # Sprint 5B: Collect URLs with lightweight priority intake
        # Sort frontier candidates by priority (cheap/fast first) before selecting
        candidates = []
        raw_batch: list[str] = []
        for _ in range(self._config.max_urls_per_step * 2):
            if not self._frontier:
                break
            url = self._frontier.popleft()
            raw_batch.append(url)

        # F320-OPT: Parallelize dedup (CPU) + DNS prewarm (I/O) — ~40% faster batch startup.
        # Flow:
        #   T+0: Extract hosts from raw_batch in thread pool (pure sync, fast)
        #   T+0: Fire DNS resolve for raw hosts (async I/O, non-blocking)
        #   T+0: Fire dedup in thread pool (CPU-bound Bloom lookup, main cost)
        #   T+dedup_done: Sort + priority scoring (must wait for dedup result)
        #   T+dns_done: Cache populated (DNS results may already be cached/in-flight)
        # F3XX: httpx.URL is module-level import

        def _extract_raw_hosts() -> set[str]:
            """Extract unique hosts from raw batch (sync, fast — runs in thread pool)."""
            hosts: set[str] = set()
            for url in raw_batch:
                if url.endswith('.onion') or url.endswith('.i2p'):
                    continue
                try:
                    hostname = httpx.URL(url).host
                except Exception:
                    continue
                if hostname:
                    hosts.add(hostname.lower())
            return hosts

        def _dedup_and_trace() -> tuple[list[str], int]:
            """Dedup + trace (sync CPU — runs in thread pool)."""
            unique, dropped = dedupe_url_list(raw_batch, self._processed_urls)
            for url in raw_batch:
                trace_dedup_decision(url, url not in unique)
            return unique, dropped

        # Reset per-batch cache — stale IPs from prior batch must never leak through.
        self._host_ips_cache = {}
        # Issue #19: Prime batch-local _concurrency_provider memoization cache ONCE at
        # batch start. All subsequent _aimd_acquire() calls within this batch will
        # re-use self._batch_cp_result instead of calling the (potentially expensive)
        # _concurrency_provider again. Invalidated automatically on next batch start.
        self._batch_cp_result = _CP_NOT_CALLED
        if self._concurrency_provider is not None:
            try:
                _result = self._concurrency_provider()
                # Issue #19: cache None as sentinel too — distinguishes "called returning None"
                # from "never called" (_CP_NOT_CALLED), preventing redundant calls per URL.
                self._batch_cp_result = _result if _result is not None else _CP_RETURNED_NONE
            except Exception:  # noqa: BLE001
                pass  # Fail-soft; _batch_cp_result stays _CP_NOT_CALLED
        resolver = get_batch_dns_resolver()

        # Fire all three operations at T+0 — True parallel execution:
        #   1. _extract_raw_hosts (thread pool, pure sync, ~0.1ms)
        #   2. resolver.resolve_many for raw hosts (async I/O, parallel with dedup)
        #   3. _dedup_and_trace (thread pool, CPU Bloom lookup, ~1-5ms)
        # DNS results feed into _host_ips_cache; dedup result feeds into sort/priority.
        raw_hosts_task = asyncio.to_thread(_extract_raw_hosts)
        dedup_task = asyncio.to_thread(_dedup_and_trace)

        # Await host extraction first (fast — <0.1ms), then fire DNS immediately.
        raw_hosts = await raw_hosts_task

        # Fire DNS resolution in parallel with dedup — DNS runs async I/O (~5ms)
        # while dedup runs CPU Bloom lookup (~1-5ms) in thread pool.
        dns_coro: asyncio.Task[dict[str, list[str]]] | None = None
        if raw_hosts:
            dns_coro = safe_create_task(
                resolver.resolve_many(list(raw_hosts), timeout=5.0)
            )

        # Dedup is critical path — must complete before sort/priority scoring.
        unique_batch, dropped = await dedup_task

        # Collect DNS results (may already be resolved from cache hits).
        if dns_coro is not None:
            try:
                resolved = await dns_coro
                self._host_ips_cache = {h: list(ips) for h, ips in resolved.items()}
            except Exception as exc:  # noqa: BLE001 — fail-soft
                logger.debug("[F-A4] batch DNS pre-resolve failed: %s: %s", type(exc).__name__, exc)

        # Priority scoring + sort — must be sequential after dedup completes.
        candidates: list[tuple[float, str]] = []
        for url in unique_batch:
            candidates.append((self._url_priority(url), url))
        del unique_batch  # bounded: drop the staging list immediately

        if not candidates:
            self._stop_reason = "frontier_empty"
            return self._get_step_result()

        # Sprint 5B: Sort by priority (lower score = higher priority) and take top N.
        candidates.sort(key=lambda x: x[0])
        urls_to_fetch = [url for _, url in candidates[:self._config.max_urls_per_step]]

        # Sprint 5B: Determine effective batch size (limited by AIMD window)
        # Fix: use the result of min() to actually limit batch size
        raw_batch_size = len(urls_to_fetch)
        effective_batch_size = min(raw_batch_size, int(self._aimd_concurrency))
        urls_to_fetch = urls_to_fetch[:effective_batch_size]
        batch_size = len(urls_to_fetch)

        # Sprint 4B: Light telemetry snapshot before fetch batch
        if is_enabled():
            trace_counter("fetch.aimd.window", self._aimd_concurrency)
            trace_counter("fetch.active", self._telemetry['active_fetches'])
            trace_counter("fetch.batch_size", batch_size)

        # F262D: safe_gather_ok is fail-soft (return_exceptions=True inside)
        # Each _fetch_url handles AIMD semaphore internally
        batch_start = time.time()
        results = await safe_gather_ok(
            *[self._fetch_url(url) for url in urls_to_fetch],
            label="fetch_coordinator:1110"
        )
        batch_elapsed = time.time() - batch_start

        # Sprint 5B: Gather hygiene - explicit exception logging
        evidence_ids = []
        for url, result in zip(urls_to_fetch, results, strict=False):
            if isinstance(result, Exception):
                # Sprint 5B: Explicit exception logging (no silent failure)
                _ev0 = type(result).__name__
                logger.debug("[BATCH] fetch exception for {url}: {type(result).__name__}: {result}", url=url, result=result, _ev0=type(result).__name__)
                continue

            if result and result.get('success'):
                # Dedup already done in _fetch_url via _dedup_lock — no duplicate add needed
                self._urls_fetched_count += 1

                # Extract evidence ID
                evidence_id = result.get('evidence_id')
                if evidence_id:
                    evidence_ids.append(evidence_id)
                    self._evidence_ids.append(evidence_id)

                # Check snapshot budget
                if budget_mgr:
                    allowed, reason = budget_mgr.check_snapshot_allowed()
                    if not allowed:
                        self._stop_reason = reason
                        break

        # Sprint 5B: Telemetry update with batch metrics
        effective_parallelism = min(len(urls_to_fetch), int(self._aimd_concurrency))
        return self._get_step_result(
            evidence_ids,
            batch_size=batch_size,
            effective_parallelism=effective_parallelism,
            batch_elapsed_ms=round(batch_elapsed * 1000, 2)
        )

    def _get_step_result(
        self,
        new_evidence_ids: list[str] | None = None,
        batch_size: int = 0,
        effective_parallelism: int = 0,
        batch_elapsed_ms: float = 0.0,
    ) -> dict[str, Any]:
        """Get bounded step result with Sprint 5B batch telemetry."""
        evidence_ids = (new_evidence_ids or [])[:self._config.max_evidence_per_step]

        return {
            'urls_fetched': len(evidence_ids),
            'evidence_ids': evidence_ids,
            'total_fetched': self._urls_fetched_count,
            'stop_reason': self._stop_reason,
            'frontier_remaining': len(self._frontier),
            # Sprint 4B: Light telemetry in response
            'aimd_window': self._aimd_concurrency,
            'active_fetches': self._telemetry['active_fetches'],
            # Sprint 5B: Batch telemetry
            'batch_size': batch_size,
            'effective_parallelism': effective_parallelism,
            'batch_elapsed_ms': batch_elapsed_ms,
        }

    @_otel_instrumented("fetch.url", component="network")
    async def _fetch_url(self, url: str, attempt: int = 0) -> dict[str, Any] | None:
        """
        Fetch a single URL with AIMD concurrency control and timeout matrix.

        Uses Lightpanda for JS-heavy pages, falls back to curl_cffi.
        Supports session injection, paywall bypass, and credential rotation.
        Implements exponential backoff retry on failure.

        AUTHORITY SEAM (audit/8SF):
          This method is the CURRENT SOURCE-INGRESS OWNER.
          It directly handles:
            - .onion via _fetch_with_tor() / _darknet_connector.fetch_onion()
            - .i2p via _darknet_connector.fetch_i2p()
            - clearnet via curl_cffi/StealthCrawler
            - JS-heavy via Lightpanda pool
          TransportResolver.resolve() is DORMANT — not called here.
          To wire it in future: replace the above with resolver.resolve(ctx).
        """
        # Sprint 82Q Phase 6: Offline mode fast-fail BEFORE any network operations
        from ..project_types import OfflineModeError, is_offline_mode
        if is_offline_mode():
            raise OfflineModeError(f"Offline mode enabled, skipping fetch: {url}")

        # Sprint F214AD: Atomic dedup check+add BEFORE aimd_acquire to prevent race condition
        async with self._dedup_lock:
            if url in self._processed_urls:
                return None
            self._processed_urls.add(url)

        # Sprint F320: Per-host circuit breaker — acquire per-host slot before AIMD.
        # Extract hostname; skip for .onion/.i2p (darknet transport handles its own concurrency).
        _host_sem: asyncio.Semaphore | None = None
        _host_name = ""
        try:
            _parsed = httpx.URL(url)
            _host_name = _parsed.host or ""
        except Exception:
            pass
        if _host_name and not url.endswith(('.onion', '.i2p')):
            _host_sem, _ = await self._per_host_gate.acquire(_host_name)

        # F281: Privacy lane gate — reserve privacy slot before AIMD
        _privacy_lane = "clearnet"
        _privacy_acquired = False
        try:
            _privacy_lane, _privacy_acquired = await self._privacy_acquire_for_url(url)
        except Exception:
            _privacy_lane = "clearnet"
            _privacy_acquired = True

        # Sprint 4B: AIMD concurrency gate (clearnet path or privacy fallback)
        _aimd_sem: asyncio.Semaphore | None = None
        if _privacy_acquired:
            # G-8: Resource pre-flight check — fail-soft skip if RAM/GPU/thermal won't sustain fetch
            # Issue #12: SSOT via get_governor() from core/protocols.py
            try:
                from hledac.universal.core.protocols import get_governor

                gov = get_governor()
                if gov is not None:
                    from ..core.resource_governor import Priority

                    if not gov.can_afford_sync({'ram_mb': 15}, Priority.CRITICAL):
                        # Release dedup slot before returning
                        async with self._dedup_lock:
                            self._processed_urls.discard(url)
                        return None
            except Exception:  # noqa: BLE001
                pass  # Fail-soft: proceed with fetch if governor check fails
            _concurrency, _aimd_sem = await self._aimd_acquire()  # _aimd_sem is always None; release via _aimd_slot

        # Sprint 71E: DNS Rebinding Defense — resolve ONCE before retry loop (F320-OPT).
        # Sprint F371: DNS + circuit breaker parallelized BEFORE retry loop.
        # DNS is cached after first call (~0ms on retries), circuit breaker is sync ~1-2ms.
        # Parallelizing these two eliminates sequential ~10-15ms overhead per retry iteration.
        # F3XX: httpx.URL is module-level import
        domain = httpx.URL(url).host

        async def _dns_check() -> tuple[bool, dict[str, Any]]:
            """DNS validation — cached after first call."""
            if url.endswith('.onion') or url.endswith('.i2p'):
                return True, {}
            return await self._validate_fetch_target(url)

        async def _circuit_breaker_check() -> tuple[bool, str, float]:
            """Circuit breaker — sync in-memory, ~1-2ms."""
            return self._check_canonical_breaker(domain)

        # Sprint F371: Parallel DNS + circuit breaker BEFORE retry loop
        try:
            async with asyncio.TaskGroup() as tg:
                dns_task = tg.create_task(_dns_check(), name="dns_check")
                cb_task = tg.create_task(_circuit_breaker_check(), name="circuit_breaker")
            dns_safe, dns_meta = dns_task.result()
            canonical_allowed, canonical_reason, canonical_retry_after = cb_task.result()
        except Exception:
            # Fail-open: proceed with fetch if parallel check fails
            dns_safe, dns_meta = True, {}
            canonical_allowed, canonical_reason, canonical_retry_after = True, "", 0.0

        # Sprint F371: DNS blocking handled BEFORE retry loop (BUG FIX: was overwritten by result=None)
        if not dns_safe:
            _ev0 = dns_meta.get('blocked_reason')
            logger.warning("DNS rebinding defense blocked: {dns_meta.get('blocked_reason')} for {domain}", domain=domain, _ev0=dns_meta.get('blocked_reason'))
            trace_fetch_end(url, "dns_rebind_defense", "blocked", 0.0, {"reason": dns_meta.get("blocked_reason")})
            # Release slots acquired before DNS check
            self._aimd_slot.release()
            if _host_sem is not None:
                self._per_host_gate.release(_host_sem)
            if _privacy_lane != "clearnet":
                self._privacy_release(_privacy_lane)
            async with self._dedup_lock:
                self._processed_urls.discard(url)
            return {"error": "blocked", "blocked_reason": dns_meta.get("blocked_reason"), "meta": dns_meta}

        # Sprint F371: Pre-acquire Tor/I2p sessions OUTSIDE retry loop.
        # Session creation (SOCKS handshake) costs 1-3s; doing it inside the retry loop
        # multiplies that cost by (1 + retries). Moving it outside saves ~2s per retry.
        _pre_acquired_tor_session: Any | None = None
        _pre_acquired_i2p_session: Any | None = None
        # url_transport and route_decision are already computed above (lines ~1865-1876)
        if url_transport is Transport.TOR and route_decision is not RouteDecision.TOR_UNAVAILABLE:
            _pre_acquired_tor_session = await self._get_tor_session(domain)
        elif url_transport is Transport.I2P and route_decision is not RouteDecision.I2P_UNAVAILABLE:
            _pre_acquired_i2p_session = await self._get_i2p_session(domain)

        # Sprint 23: Exponential backoff retry
        max_retries = getattr(self, '_max_retries', 3)
        base_delay = getattr(self, '_base_retry_delay', 1.0)

        # Sprint 8C1: Trace fetch start
        trace_fetch_start(url, "pending", {
            "attempt": attempt,
            "aimd_window": self._aimd_concurrency,
        })

        result = None
        try:
            while attempt <= max_retries:
                # Sprint F371: DNS + circuit breaker check on EVERY retry attempt.
                # Prior result is discarded; each retry may have a different network path.
                # Parallelize via TaskGroup: ~1-2ms total vs ~10-15ms sequential.
                try:
                    async with asyncio.TaskGroup() as tg:
                        dns_task = tg.create_task(_dns_check(), name="dns_check")
                        cb_task = tg.create_task(_circuit_breaker_check(), name="circuit_breaker")
                    dns_safe, dns_meta = dns_task.result()
                    canonical_allowed, canonical_reason, canonical_retry_after = cb_task.result()
                except Exception:
                    dns_safe, dns_meta = True, {}
                    canonical_allowed, canonical_reason, canonical_retry_after = True, "", 0.0

                if not dns_safe:
                    _blocked_reason = dns_meta.get('blocked_reason')
                    logger.warning("DNS rebinding defense blocked: {blocked_reason} for {domain}", domain=domain, blocked_reason=_blocked_reason)
                    trace_fetch_end(url, "dns_rebind_defense", "blocked", 0.0, {"reason": dns_meta.get("blocked_reason")})
                    break

                # F206AS: Canonical circuit breaker check
                if not canonical_allowed:
                    self._telemetry['circuit_breaker_active'] = len(self.get_blocked_domains())
                    logger.debug("[F206AS] Canonical circuit breaker open for {domain}: {canonical_reason} (retry in {canonical_retry_after:.1f}s)", domain=domain, canonical_reason=canonical_reason, canonical_retry_after=canonical_retry_after)
                    trace_fetch_end(url, "circuit_breaker", "circuit_open", 0.0)
                    result = None
                    break

                # Local circuit breaker check (fallback if canonical unavailable or not blocking)
                now = time.time()
                if domain in self._domain_blocked_until and now < self._domain_blocked_until[domain]:
                    self._telemetry['circuit_breaker_active'] = len(self.get_blocked_domains())
                    logger.debug("Circuit breaker open for domain: {domain}", domain=domain)
                    trace_fetch_end(url, "circuit_breaker", "circuit_open", 0.0)
                    result = None
                    break

                # Sprint 4B: Policy gate via SourceTransportMap — replaces hardcoded url.endswith()
                # Sprint 46 + 76: Darknet URL handling (.onion, .i2p)
                # Sprint 76: Use Tor connection pool for .onion
                # Issue #37: Strict fail-closed — use RouteDecision to drop unavailable transports
                from ..transport.transport_resolver import (
                    RouteDecision,
                    Transport,
                    get_route_decision,
                    get_transport_for_url,
                )
                url_transport = get_transport_for_url(url)
                route_decision = get_route_decision(url)

                if url_transport is Transport.TOR:
                    # Issue #37: Fail-closed — drop if Tor unavailable
                    if route_decision is RouteDecision.TOR_UNAVAILABLE:
                        logger.debug("[TOR] Tor unavailable, dropping {url}", url=url)
                        trace_fetch_end(url, "tor", "unavailable", 0.0)
                        return None
                    trace_fetch_start(url, "tor", {"attempt": attempt, "timeout": TIMEOUT_TOR})
                    # Sprint F214: Use TorTransport if enabled (circuit rotation)
                    # Issue #15: config was undefined — construct minimal TransportConfig for TorTransport.fetch()
                    if self._tor_transport_enabled and self._tor_transport:
                        from ..transport.base import TransportConfig
                        tor_config = TransportConfig(
                            url=url,
                            timeout_s=TIMEOUT_TOR,
                            max_bytes=10 * 1024 * 1024,
                        )
                        result = await self._tor_transport.fetch(tor_config)
                        if not result.err:
                            # Map TransportResult → FetchCoordinator dict
                            result = {
                                'success': True,
                                'status': result.status_code,
                                'content': b'',  # TransportResult has no raw bytes field
                                'url': url,
                                'final_url': result.final_url or url,
                                'content_type': result.content_type or 'text/html',
                            }
                            trace_fetch_end(url, "tor_transport", "ok", 0.0)
                            break
                        logger.debug("TorTransport fetch failed: {result.err}", err=result.err)
                    # Fallback: use existing Tor session pool
                    # F371: pass pre-acquired session to skip SOCKS handshake on each retry
                    result = await self._fetch_with_tor(url, session=_pre_acquired_tor_session)
                    if result:
                        # Sprint F3/F8/F9: normalize Tor result to common ingest shape
                        result['success'] = True
                        result['status_code'] = result.pop('status', 0)
                        result['url'] = url
                        result['final_url'] = url
                        result.setdefault('content_type', 'text/html')
                        trace_fetch_end(url, "tor", "ok", 0.0)
                        break
                    trace_fetch_end(url, "tor", "failed", 0.0)
                    # Issue #37: STRICT FAIL-CLOSED — no fallback to darknet_connector
                    # darknet_connector.fetch_onion uses wrong SOCKS port (9050 vs TorTransport managed)
                    # If TorTransport + Tor pool both fail, the request is dropped
                elif url_transport is Transport.I2P:
                    # Issue #37: Fail-closed — drop if I2P unavailable (strict closed)
                    if route_decision is RouteDecision.I2P_UNAVAILABLE:
                        logger.debug("[I2P] I2P router unavailable, dropping {url}", url=url)
                        trace_fetch_end(url, "i2p", "unavailable", 0.0)
                        return None
                    trace_fetch_start(url, "i2p", {"attempt": attempt, "timeout": TIMEOUT_I2P})
                    # F371: pass pre-acquired session to skip SOCKS handshake on each retry
                    result = await self._fetch_with_i2p(url, session=_pre_acquired_i2p_session)
                    if result:
                        result['success'] = True
                        result['status_code'] = result.pop('status', 0)
                        result['url'] = url
                        result['final_url'] = url
                        result.setdefault('content_type', 'text/html')
                        trace_fetch_end(url, "i2p", "ok", 0.0)
                        break
                    # Issue #37: STRICT FAIL-CLOSED — no fallback to darknet_connector.fetch_i2p()
                    # darknet_connector.fetch_i2p uses wrong port 4444 instead of 7654 (I2P SOCKS)
                    # I2P router unreachable = deanonymization risk → DROP
                    logger.debug("[I2P] Fetch failed and no fallback, dropping {url}", url=url)
                    trace_fetch_end(url, "i2p", "failed", 0.0)
                elif url_transport is Transport.GOPHER:
                    # Sprint F216: GopherTransport opt-in backend
                    if self._gopher_transport_enabled and self._gopher_transport:
                        trace_fetch_start(url, "gopher", {"attempt": attempt, "timeout": TIMEOUT_GOPHER})
                        try:
                            gopher_res = await self._gopher_transport.fetch(url, timeout_s=TIMEOUT_GOPHER)
                            if not gopher_res.err:
                                result = {
                                    'success': True,
                                    'status': 200,
                                    'content': gopher_res.content,
                                    'url': url,
                                    'final_url': url,
                                    'content_type': 'text/plain',
                                }
                                trace_fetch_end(url, "gopher_transport", "ok", 0.0)
                                break
                            logger.debug("GopherTransport fetch failed: {gopher_res.err}", err=gopher_res.err)
                        except Exception as e:
                            logger.debug("GopherTransport error: {e}", e=e)
                            trace_fetch_end(url, "gopher_transport", "error", 0.0)

                # Sprint 46: Session injection - get cookies before fetch
                # P3-5 fix: Never log raw session cookies - use _mask_cookies_for_log()
                session_cookies = None
                if self._session_manager:
                    session = await self._session_manager.get_session(domain)
                    if session:
                        session_cookies = session.get('cookies')

                # Select proxy based on geo context
                proxy = None
                if self._current_geo_context and self._current_geo_context in self._geo_proxies:
                    proxy = self._geo_proxies.get(self._current_geo_context)

                # F320-OPT: Fetch preview + main curl in parallel via TaskGroup.
                # Preview (3s timeout) runs concurrently with curl; JS detection uses
                # preview result immediately without blocking the main fetch wall-clock.
                # On cache hit for non-HTML, preview returns "" instantly and curl proceeds alone.
                _preview_text: str = ""
                _fetch_task: asyncio.Task[dict[str, Any] | None] | None = None
                _preview_task: asyncio.Task[str] | None = None

                async def _do_preview() -> str:
                    """3s HTML preview fetch — runs in parallel with curl."""
                    try:
                        from network.session_runtime import async_get_httpx_session
                        session = await async_get_httpx_session()
                        preview_timeout = httpx.Timeout(total=3)
                        async with session.head(url, allow_redirects=True, cookies=session_cookies) as resp:
                            content_type = resp.headers.get('content-type', '')
                            if content_type.startswith('text/html'):
                                async with session.get(url, cookies=session_cookies, timeout=preview_timeout) as get_resp:
                                    text = await get_resp.text()
                                    return text[:10000] if text else ""
                            return ""
                    except TimeoutError:
                        logger.debug("[PREVIEW] Timeout for {url}", url=url)
                    except Exception as e:
                        logger.debug("[PREVIEW] Failed to fetch preview for {url}: {e}", url=url, e=e)
                    return ""

                async def _do_curl() -> dict[str, Any] | None:
                    """Main curl fetch — runs in parallel with preview."""
                    trace_fetch_start(url, "curl", {"attempt": attempt, "timeout": TIMEOUT_CLEARNET_HTML})
                    r = await self._fetch_with_curl(url, proxy)
                    if r and not r.get('error'):
                        trace_fetch_end(url, "curl", "ok", 0.0)
                    else:
                        trace_fetch_end(url, "curl", r.get('error', 'failed') if r else 'none', 0.0)
                    return r

                # TaskGroup: both tasks run concurrently; TaskGroup cancels curl if preview
                # raises (and vice versa), which is the correct fail-fast behaviour.
                try:
                    async with asyncio.TaskGroup() as tg:
                        _fetch_task = tg.create_task(_do_curl(), name="curl_fetch")
                        _preview_task = tg.create_task(_do_preview(), name="html_preview")
                    result = _fetch_task.result()
                    _preview_text = _preview_task.result() or ""
                except BaseException as e:
                    # CancelledGroupError or similar — treat as fetch failure
                    logger.debug("[PREVIEW+CURL] TaskGroup failed for {url}: {e}", url=url, e=e)
                    result = None
                    _preview_text = ""

                # JS detection uses preview result; falls back to Lightpanda if heavy
                if self._is_js_heavy(url, _preview_text):
                    logger.debug("[LIGHTPANDA] JS-heavy detected: {url}", url=url)
                    trace_fetch_start(url, "lightpanda", {"attempt": attempt})
                    lightpanda_result = await self._fetch_with_lightpanda(url, proxy)
                    if lightpanda_result and lightpanda_result.get('content'):
                        lightpanda_result.setdefault('success', True)
                        lightpanda_result.setdefault('status_code', 200)
                        lightpanda_result.setdefault('content_type', 'text/html')
                        lightpanda_result.setdefault('final_url', url)
                        lightpanda_result.setdefault('headers', {})
                        result = lightpanda_result
                        trace_fetch_end(url, "lightpanda", "ok", 0.0)
                    else:
                        # Lightpanda failed AND curl already ran in parallel above — result already set
                        trace_fetch_end(url, "lightpanda", "failed", 0.0)

                # Check if we should retry
                if result is None or result.get('error') == 'timeout' or result.get('status_code', 200) >= 500:
                    if attempt < max_retries:
                        # F3XX: Use tenacity wait_exponential_jitter for standard backoff computation
                        # RetryCallState(attempt=attempt) gives wait generator the retry context
                        from tenacity import RetryCallState
                        retry_state = RetryCallState(retry_object=None, sleep=0.0, next_action=None)
                        retry_state.attempt_number = attempt
                        wait_gen = wait_exponential_jitter(initial=base_delay, max=30.0, exp_base=2.0, jitter=1.0)
                        delay = wait_gen(retry_state)
                        delay = min(delay, 30.0)
                        logger.debug("[RETRY] Attempt {attempt_number}/{max_retries} for {url} after {delay}s", max_retries=max_retries, url=url, attempt_number=attempt + 1, delay=delay)
                        trace_fetch_end(url, "none", "retry", 0.0, {"attempt": attempt, "delay": delay})
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                break

            # Sprint 4B: AIMD success - record after full fetch cycle
            if result and not result.get('error'):
                # Sprint F3/F8/F9: ensure success flag is set for corpus ingest
                result.setdefault('success', True)
                await self._aimd_release_success()
                # F206AS: Record success to canonical circuit breaker if available
                self._record_canonical_success(domain)
                # Sprint F214Q: Cover traffic — probabilistic inline OPSEC noise
                # Invariant: MUST use identical transport as real request (Tor→Tor, clearnet→clearnet)
                # Cover traffic NESMÍ go to storage pipeline — fire-and-forget only
                self._maybe_fire_cover_traffic(transport=url_transport.name.lower())
            elif result is None or result.get('error'):
                # Failure path already handled by _aimd_release_failure in fetch methods
                # F206AS: Record failure to canonical circuit breaker if available
                is_timeout = result.get('error') == 'timeout' if result else True
                self._record_canonical_failure(domain, is_timeout=is_timeout, failure_kind='fetch_error')

        except Exception as e:
            logger.warning("[_fetch_url] Unexpected error for {url}: {e}", url=url, e=e)
            await self._aimd_release_failure()
            result = {'url': url, 'content': b'', 'error': str(e)}
        finally:
            # Safety net: always release AIMD slot.
            # No captured reference needed — _aimd_slot is stable, never rebuilt.
            self._aimd_slot.release()
            # F281: Release privacy lane slot if acquired
            if _privacy_lane != "clearnet":
                self._privacy_release(_privacy_lane)
            # Sprint F320: Release per-host semaphore slot via gate
            if _host_sem is not None:
                self._per_host_gate.release(_host_sem)

        # Sprint 46: Handle 401/403 - rotate credentials
        if result and result.get('status_code') in (401, 403):
            if self._session_manager:
                await self._session_manager.rotate_credentials(domain)
                logger.info("[SESSION] Rotated credentials for {domain}", domain=domain)

        # Sprint 46: Paywall bypass - check content for paywall indicators
        if result and result.get('content'):
            content = result['content']
            if isinstance(content, bytes):
                content = content.decode(errors='ignore')

            # Try paywall bypass if content is small or paywall detected
            if len(content) < 5000 and self._paywall_bypass:  # type: ignore[ty:invalid-argument-type]  # stale type: ty can't refine content: str from upstream decode branch
                bypass_result = await self._paywall_bypass.bypass(url, content)
                if bypass_result:
                    _ev0 = bypass_result.get('bypassed')
                    logger.info("[PAYWALL] Bypassed via {bypass_result.get('bypassed')}", _ev0=bypass_result.get('bypassed'))
                    result['content'] = bypass_result.get('content', '').encode()
                    result['bypassed'] = bypass_result.get('bypassed')
                    result['paywall'] = bypass_result.get('paywall')

        trace_fetch_end(url, "none", "done", 0.0)

        # Sprint P3: CAPTCHA pre-filter — skip image/* responses flagged as CAPTCHA
        if (
            self._captcha_detector is not None
            and result
            and result.get("content")
        ):
            ct = result.get("content_type", "")
            content_bytes = result["content"]
            if ct.startswith("image/") and len(content_bytes) < 200 * 1024:  # type: ignore[ty:invalid-argument-type]  # stale type: result.get()/result[] return Any, ty can't refine to str/bytes
                url_for_check = result.get("final_url") or result.get("url") or url
                try:
                    if self._captcha_detector.is_captcha(content_bytes, url_for_check):
                        logger.debug("[CAPTCHA] CAPTCHA detected at {url_for_check}, skipping", url_for_check=url_for_check)
                        self._captcha_detections += 1
                        return None
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # fail-soft

        return result

    # ==========================================================================
    # Sprint 8BH: Deep Research — lawful surface + archival search (no Docker)
    # ==========================================================================

    async def _maybe_deep_research(self, query: str, limit: int = 10) -> list[dict[str, Any]] | None:
        """
        Execute deep research search via DDGS + Wayback CDX + optional urlscan.

        Activated only when GHOST_DEEP_RESEARCH=1.
        Fail-open: returns None on any error so original flow continues.

        Args:
            query: Search query string
            limit: Maximum number of fused results to return

        Returns:
            List of fused search results, or None if feature is disabled/error
        """
        if os.environ.get("GHOST_DEEP_RESEARCH") != "1":
            return None

        try:
            # Lazy imports — only loaded when feature flag is active
            from ..tools.ddgs_client import search_news_sync, search_text_sync
            from ..tools.deep_research_sources import urlscan_search, wayback_cdx_lookup
            from ..tools.search_fusion import top_k

            # Parallel fan-out: DDGS text, DDGS news, Wayback CDX, urlscan
            # F262D: safe_gather_ok is fail-soft (return_exceptions=True inside)
            ddgs_task = asyncio.to_thread(search_text_sync, query)
            news_task = asyncio.to_thread(search_news_sync, query)
            wayback_task = wayback_cdx_lookup(query, limit=8)
            urlscan_task = urlscan_search(query, size=8)

            # F262D: migrated asyncio.gather → safe_gather_ok (fail-soft invariant preserved)
            ddgs_rows, news_rows, wayback_rows, urlscan_rows = await safe_gather_ok(
                ddgs_task, news_task, wayback_task, urlscan_task,
                label="fetch_coordinator:1524",
            )

            # Sprint 4B: Gather hygiene - collect with explicit exception logging
            rows: list[dict[str, Any]] = []
            for part, label in [(ddgs_rows, "ddgs"), (news_rows, "news"),
                                 (wayback_rows, "wayback"), (urlscan_rows, "urlscan")]:
                if isinstance(part, list):
                    rows.extend(part)
                elif isinstance(part, Exception):
                    # Sprint 4B: Explicit exception logging (no silent failure)
                    _ev0 = type(part).__name__
                    logger.debug("[DEEP] {label} failed: {type(part).__name__}: {part}", label=label, part=part, _ev0=type(part).__name__)

            if not rows:
                return None

            fused = top_k(rows, k=limit)
            logger.info("[DEEP] query={query!r} → {raw_rows} raw rows → {fused_rows} fused", query=query, raw_rows=len(rows), fused_rows=len(fused))
            return fused

        except Exception as e:
            logger.debug("[DEEP] research failed: {e}", e=e)
            return None

    async def _do_shutdown(self, ctx: dict[str, Any]) -> None:
        """
        Cleanup on shutdown with proper drain.

        Sprint 4B: Adds small drain delay after closing sessions to allow
        SSL/TCP to finish gracefully.
        """
        logger.info(
            f"FetchCoordinator shutting down: {self._urls_fetched_count} URLs fetched | "
            f"AIMD window={self._aimd_concurrency:.1f} | "
            f"successes={self._telemetry['total_successes']} | "
            f"failures={self._telemetry['total_failures']}"
        )

        self._frontier.clear()
        # Recreate bloom filter instead of clear() (not available in RotatingBloomFilter)
        self._processed_urls = _create_dedup_strategy()
        self._cover_count = 0  # reset per-sprint cover counter

        # Issue #20: Stop checkpoint loop FIRST (uses _session_lmdb_env)
        await self._stop_checkpoint_loop()

        # F300M: Cleanup SessionManager and LMDB env — correct order:
        # 1. SessionManager.close() first (closes ThreadPoolExecutor)
        # 2. Then lmdb_env.close() (closes LMDB environment)
        if self._session_manager is not None:
            try:
                await self._session_manager.close()
            except Exception:  # noqa: BLE001
                pass
            self._session_manager = None
        if self._session_lmdb_env is not None:
            try:
                self._session_lmdb_env.close()
            except Exception:  # noqa: BLE001
                pass
            self._session_lmdb_env = None

        # F274: Cleanup darknet sessions via provider (closes transport singletons)
        from ..transport.darknet_session_provider import close_all as _close_darknet_sessions

        await _close_darknet_sessions()

        # Sprint 45: Lightpanda pool cleanup
        if self._lightpanda_pool is not None:
            try:
                await self._lightpanda_pool.close()
            except Exception:  # noqa: BLE001
                pass
            self._lightpanda_pool = None

        # Sprint 4B: Small drain to allow SSL/TCP to flush
        await asyncio.sleep(0.25)

    # ==========================================================================
    # Sprint F214Q: Cover traffic — probabilistic inline OPSEC noise
    # Invariant: cover traffic NESMÍ go to storage pipeline. Fire-and-forget only.
    # ==========================================================================

    def reset_cover_count(self) -> None:
        """Reset per-sprint cover traffic counter. Call at sprint teardown."""
        self._cover_count = 0

    async def _maybe_fire_cover_traffic(self, transport: str) -> None:
        """Probabilistically fire cover traffic after a successful real fetch.

        Pattern: probabilistic inline injection (not background task — too complex for M1).
        Rate: HLEDAC_COVER_TRAFFIC_RATE (default 0.15 = 15% chance per success).
        Limit: max _COVER_MAX fires per sprint (M1 RAM protection).
        Transport: MUST use identical transport as real request (Tor→Tor, clearnet→clearnet).

        Cover traffic URL goes to DuckDB via _cover_traffic_sink flag on CanonicalFinding.
        """
        if _COVER_RATE <= 0 or self._cover_count >= _COVER_MAX:
            return
        if not _ZERO_ATTR_ENGINE:
            return

        try:
            if random.random() < _COVER_RATE:
                # Generate transport-aware cover URLs (not query strings)
                cover_urls = _ZERO_ATTR_ENGINE.generate_cover_traffic_urls(
                    n_decoys=1, transport=transport
                )
                if not cover_urls:
                    return  # fail-soft: no URLs for this transport
                cover_url = cover_urls[0]
                self._cover_count += 1

                # Fire cover traffic with short random delay to desynchronize
                delay = random.uniform(0.5, 3.0)
                safe_create_task(
                    self._fire_cover_traffic_url(cover_url, delay, transport)
                )

                # Increment metrics counter
                from metrics_registry import get_metrics_registry  # lazy import mirrors transport/circuit_breaker.py:60
                get_metrics_registry().inc("cover_traffic_fired")
                logger.debug("[COVER] fired cover traffic #{self._cover_count} for transport={transport}", _cover_count=self._cover_count, transport=transport)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # fail-soft — cover traffic errors are silent

    async def _fire_cover_traffic_url(
        self, url: str, delay: float, transport: str
    ) -> None:
        """Fire a single cover traffic URL via the appropriate transport layer.

        Circuit breaker: skip if domain is blocked.
        Transport-aware: Tor→Tor SOCKS, I2P→I2P, clearnet→curl_cffi.
        Cover traffic is best-effort — never propagates exceptions.
        """
        try:
            await asyncio.sleep(delay)
        except Exception:
            return  # delay interrupted — skip

        # F3XX: httpx.URL is module-level import
        try:
            domain = httpx.URL(url).host
        except Exception:
            return

        # Circuit breaker check
        if hasattr(self, "_domain_blocked_until"):
            if self._domain_blocked_until.get(domain, 0) > time.time():
                return  # circuit open — skip cover fetch

        try:
            transport_lower = transport.lower()

            if transport_lower == "tor":
                # Tor SOCKS proxy — never curl_cffi directly for Tor
                try:
                    from ..transport.base import TransportConfig
                    from ..transport.tor_transport import get_tor_transport
                    tor = get_tor_transport()
                    if tor and await tor.is_running():
                        config = TransportConfig(url=url, method="GET", headers=None, body=None, timeout=10.0)
                        await tor.fetch(config)
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # Tor unavailable — skip silently

            elif transport_lower == "i2p":
                try:
                    from ..transport.base import TransportConfig
                    from ..transport.i2p_transport import get_i2p_transport
                    i2p = get_i2p_transport()
                    if i2p and i2p.is_running():
                        config = TransportConfig(url=url, method="GET", headers=None, body=None, timeout=10.0)
                        await i2p.fetch(config)
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # I2P unavailable — skip silently

            else:
                # clearnet / unknown — use curl_cffi
                try:
                    import curl_cffi.requests as _cffi

                    async with _cffi.AsyncSession(
                        impersonate="chrome131"
                    ) as session:
                        await session.get(url, timeout=10.0)
                except Exception:  # noqa: BLE001
                    pass  # noqa: BLE001  # cover fetch failures are silent

        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # fail-soft — cover traffic never crashes sprint

    async def _fire_cover_traffic(self, url: str, delay: float, transport: str) -> None:
        """Legacy wrapper — redirect to transport-aware implementation."""
        await self._fire_cover_traffic_url(url, delay, transport)

    # ── Sprint F-EXTRACT-2: pivot queue enqueue methods ──────────────────
    #
    # Extracted from SprintScheduler as part of GOD_OBJECT_ANALYSIS Phase 2.
    # State is owned by SprintScheduler; this coordinator accesses it via
    # provider callbacks. The `enqueue_pivot_provider` callback makes the
    # test-patch pattern `scheduler.enqueue_pivot = mock` work: the lambda
    # binds to the SprintScheduler instance, so attribute lookup resolves
    # to the patched mock at call time.
    #
    # PivotTask is imported lazily inside enqueue_pivot to break the
    # circular dependency (sprint_scheduler imports FetchCoordinator from
    # this module at module load).

    def enqueue_pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        confidence: float,
        degree: float = 1.0,
        task_type: str | None = None,
    ) -> None:
        """Enqueue a pivot task. Silently drops if queue is full (M1 8GB).

        Sprint 8VI §B.4: RL-adaptive priority — for generic_pivot task
        types, blend EMA reward with base priority.

        Sprint F-EXTRACT-2: moved from SprintScheduler. State
        (`_pivot_queue`, `_pivot_stats`) and helper (`_get_adaptive_priority`)
        accessed via provider callbacks.
        """
        # Late import — break circular dependency: sprint_scheduler
        # imports FetchCoordinator at module load.
        from hledac.universal.runtime.sprint_scheduler import PivotTask

        pivot_queue = self._pivot_queue_provider()
        if pivot_queue is None:
            return
        if pivot_queue.full():
            return

        # Multi-pivot: enqueue ALL applicable task types per IOC
        if task_type is not None:
            task_types_list: list[str] = [task_type]
        else:
            task_types_list = {
                "cve": ["cve_to_github", "cve_to_academic"],
                "ipv4": ["ip_to_ct", "ip_to_greynoise", "shodan_enrich"],
                "ipv6": ["ip_to_ct"],
                "domain": ["domain_to_dns", "domain_to_wayback", "domain_to_pdns",
                           "domain_to_ct", "ahmia_search", "rdap_lookup"],
                "md5": ["hash_to_mb"],
                "sha256": ["hash_to_mb"],
                "sha1": ["hash_to_mb"],
                "url": ["wayback_search", "commoncrawl_search", "paste_keyword_search",
                        "github_dork", "multi_engine_search"],
                "hypothesis": ["multi_engine_search", "rdap_lookup"],
            }.get(ioc_type, [])

        if not task_types_list:
            return

        base_priority = confidence * max(1.0, float(degree))
        pivot_stats = self._pivot_stats_provider()
        for tt in task_types_list:
            # Sprint 8VI §B.4: RL-adaptive priority blend
            effective = self._adaptive_priority_provider(tt, base_priority)
            priority = -effective
            task = PivotTask(priority, ioc_type, ioc_value, tt)
            try:
                pivot_queue.put_nowait(task)
                if pivot_stats is not None:
                    pivot_stats["total"] = pivot_stats.get("total", 0) + 1
            except asyncio.QueueFull:
                pass

    def enqueue_hypothesis_pivot(
        self,
        ioc_value: str,
        ioc_type: str = "hypothesis",
        confidence: float = 0.7,
        depth: int = 1,
    ) -> bool:
        """Enqueue a hypothesis-driven pivot task with bounded caps.

        Sprint F193B: Bounded hypothesis → finding feedback loop.
        Enforces max_hypothesis_depth (default 3) and
        max_hypothesis_queries (default 10). Returns True if
        enqueued, False if dropped due to cap.

        Sprint F-EXTRACT-2: moved from SprintScheduler. State
        (`_hypothesis_query_count`, `_hypothesis_depth`) accessed via
        read/write provider callbacks. Cap values read from
        `sprint_config_provider()`. Actual enqueue is delegated via
        `enqueue_pivot_provider` (test-patchable).
        """
        config = self._sprint_config_provider()
        if config is not None and depth > config.max_hypothesis_depth:
            logger.debug(
                f"[F193B] Hypothesis pivot dropped: depth {depth} > "
                f"max {config.max_hypothesis_depth}"
            )
            return False
        if config is not None and self._hypothesis_query_count_provider() >= config.max_hypothesis_queries:
            logger.debug(
                f"[F193B] Hypothesis pivot dropped: query count "
                f"{self._hypothesis_query_count_provider()} >= max "
                f"{config.max_hypothesis_queries}"
            )
            return False

        # Delegate to the bound SprintScheduler.enqueue_pivot wrapper
        # (or test-patched mock). The lambda provider captures `self`
        # by reference, so attribute lookup at call time resolves to
        # the current state of the instance.
        self._enqueue_pivot_provider(
            ioc_value=ioc_value,
            ioc_type=ioc_type,
            confidence=confidence,
            degree=float(depth),
            task_type=None,
        )

        # Update hypothesis state via setter providers
        self._hypothesis_query_count_setter(self._hypothesis_query_count_provider() + 1)
        self._hypothesis_depth_setter(
            max(self._hypothesis_depth_provider(), depth)
        )
        logger.debug(
            f"[F193B] Hypothesis pivot enqueued: {ioc_value} "
            f"(depth={depth}, total_queries={self._hypothesis_query_count_provider()})"
        )
        return True

    # ==========================================================================
    # Issue #20: Session LMDB checkpoint — periodic sync for bounded data loss
    # ==========================================================================

    async def _session_checkpoint_loop(self, interval_s: float = 30.0) -> None:
        """Periodically sync session LMDB to guarantee bounded data loss window.

        With sync=True on session LMDB (critical=True), each write is durable.
        This loop provides an additional guarantee: even if writes are batched
        by the OS, max data loss is bounded by interval_s.

        Runs only while _running is True. Cancelled automatically on shutdown.
        """
        import logging as _logging

        _logger = _logging.getLogger("hledac.fetch.checkpoint")
        while self._running:
            await asyncio.sleep(interval_s)
            if not self._running:
                break
            env = self._session_lmdb_env
            if env is not None:
                try:
                    env.sync()
                    _logger.debug("[Issue#20] session LMDB checkpoint synced")
                except Exception:  # noqa: BLE001
                    # Fail-soft: checkpoint miss should not crash the loop
                    pass

    def _start_checkpoint_loop(self) -> None:
        """Start the session checkpoint background task. Idempotent."""
        if self._session_checkpoint_task is None and self._session_lmdb_env is not None:
            self._running = True
            self._session_checkpoint_task = safe_create_task(
                self._session_checkpoint_loop(),
                name="session-lmdb-checkpoint",
            )

    async def _stop_checkpoint_loop(self) -> None:
        """Stop the session checkpoint loop gracefully."""
        self._running = False
        task = self._session_checkpoint_task
        if task is not None:
            self._session_checkpoint_task = None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
