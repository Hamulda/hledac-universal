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
import contextlib
import ipaddress
import os
import platform
import secrets
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
import dataclasses
import re
from typing import Any

from operator import attrgetter, itemgetter
import httpx
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
import orjson
from cachetools import TTLCache

# -----------------------------------------------------------------------------
# Claim extraction patterns (module-level for performance)
# -----------------------------------------------------------------------------
_IP_PATTERN = re.compile(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b')
_DOMAIN_PATTERN = re.compile(r'\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b')
_SHA256_PATTERN = re.compile(r'\b[a-fA-F0-9]{64}\b')
_URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

from hledac.universal._core.capabilities import (
    AIOHTTP,
    CAPS,
    DARKNET_CONNECTOR,
    HINTS,
    LIGHTPANDA,
    OTEL,
    PAYWALL_BYPASS,
    SESSION,
    STEALTH_MANAGER,
    ZERO_ATTR,
    ZSTD,
)
from hledac.universal._core.constants import NETWORK
from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
from hledac.universal.runtime.logging_setup import get_logger
from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator, make_privacy_allocator
from hledac.universal.tools.file_cache import apply_fcntl_nocache as _apply_fcntl_nocache
from hledac.universal.tools.zstd_compressor import ZstdCompressor
from hledac.universal.utils.asyncx import (
    BoundedPerHostGate,
    DomainRateLimiter,
    async_getaddrinfo,
    parallel,
    safe_create_task,
    safe_wait_for,
)
from hledac.universal.utils.batch_dns import get_batch_dns_resolver
from hledac.universal.utils.flow_trace import (
    is_enabled,
    trace_counter,
    trace_dedup_decision,
    trace_fetch_end,
    trace_fetch_start,
)
from hledac.universal.utils.locks import LazyAsyncioLock  # ISSUE-011: asyncio-safe lock

from ..tools.url_dedup import DeduplicationStrategy, dedupe_url_list
from collections import deque
from ..knowledge.cross_sprint_gate import get_cross_sprint_gate
from ..knowledge.entity_confirmation import get_entity_confirmation_service, get_entity_confirmation_service_sync
from ..knowledge.sprint_delta_index import MmapDeltaIndex, get_mmap_delta_index

from .base import UniversalCoordinator

# ── Cognitive Saturation Detection ─────────────────────────────────────────────
# Global detector registry — allows runtime/cognitive_saturation_detector.py to
# be injected without circular imports. Set via set_cognitive_saturation_detector().
_COGNITIVE_SATURATION_DETECTOR: Any = None


def set_cognitive_saturation_detector(detector: Any) -> None:
    """Set the global CognitiveSaturationDetector instance.

    Called by SprintLifecycleManager on initialization to wire the detector.
    """
    global _COGNITIVE_SATURATION_DETECTOR
    _COGNITIVE_SATURATION_DETECTOR = detector


def get_cognitive_saturation_detector() -> Any:
    """Get the global CognitiveSaturationDetector instance, or None if not set."""
    return _COGNITIVE_SATURATION_DETECTOR

# R6: Centralized Rust access — all hledac_rust_extensions symbols route through
# core.rust_backend, ensuring ABI checking, capability scoring, and graceful fallback.
from ..utils.robots_parser import RobotsDocument

# R6: Centralized Rust access
from hledac.universal._core.rust_backend import rust
from _core import aclose

PyAIMDController = rust.raw.PyAIMDController  # type: ignore[assignment]  # None if N/A

# CAPS-based availability flags (set after imports)
_otel_mod = CAPS.require(OTEL)
_otel_instrumented = _otel_mod if _otel_mod is not None else None
if _otel_instrumented is None:
    from hledac.universal.telemetry import instrumented as _otel_instrumented

_zstd_mod = CAPS.require(ZSTD)
_aiomod = CAPS.require(AIOHTTP)
_lp_manager_cls = CAPS.require(LIGHTPANDA)
_session_mgr_cls = CAPS.require(SESSION)
_hints_extractor_cls = CAPS.require(HINTS)
_zero_attr_cls = CAPS.require(ZERO_ATTR)

ZSTD_AVAILABLE = CAPS.is_available('zstd')
LIGHTPANDA_AVAILABLE = CAPS.is_available('lightpanda')
SESSION_AVAILABLE = CAPS.is_available('session')
HINTS_AVAILABLE = CAPS.is_available('deep_web_hints')

_ZERO_ATTR_ENGINE = _zero_attr_cls

_COVER_RATE = min(max(FeatureFlags.get_float(FeatureFlag.COVER_TRAFFIC_RATE, 0.05), 0.0), 1.0)
_COVER_MAX = 2


# T2: Fast URL host/path extraction — zero allocation, no httpx.URL parse.
# Used in all hot paths where only host or path is needed.
def _fast_url_host(url: str) -> str:
    """Extract host from URL via fast string slicing (10-50× faster than httpx.URL)."""
    at_slashes = url.find('://')
    if at_slashes < 0:
        return ''
    host_start = at_slashes + 3
    end = len(url)
    for i in range(host_start, end):
        c = url[i]
        if c in ('/', ':', '?', '#'):
            end = i
            break
    return url[host_start:end]


def _fast_url_path(url: str) -> str:
    """Extract path from URL via fast string slicing."""
    at_slashes = url.find('://')
    if at_slashes < 0:
        return '/'
    path_start = url.find('/', at_slashes + 3)
    if path_start < 0:
        return '/'
    query = url.find('?', path_start)
    if query < 0:
        return url[path_start:] or '/'
    return url[path_start:query] or '/'


def _create_dedup_strategy() -> DeduplicationStrategy:
    """Create the dedup strategy used by FetchCoordinator.

    P1-3: Uses create_rotating_bloom_filter() which prefers MmapBloomFilter
    (file-backed, cross-restart persistence) when Rust extension available.
    Falls back to in-memory Rust BloomFilter then probables.
    The mmap filter persists across restarts so re-fetching the same URL
    in a later sprint doesn't re-download it.
    """
    from ..tools.url_dedup import create_rotating_bloom_filter
    return create_rotating_bloom_filter(est_elements=200000)


_CP_NOT_CALLED = object()
_CP_RETURNED_NONE = object()

# R6: DNS via hickory-dns — centralized through rust_backend
# dns feature is opt-in (not in default Cargo features). When built with
# --features dns, rust.dns returns the submodule; otherwise None.
_RUST_DNS: Any = rust.dns
_RUST_DNS_ENABLED: bool = False
if _RUST_DNS is not None:
    _RUST_DNS_ENABLED = FeatureFlags.get(FeatureFlag.DNS)


def _rust_dns_prefetch(hostnames: list[str]) -> dict[str, list[str]]:
    """Prefetch DNS via hickory-dns (rust.dns.prefetch).

    Falls back to empty dict on any error (fail-soft invariant).
    Respects HLEDAC_ENABLE_DNS env flag.
    """
    if not _RUST_DNS_ENABLED:
        return {}
    try:
        return _RUST_DNS.prefetch(hostnames)
    except Exception:  # noqa: BLE001 — fail-soft: any error returns empty
        return {}

_stealth_tbc = CAPS.require(STEALTH_MANAGER)
if _stealth_tbc is None:

    class TokenBucketController:
        """Token Bucket pro řízení concurrency (inline fallback)."""
        __slots__ = ('_rate', '_capacity', '_tokens', '_last_refill', '_cond')

        def __init__(self, rate: int=5, capacity: int=10) -> None:
            self._rate = rate
            self._capacity = capacity
            self._tokens = capacity
            self._last_refill = time.time()
            self._cond = asyncio.Condition()

        async def acquire(self) -> None:
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

        async def release(self) -> None:
            pass
else:
    TokenBucketController = _stealth_tbc
logger = get_logger(__name__)

# Crypto-safe jitter — F350M-R
_JITTER_RNG = secrets.SystemRandom()
_nw = NETWORK()
TIMEOUT_CLEARNET_API = _nw.clearnet_api
TIMEOUT_CLEARNET_HTML = _nw.clearnet_html
TIMEOUT_TOR = _nw.tor
TIMEOUT_I2P = _nw.i2p
TIMEOUT_GOPHER = _nw.gopher
CONCURRENCY_TOR = 4
CONCURRENCY_CLEARNET = 12
CONCURRENCY_API = 5
CONCURRENCY_GLOBAL_MAX = 25
AIMD_ADDITIVE_INCREMENT = 2
AIMD_DECREASE_FACTOR = 0.75
AIMD_MIN_CONCURRENCY = 1
AIMD_MAX_CONCURRENCY = 25
AIMD_SUCCESS_THRESHOLD = 2
AIMD_DECREASE_BY_STATE = {'ok': 1.0, 'soft_warn': 0.75, 'warn': 0.5, 'critical': 0.25, 'emergency': 0.0}

# E4 FIX: Global in-flight response body memory limits for M1 8GB.
# AIMD window (1-25) × 10MB = 250MB worst-case without this cap.
# Strategy: semaphore-based byte budget + per-content-type max_bytes reduction.
# Conservative estimate: use 10MB (article max) as permit size.
_INFLIGHT_BYTES_BUDGET = 50 * 1024 * 1024  # 50 MB total in-flight budget
_INFLIGHT_BYTES_PERMIT = 10 * 1024 * 1024  # 10 MB per fetch permit (5 concurrent max)
_INFLIGHT_SEMAPHORE: asyncio.Semaphore | None = None  # Lazily initialized

# BLITZ-13: Aggressive-mode concurrency constants.
# When blitz mode is active, AIMD starts at the maximum allowed concurrency
# instead of the conservative CONCURRENCY_CLEARNET default, skipping the
# additive-increase ramp-up phase entirely. Multiplicative decrease on
# errors still applies for safety.
# M1 8GB bounds: 16 clearnet + 8 Tor is safe within the 8GB UMA budget.
BLITZ_CONCURRENCY_CLEARNET = 16
BLITZ_CONCURRENCY_TOR = 8
# Env var gate — BLITZ-13: ON by default. Set HLEDAC_BLITZ_FETCH=0 to opt out.
#   When ON, AIMD starts at BLITZ_CONCURRENCY_CLEARNET (16) instead of CONCURRENCY_CLEARNET (12).
_ENV_BLITZ_FETCH = FeatureFlags.get_str(FeatureFlag.BLITZ_FETCH, '1')


def _try_load_aimd_controller(initial_window: float) -> AIMDWindow | PyAIMDController:
    """
    ISSUE 2.2: Try to load PyAIMDController from Rust extension.

    Returns:
        PyAIMDController if Rust extension available and built with `data` feature.
        AIMDWindow (Python fallback) otherwise.
    """
    if PyAIMDController is not None:
        try:
            return PyAIMDController(initial_window=initial_window)
        except (TypeError, OSError):  # noqa: BLE001
            pass  # Fall through to Python fallback
    return AIMDWindow(initial=initial_window)


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
    __slots__ = ('_window', '_successes', '_failures', '_stats', '_lock', '_window_lock')

    def __init__(self, initial: float) -> None:
        self._window = float(initial)
        self._successes = 0
        self._failures = 0
        self._stats: dict[str, int] = {'increases': 0, 'decreases': 0, 'window_changes': 0}
        self._lock = asyncio.Lock()
        self._window_lock = asyncio.Lock()

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
            return (expected + 1, True)
        return (self._successes, False)

    async def on_success(self, multiplier: float=1.0) -> tuple[float, int]:
        """
        Record one success, potentially increasing the window.

        Fast path (no lock): 99% of calls — counter increment + threshold check
        only. Lock is NOT acquired unless threshold is crossed and window update
        is needed.

        Lock-free CAS loop avoids 50-100 µs lock acquisition overhead per call
        when 100+ coroutines complete simultaneously (Issue #15 partial fix).
        """
        new_successes: int
        for _ in range(5):
            current = self._successes
            new_successes, swapped = self._cas_successes(current)
            if swapped:
                break
        else:
            async with self._lock:
                self._successes += 1
                new_successes = self._successes
        if new_successes < AIMD_SUCCESS_THRESHOLD:
            return (self._window, new_successes)
        async with self._window_lock:
            if self._successes < AIMD_SUCCESS_THRESHOLD:
                return (self._window, self._successes)
            self._successes = 0
            old = self._window
            self._window = min(self._window + AIMD_ADDITIVE_INCREMENT * multiplier, AIMD_MAX_CONCURRENCY)
            if self._window != old:
                self._stats['increases'] += 1
                self._stats['window_changes'] += 1
            return (self._window, 0)

    async def on_failure(self, uma_state: str='ok') -> tuple[float, int]:
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
            self._window = max(self._window * decrease_factor, AIMD_MIN_CONCURRENCY)
            if self._window != old:
                self._stats['decreases'] += 1
                self._stats['window_changes'] += 1
                self._successes = 0
        return (self._window, new_failures)

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
            self._stats['window_changes'] += 1

    async def blitz_boost(self, target: float) -> float:
        """
        BLITZ-13: Boost the AIMD window to a target concurrency immediately.

        Skips the additive-increase ramp-up phase. Resets success counter
        to prevent immediate post-boost increase. Multiplicative decrease
        on failures still applies for safety.

        Returns the new window value.
        """
        async with self._window_lock:
            old = self._window
            self._window = float(target)
            self._successes = 0
            if self._window != old:
                self._stats['window_changes'] += 1
            return self._window

    def reset_successes(self) -> None:
        """Reset success counter (called externally after window increase)."""
        self._successes = 0

_PRIORITY_API = 0
_PRIORITY_JSON = 5
_PRIORITY_CLEARNET_HTML = 15
_PRIORITY_TOR = 30
_PRIORITY_I2P = 40
_PRIORITY_OTHER = 50
MAX_EVIDENCE_IDS_PER_STEP = 10
NOCACHE_THRESHOLD_BYTES = 50 * 1024 * 1024
F_NOCACHE = 48 if platform.system() == 'Darwin' else None

def apply_fcntl_nocache(fd: int, content_length: int | None) -> None:
    """Wrapper for backward compatibility — delegates to tools/file_cache.py."""
    _apply_fcntl_nocache(fd, content_length)


# =============================================================================
# BREAKTHROUGH #2: Speculative Prefetch via Real-Time Link Prediction
# =============================================================================

@dataclasses.dataclass(slots=True, frozen=True)
class SpeculativePrefetchResult:
    """
    BREAKTHROUGH #2: Result of speculative prefetch phase.
    
    Contains URLs to speculatively fetch based on link prediction.
    """
    prefetch_urls: tuple[str, ...] = dataclasses.field(default_factory=tuple)
    prefetch_count: int = 0
    dedup_skipped: int = 0
    dns_prefetched: int = 0
    latency_ms: float = 0.0


class SpeculativePrefetcher:
    """
    BREAKTHROUGH #2: Speculative prefetch using real-time link prediction.
    
    Architecture:
    ```
    IOC extraction → buffer IOCs → LINK PREDICTION (streaming) 
                  → prefetch candidates → fetch_coordinator (steal prefetch)
    ```
    
    Performance target:
    - Prefetch coverage: 70%+ (vs 0% in batch-only mode)
    - Link prediction latency: ~50ms (vs ~5s batch)
    - IGD improvement: +35%
    
    M1 8GB constraints:
    - MAX_CLAIMS = 5000 (bounded queue)
    - MAX_PREFETCH_URLS = 1000 (dedup filter)
    - Memory-pressure adaptive thresholds
    """
    
    __slots__ = (
        '_adjacency',
        '_available',
        '_coordinator',
        '_dedup_filter',
        '_dns_cache',
        '_frontier',
        '_link_predictor',
        '_max_prefetch',
        '_pending_nodes',
        '_prefetch_urls',
        '_rust_dns',
        '_rust_dns_enabled',
        '_streaming_task',
        '_total_predictions',
    )
    
    # Bounds for M1 8GB safety
    MAX_PREFETCH_URLS = 1000
    MAX_PENDING_NODES = 5000
    MAX_ADJACENCY_SIZE = 10000
    
    def __init__(
        self,
        coordinator: FetchCoordinator,
        *,
        max_prefetch: int = 100,
    ) -> None:
        self._coordinator = coordinator
        self._max_prefetch = max_prefetch
        
        # Adjacency list for streaming link prediction
        self._adjacency: dict[int, list[int]] = {}
        self._pending_nodes: list[int] = []
        
        # Prefetch queue
        self._prefetch_urls: deque[str] = deque(maxlen=self.MAX_PREFETCH_URLS)
        
        # Front-lookup dedup filter (RotatingBloomFilter integration)
        # Uses the coordinator's dedup strategy for cross-request persistence
        self._dedup_filter: DeduplicationStrategy | None = None
        
        # DNS prefetch cache
        self._dns_cache: TTLCache[str, list[str]] = TTLCache(maxsize=512, ttl=300)
        
        # Streaming link predictor
        self._link_predictor: Any = None
        self._streaming_task: asyncio.Task | None = None
        
        # Stats
        self._total_predictions = 0
        self._available = False
        
        # Rust DNS prefetch availability
        self._rust_dns = _RUST_DNS
        self._rust_dns_enabled = _RUST_DNS_ENABLED
        
        # Initialize async link predictor
        self._init_link_predictor()
    
    def _init_link_predictor(self) -> None:
        """Initialize streaming link predictor."""
        try:
            from hledac.universal.knowledge.link_prediction import (
                StreamingLinkPredictor,
                LinkPredictorConfig,
            )
            
            # Get DuckDB path from coordinator context
            db_path = self._get_duckdb_path()
            if db_path:
                config = LinkPredictorConfig(
                    streaming_mode=True,
                    flush_interval_ms=50,
                    max_pending_nodes=100,
                    generate_url_candidates=True,
                )
                self._link_predictor = StreamingLinkPredictor(db_path, config)
                self._available = True
                logger.debug("[BREAKTHROUGH-2] SpeculativePrefetcher initialized")
            else:
                logger.debug("[BREAKTHROUGH-2] No DuckDB path available")
        except ImportError as e:
            logger.warning("[BREAKTHROUGH-2] Streaming link predictor unavailable: %s", e)
            self._available = False
    
    def _get_duckdb_path(self) -> str | None:
        """Get DuckDB path from coordinator context."""
        try:
            ctx = self._coordinator._ctx
            if ctx:
                return ctx.get('duckdb_path')
        except Exception:
            pass
        return None
    
    def add_ioc_node(self, node_id: int, neighbors: list[int], ioc_value: str | None = None) -> None:
        """
        Add a newly discovered IOC node for link prediction.
        
        BREAKTHROUGH #2: Called during ACTIVE phase extraction.
        M1 8GB: Bounded to MAX_PENDING_NODES.
        
        Args:
            node_id: IOC node ID (from Kuzu graph)
            neighbors: List of neighbor node IDs (observed edges)
            ioc_value: Optional IOC value string (domain name, URL, IP, etc.)
                      Used for generating real URL candidates in prefetch.
        """
        if len(self._pending_nodes) >= self.MAX_PENDING_NODES:
            # Evict oldest entries
            evict_count = self.MAX_PENDING_NODES // 10
            evicted = self._pending_nodes[:evict_count]
            self._pending_nodes = self._pending_nodes[evict_count:]
            # FIX: Clean up evicted nodes from adjacency to prevent memory leak
            for evicted_id in evicted:
                if evicted_id in self._adjacency:
                    del self._adjacency[evicted_id]
        
        self._pending_nodes.append(node_id)
        
        # FIX: Update adjacency with bounded memory
        # Add bidirectional edges for graph topology
        if len(self._adjacency) < self.MAX_ADJACENCY_SIZE:
            if node_id not in self._adjacency:
                self._adjacency[node_id] = []
            for n in neighbors:
                if n not in self._adjacency[node_id]:
                    self._adjacency[node_id].append(n)
                # Add reverse edge for undirected graph
                if n not in self._adjacency:
                    self._adjacency[n] = []
                if node_id not in self._adjacency[n]:
                    self._adjacency[n].append(node_id)
        
        # FIX: Use coordinator's dedup filter for prefetch URL deduplication
        # This ensures prefetch URLs respect the same dedup constraints as normal URLs
        if self._dedup_filter is None and self._coordinator is not None:
            self._dedup_filter = getattr(self._coordinator, '_processed_urls', None)
        
        # Notify link predictor with IOC value for real URL generation
        if self._link_predictor:
            self._link_predictor.add_node(node_id, neighbors, ioc_value)
    
    async def execute_speculative_prefetch(self) -> SpeculativePrefetchResult:
        """
        BREAKTHROUGH #2: Execute speculative prefetch phase.
        
        Integrates with FetchCoordinator._do_step pipeline:
        - Phase 2.5: After DNS resolve/dedup, before priority candidates
        - Consumes link prediction output
        - Adds prefetch URLs to coordinator frontier
        - Uses RotatingBloomFilter for dedup
        - DNS prefetch via Rust async FFI
        
        Returns:
            SpeculativePrefetchResult with prefetch URLs and stats
        """
        import time
        start = time.monotonic()
        
        if not self._available or not self._link_predictor:
            return SpeculativePrefetchResult()
        
        prefetch_count = 0
        dedup_skipped = 0
        dns_prefetched = 0
        prefetch_urls: list[str] = []
        
        # FIX: Get dedup filter - prefer coordinator's RotatingBloomFilter
        # This ensures prefetch URLs respect the same dedup constraints as normal URLs
        dedup_filter = self._dedup_filter
        if dedup_filter is None and self._coordinator is not None:
            dedup_filter = getattr(self._coordinator, '_processed_urls', None)
        
        # Get streaming predictions
        try:
            async for batch in self._link_predictor.stream_predictions():
                for edge in batch.edges:
                    self._total_predictions += 1
                    
                    # Generate URL candidates from predicted edge
                    for url in edge.url_candidates[:self.MAX_PREFETCH_URLS]:
                        # FIX: Dedup using dedup filter (RotatingBloomFilter from coordinator)
                        if dedup_filter is not None and url in dedup_filter:
                            dedup_skipped += 1
                            continue
                        
                        prefetch_urls.append(url)
                        prefetch_count += 1
                        
                        # Check max bound
                        if prefetch_count >= self._max_prefetch:
                            break
                
                if prefetch_count >= self._max_prefetch:
                    break
        except Exception as e:
            logger.debug("[BREAKTHROUGH-2] Prefetch error: %s", e)
        
        # DNS prefetch via Rust async FFI (tokio::net::lookup_host)
        if prefetch_urls and self._rust_dns_enabled:
            hosts = list(set(_fast_url_host(u) for u in prefetch_urls if _fast_url_host(u)))
            if hosts:
                dns_result = _rust_dns_prefetch(hosts)
                if dns_result:
                    dns_prefetched = sum(len(ips) for ips in dns_result.values())
                    # Cache DNS results for later fetch
                    for host, ips in dns_result.items():
                        self._dns_cache[host] = ips
        
        # FIX: Add prefetch URLs to coordinator frontier via append method (deque)
        if self._coordinator is not None:
            for url in prefetch_urls:
                self._coordinator._frontier.append(url)
        
        elapsed_ms = (time.monotonic() - start) * 1000
        
        return SpeculativePrefetchResult(
            prefetch_urls=tuple(prefetch_urls),
            prefetch_count=prefetch_count,
            dedup_skipped=dedup_skipped,
            dns_prefetched=dns_prefetched,
            latency_ms=elapsed_ms,
        )
    
    async def prefetch_dns_batch(self, urls: list[str]) -> dict[str, list[str]]:
        """
        DNS prefetch for a batch of URLs using Rust async FFI.
        
        BREAKTHROUGH #2: Uses tokio::net::lookup_host for async DNS.
        Falls back to socket.getaddrinfo on error.
        """
        hosts = list(set(_fast_url_host(u) for u in urls if _fast_url_host(u)))
        results: dict[str, list[str]] = {}
        
        for host in hosts:
            # Check cache first
            if host in self._dns_cache:
                results[host] = self._dns_cache[host]
                continue
            
            # Use Rust DNS if available
            if self._rust_dns_enabled:
                try:
                    ips = await self._rust_dns.prefetch([host])
                    if host in ips:
                        results[host] = ips[host]
                        self._dns_cache[host] = ips[host]
                        continue
                except Exception:
                    pass
            
            # Fallback to socket
            try:
                import socket
                results_list = socket.getaddrinfo(host, 0)
                ips = sorted(set(r[4][0] for r in results_list))
                results[host] = ips
                self._dns_cache[host] = ips
            except (socket.gaierror, OSError):
                results[host] = []
        
        return results
    
    @property
    def total_predictions(self) -> int:
        """Total predictions made so far."""
        return self._total_predictions
    
    @property
    def prefetch_queue_size(self) -> int:
        """Current prefetch queue size."""
        return len(self._prefetch_urls)
    
    @property
    def is_available(self) -> bool:
        """Whether speculative prefetch is available."""
        return self._available
    
    def report_ioc_discovery(self, node_id: int, neighbors: list[int], ioc_value: str | None = None) -> None:
        """
        BREAKTHROUGH #2: Report IOC discovery to SpeculativePrefetcher.
        
        Called when new IOCs are extracted from fetched content.
        This enables real-time link prediction during ACTIVE phase.
        
        Args:
            node_id: IOC node ID (typically xxhash64 of IOC value)
            neighbors: List of neighbor node IDs (observed edges from fetch)
            ioc_value: Optional IOC value string (domain name, URL, IP, etc.)
        """
        self.add_ioc_node(node_id, neighbors, ioc_value)


# =============================================================================
# F360-R: Phase Result Dataclasses (slots=True for M1 8GB memory efficiency)
# =============================================================================
# These dataclasses define clear phase boundaries with typed inputs/outputs.
# Using __slots__ reduces per-instance memory overhead on M1 8GB.

@dataclasses.dataclass(slots=True, frozen=True)
class _PrefetchResult:
    """Result of preflight phase: rate limiting, privacy, AIMD checks."""
    skip_fetch: bool = False
    skip_reason: str | None = None
    host_name: str = ''
    host_sem: asyncio.Semaphore | None = None
    privacy_lane: str = 'clearnet'
    quinn_viable: bool = False
    # P1-6 FIX: Track whether AIMD semaphore was acquired to avoid
    # spurious release (early skip) or double-release (DNS blocked).
    aimd_acquired: bool = False


@dataclasses.dataclass(slots=True, frozen=True)
class _DnsCircuitResult:
    """Result of DNS + circuit breaker phase."""
    skip_fetch: bool = False
    skip_result: dict[str, Any] | None = None
    dns_safe: bool = True
    dns_meta: dict[str, Any] = dataclasses.field(default_factory=dict)
    canonical_allowed: bool = True
    canonical_reason: str = ''
    canonical_retry_after: float = 0.0
    resolve: dict[str, str] | None = None
    pre_acquired_tor_session: Any | None = None
    pre_acquired_i2p_session: Any | None = None
    url_transport: Any = None
    route_decision: Any = None
    proxy: str | None = None
    quinn_viable: bool = False
    # P1-6 CRITICAL FIX: Track whether AIMD was acquired in preflight phase.
    # When DNS blocks, _execute_dns_circuit_phase releases the semaphore.
    # _cleanup_fetch_resources uses this to skip its own release, preventing double-release.
    aimd_acquired: bool = False


class FetchCoordinatorConfig(Struct, frozen=True):
    """Configuration for FetchCoordinator."""
    max_urls_per_step: int = 5
    max_evidence_per_step: int = 10
    enable_security_check: bool = True
    enable_domain_limiter: bool = True
    budget_network_calls: int = 50
    budget_snapshots: int = 20

class FetchCoordinator(UniversalCoordinator):
    """
    Coordinator for fetch/crawl pipeline delegation.

    Responsibilities:
    - Pop URLs from frontier (bounded)
    - Run fetch pipeline with security checks
    - Create evidence packets
    - Return bounded outputs (IDs, counts, stop signals)

    A5-02: evidence_sink parameter enables Dependency Inversion —
    FetchCoordinator never imports EvidenceLog directly.
    """
    __slots__ = ('_adaptive_priority_provider', '_aimd', '_aimd_semaphore', '_base_retry_delay', '_batch_cp_result', '_blitz_mode', '_capacity', '_captcha_detections', '_captcha_detector', '_clearance_jar', '_concurrency', '_concurrency_provider', '_config', '_cooldown_seconds', '_cover_count', '_cross_sprint_gate', '_entity_confirmation_service', '_ctx', '_current_geo_context', '_darknet_connector', '_dedup_lock', '_domain_rate_limiter', '_effective_ua', '_enqueue_pivot_provider', '_entropy_bridge_queue', '_entropy_bridge_task', '_entropy_alerts_processed', '_entropy_prune_counter', '_evidence_ids', '_evidence_sink', '_frontier', '_geo_proxies', '_gopher_transport', '_gopher_transport_enabled', '_hints_extractor', '_host_ips_cache', '_host_ips_inflight', '_http_cache_enabled', '_http_cache_transport', '_hypothesis_depth_provider', '_hypothesis_depth_setter', '_hypothesis_query_count_provider', '_hypothesis_query_count_setter', '_lightpanda_lock', '_lightpanda_pool', '_lightpanda_pool_started', '_max_backoff_delay', '_max_retries', '_micro_sprint_queue', '_micro_sprint_original_findings', '_micro_sprint_worker_task', '_mmap_delta_index', '_orchestrator', '_paywall_bypass', '_per_host_gate', '_per_host_limit', '_pivot_queue_provider', '_pivot_stats_provider', '_privacy_allocator', '_privacy_lock', '_processed_urls', '_retry_budget', '_retry_budget_lock', '_retry_budget_max', '_retry_budget_window', '_robots_parser', '_running', '_session_checkpoint_task', '_session_lmdb_env', '_session_manager', '_sprint_config_provider', '_sprint_remaining_provider', '_stop_reason', '_swarm_dag', '_swarm_dag_rebalance_task', '_telemetry', '_tor_transport', '_tor_transport_enabled', '_urls_fetched_count', '_zstd')

    def __init__(self, config: FetchCoordinatorConfig | None=None, max_concurrent: int=3, blitz_mode: bool=True, pivot_queue_provider: Callable[[], Any]=lambda: None, pivot_stats_provider: Callable[[], dict] | None=None, hypothesis_query_count_provider: Callable[[], int]=lambda: 0, hypothesis_query_count_setter: Callable[[int], None]=lambda v: None, hypothesis_depth_provider: Callable[[], int]=lambda: 0, hypothesis_depth_setter: Callable[[int], None]=lambda v: None, sprint_config_provider: Callable[[], Any]=lambda: None, adaptive_priority_provider: Callable[[str, float], float]=lambda tt, base: base, enqueue_pivot_provider: Callable[..., Any]=lambda **kw: None, concurrency_provider: Callable[[], tuple[int, int, str, bool] | None] | None=None, sprint_remaining_provider: Callable[[], float | None]=lambda: None, evidence_sink: object | None=None) -> None:
        super().__init__(name='FetchCoordinator', max_concurrent=max_concurrent)
        self._config = config or FetchCoordinatorConfig()
        self._pivot_queue_provider = pivot_queue_provider
        self._pivot_stats_provider = pivot_stats_provider
        self._hypothesis_query_count_provider = hypothesis_query_count_provider
        self._hypothesis_query_count_setter = hypothesis_query_count_setter
        self._hypothesis_depth_provider = hypothesis_depth_provider
        self._hypothesis_depth_setter = hypothesis_depth_setter
        self._sprint_config_provider = sprint_config_provider
        self._adaptive_priority_provider = adaptive_priority_provider
        self._sprint_remaining_provider = sprint_remaining_provider
        self._enqueue_pivot_provider = enqueue_pivot_provider
        self._concurrency_provider = concurrency_provider
        self._batch_cp_result = _CP_NOT_CALLED
        self._frontier: deque = deque(maxlen=1000)
        self._processed_urls: DeduplicationStrategy = _create_dedup_strategy()
        self._cross_sprint_gate = get_cross_sprint_gate()
        # [NEXTGEN-04]: Initialize MmapDeltaIndex for zero-latency sprint caching
        self._mmap_delta_index: MmapDeltaIndex = get_mmap_delta_index()
        self._entity_confirmation_service = get_entity_confirmation_service_sync()
        self._evidence_ids: deque = deque(maxlen=500)
        self._evidence_sink = evidence_sink  # A5-02: Dependency Inversion — injected sink, not direct EvidenceLog import
        self._urls_fetched_count: int = 0
        self._stop_reason: str | None = None
        # E4 FIX: In-flight response body memory tracking for M1 8GB.
        # Limits concurrent fetches based on content-size budget.
        # permits = _INFLIGHT_BYTES_BUDGET // _INFLIGHT_BYTES_PERMIT (5 max concurrent with 10MB permits)
        self._inflight_sem: asyncio.Semaphore = asyncio.Semaphore(_INFLIGHT_BYTES_BUDGET // _INFLIGHT_BYTES_PERMIT)
        self._inflight_bytes: int = 0  # Track current in-flight bytes
        self._inflight_lock = asyncio.Lock()
        # M1-04: TTLCache(maxsize=2048, ttl=300) — bounded DNS cache, auto-evicts after 5 min
        self._host_ips_cache: TTLCache[str, list[str]] = TTLCache(maxsize=2048, ttl=300)
        # C3-02: Single-flight inflight map — prevents duplicate DNS resolutions within same batch
        self._host_ips_inflight: dict[str, asyncio.Future[list[str] | None]] = {}
        self._cooldown_seconds = 60
        self._base_retry_delay = 1.0
        self._max_retries = 3
        self._max_backoff_delay = 30.0
        self._orchestrator: Any | None = None
        self._ctx: dict[str, Any] = {}
        self._hints_extractor = _hints_extractor_cls() if _hints_extractor_cls else None
        self._zstd = ZstdCompressor()
        self._lightpanda_pool = _lp_manager_cls() if _lp_manager_cls else None
        self._lightpanda_pool_started = False
        self._lightpanda_lock = asyncio.Lock()
        self._geo_proxies = self._load_geo_proxies()
        self._current_geo_context = None
        self._session_lmdb_env: Any = None
        self._session_manager: Any = None
        self._session_checkpoint_task: asyncio.Task | None = None
        self._running: bool = False
        _paywall_cls = CAPS.require(PAYWALL_BYPASS)
        _darknet_cls = CAPS.require(DARKNET_CONNECTOR)
        self._paywall_bypass = _paywall_cls() if _paywall_cls else None
        self._darknet_connector = _darknet_cls() if _darknet_cls else None
        self._privacy_allocator: PrivacyBudgetAllocator | None = None
        self._privacy_lock = asyncio.Lock()
        self._robots_parser: Any = None  # RobotsParser async context manager; initialized in _do_initialize
        self._effective_ua: str | None = None  # F-05: active UA for robots.txt matching; set by _do_start
        self._tor_transport: Any = None
        self._tor_transport_enabled: bool = False
        if FeatureFlags.get(FeatureFlag.TOR):
            try:
                from ..transport.tor_transport import TorTransport
                self._tor_transport = TorTransport()
                self._tor_transport_enabled = self._tor_transport.available
                if self._tor_transport_enabled:
                    logger.info('TorTransport enabled via HLEDAC_ENABLE_TOR=1')
                    logger.info('  Circuit rotation after %s requests', self._tor_transport._max_circuit_requests)
            except Exception as e:  # noqa: BLE001 — best-effort; transport init failure; Tor disabled gracefully
                logger.warning('TorTransport init failed: %s', e)
                self._tor_transport_enabled = False
        self._gopher_transport: Any = None
        self._gopher_transport_enabled: bool = False
        if FeatureFlags.get(FeatureFlag.GOPHER):
            try:
                from ..transport.gopher_transport import GopherTransport
                self._gopher_transport = GopherTransport()
                self._gopher_transport_enabled = True
                logger.info('GopherTransport enabled via HLEDAC_ENABLE_GOPHER=1')
            except Exception as e:  # noqa: BLE001 — best-effort; transport init failure; Gopher disabled gracefully
                logger.warning('GopherTransport init failed: %s', e)
                self._gopher_transport_enabled = False
        self._http_cache_transport: Any = None
        self._http_cache_enabled: bool = FeatureFlags.get(FeatureFlag.HTTP_CACHE)
        self._captcha_detector: Any | None = None
        self._captcha_detections: int = 0
        if FeatureFlags.get(FeatureFlag.CAPTCHA_DETECTION):
            try:
                from ..security.captcha_detector import CaptchaDetector
                self._captcha_detector = CaptchaDetector()
                logger.info('CaptchaDetector enabled via HLEDAC_ENABLE_CAPTCHA_DETECTION=1')
            except Exception as e:  # noqa: BLE001 — best-effort; transport init failure; CaptchaDetector disabled gracefully
                logger.warning('CaptchaDetector init failed: %s', e)
                self._captcha_detector = None
        # F-07: Cloudflare / DataDome clearance cookie jar
        self._clearance_jar: Any | None = None
        if FeatureFlags.get(FeatureFlag.ENABLE_CAPTCHA):
            try:
                from ..security.clearance_cookie_jar import get_clearance_jar
                self._clearance_jar = get_clearance_jar()
                logger.info('ClearanceCookieJar enabled via HLEDAC_ENABLE_CAPTCHA=1')
            except Exception as e:  # noqa: BLE001 — best-effort; cookie jar init failure; clearance disabled gracefully
                logger.warning('ClearanceCookieJar init failed: %s', e)
                self._clearance_jar = None
        self._dedup_lock = asyncio.Lock()
        self._concurrency = TokenBucketController(rate=5, capacity=10)
        # BLITZ-13: Resolve blitz mode. ON by default; opt out via
        # HLEDAC_BLITZ_FETCH=0 env var OR blitz_mode=False parameter.
        _blitz = blitz_mode and _ENV_BLITZ_FETCH == '1'
        self._blitz_mode: bool = _blitz
        _aimd_initial = BLITZ_CONCURRENCY_CLEARNET if _blitz else CONCURRENCY_CLEARNET
        # BLITZ-15: In blitz mode, limit retries to 1 (2 total attempts) with
        # 1.0 s max backoff to avoid wasting time on unreachable hosts.
        if _blitz:
            self._max_retries = 1
            self._max_backoff_delay = 1.0
        # ISSUE 2.2: Unified AIMD controller — single AtomicU64 in Rust.
        # Replaces _AIMDSlotController orphan class.
        # Falls back to Python AIMDWindow if Rust extension unavailable.
        self._aimd: PyAIMDController | AIMDWindow = _try_load_aimd_controller(_aimd_initial)
        # _aimd_semaphore: asyncio.Semaphore for slot coordination (stays in Python).
        # Window state is in Rust; Python only reads window for semaphore sizing.
        self._aimd_semaphore: asyncio.Semaphore = asyncio.Semaphore(_aimd_initial)
        if _blitz:
            logger.info('[BLITZ-13] Blitz fetch mode: AIMD window initialized at %d (skip ramp-up)', _aimd_initial)
        self._per_host_limit = 4
        self._per_host_gate = BoundedPerHostGate(max_hosts=512, per_host_limit=self._per_host_limit)
        # CB-02: Per-domain rate limiter — 0.5 RPS default (1 req / 2s)
        # Configurable via HLEDAC_RATE_LIMIT_RPS env var
        _rate_limit_rps = FeatureFlags.get_float(FeatureFlag.RATE_LIMIT_RPS, 0.5)
        self._domain_rate_limiter = DomainRateLimiter(rate=_rate_limit_rps, max_hosts=512)
        self._telemetry: dict[str, Any] = {'aimd_concurrency': self._aimd.window, 'active_fetches': 0, 'total_successes': 0, 'total_failures': 0, 'circuit_breaker_blocks': 0, 'circuit_breaker_active': 0, 'uma_state': 'ok', 'decrease_factor_used': 1.0, 'backpressure_clamp_events': 0, 'io_only_skipped': 0, 'cross_sprint_skipped': 0, 'entity_confirmation_skipped': 0, 'mmap_delta_skipped': 0, 'inflight_bytes': 0, 'inflight_permits': 0}
        # CB-04: Retry budget per domain — track total retries in last 60s to prevent amplification
        # ISSUE-011 FIX: Use LazyAsyncioLock instead of threading.Lock
        # threading.Lock blocks the event loop when called from async context
        self._retry_budget: dict[str, list[float]] = {}  # domain -> list of retry timestamps
        self._retry_budget_lock: LazyAsyncioLock = LazyAsyncioLock()
        self._retry_budget_max = 20  # max retries per domain per 60s window
        self._retry_budget_window = 60.0  # sliding window in seconds
        self._cover_count: int = 0
        # UNIFIED-003: Entropy-to-Fetch feedback bridge — subscribes to EntropyFetchBridge
        # for high-uncertainty alerts and triggers micro-sprints to fetch alternative sources
        self._entropy_bridge_queue: asyncio.Queue[Any] | None = None
        self._entropy_bridge_task: asyncio.Task[None] | None = None
        self._entropy_alerts_processed: int = 0
        # F360-R: Counter for periodic entropy pruning (every N calls)
        self._entropy_prune_counter: int = 0
        # UNIFIED-003: Micro-sprint queue — bounded queue for re-fetch requests
        self._micro_sprint_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=32)
        self._micro_sprint_worker_task: asyncio.Task[None] | None = None
        # [META]-015: Cache for original findings before micro-sprint re-fetch.
        # Maps entity_id -> list of original findings (dict with source, content, confidence).
        # TTL 5 min — bounded at 256 entries (M1 8GB safe).
        self._micro_sprint_original_findings: dict[str, tuple[list[dict[str, Any]], float]] = {}
        # SILICON-07: SwarmDAG — initialized lazily in _do_initialize()
        self._swarm_dag: Any = None
        self._swarm_dag_rebalance_task: asyncio.Task[None] | None = None
        
        # BREAKTHROUGH #2: Speculative prefetch via real-time link prediction
        # Initialized lazily in _do_initialize() to allow DuckDB path to be set
        self._speculative_prefetcher: SpeculativePrefetcher | None = None
        
        self.init_session_manager()

    @property
    def _aimd_concurrency(self) -> float:
        """Return current AIMD concurrency window.

        UNIFIED-004: This property provides backward compatibility for code
        that references _aimd_concurrency. It delegates to self._aimd.window
        which is the canonical source of truth for AIMD concurrency.
        """
        return self._aimd.window

    async def blitz_boost(self, target: float | None = None) -> float:
        """
        BLITZ-13: Boost AIMD concurrency to maximum immediately.

        Skips the additive-increase ramp-up phase by setting the window
        directly to the target (default: BLITZ_CONCURRENCY_CLEARNET).
        Synchronizes both the Rust/Python AIMD window and the asyncio
        semaphore. Multiplicative decrease on failures still applies.

        Returns the new window value.
        """
        _target = target if target is not None else BLITZ_CONCURRENCY_CLEARNET
        if isinstance(self._aimd, PyAIMDController):
            # Rust path: use blitz_boost which also resets success counter
            new_window = self._aimd.blitz_boost(_target)
        else:
            new_window = await self._aimd.blitz_boost(_target)
        # Sync semaphore to new window
        # P4-3 FIX: Only increase permits when window grows. When window shrinks,
        # do nothing - releasing permits makes backpressure worse by allowing MORE
        # concurrent operations. The semaphore naturally limits new acquires.
        _diff = int(new_window) - self._aimd_semaphore._value
        if _diff > 0:
            for _ in range(_diff):
                self._aimd_semaphore.release()
        # P4-3 FIX: Removed elif _diff < 0 branch - don't release permits when shrinking
        self._telemetry['aimd_concurrency'] = new_window
        self._blitz_mode = True
        logger.info('[BLITZ-13] blitz_boost: window → %d (target=%d)', int(new_window), int(_target))
        return new_window

    @property
    def blitz_mode(self) -> bool:
        """BLITZ-13: Whether blitz (aggressive) fetch mode is active."""
        return self._blitz_mode

    # ── [NEXUS]-018-02: IGD telemetry bridge ─────────────────────────────────

    def report_iocs(self, branch_id: str, ioc_values: list[float]) -> None:
        """[NEXUS]-018-02: Report IOC quality scores to the IGD pruning policy.

        This method is the canonical bridge between the fetch layer (which
        discovers IOCs) and the meta-reasoning coordinator (which needs IOC
        rate data to drive IGD-based branch pruning).

        The caller passes the estimated quality/value of each IOC so that
        the IGD policy can filter low-confidence reports (threshold:
        ``_IGD_REPORT_MIN_IOC_VALUE``, default 0.5).

        Usage::

            # In a fetch worker that delivers IOCs for a ToT branch:
            self._orchestrator._meta_reasoning_coordinator._igd_policy.report_iocs(
                branch_id=branch.node_id,
                ioc_values=[ioc.estimated_value for ioc in findings],
            )

        Note:
            This is a fire-and-forget telemetry method. Errors are logged
            and swallowed so that fetch work is never blocked by IGD policy.
        """
        try:
            if hasattr(self, '_orchestrator') and self._orchestrator is not None:
                igd = getattr(self._orchestrator, '_igd_policy', None)
                if igd is not None and callable(igd.report_iocs):
                    igd.report_iocs(branch_id, ioc_values)
        except Exception as e:
            logger.debug('[NEXUS]-018-02 report_iocs failed: %s', e)

    # ── [ULTIMATE]-002: Cognitive Saturation Detection ────────────────────────

    def report_entity_discovery(self, entity_value: str, ioc_type: str = "") -> None:
        """[ULTIMATE]-002: Report a new entity discovery for cognitive saturation tracking.

        Call this method whenever a non-duplicate entity enters the write path
        (e.g., evidence creation, DuckDB insert). The entity is tracked in the
        sprint-level CognitiveSaturationDetector to detect when discovery stops.

        BREAKTHROUGH #2: Also reports to SpeculativePrefetcher for streaming
        link prediction. When new IOCs are discovered, they are added to the
        link predictor's adjacency graph for real-time prediction.

        This is a fire-and-forget telemetry method. Errors are logged and
        swallowed so that fetch work is never blocked by saturation detection.

        Args:
            entity_value: The entity value (e.g., domain, IP, URL, hash).
            ioc_type: Optional IOC type string for telemetry (e.g., "domain", "ipv4").
        """
        try:
            # [ULTIMATE]-002: Cognitive saturation tracking
            detector = _COGNITIVE_SATURATION_DETECTOR
            if detector is not None and hasattr(detector, 'report_entity_discovery'):
                detector.report_entity_discovery(entity_value, ioc_type)
        except Exception as e:
            logger.debug('[ULTIMATE]-002 report_entity_discovery failed: %s', e)
        
        # BREAKTHROUGH #2: Report to SpeculativePrefetcher for streaming link prediction
        # This enables real-time prediction when new IOCs are discovered
        try:
            if self._speculative_prefetcher is not None and self._speculative_prefetcher.is_available:
                # Generate node_id from entity value (consistent with Kuzu graph)
                import xxhash
                node_id = xxhash.xxh64(entity_value.lower()).intdigest()
                
                # Get existing neighbors from adjacency (or empty list)
                neighbors = self._speculative_prefetcher._adjacency.get(node_id, [])
                
                # Report with IOC value for real URL generation
                self._speculative_prefetcher.report_ioc_discovery(
                    node_id=node_id,
                    neighbors=neighbors,
                    ioc_value=entity_value if ioc_type in ('domain', 'url', 'hostname') else None,
                )
        except Exception as e:
            logger.debug('[BREAKTHROUGH-2] SpeculativePrefetcher report failed: %s', e)

    def _check_circuit(self, domain: str) -> tuple[bool, str, float]:
        """
        Check canonical circuit breaker for a domain.
        Delegates directly to transport/circuit_breaker.py.
        """
        try:
            from hledac.universal.transport import circuit_breaker as cb
            decision = cb.domain_breaker_check(domain)
            return (decision.allowed, decision.reason, decision.retry_after_s)
        except (ImportError, AttributeError, OSError) as e:  # noqa: BLE001 — best-effort; circuit_breaker unavailable; fail-open is safe
            return (True, f'cb_check_error:{e}', 0.0)

    def _record_success(self, domain: str) -> None:
        """Record fetch success to canonical circuit breaker."""
        try:
            from hledac.universal.transport import circuit_breaker as cb
            cb.domain_breaker_record_success(domain)
        except (ImportError, AttributeError):  # noqa: BLE001 — best-effort; circuit_breaker telemetry; non-critical
            pass

    def _record_failure(self, domain: str, is_timeout: bool=False, failure_kind: str='') -> None:
        """Record fetch failure to canonical circuit breaker."""
        try:
            from hledac.universal.transport import circuit_breaker as cb
            sprint_remaining = None
            if self._sprint_remaining_provider is not None:
                with contextlib.suppress(Exception):
                    sprint_remaining = self._sprint_remaining_provider()
            cb.domain_breaker_record_failure(domain, is_timeout=is_timeout, failure_kind=failure_kind or 'fetch_error', sprint_remaining_s=sprint_remaining)
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — best-effort; circuit_breaker telemetry; non-critical
            pass

    def _check_transport_circuit(self, transport: str) -> tuple[bool, str, float]:
        """CB-03: Check transport-level circuit breaker for Tor/I2P.

        When transport circuit is OPEN, ALL darknet fetches using that transport
        are skipped regardless of domain-level circuit breaker state.
        """
        if transport not in ("tor", "i2p"):
            return (True, f'unknown_transport:{transport}', 0.0)
        try:
            from hledac.universal.transport import circuit_breaker as cb
            breaker = cb.get_transport_breaker(transport)
            if breaker is None:
                return (True, f'transport_breaker_not_found:{transport}', 0.0)
            decision = breaker.check_circuit()
            return (decision.allowed, decision.reason, decision.retry_after_s)
        except (ImportError, AttributeError, OSError) as e:  # noqa: BLE001 — best-effort; transport circuit breaker unavailable; fail-open is safe
            return (True, f'transport_cb_error:{e}', 0.0)

    def _record_transport_failure(self, transport: str, is_timeout: bool = False) -> None:
        """CB-03: Record transport-level failure (Tor circuit exhausted, I2P router overload)."""
        if transport not in ("tor", "i2p"):
            return
        try:
            from hledac.universal.transport import circuit_breaker as cb
            breaker = cb.get_transport_breaker(transport)
            if breaker is not None:
                breaker.record_failure(is_timeout=is_timeout)
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — best-effort; transport circuit breaker telemetry; non-critical
            pass

    def _record_transport_success(self, transport: str) -> None:
        """CB-03: Record transport-level success."""
        if transport not in ("tor", "i2p"):
            return
        try:
            from hledac.universal.transport import circuit_breaker as cb
            breaker = cb.get_transport_breaker(transport)
            if breaker is not None:
                breaker.record_success()
        except (ImportError, AttributeError, OSError):  # noqa: BLE001 — best-effort; transport circuit breaker telemetry; non-critical
            pass

    async def _check_retry_budget(self, domain: str) -> tuple[bool, str]:
        """CB-04: Check if domain has exceeded retry budget.

        ISSUE-011 FIX: async method to avoid blocking event loop.
        Uses asyncio.Lock instead of threading.Lock for async-safe operation.

        Returns (allowed, reason) where allowed=False means skip retries for this domain.
        Uses sliding window of 60s to count retries.
        """
        if not domain:
            return (True, "empty_domain")
        now = time.monotonic()
        async with self._retry_budget_lock.get():
            # Clean expired entries
            if domain in self._retry_budget:
                self._retry_budget[domain] = [
                    ts for ts in self._retry_budget[domain]
                    if now - ts < self._retry_budget_window
                ]
                if len(self._retry_budget[domain]) >= self._retry_budget_max:
                    return (False, f"retry_budget_exceeded:{len(self._retry_budget[domain])}/{self._retry_budget_max}")
            return (True, "retry_budget_ok")

    async def _record_retry(self, domain: str) -> None:
        """CB-04: Record a retry attempt for domain.

        ISSUE-011 FIX: async method to avoid blocking event loop.
        Uses asyncio.Lock instead of threading.Lock for async-safe operation.
        """
        if not domain:
            return
        now = time.monotonic()
        async with self._retry_budget_lock.get():
            if domain not in self._retry_budget:
                self._retry_budget[domain] = []
            self._retry_budget[domain].append(now)

    def get_captcha_stats(self) -> dict[str, Any]:
        """Sprint P3: Return CAPTCHA detection stats for RL telemetry."""
        return {'captcha_detections_total': self._captcha_detections, 'captcha_detector_enabled': self._captcha_detector is not None}

    def get_circuit_stats(self) -> dict[str, Any]:
        """
        Return canonical circuit breaker stats.
        Delegates directly to transport/circuit_breaker.py.
        """
        try:
            from hledac.universal.transport import circuit_breaker as cb
            states = cb.get_all_breaker_states()
            return {
                'circuit_breaker_states': states,
                'open_count': sum(1 for s in states.values() if s == 'OPEN'),
            }
        except (ImportError, AttributeError, OSError) as e:  # noqa: BLE001 — best-effort; circuit_breaker stats unavailable; telemetry fallback
            return {'error': str(e)}

    def get_stats(self) -> dict[str, Any]:
        """
        Sprint P3: Unified stats API aggregating all telemetry from
        PyAIMDController (Rust) / AIMDWindow (Python fallback), BoundedPerHostGate,
        circuit breaker, and CAPTCHA detector.
        """
        aimd_window_stats = {}
        per_host_gate_stats = {}
        circuit_stats = {}
        captcha_stats = {}

        # ISSUE 2.2: Unified AIMD stats from PyAIMDController (Rust) or AIMDWindow (Python fallback)
        try:
            if hasattr(self, '_aimd') and self._aimd is not None:
                if hasattr(self._aimd, 'stats'):
                    # Rust PyAIMDController
                    aimd_stats = self._aimd.stats()
                    aimd_window_stats = {
                        'window': self._aimd.window,
                        'active': self._aimd.active,
                        **aimd_stats,
                    }
                else:
                    # Python AIMDWindow fallback
                    aimd_window_stats = {
                        'window': self._aimd.window,
                        'successes': self._aimd.successes,
                        'failures': self._aimd.failures,
                        **self._aimd.stats,
                    }
                # Remove old duplicate keys
                aimd_window_stats.pop('window_changes', None)
        except (KeyError, TypeError, AttributeError):  # noqa: BLE001 — best-effort; aimd stats unavailable; telemetry fallback
            aimd_window_stats = {'error': 'unavailable'}

        # BoundedPerHostGate
        try:
            if hasattr(self, '_per_host_gate') and self._per_host_gate is not None:
                per_host_gate_stats = {
                    'max_hosts': self._per_host_gate._max_hosts,
                    'active_hosts': len(self._per_host_gate._gates),
                    **self._per_host_gate._stats,
                }
        except (KeyError, TypeError, AttributeError):  # noqa: BLE001 — best-effort; per_host_gate stats unavailable; telemetry fallback
            per_host_gate_stats = {'error': 'unavailable'}

        # Circuit breaker
        try:
            circuit_stats = self.get_circuit_stats()
        except (KeyError, TypeError, AttributeError, OSError) as e:  # noqa: BLE001 — best-effort; circuit_breaker stats unavailable; telemetry fallback
            circuit_stats = {'error': str(e)}

        # CAPTCHA
        try:
            captcha_stats = self.get_captcha_stats()
        except (KeyError, TypeError, AttributeError) as e:  # noqa: BLE001 — best-effort; captcha_stats unavailable; telemetry fallback
            captcha_stats = {'error': str(e)}

        return {
            'aimd_window': aimd_window_stats,
            'per_host_gate': per_host_gate_stats,
            'circuit_breaker': circuit_stats,
            'captcha': captcha_stats,
        }

    def init_session_manager(self, lmdb_path: str | None=None) -> None:
        """Initialize session manager with LMDB persistence (idempotent)."""
        if not SESSION_AVAILABLE:
            return
        if self._session_manager is not None and self._session_lmdb_env is not None:
            return
        if lmdb_path is None:
            from hledac.universal.paths import LMDB_ROOT
            lmdb_path = str(LMDB_ROOT / 'session.lmdb')
        Path(lmdb_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard
            self._session_lmdb_env = open_lmdb_with_guard(lmdb_path, map_size=10 * 1024 * 1024, readahead=False, critical=True)
            if self._session_lmdb_env is not None and _session_mgr_cls is not None:
                self._session_manager = _session_mgr_cls(self._session_lmdb_env)
                self._start_checkpoint_loop()
        except Exception as e:  # noqa: BLE001 — best-effort; LMDB session persistence disabled; non-critical fallback
            logger.warning('[FETCH] LMDB session init failed: %s — session persistence disabled', e)
            self._session_manager = None

    def _load_geo_proxies(self) -> dict[str, str]:
        """Load proxy servers for different regions from configuration."""
        from hledac.universal.paths import DB_ROOT
        proxy_file = DB_ROOT / 'config' / 'proxies.json'
        if proxy_file.exists():
            try:
                with open(proxy_file, 'rb') as f:
                    return orjson.loads(f.read())
            except (OSError, orjson.JSONDecodeError):  # noqa: BLE001 — best-effort; proxy config load failure; returns empty dict
                pass
        return {}
    _PRIVATE_NETS = [ipaddress.ip_network(n) for n in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '169.254.0.0/16', '100.64.0.0/10']]

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
            return not ip.is_loopback
        except (ValueError, TypeError):  # noqa: BLE001 — best-effort; ip_address parse failure; returns False (private check)
            return False

    async def _validate_fetch_target(self, url: str) -> tuple[bool, dict[str, Any]]:
        """
        F360-R: Validate fetch target using phase-based approach.

        Phases:
        1. Hostname extraction and literal IP check
        2. Cache lookup (C3-02 single-flight)
        3. DNS resolution with rate limiting
        4. Private IP validation
        """
        try:
            hostname = _fast_url_host(url)
            if not hostname:
                return (False, {'blocked_reason': 'no_hostname'})

            # Phase 1: Check if hostname is a literal IP
            literal_result = await self._check_literal_ip(hostname)
            if literal_result is not None:
                return literal_result

            # Phase 2: Cache lookup with single-flight (C3-02)
            cache_result = await self._check_host_cache(hostname)
            if cache_result is not None:
                return cache_result

            # Phase 3: DNS resolution
            cache_key = hostname.lower()
            ips = await self._resolve_dns_with_gate(cache_key, hostname)
            if ips is None:
                return (False, {'resolved_ips': [], 'blocked_reason': 'dns_resolution_failed'})

            # Phase 4: Validate IPs are public
            return await self._validate_ips_public(ips)
        except (TimeoutError, httpx.HTTPError, httpx.TimeoutException, OSError) as e:  # noqa: BLE001
            return (False, {'blocked_reason': f'validation_error: {e}'})

    async def _check_literal_ip(self, hostname: str) -> tuple[bool, dict[str, Any]] | None:
        """F360-R: Phase 1 - Check if hostname is a literal IP address."""
        try:
            ip = ipaddress.ip_address(hostname)
            if not self._is_ip_public(str(ip)):
                return (False, {'resolved_ips': [str(ip)], 'blocked_reason': 'private_ip_literal'})
            return (True, {'resolved_ips': [str(ip)]})
        except ValueError:  # noqa: BLE001
            return None

    async def _check_host_cache(self, hostname: str) -> tuple[bool, dict[str, Any]] | None:
        """F360-R: Phase 2 - Check cache with C3-02 single-flight pattern."""
        cache_key = hostname.lower()
        cached_ips = self._host_ips_cache.get(cache_key)

        # Cache hit
        if cached_ips is not None:
            if not cached_ips:
                return (False, {'resolved_ips': [], 'blocked_reason': 'dns_resolution_failed'})
            for ip_str in cached_ips:
                if not self._is_ip_public(ip_str):
                    return (False, {'resolved_ips': list(cached_ips), 'blocked_reason': 'private_ip_resolved', 'blocked_ip': ip_str})
            return (True, {'resolved_ips': list(cached_ips)})

        # C3-02: Single-flight - wait on inflight resolution
        if cache_key in self._host_ips_inflight:
            ips = await self._host_ips_inflight[cache_key]
            if ips is None or not ips:
                return (False, {'resolved_ips': [], 'blocked_reason': 'dns_resolution_failed'})
            for ip_str in ips:
                if not self._is_ip_public(ip_str):
                    return (False, {'resolved_ips': ips, 'blocked_reason': 'private_ip_resolved', 'blocked_ip': ip_str})
            return (True, {'resolved_ips': ips})

        return None

    async def _resolve_dns_with_gate(self, cache_key: str, hostname: str) -> list[str] | None:
        """F360-R: Phase 3 - DNS resolution with per-host rate limiting.
        
        P4-2b FIX: Added proper cancellation handling to prevent orphan futures.
        If the coroutine is cancelled (e.g., timeout), the future is properly
        cancelled/removed from _host_ips_inflight to prevent memory leaks.
        """
        # Reserve slot for new resolution (single-flight)
        fut: asyncio.Future[list[str] | None] = asyncio.get_event_loop().create_future()
        self._host_ips_inflight[cache_key] = fut

        try:
            sem, _op_id = await self._per_host_gate.acquire(hostname)
            try:
                raw_results = await async_getaddrinfo(hostname, 0, proto=socket.IPPROTO_TCP)
            finally:
                self._per_host_gate.release(sem)

            ips = sorted({str(r[4][0]) for r in raw_results})

            async with self._dedup_lock:
                if ips:
                    self._host_ips_cache[cache_key] = ips
                fut.set_result(ips if ips else None)
                self._host_ips_inflight.pop(cache_key, None)

            return ips if ips else None
        except asyncio.CancelledError:
            # P4-2b FIX: Clean up orphan future on cancellation
            fut.cancel()
            self._host_ips_inflight.pop(cache_key, None)
            raise

    async def _validate_ips_public(self, ips: list[str]) -> tuple[bool, dict[str, Any]]:
        """F360-R: Phase 4 - Validate all resolved IPs are public."""
        for ip_str in ips:
            if not self._is_ip_public(ip_str):
                return (False, {'resolved_ips': ips, 'blocked_reason': 'private_ip_resolved', 'blocked_ip': ip_str})
        return (True, {'resolved_ips': ips})

    def _is_js_heavy(self, url: str, html_preview: str='') -> bool:
        """Detect JS-heavy pages by URL and HTML preview."""
        js_indicators = ['react', 'vue', 'angular', 'next', 'nuxt', 'svelte']
        if any(ind in url.lower() for ind in js_indicators):
            return True
        if html_preview:
            if '<script' in html_preview.lower() and len(html_preview) < 5000:
                return True
            if 'data-reactroot' in html_preview or 'ng-version' in html_preview:
                return True
        return False

    def _resolve_backpressure_state(self) -> tuple[float | None, str]:
        """Resolve backpressure clearing and UMA state from various sources."""
        bp_clearing: float | None = None
        bp_uma_state = 'ok'

        if self._batch_cp_result is _CP_RETURNED_NONE:
            pass
        elif self._batch_cp_result is not _CP_NOT_CALLED:
            bp_clearing, _, bp_uma_state, _ = self._batch_cp_result
        elif self._concurrency_provider is not None:
            try:
                bp_result = self._concurrency_provider()
                if bp_result is not None:
                    bp_clearing, _, bp_uma_state, _ = bp_result
            except (TypeError, ValueError, KeyError):  # noqa: BLE001
                pass
        return bp_clearing, bp_uma_state

    async def _apply_governor_backpressure(self, bp_clearing: float | None, bp_uma_state: str) -> tuple[float | None, str]:
        """Apply governor-based backpressure if available."""
        try:
            from hledac.universal._core.protocols import get_governor
            gov = get_governor()
            if gov is not None:
                gov_decision = await gov.evaluate()
                if bp_clearing is None or gov_decision.fetch_limit < bp_clearing:
                    bp_clearing = float(gov_decision.fetch_limit)
                if gov_decision.io_only:
                    bp_uma_state = gov_decision.uma_state
        except Exception:  # noqa: BLE001
            pass
        return bp_clearing, bp_uma_state

    async def _acquire_rust_slot(self, bp_clearing: float | None) -> float:
        """Acquire slot using Rust PyAIMDController."""
        current_window, _ = self._aimd.acquire()
        if bp_clearing is not None and bp_clearing < current_window:
            self._aimd.set_window(bp_clearing)
            current_window = bp_clearing
            self._telemetry['backpressure_clamp_events'] += 1
        return current_window

    def _sync_semaphore_to_window(self, current_window: float) -> None:
        """Sync semaphore permits to match current window.
        
        P4-3 FIX: Only increase permits when window grows. When window shrinks,
        do nothing - don't release permits. Releasing when shrinking makes
        backpressure worse by allowing MORE concurrent operations.
        
        The semaphore naturally enforces the reduced window as running tasks
        complete and release their permits. New acquires wait when window is
        smaller than the semaphore's current permits.
        """
        if current_window == self._aimd_semaphore._value:
            return
        diff = int(current_window) - self._aimd_semaphore._value
        if diff > 0:
            for _ in range(diff):
                self._aimd_semaphore.release()
        # P4-3 FIX: Removed elif diff < 0 branch. When window shrinks,
        # we must NOT release permits - that would make backpressure worse.
        # The semaphore value stays higher, naturally limiting new acquires
        # until running tasks complete and release normally.

    async def _acquire_python_slot(self, bp_clearing: float | None) -> float:
        """Acquire slot using Python AIMDWindow + semaphore."""
        if bp_clearing is not None and bp_clearing < self._aimd.window:
            await self._aimd.set_window(bp_clearing)
            self._telemetry['backpressure_clamp_events'] += 1
        current_window = self._aimd.window
        self._sync_semaphore_to_window(current_window)
        await self._aimd_semaphore.acquire()
        return current_window

    async def _aimd_acquire(self) -> tuple[float, None]:
        """Acquire AIMD slot, returns (concurrency_window, None)."""
        # Resolve backpressure state
        bp_clearing, bp_uma_state = self._resolve_backpressure_state()

        # Apply governor backpressure
        bp_clearing, bp_uma_state = await self._apply_governor_backpressure(bp_clearing, bp_uma_state)
        self._telemetry['uma_state'] = bp_uma_state

        # Acquire slot (Rust: lock-free; Python fallback: semaphore)
        if isinstance(self._aimd, PyAIMDController):
            current_window = await self._acquire_rust_slot(bp_clearing)
        else:
            current_window = await self._acquire_python_slot(bp_clearing)

        # E4 FIX: Acquire memory slot for in-flight response body
        await self._inflight_sem.acquire()
        self._telemetry['active_fetches'] += 1
        # E4 FIX: Update inflight telemetry
        self._telemetry['inflight_permits'] = self._inflight_sem._value
        return (current_window, None)

    # E4 FIX: Memory tracking helper methods
    async def _release_inflight_memory(self, content_bytes: int) -> None:
        """Release inflight memory slot after fetch completes.
        
        E4 FIX: Releases the semaphore permit acquired in _aimd_acquire.
        Also tracks bytes for telemetry.
        """
        self._inflight_sem.release()
        async with self._inflight_lock:
            self._inflight_bytes = max(0, self._inflight_bytes - content_bytes)

    def _get_effective_max_bytes(self, url: str, content_type: str | None = None) -> int:
        """Get effective max_bytes based on content type and URL.
        
        E4 FIX: Reduces max_bytes for non-article content to save memory.
        Articles get 10MB, everything else gets 2MB.
        """
        default_max = 2 * 1024 * 1024  # 2 MB default
        
        # Articles and feeds get higher limit
        if content_type and ('html' in content_type or 'xml' in content_type):
            # Check if it's likely an article
            _article_indicators = ('/article', '/post', '/news', '/blog', '/story', 'rss', 'feed', 'atom', 'sitemap')
            if any(ind in url.lower() for ind in _article_indicators):
                return 10 * 1024 * 1024  # 10 MB for articles
            return default_max
        
        # Non-HTML content gets smaller limit
        if content_type:
            if 'json' in content_type:
                return 512 * 1024  # 512 KB for JSON
            if 'text' in content_type:
                return 1024 * 1024  # 1 MB for plain text
            if content_type.startswith('image/') or content_type.startswith('video/') or content_type.startswith('audio/'):
                return 5 * 1024 * 1024  # 5 MB for media
            if 'pdf' in content_type or 'zip' in content_type or 'archive' in content_type:
                return 5 * 1024 * 1024  # 5 MB for archives
        
        return default_max

    async def _aimd_release_success(self) -> float:
        """
        Release AIMD slot after success.
        Returns new concurrency window.

        ISSUE 2.2: Uses unified PyAIMDController (Rust) for lock-free success recording.
        """
        self._telemetry['active_fetches'] -= 1
        uma_state = self._telemetry.get('uma_state', 'ok')

        if isinstance(self._aimd, PyAIMDController):
            # Rust path: lock-free success recording
            new_window, _ = self._aimd.record_success()
        else:
            # Python fallback
            multiplier = 2.0 if uma_state == 'ok' else 1.0
            new_window, _ = await self._aimd.on_success(multiplier=multiplier)

        self._telemetry['total_successes'] += 1
        self._telemetry['aimd_concurrency'] = new_window
        # E4 FIX: Release inflight memory slot (10MB permit released)
        self._inflight_sem.release()
        return new_window

    async def _aimd_release_failure(self) -> float:
        """
        Release AIMD slot after failure (timeout/throttling/pressure).
        Returns new concurrency window.

        ISSUE 2.2: Uses unified PyAIMDController (Rust) for lock-free failure recording.
        """
        self._telemetry['active_fetches'] -= 1
        uma_state = self._telemetry.get('uma_state', 'ok')
        decrease_factor = AIMD_DECREASE_BY_STATE.get(uma_state, 1.0)

        if isinstance(self._aimd, PyAIMDController):
            # Rust path: lock-free failure recording
            new_window, _ = self._aimd.record_failure(uma_state)
        else:
            # Python fallback
            new_window, new_failures = await self._aimd.on_failure(uma_state=uma_state)
            if new_window != self._aimd_semaphore._value:
                logger.warning(f'[AIMD] failure #{new_failures} uma_state={uma_state} factor={decrease_factor} → window→{new_window:.1f}')

        self._telemetry['total_failures'] += 1
        self._telemetry['aimd_concurrency'] = new_window
        self._telemetry['decrease_factor_used'] = decrease_factor
        # E4 FIX: Release inflight memory slot (10MB permit released)
        self._inflight_sem.release()
        return new_window

    def _get_privacy_semaphore(self, url: str) -> tuple[asyncio.Semaphore | None, str]:
        """
        Get the privacy semaphore for a URL's transport class.

        Returns (semaphore, lane). lane="clearnet" means no privacy lane needed.
        """
        if self._privacy_allocator is None:
            return (None, 'clearnet')
        lane = self._privacy_allocator.get_lane_for_url(url)
        if lane == 'clearnet':
            return (None, 'clearnet')
        sem = self._privacy_allocator.get_semaphore(lane)
        return (sem, lane)

    async def _privacy_acquire_for_url(self, url: str) -> tuple[str, bool]:
        """
        Acquire privacy lane slot for URL if applicable.

        Returns (lane, acquired). If lane is "clearnet" or privacy lane unavailable,
        returns ("clearnet", True) so caller falls back to AIMD path.

        Fail-soft: any error → fall back to clearnet.
        """
        if self._privacy_allocator is None:
            return ('clearnet', True)
        lane = self._privacy_allocator.get_lane_for_url(url)
        if lane == 'clearnet':
            return ('clearnet', True)
        sem = self._privacy_allocator.get_semaphore(lane)
        if sem is None:
            return ('clearnet', True)
        try:
            async with self._privacy_lock:
                await sem.acquire()
            return (lane, True)
        except asyncio.CancelledError:
            # P0-3 FIX: Cancellation is fail-CLOSED (block), not fail-open to clearnet.
            # Re-raising CancelledError ensures proper shutdown propagation.
            # Suppressing here would be a deanonymization risk.
            raise
        except TimeoutError:
            # TimeoutError is fail-open to clearnet (acceptable: slow privacy lane → fall back)
            return ('clearnet', True)

    def _privacy_release(self, lane: str) -> None:
        """Release privacy lane slot. No-op for clearnet."""
        if lane == 'clearnet' or self._privacy_allocator is None:
            return
        sem = self._privacy_allocator.get_semaphore(lane)
        if sem is not None:
            with contextlib.suppress(ValueError):
                sem.release()

    async def _fetch_with_lightpanda(self, url: str, proxy: str | None=None) -> dict[str, Any] | None:
        """Fetch URL with Lightpanda using pool (JS rendering)."""
        try:
            if not self._lightpanda_pool_started:
                async with self._lightpanda_lock:
                    if not self._lightpanda_pool_started:
                        await self._lightpanda_pool.start()
                        self._lightpanda_pool_started = True
            lp = await self._lightpanda_pool.get_instance()
            try:
                content = await lp.fetch_js(url, proxy)
                return {'url': url, 'content': content, 'js_rendered': True}
            finally:
                await self._lightpanda_pool.release(lp)
        except (TimeoutError, httpx.HTTPError, httpx.TimeoutException, OSError, ConnectionError) as e:  # noqa: BLE001 — best-effort; httpx request failure; non-critical fallback
            logger.warning('[LIGHTPANDA] Failed: %s, falling back to curl_cffi', e)
            return None

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

    async def _get_tor_session(self, domain: str) -> object | None:
        """F274: Delegate to darknet_session_provider (transport layer owns sessions)."""
        from ..transport.darknet_session_provider import get_session, mark_used
        session = await get_session('tor', domain)
        if session is not None:
            await mark_used('tor', domain)
        return session

    # OSINT-02: max_bytes cap for darknet fetches — prevents OOM from unbounded resp.read()
    _DARKNET_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB hard cap

    async def _fetch_with_tor(self, url: str, session: object | None=None, *, max_bytes: int | None=None) -> dict[str, Any] | None:
        """Fetch .onion URL using Tor connection pool.

        Args:
            url: The .onion URL to fetch.
            session: Pre-acquired Tor session (from _get_tor_session). If None, acquires one.
                     Pre-acquiring outside the retry loop saves ~2s per retry on SOCKS handshake.
            max_bytes: Optional per-call cap. Defaults to _DARKNET_MAX_BYTES (10 MB).
                       Pass lower values for targeted fetches (e.g., 1 MB for metadata-only).

        OSINT-02 FIX: Uses chunked streaming with hard 10MB cap instead of unbounded resp.read().
        """
        try:
            domain = _fast_url_host(url)
            if session is None:
                session = await self._get_tor_session(domain)
            if not session:
                return None
            async with session.get(url) as resp:
                content_chunks: list[bytes] = []
                received = 0
                cap = max_bytes if max_bytes is not None else self._DARKNET_MAX_BYTES
                async for chunk in resp.aiter_bytes():
                    remaining = cap - received
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        content_chunks.append(chunk[:remaining])
                        received = cap
                        logger.debug('[TOR] Body truncated at %d bytes for %s', cap, url)
                        break
                    content_chunks.append(chunk)
                    received += len(chunk)
                return {'status': resp.status, 'headers': dict(resp.headers), 'content': b''.join(content_chunks)}
        except TimeoutError:
            logger.debug('[TOR] Timeout for %s', url)
            await self._aimd_release_failure()
            return None
        except asyncio.CancelledError:
            # P0-3 FIX: Re-raise CancelledError for proper cancellation propagation.
            raise
        except (httpx.HTTPError, OSError) as e:  # noqa: BLE001 — best-effort; httpx response body read; non-critical
            logger.warning('Tor fetch failed: %s', e)
            await self._aimd_release_failure()
            return None

    async def _get_i2p_session(self, domain: str) -> object | None:
        """F274: Delegate to darknet_session_provider (transport layer owns sessions)."""
        from ..transport.darknet_session_provider import get_session, mark_used
        session = await get_session('i2p', domain)
        if session is not None:
            await mark_used('i2p', domain)
        return session

    async def _fetch_with_i2p(self, url: str, session: object | None=None, *, max_bytes: int | None=None) -> dict[str, Any] | None:
        """Fetch .i2p URL using I2P connection pool.

        Args:
            url: The .i2p URL to fetch.
            session: Pre-acquired I2P session (from _get_i2p_session). If None, acquires one.
                     Pre-acquiring outside the retry loop saves ~2s per retry on SOCKS handshake.
            max_bytes: Optional per-call cap. Defaults to _DARKNET_MAX_BYTES (10 MB).
                       Pass lower values for targeted fetches (e.g., 1 MB for metadata-only).

        OSINT-02 FIX: Uses chunked streaming with hard 10MB cap instead of unbounded resp.read().
        """
        try:
            domain = _fast_url_host(url)
            if session is None:
                session = await self._get_i2p_session(domain)
            if not session:
                return None
            async with session.get(url) as resp:
                content_chunks: list[bytes] = []
                received = 0
                cap = max_bytes if max_bytes is not None else self._DARKNET_MAX_BYTES
                async for chunk in resp.aiter_bytes():
                    remaining = cap - received
                    if remaining <= 0:
                        break
                    if len(chunk) > remaining:
                        content_chunks.append(chunk[:remaining])
                        received = cap
                        logger.debug('[I2P] Body truncated at %d bytes for %s', cap, url)
                        break
                    content_chunks.append(chunk)
                    received += len(chunk)
                return {'url': url, 'content': b''.join(content_chunks), 'status': resp.status, 'headers': dict(resp.headers), 'content_type': resp.content_type}
        except TimeoutError:
            logger.debug('[I2P] Timeout for %s', url)
            await self._aimd_release_failure()
            return None
        except asyncio.CancelledError:
            # P0-3 FIX: Re-raise CancelledError for proper cancellation propagation.
            raise
        except (httpx.HTTPError, OSError) as e:  # noqa: BLE001 — best-effort; httpx stream read; non-critical
            logger.warning('I2P fetch failed: %s', e)
            await self._aimd_release_failure()
            return None

    async def _fetch_with_curl(self, url: str, proxy: str | None=None, *, resolve: dict[str, str] | None=None, _extra_headers: dict[str, str] | None=None, _effective_max_bytes: int | None=None) -> dict[str, Any] | None:
        """Fetch URL via curl_cffi with HTTP/3 Alt-Svc support (F265C).

        ISSUE-0.2 FIX: Uses CAPS-based curl_cffi availability check.
        Falls back to FAIL-FAST (no silent httpx fallback without JA3).

        ISSUE-8.1 FIX: DNS Rebinding Protection via pre-bound IP addresses.
        When ``resolve`` dict is provided, curl's RESOLVE option is used to
        bind the connection to the pre-validated IP before DNS lookup,
        eliminating the TOCTOU window between validation and fetch.

        Replaced StealthWebScraper with public_fetcher's
        fetch_via_curl_cffi_cached() which has full H3 Alt-Svc LRU priming,
        conditional cache (ETag/Last-Modified), and prewarm pool support.
        """
        # ISSUE-0.2: CAPS-based availability check — never fall back to httpx without JA3
        try:
            from hledac.universal.fetching.curl_cffi_fetch import (
                fetch_via_curl_cffi_with_caps_check,
                is_curl_cffi_capable,
                next_ja3_profile,
            )
            _capable, _cap_reason = is_curl_cffi_capable()
            if not _capable:
                logger.warning(
                    "[ISSUE-0.2] curl_cffi not CAPS-capable (%s) — FAIL-FAST ( refusing httpx fallback)",
                    _cap_reason,
                )
                return {'url': url, 'content': b'', 'error': f'curl_cffi_unavailable: {_cap_reason}'}
        except ImportError:
            logger.warning("[ISSUE-0.2] fetching.curl_cffi_fetch unavailable — FAIL-FAST")
            return {'url': url, 'content': b'', 'error': 'curl_cffi_fetch_import_failed'}

        try:
            from hledac.universal.fetching.public_fetcher import (
                _altsvc_extract_host,
                _altsvc_http_version_for,
                _altsvc_record_from_result,
            )
            # PERFORMANCE FIX: Only probe Alt-Svc when cache is cold.
            # Previously, probe_altsvc_speculative was called unconditionally per-request,
            # causing redundant H3 probes even when cache was warm. Now we check
            # the cache first and only probe on cache miss.
            _altsvc_host = _altsvc_extract_host(url)
            _curl_http_version = _altsvc_http_version_for(_altsvc_host)
            if _curl_http_version is None:
                # Cache miss — probe speculatively to prime the cache
                try:
                    from hledac.universal.transport.http3_lane import probe_altsvc_speculative
                    probe_altsvc_speculative(url)
                except (ImportError, AttributeError, TypeError):  # noqa: BLE001 — best-effort; http3_lane unavailable; fail-open
                    pass
                # Re-check cache after probing (may have been populated)
                _curl_http_version = _altsvc_http_version_for(_altsvc_host)
            _ja3_profile = next_ja3_profile()
            # F-07: Merge extra headers (clearance cookies) with request headers
            _req_headers = dict(_extra_headers) if _extra_headers else None
            # E4 FIX: Use effective max_bytes (2MB for non-articles, 10MB for articles)
            _max_bytes = _effective_max_bytes if _effective_max_bytes else (10 * 1024 * 1024)
            _curl_result = await fetch_via_curl_cffi_with_caps_check(url=url, headers=_req_headers, timeout_s=30.0, max_bytes=_max_bytes, profile=_ja3_profile, http_version=_curl_http_version, _pre_probe=False, resolve=resolve)
            if _curl_result is None:
                return {'url': url, 'content': b'', 'error': 'curl_cffi_caps_check_failed'}
            _altsvc_record_from_result(url, _curl_result.get('headers'))
            _curl_bytes = _curl_result.get('content', b'')
            _curl_error = _curl_result.get('error', None)
            _curl_text = _curl_bytes.decode('utf-8', errors='replace') if _curl_bytes else None
            return {'url': url, 'final_url': _curl_result.get('final_url', url), 'content': _curl_bytes, 'text': _curl_text, 'status_code': _curl_result.get('status_code', 0), 'content_type': _curl_result.get('content_type', ''), 'headers': _curl_result.get('headers', {}), 'js_rendered': False, 'success': _curl_error is None, 'error': _curl_error}
        except TimeoutError:
            logger.debug('[CURL] Timeout for %s', url)
            await self._aimd_release_failure()
            return {'url': url, 'content': b'', 'error': 'timeout'}
        except asyncio.CancelledError:
            # P0-3 FIX: Re-raise CancelledError for proper cancellation propagation.
            raise
        except OSError as e:  # noqa: BLE001 — curl_cffi doesn't raise httpx.HTTPError; only network/OS errors expected here
            logger.warning('[CURL] Failed: %s', e)
            return {'url': url, 'content': b'', 'error': str(e)}

    def _extract_content_type(self, headers: dict[str, str]) -> str:
        """Extract content-type from response headers."""
        ct = headers.get('content-type', headers.get('Content-Type', ''))
        if ';' in ct:
            return ct.split(';', 1)[0].strip()
        return ct

    async def _fetch_with_quinn(self, url: str, method: str='GET', body: bytes | None=None, headers: list[tuple[str, str]] | None=None, timeout_s: float=30.0) -> dict[str, Any] | None:
        """Fetch URL via Rust quinn HTTP/3 client (F350M-R).

        FALLBACK PATH: Called when curl_cffi fails AND server advertises HTTP/3 via Alt-Svc.
        This is the true HTTP/3 path — not an Alt-Svc upgrade, but a real QUIC connection.

        Stack: quinn (Rust QUIC) + h3 (HTTP/3) via rust_extensions.
        M1 8GB: bounded to 3 concurrent connections, immediate memory release on drop.

        Args:
            url: Target URL (https only)
            method: HTTP method (default GET)
            body: Request body bytes (optional)
            headers: Request headers as list of (key, value) tuples (optional)
            timeout_s: Request timeout in seconds (default 30.0)

        Returns:
            dict with url, content, status_code, headers, error keys, or None on failure.
        """
        # F350M-R: Runtime feature flag — can disable even if built with quic feature
        # SWARM-010: Use FeatureFlags for registry compliance
        from hledac.universal._core.feature_flags import FeatureFlags, FeatureFlag
        if not FeatureFlags.get(FeatureFlag.ENABLE_QUIC):
            logger.debug('[QUINN] Disabled via HLEDAC_ENABLE_QUIC=0')
            return None
        try:
            from hledac.universal.rust_extensions import quic as rust_quic

            # Run blocking Rust QUIC call in thread pool to avoid blocking asyncio loop
            quic_response = await asyncio.to_thread(
                rust_quic.fetch,
                url,
                method,
                body,
                headers,
                timeout_s,
            )

            if quic_response is None:
                return None

            if quic_response.error:
                logger.debug('[QUINN] Failed: %s', quic_response.error)
                return {'url': url, 'content': b'', 'error': quic_response.error}

            # Convert to fetch_coordinator result format
            return {
                'url': url,
                'content': bytes(quic_response.body) if quic_response.body else b'',
                'status_code': quic_response.status,
                'headers': dict(quic_response.headers) if quic_response.headers else {},
                'content_type': self._extract_content_type(dict(quic_response.headers) if quic_response.headers else {}),
                'final_url': url,
                'success': True,
                'error': None,
            }

        except ImportError:
            # rust_quic module not available (built without quic feature)
            logger.debug('[QUINN] Module unavailable (built without quic feature)')
            return None
        except Exception as e:  # noqa: BLE001 — best-effort; any exception from Rust bridge
            logger.debug('[QUINN] Exception: %s', e)
            return {'url': url, 'content': b'', 'error': str(e)}

    async def _fetch_with_nw_connection(self, url: str, *, timeout_ms: int | None = None) -> dict[str, Any] | None:
        """Fetch URL via Apple Network.framework (SILICON-03).

        Network.framework provides user-space TCP with hardware-accelerated
        TLS 1.3 on Apple Silicon. Bypasses BSD socket kernel transitions
        entirely — 30-40% lower latency per connection vs curl_cffi.

        PREFERRED PATH for non-stealth clearnet targets. Falls back to
        curl_cffi when unavailable or on error.

        Args:
            url: Target URL (https:// preferred, http:// supported)
            timeout_ms: Per-request timeout in milliseconds (default 10s)

        Returns:
            dict with url, content, status_code, headers, error or None
        """
        try:
            from hledac.universal.transport.nw_connection_lane import fetch_nw_connection
            return await fetch_nw_connection(url, timeout_ms=timeout_ms)
        except ImportError:
            return None
        except Exception as e:
            logger.debug('[NW] Exception: %s', e)
            return None

    def get_supported_operations(self) -> list[Any]:
        """Return supported operation types."""
        from .base import OperationType
        return [OperationType.RESEARCH]

    async def handle_request(self, operation_ref: str, decision: object) -> dict[str, Any]:
        """
        Handle a decision request (required by UniversalCoordinator base).

        For spine pattern, we use start/step/shutdown instead.
        This is a compatibility method.
        """
        result = await self.step({'decision': decision})
        return result

    async def _do_initialize(self) -> bool:
        """Initialize coordinator."""
        logger.info('FetchCoordinator initialized')
        
        # BREAKTHROUGH #2: Initialize speculative prefetch via real-time link prediction
        # Gated by HLEDAC_ENABLE_SPECULATIVE_PREFETCH (default OFF — experimental)
        _speculative_prefetch_enabled = os.environ.get(
            'HLEDAC_ENABLE_SPECULATIVE_PREFETCH', '0',
        ).lower() in ('1', 'true', 'yes', 'on')
        if _speculative_prefetch_enabled:
            try:
                self._speculative_prefetcher = SpeculativePrefetcher(
                    coordinator=self,
                    max_prefetch=100,
                )
                if self._speculative_prefetcher.is_available:
                    logger.info(
                        '[BREAKTHROUGH-2] Speculative prefetch enabled '
                        '(target: 70%+ coverage, +35% IGD)',
                    )
                else:
                    logger.debug(
                        '[BREAKTHROUGH-2] Speculative prefetch unavailable '
                        '(streaming link predictor not available)',
                    )
                    self._speculative_prefetcher = None
            except Exception as e:
                logger.debug(
                    '[BREAKTHROUGH-2] Speculative prefetch init failed '
                    '(fail-soft): %s', e,
                )
                self._speculative_prefetcher = None
        
        # UNIFIED-003: Subscribe to EntropyFetchBridge for high-uncertainty alerts.
        # Gated by HLEDAC_ENABLE_ENTROPY_FEEDBACK (default ON). Opt-out via env=0.
        _entropy_feedback_enabled = os.environ.get(
            'HLEDAC_ENABLE_ENTROPY_FEEDBACK', '1',
        ).lower() in ('1', 'true', 'yes', 'on')
        if _entropy_feedback_enabled:
            try:
                from hledac.universal.brain.uncertainty_quant import (
                    get_entropy_bridge,
                    SeverityPriorityQueue,
                )
                bridge = get_entropy_bridge()
                if bridge is not None:
                    # ISSUE-022-03 FIX: Use SeverityPriorityQueue instead of asyncio.Queue
                    # for priority-based overflow handling. High-severity alerts
                    # (contradictions, high entropy) are preserved when queue saturates.
                    self._entropy_bridge_queue = SeverityPriorityQueue(maxsize=64)
                    subscribed = await bridge.subscribe(
                        'fetch_coordinator', self._entropy_bridge_queue,
                    )
                    if subscribed:
                        logger.info(
                            '[UNIFIED-003] Subscribed to EntropyFetchBridge '
                            'for entropy alerts (severity-priority queue enabled)',
                        )
                        # Set _running flag before starting background tasks
                        self._running = True
                        # Start background task to consume alerts
                        self._entropy_bridge_task = safe_create_task(
                            self._entropy_alert_consumer_loop(),
                            name='fetch_coordinator.entropy_consumer',
                        )
                        # Start micro-sprint worker task
                        self._micro_sprint_worker_task = safe_create_task(
                            self._micro_sprint_worker_loop(),
                            name='fetch_coordinator.micro_sprint_worker',
                        )
                    else:
                        logger.warning(
                            '[UNIFIED-003] Failed to subscribe to '
                            'EntropyFetchBridge',
                        )
            except Exception as e:
                logger.debug(
                    '[UNIFIED-003] EntropyFetchBridge subscription failed '
                    '(fail-soft): %s', e,
                )
        else:
            logger.info(
                '[UNIFIED-003] Entropy feedback loop disabled '
                '(HLEDAC_ENABLE_ENTROPY_FEEDBACK=0)',
            )
        # SILICON-07: Initialize SwarmDAG for dynamic lane rebalancing.
        _swarm_dag_enabled = FeatureFlags.get(FeatureFlag.SWARM_DAG)
        if _swarm_dag_enabled:
            try:
                from ..core.rust_backend.swarm_dag import get_domain
                self._swarm_dag = get_domain()
                logger.info(
                    '[SILICON-07] SwarmDAG initialized: type=%s, running=%s',
                    type(self._swarm_dag).__name__,
                    self._swarm_dag.is_running,
                )
                # Start rebalancer loop in background (fires every 10s)
                self._swarm_dag_rebalance_task = safe_create_task(
                    self._swarm_dag_rebalance_loop(),
                    name='fetch_coordinator.swarm_dag_rebalancer',
                )
            except Exception as e:
                logger.warning('[SILICON-07] SwarmDAG init failed (fail-soft): %s', e)
                self._swarm_dag = None
        else:
            self._swarm_dag = None
            logger.info('[SILICON-07] SwarmDAG disabled via HLEDAC_ENABLE_SWARM_DAG=0')
        # F-05: RobotsParser — 15 min TTL, 1024 domain LRU cache (M1 8GB bounded)
        try:
            from ..utils.robots_parser import RobotsParser
            _parser = RobotsParser(cache_ttl=900.0, max_cache_size=1024)
            await _parser.__aenter__()
            self._robots_parser = _parser
            logger.info('RobotsParser active (TTL=900s, max_cache=1024)')
        except Exception as _exc:  # noqa: BLE001 — best-effort; RobotsParser init failure; robots enforcement disabled
            logger.warning('RobotsParser init failed: %s — robots enforcement disabled', _exc)
            self._robots_parser = None
        if self._http_cache_enabled and self._http_cache_transport is None:
            try:
                from ..transport.http_cache import build_cache_transport
                self._http_cache_transport = await build_cache_transport(None)
                if self._http_cache_transport is not None:
                    from ..network.session_runtime import set_httpx_cache_transport
                    set_httpx_cache_transport(self._http_cache_transport)
                    logger.info('FetchCoordinator HTTP cache active (opt-out via HLEDAC_HTTP_CACHE=0)')
                else:
                    logger.info('FetchCoordinator HTTP cache requested but unavailable (install: \'uv pip install ".[osint-cache]"\')')
            except Exception as exc:  # noqa: BLE001 — best-effort; aimd release failure; non-critical
                logger.warning('HTTP cache init failed: %s', exc)
                self._http_cache_transport = None
        elif not self._http_cache_enabled:
            logger.info('FetchCoordinator HTTP cache disabled via HLEDAC_HTTP_CACHE=0')
        # [DETA]-001: Inject SprintDeltaIndex into CrossSprintGate
        if self._cross_sprint_gate is not None:
            try:
                await self._cross_sprint_gate.inject_delta_index()
                logger.info('[DETA]-001 SprintDeltaIndex injected into CrossSprintGate')
            except Exception as e:
                logger.debug('[DETA]-001 SprintDeltaIndex injection failed (fail-soft): %s', e)
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
        if self._privacy_allocator is None:
            _target = int(self._aimd_concurrency)
            self._privacy_allocator = make_privacy_allocator(_target)
            logger.info(f'[F281] PrivacyBudgetAllocator: {self._privacy_allocator.get_budget_summary()}')
        if 'frontier' in ctx:
            self._frontier = deque(ctx['frontier'], maxlen=1000)
        _ev0 = len(self._frontier)
        # F-05: sync effective UA from public_fetcher's canonical pool
        try:
            from ..fetching.public_fetcher import get_random_ua
            self._effective_ua = get_random_ua()
        except Exception:  # noqa: BLE001 — best-effort; UA pool unavailable; fallback
            self._effective_ua = 'Hledac-Bot/1.0'
        # [META]-002: Configure and load DeltaSyncEngine KnownGoodCache at sprint prelude.
        # Sprint ID and DuckDB store come from orchestrator context.
        # SprintDeltaIndex injection (for CrossSprintGate) happens in _do_initialize.
        _delta_enabled = FeatureFlags.get(FeatureFlag.CROSS_SPRINT_GATE)
        if _delta_enabled and self._orchestrator is not None:
            try:
                _duckdb = getattr(self._orchestrator, '_duckdb_store', None)
                _sid = ctx.get('sprint_id', '')
                _prior = ctx.get('prior_sprint_ids', [])
                if _duckdb and _sid:
                    from hledac.universal.knowledge.sprint_delta_index import get_delta_sync_engine
                    _engine = get_delta_sync_engine()
                    _engine.configure(duckdb_store=_duckdb, sprint_id=_sid)
                    if _prior:
                        _engine.set_prior_sprint_ids(_prior)
                    _loaded = await _engine.load_cache()
                    logger.info(
                        '[META-002] DeltaSyncEngine cache loaded: %d entities '
                        '(sprint=%s, prior=%s)',
                        _loaded, _sid, _prior,
                    )
                    # [META-002: Inject DuckDB store into SprintDeltaIndex for CrossSprintGate]
                    if self._cross_sprint_gate is not None and self._cross_sprint_gate._delta_index is not None:
                        self._cross_sprint_gate._delta_index._duckdb_store = _duckdb
                    # [META]-014: Inject DuckDB store into EntityConfirmationService
                    if self._entity_confirmation_service is not None:
                        self._entity_confirmation_service._store = _duckdb
            except Exception as exc:
                logger.debug('[META-002] DeltaSyncEngine config/load failed (fail-soft): %s', exc)
        
        # [NEXTGEN-04]: Register prior sprint bundles in MmapDeltaIndex for zero-latency caching
        # Also inject into CrossSprintGate to enable tier-1 zero-latency lookups
        try:
            _prior = ctx.get('prior_sprint_ids', [])
            if _prior and self._mmap_delta_index is not None:
                from hledac.universal.paths import get_sprint_bundle_path
                _registered = 0
                for _sprint_id in _prior:
                    _bundle_path = get_sprint_bundle_path(_sprint_id)
                    if _bundle_path and _bundle_path.exists():
                        _loaded = self._mmap_delta_index.register_bundle(_bundle_path, _sprint_id)
                        _registered += _loaded
                if _registered > 0:
                    logger.info(
                        '[NEXTGEN-04] MmapDeltaIndex loaded: %d entities from %d sprints',
                        _registered, len(_prior),
                    )
                # [NEXTGEN-04] CRITICAL: Inject MmapDeltaIndex into CrossSprintGate
                # This enables tier-1 zero-latency lookups in should_skip_batch()
                if self._cross_sprint_gate is not None:
                    self._cross_sprint_gate.inject_mmap_delta_index()
                    logger.info('[NEXTGEN-04] MmapDeltaIndex injected into CrossSprintGate for tier-1 lookups')
        except Exception as exc:
            logger.debug('[NEXTGEN-04] MmapDeltaIndex bundle registration failed (fail-soft): %s', exc)
        logger.info('FetchCoordinator started with %s URLs in frontier', len(self._frontier))
        
        # BREAKTHROUGH #2: Initialize SpeculativePrefetcher for streaming link prediction
        # Must be after ctx is set so DuckDB path is available
        try:
            from ..knowledge.link_prediction import (
                StreamingLinkPredictor,
                LinkPredictorConfig,
            )
            # [BREAKTHROUGH-2]: Get DuckDB path from orchestrator or ctx
            db_path: str | None = None
            if self._orchestrator:
                # Try to get path from DuckDB store object
                _duckdb = getattr(self._orchestrator, '_duckdb_store', None)
                if _duckdb is not None:
                    # DuckDB store object may have a path attribute
                    db_path = getattr(_duckdb, '_db_path', None) or getattr(_duckdb, 'path', None)
            if db_path is None:
                # Fall back to context
                db_path = self._ctx.get('duckdb_path')
            
            if db_path:
                config = LinkPredictorConfig(
                    streaming_mode=True,
                    flush_interval_ms=50,
                    max_pending_nodes=100,
                    generate_url_candidates=True,
                )
                self._speculative_prefetcher = SpeculativePrefetcher(
                    self,
                    max_prefetch=100,
                )
                # [BREAKTHROUGH-2]: Store DuckDB path in context for link predictor
                self._ctx['duckdb_path'] = db_path
                logger.info('[BREAKTHROUGH-2] SpeculativePrefetcher initialized with db_path=%s', db_path)
            else:
                logger.debug('[BREAKTHROUGH-2] No DuckDB path available for SpeculativePrefetcher')
        except ImportError as e:
            logger.warning('[BREAKTHROUGH-2] Streaming link predictor unavailable: %s', e)
            self._speculative_prefetcher = None
        except Exception as e:
            logger.warning('[BREAKTHROUGH-2] SpeculativePrefetcher init failed: %s', e)
            self._speculative_prefetcher = None

    def _url_priority(self, url: str) -> int:
        """
        Sprint 5B: Lightweight priority scoring for frontier intake.
        Lower score = higher priority (processed first).
        Priority: API > JSON > HTML > Tor > I2P

        LOW-2 fix: Use named constants instead of magic numbers.
        """
        lower = url.lower()
        if '.onion' in lower:
            return _PRIORITY_TOR
        if '.i2p' in lower:
            return _PRIORITY_I2P
        if '/api/' in lower or 'api.' in lower or lower.endswith('/json'):
            return _PRIORITY_API
        if lower.endswith('.json') or lower.endswith('.xml') or lower.endswith('.rss'):
            return _PRIORITY_JSON
        if '.onion' not in lower and '.i2p' not in lower:
            return _PRIORITY_CLEARNET_HTML
        return _PRIORITY_OTHER

    async def _robots_check(self, url: str) -> tuple[bool, str | None]:
        """F-05: Check robots.txt before fetching. Returns (allowed, reason)."""
        _rp = self._robots_parser
        if _rp is None:
            return (True, None)
        try:
            _host = _fast_url_host(url)
            _path = _fast_url_path(url)
            if not _host:
                return (True, None)
        except (ValueError, TypeError):  # noqa: BLE001 — best-effort; URL parse failure; skip robots check
            return (True, None)
        try:
            _doc = await _rp.fetch_robots(url)
        except Exception:  # noqa: BLE001 — best-effort; robots fetch failure; allow by default
            return (True, None)
        if _doc is None:
            return (True, None)
        _ua = getattr(self, '_effective_ua', None) or 'Hledac-Bot/1.0'
        if not _rp.can_fetch(_path, _ua, _doc):
            return (False, 'robots_blocked')
        _delay = _rp.get_crawl_delay(_ua, _doc)
        if _delay > 0:
            try:
                await asyncio.sleep(_delay)
            except asyncio.CancelledError:
                # P0-3 FIX: Re-raise CancelledError to honour cancellation.
                # Blanket suppress() would prevent clean shutdown propagation.
                raise
            except TimeoutError:
                # TimeoutError: skip the crawl delay (acceptable: slow → proceed anyway)
                pass
        return (True, None)

    async def _robots_check_fast(
        self,
        url: str,
        domain_robots: dict[str, Any],
        user_agent: str,
    ) -> tuple[bool, str | None, float]:
        """
        F-05: Fast robots.txt check using pre-fetched domain docs.

        Returns (allowed, reason, crawl_delay_seconds).
        No HTTP requests — all docs must be pre-fetched via domain pre-fetch.
        """
        try:
            _host = _fast_url_host(url)
            _path = _fast_url_path(url)
            if not _host:
                return (True, None, 0.0)
        except (ValueError, TypeError):
            return (True, None, 0.0)
        _doc = domain_robots.get(_host.lower())
        if _doc is None:
            return (True, None, 0.0)
        _rp = self._robots_parser
        if _rp is None:
            return (True, None, 0.0)
        if not _rp.can_fetch(_path, user_agent, _doc):
            return (False, 'robots_blocked', 0.0)
        _delay = _rp.get_crawl_delay(user_agent, _doc)
        return (True, None, _delay)

    # -------------------------------------------------------------------------
    # _do_step helper methods (complexity reduction: extract phases)
    # -------------------------------------------------------------------------

    def _collect_frontier_batch(self) -> list[str]:
        """Phase 1: Collect URLs from frontier."""
        raw_batch: list[str] = []
        for _ in range(self._config.max_urls_per_step * 2):
            if not self._frontier:
                break
            url = self._frontier.popleft()
            raw_batch.append(url)
        return raw_batch

    async def _resolve_dns_and_dedup(self, raw_batch: list[str]) -> tuple[set[str], list[str]]:
        """Phase 2: Extract hosts, resolve DNS, dedup URLs."""
        self._host_ips_cache = {}
        self._host_ips_inflight = {}
        self._batch_cp_result = _CP_NOT_CALLED
        if self._concurrency_provider is not None:
            try:
                _result = self._concurrency_provider()
                self._batch_cp_result = _result if _result is not None else _CP_RETURNED_NONE
            except (TypeError, ValueError):  # noqa: BLE001
                pass

        def _extract_raw_hosts() -> set[str]:
            """Extract unique hosts from raw batch (sync, fast — runs in thread pool)."""
            hosts: set[str] = set()
            for url in raw_batch:
                _url_lower = url.lower()
                if _url_lower.endswith('.onion') or _url_lower.endswith('.i2p'):
                    continue
                try:
                    at_slashes = url.find('://')
                    if at_slashes < 0:
                        continue
                    host_start = at_slashes + 3
                    host_end = len(url)
                    for i in range(host_start, len(url)):
                        c = url[i]
                        if c in {':', '/', '?', '#'}:
                            host_end = i
                            break
                    hostname = url[host_start:host_end]
                    if hostname:
                        hosts.add(hostname.lower())
                except (ValueError, TypeError):  # noqa: BLE001
                    continue
            return hosts

        def _dedup_and_trace() -> tuple[list[str], int]:
            """Dedup + trace (sync CPU — runs in thread pool).
            
            P4-4 FIX: Use add_to_filter=False to defer bloom filter updates
            until after successful URL processing. This prevents permanent
            seed loss when URLs fail validation after the dedup check.
            URLs are added to bloom in _execute_dns_circuit_phase only
            after passing DNS/circuit validation.
            """
            # P4-4: Don't add to bloom here - defer until after successful processing
            unique, dropped = dedupe_url_list(raw_batch, self._processed_urls, add_to_filter=False)
            for url in raw_batch:
                trace_dedup_decision(url, url not in unique)
            return (unique, dropped)

        resolver = get_batch_dns_resolver()
        raw_hosts_task = asyncio.to_thread(_extract_raw_hosts)
        dedup_task = asyncio.to_thread(_dedup_and_trace)
        raw_hosts = await raw_hosts_task

        dns_coro: asyncio.Task[dict[str, list[str]]] | None = None
        if raw_hosts:
            if _RUST_DNS is not None and _RUST_DNS_ENABLED:
                dns_coro = safe_create_task(
                    asyncio.to_thread(_rust_dns_prefetch, list(raw_hosts))
                )
            else:
                dns_coro = safe_create_task(resolver.resolve_many(list(raw_hosts), timeout=5.0))

        unique_batch, dropped = await dedup_task

        if dns_coro is not None:
            try:
                resolved = await dns_coro
                self._host_ips_cache = {h: list(ips) for h, ips in resolved.items()}
            except asyncio.CancelledError:
                # P0-3 FIX: Re-raise CancelledError for proper cancellation propagation.
                raise
            except (TimeoutError, OSError) as exc:  # noqa: BLE001
                logger.debug('[F-A4] batch DNS pre-resolve failed: %s: %s', type(exc).__name__, exc)

        return (raw_hosts, unique_batch)

    async def _apply_gate_filters(self, unique_batch: list[str]) -> tuple[set[str], set[str], list[Any]]:
        """
        Phase 3: Apply cross-sprint gate and entity confirmation filters.
        
        [NEXTGEN-04] UNIFIED ARCHITECTURE: 
        Now uses CrossSprintGate as single entry point for tiered skip decisions.
        CrossSprintGate.should_skip_batch() handles:
          1. MmapDeltaIndex.is_fresh_batch() — bundle-based, O(1), zero-latency
          2. SprintDeltaIndex.is_known_good_batch() — DuckDB-based confirmation
          3. DuckDB deep query — full historical analysis
        
        This ensures MmapDeltaIndex is consistently wired as tier 1 and eliminates
        redundant direct calls.
        """
        skip_set: set[str] = set()
        freshness_list: list[Any] = []
        confirmed_set: set[str] = set()

        # [NEXTGEN-04] UNIFIED: CrossSprintGate handles all tiered lookups
        # including MmapDeltaIndex as tier 1 (zero-latency bundle check)
        try:
            if self._cross_sprint_gate is not None and self._cross_sprint_gate.enabled:
                # Build entity list from URLs
                gate_entities: list[dict[str, str]] = []
                for url in unique_batch:
                    _host = _fast_url_host(url)
                    if _host:
                        gate_entities.append({"entity_value": _host.lower(), "entity_type": "domain"})
                
                if gate_entities:
                    # CrossSprintGate handles:
                    #   - MmapDeltaIndex tier 1 (bundle freshness)
                    #   - SprintDeltaIndex tier 2 (DuckDB confirmation)
                    #   - DuckDB deep query tier 3 (full history)
                    cs_skip_set, freshness_list = await self._cross_sprint_gate.should_skip_batch(gate_entities)
                    skip_set.update(cs_skip_set)
                    
                    # Track telemetry for MmapDeltaIndex hits
                    mmap_stats = self._cross_sprint_gate.get_stats()
                    self._telemetry["mmap_delta_skipped"] = mmap_stats.get("mmap_delta_skips", 0)
                    
        except Exception:  # noqa: BLE001
            pass  # Fail-soft: continue without cross-sprint filtering

        # [META]-014: Entity confirmation check (additional validation layer)
        if self._entity_confirmation_service is not None and self._entity_confirmation_service.enabled:
            try:
                conf_tuples = [
                    (_fast_url_host(url).lower(), "domain")
                    for url in unique_batch
                    if _fast_url_host(url) and _fast_url_host(url).lower() not in skip_set
                ]
                if conf_tuples:
                    conf_results = await self._entity_confirmation_service.is_confirmed_batch(conf_tuples)
                    for _key, _conf in conf_results.items():
                        if _conf.is_confirmed:
                            confirmed_set.add(_conf.entity_value)
            except Exception:  # noqa: BLE001
                pass

        return (skip_set, confirmed_set, freshness_list)

    def _build_priority_candidates(
        self,
        unique_batch: list[str],
        skip_set: set[str],
        confirmed_set: set[str],
        freshness_list: list[Any],
    ) -> list[tuple[float, str]]:
        """Phase 4: Build prioritized candidate list from URLs."""
        candidates: list[tuple[float, str]] = []
        for url in unique_batch:
            _host = _fast_url_host(url).lower()
            if _host and _host in skip_set:
                self._telemetry["cross_sprint_skipped"] += 1
                continue
            if _host and _host in confirmed_set:
                self._telemetry["entity_confirmation_skipped"] += 1
                continue
            _priority = self._url_priority(url)
            if _host:
                for _f in freshness_list:
                    if _f.entity_value == _host and _f.freshness == "novel":
                        _priority = max(_priority - 5, _PRIORITY_API)
                        break
            candidates.append((_priority, url))
        return candidates

    async def _prefetch_and_filter_robots(
        self,
        urls_to_fetch: list[str],
        user_agent: str,
    ) -> list[str]:
        """Phase 5: Prefetch robots.txt and filter URLs."""
        domain_robots: dict[str, Any] = {}
        if self._robots_parser is not None:
            unique_domains: set[str] = set()
            for _url in urls_to_fetch:
                try:
                    at_slashes = _url.find('://')
                    if at_slashes < 0:
                        continue
                    host_start = at_slashes + 3
                    host_end = len(_url)
                    for i in range(host_start, len(_url)):
                        c = _url[i]
                        if c in {':', '/', '?', '#'}:
                            host_end = i
                            break
                    _domain = _url[host_start:host_end]
                except (ValueError, TypeError):
                    continue
                if _domain:
                    unique_domains.add(_domain.lower())
            if unique_domains:
                async def _prefetch_domain(domain: str) -> tuple[str, Any]:
                    try:
                        _doc = await self._robots_parser.fetch_robots(f'https://{domain}/')
                    except Exception:
                        return (domain, None)
                    return (domain, _doc)
                domain_docs = await parallel(
                    [_prefetch_domain(d) for d in unique_domains],
                    policy="collect",
                    concurrency=10,
                    ctx="robots_prefetch",
                )
                for _item in domain_docs:
                    if isinstance(_item, Exception):
                        continue
                    _domain, _doc = _item
                    domain_robots[_domain] = _doc

        # Filter URLs based on robots.txt
        robots_results = await parallel(
            [self._robots_check_fast(_url, domain_robots, user_agent) for _url in urls_to_fetch],
            policy="collect",
            concurrency=20,
            ctx="robots_check",
        )
        filtered: list[str] = []
        total_delay: float = 0.0
        for _url, _result in zip(urls_to_fetch, robots_results.ok, strict=True):
            _allowed, _reason, _delay = _result
            total_delay += _delay
            if not _allowed:
                logger.debug('[ROBOTS] blocked by robots.txt: %s (%s)', _url, _reason)
                trace_fetch_end(_url, 'robots', 'blocked', 0.0, {'reason': _reason})
                continue
            filtered.append(_url)

        if total_delay > 0:
            try:
                await asyncio.sleep(total_delay)
            except asyncio.CancelledError:
                # P0-3 FIX: Re-raise CancelledError to honour cancellation.
                raise
            except asyncio.TimeoutError:
                # TimeoutError: skip the cumulative delay (acceptable: slow → proceed anyway)
                pass

        return filtered

    @staticmethod
    def _strip_result_content(result: dict[str, Any] | None) -> dict[str, Any] | None:
        """E4 FIX: Strip heavy content fields from result to release memory.
        
        Content (bytes) and text (str) are not needed after post-processing.
        Keeping them in memory wastes 2-10MB per result.
        Returns a lightweight dict with only metadata fields.
        """
        if result is None:
            return None
        # Create lightweight result with only essential fields
        return {
            'url': result.get('url'),
            'final_url': result.get('final_url'),
            'status_code': result.get('status_code', 0),
            'content_type': result.get('content_type', ''),
            'headers': result.get('headers', {}),
            'success': result.get('success', False),
            'error': result.get('error'),
            'evidence_id': result.get('evidence_id'),  # May be None
            # Explicitly drop 'content' and 'text' to release memory
        }

    async def _execute_batch_fetch(self, urls_to_fetch: list[str]) -> tuple[list[dict[str, Any] | None], float]:
        """Phase 6: Execute parallel fetch with TOR/I2P vs clearnet separation.
        
        E4 FIX: Uses lightweight result storage to avoid holding full content.
        Content is stripped immediately after fetch completes to release memory.
        Peak memory is now O(batch_size × overhead) instead of O(batch_size × max_bytes).
        """
        from ..transport.transport_resolver import Transport, get_transport_for_url

        tor_i2p_urls: list[str] = []
        clearnet_urls: list[str] = []
        for _url in urls_to_fetch:
            _transport = get_transport_for_url(_url)
            if _transport in (Transport.TOR, Transport.I2P):
                tor_i2p_urls.append(_url)
            else:
                clearnet_urls.append(_url)

        batch_start = time.time()
        url_to_result: dict[str, dict[str, Any] | None] = {}

        if tor_i2p_urls:
            # P4-5 FIX: policy="log" returns list[T], not ParallelResult.
            # E4 FIX: Strip content immediately after fetch to release memory.
            tor_i2p_results = await parallel(
                [self._fetch_url(url) for url in tor_i2p_urls],
                concurrency=min(len(tor_i2p_urls), 2),
                policy="log",
                ctx="fetch_coordinator.batch.tor_i2p",
            )
            for _url, _res in zip(tor_i2p_urls, tor_i2p_results, strict=False):
                url_to_result[_url] = self._strip_result_content(_res)

        if clearnet_urls:
            # P4-5 FIX: policy="log" returns list[T], not ParallelResult.
            # E4 FIX: Strip content immediately after fetch to release memory.
            clearnet_results = await parallel(
                [self._fetch_url(url) for url in clearnet_urls],
                concurrency=len(urls_to_fetch),
                policy="log",
                ctx="fetch_coordinator.batch",
            )
            for _url, _res in zip(clearnet_urls, clearnet_results, strict=False):
                url_to_result[_url] = self._strip_result_content(_res)

        results = [url_to_result.get(_url) for _url in urls_to_fetch]
        batch_elapsed = time.time() - batch_start
        return (results, batch_elapsed)

    def _process_fetch_results(
        self,
        urls_to_fetch: list[str],
        results: list[dict[str, Any] | None],
        budget_mgr: Any,
    ) -> list[str]:
        """Phase 7: Process fetch results and extract evidence IDs.
        
        E4 FIX: Results already have content stripped by _execute_batch_fetch.
        This method only extracts evidence_ids from lightweight result dicts.
        """
        evidence_ids: list[str] = []
        for url, result in zip(urls_to_fetch, results, strict=False):
            if isinstance(result, Exception):
                logger.debug('[BATCH] fetch exception for %s: %s', url, type(result).__name__)
                continue
            if result and result.get('success'):
                self._urls_fetched_count += 1
                evidence_id = result.get('evidence_id')
                if evidence_id:
                    evidence_ids.append(evidence_id)
                    self._evidence_ids.append(evidence_id)
                    if self._evidence_sink is not None:
                        with contextlib.suppress(Exception):
                            self._evidence_sink.append_evidence(evidence_id)
                    _entity_host = _fast_url_host(url)
                    if _entity_host:
                        self.report_entity_discovery(_entity_host, "domain")
                if budget_mgr:
                    allowed, reason = budget_mgr.check_snapshot_allowed()
                    if not allowed:
                        self._stop_reason = reason
                        break
        return evidence_ids


    async def _do_step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one fetch step with batch parallel fetch.

        Sprint 5B: Process up to max_urls_per_step from frontier using
        controlled parallel batch fetch that respects:
        - timeout matrix
        - concurrency matrix
        - AIMD window

        Complexity reduction: delegates to helper methods for each phase.

        BREAKTHROUGH #2: Phase 2.5 - Speculative prefetch via link prediction.
        """
        self._ctx.update(ctx)
        budget_mgr = ctx.get('budget_manager')
        if budget_mgr:
            allowed, reason = budget_mgr.check_network_allowed()
            if not allowed:
                self._stop_reason = reason
                return self._get_step_result()

        # Phase 1: Collect URLs from frontier
        raw_batch = self._collect_frontier_batch()
        if not raw_batch:
            self._stop_reason = 'frontier_empty'
            return self._get_step_result()

        # Phase 2: Resolve DNS and dedup
        _, unique_batch = await self._resolve_dns_and_dedup(raw_batch)
        del raw_batch

        # FIX: Phase 2.5 now runs IN PARALLEL with Phase 3 to hide latency
        # Use TaskGroup for concurrent execution
        prefetch_result = SpeculativePrefetchResult()
        gate_filters_ready = asyncio.Event()
        gate_filters_result: dict[str, Any] = {}
        
        async def _run_phase_25():
            """Phase 2.5: BREAKTHROUGH #2 - Speculative prefetch via link prediction."""
            nonlocal prefetch_result
            if hasattr(self, '_speculative_prefetcher') and self._speculative_prefetcher is not None:
                try:
                    prefetch_result = await self._speculative_prefetcher.execute_speculative_prefetch()
                    # Update telemetry
                    self._telemetry['speculative_prefetch_count'] = prefetch_result.prefetch_count
                    self._telemetry['speculative_prefetch_dedup'] = prefetch_result.dedup_skipped
                    self._telemetry['speculative_dns_prefetch'] = prefetch_result.dns_prefetched
                except Exception as e:
                    logger.debug("[BREAKTHROUGH-2] Speculative prefetch failed: %s", e)
            gate_filters_ready.set()
        
        async def _run_phase_3():
            """Phase 3: Apply gate filters."""
            nonlocal gate_filters_result
            skip_set, confirmed_set, freshness_list = await self._apply_gate_filters(unique_batch)
            gate_filters_result = {
                'skip_set': skip_set,
                'confirmed_set': confirmed_set,
                'freshness_list': freshness_list,
            }
        
        # Run phases 2.5 and 3 in parallel
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(_run_phase_25(), name='phase_25_prefetch')
                tg.create_task(_run_phase_3(), name='phase_3_gate_filters')
        except* Exception as exc_group:
            logger.debug("[BREAKTHROUGH-2] Parallel phase execution failed: %s", exc_group)
        
        # Wait for Phase 2.5 to complete (Phase 3 may complete faster)
        await gate_filters_ready.wait()
        
        # Get Phase 3 results
        skip_set = gate_filters_result.get('skip_set', set())
        confirmed_set = gate_filters_result.get('confirmed_set', set())
        freshness_list = gate_filters_result.get('freshness_list', [])

        # Phase 4: Build priority candidates
        candidates = self._build_priority_candidates(unique_batch, skip_set, confirmed_set, freshness_list)
        del unique_batch
        if not candidates:
            self._stop_reason = 'frontier_empty'
            return self._get_step_result()

        candidates.sort(key=lambda x: x[0])
        urls_to_fetch = [url for _, url in candidates[:self._config.max_urls_per_step]]
        del candidates

        # Phase 5: Robots.txt prefetch and filtering
        user_agent = getattr(self, '_effective_ua', None) or 'Hledac-Bot/1.0'
        urls_to_fetch = await self._prefetch_and_filter_robots(urls_to_fetch, user_agent)
        if not urls_to_fetch:
            return self._get_step_result()

        # Phase 6: Batch fetch
        effective_batch_size = min(len(urls_to_fetch), int(self._aimd_concurrency))
        urls_to_fetch = urls_to_fetch[:effective_batch_size]
        batch_size = len(urls_to_fetch)

        if is_enabled():
            trace_counter('fetch.aimd.window', self._aimd_concurrency)
            trace_counter('fetch.active', self._telemetry['active_fetches'])
            trace_counter('fetch.batch_size', batch_size)
            # BREAKTHROUGH #2: Trace prefetch telemetry
            trace_counter('fetch.prefetch.urls', prefetch_result.prefetch_count)
            trace_counter('fetch.prefetch.dns', prefetch_result.dns_prefetched)

        results, batch_elapsed = await self._execute_batch_fetch(urls_to_fetch)

        # Phase 7: Process results
        evidence_ids = self._process_fetch_results(urls_to_fetch, results, budget_mgr)
        effective_parallelism = min(len(urls_to_fetch), int(self._aimd_concurrency))

        return self._get_step_result(
            evidence_ids,
            batch_size=batch_size,
            effective_parallelism=effective_parallelism,
            batch_elapsed_ms=round(batch_elapsed * 1000, 2),
            prefetch_count=prefetch_result.prefetch_count,
            prefetch_dns=prefetch_result.dns_prefetched,
        )

    async def _check_dns_and_circuit(self, url: str, domain: str) -> tuple[bool, dict[str, Any], bool, str, float]:
        """Parallel DNS + circuit-breaker check (used in validation + retry loop).

        Runs DNS validation and circuit-breaker check concurrently via TaskGroup.
        Returns (dns_safe, dns_meta, cb_allowed, cb_reason, cb_retry_after).

        Handles .onion/.i2p domains: DNS check short-circuits to (True, {}).
        """
        async def _dns_check() -> tuple[bool, dict[str, Any]]:
            """DNS validation — cached after first call."""
            # SEC-03 FIX: case-insensitive darknet TLD check
            _url_lower = url.lower()
            if _url_lower.endswith('.onion') or _url_lower.endswith('.i2p'):
                return (True, {})
            return await self._validate_fetch_target(url)

        async def _circuit_breaker_check() -> tuple[bool, str, float]:
            """Circuit breaker — sync in-memory, ~1-2ms."""
            return self._check_circuit(domain)

        try:
            async with asyncio.TaskGroup() as tg:
                dns_task = tg.create_task(_dns_check(), name='dns_check')
                cb_task = tg.create_task(_circuit_breaker_check(), name='circuit_breaker')
            dns_safe, dns_meta = dns_task.result()
            cb_allowed, cb_reason, cb_retry_after = cb_task.result()
        except* (OSError, asyncio.TimeoutError) as exc_group:  # noqa: BLE001 — fail-closed SSRF; network errors = block request
            # Log the actual exception(s) for debugging; DO NOT swallow silently
            for exc in exc_group.exceptions:
                logger.warning(
                    'DNS/circuit check failed, fail-closed: %s (%s)',
                    type(exc).__name__,
                    exc,
                )
            # P0-3 SSRF fix: fail-CLOSED - block on DNS/validation errors
            dns_safe, dns_meta = (False, {'blocked_reason': 'dns_circuit_check_failed'})
            # Circuit breaker is permissive on failure (cb_allowed=True); circuit is secondary to DNS validation
            cb_allowed, cb_reason, cb_retry_after = (True, '', 0.0)
        # CancelledError propagates naturally via except* — no explicit handler needed
        return (dns_safe, dns_meta, cb_allowed, cb_reason, cb_retry_after)

    def _get_step_result(
        self,
        new_evidence_ids: list[str] | None=None,
        batch_size: int=0,
        effective_parallelism: int=0,
        batch_elapsed_ms: float=0.0,
        prefetch_count: int=0,
        prefetch_dns: int=0,
    ) -> dict[str, Any]:
        """Get bounded step result with Sprint 5B batch telemetry."""
        evidence_ids = (new_evidence_ids or [])[:self._config.max_evidence_per_step]
        return {
            'urls_fetched': len(evidence_ids),
            'evidence_ids': evidence_ids,
            'total_fetched': self._urls_fetched_count,
            'stop_reason': self._stop_reason,
            'frontier_remaining': len(self._frontier),
            'aimd_window': self._aimd_concurrency,
            'active_fetches': self._telemetry['active_fetches'],
            'batch_size': batch_size,
            'effective_parallelism': effective_parallelism,
            'batch_elapsed_ms': batch_elapsed_ms,
            # E4 FIX: Inflight memory telemetry
            'inflight_permits': self._inflight_sem._value,
            'inflight_bytes': self._inflight_bytes,
            # BREAKTHROUGH #2: Speculative prefetch telemetry
            'prefetch_count': prefetch_count,
            'prefetch_dns': prefetch_dns,
        }

    @_otel_instrumented('fetch.url', component='network')
    async def _fetch_url(self, url: str, attempt: int=0) -> dict[str, Any] | None:
        """
        F360-R: Refactored to delegate to _fetch_url_impl().

        See _fetch_url_impl() for full documentation.
        """
        return await self._fetch_url_impl(url, attempt)

    async def _fetch_url_impl(self, url: str, attempt: int) -> dict[str, Any] | None:
        """
        F360-R: Refactored implementation using phase-based pipeline.

        This method orchestrates distinct phases with clear data flow:
        1. Preflight: offline check, rate limiting, privacy, AIMD, governor
        2. DNS/Circuit: DNS validation + circuit breaker + transport setup
        3. Retry loop: transport dispatch with retry logic
        4. Cleanup: release resources (ALWAYS via finally block)
        5. Post-process: paywall bypass, content-type, CAPTCHA

        Returns:
            Fetch result dict or None on skip/failure.
        """
        from urllib.parse import urlparse
        from ..project_types import OfflineModeError, is_offline_mode

        # Phase 1: Preflight - rate limiting, privacy, AIMD, governor checks
        _pf = await self._execute_preflight_phase(url)

        try:
            # Phase 0: Offline check (early exit)
            if is_offline_mode():
                raise OfflineModeError(f'Offline mode enabled, skipping fetch: {url}')

            # Handle skip case from preflight
            if _pf.skip_fetch:
                return None

            # Phase 2: DNS/Circuit + transport pre-acquisition
            _parsed = urlparse(url)
            _dc = await self._execute_dns_circuit_phase(url, _parsed, _pf.host_name, _pf.quinn_viable, _pf.aimd_acquired)  # P1-6 CRITICAL FIX: propagate AIMD acquisition state

            # Handle DNS blocked case
            if _dc.skip_fetch:
                return _dc.skip_result

            # Phase 3: Main retry loop
            trace_fetch_start(url, 'pending', {'attempt': attempt, 'aimd_window': self._aimd_concurrency})
            
            # E4 FIX: Calculate effective max_bytes based on URL pattern before fetch
            # This reduces memory usage for non-article content from 10MB to 2MB
            _effective_max_bytes = self._get_effective_max_bytes(url)
            
            result = await self._fetch_with_retry_loop(
                url=url,
                attempt=attempt,
                max_retries=getattr(self, '_max_retries', 3),
                base_delay=getattr(self, '_base_retry_delay', 1.0),
                url_transport=_dc.url_transport,
                route_decision=_dc.route_decision,
                canonical_allowed=_dc.canonical_allowed,
                canonical_reason=_dc.canonical_reason,
                _host_name=_pf.host_name,
                _resolve=_dc.resolve,
                _quinn_viable=_dc.quinn_viable,
                _pre_acquired_tor_session=_dc.pre_acquired_tor_session,
                _pre_acquired_i2p_session=_dc.pre_acquired_i2p_session,
                proxy=_dc.proxy,
                _effective_max_bytes=_effective_max_bytes,  # E4 FIX: pass effective max_bytes
            )

            # Phase 5: Post-processing
            return self._fetch_url_postprocess(result, url, _pf.host_name)
        finally:
            # P1-7 FIX: Centralized cleanup via finally block ensures resources are ALWAYS released
            # even on early returns, exceptions, or cancellation.
            # Resources cleaned: _aimd_semaphore, privacy_lane, host_sem
            self._cleanup_fetch_resources(_pf)

    async def _execute_preflight_phase(self, url: str) -> _PrefetchResult:
        """
        F360-R: Phase 1 - Preflight checks (rate limit, privacy, AIMD, governor).

        Returns _PrefetchResult with skip_fetch=True if we should skip this URL.
        """
        # Extract host name
        _host_name = ''
        with contextlib.suppress(ValueError, TypeError):
            _host_name = _fast_url_host(url) or ''

        # Rate limiting + host semaphore
        _host_sem: asyncio.Semaphore | None = None
        if _host_name and not url.lower().endswith(('.onion', '.i2p')):
            _rate_wait = await self._domain_rate_limiter.acquire(_host_name)
            if _rate_wait > 0:
                logger.debug('[RATE_LIMIT] Waited %.2fs for rate limit on %s', _rate_wait, _host_name)
            _host_sem, _ = await self._per_host_gate.acquire(_host_name)

        # Privacy lane acquisition
        _privacy_lane = 'clearnet'
        _privacy_acquired = False
        try:
            _privacy_lane, _privacy_acquired = await self._privacy_acquire_for_url(url)
        except asyncio.CancelledError:
            # P0-3 FIX: Cancellation is fail-CLOSED (block), not fail-open to clearnet.
            # Re-raising ensures proper shutdown propagation.
            raise
        except TimeoutError:
            # TimeoutError is fail-open to clearnet (acceptable: slow privacy lane → fall back)
            _privacy_lane = 'clearnet'
            _privacy_acquired = True

        # Governor checks (U2-05): io_only and can_afford
        _skip_reason = await self._check_governor_early_exit(url)
        if _skip_reason:
            async with self._dedup_lock:
                self._processed_urls.discard(url)
            self._telemetry['io_only_skipped' if 'io_only' in _skip_reason else 'entity_confirmation_skipped'] += 1
            return _PrefetchResult(skip_fetch=True, skip_reason=_skip_reason, host_name=_host_name)

        # AIMD acquisition (only if not skipping)
        _aimd_acquired = False
        if _privacy_acquired:
            await self._aimd_acquire()
            _aimd_acquired = True

        # QUINN viability check (F350M-R)
        _quinn_viable = self._check_quinn_viability(url)

        return _PrefetchResult(
            skip_fetch=False,
            host_name=_host_name,
            host_sem=_host_sem,
            privacy_lane=_privacy_lane,
            quinn_viable=_quinn_viable,
            aimd_acquired=_aimd_acquired,
        )

    async def _check_governor_early_exit(self, url: str) -> str | None:
        """F360-R: Governor io_only and can_afford checks. Returns skip reason or None."""
        try:
            from hledac.universal._core.protocols import get_governor
            gov = get_governor()
            if gov is not None:
                try:
                    decision = gov.evaluate()
                    if decision.io_only:
                        return 'io_only'
                except Exception:  # noqa: BLE001
                    pass
                from ..core.resource_governor import Priority
                if not gov.can_afford_sync({'ram_mb': 15}, Priority.CRITICAL):
                    return 'cannot_afford'
        except (TypeError, ValueError, KeyError):  # noqa: BLE001
            pass
        return None

    def _check_quinn_viability(self, url: str) -> bool:
        """F360-R: QUINN HTTP/3 viability check (synchronous, no await needed)."""
        try:
            from hledac.universal.transport.http3_lane import http_version_for_curl_cffi
            _quinn_http_version = http_version_for_curl_cffi(url)
            return _quinn_http_version is not None
        except (ImportError, Exception):  # noqa: BLE001
            return False

    async def _execute_dns_circuit_phase(
        self,
        url: str,
        parsed: Any,  # urllib.parse.ParseResult
        host_name: str,
        quinn_viable: bool = False,
        aimd_acquired: bool = False,  # P1-6 CRITICAL FIX: track if AIMD was acquired in preflight
    ) -> _DnsCircuitResult:
        """
        F360-R: Phase 2 - DNS + circuit breaker validation + transport setup.

        Returns _DnsCircuitResult with skip_fetch=True if DNS blocked.
        """
        from ..transport.transport_resolver import RouteDecision, Transport, async_get_route_decision

        # DNS + circuit breaker check
        dns_safe, dns_meta, canonical_allowed, canonical_reason, canonical_retry_after = (
            await self._check_dns_and_circuit(url, host_name)
        )

        # Handle DNS blocked case
        if not dns_safe:
            logger.warning("DNS rebinding defense blocked: %s for %s", dns_meta.get('blocked_reason'), host_name)
            trace_fetch_end(url, 'dns_rebind_defense', 'blocked', 0.0, {'reason': dns_meta.get('blocked_reason')})
            # P1-6 CRITICAL FIX: Only release AIMD semaphore if it was acquired in preflight.
            # _cleanup_fetch_resources will skip its own release when aimd_acquired=True,
            # so this is the ONLY release point for DNS-blocked paths.
            if aimd_acquired:
                self._aimd_semaphore.release()
            return _DnsCircuitResult(
                skip_fetch=True,
                skip_result={'error': 'blocked', 'blocked_reason': dns_meta.get('blocked_reason'), 'meta': dns_meta},
                dns_safe=False,
                dns_meta=dns_meta,
                canonical_allowed=False,
                canonical_reason=canonical_reason,
                canonical_retry_after=canonical_retry_after,
                resolve=None,
            )

        # Add to dedup
        async with self._dedup_lock:
            self._processed_urls.add(url)

        # Transport + session pre-acquisition
        # NEW-C1 FIX: Use Transport.DIRECT (not CLEARNET - CLEARNET is in RouteDecision, not Transport)
        from ..transport.transport_resolver import Transport as _T
        url_transport: _T = _T.DIRECT  # Default fallback: DIRECT = clearnet equivalent
        try:
            from ..transport.transport_resolver import get_transport_for_url
            url_transport = get_transport_for_url(url)
        except Exception:  # noqa: BLE001
            pass

        route_decision = await async_get_route_decision(url)
        _pre_acquired_tor_session: Any | None = None
        _pre_acquired_i2p_session: Any | None = None

        try:
            # NEW-C1 FIX: Use _T alias (not bare Transport which is module-level import)
            if url_transport is _T.TOR and route_decision.name != 'TOR_UNAVAILABLE':
                _pre_acquired_tor_session = await self._get_tor_session(host_name)
            elif url_transport is _T.I2P and route_decision.name != 'I2P_UNAVAILABLE':
                _pre_acquired_i2p_session = await self._get_i2p_session(host_name)
        except Exception:  # noqa: BLE001
            pass

        # DNS rebinding protection - compute resolve binding
        _resolve = self._compute_resolve_binding(url, parsed, host_name, dns_meta)

        # Proxy selection
        _proxy: str | None = None
        if self._current_geo_context and self._current_geo_context in self._geo_proxies:
            _proxy = self._geo_proxies.get(self._current_geo_context)

        return _DnsCircuitResult(
            skip_fetch=False,
            dns_safe=True,
            dns_meta=dns_meta,
            canonical_allowed=canonical_allowed,
            canonical_reason=canonical_reason,
            canonical_retry_after=canonical_retry_after,
            resolve=_resolve,
            pre_acquired_tor_session=_pre_acquired_tor_session,
            pre_acquired_i2p_session=_pre_acquired_i2p_session,
            url_transport=url_transport,
            route_decision=route_decision,
            proxy=_proxy,
            quinn_viable=quinn_viable,
            aimd_acquired=aimd_acquired,  # P1-6 CRITICAL FIX: pass through to cleanup
        )

    def _compute_resolve_binding(
        self,
        url: str,
        parsed: Any,
        host_name: str,
        dns_meta: dict[str, Any],
    ) -> dict[str, str] | None:
        """F360-R: Compute DNS resolve binding for rebinding protection."""
        _resolve: dict[str, str] | None = None
        _resolved_ips = dns_meta.get('resolved_ips', [])
        _is_darknet_url = url.lower().endswith(('.onion', '.i2p'))

        if _resolved_ips and not _is_darknet_url:
            try:
                _hostname = parsed.host
                if _hostname:
                    _resolve = {_hostname: _resolved_ips[0]}
            except (ValueError, TypeError):  # noqa: BLE001
                pass
        elif not _is_darknet_url and not _resolved_ips:
            _retry_host = host_name or parsed.host
            if _retry_host:
                try:
                    import socket
                    # F360-R FIX: Use sync socket.getaddrinfo directly in this sync function
                    # The original asyncio.run() anti-pattern created nested event loops which
                    # can cause RuntimeError: asyncio.run() cannot be called from a running event loop
                    # Since _compute_resolve_binding is sync, we use blocking getaddrinfo
                    # wrapped in a timeout-compatible pattern
                    _retry_results: list[tuple] | None = None
                    try:
                        # Use socket.getaddrinfo directly (sync version)
                        _retry_results = socket.getaddrinfo(_retry_host, 0, socket.AF_INET, socket.SOCK_STREAM)
                    except socket.gaierror:
                        # DNS resolution failed
                        _retry_results = None
                    except TimeoutError:
                        _retry_results = None
                    except OSError:
                        _retry_results = None
                    
                    if _retry_results:
                        _retry_ips = sorted({str(r[4][0]) for r in _retry_results})
                        for _ip_str in _retry_ips:
                            if not self._is_ip_public(_ip_str):
                                logger.warning('[SEC-02] DNS rebinding defense: private IP %s for %s', _ip_str, _retry_host)
                                break
                        else:
                            _resolve = {_retry_host: _retry_ips[0]}
                except (TimeoutError, OSError):  # noqa: BLE001
                    pass
        return _resolve

    def _cleanup_fetch_resources(self, preflight: _PrefetchResult) -> None:
        """F360-R: Release all semaphores and resources after fetch."""
        try:
            # P1-6 FIX: Only release AIMD if it was actually acquired.
            # This prevents spurious release (early skip) and double-release
            # (when DNS phase already released after blocking).
            if preflight.aimd_acquired:
                self._aimd_semaphore.release()
            if preflight.privacy_lane != 'clearnet':
                self._privacy_release(preflight.privacy_lane)
            if preflight.host_sem is not None:
                self._per_host_gate.release(preflight.host_sem)
        except Exception as e:  # noqa: BLE001 — best-effort; cleanup failure; log for debugging
            logger.debug('[CLEANUP] Resource cleanup failed: %s (%s)', type(e).__name__, e)

    async def _fetch_with_retry_loop(
        self,
        url: str,
        attempt: int,
        max_retries: int,
        base_delay: float,
        url_transport: Any,
        route_decision: Any,
        canonical_allowed: bool,
        canonical_reason: str,
        _host_name: str,
        _resolve: dict[str, str] | None,
        _quinn_viable: bool,
        _pre_acquired_tor_session: Any,
        _pre_acquired_i2p_session: Any,
        proxy: str | None,
        _effective_max_bytes: int | None = None,  # E4 FIX: effective max_bytes from URL pattern
    ) -> dict[str, Any] | None:
        """
        F360-R: Refactored retry loop with phase-based approach.

        Phases:
        1. Circuit check (early exit if blocked)
        2. Transport dispatch (attempt fetch)
        3. Retry logic (exponential backoff with jitter)
        4. Result recording (success/failure telemetry)
        """
        # Phase 1: Circuit breaker check
        if not canonical_allowed:
            self._handle_circuit_block(url, _host_name, canonical_reason)
            return None

        # Phase 2-3: Retry loop with transport dispatch
        try:
            result = await self._execute_retry_loop(
                url, attempt, max_retries, base_delay, url_transport, route_decision,
                _host_name, _resolve, _quinn_viable, _pre_acquired_tor_session,
                _pre_acquired_i2p_session, proxy, _effective_max_bytes,
            )
        except asyncio.CancelledError:
            # P0-3 FIX: Re-raise CancelledError for proper cancellation propagation.
            # Do NOT suppress - blanket CancelledError handling prevents clean shutdown.
            raise
        except (TimeoutError, httpx.HTTPError, OSError) as e:
            logger.warning('[_fetch_url] Unexpected error for %s: %s', url, e)
            await self._aimd_release_failure()
            return {'url': url, 'content': b'', 'error': str(e)}

        # Phase 4: Record success/failure
        await self._record_fetch_outcome(result, url_transport, _host_name)

        return result

    def _handle_circuit_block(
        self, url: str, host_name: str, reason: str,
    ) -> None:
        """F360-R: Handle circuit breaker block - telemetry only."""
        self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
        logger.debug('[CircuitBreaker] Open for %s: %s', host_name, reason)
        trace_fetch_end(url, 'circuit_breaker', 'circuit_open', 0.0)
        return None

    async def _execute_retry_loop(
        self,
        url: str,
        attempt: int,
        max_retries: int,
        base_delay: float,
        url_transport: Any,
        route_decision: Any,
        host_name: str,
        _resolve: dict[str, str] | None,
        _quinn_viable: bool,
        _pre_acquired_tor_session: Any,
        _pre_acquired_i2p_session: Any,
        proxy: str | None,
        _effective_max_bytes: int | None = None,  # E4 FIX: effective max_bytes
    ) -> dict[str, Any] | None:
        """F360-R: Execute retry loop - dispatch and retry logic."""
        result = None
        while attempt <= max_retries:
            # Dispatch fetch based on transport
            result = await self._dispatch_transport_fetch(
                url, attempt, url_transport, route_decision, host_name,
                _resolve, _quinn_viable, _pre_acquired_tor_session,
                _pre_acquired_i2p_session, proxy, _effective_max_bytes,
            )

            # Check if we should retry
            should_retry, retry_reason = self._evaluate_retry_condition(result, attempt, max_retries)
            if not should_retry:
                break

            # Check retry budget
            budget_allowed, budget_reason = await self._check_retry_budget(host_name)
            if not budget_allowed:
                logger.debug('[RETRY-BUDGET] Skipping retry for %s: %s', host_name, budget_reason)
                self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
                break

            # Calculate delay with exponential backoff and jitter
            delay = self._calculate_retry_delay(base_delay, attempt)
            logger.debug('[RETRY] Attempt %s/%s for %s after %ss', attempt + 1, max_retries, url, delay)
            trace_fetch_end(url, 'none', 'retry', 0.0, {'attempt': attempt, 'delay': delay})
            await self._record_retry(host_name)
            await asyncio.sleep(delay)
            attempt += 1

        return result

    def _evaluate_retry_condition(
        self, result: dict[str, Any] | None, attempt: int, max_retries: int,
    ) -> tuple[bool, str]:
        """F360-R: Evaluate if current result warrants a retry."""
        if result is None:
            return attempt < max_retries, 'result_none'
        if result.get('error'):
            return attempt < max_retries, f"error:{result.get('error')}"
        status_code = result.get('status_code', 200)
        if status_code >= 500:
            return attempt < max_retries, f'status_{status_code}'
        return False, 'success'

    def _calculate_retry_delay(self, base_delay: float, attempt: int) -> float:
        """F360-R: Calculate retry delay with exponential backoff and jitter."""
        _delay = base_delay * (2 ** attempt)
        jitter = _JITTER_RNG.uniform(0, _delay)
        return min(_delay + jitter, 30.0)

    async def _dispatch_transport_fetch(
        self,
        url: str,
        attempt: int,
        url_transport: Any,
        route_decision: Any,
        host_name: str,
        _resolve: dict[str, str] | None,
        _quinn_viable: bool,
        _pre_acquired_tor_session: Any,
        _pre_acquired_i2p_session: Any,
        proxy: str | None,
        _effective_max_bytes: int | None = None,  # E4 FIX: effective max_bytes
    ) -> dict[str, Any] | None:
        """F360-R: Dispatch fetch to appropriate transport.
        
        P4-1 FIX: Was calling non-existent methods _fetch_tor/_fetch_i2p/_fetch_gopher/_fetch_clearnet.
        Now uses correct method names: _fetch_with_tor/_fetch_with_i2p/_fetch_with_curl.
        GOPHER is delegated to self._gopher_transport.fetch().
        """
        # Import Transport locally for dispatch (matches existing pattern in file)
        from ..transport.transport_resolver import Transport as _T
        
        if url_transport is _T.TOR:
            return await self._fetch_with_tor(url, session=_pre_acquired_tor_session, max_bytes=_effective_max_bytes)
        if url_transport is _T.I2P:
            return await self._fetch_with_i2p(url, session=_pre_acquired_i2p_session, max_bytes=_effective_max_bytes)
        if url_transport is _T.GOPHER:
            # GOPHER: use gopher transport if available
            if self._gopher_transport is not None and self._gopher_transport_enabled:
                try:
                    return await self._gopher_transport.fetch(url)
                except Exception as e:
                    logger.warning('[GOPHER] Fetch failed for %s: %s', url, e)
                    return None
            logger.debug('[GOPHER] Gopher transport unavailable')
            return None
        # Clearnet fetch via curl_cffi (preferred)
        # E4 FIX: Use effective max_bytes (2MB for non-articles, 10MB for articles)
        return await self._fetch_with_curl(
            url=url, proxy=proxy, resolve=_resolve,
            _effective_max_bytes=_effective_max_bytes,
        )

    async def _record_fetch_outcome(
        self, result: dict[str, Any] | None, url_transport: Any, host_name: str,
    ) -> None:
        """F360-R: Record fetch outcome to telemetry."""
        # NEW-C1 FIX: Import Transport locally (not using module-level to avoid circular imports)
        from ..transport.transport_resolver import Transport as _T
        
        if result and not result.get('error'):
            result.setdefault('success', True)
            await self._aimd_release_success()
            self._record_success(host_name)
            transport_name = url_transport.name.lower()
            if url_transport is _T.TOR:
                self._record_transport_success("tor")
            elif url_transport is _T.I2P:
                self._record_transport_success("i2p")
            # P4-1 BONUS FIX: _maybe_fire_cover_traffic is async - must be awaited
            await self._maybe_fire_cover_traffic(transport=transport_name)
        elif result is None or result.get('error'):
            is_timeout = result.get('error') == 'timeout' if result else True
            self._record_failure(host_name, is_timeout=is_timeout, failure_kind='fetch_error')
            if url_transport is _T.TOR:
                self._record_transport_failure("tor", is_timeout=is_timeout)
            elif url_transport is _T.I2P:
                self._record_transport_failure("i2p", is_timeout=is_timeout)

    def _fetch_url_postprocess(self, result: dict[str, Any] | None, url: str, _host_name: str) -> dict[str, Any] | None:
        """
        F360-R: Refactored post-processing using phase-based approach.
        
        Phases:
        1. Session rotation for 401/403
        2. Paywall bypass for small responses
        3. Content-type validation (OSINT-04)
        4. Clearance cookie handling (F-07)
        5. CAPTCHA detection
        """
        trace_fetch_end(url, 'none', 'done', 0.0)
        
        # Phase 1: Session rotation
        result = self._postprocess_session_rotation(result, _host_name)
        
        # Phase 2: Paywall bypass
        result = self._postprocess_paywall_bypass(result, url)
        
        # Phase 3: Content-type validation
        result = self._postprocess_content_type(result, url)
        
        # Phase 4: Clearance cookie handling
        result = self._postprocess_clearance(result, url, _host_name)
        
        # Phase 5: CAPTCHA detection
        result = self._postprocess_captcha(result, url)
        
        return result

    def _postprocess_session_rotation(self, result: dict[str, Any] | None, host_name: str) -> dict[str, Any] | None:
        """F360-R: Phase 1 - Rotate credentials on 401/403."""
        if result and result.get('status_code') in (401, 403) and self._session_manager:
            self._session_manager.rotate_credentials(host_name)
            logger.info('[SESSION] Rotated credentials for %s', host_name)
        return result

    def _postprocess_paywall_bypass(self, result: dict[str, Any] | None, url: str) -> dict[str, Any] | None:
        """F360-R: Phase 2 - Paywall bypass for small responses."""
        if result and result.get('content') and self._paywall_bypass:
            content = result['content']
            if isinstance(content, bytes):
                content = content.decode(errors='ignore')
            if len(content) < 5000:
                bypass_result = self._paywall_bypass.bypass(url, content)
                if bypass_result:
                    logger.info("[PAYWALL] Bypassed via %s", bypass_result.get('bypassed'))
                    result['content'] = bypass_result.get('content', '').encode()
                    result['bypassed'] = bypass_result.get('bypassed')
                    result['paywall'] = bypass_result.get('paywall')
        return result

    def _postprocess_content_type(self, result: dict[str, Any] | None, url: str) -> dict[str, Any] | None:
        """F360-R: Phase 3 - OSINT-04 Content-type validation."""
        if result and result.get('content'):
            ct = result.get('content_type', '') or ''
            _safe_ct_prefixes = ('text/', 'application/json', 'application/xml', 'application/xhtml', 'application/ld+json')
            if ct and not any(ct.startswith(p) for p in _safe_ct_prefixes):
                logger.debug('[OSINT-04] Blocking parse for content-type %s on %s', ct[:128], url)
                result['content'] = b''
        return result

    def _postprocess_clearance(self, result: dict[str, Any] | None, url: str, host_name: str) -> dict[str, Any] | None:
        """F360-R: Phase 4 - F-07 Clearance cookie handling."""
        if result and result.get('status_code') in (403, 429) and self._clearance_jar is not None:
            try:
                from ..security.turnstile_solver import detect_turnstile_challenge, get_clearance_for_domain
                result_headers = result.get('headers') or {}
                result_content = result.get('content', b'')
                if detect_turnstile_challenge(url, result.get('status_code', 0), result_headers, result_content):
                    clearance = get_clearance_for_domain(host_name, url, result.get('status_code', 0), result_headers, result_content)
                    if clearance:
                        logger.info('[CLEARANCE] Stored %d clearance cookies for %s', len(clearance), host_name)
            except Exception as e:  # noqa: BLE001
                # NEW-H6 fix: Log clearance cookie failure instead of silent pass
                logger.debug('[CLEARANCE] Store clearance cookies failed: %s', e)
        return result

    def _postprocess_captcha(self, result: dict[str, Any] | None, url: str) -> dict[str, Any] | None:
        """F360-R: Phase 5 - CAPTCHA detection for small images."""
        if self._captcha_detector is not None and result and result.get('content'):
            ct = result.get('content_type', '')
            content_bytes = result['content']
            if ct.startswith('image/') and len(content_bytes) < 200 * 1024:
                url_for_check = result.get('final_url') or result.get('url') or url
                try:
                    if self._captcha_detector.is_captcha(content_bytes, url_for_check):
                        logger.debug('[CAPTCHA] CAPTCHA detected at %s, skipping', url_for_check)
                        self._captcha_detections += 1
                        return None
                except Exception as e:  # noqa: BLE001
                    # NEW-H6 fix: Log CAPTCHA detection failure instead of silent pass
                    logger.debug('[CAPTCHA] Detection failed: %s', e)
        return result

    async def _maybe_deep_research(self, query: str, limit: int=10) -> list[dict[str, Any]] | None:
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
        if os.environ.get('GHOST_DEEP_RESEARCH') != '1':
            return None
        try:
            from ..tools.ddgs_client import search_news_sync, search_text_sync
            from ..tools.deep_research_sources import urlscan_search, wayback_cdx_lookup
            from ..tools.search_fusion import top_k
            # P4-5 FIX: policy="log" returns list[T] (only successes), not ParallelResult.
            # Use result directly - it already contains only non-exception values.
            deep_results = await parallel(
                [asyncio.to_thread(search_text_sync, query), asyncio.to_thread(search_news_sync, query), wayback_cdx_lookup(query, limit=8), urlscan_search(query, size=8)],
                concurrency=4,
                policy="log",
                ctx="fetch_coordinator.deep_research",
            )
            # deep_results is list[Any] - unpack by position
            # Each position may be None if that particular search source failed
            ddgs_rows = deep_results[0] if len(deep_results) > 0 else None
            news_rows = deep_results[1] if len(deep_results) > 1 else None
            wayback_rows = deep_results[2] if len(deep_results) > 2 else None
            urlscan_rows = deep_results[3] if len(deep_results) > 3 else None
            rows: list[dict[str, Any]] = []
            for part, label in [(ddgs_rows, 'ddgs'), (news_rows, 'news'), (wayback_rows, 'wayback'), (urlscan_rows, 'urlscan')]:
                if isinstance(part, list):
                    rows.extend(part)
                elif isinstance(part, Exception):
                    _ev0 = type(part).__name__
                    logger.debug('[DEEP] %s failed: %s: %s', label, type(part).__name__, part)
            if not rows:
                return None
            fused = top_k(rows, k=limit)
            logger.info('[DEEP] query=%r → %s raw rows → %s fused', query, len(rows), len(fused))
            return fused
        except Exception as e:  # noqa: BLE001 — best-effort; httpx close failure; non-critical
            logger.debug('[DEEP] research failed: %s', e)
            return None

    async def _do_shutdown(self, ctx: dict[str, Any]) -> None:
        """
        Cleanup on shutdown with proper drain.

        Sprint 4B: Adds small drain delay after closing sessions to allow
        SSL/TCP to finish gracefully.
        """
        logger.info(f"FetchCoordinator shutting down: {self._urls_fetched_count} URLs fetched | AIMD window={self._aimd_concurrency:.1f} | successes={self._telemetry['total_successes']} | failures={self._telemetry['total_failures']}")
        self._frontier.clear()
        self._processed_urls = _create_dedup_strategy()
        self._cover_count = 0
        # META-001: Clear CrossSprintGate TTL cache on shutdown
        try:
            if self._cross_sprint_gate is not None:
                self._cross_sprint_gate.reset()
        except Exception:  # noqa: BLE001 — best-effort; gate reset is cleanup only
            pass
        # [META]-014: Clear EntityConfirmationService cache on shutdown
        try:
            if self._entity_confirmation_service is not None:
                await self._entity_confirmation_service.invalidate_all()
        except Exception:  # noqa: BLE001 — best-effort; confirmation reset is cleanup only
            pass
        # UNIFIED-003: Stop consumer/worker loops gracefully before cancelling tasks.
        # Sets _running=False so loops exit their while conditions naturally,
        # then cancel for immediate teardown. Prevents CancelledError noise.
        self._running = False
        # UNIFIED-004: Cancel entropy bridge consumer task
        if self._entropy_bridge_task is not None:
            self._entropy_bridge_task.cancel()
            try:
                await self._entropy_bridge_task
            except asyncio.CancelledError:
                # P0-3 FIX: Re-raise CancelledError to honour cancellation propagation.
                # Blanket suppress() prevents proper task cancellation chain.
                raise
            self._entropy_bridge_task = None
        # UNIFIED-004: Cancel micro-sprint worker task
        if self._micro_sprint_worker_task is not None:
            self._micro_sprint_worker_task.cancel()
            try:
                await self._micro_sprint_worker_task
            except asyncio.CancelledError:
                # P0-3 FIX: Re-raise CancelledError to honour cancellation propagation.
                raise
            self._micro_sprint_worker_task = None
        # SILICON-07: Stop SwarmDAG rebalancer loop and DAG workers
        if self._swarm_dag_rebalance_task is not None:
            self._swarm_dag_rebalance_task.cancel()
            try:
                await self._swarm_dag_rebalance_task
            except asyncio.CancelledError:
                # P0-3 FIX: Re-raise CancelledError to honour cancellation propagation.
                raise
            self._swarm_dag_rebalance_task = None
        if self._swarm_dag is not None:
            with contextlib.suppress(Exception):
                self._swarm_dag.stop()
            self._swarm_dag = None
        await self._stop_checkpoint_loop()
        if self._session_manager is not None:
            with contextlib.suppress(Exception):
                await self._session_manager.close()
            self._session_manager = None
        # F-05: cleanup RobotsParser async session
        if self._robots_parser is not None:
            with contextlib.suppress(Exception):
                await self._robots_parser.__aexit__(None, None, None)
            self._robots_parser = None
        if self._session_lmdb_env is not None:
            with contextlib.suppress(Exception):
                self._session_lmdb_env.close()
            self._session_lmdb_env = None
        from ..transport.darknet_session_provider import close_all as _close_darknet_sessions
        await _close_darknet_sessions()
        if self._lightpanda_pool is not None:
            with contextlib.suppress(Exception):
                await self._lightpanda_pool.close()
            self._lightpanda_pool = None
        await asyncio.sleep(0.25)

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
            if _JITTER_RNG.random() < _COVER_RATE:
                cover_urls = _ZERO_ATTR_ENGINE.generate_cover_traffic_urls(n_decoys=1, transport=transport)
                if not cover_urls:
                    return
                cover_url = cover_urls[0]
                self._cover_count += 1
                delay = _JITTER_RNG.uniform(0.5, 3.0)
                safe_create_task(self._fire_cover_traffic_url(cover_url, delay, transport))
                from metrics_registry import get_metrics_registry
                get_metrics_registry().inc('cover_traffic_fired')
                logger.debug('[COVER] fired cover traffic #%s for transport=%s', self._cover_count, transport)
        except* Exception as exc_group:  # noqa: BLE001 — best-effort; cover traffic outer TaskGroup failure; non-critical
            # Log actual exception(s) for debugging; DO NOT swallow silently
            for exc in exc_group.exceptions:
                logger.debug('[COVER] TaskGroup failed: %s (%s)', type(exc).__name__, exc)
            # P0-3 SSRF fix: Never catch BaseException — let CancelledError propagate for proper shutdown
            # Note: CancelledError is NOT BaseException in Python 3.8+ but explicit handling is safer

    async def _fire_cover_traffic_url(self, url: str, delay: float, transport: str) -> None:
        """Fire a single cover traffic URL via the appropriate transport layer.

        Circuit breaker: skip if domain is blocked.
        Transport-aware: Tor→Tor SOCKS, I2P→I2P, clearnet→curl_cffi.
        Cover traffic is best-effort — never propagates exceptions.
        """
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            # P0-3 FIX: Re-raise CancelledError to honour cancellation.
            # Cover traffic is non-critical, so proper cancellation takes priority.
            raise
        except TimeoutError:
            # TimeoutError: skip the cover traffic delay (acceptable: slow → skip this cover)
            return
        try:
            _ = _fast_url_host(url)
        except (ValueError, TypeError):  # noqa: BLE001 — best-effort; httpx.URL parse failure; non-critical
            return
        transport_lower = transport.lower()
        if transport_lower == 'tor':
            await self._cover_tor(url)
        elif transport_lower == 'i2p':
            await self._cover_i2p(url)
        else:
            await self._cover_clearnet(url)

    async def _cover_tor(self, url: str) -> None:
        """Cover traffic via Tor transport."""
        try:
            try:
                from ..transport.base import TransportConfig
                from ..transport.tor_transport import get_tor_transport
                tor = get_tor_transport()
                if tor and await tor.is_running():
                    config = TransportConfig(url=url, method='GET', headers=None, body=None, timeout=10.0)
                    await tor.fetch(config)
            except* Exception:
                # Best-effort; Tor transport fetch failure; fire-and-forget cover traffic
                pass
        except asyncio.CancelledError:
            # P1-8 FIX: Use plain 'except' to catch CancelledError nested in ExceptionGroup
            # (e.g., from TaskGroup cancellation). except* cannot catch nested exceptions.
            raise

    async def _cover_i2p(self, url: str) -> None:
        """Cover traffic via I2P transport."""
        try:
            try:
                from ..transport.base import TransportConfig
                from ..transport.i2p_transport import get_i2p_transport
                i2p = get_i2p_transport()
                if i2p and i2p.is_running():
                    config = TransportConfig(url=url, method='GET', headers=None, body=None, timeout=10.0)
                    await i2p.fetch(config)
            except* Exception:
                # Best-effort; I2P transport fetch failure; fire-and-forget cover traffic
                pass
        except asyncio.CancelledError:
            # P1-8 FIX: Use plain 'except' to catch CancelledError nested in ExceptionGroup
            # (e.g., from TaskGroup cancellation). except* cannot catch nested exceptions.
            raise

    async def _cover_clearnet(self, url: str) -> None:
        """Cover traffic via clearnet (curl_cffi)."""
        try:
            try:
                from hledac.universal.transport.curl_cffi_fetch import async_get_curl_cffi_session_for_host
                ok, session, used_profile, host = await async_get_curl_cffi_session_for_host(url, profile='chrome131')
                if ok and session is not None:
                    await session.get(url, timeout=10.0)
            except* Exception:
                # Best-effort; curl_cffi fetch failure; fire-and-forget cover traffic
                pass
        except asyncio.CancelledError:
            # P1-8 FIX: Use plain 'except' to catch CancelledError nested in ExceptionGroup
            # (e.g., from TaskGroup cancellation). except* cannot catch nested exceptions.
            raise

    async def _fire_cover_traffic(self, url: str, delay: float, transport: str) -> None:
        """Legacy wrapper — redirect to transport-aware implementation."""
        await self._fire_cover_traffic_url(url, delay, transport)


    def _put_task(self, task: object, pivot_queue: asyncio.Queue, pivot_stats: dict | None) -> None:
        """Non-blocking put via call_later. Used by enqueue_pivot stagger."""
        try:
            pivot_queue.put_nowait(task)
            if pivot_stats is not None:
                pivot_stats['total'] = pivot_stats.get('total', 0) + 1
        except asyncio.QueueFull:  # noqa: BLE001
            pass


    def enqueue_pivot(self, ioc_value: str, ioc_type: str, confidence: float, degree: float=1.0, task_type: str | None=None) -> None:
        """Enqueue a pivot task. Silently drops if queue is full (M1 8GB).

        Sprint 8VI §B.4: RL-adaptive priority — for generic_pivot task
        types, blend EMA reward with base priority.

        Sprint F-EXTRACT-2: moved from SprintScheduler. State
        (`_pivot_queue`, `_pivot_stats`) and helper (`_get_adaptive_priority`)
        accessed via provider callbacks.

        [FINAL]-019: Cross-Lane Temporal Correlation Footprint.
        When HLEDAC_PIVOT_STAGGER_MS > 0 (default 500ms), tasks of the same
        IoC type are staggered with Gaussian jitter before enqueueing. This
        breaks the zero-interval burst fingerprint that SIEM correlation rules
        use to detect automated OSINT tooling. The stagger is per-task-type, so
        e.g. ipv4→[ip_to_ct, ip_to_greynoise, shodan_enrich] are each delayed
        by a decorrelated offset before hitting the pivot queue.
        """
        from hledac.universal.runtime.pivot_types import PivotTask
        import random as _rng
        import time as _time

        pivot_queue = self._pivot_queue_provider()
        if pivot_queue is None:
            return
        if pivot_queue.full():
            return
        if task_type is not None:
            task_types_list: list[str] = [task_type]
        else:
            task_types_list = {'cve': ['cve_to_github', 'cve_to_academic'], 'ipv4': ['ip_to_ct', 'ip_to_greynoise', 'shodan_enrich'], 'ipv6': ['ip_to_ct'], 'domain': ['domain_to_dns', 'domain_to_wayback', 'domain_to_pdns', 'domain_to_ct', 'ahmia_search', 'rdap_lookup'], 'md5': ['hash_to_mb'], 'sha256': ['hash_to_mb'], 'sha1': ['hash_to_mb'], 'url': ['wayback_search', 'commoncrawl_search', 'paste_keyword_search', 'github_dork', 'multi_engine_search'], 'hypothesis': ['multi_engine_search', 'rdap_lookup']}.get(ioc_type, [])
        if not task_types_list:
            return
        base_priority = confidence * max(1.0, float(degree))
        pivot_stats = self._pivot_stats_provider()

        # [FINAL]-019: temporal correlation decorrelation
        _pivot_stagger_ms = FeatureFlags.get_int(FeatureFlag.PIVOT_STAGGER_MS, 500)

        for idx, tt in enumerate(task_types_list):
            effective = self._adaptive_priority_provider(tt, base_priority)
            priority = -effective
            task = PivotTask(priority, ioc_type, ioc_value, tt)

            # [FINAL]-019: stagger only between distinct task types,
            # not after the last one. Gaussian jitter decorrelates bursts.
            if idx < len(task_types_list) - 1 and _pivot_stagger_ms > 0:
                # Gaussian with sigma = stagger_ms/3 gives ~99.7% within ±stagger_ms
                jitter_s = abs(_rng.gauss(0.0, _pivot_stagger_ms / 3000.0))
                # [FINAL]-019-02: use call_later instead of time.sleep().
                # time.sleep() blocks the event-loop thread — all async workers
                # are frozen for jitter_s × N calls (~1-2s total stagger).
                # call_later() schedules a callback and immediately returns;
                # the event loop stays responsive during the delay.
                try:
                    _loop = asyncio.get_running_loop()
                    _loop.call_later(jitter_s, self._put_task, task, pivot_queue, pivot_stats)
                    continue  # call_later handles placement; skip immediate put
                except RuntimeError:  # noqa: BLE001
                    pass  # no running loop → fall through to immediate put
            # Immediate put: last item, or fallback when no loop available
            try:
                pivot_queue.put_nowait(task)
                if pivot_stats is not None:
                    pivot_stats['total'] = pivot_stats.get('total', 0) + 1
            except asyncio.QueueFull:  # noqa: BLE001
                pass

    async def drain_pivot_queue(self, max_tasks: int = 5) -> int:
        """Drain pivot queue, return number processed.

        PivotProtocol §A.2: Async drain for cooperative scheduling.
        On M1 8GB, this is a no-op stub — pivot queue is consumed
        by the micro-sprint worker inside this coordinator.
        Returns 0 as queue drain is internal.

        Args:
            max_tasks: Maximum number of tasks to drain (ignored in stub).

        Returns:
            Number of tasks processed (0 for this stub implementation).
        """
        # Pivot queue is consumed by micro-sprint workers internally.
        # This stub satisfies PivotProtocol for type checking.
        return 0

    async def record_feedback(
        self,
        pivot_type: str,
        ioc_type: str,
        succeeded: bool,
    ) -> None:
        """Record pivot execution feedback for adaptation.

        PivotProtocol §A.3: Feedback loop integration with F203G pattern.

        F203G PATTERN INTEGRATION:
        ─────────────────────────
        This method bridges PivotProtocol.record_feedback() to the
        DuckDB-backed HypothesisFeedbackAdapter for RL-adaptive pivot scoring.

        Architecture (F203G):
            PivotProtocol.record_feedback()     ← this method
                    ↓
            HypothesisFeedbackAdapter.async_record()
                    ↓
            DuckDBShadowStore.async_record_hypothesis_feedback()
                    ↓
            PivotPlanner uses summary to penalize low-yield pivot types

        On M1 8GB with no DuckDB store available:
            - Method is a safe no-op stub (returns silently)
            - No exception raised to avoid breaking the sprint

        Args:
            pivot_type: Type of pivot that was executed (domain/identity/leak/archive/graph).
            ioc_type: The IOC type operated on (ipv4/domain/md5/etc).
            succeeded: Whether the pivot produced accepted findings.
        """
        # F203G: Try to record via DuckDB-backed HypothesisFeedbackAdapter
        try:
            from ..runtime.hypothesis_feedback import HypothesisFeedbackAdapter
            from ..knowledge.duckdb_store import DuckDBShadowStore

            # Get DuckDB store - prefer orchestrator's store if available
            duckdb_store: DuckDBShadowStore | None = None
            if self._orchestrator is not None:
                duckdb_store = getattr(self._orchestrator, '_duckdb_store', None)
            if duckdb_store is None:
                # Fallback: try global singleton
                try:
                    from ..knowledge.db import get_duckdb_store
                    duckdb_store = get_duckdb_store()
                except Exception:  # noqa: BLE001
                    duckdb_store = None

            if duckdb_store is None:
                # M1 8GB safe: no DuckDB available, skip silently
                return

            # Convert succeeded bool to count-based feedback (F203G expects counts)
            produced = 1
            accepted = 1 if succeeded else 0
            signal = 1.0 if succeeded else 0.0

            # Get target_id from orchestrator if available
            target_id = getattr(self._orchestrator, '_target_id', 'fetch_coordinator') if self._orchestrator else 'fetch_coordinator'

            # Record via HypothesisFeedbackAdapter
            adapter = HypothesisFeedbackAdapter(duckdb_store, target_id)
            await adapter.async_record(
                pivot_type=pivot_type,
                ioc_type=ioc_type,
                produced_count=produced,
                accepted_count=accepted,
                signal_value=signal,
            )
        except Exception:  # noqa: BLE001 — F203G feedback is best-effort; never break sprint
            # M1 8GB safe: feedback recording failure is non-critical
            pass

    def _enqueue_hypothesis_pivot(self, ioc_value: str, ioc_type: str, confidence: float, depth: int, degree: float=1.0) -> bool:
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
            logger.debug(f'[F193B] Hypothesis pivot dropped: depth {depth} > max {config.max_hypothesis_depth}')
            return False
        if config is not None and self._hypothesis_query_count_provider() >= config.max_hypothesis_queries:
            logger.debug(f'[F193B] Hypothesis pivot dropped: query count {self._hypothesis_query_count_provider()} >= max {config.max_hypothesis_queries}')
            return False
        self._enqueue_pivot_provider(ioc_value=ioc_value, ioc_type=ioc_type, confidence=confidence, degree=float(depth), task_type=None)
        self._hypothesis_query_count_setter(self._hypothesis_query_count_provider() + 1)
        self._hypothesis_depth_setter(max(self._hypothesis_depth_provider(), depth))
        logger.debug(f'[F193B] Hypothesis pivot enqueued: {ioc_value} (depth={depth}, total_queries={self._hypothesis_query_count_provider()})')
        return True

    async def _session_checkpoint_loop(self, interval_s: float=30.0) -> None:
        """Periodically sync session LMDB to guarantee bounded data loss window.

        With sync=True on session LMDB (critical=True), each write is durable.
        This loop provides an additional guarantee: even if writes are batched
        by the OS, max data loss is bounded by interval_s.

        Runs only while _running is True. Cancelled automatically on shutdown.
        """
        import logging as _logging
        _logger = _logging.getLogger('hledac.fetch.checkpoint')
        while self._running:
            await asyncio.sleep(interval_s)
            if not self._running:
                break
            env = self._session_lmdb_env
            if env is not None:
                try:
                    env.sync()
                    _logger.debug('[Issue#20] session LMDB checkpoint synced')
                except Exception:  # noqa: BLE001 — best-effort; LMDB env.sync() failure; checkpoint best-effort
                    pass

    def _start_checkpoint_loop(self) -> None:
        """Start the session checkpoint background task. Idempotent."""
        if self._session_checkpoint_task is None and self._session_lmdb_env is not None:
            self._running = True
            self._session_checkpoint_task = safe_create_task(self._session_checkpoint_loop(), name='session-lmdb-checkpoint')

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
                # P0-3 FIX: Re-raise CancelledError to honour cancellation propagation.
                raise

    # ─────────────────────────────────────────────────────────────────────────
    # [META]-015: Micro-Sprint Contradiction Detection
    # Detects contradictions between original findings and micro-sprint re-fetch results
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_original_findings_for_entity(self, entity_id: str) -> tuple[list[dict[str, Any]], float]:
        """
        Fetch original findings for an entity from DuckDB before micro-sprint re-fetch.

        [META]-015: Critical step — stores original findings so they can be compared
        against micro-sprint results. If contradiction is detected, the original
        source is flagged for retraction.

        Args:
            entity_id: The entity to look up (URL, domain, IP, hash)

        Returns:
            Tuple of (findings list, original entropy). Findings are dicts with
            source, content, confidence, ts fields. Returns ([], 0.0) on failure.
        """
        try:
            # Get orchestrator and duckdb store
            orchestrator = self._orchestrator
            if orchestrator is None:
                logger.debug('[META-015] No orchestrator, skipping original findings fetch')
                return [], 0.0

            duckdb_store = getattr(orchestrator, '_duckdb_store', None)
            if duckdb_store is None:
                logger.debug('[META-015] No DuckDB store, skipping original findings fetch')
                return [], 0.0

            # Check cache first (TTL 5 min)
            if entity_id in self._micro_sprint_original_findings:
                cached_findings, cached_at = self._micro_sprint_original_findings[entity_id]
                if time.monotonic() - cached_at < 300.0:  # 5 min TTL
                    # Compute entropy from cached findings content
                    contents = [f['content'] for f in cached_findings if f.get('content')]
                    entropy = self._compute_simple_entropy(contents)
                    return cached_findings, entropy
                else:
                    # Cache expired, remove stale entry
                    del self._micro_sprint_original_findings[entity_id]

            # Fetch from DuckDB using async path
            # Use LIKE query on payload_text to find entity-related findings
            rows = await duckdb_store.async_query_findings_by_text(
                like_pattern=f'%{entity_id}%',
                limit=50,
            )

            findings = []
            for row in rows:
                if isinstance(row, dict):
                    # Extract relevant fields
                    content = row.get('payload_text', '') or row.get('content', '') or ''
                    if not content:
                        continue

                    # Only include if entity_id is actually mentioned
                    if entity_id.lower() in content.lower():
                        findings.append({
                            'finding_id': row.get('finding_id', row.get('id', '')),
                            'source': row.get('source_type', 'unknown'),
                            'content': content,
                            'confidence': float(row.get('confidence', 0.0)),
                            'ts': float(row.get('ts', 0.0)),
                        })

            # Cache findings with timestamp
            self._micro_sprint_original_findings[entity_id] = (findings, time.monotonic())

            # Prune cache if too large (M1 8GB safe: max 256 entries)
            if len(self._micro_sprint_original_findings) > 256:
                # Remove oldest entries (by timestamp)
                oldest_keys = sorted(
                    self._micro_sprint_original_findings.keys(),
                    key=lambda k: self._micro_sprint_original_findings[k][1]
                )[:32]  # Remove 32 oldest
                for key in oldest_keys:
                    del self._micro_sprint_original_findings[key]

            # Compute entropy from findings content
            contents = [f['content'] for f in findings if f.get('content')]
            entropy = self._compute_simple_entropy(contents)

            logger.debug(
                '[META-015] Fetched %d original findings for %s (entropy=%.3f)',
                len(findings), entity_id, entropy,
            )
            return findings, entropy

        except Exception as e:
            logger.debug('[META-015] Failed to fetch original findings for %s: %s', entity_id, e)
            return [], 0.0

    def _detect_micro_sprint_contradictions(
        self,
        original_findings: list[dict[str, Any]],
        micro_sprint_evidence_ids: list[str],
    ) -> list[dict[str, Any]]:
        """
        Detect contradictions between original findings and micro-sprint results.

        [META]-015: Core fix — compares original findings against micro-sprint
        re-fetch. Uses simple content comparison to detect factual contradictions.

        Contradiction types detected:
        - Factual: same entity has conflicting claims (e.g., different IPs for same domain)
        - Confidence: new findings have significantly different confidence scores
        - Source conflict: multiple sources disagree on same attribute

        Args:
            original_findings: List of original finding dicts (from DuckDB)
            micro_sprint_evidence_ids: Evidence IDs from micro-sprint re-fetch

        Returns:
            List of contradiction dicts with severity, reason, conflicting_sources
        """
        if not original_findings or not micro_sprint_evidence_ids:
            return []

        try:
            ms_sources, ms_values = self._parse_micro_sprint_ids(micro_sprint_evidence_ids)
            contradictions = self._detect_confidence_contradictions(original_findings, ms_sources, ms_values)
            contradictions.extend(self._detect_protocol_conflicts(original_findings, ms_sources, ms_values))
            unique = self._deduplicate_contradictions(contradictions)
            logger.debug('[META-015] Detected %d contradictions', len(unique))
            return unique[:10]
        except Exception as e:
            logger.debug('[META-015] Contradiction detection failed: %s', e)
            return []

    def _parse_micro_sprint_ids(self, evidence_ids: list[str]) -> tuple[set[str], dict[str, set[str]]]:
        """Parse micro-sprint evidence IDs into sources and values."""
        sources: set[str] = set()
        values: dict[str, set[str]] = {}
        for eid in evidence_ids:
            parts = eid.split(':', 2)
            if len(parts) >= 2:
                protocol = parts[0]
                sources.add(protocol)
                if len(parts) >= 3:
                    value = parts[2]
                    if value not in values.get(protocol, set()):
                        values.setdefault(protocol, set()).add(value)
        return sources, values

    def _detect_confidence_contradictions(
        self,
        findings: list[dict[str, Any]],
        ms_sources: set[str],
        ms_values: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        """Detect contradictions based on confidence and content differences."""
        contradictions: list[dict[str, Any]] = []
        ms_content_hints = {v for vals in ms_values.values() for v in vals}

        for finding in findings:
            confidence = finding.get('confidence', 0.5)
            if confidence <= 0.7 or not ms_sources:
                continue

            content = finding.get('content', '')[:200].lower()
            source = finding.get('source', 'unknown')
            finding_id = finding.get('finding_id', '')

            for hint in ms_content_hints:
                if hint and hint != finding_id and hint.lower() not in content:
                    contradictions.append({
                        'severity': 0.8,
                        'reason': 'micro_sprint_contradiction',
                        'original_source': source,
                        'micro_sprint_sources': list(ms_sources),
                        'description': f'Micro-sprint found new value "{hint[:50]}" not in original finding',
                        'entity_id': '',
                    })
        return contradictions

    def _detect_protocol_conflicts(
        self,
        findings: list[dict[str, Any]],
        ms_sources: set[str],
        ms_values: dict[str, set[str]],
    ) -> list[dict[str, Any]]:
        """Detect protocol-level conflicts between sources."""
        contradictions: list[dict[str, Any]] = []
        if len(ms_sources) <= 1:
            return contradictions

        protocol_values_flat = {v for vals in ms_values.values() for v in vals}
        if len(protocol_values_flat) <= 1:
            return contradictions

        for finding in findings:
            confidence = finding.get('confidence', 0.5)
            if confidence > 0.6:
                contradictions.append({
                    'severity': 0.7,
                    'reason': 'protocol_conflict',
                    'original_source': finding.get('source', ''),
                    'micro_sprint_sources': list(ms_sources),
                    'description': f'Protocol conflict: {finding.get("source", "")} vs {list(ms_sources)}',
                    'entity_id': '',
                })
        return contradictions

    def _deduplicate_contradictions(self, contradictions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate contradictions by description."""
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for c in contradictions:
            desc = c.get('description', '')
            if desc not in seen:
                seen.add(desc)
                unique.append(c)
        return unique

    def _compute_simple_entropy(self, contents: list[str]) -> float:
        """
        Compute simple entropy from text content list.

        Uses Shannon entropy on character distribution.
        Returns 0.0-1.0 normalized score.

        Args:
            contents: List of text strings to analyze

        Returns:
            Average normalized entropy (0.0-1.0)
        """
        if not contents:
            return 0.0

        try:
            import math as _math
            from collections import Counter

            total_entropy = 0.0
            count = 0

            for text in contents:
                if not text or len(text) < 2:
                    continue

                # Use character distribution for entropy
                char_counts = Counter(text.lower())
                total_chars = len(text)

                entropy = 0.0
                for cnt in char_counts.values():
                    p = cnt / total_chars
                    if p > 0:
                        entropy -= p * _math.log2(p)

                # Normalize: max entropy for text is ~4.5 bits (English)
                normalized = min(entropy / 4.5, 1.0)
                total_entropy += normalized
                count += 1

            return total_entropy / count if count > 0 else 0.0

        except Exception:
            return 0.0

    def _extract_claims_from_content(self, content: str) -> dict[str, str]:
        """
        Extract IOC claims from finding content.

        Returns dict of claim_type -> extracted_value.
        Simple heuristic extraction for common IOC types.
        """
        claims: dict[str, str] = {}
        content_lower = content.lower()

        # Extract IP addresses
        ips = _IP_PATTERN.findall(content)
        if ips:
            claims['ip'] = ips[0]

        # Extract domain names (simple heuristic)
        domains = _DOMAIN_PATTERN.findall(content_lower)
        if domains:
            claims['domain'] = domains[0]

        # Extract SHA256 hashes
        sha256s = _SHA256_PATTERN.findall(content)
        if sha256s:
            claims['sha256'] = sha256s[0]

        # Extract URLs
        urls = _URL_PATTERN.findall(content)
        if urls:
            claims['url'] = urls[0][:100]  # Truncate for comparison

        return claims

    async def _emit_contradiction_alert(
        self,
        entity_id: str,
        contradictions: list[dict[str, Any]],
        original_entropy: float,
    ) -> None:
        """
        Emit EntropyAlert for detected micro-sprint contradictions.

        [META]-015: Bridges contradiction detection back to EntropyFetchBridge
        for downstream handling (JTMS retraction, source blacklisting).

        Args:
            entity_id: The entity that had contradictions
            contradictions: List of contradiction dicts from _detect_micro_sprint_contradictions
            original_entropy: Entropy of original findings
        """
        if not contradictions:
            return

        try:
            # Get EntropyFetchBridge
            from ..brain.uncertainty_quant import get_entropy_bridge, EntropyAlert

            bridge = get_entropy_bridge()
            if bridge is None:
                logger.debug('[META-015] No EntropyFetchBridge available')
                return

            # Create metadata for contradiction alert
            metadata = {
                'reason': 'micro_sprint_contradiction',
                'contradiction_count': len(contradictions),
                'max_severity': max(c.get('severity', 0.0) for c in contradictions),
                'contradictions': [
                    {
                        'severity': c.get('severity', 0.0),
                        'reason': c.get('reason', ''),
                        'original_source': c.get('original_source', ''),
                        'micro_sprint_sources': c.get('micro_sprint_sources', []),
                        'description': c.get('description', ''),
                    }
                    for c in contradictions
                ],
                'original_entropy': original_entropy,
                'alert_type': 'micro_sprint_contradiction',
            }

            # Find most severe source to blame
            most_severe = max(contradictions, key=attrgetter("get")('severity', 0.0))
            contradiction_source = most_severe.get('original_source', None)

            # Create and emit alert with high severity
            max_severity = max(c.get('severity', 0.0) for c in contradictions)

            # ISSUE-022-03 FIX (secondary): Use 'critical' risk_level for contradictions.
            # This matches META-008 comment: contradictions are highest priority.
            # [META-008]: contradictions should use 'critical' risk_level.
            alert = EntropyAlert(
                entity_id=entity_id,
                entropy=min(max_severity * 2.0, 1.0),  # Cap at 1.0 to stay in valid range
                threshold_exceeded=1.5,
                # ISSUE-022-03 FIX (secondary): Low confidence in original finding
                # means we distrust the conflicting source. This drives re-fetch
                # via the entropy feedback loop.
                confidence=1.0 - max_severity,
                risk_level='critical',  # [META-008] Contradictions = highest priority
                timestamp=time.time(),
                metadata=metadata,
                contradiction_source_id=contradiction_source,
            )

            await bridge.emit(alert)

            logger.info(
                '[META-015] Emitted contradiction alert for %s: '
                '%d contradictions, max_severity=%.2f, source=%s',
                entity_id, len(contradictions), max_severity, contradiction_source,
            )

        except Exception as e:
            logger.debug('[META-015] Failed to emit contradiction alert: %s', e)

    # ─────────────────────────────────────────────────────────────────────────
    # UNIFIED-004: Micro-Sprint API — Entropy Feedback Loop
    # ─────────────────────────────────────────────────────────────────────────

    async def trigger_micro_sprint(
        self,
        entity_id: str,
        entropy: float,
        alternative_protocols: list[str] | None = None,
        max_hops: int = 2,
        timeout: float = 30.0,
        reason: str | None = None,
    ) -> Any:
        """
        Lightweight targeted re-fetch for high-entropy entities.

        UNIFIED-004: Closes the entropy feedback loop by attempting to improve
        low-entropy findings via alternative discovery protocols and bounded
        graph traversal.

        Design constraints (M1 8GB):
        - Single entity focus (no batch overhead)
        - Bounded timeout (30s max) prevents sprint starvation
        - Limited hops (1-2) bounds graph traversal cost
        - Protocol diversity: tries alternative discovery paths

        Args:
            entity_id: Target entity (URL, domain, IP, hash)
            entropy: Current entropy score (0.0-1.0) that triggered re-fetch
            alternative_protocols: Discovery protocols to try (e.g., ["ct", "passive_dns"])
            max_hops: Graph traversal depth (1-2, default 2)
            timeout: Hard timeout in seconds (default 30.0, max 30.0)
            reason: Human-readable reason for re-fetch (optional)

        Returns:
            MicroSprintResult with success status, new entropy, and evidence IDs
        """
        from ..project_types import MicroSprintPlan, MicroSprintResult

        # Build plan with validation
        plan = MicroSprintPlan.create(
            entity_id=entity_id,
            entropy=entropy,
            protocols=alternative_protocols or [],
            max_hops=max_hops,
            timeout=timeout,
            reason=reason,
        )

        start_time = time.monotonic()
        protocols_tried: list[str] = []
        evidence_ids: list[str] = []
        best_entropy = 0.0
        hops_explored = 0

        try:
            # Execute micro-sprint with timeout
            async with asyncio.timeout(plan.timeout):
                # Try each protocol in order
                for protocol in plan.protocols:
                    if time.monotonic() - start_time >= plan.timeout:
                        break

                    protocols_tried.append(protocol)
                    protocol_evidence = await self._execute_micro_sprint_protocol(
                        plan.entity_id, protocol, plan.max_hops
                    )

                    if protocol_evidence:
                        evidence_ids.extend(protocol_evidence)
                        hops_explored += 1

                        # Compute entropy of new evidence
                        new_entropy = await self._compute_evidence_entropy(protocol_evidence)
                        if new_entropy > best_entropy:
                            best_entropy = new_entropy

                        # Early exit if we've achieved good entropy
                        if best_entropy >= 0.5:
                            break

                duration_ms = (time.monotonic() - start_time) * 1000.0
                # UNIFIED-004: Use fixed byte-level entropy threshold for success.
                # best_entropy is normalized Shannon entropy (0.0-1.0) from
                # calculate_entropy(). plan.entropy is now also 0.0-1.0
                # (implied_confidence-based). Compare same-scale values.
                _MIN_USEFUL_BYTE_ENTROPY = 0.35
                success = (
                    best_entropy >= _MIN_USEFUL_BYTE_ENTROPY
                    and best_entropy > plan.entropy
                )

                return MicroSprintResult(
                    entity_id=plan.entity_id,
                    success=success,
                    new_entropy=best_entropy,
                    protocols_tried=tuple(protocols_tried),
                    evidence_ids=tuple(evidence_ids),
                    duration_ms=duration_ms,
                    hops_explored=hops_explored,
                )

        except TimeoutError:
            duration_ms = (time.monotonic() - start_time) * 1000.0
            return MicroSprintResult(
                entity_id=plan.entity_id,
                success=False,
                new_entropy=0.0,
                protocols_tried=tuple(protocols_tried),
                evidence_ids=tuple(evidence_ids),
                duration_ms=duration_ms,
                error='micro_sprint_timeout',
                hops_explored=hops_explored,
            )
        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000.0
            logger.warning('[UNIFIED-004] Micro-sprint failed for %s: %s', entity_id, e)
            return MicroSprintResult(
                entity_id=plan.entity_id,
                success=False,
                new_entropy=0.0,
                protocols_tried=tuple(protocols_tried),
                evidence_ids=tuple(evidence_ids),
                duration_ms=duration_ms,
                error=str(e),
                hops_explored=hops_explored,
            )

    # ---------------------------------------------------------------------------
    # ---------------------------------------------------------------------------
    # ---------------------------------------------------------------------------
    async def _execute_micro_sprint_protocol(
        self,
        entity_id: str,
        protocol: str,
        max_hops: int,
    ) -> list[str]:
        """
        Execute a single protocol for micro-sprint.
        """
        evidence_ids: list[str] = []
        try:
            handler_name = _PROTOCOL_HANDLERS.get(protocol)
            if handler_name:
                handler = getattr(self, handler_name, None)
                if handler:
                    evidence_ids = await handler(entity_id)
            else:
                logger.debug('[UNIFIED-004] Unknown micro-sprint protocol: %s', protocol)
        except Exception as e:
            logger.debug('[UNIFIED-004] Protocol %s failed for %s: %s', protocol, entity_id, e)
        return evidence_ids


    async def _compute_evidence_entropy(self, evidence_ids: list[str]) -> float:
        """
        Compute entropy of evidence created during micro-sprint.

        Uses canonical calculate_entropy() from brain/uncertainty_quant.py
        which falls back to Rust NEON-accelerated compute on M1.

        Args:
            evidence_ids: List of evidence IDs to evaluate

        Returns:
            Normalized entropy score (0.0-1.0)
        """
        if not evidence_ids:
            return 0.0

        try:
            from hledac.universal.brain.uncertainty_quant import calculate_entropy

            # Attempt to read evidence and compute entropy
            if self._evidence_sink is not None:
                # Use injected evidence sink
                total_entropy = 0.0
                count = 0
                for evidence_id in evidence_ids[:5]:  # Limit to 5 for perf
                    try:
                        evidence = await self._evidence_sink.get_evidence(
                            evidence_id,
                        )
                        if evidence and hasattr(evidence, 'payload_text'):
                            text = evidence.payload_text or ''
                            if text:
                                entropy = calculate_entropy(text)
                                total_entropy += entropy
                                count += 1
                    except Exception:
                        continue

                if count > 0:
                    return total_entropy / count

        except Exception as e:
            logger.debug('[UNIFIED-004] Entropy computation failed: %s', e)

        return 0.0


    async def _entropy_alert_consumer_loop(self) -> None:
        """
        F360-R: Refactored entropy alert consumer with phase-based approach.

        Phases:
        1. Queue initialization
        2. Main loop with periodic pruning
        3. Alert processing (extract, dedup, enqueue)
        """
        logger.info('[UNIFIED-003/004] Entropy alert consumer loop started')
        queue = getattr(self, '_entropy_bridge_queue', None)
        if queue is None:
            logger.warning('[UNIFIED-003/004] No entropy bridge queue available')
            return

        # Initialize pending entities tracking (dedup)
        pending_entities: dict[str, float] = self._init_alert_dedup_tracking()

        while self._running:
            result = await self._process_alert_iteration(queue, pending_entities)
            if result == 'shutdown':
                break

        logger.info('[UNIFIED-003/004] Entropy alert consumer loop stopped')

    def _init_alert_dedup_tracking(self) -> dict[str, float]:
        """F360-R: Initialize pending entities tracking for dedup."""
        return {}  # entity_id → added_at timestamp

    async def _process_alert_iteration(
        self, queue: Any, pending_entities: dict[str, float],
    ) -> str:
        """
        F360-R: Process one alert iteration.

        Returns 'shutdown' to signal graceful shutdown, 'continue' otherwise.
        """
        try:
            # Wait for alert with timeout (allows graceful shutdown)
            alert = await safe_wait_for(queue.get(), timeout=5.0)

            if not self._running:
                return 'shutdown'

            # Periodic pruning of stale pending entries
            pending_entities = self._prune_stale_entities(pending_entities)

            # Process alert: extract, dedup, enqueue
            await self._process_single_alert(alert, pending_entities)

            return 'continue'

        except asyncio.TimeoutError:
            return 'continue'
        except asyncio.CancelledError:
            logger.info('[UNIFIED-003/004] Entropy consumer loop cancelled')
            return 'shutdown'
        except Exception as e:
            logger.warning('[UNIFIED-003/004] Entropy consumer loop error: %s', e)
            return 'continue'

    def _prune_stale_entities(
        self, pending_entities: dict[str, float],
    ) -> dict[str, float]:
        """F360-R: Prune stale entities from pending set."""
        _PENDING_TTL_S = 120.0
        _PRUNE_INTERVAL = 10
        _now = time.monotonic()

        # Instance counter for periodic pruning (initialized in __init__)
        self._entropy_prune_counter += 1

        if self._entropy_prune_counter % _PRUNE_INTERVAL != 0:
            return pending_entities

        stale = [
            eid for eid, ts in pending_entities.items()
            if _now - ts > _PENDING_TTL_S
        ]
        for eid in stale:
            del pending_entities[eid]

        if stale:
            logger.debug(
                '[UNIFIED-003/004] Pruned %d stale pending entities (remaining=%d)',
                len(stale), len(pending_entities),
            )

        return pending_entities

    async def _process_single_alert(
        self, alert: Any, pending_entities: dict[str, float],
    ) -> None:
        """F360-R: Extract, dedup, and enqueue single alert."""
        entity_id = alert.entity_id
        entropy = alert.entropy
        protocols = alert.metadata.get('alternative_protocols', ['ct', 'passive_dns'])
        reason = f"high_entropy:{alert.risk_level}"

        # Skip if no entity_id
        if not entity_id:
            logger.debug('[UNIFIED-003/004] Alert missing entity_id, skipping')
            return

        # Dedup: skip if already pending
        if entity_id in pending_entities:
            logger.debug('[UNIFIED-003/004] Entity %s already pending, skipping', entity_id)
            return

        logger.info(
            '[UNIFIED-003/004] High entropy alert: entity=%s entropy=%.3f reason=%s',
            entity_id, entropy, reason,
        )

        # Enqueue with backpressure
        request = {
            'entity_id': entity_id,
            'entropy': entropy,
            'protocols': protocols,
            'reason': reason,
        }

        try:
            self._micro_sprint_queue.put_nowait(request)
            pending_entities[entity_id] = time.monotonic()
            self._entropy_alerts_processed += 1
        except asyncio.QueueFull:
            logger.warning(
                '[ISSUE-022-03] Micro-sprint queue FULL (%d/%d), dropping alert for entity=%s',
                self._micro_sprint_queue.qsize(),
                self._micro_sprint_queue.maxsize,
                entity_id,
            )

    async def _micro_sprint_worker_loop(self) -> None:
        """
        Background worker that drains _micro_sprint_queue and executes micro-sprints.

        UNIFIED-004: Decouples alert ingestion (consumer loop) from execution
        (worker loop) with bounded backpressure via _micro_sprint_queue.

        UNIFIED-004 iterative feedback: If micro-sprint does not improve entropy,
        re-enqueues with remaining untried protocols (up to MAX_RETRY_ROUNDS=2).
        Each retry uses a subset of protocols not yet attempted, with exponential
        backoff (2^retry seconds delay) to avoid thrashing.
        """
        _MAX_RETRY_ROUNDS = 2
        _RETRY_BACKOFF_BASE = 2.0  # seconds — exponential: 2s, 4s

        logger.info('[UNIFIED-004] Micro-sprint worker loop started')

        while self._running:
            try:
                # Wait for request with timeout to allow graceful shutdown
                request = await safe_wait_for(self._micro_sprint_queue.get(), timeout=5.0)

                if not self._running:
                    break

                # Process the micro-sprint request
                await self._process_micro_sprint_request(
                    request, _MAX_RETRY_ROUNDS, _RETRY_BACKOFF_BASE
                )

            except asyncio.TimeoutError:
                # Normal timeout, continue loop
                continue
            except asyncio.CancelledError:
                logger.info('[UNIFIED-004] Micro-sprint worker loop cancelled')
                break
            except Exception as e:
                logger.warning('[UNIFIED-004] Micro-sprint worker loop error: %s', e)
                # Continue loop despite errors
                continue

        logger.info('[UNIFIED-004] Micro-sprint worker loop stopped')

    # ------------------------------------------------------------------
    # Complexity-reduced helpers for _micro_sprint_worker_loop (25 → ~10)
    # ------------------------------------------------------------------

    async def _process_micro_sprint_request(
        self, request: dict[str, Any], max_retry_rounds: int, backoff_base: float
    ) -> None:
        """Process a single micro-sprint request from the queue."""
        entity_id = request['entity_id']
        entropy = request['entropy']
        protocols = list(request.get('protocols', ['ct', 'passive_dns']))
        reason = request.get('reason', 'high_entropy')
        retry_count = request.get('_retry_count', 0)
        previously_tried = list(request.get('_previously_tried', []))

        # Filter out already-tried protocols
        untried_protocols = [p for p in protocols if p not in previously_tried]
        if not untried_protocols:
            logger.debug(
                '[UNIFIED-004] All protocols exhausted for %s (tried=%s) — giving up',
                entity_id, previously_tried,
            )
            return

        # [NEXUS]-018-02: IGD abort check
        if self._should_igd_abort(entity_id):
            return

        # Trigger micro-sprint with untried protocols only
        result = await self.trigger_micro_sprint(
            entity_id=entity_id,
            entropy=entropy,
            alternative_protocols=untried_protocols,
            max_hops=2,
            timeout=30.0,
            reason=reason,
        )

        # [META]-015: Contradiction check
        await self._check_micro_sprint_contradictions(entity_id, result)

        all_tried = previously_tried + list(result.protocols_tried)

        if result.success:
            self._handle_micro_sprint_success(entity_id, result, retry_count)
            return

        # UNIFIED-004 iterative feedback: re-enqueue if retries remain
        await self._requeue_micro_sprint_retry(
            entity_id, entropy, protocols, reason, retry_count, all_tried,
            untried_protocols, result, max_retry_rounds, backoff_base
        )

    def _should_igd_abort(self, entity_id: str) -> bool:
        """
        [NEXUS]-018-02: Check if IGD policy should abort this micro-sprint.
        Keys micro-sprint branches as "ms:<entity_id>" to avoid polluting ToT branch keys.
        """
        try:
            if not hasattr(self, '_orchestrator') or self._orchestrator is None:
                return False
            igd = getattr(self._orchestrator, '_igd_policy', None)
            if igd is None or not callable(igd.should_abort):
                return False
            ms_key = f'ms:{entity_id}'
            igd.register_branch(ms_key)
            if igd.should_abort(ms_key, depth=1):
                logger.info('[NEXUS]-018-02 IGD abort micro-sprint: entity=%s', entity_id)
                return True
        except Exception:  # noqa: BLE001
            pass  # fail-soft — IGD abort is advisory, never blocks micro-sprint
        return False

    async def _check_micro_sprint_contradictions(self, entity_id: str, result: Any) -> None:
        """
        [META]-015: Contradiction check — compare micro-sprint results with originals.
        If micro-sprint finds conflicting data, emit an EntropyAlert for JTMS retraction.
        """
        if not result.evidence_ids:
            return
        original_findings, original_entropy = await self._get_original_findings_for_entity(entity_id)
        if not original_findings:
            return
        contradictions = self._detect_micro_sprint_contradictions(
            original_findings,
            list(result.evidence_ids),
        )
        if contradictions:
            await self._emit_contradiction_alert(entity_id, contradictions, original_entropy)

    def _handle_micro_sprint_success(self, entity_id: str, result: Any, retry_count: int) -> None:
        """Handle successful micro-sprint with IGD feedback."""
        logger.info(
            '[UNIFIED-004] Micro-sprint improved entropy: entity=%s '
            'new_entropy=%.3f protocols=%s retries=%d',
            entity_id, result.new_entropy,
            result.protocols_tried, retry_count,
        )
        # [NEXUS]-018-02: Feed micro-sprint success back to IGD policy
        try:
            if hasattr(self, '_orchestrator') and self._orchestrator is not None:
                igd = getattr(self._orchestrator, '_igd_policy', None)
                if igd is not None and callable(igd.report_iocs):
                    igd.report_iocs(f'ms:{entity_id}', [result.new_entropy])
        except Exception:  # noqa: BLE001
            pass

    async def _requeue_micro_sprint_retry(
        self, entity_id: str, entropy: float, protocols: list[str], reason: str,
        retry_count: int, all_tried: list[str], untried_protocols: list[str],
        result: Any, max_retry_rounds: int, backoff_base: float
    ) -> None:
        """Re-enqueue micro-sprint with retry logic and exponential backoff."""
        remaining_retries = max_retry_rounds - retry_count
        if remaining_retries <= 0 or len(untried_protocols) <= len(result.protocols_tried):
            logger.debug(
                '[UNIFIED-004] Micro-sprint exhausted: entity=%s tried=%s retries=%d',
                entity_id, all_tried, retry_count,
            )
            return

        # Some protocols were not attempted (timeout or early exit)
        next_retry = retry_count + 1
        backoff_s = backoff_base * (2 ** retry_count)

        logger.info(
            '[UNIFIED-004] Micro-sprint retry queued: entity=%s retry=%d/%d backoff=%.1fs protocols=%s',
            entity_id, next_retry, max_retry_rounds, backoff_s, untried_protocols,
        )

        retry_request = {
            'entity_id': entity_id,
            'entropy': entropy,
            'protocols': protocols,
            'reason': reason,
            '_retry_count': next_retry,
            '_previously_tried': all_tried,
            '_backoff_s': backoff_s,
        }

        # Delay before re-enqueue (exponential backoff)
        try:
            await asyncio.sleep(backoff_s)
        except asyncio.CancelledError:
            return

        # Re-enqueue (drop if full — prevent feedback loops from saturating the queue)
        try:
            self._micro_sprint_queue.put_nowait(retry_request)
        except asyncio.QueueFull:
            logger.debug('[UNIFIED-004] Retry queue full for %s — dropping', entity_id)

    async def _handle_url(self, entity_id: str) -> list[str]:
        """Handle direct URL fetch protocol."""
        self._frontier.append(entity_id)
        step_result = await self.step(self._ctx)
        return step_result.get('evidence_ids', [])

    async def _handle_ct(self, entity_id: str) -> list[str]:
        """Handle Certificate Transparency protocol."""
        from ..recon.ct_log_client import CTLogClient
        from hledac.universal.paths import CACHE_ROOT
        import httpx

        evidence_ids = []
        cache_dir = CACHE_ROOT / 'ct_logs'
        cache_dir.mkdir(parents=True, exist_ok=True)

        client = CTLogClient(cache_dir=cache_dir)
        async with httpx.AsyncClient() as session:
            results = await client.search(entity_id, session)
            for result in results:
                if isinstance(result, dict) and 'san_names' in result:
                    for san_name in result['san_names'][:5]:
                        evidence_ids.append(f"ct:{entity_id}:{san_name}")
        return evidence_ids

    async def _handle_passive_dns(self, entity_id: str) -> list[str]:
        """Handle Passive DNS protocol."""
        from ..security.passive_dns import lookup_passive_dns
        evidence_ids = []
        results = await lookup_passive_dns(entity_id)
        for result in results:
            if isinstance(result, str):
                evidence_ids.append(f"pdns:{entity_id}:{result}")
        return evidence_ids

    async def _handle_doh(self, entity_id: str) -> list[str]:
        """Handle DNS-over-HTTPS protocol."""
        from ..security.passive_dns import resolve_doh
        evidence_ids = []
        results = await resolve_doh(entity_id)
        for result in results:
            if isinstance(result, str):
                evidence_ids.append(f"doh:{entity_id}:{result}")
        return evidence_ids

    async def _handle_wayback(self, entity_id: str) -> list[str]:
        """Handle Wayback Machine protocol."""
        from ..discovery.wayback_cdx_adapter import WaybackCDXAdapter
        evidence_ids = []
        adapter = WaybackCDXAdapter()
        batch = await adapter.search(entity_id, max_results=10)
        for hit in (batch.hits or []):
            if hasattr(hit, 'url') and hit.url:
                evidence_ids.append(f"wayback:{entity_id}:{hit.url[:80]}")
        return evidence_ids

    async def _handle_bgp(self, entity_id: str) -> list[str]:
        """
        Handle BGP enrichment protocol.

        Gap D FIX: Replaced stub with real ASN lookup via BGPAdapter.enrich_ips().

        Flow:
          1. Check if entity_id is an IP address (BGP works on IPs)
          2. Call BGPAdapter.enrich_ips() for real ASN/path lookup
          3. Create evidence IDs from BGP result (ASN, prefix, org, country)
          4. Log enrichment for telemetry
          5. Close adapter to release resources

        Args:
            entity_id: IP address or entity identifier

        Returns:
            List of evidence IDs from BGP enrichment
        """
        evidence_ids = []
        if not FeatureFlags.get(FeatureFlag.BGP):
            logger.debug('[UNIFIED-004] BGP skipped for %s (HLEDAC_ENABLE_BGP=0)', entity_id)
            return evidence_ids

        # BGP works on IP addresses - skip if not IP
        try:
            import ipaddress
            ipaddress.ip_address(entity_id)
        except ValueError:
            logger.debug('[UNIFIED-004] BGP skipped for non-IP entity: %s', entity_id)
            return evidence_ids

        adapter = None
        try:
            from ..recon.bgp_lane import BGPAdapter

            adapter = BGPAdapter()
            results = await adapter.enrich_ips([entity_id])

            if results and len(results) > 0:
                result = results[0]
                if result.asn:
                    # Create structured evidence IDs from BGP result
                    evidence_ids.append(f"bgp:{entity_id}:asn:{result.asn}")
                    if result.prefix:
                        evidence_ids.append(f"bgp:{entity_id}:prefix:{result.prefix}")
                    if result.org_name:
                        evidence_ids.append(f"bgp:{entity_id}:org:{result.org_name}")
                    if result.country_code:
                        evidence_ids.append(f"bgp:{entity_id}:cc:{result.country_code}")
                    if result.rir:
                        evidence_ids.append(f"bgp:{entity_id}:rir:{result.rir}")

                    logger.info(
                        '[UNIFIED-004] BGP enriched: %s → ASN %s / %s / %s',
                        entity_id,
                        result.asn,
                        result.prefix or 'unknown',
                        result.org_name or 'unknown',
                    )
                else:
                    logger.debug('[UNIFIED-004] BGP no ASN found for %s', entity_id)
            else:
                logger.debug('[UNIFIED-004] BGP no result for %s', entity_id)

        except ImportError:
            logger.warning('[UNIFIED-004] BGP adapter unavailable, skipping enrichment')
        except Exception as e:
            logger.debug('[UNIFIED-004] BGP enrichment failed for %s: %s', entity_id, e)
        finally:
            # Gap D FIX: Always close adapter to release HTTP session resources
            if adapter is not None:
                try:
                    await adapter.close()
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass

        return evidence_ids

    async def _handle_shodan(self, entity_id: str) -> list[str]:
        """Handle Shodan protocol."""
        evidence_ids = []
        if FeatureFlags.get(FeatureFlag.SHODAN):
            from ..recon.shodan_lane import ShodanLane
            lane = ShodanLane()
            result = await lane.search_ip(entity_id, max_results=5)
            if result and hasattr(result, 'items'):
                for item in result.items[:5]:
                    eid = f"shodan:{entity_id}:{item.get('ip_str', '')}"
                    evidence_ids.append(eid)
        else:
            logger.debug('[UNIFIED-004] Shodan skipped for %s (HLEDAC_ENABLE_SHODAN=0)', entity_id)
        return evidence_ids

    async def _handle_censys(self, entity_id: str) -> list[str]:
        """Handle Censys protocol."""
        evidence_ids = []
        if FeatureFlags.get(FeatureFlag.CENSYS):
            from ..recon.exposure_clients import CensysClient
            client = CensysClient()
            result = await client.search(entity_id, max_results=5)
            if result and hasattr(result, 'items'):
                for item in result.items[:5]:
                    eid = f"censys:{entity_id}:{item.get('ip', '')}"
                    evidence_ids.append(eid)
        else:
            logger.debug('[UNIFIED-004] Censys skipped for %s (HLEDAC_ENABLE_CENSYS=0)', entity_id)
        return evidence_ids

    async def _handle_gopher(self, entity_id: str) -> list[str]:
        """Handle Gopher protocol."""
        evidence_ids = []
        if FeatureFlags.get(FeatureFlag.GOPHER):
            if self._gopher_transport is not None:
                result = await self._gopher_transport.fetch(entity_id)
                if result and result.get('success'):
                    evidence_ids.append(f"gopher:{entity_id}")
        else:
            logger.debug('[UNIFIED-004] Gopher skipped for %s (HLEDAC_ENABLE_GOPHER=0)', entity_id)
        return evidence_ids

    async def _handle_commoncrawl(self, entity_id: str) -> list[str]:
        """Handle CommonCrawl protocol."""
        import httpx
        evidence_ids = []
        if FeatureFlags.get(FeatureFlag.COMMONCRAWL):
            cc_url = f"http://index.commoncrawl.org/CC-MAIN-2024-10-index?url={entity_id}&output=json"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(cc_url)
                if resp.status_code == 200:
                    for line in resp.text.strip().split('\n')[:5]:
                        evidence_ids.append(f"commoncrawl:{entity_id}:{line[:80]}")
        else:
            logger.debug('[UNIFIED-004] CommonCrawl skipped for %s (HLEDAC_ENABLE_COMMONCRAWL=0)', entity_id)
        return evidence_ids

    async def _handle_dht(self, entity_id: str) -> list[str]:
        """Handle DHT (BitTorrent) protocol."""
        evidence_ids = []
        if FeatureFlags.get(FeatureFlag.DHT):
            evidence_ids.append(f"dht:{entity_id}:probe")
            logger.debug('[UNIFIED-004] DHT probe queued for %s', entity_id)
        else:
            logger.debug('[UNIFIED-004] DHT skipped for %s (HLEDAC_ENABLE_DHT=0)', entity_id)
        return evidence_ids

    async def _handle_blockchain(self, entity_id: str) -> list[str]:
        """Handle Blockchain protocol."""
        evidence_ids = []
        if FeatureFlags.get(FeatureFlag.BLOCKCHAIN_ANALYZER):
            evidence_ids.append(f"blockchain:{entity_id}:lookup")
            logger.debug('[UNIFIED-004] Blockchain lookup queued for %s', entity_id)
        else:
            logger.debug('[UNIFIED-004] Blockchain skipped for %s (HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER=0)', entity_id)
        return evidence_ids


# Dispatch table - maps protocol name to handler method name
_PROTOCOL_HANDLERS: dict[str, str] = {
    'url': '_handle_url',
    'ct': '_handle_ct',
    'passive_dns': '_handle_passive_dns',
    'doh': '_handle_doh',
    'wayback': '_handle_wayback',
    'bgp': '_handle_bgp',
    'shodan': '_handle_shodan',
    'censys': '_handle_censys',
    'gopher': '_handle_gopher',
    'commoncrawl': '_handle_commoncrawl',
    'dht': '_handle_dht',
    'blockchain': '_handle_blockchain',
}
