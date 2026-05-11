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
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import time

import lmdb
from collections import deque

# Sprint 41: zstd compression with passive dictionary
try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False
    zstd = None

# Sprint 44: Lightpanda for JS-heavy pages
try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    aiohttp = None

# Sprint 46: Session management and Paywall bypass
try:
    from ..tools.session_manager import SessionManager
    from ..tools.paywall import PaywallBypass
    from ..tools.darknet import DarknetConnector
    SESSION_AVAILABLE = True
except ImportError:
    SESSION_AVAILABLE = False
    SessionManager = None
    PaywallBypass = None
    DarknetConnector = None

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from .base import UniversalCoordinator
from ..tools.url_dedup import RotatingBloomFilter, create_rotating_bloom_filter
from ..utils.async_helpers import async_getaddrinfo

# Sprint 8C1: Flow trace
from ..utils.flow_trace import (
    trace_fetch_start, trace_fetch_end, trace_dedup_decision,
    trace_counter, is_enabled,
)

# Sprint 80: TokenBucketController
try:
    from ..stealth.stealth_manager import TokenBucketController
except ImportError:
    # Fallback - inline definition
    import threading

    class TokenBucketController:
        """Token Bucket pro řízení concurrency."""

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

# Sprint 39: Deep web hints extraction
try:
    from ..tools.deep_web_hints import DeepWebHintsExtractor, DeepWebHints
    HINTS_AVAILABLE = True
except ImportError:
    HINTS_AVAILABLE = False
    DeepWebHintsExtractor = None
    DeepWebHints = None

logger = logging.getLogger(__name__)


# =============================================================================
# Sprint 4B: TIMEOUT MATRIX
# Canonical timeouts for fetch runtime (used in actual requests, not just constants)
# =============================================================================
TIMEOUT_CLEARNET_API = 20.0   # seconds - API JSON endpoints
TIMEOUT_CLEARNET_HTML = 35.0  # seconds - HTML page fetch
TIMEOUT_TOR = 75.0            # seconds - .onion over Tor
TIMEOUT_I2P = 150.0           # seconds - .i2p over I2P

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
AIMD_ADDITIVE_INCREMENT = 1    # add this many slots on success
AIMD_DECREASE_FACTOR = 0.75    # multiply by this on failure (25% reduction)
AIMD_MIN_CONCURRENCY = 1      # floor
AIMD_MAX_CONCURRENCY = 25     # ceiling (matches GLOBAL_MAX)
AIMD_SUCCESS_THRESHOLD = 3    # count successes before increase

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
import platform
import fcntl

NOCACHE_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50MB
F_NOCACHE = 48 if platform.system() == "Darwin" else None


def apply_fcntl_nocache(fd: int, content_length: int | None) -> None:
    """
    Apply F_NOCACHE flag to file descriptor for large downloads.

    This tells Darwin's kernel not to cache the file data in memory,
    which is beneficial for very large downloads (>50MB) on memory-constrained systems.

    Args:
        fd: File descriptor to apply the flag to
        content_length: Size of the content being written (if known)
    """
    if content_length is None or content_length <= NOCACHE_THRESHOLD_BYTES:
        return

    # LOW-7 fix: F_NOCACHE is Darwin-only
    if F_NOCACHE is None:
        return

    try:
        fcntl.fcntl(fd, F_NOCACHE, 1)
    except OSError:
        # Fail-safe: never let fcntl failure abort the write
        # Catches: platform not supported, invalid fd, etc.
        pass


@dataclass(slots=True)
class FetchCoordinatorConfig:
    """Configuration for FetchCoordinator."""
    max_urls_per_step: int = 5
    max_evidence_per_step: int = 10
    enable_security_check: bool = True
    enable_domain_limiter: bool = True
    budget_network_calls: int = 50
    budget_snapshots: int = 20


# Sprint 41: zstd compressor with passive dictionary
class ZstdCompressor:
    """Compressor with content-aware levels and passive dictionary."""

    def __init__(self):
        self._dctx = zstd.ZstdDecompressor() if ZSTD_AVAILABLE else None
        self._dictionary_data = None
        self._response_counter = 0
        self._response_samples: deque = deque(maxlen=100)

    def compress(self, data: bytes, content_type: str = 'text') -> bytes:
        """Compress with optional dictionary and content-aware level."""
        if not ZSTD_AVAILABLE or data is None:
            return data
        level = 1 if content_type == 'json' else 3
        try:
            if self._dictionary_data and self._response_counter > 100:
                cctx = zstd.ZstdCompressor(level=level, dict_data=self._dictionary_data)
            else:
                cctx = zstd.ZstdCompressor(level=level)
            return cctx.compress(data)
        except Exception:
            return data

    def decompress(self, data: bytes) -> bytes:
        if not ZSTD_AVAILABLE or data is None:
            return data
        try:
            if self._dictionary_data:
                dctx = zstd.ZstdDecompressor(dict_data=self._dictionary_data)
                return dctx.decompress(data)
            return self._dctx.decompress(data)
        except Exception:
            return data

    def add_sample(self, data: bytes, content_type: str):
        """Collect samples for dictionary building. Rebuilds dictionary every 100 samples."""
        if not ZSTD_AVAILABLE:
            return
        # P2-2 fix: always collect samples (deque maxlen=100 auto-evicts oldest)
        self._response_samples.append((data, content_type))
        self._response_counter += 1
        # Rebuild dictionary every 100 samples (not just once at counter==100)
        if self._response_counter >= 100 and self._response_counter % 100 == 0:
            self._build_dictionary()

    def _build_dictionary(self):
        """Build zstd dictionary from collected samples."""
        if not ZSTD_AVAILABLE:
            return
        try:
            samples = [s[0] for s in self._response_samples]
            if samples:
                self._dictionary_data = zstd.train_dictionary(1024 * 1024, samples)
        except Exception:
            pass


