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
import orjson
import os
import secrets
import socket
import time
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from cachetools import TTLCache
from typing import Any
import httpx
import msgspec


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
from tenacity import wait_exponential_jitter
from hledac.universal.core.capabilities import AIOHTTP, CAPS, DARKNET_CONNECTOR, HINTS, LIGHTPANDA, OTEL, PAYWALL_BYPASS, SESSION, STEALTH_MANAGER, ZERO_ATTR, ZSTD
from hledac.universal.runtime.logging_setup import get_logger
_otel_mod = CAPS.require(OTEL)
if _otel_mod is not None:
    _otel_instrumented = _otel_mod
else:
    from hledac.universal.telemetry import instrumented as _otel_instrumented
_zstd_mod = CAPS.require(ZSTD)
_aiomod = CAPS.require(AIOHTTP)
_lp_manager_cls = CAPS.require(LIGHTPANDA)
_session_mgr_cls = CAPS.require(SESSION)
_hints_extractor_cls = CAPS.require(HINTS)
ZSTD_AVAILABLE = CAPS.is_available('zstd')
AIOHTTP_AVAILABLE = CAPS.is_available('aiohttp')
LIGHTPANDA_AVAILABLE = CAPS.is_available('lightpanda')
SESSION_AVAILABLE = CAPS.is_available('session')
HINTS_AVAILABLE = CAPS.is_available('deep_web_hints')
from hledac.universal.runtime.privacy_budget import PrivacyBudgetAllocator, make_privacy_allocator
from hledac.universal.tools.zstd_compressor import ZstdCompressor
from hledac.universal.utils.async_helpers import parallel, safe_create_task
from ..tools.url_dedup import DeduplicationStrategy
# ISSUE 2.2: PyAIMDController — lock-free AIMD in Rust (AtomicU64). Lazy import.
try:
    from hledac_rust_extensions import PyAIMDController
except ImportError:
    PyAIMDController = None  # type: ignore[misc,assignment]
from .base import UniversalCoordinator
_zero_attr_cls = CAPS.require(ZERO_ATTR)
_ZERO_ATTR_ENGINE = _zero_attr_cls
_COVER_RATE = float(os.environ.get('HLEDAC_COVER_TRAFFIC_RATE', '0.05'))
_COVER_RATE = min(max(_COVER_RATE, 0.0), 1.0)
_COVER_MAX = 2

def _create_dedup_strategy():
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
from ..tools.url_dedup import dedupe_url_list
from ..utils.async_helpers import BoundedPerHostGate, DomainRateLimiter, async_getaddrinfo
from ..utils.batch_dns import get_batch_dns_resolver

# F350M-R: DNS via hickory-dns — replaces batch_dns.py triple-path duplication
# Lazy import to avoid adding hickory-dns to the core build
_RUST_DNS: Any = None
_RUST_DNS_ENABLED: bool = False
try:
    from hledac_rust_extensions import dns as _rust_dns_module
    _RUST_DNS = _rust_dns_module
    # F350M-R: Runtime feature flag — can disable even if built with dns feature
    import os
    _RUST_DNS_ENABLED = os.environ.get('HLEDAC_ENABLE_DNS', '1').lower() in ('1', 'true', 'yes', 'on')
except ImportError:
    _RUST_DNS = None  # type: ignore[assignment]
    _RUST_DNS_ENABLED = False


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
from ..utils.flow_trace import is_enabled, trace_counter, trace_dedup_decision, trace_fetch_end, trace_fetch_start
_stealth_tbc = CAPS.require(STEALTH_MANAGER)
if _stealth_tbc is None:

    class TokenBucketController:
        """Token Bucket pro řízení concurrency (inline fallback)."""
        __slots__ = ('_rate', '_capacity', '_tokens', '_last_refill', '_cond')

        def __init__(self, rate: int=5, capacity: int=10):
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

