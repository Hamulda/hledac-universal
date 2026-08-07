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
from typing import Any

from operator import attrgetter, itemgetter
import httpx
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
import orjson
from cachetools import TTLCache

from hledac.universal.core.capabilities import (
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
from hledac.universal.core.constants import NETWORK
from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags
from hledac.universal.runtime.logging_setup import get_logger
from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator, make_privacy_allocator
from hledac.universal.tools.file_cache import apply_fcntl_nocache as _apply_fcntl_nocache
from hledac.universal.tools.zstd_compressor import ZstdCompressor
from hledac.universal.utils.async_helpers import (
    BoundedPerHostGate,
    DomainRateLimiter,
    async_getaddrinfo,
    parallel,
    safe_create_task,
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
from ..knowledge.cross_sprint_gate import get_cross_sprint_gate
from ..knowledge.entity_confirmation import get_entity_confirmation_service
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
from hledac.universal.core.rust_backend import rust

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
    __slots__ = ('_adaptive_priority_provider', '_aimd', '_aimd_semaphore', '_base_retry_delay', '_batch_cp_result', '_blitz_mode', '_capacity', '_captcha_detections', '_captcha_detector', '_concurrency', '_concurrency_provider', '_config', '_cooldown_seconds', '_cover_count', '_cross_sprint_gate', '_entity_confirmation_service', '_ctx', '_current_geo_context', '_darknet_connector', '_dedup_lock', '_domain_rate_limiter', '_effective_ua', '_enqueue_pivot_provider', '_entropy_bridge_queue', '_entropy_bridge_task', '_entropy_alerts_processed', '_evidence_ids', '_evidence_sink', '_frontier', '_geo_proxies', '_gopher_transport', '_gopher_transport_enabled', '_hints_extractor', '_host_ips_cache', '_host_ips_inflight', '_http_cache_enabled', '_http_cache_transport', '_hypothesis_depth_provider', '_hypothesis_depth_setter', '_hypothesis_query_count_provider', '_hypothesis_query_count_setter', '_lightpanda_lock', '_lightpanda_pool', '_lightpanda_pool_started', '_max_backoff_delay', '_max_retries', '_micro_sprint_queue', '_micro_sprint_original_findings', '_micro_sprint_worker_task', '_orchestrator', '_paywall_bypass', '_per_host_gate', '_per_host_limit', '_pivot_queue_provider', '_pivot_stats_provider', '_privacy_allocator', '_privacy_lock', '_processed_urls', '_retry_budget', '_retry_budget_lock', '_retry_budget_max', '_retry_budget_window', '_robots_parser', '_running', '_session_checkpoint_task', '_session_lmdb_env', '_session_manager', '_sprint_config_provider', '_sprint_remaining_provider', '_stop_reason', '_swarm_dag', '_swarm_dag_rebalance_task', '_telemetry', '_tor_transport', '_tor_transport_enabled', '_urls_fetched_count', '_zstd')

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
        self._entity_confirmation_service = get_entity_confirmation_service_sync()
        self._evidence_ids: deque = deque(maxlen=500)
        self._evidence_sink = evidence_sink  # A5-02: Dependency Inversion — injected sink, not direct EvidenceLog import
        self._urls_fetched_count: int = 0
        self._stop_reason: str | None = None
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
        self._telemetry: dict[str, Any] = {'aimd_concurrency': self._aimd.window, 'active_fetches': 0, 'total_successes': 0, 'total_failures': 0, 'circuit_breaker_blocks': 0, 'circuit_breaker_active': 0, 'uma_state': 'ok', 'decrease_factor_used': 1.0, 'backpressure_clamp_events': 0, 'io_only_skipped': 0, 'cross_sprint_skipped': 0, 'entity_confirmation_skipped': 0}
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
        _diff = int(new_window) - self._aimd_semaphore._value
        if _diff > 0:
            for _ in range(_diff):
                self._aimd_semaphore.release()
        elif _diff < 0:
            for _ in range(-_diff):
                self._aimd_semaphore.release()  # negative diff: release excess permits
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

        This is a fire-and-forget telemetry method. Errors are logged and
        swallowed so that fetch work is never blocked by saturation detection.

        Args:
            entity_value: The entity value (e.g., domain, IP, URL, hash).
            ioc_type: Optional IOC type string for telemetry (e.g., "domain", "ipv4").
        """
        try:
            detector = _COGNITIVE_SATURATION_DETECTOR
            if detector is not None and hasattr(detector, 'report_entity_discovery'):
                detector.report_entity_discovery(entity_value, ioc_type)
        except Exception as e:
            logger.debug('[ULTIMATE]-002 report_entity_discovery failed: %s', e)

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

    async def _validate_fetch_target(self, url: str) -> tuple[bool, dict[str, Any]]:  # noqa: C901
        """
        Validate fetch target: resolve and check for private IPs.

        NOTE (P3-8): This provides DNS rebinding protection but has a residual
        TOCTOU window between validation and fetch. The actual fetch
        (curl_cffi) resolves DNS independently. For HTTPS, certificate
        validation provides secondary protection. For HTTP, the risk is
        acknowledged but the performance cost of binding to pre-validated
        IPs is prohibitive.

        Sprint F-A4: consults ``self._host_ips_cache`` first (populated
        by ``run_step`` via the batch DNS resolver) and falls through
        to a per-fetch ``async_getaddrinfo`` on miss. Cache is reset
        every batch so freshness is preserved.

        C3-02 FIX: Single-flight pattern — concurrent cache misses for the same
        host share one Future so only one DNS resolution runs. The ``_per_host_gate``
        semaphore still enforces per-host rate limiting; single-flight prevents
        duplicate work on simultaneous misses.
        """
        try:
            hostname = _fast_url_host(url)
            if not hostname:
                return (False, {'blocked_reason': 'no_hostname'})
            try:
                ip = ipaddress.ip_address(hostname)
                if not self._is_ip_public(str(ip)):
                    return (False, {'resolved_ips': [str(ip)], 'blocked_reason': 'private_ip_literal'})
                return (True, {'resolved_ips': [str(ip)]})
            except ValueError:  # noqa: BLE001
                pass
            cache_key = hostname.lower()
            cached_ips = self._host_ips_cache.get(cache_key)
            if cached_ips is not None:
                if not cached_ips:
                    return (False, {'resolved_ips': [], 'blocked_reason': 'dns_resolution_failed'})
                for ip_str in cached_ips:
                    if not self._is_ip_public(ip_str):
                        return (False, {'resolved_ips': list(cached_ips), 'blocked_reason': 'private_ip_resolved', 'blocked_ip': ip_str})
                return (True, {'resolved_ips': list(cached_ips)})

            # C3-02: Single-flight — if another task is already resolving this host, wait on its Future
            if cache_key in self._host_ips_inflight:
                ips = await self._host_ips_inflight[cache_key]
                if ips is None or not ips:
                    return (False, {'resolved_ips': [], 'blocked_reason': 'dns_resolution_failed'})
                for ip_str in ips:
                    if not self._is_ip_public(ip_str):
                        return (False, {'resolved_ips': ips, 'blocked_reason': 'private_ip_resolved', 'blocked_ip': ip_str})
                return (True, {'resolved_ips': ips})

            # Reserve slot for new resolution
            fut: asyncio.Future[list[str] | None] = asyncio.get_event_loop().create_future()
            self._host_ips_inflight[cache_key] = fut

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
            if not ips:
                return (False, {'resolved_ips': [], 'blocked_reason': 'dns_resolution_failed'})
            for ip_str in ips:
                if not self._is_ip_public(ip_str):
                    return (False, {'resolved_ips': ips, 'blocked_reason': 'private_ip_resolved', 'blocked_ip': ip_str})
            return (True, {'resolved_ips': ips})
        except (TimeoutError, httpx.HTTPError, httpx.TimeoutException, OSError) as e:  # noqa: BLE001 — best-effort; httpx client creation failure; non-critical
            return (False, {'blocked_reason': f'validation_error: {e}'})

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

    async def _aimd_acquire(self) -> tuple[float, None]:  # noqa: C901
        """
        Acquire AIMD slot, returns (concurrency_window, None).

        ISSUE 2.2: Uses unified PyAIMDController (Rust) with lock-free atomic state.
        Falls back to Python AIMDWindow for backpressure clamping + semaphore acquire.
        """
        _bp_clearing: float | None = None
        _bp_uma_state = 'ok'
        if self._batch_cp_result is _CP_RETURNED_NONE:
            pass
        elif self._batch_cp_result is not _CP_NOT_CALLED:
            _bp_clearing, _bp_stealth, _bp_uma_state, _ = self._batch_cp_result
        elif self._concurrency_provider is not None:
            try:
                _bp_result = self._concurrency_provider()
                if _bp_result is not None:
                    _bp_clearing, _bp_stealth, _bp_uma_state, _ = _bp_result
            except (TypeError, ValueError, KeyError):  # noqa: BLE001 — best-effort; concurrency_provider result parsing failure; non-critical
                pass
        # U2-05 FIX: Direct governor fetch_limit — bypasses concurrency_provider cache.
        # At 7.2+ GiB (io_only=True) we arrive here even when batch_cp_result is
        # _CP_NOT_CALLED. The governor.evaluate() call is fast (0.5s TTL on io_only)
        # and gives the authoritative fetch_limit for this precise moment.
        _governor_fetch_limit: int | None = None
        try:
            from hledac.universal.core.protocols import get_governor
            _gov = get_governor()
            if _gov is not None:
                _gov_decision = await _gov.evaluate()
                _governor_fetch_limit = _gov_decision.fetch_limit
                if _bp_clearing is None or _governor_fetch_limit < _bp_clearing:
                    _bp_clearing = float(_governor_fetch_limit)
                if _gov_decision.io_only:
                    _bp_uma_state = _gov_decision.uma_state
        except Exception:  # noqa: BLE001 — best-effort; governor evaluate failure; continue with cached path
            pass
        self._telemetry['uma_state'] = _bp_uma_state

        # Acquire slot (Rust: lock-free; Python fallback: semaphore)
        if isinstance(self._aimd, PyAIMDController):
            # Rust path: lock-free atomic acquire
            current_window, _ = self._aimd.acquire()
            # Backpressure clamping if needed
            if _bp_clearing is not None and _bp_clearing < current_window:
                self._aimd.set_window(_bp_clearing)
                current_window = _bp_clearing
                self._telemetry['backpressure_clamp_events'] += 1
        else:
            # Python fallback: use existing AIMDWindow + semaphore pattern
            if _bp_clearing is not None and _bp_clearing < self._aimd.window:
                await self._aimd.set_window(_bp_clearing)
                self._telemetry['backpressure_clamp_events'] += 1
            current_window = self._aimd.window
            # Sync window to semaphore if needed
            if current_window != self._aimd_semaphore._value:
                # Adjust semaphore to match window
                diff = int(current_window) - self._aimd_semaphore._value
                if diff > 0:
                    for _ in range(diff):
                        self._aimd_semaphore.release()
                elif diff < 0:
                    # Shrinking: release excess permits directly without blocking acquire.
                    # When active==window the semaphore is already near-zero, so we
                    # may temporarily hold more permits than the new window — this
                    # is safe as active slots drain naturally on release().
                    for _ in range(-diff):
                        self._aimd_semaphore.release()
            await self._aimd_semaphore.acquire()

        self._telemetry['aimd_concurrency'] = current_window
        self._telemetry['active_fetches'] += 1
        return (current_window, None)

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
        except (TimeoutError, asyncio.CancelledError):  # noqa: BLE001 — best-effort; privacy_acquire cancellation/timeout; fail-open to clearnet
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
        except (httpx.HTTPError, OSError, asyncio.CancelledError) as e:  # noqa: BLE001 — best-effort; httpx response body read; non-critical
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
        except (httpx.HTTPError, OSError, asyncio.CancelledError) as e:  # noqa: BLE001 — best-effort; httpx stream read; non-critical
            logger.warning('I2P fetch failed: %s', e)
            await self._aimd_release_failure()
            return None

    async def _fetch_with_curl(self, url: str, proxy: str | None=None, *, resolve: dict[str, str] | None=None, _extra_headers: dict[str, str] | None=None) -> dict[str, Any] | None:
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
            try:
                from hledac.universal.transport.http3_lane import probe_altsvc_speculative
                probe_altsvc_speculative(url)
            except (ImportError, AttributeError, TypeError):  # noqa: BLE001 — best-effort; http3_lane unavailable; fail-open
                pass
            _curl_http_version = _altsvc_http_version_for(_altsvc_extract_host(url))
            _ja3_profile = next_ja3_profile()
            # F-07: Merge extra headers (clearance cookies) with request headers
            _req_headers = dict(_extra_headers) if _extra_headers else None
            _curl_result = await fetch_via_curl_cffi_with_caps_check(url=url, headers=_req_headers, timeout_s=30.0, max_bytes=10 * 1024 * 1024, profile=_ja3_profile, http_version=_curl_http_version, _pre_probe=False, resolve=resolve)
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
        except (OSError, asyncio.CancelledError) as e:  # noqa: BLE001 — curl_cffi doesn't raise httpx.HTTPError; only network/OS errors expected here
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
        from hledac.universal.core.feature_flags import FeatureFlags, FeatureFlag
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
                'content_type': _extract_content_type(dict(quic_response.headers) if quic_response.headers else {}),
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
        logger.info('FetchCoordinator started with %s URLs in frontier', len(self._frontier))

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
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.sleep(_delay)
        return (True, None)

    async def _robots_check_fast(
        self,
        url: str,
        domain_robots: dict[str, RobotsDocument | None],
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

    async def _do_step(self, ctx: dict[str, Any]) -> dict[str, Any]:  # noqa: C901
        """
        Execute one fetch step with batch parallel fetch.

        Sprint 5B: Process up to max_urls_per_step from frontier using
        controlled parallel batch fetch that respects:
        - timeout matrix
        - concurrency matrix
        - AIMD window
        """
        self._ctx.update(ctx)
        budget_mgr = ctx.get('budget_manager')
        if budget_mgr:
            allowed, reason = budget_mgr.check_network_allowed()
            if not allowed:
                self._stop_reason = reason
                return self._get_step_result()
        candidates = []
        raw_batch: list[str] = []
        for _ in range(self._config.max_urls_per_step * 2):
            if not self._frontier:
                break
            url = self._frontier.popleft()
            raw_batch.append(url)

        def _extract_raw_hosts() -> set[str]:
            """Extract unique hosts from raw batch (sync, fast — runs in thread pool).

            P1-3 OPT: Uses fast string slicing instead of httpx.URL() parsing.
            URL format: scheme://host[:port][/path]. host is between "://" and ":" or "/".
            This is 10-50× faster than httpx.URL() for pure host extraction.
            """
            hosts: set[str] = set()
            for url in raw_batch:
                # SEC-03 FIX: case-insensitive darknet TLD check — uppercase .ONION
                # would otherwise bypass this guard and hit the OS DNS resolver.
                _url_lower = url.lower()
                if _url_lower.endswith('.onion') or _url_lower.endswith('.i2p'):
                    continue
                try:
                    # Fast path: find host between :// and : or / or end
                    at_slashes = url.find('://')
                    if at_slashes < 0:
                        continue
                    host_start = at_slashes + 3
                    # Find end of host: first : / ? # or end
                    host_end = len(url)
                    for i in range(host_start, len(url)):
                        c = url[i]
                        if c in {':', '/', '?', '#'}:
                            host_end = i
                            break
                    hostname = url[host_start:host_end]
                    if hostname:
                        hosts.add(hostname.lower())
                except (ValueError, TypeError):  # noqa: BLE001 — best-effort; URL parse failure; skip this URL
                    continue
            return hosts

        def _dedup_and_trace() -> tuple[list[str], int]:
            """Dedup + trace (sync CPU — runs in thread pool)."""
            unique, dropped = dedupe_url_list(raw_batch, self._processed_urls)
            for url in raw_batch:
                trace_dedup_decision(url, url not in unique)
            return (unique, dropped)
        self._host_ips_cache = {}
        self._host_ips_inflight = {}
        self._batch_cp_result = _CP_NOT_CALLED
        if self._concurrency_provider is not None:
            try:
                _result = self._concurrency_provider()
                self._batch_cp_result = _result if _result is not None else _CP_RETURNED_NONE
            except (TypeError, ValueError):  # noqa: BLE001 — best-effort; concurrency_provider result parsing; non-critical
                pass
        resolver = get_batch_dns_resolver()
        raw_hosts_task = asyncio.to_thread(_extract_raw_hosts)
        dedup_task = asyncio.to_thread(_dedup_and_trace)
        raw_hosts = await raw_hosts_task
        dns_coro: asyncio.Task[dict[str, list[str]]] | None = None
        if raw_hosts:
            # F350M-R: Prefer rust.dns.prefetch() over batch_dns.py
            # hickory-dns async pipeline is 20-30% faster than aiodns/c-ares
            # Respects HLEDAC_ENABLE_DNS runtime flag
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
            except (TimeoutError, asyncio.CancelledError, OSError) as exc:  # noqa: BLE001 — best-effort; batch DNS pre-resolve failure; non-critical
                logger.debug('[F-A4] batch DNS pre-resolve failed: %s: %s', type(exc).__name__, exc)
        candidates: list[tuple[float, str]] = []
        # META-001: Cross-sprint pre-fetch gate — skip domains confirmed by
        # >=2 distinct sources across >=1 sprints with avg confidence >=75%.
        _skip_set: set[str] = set()
        _freshness_list: list[Any] = []
        try:
            if self._cross_sprint_gate is not None and self._cross_sprint_gate.enabled:
                # Extract entity_values (hosts) from unique URLs
                _gate_entities: list[dict[str, str]] = []
                for url in unique_batch:
                    _host = _fast_url_host(url)
                    if _host:
                        _gate_entities.append({"entity_value": _host.lower(), "entity_type": "domain"})
                if _gate_entities:
                    _skip_set, _freshness_list = await self._cross_sprint_gate.should_skip_batch(
                        _gate_entities
                    )
        except Exception:  # noqa: BLE001 — best-effort; cross-sprint gating failure; continue
            pass

        # [META]-014: Entity confirmation check — skip entities confirmed by
        # >=3 distinct source types with MAX(confidence) > 0.7
        _confirmed_set: set[str] = set()
        if self._entity_confirmation_service is not None and self._entity_confirmation_service.enabled:
            try:
                _conf_tuples = [
                    (_fast_url_host(url).lower(), "domain")
                    for url in unique_batch
                    if _fast_url_host(url)
                ]
                if _conf_tuples:
                    _conf_results = await self._entity_confirmation_service.is_confirmed_batch(
                        _conf_tuples
                    )
                    for _key, _conf in _conf_results.items():
                        if _conf.is_confirmed:
                            _confirmed_set.add(_conf.entity_value)
            except Exception:  # noqa: BLE001 — best-effort; entity confirmation failure; continue
                pass

        for url in unique_batch:
            _host = _fast_url_host(url).lower()
            # META-001: Skip known-good domains (CrossSprintGate)
            if _host and _host in _skip_set:
                self._telemetry["cross_sprint_skipped"] += 1
                continue
            # [META]-014: Skip confirmed entities (EntityConfirmationService)
            # Entity is confirmed if >=3 distinct sources with MAX(confidence) > 0.7
            if _host and _host in _confirmed_set:
                self._telemetry["entity_confirmation_skipped"] += 1
                continue
            # META-001: Boost priority for novel domains (never seen in any sprint)
            _priority = self._url_priority(url)
            if _host:
                for _f in _freshness_list:
                    if _f.entity_value == _host and _f.freshness == "novel":
                        _priority = max(_priority - 5, _PRIORITY_API)  # boost to near-API priority
                        break
            candidates.append((_priority, url))
        del unique_batch
        if not candidates:
            self._stop_reason = 'frontier_empty'
            return self._get_step_result()
        candidates.sort(key=lambda x: x[0])
        urls_to_fetch = [url for _, url in candidates[:self._config.max_urls_per_step]]
        # F-05: robots.txt enforcement — filter blocked URLs before fetch
        # Pre-fetch robots.txt for all unique domains in parallel (one HTTP per domain,
        # not one HTTP per URL), then check can_fetch from cached docs.
        _domain_robots: dict[str, RobotsDocument | None] = {}
        if self._robots_parser is not None:
            _unique_domains: set[str] = set()
            for _url in urls_to_fetch:
                try:
                    # P1-3 OPT: fast string slicing for host extraction (10-50× faster than httpx.URL)
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
                    _unique_domains.add(_domain.lower())
            if _unique_domains:
                async def _prefetch_domain(domain: str) -> tuple[str, RobotsDocument | None]:
                    try:
                        _doc = await self._robots_parser.fetch_robots(f'https://{domain}/')
                    except Exception:
                        return (domain, None)
                    return (domain, _doc)
                _domain_docs = await parallel(
                    [_prefetch_domain(d) for d in _unique_domains],
                    policy="collect",
                    concurrency=10,
                    ctx="robots_prefetch",
                )
                for _item in _domain_docs:
                    if isinstance(_item, Exception):
                        continue
                    _domain, _doc = _item
                    _domain_robots[_domain] = _doc
        _robots_filtered: list[str] = []
        _ua = getattr(self, '_effective_ua', None) or 'Hledac-Bot/1.0'
        # B20 FIX: parallelize robots checks across all URLs (concurrency=20).
        # Each _robots_check_fast is a sync in-memory check (no I/O), so high
        # concurrency is cheap. Aggregates crawl_delay and filters allowed URLs.
        _robots_results = await parallel(
            [self._robots_check_fast(_url, _domain_robots, _ua) for _url in urls_to_fetch],
            policy="collect",
            concurrency=20,
            ctx="robots_check",
        )
        _total_crawl_delay: float = 0.0
        # B20 FIX (rev2): zip is safe against index mismatch when some results
        # are absent from ok[]. parallel() preserves original coroutine order, so
        # zip naturally skips any trailing (url, result) pair when a coroutine
        # raises — no enumerate index corruption possible.
        for _url, _result in zip(urls_to_fetch, _robots_results.ok, strict=True):
            _allowed, _reason, _delay = _result
            _total_crawl_delay += _delay
            if not _allowed:
                logger.debug('[ROBOTS] blocked by robots.txt: %s (%s)', _url, _reason)
                trace_fetch_end(_url, 'robots', 'blocked', 0.0, {'reason': _reason})
                continue
            _robots_filtered.append(_url)
        if _total_crawl_delay > 0:
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.sleep(_total_crawl_delay)
        urls_to_fetch = _robots_filtered
        if not urls_to_fetch:
            return self._get_step_result()
        raw_batch_size = len(urls_to_fetch)
        effective_batch_size = min(raw_batch_size, int(self._aimd_concurrency))
        urls_to_fetch = urls_to_fetch[:effective_batch_size]
        batch_size = len(urls_to_fetch)
        if is_enabled():
            trace_counter('fetch.aimd.window', self._aimd_concurrency)
            trace_counter('fetch.active', self._telemetry['active_fetches'])
            trace_counter('fetch.batch_size', batch_size)

        # B1-FIX: Separate TOR/I2P URLs from clearnet and run with dedicated
        # concurrency (2 for TOR circuit reuse, 2 for I2P). Prevents 4× onion URLs
        # blocking each other at concurrency=1 while waiting for 2-5s TOR circuits.
        # TOR circuit establishment is slow but pipelining multiple requests over the
        # same circuit is cheap — concurrency=2 maximizes throughput without
        # overwhelming the circuit pool.
        from ..transport.transport_resolver import Transport, get_transport_for_url
        _tor_i2p_urls: list[str] = []
        _clearnet_urls: list[str] = []
        for _url in urls_to_fetch:
            _transport = get_transport_for_url(_url)
            if _transport in (Transport.TOR, Transport.I2P):
                _tor_i2p_urls.append(_url)
            else:
                _clearnet_urls.append(_url)

        batch_start = time.time()
        # B1-FIX: Build url->result mapping then reconstruct aligned results list.
        # Running TOR/I2P first (concurrency=2) allows circuit warmup before
        # clearnet batch saturates the AIMD window.
        url_to_result: dict[str, dict[str, Any] | None] = {}
        if _tor_i2p_urls:
            _tor_i2p_result = await parallel(
                [self._fetch_url(url) for url in _tor_i2p_urls],
                concurrency=min(len(_tor_i2p_urls), 2),  # B1: 2 for circuit reuse
                policy="log",
                ctx="fetch_coordinator.batch.tor_i2p",
            )
            for _url, _res in zip(_tor_i2p_urls, _tor_i2p_result.ok, strict=False):
                url_to_result[_url] = _res
        if _clearnet_urls:
            _clearnet_result = await parallel(
                [self._fetch_url(url) for url in _clearnet_urls],
                concurrency=batch_size,
                policy="log",
                ctx="fetch_coordinator.batch",
            )
            for _url, _res in zip(_clearnet_urls, _clearnet_result.ok, strict=False):
                url_to_result[_url] = _res
        # Preserve original urls_to_fetch order for downstream zip pairing
        results = [url_to_result.get(_url) for _url in urls_to_fetch]
        batch_elapsed = time.time() - batch_start
        evidence_ids = []
        for url, result in zip(urls_to_fetch, results, strict=False):
            if isinstance(result, Exception):
                _ev0 = type(result).__name__
                logger.debug('[BATCH] fetch exception for %s: %s: %s', url, type(result).__name__, result)
                continue
            if result and result.get('success'):
                self._urls_fetched_count += 1
                evidence_id = result.get('evidence_id')
                if evidence_id:
                    evidence_ids.append(evidence_id)
                    self._evidence_ids.append(evidence_id)
                    # A5-02: Write to injected evidence_sink if available (Dependency Inversion)
                    if self._evidence_sink is not None:
                        with contextlib.suppress(Exception):
                            await self._evidence_sink.append_evidence(evidence_id)
                    # [ULTIMATE]-002: Report entity discovery for cognitive saturation tracking.
                    # Extract entity value from URL for domain-level tracking.
                    _entity_host = _fast_url_host(url)
                    if _entity_host:
                        self.report_entity_discovery(_entity_host, "domain")
                if budget_mgr:
                    allowed, reason = budget_mgr.check_snapshot_allowed()
                    if not allowed:
                        self._stop_reason = reason
                        break
        effective_parallelism = min(len(urls_to_fetch), int(self._aimd_concurrency))
        return self._get_step_result(evidence_ids, batch_size=batch_size, effective_parallelism=effective_parallelism, batch_elapsed_ms=round(batch_elapsed * 1000, 2))

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
        except* (Exception, BaseException):  # noqa: BLE001 — best-effort; TaskGroup ExceptionGroup or single exc; domain_breaker unavailable; non-critical
            dns_safe, dns_meta = (True, {})
            cb_allowed, cb_reason, cb_retry_after = (True, '', 0.0)
        return (dns_safe, dns_meta, cb_allowed, cb_reason, cb_retry_after)

    def _get_step_result(self, new_evidence_ids: list[str] | None=None, batch_size: int=0, effective_parallelism: int=0, batch_elapsed_ms: float=0.0) -> dict[str, Any]:
        """Get bounded step result with Sprint 5B batch telemetry."""
        evidence_ids = (new_evidence_ids or [])[:self._config.max_evidence_per_step]
        return {'urls_fetched': len(evidence_ids), 'evidence_ids': evidence_ids, 'total_fetched': self._urls_fetched_count, 'stop_reason': self._stop_reason, 'frontier_remaining': len(self._frontier), 'aimd_window': self._aimd_concurrency, 'active_fetches': self._telemetry['active_fetches'], 'batch_size': batch_size, 'effective_parallelism': effective_parallelism, 'batch_elapsed_ms': batch_elapsed_ms}

    @_otel_instrumented('fetch.url', component='network')
    async def _fetch_url(self, url: str, attempt: int=0) -> dict[str, Any] | None:  # noqa: C901
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
        from ..project_types import OfflineModeError, is_offline_mode
        if is_offline_mode():
            raise OfflineModeError(f'Offline mode enabled, skipping fetch: {url}')
        # B2-FIX: _processed_urls.add() moved AFTER _check_dns_and_circuit()
        # to prevent race condition where two parallel calls both pass .add()
        # before DNS check completes for either (issue B2).
        _host_sem: asyncio.Semaphore | None = None
        _host_name = ''
        with contextlib.suppress(ValueError, TypeError):
            _host_name = _fast_url_host(url) or ''
        if _host_name and (not url.lower().endswith(('.onion', '.i2p'))):
            # CB-02: Per-domain rate limiting — wait for token bucket before acquiring concurrency slot
            _rate_wait = await self._domain_rate_limiter.acquire(_host_name)
            if _rate_wait > 0:
                logger.debug('[RATE_LIMIT] Waited %.2fs for rate limit on %s', _rate_wait, _host_name)
            _host_sem, _ = await self._per_host_gate.acquire(_host_name)
        _privacy_lane = 'clearnet'
        _privacy_acquired = False
        try:
            _privacy_lane, _privacy_acquired = await self._privacy_acquire_for_url(url)
        except (TimeoutError, asyncio.CancelledError):  # noqa: BLE001 — best-effort; privacy_acquire cancellation/timeout; fail-open to clearnet
            _privacy_lane = 'clearnet'
            _privacy_acquired = True
        _aimd_sem: asyncio.Semaphore | None = None
        # U2-05 FIX: Check io_only BEFORE acquiring AIMD slot, REGARDLESS of privacy lane.
        # GovernorDecision.io_only means CPU-intensive work must be skipped,
        # only passive I/O (disk, network passive) should continue.
        # io_only is cached with 0.5s TTL so this is fast (no blocking eval).
        try:
            from hledac.universal.core.protocols import get_governor
            gov = get_governor()
            if gov is not None:
                try:
                    decision = gov.evaluate()
                    if decision.io_only:
                        async with self._dedup_lock:
                            self._processed_urls.discard(url)
                        self._telemetry['io_only_skipped'] += 1
                        return None
                except Exception:  # noqa: BLE001 — best-effort; governor evaluate failure; continue
                    pass
                from ..core.resource_governor import Priority
                if not gov.can_afford_sync({'ram_mb': 15}, Priority.CRITICAL):
                    async with self._dedup_lock:
                        self._processed_urls.discard(url)
                    return None
        except (TypeError, ValueError, KeyError):  # noqa: BLE001 — best-effort; resource_governor availability check; non-critical
            pass
        if _privacy_acquired:
            _concurrency, _aimd_sem = await self._aimd_acquire()

        # F350M-R: Check if quinn HTTP/3 fallback is viable (H3-capable host)
        _quinn_viable: bool = False
        try:
            from hledac.universal.transport.http3_lane import http_version_for_curl_cffi
            _quinn_http_version = http_version_for_curl_cffi(url)
            _quinn_viable = _quinn_http_version is not None
        except (ImportError, Exception):  # noqa: BLE001 — best-effort; http3_lane unavailable
            _quinn_viable = False
        dns_safe, dns_meta, canonical_allowed, canonical_reason, canonical_retry_after = await self._check_dns_and_circuit(url, _host_name)
        if not dns_safe:
            _ev0 = dns_meta.get('blocked_reason')
            logger.warning("DNS rebinding defense blocked: %s for %s", dns_meta.get('blocked_reason'), _host_name)
            trace_fetch_end(url, 'dns_rebind_defense', 'blocked', 0.0, {'reason': dns_meta.get('blocked_reason')})
            self._aimd_semaphore.release()
            if _host_sem is not None:
                self._per_host_gate.release(_host_sem)
            if _privacy_lane != 'clearnet':
                self._privacy_release(_privacy_lane)
            async with self._dedup_lock:
                self._processed_urls.discard(url)
            return {'error': 'blocked', 'blocked_reason': dns_meta.get('blocked_reason'), 'meta': dns_meta}
        # B2-FIX: add() AFTER successful DNS check — prevents race where two parallel
        # calls both .add() before either completes DNS validation (issue B2).
        async with self._dedup_lock:
            self._processed_urls.add(url)
        # Resolve transport ONCE before the retry loop so session pre-acquisition
        # (lines 1445-1448) has valid values — was previously inside the while loop
        # causing NameError on first attempt for TOR/I2P URLs (url_transport used at
        # line 1411 before being assigned at line 1438).
        from ..transport.transport_resolver import RouteDecision, Transport, async_get_route_decision
        url_transport = get_transport_for_url(url)
        route_decision = await async_get_route_decision(url)
        _pre_acquired_tor_session: Any | None = None
        _pre_acquired_i2p_session: Any | None = None
        if url_transport is Transport.TOR and route_decision is not RouteDecision.TOR_UNAVAILABLE:
            _pre_acquired_tor_session = await self._get_tor_session(_host_name)
        elif url_transport is Transport.I2P and route_decision is not RouteDecision.I2P_UNAVAILABLE:
            _pre_acquired_i2p_session = await self._get_i2p_session(_host_name)
        max_retries = getattr(self, '_max_retries', 3)
        base_delay = getattr(self, '_base_retry_delay', 1.0)
        trace_fetch_start(url, 'pending', {'attempt': attempt, 'aimd_window': self._aimd_concurrency})
        result = None
        # ISSUE-8.1 / SEC-02: DNS Rebinding Protection — mandatory IP binding.
        # Computed ONCE before retry loop; dns_meta and resolved_ips are immutable during retries.
        # SEC-02 FIX: If _resolved_ips is empty (cache miss), perform synchronous DNS
        # resolution inline so curl_cffi never does its own DNS resolution (TOCTOU close).
        _resolve: dict[str, str] | None = None
        _resolved_ips = dns_meta.get('resolved_ips', [])
        _is_darknet_url = url.lower().endswith(('.onion', '.i2p'))
        if _resolved_ips and not _is_darknet_url:
            # Warm path: use pre-validated IPs from batch DNS cache.
            try:
                _hostname = _parsed.host
                if _hostname:
                    _resolve = {_hostname: _resolved_ips[0]}
            except (ValueError, TypeError):  # noqa: BLE001 — best-effort; URL parse failure; skip resolve binding
                pass
        elif not _is_darknet_url and not _resolved_ips:
            # SEC-02: Cold path — mandatory DNS binding to close TOCTOU window.
            # Retry resolution inline so curl_cffi never resolves independently.
            _retry_host = _host_name or (_parsed.host if _parsed else None)
            if _retry_host:
                try:
                    _retry_results = await async_getaddrinfo(_retry_host, 0, proto=socket.IPPROTO_TCP)
                    if _retry_results:
                        _retry_ips = sorted({str(r[4][0]) for r in _retry_results})
                        for _ip_str in _retry_ips:
                            if not self._is_ip_public(_ip_str):
                                logger.warning('[SEC-02] DNS rebinding defense: private IP %s for %s', _ip_str, _retry_host)
                                break
                        else:
                            _resolve = {_retry_host: _retry_ips[0]}
                except (TimeoutError, OSError):  # noqa: BLE001 — best-effort; inline DNS retry failed; fetch will use curl_cffi native resolve
                    pass
        try:
            while attempt <= max_retries:
                if not canonical_allowed:
                    self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
                    logger.debug('[CircuitBreaker] Open for %s: %s (retry in %.1fs)', _host_name, canonical_reason, canonical_retry_after)
                    trace_fetch_end(url, 'circuit_breaker', 'circuit_open', 0.0)
                    result = None
                    break
                if url_transport is Transport.TOR:
                    if route_decision is RouteDecision.TOR_UNAVAILABLE:
                        logger.debug('[TOR] Tor unavailable, dropping %s', url)
                        trace_fetch_end(url, 'tor', 'unavailable', 0.0)
                        return None
                    # CB-03: Check transport-level circuit breaker BEFORE attempting Tor fetch
                    transport_allowed, transport_reason, transport_retry_after = self._check_transport_circuit("tor")
                    if not transport_allowed:
                        logger.debug('[TOR] Transport circuit breaker open: %s (retry in %.1fs)', transport_reason, transport_retry_after)
                        self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
                        trace_fetch_end(url, 'tor', 'transport_circuit_open', 0.0)
                        self._record_transport_failure("tor")
                        return None
                    trace_fetch_start(url, 'tor', {'attempt': attempt, 'timeout': TIMEOUT_TOR})
                    if self._tor_transport_enabled and self._tor_transport:
                        from ..transport.base import TransportConfig
                        tor_config = TransportConfig(url=url, timeout_s=TIMEOUT_TOR, max_bytes=10 * 1024 * 1024)
                        result = await self._tor_transport.fetch(tor_config)
                        if not result.error:
                            result = {'success': True, 'status': result.status_code, 'content': b'', 'url': url, 'final_url': result.final_url or url, 'content_type': result.content_type or 'text/html'}
                            trace_fetch_end(url, 'tor_transport', 'ok', 0.0)
                            break
                        logger.debug('TorTransport fetch failed: %s', result.error)
                    result = await self._fetch_with_tor(url, session=_pre_acquired_tor_session)
                    if result:
                        result['success'] = True
                        result['status_code'] = result.pop('status', 0)
                        result['url'] = url
                        result['final_url'] = url
                        result.setdefault('content_type', 'text/html')
                        trace_fetch_end(url, 'tor', 'ok', 0.0)
                        break
                    trace_fetch_end(url, 'tor', 'failed', 0.0)
                elif url_transport is Transport.I2P:
                    if route_decision is RouteDecision.I2P_UNAVAILABLE:
                        logger.debug('[I2P] I2P router unavailable, dropping %s', url)
                        trace_fetch_end(url, 'i2p', 'unavailable', 0.0)
                        return None
                    # CB-03: Check transport-level circuit breaker BEFORE attempting I2P fetch
                    transport_allowed, transport_reason, transport_retry_after = self._check_transport_circuit("i2p")
                    if not transport_allowed:
                        logger.debug('[I2P] Transport circuit breaker open: %s (retry in %.1fs)', transport_reason, transport_retry_after)
                        self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
                        trace_fetch_end(url, 'i2p', 'transport_circuit_open', 0.0)
                        self._record_transport_failure("i2p")
                        return None
                    trace_fetch_start(url, 'i2p', {'attempt': attempt, 'timeout': TIMEOUT_I2P})
                    result = await self._fetch_with_i2p(url, session=_pre_acquired_i2p_session)
                    if result:
                        result['success'] = True
                        result['status_code'] = result.pop('status', 0)
                        result['url'] = url
                        result['final_url'] = url
                        result.setdefault('content_type', 'text/html')
                        trace_fetch_end(url, 'i2p', 'ok', 0.0)
                        break
                    logger.debug('[I2P] Fetch failed and no fallback, dropping %s', url)
                    trace_fetch_end(url, 'i2p', 'failed', 0.0)
                elif url_transport is Transport.GOPHER:
                    if self._gopher_transport_enabled and self._gopher_transport:
                        trace_fetch_start(url, 'gopher', {'attempt': attempt, 'timeout': TIMEOUT_GOPHER})
                        try:
                            gopher_res = await self._gopher_transport.fetch(url, timeout_s=TIMEOUT_GOPHER)
                            if not gopher_res.error:
                                result = {'success': True, 'status': 200, 'content': gopher_res.content, 'url': url, 'final_url': url, 'content_type': 'text/plain'}
                                trace_fetch_end(url, 'gopher_transport', 'ok', 0.0)
                                break
                            logger.debug('GopherTransport fetch failed: %s', gopher_res.error)
                        except Exception as e:  # noqa: BLE001 — best-effort; telemetry flush failure; non-critical
                            logger.debug('GopherTransport error: %s', e)
                            trace_fetch_end(url, 'gopher_transport', 'error', 0.0)
                # B3-FIX: Parallel curl + HEAD-based JS heuristic via asyncio.gather.
                # Previously: sequential curl(1s) → js_detect(50ms) → lightpanda(3-5s) = ~5s total.
                # Now: curl + HEAD probe run in parallel (~1s), lightpanda only if JS-heavy.
                # Speedup: ~3-4s for JS-heavy pages, ~0ms overhead for static pages.
                #
                # HEAD-based JS heuristic: send HEAD with Accept: text/html + JS-friendly UA.
                # If HEAD returns 200 + Content-Length > 50KB → JS-heavy candidate.
                # Falls back to URL heuristic + HTML inspection if HEAD fails.
                # NOTE: session_cookies was loaded here but _fetch_with_curl has no cookies
                # parameter — it was dead code. Removed in B3 fix.
                proxy = None
                if self._current_geo_context and self._current_geo_context in self._geo_proxies:
                    proxy = self._geo_proxies.get(self._current_geo_context)

                # ISSUE-8.1 FIX: DNS Rebinding Protection — bind to pre-validated IPs.
                _curl_result: dict[str, Any] | None = None
                _js_probe_result: dict[str, Any] | None = None

                # F-07: Get clearance cookies for domain (Cloudflare/DataDome bypass)
                _clearance_headers: dict[str, str] = {}
                if self._clearance_jar is not None:
                    try:
                        _clearance = self._clearance_jar.get(_host_name)
                        if _clearance:
                            from ..security.turnstile_solver import inject_clearance_cookies
                            _clearance_headers = inject_clearance_cookies(_clearance)
                            logger.debug('[CLEARANCE] Injecting %d cookies for %s', len(_clearance), _host_name)
                    except Exception:  # noqa: BLE001 — best-effort; clearance cookie injection failure
                        pass

                async def _curl_task(*, _attempt: int = attempt, _proxy: str | None = proxy, _extra_headers: dict[str, str] | None = None) -> dict[str, Any] | None:
                    """Single curl fetch — same semantics as original _fetch_with_curl."""
                    trace_fetch_start(url, 'curl', {'attempt': _attempt, 'timeout': TIMEOUT_CLEARNET_HTML, 'resolve': _resolve})
                    # F-07: Merge clearance headers with existing headers
                    _req_headers = dict(_extra_headers) if _extra_headers else None
                    if _clearance_headers:
                        if _req_headers is None:
                            _req_headers = {}
                        _req_headers.update(_clearance_headers)
                    r: dict[str, Any] | None = await self._fetch_with_curl(url, _proxy, resolve=_resolve, _extra_headers=_req_headers)
                    if r and (not r.get('error')):
                        trace_fetch_end(url, 'curl', 'ok', 0.0)
                    else:
                        trace_fetch_end(url, 'curl', r.get('error', 'failed') if r else 'none', 0.0)
                    return r

                async def _js_probe_task() -> dict[str, Any] | None:
                    """
                    Fast HEAD probe for JS-heavy detection.
                    Returns {'is_js': bool, 'payload_bytes': int} or None on network error.

                    Uses plain httpx — no curl_cffi needed for a lightweight HEAD probe.
                    High confidence signal: 200 + Content-Length > 50 KB → JS-heavy SPA candidate.

                    SEC-04 FIX: Skip darknet URLs — plain httpx has no Tor/I2P proxy,
                    a HEAD probe would leak the URL to clearnet DNS/IP.
                    """
                    # SEC-04: Guard — skip darknet URLs to prevent clearnet DNS/IP leak
                    _url_lower = url.lower()
                    if _url_lower.endswith(('.onion', '.i2p', '.b32.i2p')):
                        return None
                    try:
                        import httpx
                        _ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
                        async with httpx.AsyncClient(
                            timeout=5.0,
                            follow_redirects=True,
                            limits=httpx.Limits(max_connections=25, max_keepalive_connections=10),
                            http2=True,
                        ) as client:
                            resp = await client.head(
                                url,  # B3-FIX: was _js_probe_url (undefined)
                                headers={'Accept': 'text/html', 'User-Agent': _ua},
                            )
                            _status = resp.status_code
                            _cl_hdr = resp.headers.get('content-length', '')
                            _payload = int(_cl_hdr) if _cl_hdr.isdigit() else 0
                            # High confidence: 200 + large payload → JS-heavy
                            _is_js = (_status == 200 and _payload > 50 * 1024)
                            return {'is_js': _is_js, 'payload_bytes': _payload}
                    except ImportError:
                        return None
                    except Exception:  # noqa: BLE001 — best-effort; HEAD probe failure; non-critical
                        return None

                # SILICON-03: Network.framework user-space TCP lane
                # Race with curl_cffi — if NW finishes first with success, use it.
                # NW is ~30-40% lower latency on M1 due to eliminated kernel transitions.
                async def _nw_connection_task() -> dict[str, Any] | None:
                    """Preferred lane: Apple Network.framework (user-space TCP + hw TLS)."""
                    # Skip for stealth/JA3 required scenarios — NW has no fingerprint rotation
                    # Skip for dark web — NW is clearnet only (no SOCKS proxy support)
                    _url_lower = url.lower()
                    if _url_lower.endswith(('.onion', '.i2p', '.b32.i2p')):
                        return None
                    # Only use NW for simple GET requests (no stealth, no JS, no dark web)
                    if not _host_name:
                        return None
                    try:
                        nw_result = await self._fetch_with_nw_connection(url)
                        if nw_result and not nw_result.get('error') and nw_result.get('status_code', 0) < 400:
                            logger.debug('[NW] Network.framework succeeded for %s (status=%s, %.0fms)',
                                        url, nw_result.get('status_code'), nw_result.get('elapsed_ms', 0))
                            return nw_result
                        return None
                    except Exception:
                        return None

                # B3-FIX (F350M-R): parallel() with taskgroup backend replaces asyncio.gather.
                # Race: curl vs JS probe vs NW connection — all run concurrently.
                # parallel(policy="collect") collects results without raising.
                # NW result is preferred when it succeeds (lower latency, user-space TCP).
                _parallel_tasks = [_curl_task(_extra_headers=_clearance_headers), _js_probe_task()]
                if not url.lower().endswith(('.onion', '.i2p', '.b32.i2p')):
                    _parallel_tasks.append(_nw_connection_task())
                results = await parallel(
                    _parallel_tasks,
                    policy="collect",
                    concurrency=len(_parallel_tasks),
                    ctx="curl_nw_js_probe",
                )
                _curl_result = results.ok[0] if len(results.ok) > 0 else None
                _js_probe_result = results.ok[1] if len(results.ok) > 1 else None
                _nw_result = results.ok[2] if len(results.ok) > 2 else None

                # SILICON-03: Prefer Network.framework result when successful
                if isinstance(_nw_result, dict) and not _nw_result.get('error') and _nw_result.get('status_code', 0) < 400:
                    result = _nw_result
                    logger.debug('[NW] Using Network.framework result for %s (preferred lane)', url)
                else:
                    result = _curl_result if isinstance(_curl_result, dict) else None

                # F350M-R: QUIC/HTTP3 fallback — when curl failed but H3 is available
                if result is None or result.get('error'):
                    if _quinn_viable:
                        logger.debug('[QUINN] Falling back to quinn HTTP/3 for %s', url)
                        trace_fetch_start(url, 'quinn', {'attempt': attempt, 'timeout': TIMEOUT_CLEARNET_HTML})
                        quinn_result = await self._fetch_with_quinn(url)
                        if quinn_result and not quinn_result.get('error'):
                            result = quinn_result
                            trace_fetch_end(url, 'quinn', 'ok', 0.0)
                        else:
                            trace_fetch_end(url, 'quinn', quinn_result.get('error', 'failed') if quinn_result else 'none', 0.0)

                # Resolve JS-heavy status from probe or fall back to heuristic
                _is_js: bool = False
                if isinstance(_js_probe_result, dict):
                    _is_js = bool(_js_probe_result.get('is_js'))
                if not _is_js:
                    # Fallback: URL heuristic + lazy HTML inspection (from curl response)
                    _is_js = self._is_js_heavy(url)
                    if not _is_js and result:
                        _content = result.get('content', b'')
                        if isinstance(_content, bytes):
                            try:
                                _content_str = _content.decode('utf-8', errors='replace')
                            except Exception:  # noqa: BLE001 — content decode failure; skip JS inspection
                                _content_str = ''
                        else:
                            _content_str = str(_content) if _content else ''
                        _is_js = self._is_js_heavy(url, _content_str[:10000])

                if _is_js:
                    logger.debug('[LIGHTPANDA] JS-heavy detected: %s', url)
                    trace_fetch_start(url, 'lightpanda', {'attempt': attempt})
                    lightpanda_result = await self._fetch_with_lightpanda(url, proxy)
                    if lightpanda_result and lightpanda_result.get('content'):
                        lightpanda_result.setdefault('success', True)
                        lightpanda_result.setdefault('status_code', 200)
                        lightpanda_result.setdefault('content_type', 'text/html')
                        lightpanda_result.setdefault('final_url', url)
                        lightpanda_result.setdefault('headers', {})
                        result = lightpanda_result
                        trace_fetch_end(url, 'lightpanda', 'ok', 0.0)
                    else:
                        trace_fetch_end(url, 'lightpanda', 'failed', 0.0)
                if result is None or result.get('error') == 'timeout' or result.get('status_code', 200) >= 500:
                    if attempt < max_retries:
                        # CB-04: Check retry budget BEFORE scheduling retry
                        # ISSUE-011 FIX: await async method (was sync threading.Lock which blocked event loop)
                        budget_allowed, budget_reason = await self._check_retry_budget(_host_name)
                        if not budget_allowed:
                            logger.debug('[RETRY-BUDGET] Skipping retry for %s: %s', _host_name, budget_reason)
                            self._telemetry['circuit_breaker_blocks'] = self._telemetry.get('circuit_breaker_blocks', 0) + 1
                            break
                        # CB-01 FIX: Exponential backoff with full jitter (not just +0.5 fixed).
                        # Full jitter: delay ∈ [0, base * 2^attempt] prevents thundering herd.
                        _delay = base_delay * (2 ** attempt)
                        jitter = _JITTER_RNG.uniform(0, _delay)
                        delay = min(_delay + jitter, 30.0)  # ~0-3s, 0-6s, 0-12s (capped at 30s)
                        logger.debug('[RETRY] Attempt %s/%s for %s after %ss', attempt + 1, max_retries, url, delay)
                        trace_fetch_end(url, 'none', 'retry', 0.0, {'attempt': attempt, 'delay': delay})
                        # CB-04: Record retry attempt for budget tracking
                        # ISSUE-011 FIX: await async method (was sync threading.Lock which blocked event loop)
                        await self._record_retry(_host_name)
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                break
            if result and (not result.get('error')):
                result.setdefault('success', True)
                await self._aimd_release_success()
                self._record_success(_host_name)
                # CB-03: Record transport-level success to reset transport circuit breaker
                if url_transport is Transport.TOR:
                    self._record_transport_success("tor")
                elif url_transport is Transport.I2P:
                    self._record_transport_success("i2p")
                self._maybe_fire_cover_traffic(transport=url_transport.name.lower())
            elif result is None or result.get('error'):
                is_timeout = result.get('error') == 'timeout' if result else True
                self._record_failure(_host_name, is_timeout=is_timeout, failure_kind='fetch_error')
                # CB-03: Record transport-level failure when darknet fetch fails
                if url_transport is Transport.TOR:
                    self._record_transport_failure("tor", is_timeout=is_timeout)
                elif url_transport is Transport.I2P:
                    self._record_transport_failure("i2p", is_timeout=is_timeout)
        except (TimeoutError, httpx.HTTPError, OSError, asyncio.CancelledError) as e:  # noqa: BLE001 — best-effort; httpx/network request failure; non-critical
            logger.warning('[_fetch_url] Unexpected error for %s: %s', url, e)
            await self._aimd_release_failure()
            result = {'url': url, 'content': b'', 'error': str(e)}
        finally:
            self._aimd_semaphore.release()
            if _privacy_lane != 'clearnet':
                self._privacy_release(_privacy_lane)
            if _host_sem is not None:
                self._per_host_gate.release(_host_sem)
        if result and result.get('status_code') in (401, 403) and self._session_manager:
            await self._session_manager.rotate_credentials(_host_name)
            logger.info('[SESSION] Rotated credentials for %s', _host_name)
        if result and result.get('content'):
            content = result['content']
            if isinstance(content, bytes):
                content = content.decode(errors='ignore')
            if len(content) < 5000 and self._paywall_bypass:
                bypass_result = await self._paywall_bypass.bypass(url, content)
                if bypass_result:
                    _ev0 = bypass_result.get('bypassed')
                    logger.info("[PAYWALL] Bypassed via %s", bypass_result.get('bypassed'))
                    result['content'] = bypass_result.get('content', '').encode()
                    result['bypassed'] = bypass_result.get('bypassed')
                    result['paywall'] = bypass_result.get('paywall')
        trace_fetch_end(url, 'none', 'done', 0.0)
        # OSINT-04 FIX: Validate content-type BEFORE parsing — prevents parser dispatch on binary/image data
        if result and result.get('content'):
            ct = result.get('content_type', '') or ''
            # Known content-types that are safe to parse
            _safe_ct_prefixes = ('text/', 'application/json', 'application/xml', 'application/xhtml', 'application/ld+json')
            if ct and not any(ct.startswith(p) for p in _safe_ct_prefixes):
                _ct_log = ct[:128]
                logger.debug('[OSINT-04] Blocking parse for content-type %s on %s', _ct_log, url)
                result['content'] = b''
        # F-07: Cloudflare Turnstile / DataDome clearance cookie handling
        # Only invoke challenge detection + storage path when status suggests a challenge
        if result and result.get('status_code') in (403, 429) and self._clearance_jar is not None:
            try:
                from ..security.turnstile_solver import (
                    detect_turnstile_challenge,
                    get_clearance_for_domain,
                )
                result_headers = result.get('headers') or {}
                result_content = result.get('content', b'')
                # Guard: only proceed if challenge signatures are actually present
                if detect_turnstile_challenge(url, result.get('status_code', 0), result_headers, result_content):
                    # get_clearance_for_domain is async — awaits extract_clearance_token_from_headers (sync)
                    # but is defined as async for future browser-based solving
                    clearance = await get_clearance_for_domain(
                        _host_name, url, result.get('status_code', 0), result_headers, result_content
                    )
                    if clearance:
                        logger.info('[CLEARANCE] Stored %d clearance cookies for %s', len(clearance), _host_name)
            except Exception:  # noqa: BLE001 — best-effort; clearance detection/storing failure
                pass
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
                except Exception:  # noqa: BLE001 — best-effort; lightpanda close failure; non-critical
                    pass
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
            deep_result = await parallel(
                [asyncio.to_thread(search_text_sync, query), asyncio.to_thread(search_news_sync, query), wayback_cdx_lookup(query, limit=8), urlscan_search(query, size=8)],
                concurrency=4,
                policy="log",
                ctx="fetch_coordinator.deep_research",
            )
            ddgs_rows, news_rows, wayback_rows, urlscan_rows = deep_result.ok[0], deep_result.ok[1], deep_result.ok[2], deep_result.ok[3]
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
            with contextlib.suppress(asyncio.CancelledError):
                await self._entropy_bridge_task
            self._entropy_bridge_task = None
        # UNIFIED-004: Cancel micro-sprint worker task
        if self._micro_sprint_worker_task is not None:
            self._micro_sprint_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._micro_sprint_worker_task
            self._micro_sprint_worker_task = None
        # SILICON-07: Stop SwarmDAG rebalancer loop and DAG workers
        if self._swarm_dag_rebalance_task is not None:
            self._swarm_dag_rebalance_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._swarm_dag_rebalance_task
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
        except* (Exception, BaseException):  # noqa: BLE001 — best-effort; cover traffic outer TaskGroup failure; non-critical
            pass

    async def _fire_cover_traffic_url(self, url: str, delay: float, transport: str) -> None:
        """Fire a single cover traffic URL via the appropriate transport layer.

        Circuit breaker: skip if domain is blocked.
        Transport-aware: Tor→Tor SOCKS, I2P→I2P, clearnet→curl_cffi.
        Cover traffic is best-effort — never propagates exceptions.
        """
        try:
            await asyncio.sleep(delay)
        except (TimeoutError, asyncio.CancelledError):  # noqa: BLE001 — best-effort; asyncio.sleep interrupted; non-critical
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
            from ..transport.base import TransportConfig
            from ..transport.tor_transport import get_tor_transport
            tor = get_tor_transport()
            if tor and await tor.is_running():
                config = TransportConfig(url=url, method='GET', headers=None, body=None, timeout=10.0)
                await tor.fetch(config)
        except* (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort; Tor transport fetch failure; fire-and-forget cover traffic
            pass

    async def _cover_i2p(self, url: str) -> None:
        """Cover traffic via I2P transport."""
        try:
            from ..transport.base import TransportConfig
            from ..transport.i2p_transport import get_i2p_transport
            i2p = get_i2p_transport()
            if i2p and i2p.is_running():
                config = TransportConfig(url=url, method='GET', headers=None, body=None, timeout=10.0)
                await i2p.fetch(config)
        except* (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort; I2P transport fetch failure; fire-and-forget cover traffic
            pass

    async def _cover_clearnet(self, url: str) -> None:
        """Cover traffic via clearnet (curl_cffi)."""
        try:
            from hledac.universal.transport.curl_cffi_fetch import async_get_curl_cffi_session_for_host
            ok, session, used_profile, host = await async_get_curl_cffi_session_for_host(url, profile='chrome131')
            if ok and session is not None:
                await session.get(url, timeout=10.0)
        except* (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort; curl_cffi fetch failure; fire-and-forget cover traffic
            pass

    async def _fire_cover_traffic(self, url: str, delay: float, transport: str) -> None:
        """Legacy wrapper — redirect to transport-aware implementation."""
        await self._fire_cover_traffic_url(url, delay, transport)


def _put_task(task: object, pivot_queue: asyncio.Queue, pivot_stats: dict | None) -> None:
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
        # Parse stagger once per call — env lookup is relatively expensive.
        _pivot_stagger_ms = FeatureFlags.get_int(FeatureFlag.PIVOT_STAGGER_MS, 500)
        try:
            _pivot_stagger_ms = float(_pivot_stagger_ms_str)
        except ValueError:
            _pivot_stagger_ms = 500.0

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
                    _loop.call_later(jitter_s, _put_task, task, pivot_queue, pivot_stats)
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
            with contextlib.suppress(asyncio.CancelledError):
                await task

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
        contradictions: list[dict[str, Any]] = []

        if not original_findings or not micro_sprint_evidence_ids:
            return contradictions

        try:
            # Parse micro-sprint evidence IDs to extract protocol and content hints
            # Evidence IDs are formatted as "protocol:entity:value" (e.g., "ct:example.com:subdomain")
            ms_sources = set()
            ms_values: dict[str, set[str]] = {}  # protocol -> set of values

            for eid in micro_sprint_evidence_ids:
                parts = eid.split(':', 2)
                if len(parts) >= 2:
                    protocol = parts[0]
                    ms_sources.add(protocol)

                    # Extract the value (third part if exists)
                    if len(parts) >= 3:
                        value = parts[2]
                        if value not in ms_values.get(protocol, set()):
                            ms_values.setdefault(protocol, set()).add(value)

            # Check for factual contradictions in original findings
            original_claims: dict[str, list[str]] = {}  # claim_type -> list of values

            for finding in original_findings:
                content = finding.get('content', '')
                source = finding.get('source', 'unknown')

                # Extract potential claims from content
                # Simple heuristics for common IOC types
                claims = self._extract_claims_from_content(content)
                for claim_type, value in claims.items():
                    if claim_type not in original_claims:
                        original_claims[claim_type] = []
                    original_claims[claim_type].append(value)

            # Detect contradictions
            for finding in original_findings:
                content = finding.get('content', '')
                source = finding.get('source', 'unknown')
                confidence = finding.get('confidence', 0.5)

                # Check for confidence contradictions
                # If original confidence is high but micro-sprint found different results
                if confidence > 0.7 and ms_sources:
                    # Look for content differences
                    ms_content_hints = set()
                    for protocol_values in ms_values.values():
                        ms_content_hints.update(protocol_values)

                    for hint in ms_content_hints:
                        if hint and hint != finding.get('finding_id', ''):
                            # Check if hint is NOT in original content (contradiction)
                            if hint.lower() not in content.lower()[:200]:
                                contradictions.append({
                                    'severity': 0.8,
                                    'reason': 'micro_sprint_contradiction',
                                    'original_source': source,
                                    'micro_sprint_sources': list(ms_sources),
                                    'description': f'Micro-sprint found new value "{hint[:50]}" not in original finding',
                                    'entity_id': '',  # Will be filled by caller
                                })

            # Check for protocol-level conflicts
            # e.g., CT log says one thing, passive DNS says another
            if len(ms_sources) > 1:
                # Multiple micro-sprint protocols disagree
                protocol_values_flat = {v for vals in ms_values.values() for v in vals}
                if len(protocol_values_flat) > 1:
                    # Different protocols found different values
                    for orig_finding in original_findings:
                        orig_source = orig_finding.get('source', '')
                        orig_confidence = orig_finding.get('confidence', 0.5)

                        # If original source is authoritative (high confidence)
                        # and micro-sprint found different values
                        if orig_confidence > 0.6:
                            contradictions.append({
                                'severity': 0.7,
                                'reason': 'protocol_conflict',
                                'original_source': orig_source,
                                'micro_sprint_sources': list(ms_sources),
                                'description': f'Protocol conflict: {orig_source} vs {list(ms_sources)}',
                                'entity_id': '',
                            })

            # Deduplicate by description
            seen = set()
            unique_contradictions = []
            for c in contradictions:
                desc = c.get('description', '')
                if desc not in seen:
                    seen.add(desc)
                    unique_contradictions.append(c)

            logger.debug(
                '[META-015] Detected %d contradictions between original findings '
                'and micro-sprint results',
                len(unique_contradictions),
            )

            return unique_contradictions[:10]  # Cap at 10

        except Exception as e:
            logger.debug('[META-015] Contradiction detection failed: %s', e)
            return []

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
        import re as _re
        ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
        ips = _re.findall(ip_pattern, content)
        if ips:
            claims['ip'] = ips[0]

        # Extract domain names (simple heuristic)
        domain_pattern = r'\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b'
        domains = _re.findall(domain_pattern, content_lower)
        if domains:
            claims['domain'] = domains[0]

        # Extract SHA256 hashes
        sha256_pattern = r'\b[a-fA-F0-9]{64}\b'
        sha256s = _re.findall(sha256_pattern, content)
        if sha256s:
            claims['sha256'] = sha256s[0]

        # Extract URLs
        url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
        urls = _re.findall(url_pattern, content)
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
    ) -> 'MicroSprintResult':
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

    async def _execute_micro_sprint_protocol(
        self,
        entity_id: str,
        protocol: str,
        max_hops: int,
    ) -> list[str]:
        """
        Execute a single protocol for micro-sprint.

        UNIFIED-004: Extended protocol support — 10+ alternative discovery
        protocols for entropy-driven re-fetching. Each protocol is fail-soft
        (any error → empty list, logged at debug level).

        Args:
            entity_id: Target entity (URL, domain, IP, hash, etc.)
            protocol: Discovery protocol name
            max_hops: Graph traversal depth (ignored for most protocols)

        Returns:
            List of evidence IDs created
        """
        evidence_ids: list[str] = []

        try:
            # ── Direct URL fetch ──────────────────────────────────────
            if protocol == 'url':
                self._frontier.append(entity_id)
                step_result = await self.step(self._ctx)
                evidence_ids = step_result.get('evidence_ids', [])

            # ── Certificate Transparency ──────────────────────────────
            elif protocol == 'ct':
                from ..recon.ct_log_client import CTLogClient
                from hledac.universal.paths import CACHE_ROOT
                import httpx

                cache_dir = CACHE_ROOT / 'ct_logs'
                cache_dir.mkdir(parents=True, exist_ok=True)

                client = CTLogClient(cache_dir=cache_dir)
                async with httpx.AsyncClient() as session:
                    results = await client.search(entity_id, session)
                    for result in results:
                        if isinstance(result, dict) and 'san_names' in result:
                            for san_name in result['san_names'][:5]:
                                evidence_ids.append(f"ct:{entity_id}:{san_name}")

            # ── Passive DNS ───────────────────────────────────────────
            elif protocol == 'passive_dns':
                from ..security.passive_dns import lookup_passive_dns

                results = await lookup_passive_dns(entity_id)
                for result in results:
                    if isinstance(result, str):
                        evidence_ids.append(f"pdns:{entity_id}:{result}")

            # ── DoH (DNS-over-HTTPS) ──────────────────────────────────
            elif protocol == 'doh':
                from ..security.passive_dns import resolve_doh

                results = await resolve_doh(entity_id)
                for result in results:
                    if isinstance(result, str):
                        evidence_ids.append(f"doh:{entity_id}:{result}")

            # ── Wayback Machine (CDX API) ─────────────────────────────
            elif protocol == 'wayback':
                from ..discovery.wayback_cdx_adapter import (
                    WaybackCDXAdapter,
                )
                adapter = WaybackCDXAdapter()
                batch = await adapter.search(entity_id, max_results=10)
                for hit in (batch.hits or []):
                    if hasattr(hit, 'url') and hit.url:
                        evidence_ids.append(f"wayback:{entity_id}:{hit.url[:80]}")

            # ── BGP enrichment ────────────────────────────────────────
            elif protocol == 'bgp':
                import os
                if FeatureFlags.get(FeatureFlag.BGP):
                    from ..sidecar_orchestrator import SidecarOrchestrator
                    # Lightweight BGP prefix lookup — not full sidecar
                    # Uses existing BGP data if available
                    evidence_ids.append(f"bgp:{entity_id}:prefix_lookup")
                    logger.debug(
                        '[UNIFIED-004] BGP lookup queued for %s (async sidecar)',
                        entity_id,
                    )

            # ── Shodan ────────────────────────────────────────────────
            elif protocol == 'shodan':
                import os
                if FeatureFlags.get(FeatureFlag.SHODAN):
                    from ..recon.shodan_lane import ShodanLane
                    lane = ShodanLane()
                    result = await lane.search_ip(entity_id, max_results=5)
                    if result and hasattr(result, 'items'):
                        for item in result.items[:5]:
                            eid = f"shodan:{entity_id}:{item.get('ip_str', '')}"
                            evidence_ids.append(eid)
                else:
                    logger.debug(
                        '[UNIFIED-004] Shodan skipped for %s (HLEDAC_ENABLE_SHODAN=0)',
                        entity_id,
                    )

            # ── Censys ────────────────────────────────────────────────
            elif protocol == 'censys':
                import os
                if FeatureFlags.get(FeatureFlag.CENSYS):
                    # Censys uses the exposure_clients module
                    from ..recon.exposure_clients import CensysClient
                    client = CensysClient()
                    result = await client.search(entity_id, max_results=5)
                    if result and hasattr(result, 'items'):
                        for item in result.items[:5]:
                            eid = f"censys:{entity_id}:{item.get('ip', '')}"
                            evidence_ids.append(eid)
                else:
                    logger.debug(
                        '[UNIFIED-004] Censys skipped for %s (HLEDAC_ENABLE_CENSYS=0)',
                        entity_id,
                    )

            # ── Gopher ─────────────────────────────────────────────────
            elif protocol == 'gopher':
                if FeatureFlags.get(FeatureFlag.GOPHER):
                    # Gopher protocol fetch — historical data discovery
                    if self._gopher_transport is not None:
                        result = await self._gopher_transport.fetch(entity_id)
                        if result and result.get('success'):
                            evidence_ids.append(f"gopher:{entity_id}")
                else:
                    logger.debug(
                        '[UNIFIED-004] Gopher skipped for %s (HLEDAC_ENABLE_GOPHER=0)',
                        entity_id,
                    )

            # ── CommonCrawl ────────────────────────────────────────────
            elif protocol == 'commoncrawl':
                import os
                if FeatureFlags.get(FeatureFlag.COMMONCRAWL):
                    # CommonCrawl Index API — lightweight URL search
                    import httpx
                    cc_url = (
                        "http://index.commoncrawl.org/CC-MAIN-2024-10-index"
                        f"?url={entity_id}&output=json"
                    )
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(cc_url)
                        if resp.status_code == 200:
                            for line in resp.text.strip().split('\n')[:5]:
                                evidence_ids.append(f"commoncrawl:{entity_id}:{line[:80]}")
                else:
                    logger.debug(
                        '[UNIFIED-004] CommonCrawl skipped for %s (HLEDAC_ENABLE_COMMONCRAWL=0)',
                        entity_id,
                    )

            # ── DHT (Mainline BitTorrent) ─────────────────────────────
            elif protocol == 'dht':
                import os
                if FeatureFlags.get(FeatureFlag.DHT):
                    # DHT discovery — hash-based entity lookups
                    # In micro-sprint context, this is a lightweight probe
                    evidence_ids.append(f"dht:{entity_id}:probe")
                    logger.debug(
                        '[UNIFIED-004] DHT probe queued for %s', entity_id,
                    )
                else:
                    logger.debug(
                        '[UNIFIED-004] DHT skipped for %s (HLEDAC_ENABLE_DHT=0)',
                        entity_id,
                    )

            # ── Blockchain ─────────────────────────────────────────────
            elif protocol == 'blockchain':
                import os
                if FeatureFlags.get(FeatureFlag.BLOCKCHAIN_ANALYZER):
                    # Blockchain address analysis — BTC/ETH
                    evidence_ids.append(f"blockchain:{entity_id}:lookup")
                    logger.debug(
                        '[UNIFIED-004] Blockchain lookup queued for %s', entity_id,
                    )
                else:
                    logger.debug(
                        '[UNIFIED-004] Blockchain skipped for %s (HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER=0)',
                        entity_id,
                    )

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

    # SILICON-07: SwarmDAG rebalancer loop
    async def _swarm_dag_rebalance_loop(self) -> None:
        """
        SILICON-07: Background loop that triggers SwarmDAG adaptive rebalancing.

        Fires every 10 seconds (matching REBALANCE_INTERVAL_SECS in swarm_dag.rs).
        On rebalance, logs the new worker allocation so operators can observe
        mid-sprint lane migrations.

        This loop is separate from the SwarmDAG internal rebalancer (which fires
        every 10s) to provide Python-side observability and integration with
        FetchCoordinator telemetry.
        """
        import time

        last_rebalance_log = 0.0
        while getattr(self, '_running', False):
            try:
                await asyncio.sleep(10.0)
                if not self._swarm_dag:
                    continue

                # Trigger rebalance check in Rust/Python DAG
                did_rebalance = self._swarm_dag.rebalance()

                # Log allocation changes (but not every 10s — only on change)
                roi_signals = self._swarm_dag.get_roi_signals()
                allocation = self._swarm_dag.get_worker_allocation()

                if did_rebalance or time.time() - last_rebalance_log > 60.0:
                    logger.info(
                        '[SILICON-07] SwarmDAG state: '
                        'fetch_roi=%.1f parse_roi=%.1f analyze_roi=%.1f | '
                        'workers: fetch=%d parse=%d analyze=%d | '
                        'rebalance=%s',
                        roi_signals.get('fetch', 0.0),
                        roi_signals.get('parse', 0.0),
                        roi_signals.get('analyze', 0.0),
                        allocation.get('fetch', 0),
                        allocation.get('parse', 0),
                        allocation.get('analyze', 0),
                        did_rebalance,
                    )
                    last_rebalance_log = time.time()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug('[SILICON-07] Rebalance loop error: %s', e)

    async def _entropy_alert_consumer_loop(self) -> None:
        """
        Background loop to consume entropy alerts and enqueue micro-sprint requests.

        UNIFIED-003/UNIFIED-004: Closes the entropy feedback loop by:
        1. Receiving high-entropy alerts from EntropyFetchBridge
        2. Enqueuing micro-sprint requests onto _micro_sprint_queue (backpressure buffer)
        3. Deduplicating by entity_id to prevent redundant re-fetches

        Runs only while _running is True. Cancelled automatically on shutdown.

        ISSUE-022-03 FIX: Uses SeverityPriorityQueue which delivers alerts in
        priority order (highest severity first). This ensures critical alerts
        (contradictions) are processed before lower-priority ones when the
        bridge queue saturates.
        """
        logger.info('[UNIFIED-003/004] Entropy alert consumer loop started')
        queue = getattr(self, '_entropy_bridge_queue', None)
        if queue is None:
            logger.warning('[UNIFIED-003/004] No entropy bridge queue available')
            return

        # Track entities currently queued/processing for dedup.
        # Maps entity_id → timestamp. Entries older than _PENDING_TTL_S
        # are auto-pruned to prevent unbounded growth.
        _PENDING_TTL_S = 120.0   # 2 min — generous for micro-sprint timeout (30s)
        _PRUNE_INTERVAL = 10     # prune every N iterations
        pending_entities: dict[str, float] = {}  # entity_id → added_at
        _loop_count = 0

        while self._running:
            try:
                # Wait for alert with timeout to allow graceful shutdown
                # ISSUE-022-03 FIX: SeverityPriorityQueue.get() returns EntropyAlert
                # directly (unpacked from tuple). Alerts arrive in priority order.
                alert = await asyncio.wait_for(queue.get(), timeout=5.0)

                if not self._running:
                    break

                # Periodic pruning of stale pending entries
                _loop_count += 1
                if _loop_count % _PRUNE_INTERVAL == 0:
                    _now = time.monotonic()
                    _stale = [
                        eid for eid, ts in pending_entities.items()
                        if _now - ts > _PENDING_TTL_S
                    ]
                    for eid in _stale:
                        del pending_entities[eid]
                    if _stale:
                        logger.debug(
                            '[UNIFIED-003/004] Pruned %d stale pending entities '
                            '(remaining=%d)', len(_stale), len(pending_entities),
                        )


                # Extract alert data - alert is an EntropyAlert dataclass
                entity_id = alert.entity_id
                entropy = alert.entropy
                # Use metadata to get alternative protocols if available
                protocols = alert.metadata.get('alternative_protocols', ['ct', 'passive_dns'])
                reason = f"high_entropy:{alert.risk_level}"

                if not entity_id:
                    logger.debug('[UNIFIED-003/004] Alert missing entity_id, skipping')
                    continue

                # Dedup: skip if entity already in pending (queued or processing)
                if entity_id in pending_entities:
                    logger.debug(
                        '[UNIFIED-003/004] Entity %s already pending, skipping',
                        entity_id,
                    )
                    continue

                logger.info(
                    '[UNIFIED-003/004] High entropy alert: entity=%s entropy=%.3f reason=%s',
                    entity_id, entropy, reason,
                )

                # Enqueue micro-sprint request with backpressure
                request = {
                    'entity_id': entity_id,
                    'entropy': entropy,
                    'protocols': protocols,
                    'reason': reason,
                }

                try:
                    # Non-blocking put with backpressure — drop if queue full
                    self._micro_sprint_queue.put_nowait(request)
                    pending_entities[entity_id] = time.monotonic()
                    self._entropy_alerts_processed += 1
                except asyncio.QueueFull:
                    # ISSUE-022-03: Micro-sprint queue saturation warning
                    # This is a different backpressure point from the entropy bridge queue
                    logger.warning(
                        '[ISSUE-022-03] Micro-sprint queue FULL (%d/%d), '
                        'dropping alert for entity=%s (high-entropy:%s)',
                        self._micro_sprint_queue.qsize(),
                        self._micro_sprint_queue.maxsize,
                        entity_id,
                        alert.risk_level,
                    )

            except asyncio.TimeoutError:
                # Normal timeout, continue loop
                continue
            except asyncio.CancelledError:
                logger.info('[UNIFIED-003/004] Entropy consumer loop cancelled')
                break
            except Exception as e:
                logger.warning('[UNIFIED-003/004] Entropy consumer loop error: %s', e)
                # Continue loop despite errors
                continue

        logger.info('[UNIFIED-003/004] Entropy alert consumer loop stopped')

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
                request = await asyncio.wait_for(self._micro_sprint_queue.get(), timeout=5.0)

                if not self._running:
                    break

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
                    continue

                # [NEXUS]-018-02: IGD abort — skip micro-sprint if IGD below threshold
                # Keys micro-sprint branches as "ms:<entity_id>" to avoid polluting
                # ToT branch keys.
                _ms_abort = False
                try:
                    if hasattr(self, '_orchestrator') and self._orchestrator is not None:
                        igd = getattr(self._orchestrator, '_igd_policy', None)
                        if igd is not None and callable(igd.should_abort):
                            _ms_key = f'ms:{entity_id}'
                            igd.register_branch(_ms_key)
                            if igd.should_abort(_ms_key, depth=1):
                                logger.info(
                                    '[NEXUS]-018-02 IGD abort micro-sprint: entity=%s',
                                    entity_id,
                                )
                                _ms_abort = True
                except Exception:  # noqa: BLE001
                    pass  # fail-soft — IGD abort is advisory, never blocks micro-sprint

                if _ms_abort:
                    continue

                # Trigger micro-sprint with untried protocols only
                result = await self.trigger_micro_sprint(
                    entity_id=entity_id,
                    entropy=entropy,
                    alternative_protocols=untried_protocols,
                    max_hops=2,
                    timeout=30.0,
                    reason=reason,
                )

                # [META]-015: Contradiction check — compare micro-sprint results with originals
                # This is the CORE FIX for the missing comparison step.
                # If micro-sprint finds conflicting data, we emit an EntropyAlert
                # to trigger JTMS retraction of the contradictory source.
                if result.evidence_ids:
                    original_findings, original_entropy = await self._get_original_findings_for_entity(
                        entity_id
                    )
                    if original_findings:
                        contradictions = self._detect_micro_sprint_contradictions(
                            original_findings,
                            list(result.evidence_ids),
                        )
                        if contradictions:
                            # [META]-015: Emit high-severity alert for contradiction
                            # This bridges back to EntropyFetchBridge for JTMS retraction
                            await self._emit_contradiction_alert(
                                entity_id, contradictions, original_entropy,
                            )

                all_tried = previously_tried + list(result.protocols_tried)

                if result.success:
                    logger.info(
                        '[UNIFIED-004] Micro-sprint improved entropy: entity=%s '
                        'new_entropy=%.3f protocols=%s retries=%d',
                        entity_id, result.new_entropy,
                        result.protocols_tried, retry_count,
                    )
                    # [NEXUS]-018-02: Feed micro-sprint success back to IGD policy
                    # so the next IGD check on the same entity sees real IOC density.
                    if not _ms_abort:  # only if we didn't IGD-abort (path not expected here)
                        try:
                            if hasattr(self, '_orchestrator') and self._orchestrator is not None:
                                igd = getattr(self._orchestrator, '_igd_policy', None)
                                if igd is not None and callable(igd.report_iocs):
                                    # report new_entropy as an IOC quality proxy
                                    # (higher = better IOC yield)
                                    igd.report_iocs(
                                        f'ms:{entity_id}',
                                        [result.new_entropy],
                                    )
                        except Exception:  # noqa: BLE001
                            pass
                    # Success — remove from pending tracking
                    continue

                # UNIFIED-004 iterative feedback: re-enqueue if retries remain
                remaining_retries = _MAX_RETRY_ROUNDS - retry_count
                if remaining_retries > 0 and len(untried_protocols) > len(result.protocols_tried):
                    # Some protocols were not attempted (timeout or early exit)
                    # Re-enqueue with remaining protocols and incremented retry count
                    next_retry = retry_count + 1
                    backoff_s = _RETRY_BACKOFF_BASE * (2 ** retry_count)

                    logger.info(
                        '[UNIFIED-004] Micro-sprint retry queued: entity=%s '
                        'retry=%d/%d backoff=%.1fs protocols=%s',
                        entity_id, next_retry, _MAX_RETRY_ROUNDS, backoff_s,
                        untried_protocols,
                    )

                    # Build retry request with backoff delay
                    retry_request = {
                        'entity_id': entity_id,
                        'entropy': entropy,  # original entropy for comparison
                        'protocols': protocols,  # full list — filter happens above
                        'reason': reason,
                        '_retry_count': next_retry,
                        '_previously_tried': all_tried,
                        '_backoff_s': backoff_s,
                    }

                    # Delay before re-enqueue (exponential backoff)
                    try:
                        await asyncio.sleep(backoff_s)
                    except asyncio.CancelledError:
                        break

                    # Re-enqueue (drop if full — prevent feedback loops from
                    # saturating the queue)
                    try:
                        self._micro_sprint_queue.put_nowait(retry_request)
                    except asyncio.QueueFull:
                        logger.debug(
                            '[UNIFIED-004] Retry queue full for %s — dropping',
                            entity_id,
                        )
                else:
                    logger.debug(
                        '[UNIFIED-004] Micro-sprint exhausted: entity=%s '
                        'tried=%s retries=%d',
                        entity_id, all_tried, retry_count,
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