# Sprint 44: Lightpanda Manager for JS-heavy pages
class LightpandaManager:
    """Manages Lightpanda headless browser for JS-heavy page rendering."""

    def __init__(self):
        self._proc = None
        self._endpoint = os.environ.get("CDP_ENDPOINT", "ws://127.0.0.1:9222")  # Configurable via CDP_ENDPOINT env variable
        from hledac.universal.paths import DB_ROOT
        self._bin_path = DB_ROOT / 'bin' / 'lightpanda'

    async def _download_if_missing(self):
        """Download Lightpanda binary if missing."""
        if self._bin_path.exists():
            return
        os.makedirs(self._bin_path.parent, exist_ok=True)

        if not AIOHTTP_AVAILABLE:
            logger.warning("[LIGHTPANDA] aiohttp not available, cannot download")
            raise ImportError("aiohttp not available")

        url = "https://github.com/lightpanda-io/browser/releases/latest/download/lightpanda-aarch64-macos"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        content = await resp.read()
                        # P3-7 fix: compute hash for security auditing
                        # Expected hash from trusted source stored in LIGHTPANDA_SHA256 env var
                        actual_hash = hashlib.sha256(content).hexdigest()
                        expected_hash = os.environ.get('LIGHTPANDA_SHA256')
                        if not expected_hash:
                            raise ValueError(
                                "[LIGHTPANDA] LIGHTPANDA_SHA256 env var must be set to verify "
                                "binary integrity before download. Set it to the trusted SHA256 hash."
                            )
                        if actual_hash != expected_hash:
                            raise ValueError(
                                f"[LIGHTPANDA] Hash mismatch! "
                                f"expected={expected_hash}, actual={actual_hash}"
                            )
                        logger.info(f"[LIGHTPANDA] Hash verified: {actual_hash[:16]}...")
                        with open(self._bin_path, 'wb') as f:
                            f.write(content)
                        os.chmod(self._bin_path, 0o755)
                    else:
                        logger.warning(f"[LIGHTPANDA] Download failed: {resp.status}")
        except Exception as e:
            logger.warning(f"[LIGHTPANDA] Download error: {e}")
            raise

    async def ensure_running(self):
        """Ensure Lightpanda process is running."""
        if self._proc is None or self._proc.returncode is not None:
            await self._download_if_missing()
            self._proc = await asyncio.create_subprocess_exec(
                str(self._bin_path), "serve", "--port", "9222",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            # Wait for port to be open
            for _ in range(50):  # max 5s
                try:
                    reader, writer = await asyncio.open_connection('127.0.0.1', 9222)
                    writer.close()
                    await writer.wait_closed()
                    break
                except Exception:
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Lightpanda failed to start")

    # SEC-07: Guard for tab.evaluate() - prevents JS injection if made dynamic
    # Currently uses static string literal (safe). If refactored to accept dynamic input,
    # validate with _SAFE_JS_IDENTIFIER_PATTERN below.
    _SAFE_JS_PATTERN = re.compile(r"^[a-zA-Z0-9_.\[\]]+$")

    def _validate_js_expression(self, expr: str) -> str:
        """Validate a JavaScript expression is safe for tab.evaluate()."""
        if not self._SAFE_JS_PATTERN.match(expr):
            raise ValueError(f"Unsafe JS expression rejected: {expr!r}")
        return expr

    async def fetch_js(self, url: str, proxy: str = None) -> bytes:
        """Fetch URL with JS rendering using nodriver."""
        try:
            from nodriver import start, Config
        except ImportError:
            logger.warning("[LIGHTPANDA] nodriver not installed, falling back")
            raise ImportError("nodriver not available")

        await self.ensure_running()
        config = Config(browserWSEndpoint=self._endpoint)
        browser = await start(config)

        try:
            if proxy:
                await browser.settings.set_proxy(proxy)

            tab = await browser.get(url)
            await tab.wait_domcontentloaded()
            # SEC-07: Static string - safe from injection. Guard validates if ever made dynamic.
            js_expr = self._validate_js_expression("document.documentElement.outerHTML")
            content = await tab.evaluate(js_expr)
            await browser.stop()
            return content.encode()
        except Exception as e:
            await browser.stop()
            raise


# Sprint 45: Lightpanda Pool for concurrent JS rendering
class LightpandaPool:
    """Pool of Lightpanda instances for concurrent JS rendering."""

    def __init__(self, size: int = 2):
        self._size = size
        # F207N-D: bounded for M1 8GB safety — pool size is small (default 2),
        # so maxsize=8 gives headroom without starving the pool.
        self._available = asyncio.Queue(maxsize=max(4, size * 4))
        self._all_instances = []
        self._started = False

    async def start(self):
        """Initialize pool with N Lightpanda instances."""
        if self._started:
            return

        for i in range(self._size):
            lp = LightpandaManager()
            try:
                await lp.ensure_running()
                self._all_instances.append(lp)
                await self._available.put(lp)
            except Exception as e:
                logger.warning(f"[POOL] Failed to start instance {i}: {e}")

        self._started = True
        logger.info(f"[POOL] Started {len(self._all_instances)} Lightpanda instances")

    async def get_instance(self) -> LightpandaManager:
        """Get available instance or wait."""
        if not self._started:
            await self.start()

        # Wait for available instance
        return await self._available.get()

    async def release(self, instance: LightpandaManager):
        """Return instance to pool."""
        await self._available.put(instance)


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
        config: Optional[FetchCoordinatorConfig] = None,
        max_concurrent: int = 3,
    ):
        super().__init__(name="FetchCoordinator", max_concurrent=max_concurrent)
        self._config = config or FetchCoordinatorConfig()

        # State
        self._frontier: deque = deque(maxlen=1000)
        self._processed_urls = create_rotating_bloom_filter()
        self._evidence_ids: deque = deque(maxlen=500)
        self._urls_fetched_count: int = 0
        self._stop_reason: Optional[str] = None

        # Per-domain circuit breaker (Sprint F195C)
        self._domain_failures: Dict[str, int] = {}
        self._domain_failure_timestamps: Dict[str, float] = {}  # for eviction (P2-1)
        self._domain_blocked_until: Dict[str, float] = {}
        self._failure_threshold = 3
        self._cooldown_seconds = 60
        self._base_retry_delay = 1.0  # Sprint F195C: circuit breaker retry config
        self._max_retries = 3
        self._max_backoff_delay = 30.0

        # Orchestrator reference (set via start)
        self._orchestrator: Optional[Any] = None
        self._ctx: Dict[str, Any] = {}

        # Sprint 39: Deep web hints extractor
        self._hints_extractor = DeepWebHintsExtractor() if HINTS_AVAILABLE else None

        # Sprint 41: zstd compression
        self._zstd = ZstdCompressor()

        # Sprint 44/45: Lightpanda pool for JS-heavy pages + concurrent requests
        self._lightpanda_pool = LightpandaPool(size=2)
        self._lightpanda_pool_started = False
        self._lightpanda_lock = asyncio.Lock()  # P1-1: thread-safe pool init
        self._geo_proxies = self._load_geo_proxies()
        self._current_geo_context = None  # set by caller

        # Sprint 46: Session management
        self._session_lmdb_env = None
        self._session_manager = None
        self._paywall_bypass = PaywallBypass() if SESSION_AVAILABLE else None
        self._darknet_connector = DarknetConnector() if SESSION_AVAILABLE else None

        # Sprint 76: Tor connection pooling
        self._tor_sessions: Dict[str, Any] = {}
        self._tor_last_used: Dict[str, float] = {}
        self._tor_max_sessions = CONCURRENCY_TOR
        self._tor_lock = asyncio.Lock()

        # Sprint 80: Token bucket concurrency (still kept for compatibility)
        self._concurrency = TokenBucketController(rate=5, capacity=10)

        # Sprint F3/F8/F9: Local corpus ingester seam (lazy, fail-soft)
        self._corpus_ingester = None

        # Sprint 4B: AIMD Adaptive Concurrency Controller
        self._aimd_concurrency: float = float(CONCURRENCY_CLEARNET)  # current window
        self._aimd_successes: int = 0  # successes since last increase
        self._aimd_failures: int = 0  # consecutive failures
        self._aimd_semaphore: Optional[asyncio.Semaphore] = None  # created on first use
        self._aimd_semaphore_limit: int = int(CONCURRENCY_CLEARNET)  # P1-3: track limit explicitly (avoid _value private API)
        self._aimd_lock = asyncio.Lock()

        # Sprint 4B: Telemetry state
        self._telemetry: Dict[str, Any] = {
            'aimd_concurrency': self._aimd_concurrency,
            'active_fetches': 0,
            'total_successes': 0,
            'total_failures': 0,
            # P1-13: Circuit breaker metrics
            'circuit_breaker_blocks': 0,
            'circuit_breaker_active': 0,
        }

        # F206AS: Canonical circuit breaker adapter (lazy, fail-soft)
        # References canonical transport/circuit_breaker.py domain_breaker_check / record functions
        self._canonical_breaker: Any = None
        self._canonical_breaker_available: bool = False
        self._canonical_breaker_checked: int = 0
        self._canonical_breaker_blocks: int = 0
        self._canonical_breaker_fallback_used: int = 0
        self._canonical_breaker_lock = __import__('threading').Lock()

    def _ensure_canonical_breaker(self) -> Tuple[bool, Any, str]:
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

    def _check_canonical_breaker(self, domain: str) -> Tuple[bool, str, float]:
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
            cb_module.get_breaker(domain).record_failure(
                is_timeout=is_timeout,
                failure_kind=failure_kind or 'fetch_error',
            )
        except Exception:
            self._canonical_breaker_fallback_used += 1

    async def _record_domain_failure(self, domain: str) -> None:
        """Record a failure for a domain; block it after _failure_threshold failures."""
        # P2-1: Evict stale entries if dict grows too large
        if len(self._domain_failures) > 1000:
            cutoff = time.time() - (24 * 3600)  # 24 hours
            stale_domains = [d for d, ts in self._domain_failure_timestamps.items() if ts < cutoff and d not in self._domain_blocked_until]
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

    def get_blocked_domains(self) -> Dict[str, float]:
        """Returns {domain: unblock_timestamp} for currently blocked domains."""
        now = time.time()
        return {d: t for d, t in self._domain_blocked_until.items() if t > now}

    def get_canonical_breaker_stats(self) -> Dict[str, Any]:
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
            except Exception:
                pass  # fail-soft: return empty states on error
        return result

    def init_session_manager(self, lmdb_path: Optional[str] = None):
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
        self._session_lmdb_env = lmdb.open(str(lmdb_path), map_size=10*1024*1024)
        self._session_manager = SessionManager(self._session_lmdb_env)

    def _load_geo_proxies(self) -> Dict[str, str]:
        """Load proxy servers for different regions from configuration."""
        from hledac.universal.paths import DB_ROOT
        proxy_file = DB_ROOT / 'config' / 'proxies.json'
        if proxy_file.exists():
            try:
                with open(proxy_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    # Sprint 71E: DNS Rebinding Defense
    _PRIVATE_NETS = [ipaddress.ip_network(n) for n in [
        "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
        "127.0.0.0/8", "169.254.0.0/16", "100.64.0.0/10"
    ]]

    async def _resolve_host_ips_async(self, host: str) -> List[str]:
        """Resolve hostname to IPs (deterministically sorted) using async DNS."""
        try:
            results = await async_getaddrinfo(host, 0, proto=socket.IPPROTO_TCP)
            ips = sorted(set(str(r[4][0]) for r in results))
            return ips
        except Exception:
            return []

    def _resolve_host_ips(self, host: str) -> List[str]:
        """Resolve hostname to IPs synchronously using blocking socket.getaddrinfo."""
        try:
            results = socket.getaddrinfo(host, 0, proto=socket.IPPROTO_TCP)
            ips = sorted(set(str(r[4][0]) for r in results))
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

    async def _validate_fetch_target(self, url: str) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate fetch target: resolve and check for private IPs.

        NOTE (P3-8): This provides DNS rebinding protection but has a residual
        TOCTOU window between validation and fetch. The actual aiohttp fetch
        resolves DNS independently. For HTTPS, certificate validation provides
        secondary protection. For HTTP, the risk is acknowledged but the
        performance cost of binding to pre-validated IPs is prohibitive.
        """
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
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

            # Resolve DNS natively (async, no thread pool)
            raw_results = await async_getaddrinfo(hostname, 0, proto=socket.IPPROTO_TCP)
            ips = sorted(set(str(r[4][0]) for r in raw_results))
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

    async def _aimd_acquire(self) -> float:
        """
        Acquire AIMD slot, returns the current AIMD concurrency window.
        Thread-safe, creates semaphore lazily.
        """
        async with self._aimd_lock:
            if self._aimd_semaphore is None:
                self._aimd_semaphore = asyncio.Semaphore(int(self._aimd_concurrency))
                self._aimd_semaphore_limit = int(self._aimd_concurrency)
            # Ensure semaphore limit matches current window
            # (recreate if window changed significantly)
            # P1-3 fix: use explicit limit tracking instead of private _value
            target = int(self._aimd_concurrency)
            if abs(self._aimd_semaphore_limit - target) > 2:
                self._aimd_semaphore = asyncio.Semaphore(target)
                self._aimd_semaphore_limit = target
            await self._aimd_semaphore.acquire()
            self._telemetry['active_fetches'] += 1
            return self._aimd_concurrency

    def _aimd_release_success(self) -> float:
        """
        Release AIMD slot after success.
        Returns new concurrency window.
        """
        self._aimd_successes += 1
        self._telemetry['total_successes'] += 1
        self._telemetry['active_fetches'] -= 1

        if self._aimd_successes >= AIMD_SUCCESS_THRESHOLD:
            # Additive increase
            new_concurrency = min(
                self._aimd_concurrency + AIMD_ADDITIVE_INCREMENT,
                AIMD_MAX_CONCURRENCY
            )
            if new_concurrency != self._aimd_concurrency:
                self._aimd_concurrency = new_concurrency
                self._aimd_semaphore_limit = int(new_concurrency)  # P1-3: sync limit
                logger.debug(
                    f"[AIMD] success #{self._aimd_successes} → "
                    f"additive increase → window={self._aimd_concurrency:.1f}"
                )
            self._aimd_successes = 0

        self._aimd_failures = 0
        self._telemetry['aimd_concurrency'] = self._aimd_concurrency
        return self._aimd_concurrency

    def _aimd_release_failure(self) -> float:
        """
        Release AIMD slot after failure (timeout/throttling/pressure).
        Returns new concurrency window.
        """
        self._aimd_failures += 1
        self._telemetry['total_failures'] += 1
        self._telemetry['active_fetches'] -= 1

        # Multiplicative decrease
        new_concurrency = max(
            self._aimd_concurrency * AIMD_DECREASE_FACTOR,
            AIMD_MIN_CONCURRENCY
        )
        if new_concurrency != self._aimd_concurrency:
            old = self._aimd_concurrency
            self._aimd_concurrency = new_concurrency
            self._aimd_semaphore_limit = int(new_concurrency)  # P1-3: sync limit
            logger.warning(
                f"[AIMD] failure #{self._aimd_failures} → "
                f"multiplicative decrease → window={old:.1f}→{self._aimd_concurrency:.1f}"
            )
        self._aimd_successes = 0
        self._telemetry['aimd_concurrency'] = self._aimd_concurrency
        return self._aimd_concurrency

    async def _fetch_with_lightpanda(self, url: str, proxy: str = None) -> Dict[str, Any]:
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
            logger.warning(f"[LIGHTPANDA] Failed: {e}, falling back to curl_cffi")
            return None

    # =============================================================================
    # Sprint 76: Tor Connection Pooling
    # =============================================================================

    @staticmethod
    def _mask_cookies_for_log(cookies: Optional[Dict[str, str]]) -> Dict[str, str]:
        """
        P3-5 fix: Mask cookie values for safe logging.

        Args:
            cookies: Raw cookie dict {name: value}

        Returns:
            Masked dict {name: '***'} preserving structure but hiding values
        """
        if not cookies:
            return {}
        return {k: '***' for k in cookies}

    async def _get_tor_session(self, domain: str) -> Optional[Any]:
        """Get or create Tor session with connection pooling."""
        async with self._tor_lock:
            import time
            now = time.time()

            # Cleanup expired sessions (5 min TTL)
            expired = [d for d, t in self._tor_last_used.items() if now - t > 300]
            for d in expired:
                if d in self._tor_sessions:
                    await self._tor_sessions[d].close()
                    del self._tor_sessions[d]
                    del self._tor_last_used[d]

            # Enforce limit
            if len(self._tor_sessions) >= self._tor_max_sessions:
                oldest = min(self._tor_last_used.items(), key=lambda x: x[1])
                await self._tor_sessions[oldest[0]].close()
                del self._tor_sessions[oldest[0]]
                del self._tor_last_used[oldest[0]]

            # Create new session if needed
            if domain not in self._tor_sessions:
                try:
                    import aiohttp_socks
                    # P3-6 fix: Use environment variable for Tor proxy, default to localhost:9050
                    tor_proxy = os.environ.get('TOR_PROXY', 'socks5://127.0.0.1:9050')
                    connector = aiohttp_socks.SocksConnector.from_url(tor_proxy, rdns=True)
                    # Sprint 4B: Use TIMEOUT_TOR matrix constant
                    session = aiohttp.ClientSession(
                        connector=connector,
                        timeout=aiohttp.ClientTimeout(total=TIMEOUT_TOR)
                    )
                    self._tor_sessions[domain] = session
                except Exception as e:
                    logger.warning(f"Tor session creation failed: {e}")
                    return None

            self._tor_last_used[domain] = now
            return self._tor_sessions.get(domain)

    async def _fetch_with_tor(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch .onion URL using Tor connection pool."""
        # Sprint 4B: Use TIMEOUT_TOR matrix constant (passed to session at creation)
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
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
        except asyncio.TimeoutError:
            logger.debug(f"[TOR] Timeout for {url}")
            # Trigger AIMD failure
            self._aimd_release_failure()
            return None
        except Exception as e:
            logger.warning(f"Tor fetch failed: {e}")
            self._aimd_release_failure()
            return None

    async def _fetch_with_curl(self, url: str, proxy: str = None) -> Dict[str, Any]:
        """Fetch URL with curl_cffi (fallback)."""
        # Sprint F3/F8/F9: also populates status_code/content_type/headers for corpus ingest
        try:
            from ..intelligence.stealth_crawler import StealthWebScraper
            scraper = StealthWebScraper()
            if not await scraper.initialize():
                return {'url': url, 'content': b'', 'error': 'scraper_init_failed'}
            result = await scraper.scrape(url)
            if result.success:
                return {
                    'url': url,
                    'final_url': url,
                    'content': (result.content or '').encode('utf-8', errors='replace'),
                    'status_code': result.status_code or 200,
                    'content_type': (result.headers or {}).get('content-type', 'text/html'),
                    'headers': result.headers or {},
                    'js_rendered': False,
                    'success': True,
                }
            return {'url': url, 'content': b'', 'error': f'scrape_failed status={result.status_code}'}
        except asyncio.TimeoutError:
            logger.debug(f"[CURL] Timeout for {url}")
            self._aimd_release_failure()
            return {'url': url, 'content': b'', 'error': 'timeout'}
        except Exception as e:
            logger.warning(f"[CURL] Failed: {e}")
            return {'url': url, 'content': b'', 'error': str(e)}

    def get_supported_operations(self) -> List[Any]:
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
        return True

    async def _do_start(self, ctx: Dict[str, Any]) -> None:
        """
        Start coordinator with context from orchestrator.

        Expected ctx keys:
        - frontier: List[str] - URLs to fetch
        - orchestrator: reference to orchestrator instance
        - budget_manager: BudgetManager for limits
        """
        self._ctx = ctx
        self._orchestrator = ctx.get('orchestrator')

        # Load frontier if provided
        if 'frontier' in ctx:
            self._frontier = deque(ctx['frontier'], maxlen=1000)

        # Sprint F3/F8/F9: lazy-init corpus ingester on first start
        if self._corpus_ingester is None:
            try:
                from ..knowledge.corpus_ingester import get_corpus_ingester
                self._corpus_ingester = get_corpus_ingester()
            except Exception as e:
                logger.warning(f"CorpusIngester: lazy init failed: {e}")
                self._corpus_ingester = None

        logger.info(f"FetchCoordinator started with {len(self._frontier)} URLs in frontier")

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

    async def _do_step(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
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
        for _ in range(self._config.max_urls_per_step * 2):
            if not self._frontier:
                break
            url = self._frontier.popleft()
            is_deduped = url in self._processed_urls
            trace_dedup_decision(url, is_deduped)
            if not is_deduped:
                candidates.append((self._url_priority(url), url))

        if not candidates:
            self._stop_reason = "frontier_empty"
            return self._get_step_result()

        # Sprint 5B: Sort by priority (lower score = higher priority) and take top N
        candidates.sort(key=lambda x: x[0])
        urls_to_fetch = [url for _, url in candidates[:self._config.max_urls_per_step]]

        # Sprint 5B: Determine effective batch size (limited by AIMD window)
        batch_size = len(urls_to_fetch)
        effective_batch = min(batch_size, int(self._aimd_concurrency))

        # Sprint 4B: Light telemetry snapshot before fetch batch
        if is_enabled():
            trace_counter("fetch.aimd.window", self._aimd_concurrency)
            trace_counter("fetch.active", self._telemetry['active_fetches'])
            trace_counter("fetch.batch_size", batch_size)

        # Sprint 5B: Batch fetch with gather + return_exceptions
        # Each _fetch_url handles AIMD semaphore internally
        batch_start = time.time()
        results = await asyncio.gather(
            *[self._fetch_url(url) for url in urls_to_fetch],
            return_exceptions=True
        )
        batch_elapsed = time.time() - batch_start

        # Sprint 5B: Gather hygiene - explicit exception logging
        evidence_ids = []
        for url, result in zip(urls_to_fetch, results):
            if isinstance(result, Exception):
                # Sprint 5B: Explicit exception logging (no silent failure)
                logger.debug(f"[BATCH] fetch exception for {url}: {type(result).__name__}: {result}")
                continue

            if result and result.get('success'):
                self._processed_urls.add(url)
                self._urls_fetched_count += 1

                # Sprint F3/F8/F9: ingest successful fetch into local corpus
                if self._corpus_ingester is not None:
                    try:
                        self._corpus_ingester.ingest(result)
                    except Exception as e:
                        logger.debug(f"[INGEST] corpus ingest failed for {url}: {e}")

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
        new_evidence_ids: Optional[List[str]] = None,
        batch_size: int = 0,
        effective_parallelism: int = 0,
        batch_elapsed_ms: float = 0.0,
    ) -> Dict[str, Any]:
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

    async def _fetch_url(self, url: str, attempt: int = 0) -> Optional[Dict[str, Any]]:
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
        from ..types import is_offline_mode, OfflineModeError
        if is_offline_mode():
            raise OfflineModeError(f"Offline mode enabled, skipping fetch: {url}")

        # Sprint 4B: AIMD concurrency gate
        await self._aimd_acquire()

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
                # F206AS: Canonical circuit breaker check (before local breaker)
                # Consult canonical transport/circuit_breaker.py domain_breaker_check if available
                domain = urlparse(url).netloc
                canonical_allowed, canonical_reason, canonical_retry_after = self._check_canonical_breaker(domain)
                if not canonical_allowed:
                    # F206AS: Update active count on each canonical circuit breaker hit
                    self._telemetry['circuit_breaker_active'] = len(self.get_blocked_domains())
                    logger.debug(f"[F206AS] Canonical circuit breaker open for {domain}: {canonical_reason} (retry in {canonical_retry_after:.1f}s)")
                    trace_fetch_end(url, "circuit_breaker", "circuit_open", 0.0)
                    result = None
                    break

                # Local circuit breaker check (fallback if canonical unavailable or not blocking)
                now = time.time()
                if domain in self._domain_blocked_until and now < self._domain_blocked_until[domain]:
                    # P1-13: Update active count on each circuit breaker hit
                    self._telemetry['circuit_breaker_active'] = len(self.get_blocked_domains())
                    logger.debug(f"Circuit breaker open for domain: {domain}")
                    trace_fetch_end(url, "circuit_breaker", "circuit_open", 0.0)
                    result = None
                    break

                # Sprint 71E: DNS Rebinding Defense - resolve and validate before fetch
                if not url.endswith('.onion') and not url.endswith('.i2p'):
                    is_safe, meta = await self._validate_fetch_target(url)
                    if not is_safe:
                        logger.warning(f"DNS rebinding defense blocked: {meta.get('blocked_reason')} for {domain}")
                        trace_fetch_end(url, "dns_rebind_defense", "blocked", 0.0, {"reason": meta.get("blocked_reason")})
                        result = {"error": "blocked", "blocked_reason": meta.get("blocked_reason"), "meta": meta}
                        break

                # Sprint 4B: Policy gate via SourceTransportMap — replaces hardcoded url.endswith()
                # Sprint 46 + 76: Darknet URL handling (.onion, .i2p)
                # Sprint 76: Use Tor connection pool for .onion
                from ..transport.transport_resolver import get_transport_for_url, Transport
                url_transport = get_transport_for_url(url)

                if url_transport is Transport.TOR:
                    trace_fetch_start(url, "tor", {"attempt": attempt, "timeout": TIMEOUT_TOR})
                    result = await self._fetch_with_tor(url)
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
                    # Fallback to darknet connector if Tor pool failed
                    if self._darknet_connector:
                        result = await self._darknet_connector.fetch_onion(url)
                        if result:
                            result['success'] = True
                            result['status_code'] = result.get('status_code', 0)
                            result['url'] = url
                            result['final_url'] = url
                            trace_fetch_end(url, "darknet_fallback", "ok", 0.0)
                            break
                elif url_transport is Transport.I2P and self._darknet_connector:
                    trace_fetch_start(url, "i2p", {"attempt": attempt, "timeout": TIMEOUT_I2P})
                    result = await self._darknet_connector.fetch_i2p(url)
                    if result:
                        result['success'] = True
                        result['status_code'] = result.get('status_code', 0)
                        result['url'] = url
                        result['final_url'] = url
                        trace_fetch_end(url, "i2p", "ok", 0.0)
                        break

                # Sprint 46: Session injection - get cookies before fetch
                # P3-5 fix: Never log raw session cookies - use _mask_cookies_for_log()
                session_cookies = None
                if self._session_manager:
                    session = await self._session_manager.get_session(domain)
                    if session:
                        session_cookies = session.get('cookies')

                # Sprint 4B: HTML preview fetch with timeout matrix (3s preview)
                html_preview = ""
                try:
                    if AIOHTTP_AVAILABLE:
                        async def _async_fetch_preview():
                            # Sprint 4B: Hardcoded 3s for preview (within clearnet HTML class)
                            preview_timeout = aiohttp.ClientTimeout(total=3)
                            async with aiohttp.ClientSession(timeout=preview_timeout) as session:
                                async with session.head(url, allow_redirects=True, cookies=session_cookies) as resp:
                                    content_type = resp.headers.get('content-type', '')
                                    if content_type.startswith('text/html'):
                                        async with session.get(url, cookies=session_cookies) as get_resp:
                                            text = await get_resp.text()
                                            return text[:10000] if text else ""
                                    return ""
                        html_preview = await _async_fetch_preview()
                except asyncio.TimeoutError:
                    logger.debug(f"[PREVIEW] Timeout for {url}")
                except Exception as e:
                    # Sprint 4B: Gather hygiene - log but don't swallow
                    logger.debug(f"[PREVIEW] Failed to fetch preview for {url}: {e}")

                # Select proxy based on geo context
                proxy = None
                if self._current_geo_context and self._current_geo_context in self._geo_proxies:
                    proxy = self._geo_proxies.get(self._current_geo_context)

                # JS detection - use Lightpanda for JS-heavy pages
                if self._is_js_heavy(url, html_preview):
                    logger.debug(f"[LIGHTPANDA] JS-heavy detected: {url}")
                    trace_fetch_start(url, "lightpanda", {"attempt": attempt})
                    lightpanda_result = await self._fetch_with_lightpanda(url, proxy)
                    if lightpanda_result and lightpanda_result.get('content'):
                        # Sprint F3/F8/F9: normalize to common ingest shape
                        lightpanda_result.setdefault('success', True)
                        lightpanda_result.setdefault('status_code', 200)
                        lightpanda_result.setdefault('content_type', 'text/html')
                        lightpanda_result.setdefault('final_url', url)
                        lightpanda_result.setdefault('headers', {})
                        result = lightpanda_result
                        trace_fetch_end(url, "lightpanda", "ok", 0.0)
                    else:
                        # Fallback to curl if Lightpanda failed
                        trace_fetch_start(url, "curl_fallback", {"attempt": attempt})
                        result = await self._fetch_with_curl(url, proxy)
                        trace_fetch_end(url, "curl_fallback", "fallback", 0.0)
                else:
                    # Sprint 4B: clearnet HTML fetch with TIMEOUT_CLEARNET_HTML
                    trace_fetch_start(url, "curl", {"attempt": attempt, "timeout": TIMEOUT_CLEARNET_HTML})
                    result = await self._fetch_with_curl(url, proxy)
                    if result and not result.get('error'):
                        trace_fetch_end(url, "curl", "ok", 0.0)
                    else:
                        trace_fetch_end(url, "curl", result.get('error', 'failed'), 0.0)

                # Check if we should retry
                if result is None or result.get('error') == 'timeout' or result.get('status_code', 200) >= 500:
                    if attempt < max_retries:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        logger.debug(f"[RETRY] Attempt {attempt + 1}/{max_retries} for {url} after {delay:.1f}s")
                        trace_fetch_end(url, "none", "retry", 0.0, {"attempt": attempt, "delay": delay})
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                break

            # Sprint 4B: AIMD success - record after full fetch cycle
            if result and not result.get('error'):
                # Sprint F3/F8/F9: ensure success flag is set for corpus ingest
                result.setdefault('success', True)
                self._aimd_release_success()
                # F206AS: Record success to canonical circuit breaker if available
                self._record_canonical_success(domain)
            elif result is None or result.get('error'):
                # Failure path already handled by _aimd_release_failure in fetch methods
                # F206AS: Record failure to canonical circuit breaker if available
                is_timeout = result.get('error') == 'timeout' if result else True
                self._record_canonical_failure(domain, is_timeout=is_timeout, failure_kind='fetch_error')

        except Exception as e:
            logger.warning(f"[_fetch_url] Unexpected error for {url}: {e}")
            self._aimd_release_failure()
            result = {'url': url, 'content': b'', 'error': str(e)}
        finally:
            # Safety net: always release AIMD semaphore slot if acquired.
            # Normal success path: _aimd_release_success() called at line 1170.
            # Normal failure path: _aimd_release_failure() called in fetch methods (lines 763, 767, 793).
            # Exception path: _aimd_release_failure() called at line 1177.
            # In all cases, semaphore.release() must be called to avoid resource leak.
            if self._aimd_semaphore is not None:
                try:
                    self._aimd_semaphore.release()
                except ValueError:
                    pass  # Semaphore not acquired or already released

        # Sprint 46: Handle 401/403 - rotate credentials
        if result and result.get('status_code') in (401, 403):
            if self._session_manager:
                await self._session_manager.rotate_credentials(domain)
                logger.info(f"[SESSION] Rotated credentials for {domain}")

        # Sprint 46: Paywall bypass - check content for paywall indicators
        if result and result.get('content'):
            content = result['content']
            if isinstance(content, bytes):
                content = content.decode(errors='ignore')

            # Try paywall bypass if content is small or paywall detected
            if len(content) < 5000 and self._paywall_bypass:
                bypass_result = await self._paywall_bypass.bypass(url, content)
                if bypass_result:
                    logger.info(f"[PAYWALL] Bypassed via {bypass_result.get('bypassed')}")
                    result['content'] = bypass_result.get('content', '').encode()
                    result['bypassed'] = bypass_result.get('bypassed')
                    result['paywall'] = bypass_result.get('paywall')

        trace_fetch_end(url, "none", "done", 0.0)
        return result

    # ==========================================================================
    # Sprint 8BH: Deep Research — lawful surface + archival search (no Docker)
    # ==========================================================================

    async def _maybe_deep_research(self, query: str, limit: int = 10) -> Optional[List[Dict[str, Any]]]:
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
            from ..tools.ddgs_client import search_text_sync, search_news_sync
            from ..tools.deep_research_sources import wayback_cdx_lookup, urlscan_search
            from ..tools.search_fusion import top_k

            # Parallel fan-out: DDGS text, DDGS news, Wayback CDX, urlscan
            # Sprint 4B: All 4 tasks use gather with return_exceptions=True
            ddgs_task = asyncio.to_thread(search_text_sync, query)
            news_task = asyncio.to_thread(search_news_sync, query)
            wayback_task = wayback_cdx_lookup(query, limit=8)
            urlscan_task = urlscan_search(query, size=8)

            ddgs_rows, news_rows, wayback_rows, urlscan_rows = await asyncio.gather(
                ddgs_task, news_task, wayback_task, urlscan_task, return_exceptions=True
            )

            # Sprint 4B: Gather hygiene - collect with explicit exception logging
            rows: List[Dict[str, Any]] = []
            for part, label in [(ddgs_rows, "ddgs"), (news_rows, "news"),
                                 (wayback_rows, "wayback"), (urlscan_rows, "urlscan")]:
                if isinstance(part, list):
                    rows.extend(part)
                elif isinstance(part, Exception):
                    # Sprint 4B: Explicit exception logging (no silent failure)
                    logger.debug(f"[DEEP] {label} failed: {type(part).__name__}: {part}")

            if not rows:
                return None

            fused = top_k(rows, k=limit)
            logger.info(f"[DEEP] query={query!r} → {len(rows)} raw rows → {len(fused)} fused")
            return fused

        except Exception as e:
            logger.debug(f"[DEEP] research failed: {e}")
            return None

    async def _do_shutdown(self, ctx: Dict[str, Any]) -> None:
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
        self._processed_urls = create_rotating_bloom_filter()

        # Sprint F3/F8/F9: persist local corpus manifest at windup
        if self._corpus_ingester is not None:
            try:
                self._corpus_ingester.close()
            except Exception as e:
                logger.debug(f"CorpusIngester close failed: {e}")

        # F300M: Cleanup SessionManager and LMDB env — correct order:
        # 1. SessionManager.close() first (closes ThreadPoolExecutor)
        # 2. Then lmdb_env.close() (closes LMDB environment)
        if self._session_manager is not None:
            try:
                await self._session_manager.close()
            except Exception:
                pass
            self._session_manager = None
        if self._session_lmdb_env is not None:
            try:
                self._session_lmdb_env.close()
            except Exception:
                pass
            self._session_lmdb_env = None

        # Sprint 76: Cleanup Tor sessions with drain
        for session in self._tor_sessions.values():
            try:
                await session.close()
            except Exception:
                pass
        self._tor_sessions.clear()
        self._tor_last_used.clear()

        # Sprint 4B: Small drain to allow SSL/TCP to flush
        await asyncio.sleep(0.25)