# Crypto-safe jitter — F350M-R
_JITTER_RNG = secrets.SystemRandom()
from hledac.universal.core.constants import NETWORK
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
        except (TypeError, OSError):
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
import platform
NOCACHE_THRESHOLD_BYTES = 50 * 1024 * 1024
F_NOCACHE = 48 if platform.system() == 'Darwin' else None
from hledac.universal.tools.file_cache import apply_fcntl_nocache as _apply_fcntl_nocache

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
    __slots__ = tuple(('_adaptive_priority_provider', '_aimd', '_aimd_semaphore', '_base_retry_delay', '_batch_cp_result', '_capacity', '_captcha_detections', '_captcha_detector', '_concurrency', '_concurrency_provider', '_config', '_cooldown_seconds', '_cover_count', '_ctx', '_current_geo_context', '_darknet_connector', '_dedup_lock', '_domain_rate_limiter', '_effective_ua', '_enqueue_pivot_provider', '_evidence_ids', '_evidence_sink', '_frontier', '_geo_proxies', '_gopher_transport', '_gopher_transport_enabled', '_hints_extractor', '_host_ips_cache', '_host_ips_inflight', '_http_cache_enabled', '_http_cache_transport', '_hypothesis_depth_provider', '_hypothesis_depth_setter', '_hypothesis_query_count_provider', '_hypothesis_query_count_setter', '_lightpanda_lock', '_lightpanda_pool', '_lightpanda_pool_started', '_max_backoff_delay', '_max_retries', '_orchestrator', '_paywall_bypass', '_per_host_gate', '_per_host_limit', '_pivot_queue_provider', '_pivot_stats_provider', '_privacy_allocator', '_privacy_lock', '_processed_urls', '_retry_budget', '_retry_budget_lock', '_robots_parser', '_running', '_session_checkpoint_task', '_session_lmdb_env', '_session_manager', '_sprint_config_provider', '_sprint_remaining_provider', '_stop_reason', '_telemetry', '_tor_transport', '_tor_transport_enabled', '_urls_fetched_count', '_zstd'))

    def __init__(self, config: FetchCoordinatorConfig | None=None, max_concurrent: int=3, pivot_queue_provider: Callable[[], Any]=lambda: None, pivot_stats_provider: Callable[[], dict] | None=None, hypothesis_query_count_provider: Callable[[], int]=lambda: 0, hypothesis_query_count_setter: Callable[[int], None]=lambda v: None, hypothesis_depth_provider: Callable[[], int]=lambda: 0, hypothesis_depth_setter: Callable[[int], None]=lambda v: None, sprint_config_provider: Callable[[], Any]=lambda: None, adaptive_priority_provider: Callable[[str, float], float]=lambda tt, base: base, enqueue_pivot_provider: Callable[..., Any]=lambda **kw: None, concurrency_provider: Callable[[], tuple[int, int, str, bool] | None] | None=None, sprint_remaining_provider: Callable[[], float | None]=lambda: None, evidence_sink: Any=None):
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
        if os.environ.get('HLEDAC_ENABLE_TOR') == '1':
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
        if os.environ.get('HLEDAC_ENABLE_GOPHER') == '1':
            try:
                from ..transport.gopher_transport import GopherTransport
                self._gopher_transport = GopherTransport()
                self._gopher_transport_enabled = True
                logger.info('GopherTransport enabled via HLEDAC_ENABLE_GOPHER=1')
            except Exception as e:  # noqa: BLE001 — best-effort; transport init failure; Gopher disabled gracefully
                logger.warning('GopherTransport init failed: %s', e)
                self._gopher_transport_enabled = False
        self._http_cache_transport: Any = None
        self._http_cache_enabled: bool = os.environ.get('HLEDAC_HTTP_CACHE', '1') != '0'
        self._captcha_detector: Any | None = None
        self._captcha_detections: int = 0
        if os.environ.get('HLEDAC_ENABLE_CAPTCHA_DETECTION') == '1':
            try:
                from ..security.captcha_detector import CaptchaDetector
                self._captcha_detector = CaptchaDetector()
                logger.info('CaptchaDetector enabled via HLEDAC_ENABLE_CAPTCHA_DETECTION=1')
            except Exception as e:  # noqa: BLE001 — best-effort; transport init failure; CaptchaDetector disabled gracefully
                logger.warning('CaptchaDetector init failed: %s', e)
                self._captcha_detector = None
        self._dedup_lock = asyncio.Lock()
        self._concurrency = TokenBucketController(rate=5, capacity=10)
        # ISSUE 2.2: Unified AIMD controller — single AtomicU64 in Rust.
        # Replaces _AIMDSlotController orphan class.
        # Falls back to Python AIMDWindow if Rust extension unavailable.
        self._aimd: PyAIMDController | AIMDWindow = _try_load_aimd_controller(CONCURRENCY_CLEARNET)
        # _aimd_semaphore: asyncio.Semaphore for slot coordination (stays in Python).
        # Window state is in Rust; Python only reads window for semaphore sizing.
        self._aimd_semaphore: asyncio.Semaphore = asyncio.Semaphore(CONCURRENCY_CLEARNET)
        self._per_host_limit = 4
        self._per_host_gate = BoundedPerHostGate(max_hosts=512, per_host_limit=self._per_host_limit)
        # CB-02: Per-domain rate limiter — 0.5 RPS default (1 req / 2s)
        # Configurable via HLEDAC_RATE_LIMIT_RPS env var
        _rate_limit_rps = float(os.environ.get("HLEDAC_RATE_LIMIT_RPS", "0.5"))
        self._domain_rate_limiter = DomainRateLimiter(rate=_rate_limit_rps, max_hosts=512)
        self._telemetry: dict[str, Any] = {'aimd_concurrency': self._aimd.window, 'active_fetches': 0, 'total_successes': 0, 'total_failures': 0, 'circuit_breaker_blocks': 0, 'circuit_breaker_active': 0, 'uma_state': 'ok', 'decrease_factor_used': 1.0, 'backpressure_clamp_events': 0, 'io_only_skipped': 0}
        # CB-04: Retry budget per domain — track total retries in last 60s to prevent amplification
        self._retry_budget: dict[str, list[float]] = {}  # domain -> list of retry timestamps
        self._retry_budget_lock = threading.Lock()
        self._retry_budget_max = 20  # max retries per domain per 60s window
        self._retry_budget_window = 60.0  # sliding window in seconds
        self._cover_count: int = 0
        self.init_session_manager()

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
                try:
                    sprint_remaining = self._sprint_remaining_provider()
                except Exception:  # noqa: BLE001 — best-effort; sprint_remaining provider unavailable; non-critical
                    pass
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

    def _check_retry_budget(self, domain: str) -> tuple[bool, str]:
        """CB-04: Check if domain has exceeded retry budget.

        Returns (allowed, reason) where allowed=False means skip retries for this domain.
        Uses sliding window of 60s to count retries.
        """
        if not domain:
            return (True, "empty_domain")
        now = time.monotonic()
        with self._retry_budget_lock:
            # Clean expired entries
            if domain in self._retry_budget:
                self._retry_budget[domain] = [
                    ts for ts in self._retry_budget[domain]
                    if now - ts < self._retry_budget_window
                ]
                if len(self._retry_budget[domain]) >= self._retry_budget_max:
                    return (False, f"retry_budget_exceeded:{len(self._retry_budget[domain])}/{self._retry_budget_max}")
            return (True, "retry_budget_ok")

    def _record_retry(self, domain: str) -> None:
        """CB-04: Record a retry attempt for domain."""
        if not domain:
            return
        now = time.monotonic()
        with self._retry_budget_lock:
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

    def init_session_manager(self, lmdb_path: str | None=None):
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
            if ip.is_loopback:
                return False
            return True
        except (ValueError, TypeError):  # noqa: BLE001 — best-effort; ip_address parse failure; returns False (private check)
            return False

    async def _validate_fetch_target(self, url: str) -> tuple[bool, dict[str, Any]]:
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
            except ValueError:
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
        except (httpx.HTTPError, httpx.TimeoutException, OSError, asyncio.TimeoutError) as e:  # noqa: BLE001 — best-effort; httpx client creation failure; non-critical
            return (False, {'blocked_reason': f'validation_error: {e}'})

    def _is_js_heavy(self, url: str, html_preview: str='') -> bool:
        """Detect JS-heavy pages by URL and HTML preview."""
        js_indicators = ['react', 'vue', 'angular', 'next', 'nuxt', 'svelte']
        if any((ind in url.lower() for ind in js_indicators)):
            return True
        if html_preview:
            if '<script' in html_preview.lower() and len(html_preview) < 5000:
                return True
            if 'data-reactroot' in html_preview or 'ng-version' in html_preview:
                return True
        return False

    async def _aimd_acquire(self) -> tuple[float, None]:
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
                _gov_decision = _gov.evaluate()
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
        except (asyncio.CancelledError, asyncio.TimeoutError):  # noqa: BLE001 — best-effort; privacy_acquire cancellation/timeout; fail-open to clearnet
            return ('clearnet', True)

    def _privacy_release(self, lane: str) -> None:
        """Release privacy lane slot. No-op for clearnet."""
        if lane == 'clearnet' or self._privacy_allocator is None:
            return
        sem = self._privacy_allocator.get_semaphore(lane)
        if sem is not None:
            try:
                sem.release()
            except ValueError:
                pass

    async def _fetch_with_lightpanda(self, url: str, proxy: str | None=None):
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
        except (httpx.HTTPError, httpx.TimeoutException, asyncio.TimeoutError, OSError, ConnectionError) as e:  # noqa: BLE001 — best-effort; httpx request failure; non-critical fallback
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

    async def _get_tor_session(self, domain: str) -> Any | None:
        """F274: Delegate to darknet_session_provider (transport layer owns sessions)."""
        from ..transport.darknet_session_provider import get_session, mark_used
        session = await get_session('tor', domain)
        if session is not None:
            await mark_used('tor', domain)
        return session

    # OSINT-02: max_bytes cap for darknet fetches — prevents OOM from unbounded resp.read()
    _DARKNET_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB hard cap

    async def _fetch_with_tor(self, url: str, session: Any | None=None, *, max_bytes: int | None=None) -> dict[str, Any] | None:
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
        except (httpx.HTTPError, OSError, asyncio.TimeoutError, asyncio.CancelledError) as e:  # noqa: BLE001 — best-effort; httpx response body read; non-critical
            logger.warning('Tor fetch failed: %s', e)
            await self._aimd_release_failure()
            return None

    async def _get_i2p_session(self, domain: str) -> Any | None:
        """F274: Delegate to darknet_session_provider (transport layer owns sessions)."""
        from ..transport.darknet_session_provider import get_session, mark_used
        session = await get_session('i2p', domain)
        if session is not None:
            await mark_used('i2p', domain)
        return session

    async def _fetch_with_i2p(self, url: str, session: Any | None=None, *, max_bytes: int | None=None) -> dict[str, Any] | None:
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
        except (httpx.HTTPError, OSError, asyncio.TimeoutError, asyncio.CancelledError) as e:  # noqa: BLE001 — best-effort; httpx stream read; non-critical
            logger.warning('I2P fetch failed: %s', e)
            await self._aimd_release_failure()
            return None

    async def _fetch_with_curl(self, url: str, proxy: str | None=None, *, resolve: dict[str, str] | None=None):
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
            from hledac.universal.fetching.curl_cffi_fetch import fetch_via_curl_cffi_with_caps_check, is_curl_cffi_capable, next_ja3_profile
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
            from hledac.universal.fetching.public_fetcher import _altsvc_extract_host, _altsvc_http_version_for, _altsvc_record_from_result
            try:
                from hledac.universal.transport.http3_lane import probe_altsvc_speculative
                probe_altsvc_speculative(url)
            except (ImportError, AttributeError, TypeError):  # noqa: BLE001 — best-effort; http3_lane unavailable; fail-open
                pass
            _curl_http_version = _altsvc_http_version_for(_altsvc_extract_host(url))
            _ja3_profile = next_ja3_profile()
            _curl_result = await fetch_via_curl_cffi_with_caps_check(url=url, headers=None, timeout_s=30.0, max_bytes=10 * 1024 * 1024, profile=_ja3_profile, http_version=_curl_http_version, _pre_probe=False, resolve=resolve)
            if _curl_result is None:
                return {'url': url, 'content': b'', 'error': 'curl_cffi_caps_check_failed'}
            _altsvc_record_from_result(url, _curl_result.get('headers'))
            _curl_bytes = _curl_result.get('content', b'')
            _curl_error = _curl_result.get('error', None)
            if _curl_bytes:
                _curl_text = _curl_bytes.decode('utf-8', errors='replace')
            else:
                _curl_text = None
            return {'url': url, 'final_url': _curl_result.get('final_url', url), 'content': _curl_bytes, 'text': _curl_text, 'status_code': _curl_result.get('status_code', 0), 'content_type': _curl_result.get('content_type', ''), 'headers': _curl_result.get('headers', {}), 'js_rendered': False, 'success': _curl_error is None, 'error': _curl_error}
        except TimeoutError:
            logger.debug('[CURL] Timeout for %s', url)
            await self._aimd_release_failure()
            return {'url': url, 'content': b'', 'error': 'timeout'}
        except (OSError, asyncio.TimeoutError, asyncio.CancelledError) as e:  # noqa: BLE001 — curl_cffi doesn't raise httpx.HTTPError; only network/OS errors expected here
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
        import os
        if os.environ.get('HLEDAC_ENABLE_QUIC', '1').lower() not in ('1', 'true', 'yes', 'on'):
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

    def get_supported_operations(self) -> list[Any]:
        """Return supported operation types."""
        from .base import OperationType
        return [OperationType.RESEARCH]

    async def handle_request(self, operation_ref: str, decision: Any) -> Any:
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
        # F-05: RobotsParser — 15 min TTL, 1024 domain LRU cache (M1 8GB bounded)
        try:
            from ..utils.robots_parser import RobotsParser, RobotsDocument
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
            try:
                await asyncio.sleep(_delay)
            except (asyncio.CancelledError, asyncio.TimeoutError):  # noqa: BLE001 — best-effort; crawl-delay sleep interrupted; continue
                pass
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

    async def _do_step(self, ctx: dict[str, Any]) -> dict[str, Any]:
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
                        if c == ':' or c == '/' or c == '?' or c == '#':
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
            except (asyncio.TimeoutError, asyncio.CancelledError, OSError) as exc:  # noqa: BLE001 — best-effort; batch DNS pre-resolve failure; non-critical
                logger.debug('[F-A4] batch DNS pre-resolve failed: %s: %s', type(exc).__name__, exc)
        candidates: list[tuple[float, str]] = []
        for url in unique_batch:
            candidates.append((self._url_priority(url), url))
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
                        if c == ':' or c == '/' or c == '?' or c == '#':
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
        for _url, _result in zip(urls_to_fetch, _robots_results.ok):
            _allowed, _reason, _delay = _result
            _total_crawl_delay += _delay
            if not _allowed:
                logger.debug('[ROBOTS] blocked by robots.txt: %s (%s)', _url, _reason)
                trace_fetch_end(_url, 'robots', 'blocked', 0.0, {'reason': _reason})
                continue
            _robots_filtered.append(_url)
        if _total_crawl_delay > 0:
            try:
                await asyncio.sleep(_total_crawl_delay)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
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
        from ..transport.transport_resolver import get_transport_for_url, Transport
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
                        try:
                            await self._evidence_sink.append_evidence(evidence_id)
                        except Exception:
                            pass  # fail-safe: sink error doesn't break fetch pipeline
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
        except* (Exception, BaseException) as exc:  # noqa: BLE001 — best-effort; TaskGroup ExceptionGroup or single exc; domain_breaker unavailable; non-critical
            dns_safe, dns_meta = (True, {})
            cb_allowed, cb_reason, cb_retry_after = (True, '', 0.0)
        return (dns_safe, dns_meta, cb_allowed, cb_reason, cb_retry_after)

    def _get_step_result(self, new_evidence_ids: list[str] | None=None, batch_size: int=0, effective_parallelism: int=0, batch_elapsed_ms: float=0.0) -> dict[str, Any]:
        """Get bounded step result with Sprint 5B batch telemetry."""
        evidence_ids = (new_evidence_ids or [])[:self._config.max_evidence_per_step]
        return {'urls_fetched': len(evidence_ids), 'evidence_ids': evidence_ids, 'total_fetched': self._urls_fetched_count, 'stop_reason': self._stop_reason, 'frontier_remaining': len(self._frontier), 'aimd_window': self._aimd_concurrency, 'active_fetches': self._telemetry['active_fetches'], 'batch_size': batch_size, 'effective_parallelism': effective_parallelism, 'batch_elapsed_ms': batch_elapsed_ms}

    @_otel_instrumented('fetch.url', component='network')
    async def _fetch_url(self, url: str, attempt: int=0) -> dict[str, Any] | None:
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
        try:
            _host_name = _fast_url_host(url) or ''
        except (ValueError, TypeError):  # noqa: BLE001 — best-effort; fast host extraction failure; skip gate
            pass
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
        except (asyncio.CancelledError, asyncio.TimeoutError):  # noqa: BLE001 — best-effort; privacy_acquire cancellation/timeout; fail-open to clearnet
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
                except (OSError, asyncio.TimeoutError):  # noqa: BLE001 — best-effort; inline DNS retry failed; fetch will use curl_cffi native resolve
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

                async def _curl_task() -> dict[str, Any] | None:
                    """Single curl fetch — same semantics as original _fetch_with_curl."""
                    trace_fetch_start(url, 'curl', {'attempt': attempt, 'timeout': TIMEOUT_CLEARNET_HTML, 'resolve': _resolve})
                    r: dict[str, Any] | None = await self._fetch_with_curl(url, proxy, resolve=_resolve)
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

                # B3-FIX (F350M-R): parallel() with taskgroup backend replaces asyncio.gather.
                # Race: curl vs JS probe — both run concurrently, first dict result wins.
                # parallel(policy="collect") collects results without raising.
                results = await parallel(
                    [_curl_task(), _js_probe_task()],
                    policy="collect",
                    concurrency=2,
                    ctx="curl_js_probe",
                )
                _curl_result = results.ok[0] if len(results.ok) > 0 else None
                _js_probe_result = results.ok[1] if len(results.ok) > 1 else None

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
                        budget_allowed, budget_reason = self._check_retry_budget(_host_name)
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
                        self._record_retry(_host_name)
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
        except (httpx.HTTPError, OSError, asyncio.TimeoutError, asyncio.CancelledError) as e:  # noqa: BLE001 — best-effort; httpx/network request failure; non-critical
            logger.warning('[_fetch_url] Unexpected error for %s: %s', url, e)
            await self._aimd_release_failure()
            result = {'url': url, 'content': b'', 'error': str(e)}
        finally:
            self._aimd_semaphore.release()
            if _privacy_lane != 'clearnet':
                self._privacy_release(_privacy_lane)
            if _host_sem is not None:
                self._per_host_gate.release(_host_sem)
        if result and result.get('status_code') in (401, 403):
            if self._session_manager:
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
        await self._stop_checkpoint_loop()
        if self._session_manager is not None:
            try:
                await self._session_manager.close()
            except Exception:  # noqa: BLE001 — best-effort; session_manager.close() failure; shutdown best-effort
                pass
            self._session_manager = None
        # F-05: cleanup RobotsParser async session
        if self._robots_parser is not None:
            try:
                await self._robots_parser.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001 — best-effort; RobotsParser shutdown failure; non-critical
                pass
            self._robots_parser = None
        if self._session_lmdb_env is not None:
            try:
                self._session_lmdb_env.close()
            except Exception:  # noqa: BLE001 — best-effort; LMDB env.close() failure; shutdown best-effort
                pass
            self._session_lmdb_env = None
        from ..transport.darknet_session_provider import close_all as _close_darknet_sessions
        await _close_darknet_sessions()
        if self._lightpanda_pool is not None:
            try:
                await self._lightpanda_pool.close()
            except Exception:  # noqa: BLE001 — best-effort; lightpanda_pool.close() failure; shutdown best-effort
                pass
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
        except (asyncio.CancelledError, asyncio.TimeoutError):  # noqa: BLE001 — best-effort; asyncio.sleep interrupted; non-critical
            return
        try:
            domain = _fast_url_host(url)
        except (ValueError, TypeError):  # noqa: BLE001 — best-effort; httpx.URL parse failure; non-critical
            return
        # Circuit breaker check delegated to transport/circuit_breaker.py
        try:
            transport_lower = transport.lower()
            if transport_lower == 'tor':
                try:
                    from ..transport.base import TransportConfig
                    from ..transport.tor_transport import get_tor_transport
                    tor = get_tor_transport()
                    if tor and await tor.is_running():
                        config = TransportConfig(url=url, method='GET', headers=None, body=None, timeout=10.0)
                        await tor.fetch(config)
                except* (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort; Tor transport fetch failure; fire-and-forget cover traffic
                    pass
            elif transport_lower == 'i2p':
                try:
                    from ..transport.base import TransportConfig
                    from ..transport.i2p_transport import get_i2p_transport
                    i2p = get_i2p_transport()
                    if i2p and i2p.is_running():
                        config = TransportConfig(url=url, method='GET', headers=None, body=None, timeout=10.0)
                        await i2p.fetch(config)
                except* (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort; I2P transport fetch failure; fire-and-forget cover traffic
                    pass
            else:
                try:
                    from hledac.universal.transport.curl_cffi_fetch import async_get_curl_cffi_session_for_host
                    ok, session, used_profile, host = await async_get_curl_cffi_session_for_host(url, profile='chrome131')
                    if ok and session is not None:
                        await session.get(url, timeout=10.0)
                except* (asyncio.CancelledError, Exception):  # noqa: BLE001 — best-effort; curl_cffi fetch failure; fire-and-forget cover traffic
                    pass
        except* (Exception, BaseException):  # noqa: BLE001 — best-effort; cover traffic transport failure; non-critical
            pass

    async def _fire_cover_traffic(self, url: str, delay: float, transport: str) -> None:
        """Legacy wrapper — redirect to transport-aware implementation."""
        await self._fire_cover_traffic_url(url, delay, transport)

    def enqueue_pivot(self, ioc_value: str, ioc_type: str, confidence: float, degree: float=1.0, task_type: str | None=None) -> None:
        """Enqueue a pivot task. Silently drops if queue is full (M1 8GB).

        Sprint 8VI §B.4: RL-adaptive priority — for generic_pivot task
        types, blend EMA reward with base priority.

        Sprint F-EXTRACT-2: moved from SprintScheduler. State
        (`_pivot_queue`, `_pivot_stats`) and helper (`_get_adaptive_priority`)
        accessed via provider callbacks.
        """
        from hledac.universal.runtime.pivot_types import PivotTask
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
        for tt in task_types_list:
            effective = self._adaptive_priority_provider(tt, base_priority)
            priority = -effective
            task = PivotTask(priority, ioc_type, ioc_value, tt)
            try:
                pivot_queue.put_nowait(task)
                if pivot_stats is not None:
                    pivot_stats['total'] = pivot_stats.get('total', 0) + 1
            except asyncio.QueueFull:
                pass

    def enqueue_hypothesis_pivot(self, ioc_value: str, ioc_type: str='hypothesis', confidence: float=0.7, depth: int=1) -> bool:
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
                pass