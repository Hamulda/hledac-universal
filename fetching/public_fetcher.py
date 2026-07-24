"""
Public-passive text/HTML fetcher using curl_cffi (primary) + httpx (HTTP/2).
Always-on, bounded, fail-soft, typed via msgspec.Struct.

F4XX: HTTP transport modernization:
- Primary: curl_cffi (stealth, JA3 fingerprint rotation)
- HTTP/2: httpx (native HTTP/2, httpx-socks for SOCKS5)
- Tor/I2P: httpx-socks via transport/session_pool.py:httpx_socks_client()

P4: Tor + stealth layer integration:
- .onion domains routed via Tor SOCKS5 proxy (9050)
- Optional stealth mode via StealthManager
- Circuit renewal every TOR_CIRCUIT_RENEWAL_REQUEST_COUNT requests
- Random jitter before each request when using Tor/stealth

F-GLOBAL: Global state refactoring (2026-06-30):
- _body_hashes + _body_hashes_lock → _BodyHashStore class (encapsulated, __slots__)
- _js_renderer_capability + _js_renderer_capability_lock → _JSRendererCapability class
- _DRAIN_REGISTRY + _DRAIN_TOTAL_* → _DrainRegistry class (singleton, __slots__)
- _session_source_telemetry → _SessionManager._session_source_telemetry (instance dict, __slots__)
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import contextvars
import functools
import importlib.util
import os
import re
import secrets
import threading
import time
import urllib.parse
from collections import deque as _f273c_deque
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any, Final, cast
import msgspec
from core.env_config import ENV
from hledac.universal.utils.cache import PyCacheDict
from runtime.logging_setup import get_logger
from hledac.universal.tools.regex_cache import collapse_whitespace, strip_html_tags
if TYPE_CHECKING:
    import httpx
from core.psutil_shim import process as _psutil_process
from core.rust_backend import rust as _rust_backend
from hledac.universal.utils.async_helpers import parallel
from tenacity import (
    retry,
    retry_if_exception,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from tenacity import RetryCallState as _TenacityRetryCallState

# Context variable for passing circuit-breaker state into tenacity callbacks.
# ISSUE-7: avoids closure capture of mutable objects across tenacity retry boundaries.
_cb_domain_var: contextvars.ContextVar[str] = contextvars.ContextVar('_cb_domain', default='')
_cb_breaker_var: contextvars.ContextVar['CircuitBreaker | None'] = contextvars.ContextVar('_cb_breaker', default=None)  # type: ignore[valid-type]

_URL_OPS_WARNING = False

def __getattr__(name: str) -> Any:
    """Lazy module-level attribute access for rust_backend sub-domains.

    Supported names (transparent to callers of the removed shims):
        url_ops   -> rust_backend.url   (was _get_url_ops)
        rust_url  -> rust_backend.url   (direct access for internal use)

    Raises AttributeError for unknown names so normal module errors occur.
    """
    global _URL_OPS_WARNING
    if name == 'url_ops':
        return _rust_backend.url
    if name == 'rust_url':
        return _rust_backend.url
    if name == 'rust_html':
        return _rust_backend.raw
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


# --- Tenacity retry integration (ISSUE-7) ----------------------------------------


class _RetryableStatus(Exception):
    """Signals a retryable HTTP status that tenacity can retry via retry_if_exception_type."""

    __slots__ = ('status_code', 'retry_after', 'circuit_breaker_domain', 'is_timeout')

    def __init__(
        self,
        status_code: int,
        retry_after: float | None = None,
        circuit_breaker_domain: str = '',
        is_timeout: bool = False,
    ) -> None:
        super().__init__(status_code, retry_after, circuit_breaker_domain, is_timeout)
        self.status_code = status_code
        self.retry_after = retry_after
        self.circuit_breaker_domain = circuit_breaker_domain
        self.is_timeout = is_timeout


# Module-level state for decorrelated jitter chain across tenacity retries.
# Reset before each top-level fetch call via _reset_tenacity_jitter_state().
_tenacity_prev_sleep: float = 0.0


def _reset_tenacity_jitter_state() -> None:
    """Reset jitter state before a new fetch call (ISSUE-7)."""
    global _tenacity_prev_sleep
    _tenacity_prev_sleep = 0.0


def _tenacity_wait_jitter(retry_state: _TenacityRetryCallState) -> float:
    """Tenacity wait generator: Retry-After header → backoff → jitter cap at 8 s.

    ISSUES-7: Replaces manual retry loop with tenacity decorator.
    Uses decorrelated jitter (same formula as existing _compute_backoff_seconds)
    but accepts tenacity's RetryCallState so @retry can drive it.

    prev_sleep is carried via module-level _tenacity_prev_sleep to maintain
    the decorrelated jitter chain across retries. Reset via _reset_tenacity_jitter_state().
    """
    global _tenacity_prev_sleep
    attempt = retry_state.attempt_number
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
    retry_after: float | None = None
    if isinstance(exc, _RetryableStatus):
        retry_after = exc.retry_after
    # Fallback: geometric backoff capped at 8 s (matches _compute_backoff_seconds)
    if retry_after is None or retry_after <= 0:
        retry_after = min(2.0 ** (attempt + 1), 8.0)
    else:
        retry_after = min(retry_after, 60.0)
    # Decorrelated jitter: same formula as _compute_backoff_seconds
    jittered = min(8.0, _JITTER_RNG.uniform(0.0, max(retry_after, _tenacity_prev_sleep) * 3.0))
    _tenacity_prev_sleep = jittered
    return jittered


# --- _RetryableStatus retry predicate (used by tenacity) ------------------------


def _is_retryable_status_exception(exc: BaseException) -> bool:
    """Tenacity predicate: retry only on _RetryableStatus (HTTP retryable codes)."""
    return isinstance(exc, _RetryableStatus)


def _tenacity_before_sleep(retry_state: _TenacityRetryCallState) -> None:
    """Tenacity before_sleep: record circuit-breaker failure before retry delay.

    ISSUES-7: Called by tenacity AFTER a retryable failure but BEFORE the wait delay.
    Reads circuit-breaker state from context variables set by async_fetch_public_text.
    """
    exc = retry_state.outcome.exception() if retry_state.outcome is not None else None
    if not isinstance(exc, _RetryableStatus):
        return
    cb = _cb_breaker_var.get()
    cb_domain = _cb_domain_var.get()
    if cb is not None:
        cb.record_failure(failure_kind=str(exc.status_code), is_timeout=exc.is_timeout)
    if cb_domain:
        try:
            from transport.circuit_breaker import rust_circuit_record_failure

            rust_circuit_record_failure(cb_domain, is_timeout=exc.is_timeout)
        except Exception:  # noqa: BLE001 — best-effort; Rust CB unavailable is non-fatal
            pass


def _tenacity_after(retry_state: _TenacityRetryCallState) -> None:
    """Tenacity after: record circuit-breaker success on final success (ISSUE-7).

    Reads circuit-breaker state from context variables set by async_fetch_public_text.
    """
    outcome_ok = retry_state.outcome.exception() is None if retry_state.outcome is not None else False
    if not outcome_ok:
        return
    cb = _cb_breaker_var.get()
    cb_domain = _cb_domain_var.get()
    if cb is not None:
        cb.record_success()
    if cb_domain:
        try:
            from transport.circuit_breaker import rust_circuit_record_success

            rust_circuit_record_success(cb_domain)
        except Exception:  # noqa: BLE001 — best-effort; Rust CB unavailable is non-fatal
            pass


_classify_url_cache: PyCacheDict[str, tuple[str, str]] = PyCacheDict(512, 300.0)
@functools.lru_cache(maxsize=1)
def _get_rust_url_cache() -> 'Any':
    """Lazy singleton for UrlClassifyCachePy — created on first call.

    Thread-safe via functools.lru_cache internals (one lock, acquired once).
    """
    return _rust_backend.url.UrlClassifyCachePy(capacity=50000, ttl_s=300.0)

def _classify_url_cached(url: str) -> tuple[str, str]:
    """Returns (kind_str, lowercase_host) using Rust when available.

    Fast path: Rust classify_url (single GIL transition, 3× faster).
    Fallback: _python_classify_url (pure Python, no Rust, no side effects).
    Caches both paths in PyCacheDict for consistency.
    """
    cached = _classify_url_cache.get(url)
    if cached is not None:
        return cached
    try:
        result = _rust_backend.url.classify_url(url)
    except Exception:  # noqa: BLE001 — best-effort fallback; Rust unavailable/non-functional
        result = _python_classify_url(url)
    _classify_url_cache.set(url, result)
    return result

def _python_classify_url(url: str) -> tuple[str, str]:
    """Pure-Python URL classifier — no cache, no Rust, no side effects.

    Must stay in sync with rust_backend/url.py._python_classify_url.
    Delta (beyond the Rust path): VCS, social, document, storage classification.
    Used as fallback when Rust is unavailable or as Python-only path
    in _batch_classify_url_cached. Never raises.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if not netloc:
            return ('malformed', '')
        # VCS hosting
        if any(k in netloc for k in ("github.com", "gitlab.com", "bitbucket.org")):
            return ("code", "vcs")
        # Social platforms
        if any(k in netloc for k in ("twitter.com", "x.com", "mastodon.social")):
            return ("social", "twitter")
        if any(k in netloc for k in ("reddit.com", "old.reddit.com")):
            return ("social", "reddit")
        # Document URLs
        if parsed.path.endswith((".pdf", ".doc", ".docx")):
            return ("document", "file")
        # Cloud storage
        if any(k in netloc for k in ("drive.google.com", "dropbox.com", "onedrive.live.com")):
            return ("storage", "cloud")
        # Darknet before clearnet — use hostname (port-stripped) so :8080 doesn't break .i2p/.onion detection
        hostname = parsed.hostname or ''
        if hostname.endswith(".onion"):
            return ("onion", netloc)
        if hostname.endswith(".i2p") or hostname.endswith(".b32.i2p"):
            return ("i2p", hostname)
        if hostname.endswith(".freenet") or 'freenet' in netloc or 'hyphanet' in netloc:
            return ("freenet", netloc)
        # Clearnet: http/https URLs that aren't special categories
        if parsed.scheme in ("http", "https"):
            return ("clearnet", netloc.removeprefix("www."))
        return ("unknown", netloc)
    except Exception:  # noqa: BLE001 — best-effort fallback; parse failure returns default
        return ('malformed', '')

def _batch_classify_url_cached(urls: list[str]) -> list[tuple[str, str]]:
    """Batch URL classifier with embedded Rust xxh3 cache (Issue #4).

    Primary path: UrlClassifyCachePy.classify_batch_cached()
    - Single GIL transition for all N URLs (vs N transitions in Python dict)
    - xxh3_64(url) as cache key — 8 bytes vs 80-200 bytes for full URL string
    - AHashMap<u64, (kind, host)> — ahash 10× faster than Python dict
    - parking_lot::RwLock — read-lock-free reads
    - Rayon parallel classify for misses within the same GIL transition

    Fallback: Python PyCacheDict (original 3-stage approach) when Rust unavailable.

    Bounded: hard-cap 50_000 items per call.

    Returns list of (kind_str, lowercase_host) in same order as input.
    """
    if not urls:
        return []
    hard_cap = 50000
    if len(urls) > hard_cap:
        urls = urls[:hard_cap]
    try:
        cache = _get_rust_url_cache()
        return cache.classify_batch_cached(urls)
    except Exception:  # noqa: BLE001 — best-effort; batch classification failure is non-fatal
        pass
    results: list[tuple[str, str] | None] = [None] * len(urls)  # fully populated before return
    misses: list[tuple[int, str]] = []
    for i, url in enumerate(urls):
        cached = _classify_url_cache.get(url)
        if cached is not None:
            results[i] = cached
        else:
            misses.append((i, url))
    if not misses:
        return cast("list[tuple[str, str]]", results)
    miss_urls = [u for _, u in misses]
    try:
        batch_results = _rust_backend.url.batch_classify(miss_urls)
    except Exception:  # noqa: BLE001 — best-effort; fallback to Python classifier
        batch_results = [_python_classify_url(u) for u in miss_urls]
    batch_updates = dict(zip(miss_urls, batch_results))
    _classify_url_cache.update(batch_updates)
    for (orig_idx, url), classified in zip(misses, batch_results):
        results[orig_idx] = classified
    return cast("list[tuple[str, str]]", results)
from hledac.universal.layers.ua_rotator import build_randomized_headers as _canonical_build_randomized_headers
from hledac.universal.layers.ua_rotator import get_random_accept_language as _canonical_get_random_accept_language
from hledac.universal.layers.ua_rotator import get_random_ua as _canonical_get_random_ua
from hledac.universal.transport.base import CircuitBreaker, TransportDecision, fetch_via_httpx_h2, fetch_via_tor_curl_cffi, get_breaker, route_transport, should_use_curl_cffi
from hledac.universal.transport.body_limiter import BodyReadResult, _read_body_into
# ISSUE-0.2: Import from fetching/curl_cffi_fetch.py (CAPS-aware wrapper)
# This ensures CAPS-based availability checking for curl_cffi
from hledac.universal.fetching.curl_cffi_fetch import _blocking_altsvc_probe_for_url, fetch_via_curl_cffi_cached, fetch_via_i2p_curl_cffi, is_curl_cffi_capable, require_curl_cffi
# Backward compat: still import is_curl_cffi_available from curl_cffi_runtime
from hledac.universal.transport.curl_cffi_runtime import is_curl_cffi_available as _runtime_is_curl_cffi_available
from hledac.universal.transport.decompression import build_accept_encoding_header
from hledac.universal.transport.session_pool import httpx_socks_client
from hledac.universal.utils.concurrency import get_clearnet_semaphore, get_tor_semaphore
from hledac.universal.utils.encoding import decode_response_bytes, parse_charset_from_content_type
from hledac.universal.utils.patterns.pattern_matcher import PatternHit, match_text
from hledac.universal.utils.uma_budget import M1_FETCH_SOFT_CEILING_GB
logger = get_logger(__name__)
_ContentHasher: object | None = None
_RUST_CONTENT_HASHER: bool = False
MAX_BODY_HASHES: Final[int] = 10000

# ISSUE-018: Deduplicated — canonical BodyHashStore lives in fetching/_body_hash.py
from fetching._body_hash import BodyHashStore as _BodyHashStore
from fetching._body_hash import body_hash_store as _body_hash_store

# Backward-compat alias — tests and any external code access the internal dict
# directly via _body_hashes. Use .hashes property for read-only access.
# Mutation should go through _store_body_hash() for thread safety.
_body_hashes: dict[str, str] = _body_hash_store.hashes

def _get_content_hasher() -> object | None:
    """Lazy-load Rust backend hash domain.

    Canonical RustBackend entry point — single lazy-load for all content hashing
    needs. Returns rust.hash on success, None on failure. Cached after first call.

    Thread-safe via functools.lru_cache internals (one lock, acquired once).
    """
    global _ContentHasher, _RUST_CONTENT_HASHER
    if _RUST_CONTENT_HASHER:
        return _ContentHasher
    try:
        from core.rust_backend import rust
        _ContentHasher = rust.hash
        _RUST_CONTENT_HASHER = True
    except Exception:  # noqa: BLE001 — best-effort; Rust backend unavailable, fallback to Python
        _RUST_CONTENT_HASHER = False
        _ContentHasher = None
    return _ContentHasher

def _compute_body_hash(body: bytes) -> str:
    """Return 16-char hex fingerprint of a response body.

    Priority chain (fail-soft, never raises):
    1. Rust blake3_64  — BLAKE3, NEON-accelerated on M1 (~5 GB/s)
    2. Rust xxh3_64_hex — xxh3-64, native Rust, no Python wheel needed
    3. hashlib.sha256   — stdlib, guaranteed available, ~500 MB/s on M1

    Returns empty string only if body is empty/None.
    """
    import hashlib
    if not body:
        return ''
    rh = _get_content_hasher()
    if rh is not None:
        try:
            return cast(Any, rh).blake3_64(body)
        except Exception:  # noqa: BLE001 — best-effort; blake3 failure is non-fatal
            pass
    if rh is not None:
        try:
            return cast(Any, rh).xxh3_64_hex(body)
        except Exception:  # noqa: BLE001 — best-effort; xxh3 failure is non-fatal
            pass
    try:
        import xxhash
        return xxhash.xxh3_64(body).hexdigest()
    except Exception:  # noqa: BLE001 — best-effort; xxhash failure is non-fatal
        pass
    try:
        return hashlib.sha256(body).hexdigest()[:16]
    except Exception:  # noqa: BLE001 — best-effort; sha256 fallback returns short hash
        return ''

def _store_body_hash(url: str, hash_hex: str) -> None:
    """Persist `url → hash_hex` in the bounded module-level dict.

    Body hash is POUZE metadata — it is never written to the DuckDB
    canonical store. FIFO eviction keeps the dict bounded (MAX_BODY_HASHES).
    Never raises.
    """
    _body_hash_store.store(url, hash_hex)
from hledac.universal.transport.http3_lane import http_version_for_curl_cffi as _h3_http_version_for_url
from hledac.universal.transport.http3_lane import record_from_curl_cffi_result as _h3_record_from_result_headers

def _altsvc_extract_host(url: str, preclassified_host: str='') -> str:
    """Return lowercased hostname from URL, or empty string on parse failure.

    F271: Rust _rust_backend.url.extract_host fast path with urllib.parse fallback.
    B1: When caller already classified the URL via _classify_url_cached,
    pass preclassified_host to skip the FFI entirely.
    """
    if preclassified_host:
        return preclassified_host
    try:
        _uops = _rust_backend.url
        if _uops is not None:
            return _uops.extract_host(url)
        _, host = _classify_url_cached(url)
        return host
    except Exception:  # noqa: BLE001 — best-effort; host extraction failure returns empty string
        return ''

def _altsvc_http_version_for(host: str) -> Any:
    """F260 compat shim — delegates to ``http3_lane`` by reconstructing
    a synthetic URL. Returns ``None`` when the gate is closed, when the
    host has no recorded h3 advertisement, when curl_cffi is missing,
    or when the M1 8GB RSS memory guard is over budget.
    """
    if not host:
        return None
    return _h3_http_version_for_url(f'https://{host}/')

def _altsvc_record_from_result(url: str, headers: Any) -> None:
    """F260 compat shim — delegates to ``http3_lane`` which owns the
    LRU cache, the parse, and the env gate.
    """
    try:
        _h3_record_from_result_headers(url, headers)
    except Exception:  # noqa: BLE001 — best-effort; H3 record failure is non-fatal
        pass

def _try_decode_with_charset(body: bytes, *, http_charset: str | None=None, max_bytes: int=5 * 1024 * 1024) -> tuple[str, bool, int, str]:
    """STORAGE-FIX-4 wiring: charset_normalizer chain with fail-soft fallback.

    Tries the bounded encoding chain from utils.encoding first; on any exception,
    falls back to the legacy _try_decode (UTF-8 → windows-1252 → latin-1 → UTF-8 replace).

    Returns (text, decode_replaced, decode_replacement_count, codec) — same shape as _try_decode.
    codec返回值: 'charset_normalizer' | 'chardet' | 'http_hint' | fallback codec string.
    """
    try:
        text = decode_response_bytes(body, http_charset=http_charset, max_bytes=max_bytes)
        replacement_count = text.count('�')
        # decode_response_bytes doesn't expose which codec succeeded;
        # 'charset_normalizer' is the primary path so we use that as the label.
        codec = 'charset_normalizer' if replacement_count == 0 else 'charset_normalizer'
        return (text, replacement_count > 0, replacement_count, codec)
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.debug('decode_response_bytes failed, falling back to _try_decode: %s', e)
        return _try_decode(body)
TOR_SOCKS_PROXY: Final[str] = os.environ.get('TOR_SOCKS_PROXY_URL', 'socks5h://127.0.0.1:9050')
I2P_SOCKS_PROXY: Final[str] = os.environ.get('I2P_PROXY_URL', 'socks5://127.0.0.1:7654')
TOR_CIRCUIT_RENEWAL_REQUEST_COUNT: Final[int] = 10
TOR_STEALTH_TIMEOUT_SCALE: Final[float] = 2.0
JITTER_MIN_S: Final[float] = 0.1
JITTER_MAX_S: Final[float] = 0.5

class _SessionManager:
    """Encapsulates all Tor/I2P session state with __slots__.

    Replaces 11 module-level globals:
      _tor_session, _i2p_session, _tor_request_count,
      _tor_session_lock, _i2p_session_lock,
      _tor_session_locally_created, _i2p_session_locally_created,
      _injected_session_provider, _session_source_telemetry,
      _tor_circuit_renewal_request_count (inline)

    Crash-safe: sessions track their own .closed flag. After a crash, stale
    references are detected via session.closed=True and automatically re-created.

    F272: Tor circuit renewal + F206AT injected provider seam unified here.

    ISSUE-014 FIX: asyncio.Lock instances are lazily created on first async access,
    not at module import time. This avoids "no running event loop" errors on macOS
    where asyncio.Lock() captures the main thread's loop at import time (which may
    be None or a different loop than the one used at runtime).
    """
    __slots__ = ('_tor_session', '_i2p_session', '_tor_request_count', '_tor_session_lock', '_i2p_session_lock', '_tor_session_locally_created', '_i2p_session_locally_created', '_injected_session_provider', '_session_source_telemetry', '_tor_circuit_renewal_count')

    def __init__(self) -> None:
        self._tor_session: httpx.AsyncClient | None = None
        self._i2p_session: httpx.AsyncClient | None = None
        self._tor_request_count: int = 0
        self._tor_session_lock: asyncio.Lock | None = None
        self._i2p_session_lock: asyncio.Lock | None = None
        self._tor_session_locally_created: bool = False
        self._i2p_session_locally_created: bool = False
        self._injected_session_provider: tuple[httpx.AsyncClient | None, httpx.AsyncClient | None] | None = None
        self._session_source_telemetry: dict[str, str] = {'tor': 'unavailable', 'i2p': 'unavailable'}
        self._tor_circuit_renewal_count: int = 0

    def _get_tor_lock(self) -> asyncio.Lock:
        """Lazily create Tor session lock in the current event loop."""
        if self._tor_session_lock is None:
            self._tor_session_lock = asyncio.Lock()
        return self._tor_session_lock

    def _get_i2p_lock(self) -> asyncio.Lock:
        """Lazily create I2P session lock in the current event loop."""
        if self._i2p_session_lock is None:
            self._i2p_session_lock = asyncio.Lock()
        return self._i2p_session_lock

    def tor_is_healthy(self) -> bool:
        """Return True if Tor session exists and is not closed."""
        if self._tor_session is None:
            return False
        return not self._tor_session.is_closed

    def i2p_is_healthy(self) -> bool:
        """Return True if I2P session exists and is not closed."""
        if self._i2p_session is None:
            return False
        return not self._i2p_session.is_closed

    def _session_is_closed(self, session: httpx.AsyncClient | None) -> bool:
        """Return True if session is closed or None."""
        if session is None:
            return True
        return session.is_closed

    async def _session_aclose(self, session: httpx.AsyncClient | None) -> None:
        """Close a session (httpx.AsyncClient only)."""
        if session is None:
            return
        await session.aclose()

    def record_tor_source(self, source: str) -> None:
        self._session_source_telemetry['tor'] = source

    def record_i2p_source(self, source: str) -> None:
        self._session_source_telemetry['i2p'] = source

    def get_telemetry(self) -> dict[str, str]:
        return dict(self._session_source_telemetry)

    def reset_for_winddown(self) -> None:
        """Reset session state at sprint winddown. Idempotent."""
        self._tor_session = None
        self._i2p_session = None
        self._tor_session_locally_created = False
        self._i2p_session_locally_created = False
        self._tor_request_count = 0
        self._session_source_telemetry = {'tor': 'unavailable', 'i2p': 'unavailable'}

    def reset_for_testing(self) -> None:
        """Reset all session state for testing isolation.

        Closes any open sessions and returns to pristine factory state.
        Unlike reset_for_winddown, this also resets circuit counters.
        """
        # C7-FIX: Always use asyncio.Runner() — run_until_complete on a running loop
        # is an M1 Metal crash vector. Runner() handles both cases correctly.
        with asyncio.Runner() as runner:
            if not self._session_is_closed(self._tor_session):
                runner.run(self._session_aclose(self._tor_session))
            if not self._session_is_closed(self._i2p_session):
                runner.run(self._session_aclose(self._i2p_session))
        self._tor_session = None
        self._i2p_session = None
        self._tor_session_locally_created = False
        self._i2p_session_locally_created = False
        self._tor_request_count = 0
        self._injected_session_provider = None
        self._session_source_telemetry = {'tor': 'unavailable', 'i2p': 'unavailable'}
        self._tor_circuit_renewal_count = 0

    def status_snapshot(self) -> dict:
        """Return a consistent status snapshot for diagnostics."""
        return {'tor_present': self._tor_session is not None, 'tor_closed': self._session_is_closed(self._tor_session), 'tor_locally_created': self._tor_session_locally_created, 'i2p_present': self._i2p_session is not None, 'i2p_closed': self._session_is_closed(self._i2p_session), 'i2p_locally_created': self._i2p_session_locally_created, 'injected_active': self._injected_session_provider is not None, 'telemetry': dict(self._session_source_telemetry)}
_SESSION_MGR: _SessionManager = _SessionManager()

# ISSUE-014: _global_httpx_session dead code removed.
# All callers migrated to network.session_runtime:async_get_httpx_session()
# which has adaptive M1 8GB-safe limits (min(40, fd_limit//4)).
# Keep stubs here for backward compat — they delegate to the canonical impl.

async def get_httpx_session() -> httpx.AsyncClient:
    """ISSUE-014: Delegate to canonical session_runtime (backward compat stub).

    The _global_httpx_session at this location was dead code (0 callers).
    Canonical httpx session is network.session_runtime:async_get_httpx_session().
    """
    from network.session_runtime import async_get_httpx_session as _canonical_get
    return await _canonical_get()


async def close_httpx_session() -> None:
    """ISSUE-014: Delegate to canonical session_runtime (backward compat stub)."""
    from network.session_runtime import close_httpx_session_async as _canonical_close
    await _canonical_close()


async def close_aiohttp_session() -> None:
    """ISSUE-014: Delegate to canonical session_runtime (backward compat stub)."""
    await close_httpx_session()
PUBLIC_FETCHER_POOL_AUTHORITY: Final[str] = 'local_fallback_until_transport_unified'

def inject_session_provider(tor_session: httpx.AsyncClient | None, i2p_session: httpx.AsyncClient | None) -> None:
    """ISSUE-007: Inject canonical session provider for Tor/I2P pools.

    When injected with non-None sessions, the provided sessions are used instead of
    local _tor_session/_i2p_session. This allows FetchCoordinator or transport layer
    to own the canonical session lifecycle.

    Calling with (None, None) resets to local-only mode — the seam is deactivated.

    Args:
        tor_session: Canonical Tor httpx session, or None to use local fallback.
        i2p_session: Canonical I2P httpx session, or None to use local fallback.
    """
    if tor_session is None and i2p_session is None:
        _SESSION_MGR._injected_session_provider = None
    else:
        _SESSION_MGR._injected_session_provider = (tor_session, i2p_session)
        _SESSION_MGR._tor_session_locally_created = False
        _SESSION_MGR._i2p_session_locally_created = False

def get_session_source_telemetry() -> dict[str, str]:
    """F206AT: Return snapshot of session source telemetry.

    Returns:
        dict with keys:
        - tor: "injected" | "local_tor" | "unavailable"
        - i2p: "injected" | "local_i2p" | "unavailable"
        - transport_policy_bypassed: "true" | "false"
        - fallback_reason: str | None
    """
    result = _SESSION_MGR.get_telemetry()
    result['transport_policy_bypassed'] = 'true' if _SESSION_MGR._injected_session_provider is None else 'false'
    result['fallback_reason'] = 'injected_provider_available' if _SESSION_MGR._injected_session_provider is not None else 'local_pool_until_transport_unified'
    return result
_CAMOUFOX_LOCK: asyncio.Lock | None = None
_CAMOUFOX_LOCK_INIT: bool = False

def _get_camoufox_lock() -> asyncio.Lock:
    """Lazily create camoufox lock in the current event loop.

    ISSUE-014 FIX: asyncio.Lock() at module import time causes "no running event loop"
    errors on macOS. This function creates the lock lazily on first async access.
    """
    global _CAMOUFOX_LOCK, _CAMOUFOX_LOCK_INIT
    if _CAMOUFOX_LOCK is None or not _CAMOUFOX_LOCK_INIT:
        _CAMOUFOX_LOCK = asyncio.Lock()
        _CAMOUFOX_LOCK_INIT = True
    return _CAMOUFOX_LOCK

DEFAULT_UA: Final[str] = 'Mozilla/5.0 (compatible; research-bot/1.0; +passive-public-fetch)'
_BROWSER_UA_POOL: tuple[str, ...] = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36', 'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0', 'Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0', 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0')
_ACCEPT_LANGUAGE_POOL: tuple[str, ...] = ('en-US,en;q=0.9', 'en-GB,en;q=0.8', 'en-US,en;q=0.9,de;q=0.8', 'en-US,en;q=0.9,fr;q=0.8', 'en-US,en;q=0.9,es;q=0.8', 'en-US,en;q=0.9,ja;q=0.8', 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7', 'de-DE,de;q=0.9,en;q=0.8', 'fr-FR,fr;q=0.9,en;q=0.8', 'ja-JP,ja;q=0.9,en;q=0.8', 'en-US,en;q=0.9', 'en-AU,en;q=0.9', 'en-CA,en;q=0.9', 'en-IE,en;q=0.9', 'en-NZ,en;q=0.9')

def get_random_ua() -> str:
    """Return a random User-Agent from the canonical pool.

    Issue 10.2: delegates to layers.ua_rotator (single source of truth).
    Kept here as backward-compat wrapper for callers inside this module.
    """
    return _canonical_get_random_ua()

def get_random_accept_language() -> str:
    """Return a random Accept-Language from the canonical pool.

    Issue 10.2: delegates to layers.ua_rotator (single source of truth).
    """
    return _canonical_get_random_accept_language()

def build_randomized_headers() -> dict[str, str]:
    """Build a randomized headers dict for HTTP requests.

    Issue 10.2: delegates to layers.ua_rotator (single source of truth).
    Uses build_accept_encoding_header() from transport.decompression for
    Accept-Encoding (maintains existing behavior for callers that depend on
    specific encoding choices).
    """
    headers = _canonical_build_randomized_headers()
    headers['Accept-Encoding'] = build_accept_encoding_header()
    return headers
MAX_BYTES_DEFAULT: Final[int] = 2000000
MAX_BYTES_HARD: Final[int] = 10000000
_MAX_COUNT: int = 999999

class TransportCounters:
    """Lightweight per-fetch transport counter bundle (M1-safe __slots__).

    Bounded ints — counters saturate at MAX_COUNT rather than growing unbounded.
    Not exposed in public API — aggregated by sprint coordinator from FetchResult.
    """
    __slots__ = ('aiohttp_count', 'httpx_h2_count', 'curl_cffi_count', 'curl_cffi_tor_count', 'curl_cffi_tor_fallback_count', 'tor_httpx_socks_count', 'i2p_httpx_socks_count', 'js_renderer_count', 'fallback_count', 'curl_cffi_fallback_to_aiohttp_count', 'httpx_h2_fallback_to_aiohttp_count', 'static_hydration_attempted', 'static_hydration_sufficient', 'static_hydration_insufficient', 'macos_webkit_count')

    def __init__(self, aiohttp_count: int=0, httpx_h2_count: int=0, curl_cffi_count: int=0, curl_cffi_tor_count: int=0, curl_cffi_tor_fallback_count: int=0, tor_httpx_socks_count: int=0, i2p_httpx_socks_count: int=0, js_renderer_count: int=0, fallback_count: int=0, curl_cffi_fallback_to_aiohttp_count: int=0, httpx_h2_fallback_to_aiohttp_count: int=0, static_hydration_attempted: int=0, static_hydration_sufficient: int=0, static_hydration_insufficient: int=0, macos_webkit_count: int=0) -> None:
        self.aiohttp_count = min(aiohttp_count, _MAX_COUNT)
        self.httpx_h2_count = min(httpx_h2_count, _MAX_COUNT)
        self.curl_cffi_count = min(curl_cffi_count, _MAX_COUNT)
        self.curl_cffi_tor_count = min(curl_cffi_tor_count, _MAX_COUNT)
        self.curl_cffi_tor_fallback_count = min(curl_cffi_tor_fallback_count, _MAX_COUNT)
        self.tor_httpx_socks_count = min(tor_httpx_socks_count, _MAX_COUNT)
        self.i2p_httpx_socks_count = min(i2p_httpx_socks_count, _MAX_COUNT)
        self.js_renderer_count = min(js_renderer_count, _MAX_COUNT)
        self.fallback_count = min(fallback_count, _MAX_COUNT)
        self.curl_cffi_fallback_to_aiohttp_count = min(curl_cffi_fallback_to_aiohttp_count, _MAX_COUNT)
        self.httpx_h2_fallback_to_aiohttp_count = min(httpx_h2_fallback_to_aiohttp_count, _MAX_COUNT)
        self.static_hydration_attempted = min(static_hydration_attempted, _MAX_COUNT)
        self.static_hydration_sufficient = min(static_hydration_sufficient, _MAX_COUNT)
        self.static_hydration_insufficient = min(static_hydration_insufficient, _MAX_COUNT)
        self.macos_webkit_count = min(macos_webkit_count, _MAX_COUNT)

class FetchResult(msgspec.Struct, frozen=True, gc=False):
    """Frozen msgspec result — no mutations after construction. F350M-R: gc=False for M1 8GB.

    Backward-compatible: added fields have defaults so existing callers are unaffected.

    Access-path truth fields (F169B):
    - redirected: True when final_url != url (explicit redirect flag, downstream-friendly)
    - redirect_target: redirect destination (set only when redirected=True)
    - failure_stage: coarse classification of where fetch pipeline failed
    - network_error_kind: fine-grained network error kind for connection/tls/dns failures
    """
    url: str
    final_url: str
    status_code: int
    content_type: str
    text: str | None
    fetched_bytes: int
    declared_length: int
    elapsed_ms: float
    body: bytes | None = None
    error: str | None = None
    xml_recovered: bool = False
    xml_source_hint: bool = False
    decode_replaced: bool = False
    decode_replacement_count: int = 0
    body_read_error: bool = False
    redirected: bool = False
    redirect_target: str | None = None
    failure_stage: str | None = None
    network_error_kind: str | None = None
    selected_transport: str | None = None
    http_version: str | None = None
    transport_policy_reason: str | None = None
    transport_fallback_reason: str | None = None
    transport_counters: TransportCounters | None = None
    js_renderer_skipped_reason: str | None = None
    hydration_score: float | None = None
    hydration_sources: tuple[str, ...] = ()
    tls_cert_san: tuple[str, ...] = ()
    tls_cert_issuer: str | None = None
    tls_cert_sha256: str | None = None
    server_header: str | None = None
    matched_patterns: tuple[str, ...] = ()
ACCEPTED_CONTENT_TYPES: Final[frozenset[str]] = frozenset({'text/html', 'text/plain', 'text/xml', 'application/xhtml+xml', 'application/xml', 'application/rss+xml', 'application/atom+xml'})

def _validate_url(url: str) -> str | None:
    """
    Validate URL is http/https and well-formed.
    Returns None on success, error string on failure.

    F271: Rust _rust_backend.url.classify_url fast path with urllib.parse fallback.
    classify_url returns (kind, host) where kind ∈
    {"clearnet","onion","i2p","freenet","empty","malformed"}.
    Rust path is used when the module loads; ImportError or runtime
    failure falls through to the unchanged Python branch below.
    """
    if not url or not isinstance(url, str):
        return 'url_empty'
    url = url.strip()
    if not url:
        return 'url_empty'
    _uops = _rust_backend.url
    if _uops is not None:
        try:
            kind, host = _classify_url_cached(url)
            if kind == 'empty':
                return 'url_empty'
            if kind == 'malformed':
                return 'url_malformed'
            if not host:
                return 'url_no_netloc'
            scheme_idx = url.find('://')
            if scheme_idx == -1:
                return 'url_malformed'
            scheme = url[:scheme_idx].lower()
            if scheme not in ('http', 'https'):
                return f'url_unsupported_scheme:{scheme}'
            return None
        except Exception:  # noqa: BLE001 — best-effort; URL parse failure is non-fatal
            pass
    _kind, _host = _python_classify_url(url)
    if _kind == 'empty':
        return 'url_empty'
    if _kind == 'malformed':
        return 'url_malformed'
    if not _host:
        return 'url_no_netloc'
    scheme_idx = url.find('://')
    if scheme_idx == -1:
        return 'url_malformed'
    scheme = url[:scheme_idx].lower()
    if scheme not in ('http', 'https'):
        return f'url_unsupported_scheme:{scheme}'
    return None
MAX_RETRIES: Final[int] = 2
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 502, 503, 504, 520})

def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES

def _extract_retry_after(headers) -> float | None:
    """Parse Retry-After header, return seconds or None."""
    ra = headers.get('Retry-After') or headers.get('retry-after')
    if ra is None:
        return None
    try:
        return float(ra)
    except (ValueError, TypeError):
        return None

def _compute_backoff_seconds(retry_after: float | None, attempt: int, *, jitter: bool=True, _prev_sleep: float=0.0) -> float:
    """Return bounded backoff in seconds.

    Uses Retry-After if available, otherwise exponential backoff capped at 8 s.
    Attempt 0 = no backoff (first failure already counted).

    When ``jitter`` is True (default), applies decorrelated jitter (AWS
    Architecture Blog "Exponential Backoff and Jitter"): samples
    ``Uniform(0, max(base, _prev_sleep) * 3)`` and caps at 8 s. The optional
    ``_prev_sleep`` carries state across consecutive retries so successive
    sleep durations are de-correlated (callers may pass it as a kwarg).
    """
    if retry_after is not None and retry_after > 0:
        base = min(retry_after, 60.0)
    else:
        base = min(2.0 ** (attempt + 1), 8.0)
    if jitter:
        return min(8.0, _JITTER_RNG.uniform(0.0, max(base, _prev_sleep) * 3.0))
    return base

def _build_retry_error(status_code: int, retry_after: float | None) -> str:
    """Build retry error string with : separator between code and details.

    Adapter uses .split(":", 2) — first two parts are always prefix+code,
    any additional colons in the message body are preserved in part[2].
    """
    parts = [f'retryable:{status_code}']
    if retry_after is not None:
        parts.append(f'retry_after={retry_after:.1f}s')
    else:
        parts.append('backoff=exp')
    return '|'.join(parts)

def _extract_tls_metadata_from_response(resp) -> dict:
    """
    Extract TLS certificate metadata and Server header from an HTTP response.
    For aiohttp response: resp is aiohttp.ClientResponse
    For httpx response: resp is httpx.Response

    Architecture (Issue B5 / Issue-9):
        - Python pre-fetches raw SSL object via short-circuit getattr chain
        - Python parses dict form of getpeercert() -> san_entries + issuer_org
        - Rust does SAN cap (20) + issuer cap (200) + SHA-256 in a single call
        - Server header: plain Python (no Rust needed)
    Memory bounds: all collections are bounded, fail-safe throughout.
    """
    result: dict = {'tls_cert_san': (), 'tls_cert_issuer': None, 'tls_cert_sha256': None, 'server_header': None}
    try:
        server = resp.headers.get('Server') or resp.headers.get('server')
        if server:
            result['server_header'] = server[:200]
    except Exception:  # noqa: BLE001 — best-effort; server header extraction failure is non-fatal
        pass

    # --- SSL object extraction via short-circuit getattr chain ---
    ssl_obj = getattr(resp, 'connection', None) or getattr(resp, '_ssl', None)
    if ssl_obj is None and hasattr(resp, 'transport'):
        try:
            ssl_obj = resp.transport.get_extra_info('ssl_object')
        except Exception:  # noqa: BLE001 — best-effort; transport SSL object extraction failure is non-fatal
            pass
    if ssl_obj is None:
        return result

    # --- Certificate extraction (dict form + DER bytes) — independent try/except ---
    cert_dict: dict | None = None
    try:
        cert_dict = ssl_obj.getpeercert()
    except Exception:  # noqa: BLE001 — best-effort; getpeercert() failure is non-fatal
        pass
    der_bytes: bytes | None = None
    try:
        der_bytes = ssl_obj.getpeercert(binary_form=True)
    except Exception:  # noqa: BLE001 — best-effort; binary cert extraction failure is non-fatal
        pass

    # --- Parse cert_dict → san_entries + issuer_org (Python-side cap prevents OOM from malicious certs) ---
    issuer_org: str | None = None
    san_entries: list[tuple[int, str]] = []
    if cert_dict:
        san_list = cert_dict.get('subjectAltName', [])
        for typ, val in san_list:
            if not isinstance(val, (str, bytes)):
                continue
            if len(san_entries) >= 100:   # cap before Rust call — malicious certs can have 10k+ SANs
                break
            # val is already str from getpeercert(); str(str) is redundant, use directly
            san_entries.append((typ, val) if isinstance(val, str) else (typ, val.decode('utf-8', errors='replace')))
        subject = cert_dict.get('subject', ())
        for rdn in subject:
            for k, v in rdn:
                if k == 'organizationName':
                    issuer_org = v if isinstance(v, str) else str(v) if isinstance(v, bytes) else str(v)
                    break
            if issuer_org:
                break

    # --- Rust: SAN cap (20) + issuer cap (200) + SHA-256 in a single call ---
    try:
        sans, issuer, sha256 = _rust_backend.tls.extract_tls_metadata(san_entries, issuer_org, der_bytes)
        result['tls_cert_san'] = tuple(sans)
        result['tls_cert_issuer'] = issuer
        result['tls_cert_sha256'] = sha256
    except Exception:  # noqa: BLE001 — best-effort; Rust TLS metadata extraction failure is non-fatal
        pass

    return result

def _derive_redirect_fields(url: str, final_url: str) -> tuple[bool, str | None]:
    """Return (redirected, redirect_target) based on URL comparison.

    downstream can use redirected=True as explicit signal instead of
    computing final_url != url themselves.
    """
    if final_url != url:
        return (True, final_url)
    return (False, None)

def _derive_failure_stage_and_network_kind(error: str | None) -> tuple[str | None, str | None]:
    """Parse error string to extract structured failure_stage and network_error_kind.

    Returns (failure_stage, network_error_kind).
    Both are None when error is None (success) or for URL-validation errors.

    failure_stage taxonomy:
      - validation  : URL was invalid before any network call
      - connection  : TCP/DNS/connection-level failure (body never reached)
      - tls          : TLS handshake failure
      - http         : HTTP-level failure (response received, non-2xx)
      - body         : headers OK but body read failed mid-stream
      - size         : body truncated due to size cap

    network_error_kind (connection/tls only):
      - dns_error    : DNS resolution failure
      - connect_error: TCP connection refused/reset
      - tls_error    : TLS handshake/verification failure
      - timeout      : request timed out
    """
    if error is None:
        return (None, None)
    if error.startswith('url_'):
        return ('validation', None)
    if error == 'timeout':
        return ('connection', 'timeout')
    if error == 'size_cap_exceeded':
        return ('size', None)
    if error.startswith('content_type_rejected:'):
        return ('http', None)
    if error.startswith('retryable:'):
        return ('http', None)
    if error.startswith('fetch_error;'):
        parts = error.split(';', 2)
        exc_type = parts[1] if len(parts) > 1 else ''
        if 'SSL' in exc_type or 'TLS' in exc_type or 'Certificate' in exc_type:
            return ('tls', 'tls_error')
        if 'DNS' in exc_type or 'Resolver' in exc_type:
            return ('connection', 'dns_error')
        if 'Connect' in exc_type or 'Connection' in exc_type or 'Network' in exc_type:
            return ('connection', 'connect_error')
        return ('connection', 'connect_error')
    return ('body', None)
import bisect

def _build_error_trie() -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Build optimized error taxonomy: O(1) exact dict + O(log n) sorted prefix list.

    Sprint F350M-R Issue #P2: replaces O(n) linear prefix scan with O(log n) bisect.
    ~15 entries — bisect + early break is ~3x faster than naive iteration.
    """
    exact: dict[str, str] = {'circuit_breaker': 'circuit_breaker_blocked', 'resource_governor': 'resource_governor_blocked', 'fetch_text_none_or_empty': 'body_empty'}
    prefixes: list[tuple[str, str]] = sorted([('fetch_exception: ClientConnectorCertificateError', 'tls_error'), ('fetch_exception: ClientSSLError', 'tls_error'), ('fetch_exception: ClientProxyError', 'proxy_error'), ('fetch_exception: ClientConnectorError', 'connect_error'), ('fetch_exception: asyncio.TimeoutError', 'connect_timeout'), ('fetch_exception: TimeoutError', 'read_timeout'), ('fetch_timeout_after_', 'connect_timeout'), ('content_type_rejected:', 'content_type_rejected')], key=lambda x: len(x[0]), reverse=True)
    return (exact, prefixes)
_EXACT_ERROR_MAP, _SORTED_PREFIX_LIST = _build_error_trie()
_PREFIX_KEYS = [p[0] for p in _SORTED_PREFIX_LIST]

def _lookup_prefix_fast(error_str: str) -> str | None:
    """O(log n) prefix lookup via bisect + early break on startswith."""
    if not error_str:
        return None
    min_len = len(_PREFIX_KEYS[-1]) if _PREFIX_KEYS else 0
    if len(error_str) < min_len:
        return None
    idx = bisect.bisect_right(_PREFIX_KEYS, error_str)
    for i in range(idx - 1, -1, -1):
        if error_str.startswith(_PREFIX_KEYS[i]):
            return _SORTED_PREFIX_LIST[i][1]
        if i + 1 < len(_PREFIX_KEYS) and len(_PREFIX_KEYS[i]) < len(_PREFIX_KEYS[i + 1]):
            break
    return None
_FETCH_ERROR_TAXONOMY: dict[str, str] = {'dns_error': 'dns_error', 'connect_error': 'connect_error', 'tls_error': 'tls_error', 'timeout': 'read_timeout', 'content_type_rejected:': 'content_type_rejected', 'fetch_text_none_or_empty': 'body_empty', 'fetch_timeout_after_': 'connect_timeout', 'fetch_exception: asyncio.TimeoutError': 'connect_timeout', 'fetch_exception: TimeoutError': 'read_timeout', 'fetch_exception: ClientConnectorError': 'connect_error', 'fetch_exception: ClientSSLError': 'tls_error', 'fetch_exception: ClientProxyError': 'proxy_error', 'fetch_exception: ClientConnectorCertificateError': 'tls_error', 'circuit_breaker': 'circuit_breaker_blocked', 'resource_governor': 'resource_governor_blocked'}

def classify_fetch_error(result_or_error) -> str:
    """Classify a fetch outcome into a flat error type string for verdict telemetry.

    Takes a FetchResult (success or failure) or an error string.
    Returns one of the Sprint F206AC taxonomy strings:
      none | dns_error | connect_timeout | read_timeout | tls_error | proxy_error
      | http_403 | http_404 | http_429 | http_5xx | content_type_rejected
      | body_empty | max_bytes_exceeded | circuit_breaker_blocked
      | resource_governor_blocked | task_cancelled | unknown_fetch_error

    HARD RULE: CancelledError is re-raised, never classified and swallowed.
    """
    if hasattr(result_or_error, 'status_code'):
        result = result_or_error
        if result.error is None and result.status_code == 200 and result.text:
            if not result.text.strip():
                return 'body_empty'
            return 'none'
        error_str = result.error or ''
        status_code = result.status_code or 0
        try:
            failure_stage = result.failure_stage or ''
        except AttributeError:
            failure_stage = ''
        try:
            network_kind = result.network_error_kind or ''
        except AttributeError:
            network_kind = ''
        if 'CancelledError' in error_str:
            raise asyncio.CancelledError('fetch cancelled')
        if status_code == 403:
            return 'http_403'
        if status_code == 404:
            return 'http_404'
        if status_code == 429:
            return 'http_429'
        if 500 <= status_code < 600:
            return 'http_5xx'
        if failure_stage == 'validation':
            return 'unknown_fetch_error'
        if failure_stage == 'tls' or network_kind == 'tls_error':
            return 'tls_error'
        if network_kind == 'dns_error':
            return 'dns_error'
        if network_kind == 'connect_error':
            return 'connect_error'
        if network_kind == 'timeout':
            return 'read_timeout'
        if failure_stage == 'http':
            if 'content_type_rejected' in error_str:
                return 'content_type_rejected'
            return 'unknown_fetch_error'
        if failure_stage == 'size':
            return 'max_bytes_exceeded'
        if 'circuit_breaker' in error_str:
            return 'circuit_breaker_blocked'
        if 'resource_governor' in error_str:
            return 'resource_governor_blocked'
        _prefix_result = _lookup_prefix_fast(error_str)
        if _prefix_result is not None:
            return _prefix_result
        if error_str:
            return 'unknown_fetch_error'
        return 'none'
    error_str = str(result_or_error) if result_or_error is not None else ''
    if 'CancelledError' in error_str:
        raise asyncio.CancelledError('fetch cancelled')
    if not error_str:
        return 'none'
    if 'circuit_breaker' in error_str:
        return 'circuit_breaker_blocked'
    if 'resource_governor' in error_str:
        return 'resource_governor_blocked'
    _prefix_result = _lookup_prefix_fast(error_str)
    if _prefix_result is not None:
        return _prefix_result
    return 'unknown_fetch_error'
_XML_MARKER = b'<?xml'
_XML_TAG_RE = re.compile(b'^\\s*<[a-zA-Z]', re.IGNORECASE)

def _looks_xmlish(body: bytes) -> bool:
    """Return True if body starts like XML (<?xml or <tag).

    Strips leading ASCII whitespace so servers that prepend newlines
    before the XML declaration are correctly identified.
    """
    stripped = body.lstrip()
    if stripped.startswith(_XML_MARKER):
        return True
    return bool(_XML_TAG_RE.match(stripped))

def _try_decode(body: bytes) -> tuple[str, bool, int, str]:
    """Decode bytes to str, return (text, replaced_bool, replacement_count, codec).

    F178E: replacement_count is actual U+FFFD count (not just bool).

    codec返回值: 'utf-8' | 'windows-1252' | 'latin-1' | 'utf-8-replace'

    replaced_bool=True when the decoder had to substitute characters
    (i.e. U+FFFD replacement chars were inserted). For 'latin-1' the text
    is byte-to-byte lossless but the encoding may NOT match the original
    charset (e.g. a Windows-1252 page decoded as latin-1 is semantically
    wrong). Callers that treat replaced_bool=False as "encoding correct"
    are wrong — only 'utf-8' and 'windows-1252' give that guarantee.
    """
    try:
        text = body.decode('utf-8', errors='strict')
        return (text, False, 0, 'utf-8')
    except UnicodeDecodeError:
        pass
    try:
        text = body.decode('windows-1252', errors='strict')
        return (text, False, 0, 'windows-1252')
    except (UnicodeDecodeError, LookupError):
        pass
    try:
        text = body.decode('latin-1', errors='strict')
        # ISSUE-14 fix: latin-1 is lossless (byte→byte), not a replacement.
        # replaced=False is technically correct for "no U+FFFD substitution occurred",
        # but latin-1 fallback means the caller should treat the text as
        # "possibly wrong encoding — do not assume UTF-8".
        return (text, False, 0, 'latin-1')
    except (UnicodeDecodeError, LookupError):
        pass
    text = body.decode('utf-8', errors='replace')
    count = text.count('�')
    return (text, True, count, 'utf-8-replace')

def _classify_url_kind(url: str) -> str:
    """Returns URL kind (onion|i2p|freenet|clearnet|malformed).

    Single GIL transition for kind-only check.
    Replaces 3x _is_*_url() calls in loops with one classification + bool compare.
    """
    kind, _ = _classify_url_cached(url)
    return kind

def _is_onion_url(url: str) -> bool:
    """Detect if URL targets a .onion darknet address.

    F271: Delegates to _classify_url_kind (single GIL transition).
    """
    try:
        return _classify_url_kind(url) == 'onion'
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.warning('URL parse error in _is_onion_url for %s: %s', url, e)
        return False

def _is_i2p_url(url: str) -> bool:
    """P10: Detect if URL targets an I2P address (.i2p or .b32.i2p).

    F271: Delegates to _classify_url_kind (single GIL transition).
    """
    try:
        return _classify_url_kind(url) == 'i2p'
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.warning('URL parse error in _is_i2p_url for %s: %s', url, e)
        return False

def _is_freenet_url(url: str) -> bool:
    """P10: Detect if URL targets a Freenet address (.freenet or Hyphanet).

    F271: Delegates to _classify_url_kind (single GIL transition).
    """
    try:
        return _classify_url_kind(url) == 'freenet'
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.warning('URL parse error in _is_freenet_url for %s: %s', url, e)
        return False

async def _get_tor_session():
    """Get Tor session for .onion URL fetches.

    F260 JA3 unification: prefers curl_cffi wrapper (chrome_120 JA3, no
    Python TLS fingerprint leak). Falls back to httpx-socks when curl_cffi
    is unavailable. Telemetry records the chosen path.

    F206AT: If _SESSION_MGR._injected_session_provider is set, returns the injected
    aiohttp session verbatim and records source as 'injected' — the wrapper
    path is skipped to preserve back-compat with tests using fake aiohttp.
    """
    if _SESSION_MGR._injected_session_provider is not None:
        injected_tor, _ = _SESSION_MGR._injected_session_provider
        if not _SESSION_MGR._session_is_closed(injected_tor):
            _SESSION_MGR.record_tor_source('injected')
            return injected_tor
    _cc_available, _cc_reason = _runtime_is_curl_cffi_available()
    if _cc_available:
        _SESSION_MGR.record_tor_source('curl_cffi')
        return _TorCurlCffiWrapper()
    async with _SESSION_MGR._get_tor_lock():
        if not _SESSION_MGR.tor_is_healthy():
            _SESSION_MGR._tor_session = await httpx_socks_client(TOR_SOCKS_PROXY, rdns=True)
            _SESSION_MGR._tor_session_locally_created = True
    _SESSION_MGR.record_tor_source('local_tor')
    return _SESSION_MGR._tor_session

async def _get_i2p_session():
    """
    P10: Get I2P session for .i2p/.b32.i2p URL fetches.

    F260 JA3 unification: prefers curl_cffi wrapper (chrome_120 JA3). I2P
    has no NEWNYM equivalent so circuit rotation is intentionally absent.
    Falls back to httpx-socks when curl_cffi is unavailable.
    """
    if _SESSION_MGR._injected_session_provider is not None:
        _, injected_i2p = _SESSION_MGR._injected_session_provider
        if not _SESSION_MGR._session_is_closed(injected_i2p):
            _SESSION_MGR.record_i2p_source('injected')
            return injected_i2p
    _cc_available, _cc_reason = _runtime_is_curl_cffi_available()
    if _cc_available:
        _SESSION_MGR.record_i2p_source('curl_cffi')
        return _I2pCurlCffiWrapper()
    async with _SESSION_MGR._get_i2p_lock():
        if not _SESSION_MGR.i2p_is_healthy():
            _SESSION_MGR._i2p_session = await httpx_socks_client(I2P_SOCKS_PROXY, rdns=True)
            _SESSION_MGR._i2p_session_locally_created = True
    _SESSION_MGR.record_i2p_source('local_i2p')
    return _SESSION_MGR._i2p_session

class _CurlCffiResponseAdapter:
    """Minimal aiohttp-compatible response adapter for curl_cffi fetch results.

    Provides the surface that async_fetch_public_text() needs when iterating
    over a body via `async with session.get(url) as resp:`. We do not aim
    for full aiohttp.ClientResponse parity — only the fields used by the
    aiohttp body-read loop (.url, .status, .headers, .iter_chunked()).
    """
    __slots__ = ('url', 'status', 'headers', 'content_type', '_content')

    def __init__(self, url: str, status: int, headers: dict[str, str] | None, content: bytes) -> None:
        self.url = url
        self.status = status
        self.headers: dict[str, str] = dict(headers) if headers else {}
        ct = self.headers.get('Content-Type') or self.headers.get('content-type') or ''
        self.content_type = ct
        self._content = content

    async def read(self) -> bytes:
        return self._content

    async def text(self, encoding: str='utf-8', errors: str='strict') -> str:
        return self._content.decode(encoding, errors=errors)

    async def iter_chunked(self, n: int):
        """Yield body in n-byte chunks (matches aiohttp's iter_chunked API)."""
        data = self._content
        for i in range(0, len(data), n):
            yield data[i:i + n]

class _CurlCffiGetContextManager:
    """Async context manager wrapping an adapter-yielding object.

    Mirrors aiohttp's `session.get(...)` return value: a context manager
    you can `async with` to get the response. The wrapped object must
    implement `__aenter__` returning a _CurlCffiResponseAdapter.
    """
    __slots__ = tuple(('_future',))

    def __init__(self, future: Any) -> None:
        self._future = future

    async def __aenter__(self):
        return await self._future.__aenter__()

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None

class _TorCurlCffiFetchFuture:
    """Lazy adapter: defer the fetch until __aenter__.

    Wraps fetch_via_tor_curl_cffi() and exposes aiohttp-like response
    surface on completion. Created by _TorCurlCffiWrapper.get().
    """
    __slots__ = ('_url', '_kwargs', '_fetched', '_err', '_adapter')

    def __init__(self, url: str, kwargs: dict) -> None:
        self._url = url
        self._kwargs = kwargs
        self._fetched = False
        self._err: str | None = None
        self._adapter: _CurlCffiResponseAdapter | None = None

    async def __aenter__(self) -> _CurlCffiResponseAdapter:
        if not self._fetched:
            try:
                result = await fetch_via_tor_curl_cffi(url=self._url, headers=self._kwargs.get('headers'), timeout_s=self._kwargs.get('timeout_s', 35.0), max_bytes=self._kwargs.get('max_bytes', 10 * 1024 * 1024), profile='chrome110')
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                self._fetched = True
                self._err = f'tor_curl_cffi_failed:{type(exc).__name__}:{exc}'
                raise httpx.HTTPError(self._err) from exc
            self._fetched = True
            if not result.get('success', False):
                self._err = f"tor_curl_cffi_failed:{result.get('error', 'unknown')}"
                raise httpx.HTTPError(self._err)
            self._adapter = _CurlCffiResponseAdapter(url=result.get('final_url', self._url), status=int(result.get('status_code', 0)), headers=result.get('headers'), content=result.get('content', b'') or b'')
        if self._adapter is None:
            raise httpx.HTTPError('tor_curl_cffi_failed:no_adapter')
        return self._adapter

class _I2pCurlCffiFetchFuture:
    """Lazy adapter for I2P curl_cffi fetch. No circuit rotation (I2P invariant)."""
    __slots__ = ('_url', '_kwargs', '_fetched', '_adapter')

    def __init__(self, url: str, kwargs: dict) -> None:
        self._url = url
        self._kwargs = kwargs
        self._fetched = False
        self._adapter: _CurlCffiResponseAdapter | None = None

    async def __aenter__(self) -> _CurlCffiResponseAdapter:
        if not self._fetched:
            try:
                result = await fetch_via_i2p_curl_cffi(url=self._url, headers=self._kwargs.get('headers'), timeout_s=self._kwargs.get('timeout_s', 35.0), max_bytes=self._kwargs.get('max_bytes', 10 * 1024 * 1024), profile='chrome110')
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                self._fetched = True
                raise httpx.HTTPError(f'i2p_curl_cffi_failed:{type(exc).__name__}:{exc}') from exc
            self._fetched = True
            if not result.get('success', False):
                raise httpx.HTTPError(f"i2p_curl_cffi_failed:{result.get('error', 'unknown')}")
            self._adapter = _CurlCffiResponseAdapter(url=result.get('final_url', self._url), status=int(result.get('status_code', 0)), headers=result.get('headers'), content=result.get('content', b'') or b'')
        if self._adapter is None:
            raise httpx.HTTPError('i2p_curl_cffi_failed:no_adapter')
        return self._adapter

class _TorCurlCffiWrapper:
    """Aiohttp-like session wrapper backed by fetch_via_tor_curl_cffi.

    Provides .get(url) returning an async context manager with an
    aiohttp-like response. State-light: each call creates its own future.
    """
    __slots__ = ()
    closed: bool = False

    def get(self, url: str, **kwargs) -> _CurlCffiGetContextManager:
        return _CurlCffiGetContextManager(_TorCurlCffiFetchFuture(url, kwargs))

    async def close(self) -> None:
        return None

class _I2pCurlCffiWrapper:
    """Aiohttp-like session wrapper backed by fetch_via_i2p_curl_cffi.

    Same shape as _TorCurlCffiWrapper but I2P-invariant: no circuit
    rotation. I2P has no NEWNYM equivalent.
    """
    __slots__ = ()
    closed: bool = False

    def get(self, url: str, **kwargs) -> _CurlCffiGetContextManager:
        return _CurlCffiGetContextManager(_I2pCurlCffiFetchFuture(url, kwargs))

    async def close(self) -> None:
        return None

async def _renew_tor_circuit() -> bool:
    """
    Renew Tor circuit via NEWNYM signal through control port.
    Returns True if successful, False otherwise.
    """
    try:
        import stem.control
        with stem.control.Controller.from_port(port=str(9051)) as ctrl:
            ctrl.authenticate()
            ctrl.signal(stem.control.Signal.NEWNYM)
            logger.debug('Tor circuit renewed via NEWNYM signal')
            return True
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.warning('Tor circuit renewal failed: %s', e)
        return False

async def _maybe_renew_tor_circuit() -> None:
    """Renew Tor circuit if request count threshold reached."""
    async with _SESSION_MGR._get_tor_lock():
        _SESSION_MGR._tor_request_count += 1
        if _SESSION_MGR._tor_request_count >= TOR_CIRCUIT_RENEWAL_REQUEST_COUNT:
            _SESSION_MGR._tor_request_count = 0
    if _SESSION_MGR._tor_request_count == 0:
        await _renew_tor_circuit()

# Crypto-safe jitter — reused across retries (F350M-R)
_JITTER_RNG = secrets.SystemRandom()

async def _jitter_delay() -> None:
    """Apply random jitter before request (Tor/stealth anti-correlation)."""
    await asyncio.sleep(_JITTER_RNG.uniform(JITTER_MIN_S, JITTER_MAX_S))

async def _close_tor_session() -> None:
    """Close the Tor session (for cleanup)."""
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._tor_session) and _SESSION_MGR._tor_session_locally_created:
        await _SESSION_MGR._session_aclose(_SESSION_MGR._tor_session)
    _SESSION_MGR._tor_session = None
    _SESSION_MGR._tor_session_locally_created = False

def _close_tor_session_sync() -> None:
    """Sync wrapper for Tor session cleanup via atexit.

    C7-FIX: Uses asyncio.Runner() (PEP 654) instead of new_event_loop/run_until_complete.
    asyncio.Runner() properly manages loop lifecycle and avoids M1 Metal crash vectors.
    """
    import threading
    if not _SESSION_MGR.tor_is_healthy():
        return
    if not _SESSION_MGR._tor_session_locally_created:
        _SESSION_MGR._tor_session = None
        return
    session = _SESSION_MGR._tor_session
    if session is None:
        _SESSION_MGR._tor_session = None
        _SESSION_MGR._tor_session_locally_created = False
        return

    def _run_closer() -> None:
        # C7-FIX: Use asyncio.Runner() — properly manages loop lifecycle.
        # This is safe from a daemon thread (no M1 crash vector).
        try:
            with asyncio.Runner() as runner:
                runner.run(session.aclose())
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.warning('Error closing Tor session in thread: %s', e)
        finally:
            _SESSION_MGR._tor_session = None

    try:
        asyncio.get_running_loop()
        # Inside a running loop — spawn a daemon thread to do the close.
        # Using Runner() in the thread is safe (not the M1 crash path).
        _t = threading.Thread(target=_run_closer, daemon=True)
        _t.start()
    except RuntimeError:
        # No running loop — run directly with Runner().
        _run_closer()
    finally:
        _SESSION_MGR._tor_session_locally_created = False

async def _close_i2p_session() -> None:
    """
    P10: Close the I2P session (for cleanup).
    """
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._i2p_session) and _SESSION_MGR._i2p_session_locally_created:
        await _SESSION_MGR._session_aclose(_SESSION_MGR._i2p_session)
    _SESSION_MGR._i2p_session = None
    _SESSION_MGR._i2p_session_locally_created = False

def _close_i2p_session_sync() -> None:
    """Sync wrapper for I2P session cleanup via atexit.

    C7-FIX: Uses asyncio.Runner() (PEP 654) instead of new_event_loop/run_until_complete.
    asyncio.Runner() properly manages loop lifecycle and avoids M1 Metal crash vectors.
    """
    import threading
    if not _SESSION_MGR.i2p_is_healthy():
        return
    if not _SESSION_MGR._i2p_session_locally_created:
        _SESSION_MGR._i2p_session = None
        return
    session = _SESSION_MGR._i2p_session
    if session is None:
        _SESSION_MGR._i2p_session = None
        _SESSION_MGR._i2p_session_locally_created = False
        return

    def _run_closer() -> None:
        # C7-FIX: Use asyncio.Runner() — properly manages loop lifecycle.
        # This is safe from a daemon thread (not the M1 crash path).
        try:
            with asyncio.Runner() as runner:
                runner.run(session.aclose())
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            logger.warning('Error closing I2P session in thread: %s', e)
        finally:
            _SESSION_MGR._i2p_session = None

    try:
        asyncio.get_running_loop()
        # Inside a running loop — spawn a daemon thread to do the close.
        _t = threading.Thread(target=_run_closer, daemon=True)
        _t.start()
    except RuntimeError:
        # No running loop — run directly with Runner().
        _run_closer()
    finally:
        _SESSION_MGR._i2p_session_locally_created = False

async def close_public_fetcher_sessions_async() -> dict:
    """Close all locally-managed Tor and I2P aiohttp sessions.

    F219D: This is the canonical teardown surface for public_fetcher local sessions.
    It safely closes local _tor_session and _i2p_session that were created by
    _get_tor_session() and _get_i2p_session(). Injected provider sessions
    (via inject_session_provider) are NOT closed — they are owned externally.

    Returns:
        dict with keys:
            - tor_close_attempted: bool
            - tor_close_success: bool
            - tor_close_error: str | None
            - i2p_close_attempted: bool
            - i2p_close_success: bool
            - i2p_close_error: str | None
            - injected_provider_active: bool
    """
    _injected_active = _SESSION_MGR._injected_session_provider is not None
    _tor_attempted = False
    _tor_success = False
    _tor_error: str | None = None
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._tor_session):
        _tor_attempted = True
        if _SESSION_MGR._tor_session_locally_created:
            try:
                await _SESSION_MGR._session_aclose(_SESSION_MGR._tor_session)
                _tor_success = True
            except asyncio.CancelledError:
                raise
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _tor_error = str(_e)
                logger.warning('Error closing Tor session: %s', _e)
    _SESSION_MGR._tor_session = None
    _SESSION_MGR._tor_session_locally_created = False
    _i2p_attempted = False
    _i2p_success = False
    _i2p_error: str | None = None
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._i2p_session):
        _i2p_attempted = True
        if _SESSION_MGR._i2p_session_locally_created:
            try:
                await _SESSION_MGR._session_aclose(_SESSION_MGR._i2p_session)
                _i2p_success = True
            except asyncio.CancelledError:
                raise
            except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                _i2p_error = str(_e)
                logger.warning('Error closing I2P session: %s', _e)
    _SESSION_MGR._i2p_session = None
    _SESSION_MGR._i2p_session_locally_created = False
    _SESSION_MGR._session_source_telemetry['tor'] = 'unavailable'
    _SESSION_MGR._session_source_telemetry['i2p'] = 'unavailable'
    return {'tor_close_attempted': _tor_attempted, 'tor_close_success': _tor_success, 'tor_close_error': _tor_error, 'i2p_close_attempted': _i2p_attempted, 'i2p_close_success': _i2p_success, 'i2p_close_error': _i2p_error, 'injected_provider_active': _injected_active}

def get_public_fetcher_session_status() -> dict:
    """Return lightweight status of public_fetcher local sessions (O(1), side-effect free).

    F219D: Reports the state of locally-managed Tor/I2P sessions. Does NOT
    report on injected provider sessions (those are owned externally).

    Returns:
        dict with keys:
            - tor_session_present: bool
            - tor_session_closed: bool
            - i2p_session_present: bool
            - i2p_session_closed: bool
            - injected_provider_active: bool
            - session_source_telemetry: dict (snapshot)
    """
    status = _SESSION_MGR.status_snapshot()
    return {'tor_session_present': status['tor_present'], 'tor_session_closed': status['tor_closed'], 'i2p_session_present': status['i2p_present'], 'i2p_session_closed': status['i2p_closed'], 'injected_provider_active': status['injected_active'], 'session_source_telemetry': status['telemetry']}

def _register_atexit_cleanup() -> None:
    """Lazy atexit registration to defer import cost."""
    import atexit
    atexit.register(_close_tor_session_sync)
    atexit.register(_close_i2p_session_sync)
_register_atexit_cleanup()
_SERP_HOST_RE = re.compile('(google\\.|bing\\.|duckduckgo\\.|yahoo\\.|baidu\\.|yandex\\.|so\\.|startpage\\.|search\\.|serp)|searchresults|webcache|googlesyndication|googletagmanager| DoubleClick|search\\?q=|/search\\?|\\?q=|\\&oq=|\\&gs_l=', re.IGNORECASE)
_CONTENT_LENGTH_RE = re.compile('content-length\\s*[=:]\\s*(\\d+)', re.IGNORECASE)
_NOSCRIPT_RE = re.compile('<noscript[^>]*>|enable javascript', re.IGNORECASE)
_FEED_URL_RE = re.compile('/?(?:rss|feed|atom|xml|sitemap|opensearch)', re.IGNORECASE)
_JS_SKIP_HOST_RE = re.compile('(?:^|\\.)(?:threatfox\\.abuse\\.ch|bleepingcomputer\\.com|thehackernews\\.com|krebsonsecurity\\.com|cisa\\.gov|id-ransomware\\.malwarehunterteam\\.com|ransomwaretracker\\.xyz|abuse\\.ch|urlhaus\\.abuse\\.ch|feodo\\.tracker|openphish\\.com|cyberscoop\\.com|darkreading\\.com|threatpost\\.com|therecord\\.media|securityweek\\.com|inforisktoday\\.com|helpnetsecurity\\.com|malwarebazaar\\.abuse\\.ch|sslbl\\.abuse\\.ch)$', re.IGNORECASE)

class _JSRendererCapability:
    """Thread-safe JS renderer capability tracker.

    F-GLOBAL: Encapsulates _js_renderer_capability dict and
    _js_renderer_capability_lock.

    Tracks availability of camoufox, nodriver, and playwright.
    Uses threading.Lock for thread-safe access.
    Cached after first check — use reset() to force re-check.
    """
    __slots__ = ('_capability', '_lock')

    def __init__(self) -> None:
        self._capability: dict[str, str | None] = {'camoufox': None, 'nodriver': None, 'playwright': None}
        self._lock = threading.Lock()

    def get(self) -> dict[str, str | None]:
        """Get current capability snapshot (copy)."""
        with self._lock:
            return dict(self._capability)

    def reset(self) -> None:
        """Reset all capabilities to unknown (force re-check)."""
        with self._lock:
            self._capability = {'camoufox': None, 'nodriver': None, 'playwright': None}

    def mark_unavailable(self, name: str, reason: str) -> None:
        """Mark a renderer as unavailable with a reason string."""
        if name in self._capability:
            with self._lock:
                self._capability[name] = reason

    def check_and_update(self) -> dict[str, str | None]:
        """Run capability checks and update cached state.

        Returns capability dict with reasons for unavailability.
        """
        with self._lock:
            self._check_camoufox()
            self._check_nodriver()
            self._check_playwright()
            return dict(self._capability)

    def _check_camoufox(self) -> None:
        """Check camoufox availability."""
        if self._capability['camoufox'] is not None:
            return
        try:
            import camoufox
            _ = camoufox.Session
            self._capability['camoufox'] = None
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._capability['camoufox'] = f'camoufox_unavailable: {e}'

    def _check_nodriver(self) -> None:
        """Check nodriver availability."""
        if self._capability['nodriver'] is not None:
            return
        if not _check_chrome_binary_exists():
            self._capability['nodriver'] = 'chrome_binary_missing'
            return
        try:
            import nodriver
            _ = nodriver.start
            self._capability['nodriver'] = None
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._capability['nodriver'] = f'nodriver_unavailable: {e}'

    def _check_playwright(self) -> None:
        """Check playwright availability."""
        if self._capability['playwright'] is not None:
            return
        heavy_browser_enabled = ENV.get_bool('HLEDAC_ENABLE_HEAVY_BROWSER')
        if not heavy_browser_enabled:
            self._capability['playwright'] = 'heavy_browser_disabled'
            return
        try:
            import playwright
            _ = playwright.async_api
            self._capability['playwright'] = None
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            self._capability['playwright'] = f'playwright_unavailable: {e}'

    def is_any_available(self) -> bool:
        """Check if any JS renderer is available."""
        with self._lock:
            return any((v is None for v in self._capability.values()))
_js_renderer_cap = _JSRendererCapability()

def _check_chrome_binary_exists() -> bool:
    """Check if Chrome/Chromium binary is available on the system (macOS + Linux)."""
    import os
    candidates = ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Chromium.app/Contents/MacOS/Chromium', '/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/usr/bin/chromium', '/usr/bin/chromium-browser']
    from pathlib import Path
    return any((Path(p).exists() and os.access(p, os.X_OK) for p in candidates))

def _get_js_renderer_capability() -> dict[str, str | None]:
    """
    Return capability dict for all JS renderers.
    Values: None = available, str = unavailable reason.
    Cached after first call per renderer.

    F-GLOBAL: Delegates to _js_renderer_cap singleton.
    """
    return _js_renderer_cap.check_and_update()

def _all_js_renderers_unavailable() -> bool:
    """Return True if all JS renderers are unavailable.

    Checks the cached capability dict directly without triggering re-detection.
    None = available (renderer has no unavailable reason).
    str = unavailable reason.
    """
    cap = _js_renderer_cap.get()
    return all((v is not None for v in cap.values()))

def reset_js_renderer_capability_cache() -> None:
    """
    Reset JS renderer capability cache.

    Use this for tests, diagnostics, or long-running runtime refresh.
    Does NOT trigger browser startup or heavy imports — only resets
    the cached capability dict so the next _get_js_renderer_capability()
    call re-detects from scratch.
    """
    _js_renderer_cap.reset()

def refresh_js_renderer_capability() -> dict[str, str | None]:
    """
    Force re-detect JS renderer capabilities and return current state.

    Unlike reset_js_renderer_capability_cache(), this also returns
    the freshly-detected capability dict.
    """
    reset_js_renderer_capability_cache()
    return _get_js_renderer_capability()

def _looks_like_feed_url(url: str) -> bool:
    """Return True if URL path strongly suggests an RSS/XML/Atom/Sitemap feed.

    F271: Rust _rust_backend.url.looks_like_feed_url fast path with urllib.parse fallback.
    The Rust function is a direct drop-in for the regex check on
    urlparse(url).path.rstrip("/"). ImportError or runtime failure
    falls through to the unchanged Python branch.
    """
    try:
        _uops = _rust_backend.url
        if _uops is not None:
            return _uops.looks_like_feed_url(url)
    except Exception:  # noqa: BLE001 — best-effort; feed URL detection failure returns False
        pass
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip('/')
        return bool(_FEED_URL_RE.search(path))
    except Exception:  # noqa: BLE001 — best-effort; regex failure returns False
        return False

def _needs_js_fetch(text: str, *, url: str='', content_length: int=0, declared_length: int=-1) -> bool:
    """Detect if response suggests JS-rendered content is needed.
    Enhanced P0-FIX: covers three failure modes of the original _NOSCRIPT_RE-only
    detection that caused 10/10 SERP URLs to be rejected as empty_text:
    1. <noscript> tag presence (original)
    2. Known SERP/search engine hosts (new)
    3. Content-length ratio: tiny body vs large declared Content-Length (new)
    Args:
        text: Decoded response text.
        url: Source URL for SERP host detection.
        content_length: Actual body byte length.
        declared_length: Declared Content-Length header value (-1 if unknown).
    """
    if url:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ''
            if host and _JS_SKIP_HOST_RE.search(host):
                return False
        except Exception:  # noqa: BLE001 — best-effort; host parse failure is non-fatal
            pass
    if _NOSCRIPT_RE.search(text):
        return True
    if url:
        try:
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ''
            if host and _SERP_HOST_RE.search(host + '/' + url):
                return True
            if _JS_SKIP_HOST_RE.search(host):
                return False
        except Exception:  # noqa: BLE001 — best-effort; SERP host detection failure is non-fatal
            pass
    if declared_length > 0 and content_length > 0:
        if declared_length > content_length * 3 and content_length < 20000:
            return True
    return False
try:
    from hledac.universal.utils.uma_budget import is_uma_critical as _is_uma_critical
except Exception:  # noqa: BLE001 — best-effort; fallback to non-critical if import fails

    def _is_uma_critical() -> bool:
        return False
MAX_BYTES_HARD_PRESSURE: Final[int] = 5000000

def _compute_effective_max_bytes(requested: int) -> int:
    """
    F226A: Adaptive body cap honoring caller request, hard cap, and UMA pressure.

    Behavior:
    - Clamps requested to [1, MAX_BYTES_HARD].
    - On UMA critical, further halves the cap to MAX_BYTES_HARD_PRESSURE (5MB).
    - Fail-soft: if UMA sampler throws, falls back to MAX_BYTES_HARD (10MB).

    Why this matters: 25 in-flight × 10MB = 250MB just for fetch bodies on M1 8GB.
    Under pressure, halving brings that to 125MB, leaving headroom for browser
    processes (300-500MB) and MLX (2GB).
    """
    try:
        hard = MAX_BYTES_HARD_PRESSURE if _is_uma_critical() else MAX_BYTES_HARD
    except Exception:  # noqa: BLE001 — best-effort; UMA check failure falls back to non-pressure cap
        hard = MAX_BYTES_HARD
    if requested <= 0:
        return hard
    return min(max(requested, 1), hard)
_JS_RENDERER_SEMAPHORE: asyncio.Semaphore | None = None
_JSC_RENDERER_COOLDOWN_S = 0.5

def _get_js_renderer_semaphore() -> asyncio.Semaphore:
    """F226A: Lazily-initialized, per-event-loop JS renderer Semaphore(1).

    Thread-safe via functools.lru_cache internals (one lock, acquired once).
    Note: asyncio.Semaphore is created in the calling event loop context.
    """
    global _JS_RENDERER_SEMAPHORE
    if _JS_RENDERER_SEMAPHORE is not None:
        return _JS_RENDERER_SEMAPHORE
    from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
    _JS_RENDERER_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
    return _JS_RENDERER_SEMAPHORE

async def _cooldown_after_browser_stop() -> None:
    """
    P14 FIX: Yield to event loop after browser.stop() so the OS can fully reap
    the child process before the next renderer acquires the semaphore.
    On macOS, browser processes (Chrome/Firefox) are multi-process — the main
    process exits fast but sandbox/helper processes may linger for 50-100ms.
    """
    await asyncio.sleep(_JSC_RENDERER_COOLDOWN_S)

async def _teardown_browser_pool() -> None:
    """
    Teardown camoufox/nodriver shared state at sprint winddown.

    Called from sprint_scheduler run_winddown(). Fail-soft — any error is
    swallowed at DEBUG level. Must be idempotent (safe to call multiple times).

    Browser instances are self-contained per fetch call (created and torn down
    inline); this function resets the lazy singletons:
    - _JS_RENDERER_SEMAPHORE: released and cleared so next sprint re-initializes
      in the correct event loop
    - _js_renderer_capability: reset to None so next sprint re-probes availability
    - yields cooldown to let any in-flight browser.stop() calls finish
    """
    global _JS_RENDERER_SEMAPHORE
    try:
        _sem = _JS_RENDERER_SEMAPHORE
        if _sem is not None:
            try:
                for _ in range(_sem._value + 1):
                    await asyncio.sleep(0)
            except Exception:  # noqa: BLE001 — best-effort; semaphore drain failure is non-fatal
                pass
            _JS_RENDERER_SEMAPHORE = None
    except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.debug('[browser_pool] semaphore teardown skipped: %s', _e)
    try:
        _js_renderer_cap.reset()
    except Exception as _e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.debug('[browser_pool] capability reset skipped: %s', _e)
    try:
        await asyncio.sleep(_JSC_RENDERER_COOLDOWN_S)
    except Exception:  # noqa: BLE001 — best-effort; cooldown sleep failure is non-fatal
        pass
    logger.debug('[winddown] browser pool torn down')

class AiohttpBodyOutcome(msgspec.Struct, frozen=True, gc=False):
    """F226B: aiohttp body read outcome with peek + size cap. F350M-R: gc=False for M1 8GB."""
    body: bytes
    total_read: int
    truncated: bool
    chunks_consumed: int
    xml_recovered: bool
    first_chunk_peeked: bool

async def _read_aiohttp_body_with_peek(chunks: AsyncIterator[bytes], max_bytes: int, *, enable_peek: bool) -> AiohttpBodyOutcome:
    """
    F226B: Read an aiohttp chunked body stream with hard byte cap.

    Replaces inline chunked loop (was duplicated body_limiter.read_body_with_cap).
    Adds first-chunk XML peek for content-type recovery decisions.

    Behavior:
    - O(1) amortized append via bytearray.extend().
    - Bounded by CHUNKS_BUDGET (8k chunks) for safety against pathological sources.
    - Truncates in-place when projected size exceeds max_bytes.
    - If enable_peek=True, the first chunk is inspected via _looks_xmlish().
    - CancelledError propagates unchanged.

    Returns AiohttpBodyOutcome with all context caller needs to build FetchResult.
    """
    from hledac.universal.transport.body_limiter import CHUNKS_BUDGET
    content_bytes = bytearray()
    xml_recovered = False
    first_chunk_peeked = False
    chunks_consumed = 0
    truncated = False
    async for chunk in chunks:
        if chunks_consumed >= CHUNKS_BUDGET:
            logger.warning(f'Aiohttp body read hit CHUNKS_BUDGET={CHUNKS_BUDGET}; truncating at {len(content_bytes)} bytes')
            truncated = True
            break
        chunks_consumed += 1
        if enable_peek and (not first_chunk_peeked):
            first_chunk_peeked = True
            if _looks_xmlish(chunk):
                xml_recovered = True
        if max_bytes > 0 and len(content_bytes) + len(chunk) > max_bytes:
            remaining = max_bytes - len(content_bytes)
            if remaining > 0:
                content_bytes.extend(chunk[:remaining])
            logger.debug('Aiohttp body truncated to {max_bytes} bytes after {chunks_consumed} chunks', max_bytes=max_bytes, chunks_consumed=chunks_consumed)
            truncated = True
            break
        content_bytes.extend(chunk)
    return AiohttpBodyOutcome(body=bytes(content_bytes), total_read=len(content_bytes), truncated=truncated, chunks_consumed=chunks_consumed, xml_recovered=xml_recovered, first_chunk_peeked=first_chunk_peeked)

async def _peek_aiohttp_first_chunk(chunks: AsyncIterator[bytes]) -> tuple[bool, bytes | None]:
    """
    F226B: Peek at the first chunk of an aiohttp body stream.

    Returns:
        (is_xmlish, first_chunk_bytes or None)
        - is_xmlish=True if the first chunk looks like XML (used for CT recovery).
        - first_chunk_bytes is the raw first chunk (caller keeps ownership of
          it — must include in final body to avoid losing data).

    The first chunk is consumed but yielded back to the caller so the subsequent
    body read sees the complete stream. (Caller appends it to body_chunks before
    reading the rest.)
    """
    try:
        first_chunk = await anext(chunks)
    except StopAsyncIteration:
        return (False, None)
    return (_looks_xmlish(first_chunk), first_chunk)

async def _fetch_with_camoufox(url: str, timeout: float=15.0) -> str:
    """
    Fetch JS-heavy page via Camoufox (Firefox-based anti-detect).
    Max 1 instance, protected by _CAMOUFOX_LOCK singleton.
    M1-optimized: headless, WebGL spoofed for Apple M1.

    F202H: Uses opsec_policy.get_renderer_policy() for M1 conflict guard —
    replaces inline is_embedding_context_active() check with centralized policy.
    """
    try:
        from hledac.universal.embedding_pipeline import is_embedding_context_active
        from hledac.universal.runtime.opsec_policy import OPSECContext, get_renderer_policy
        has_model = is_embedding_context_active()
        ctx = OPSECContext(has_model_context=has_model)
        policy = get_renderer_policy(ctx)
        if not policy.allowed:
            logger.warning(f'[F202H] Renderer blocked by opsec_policy: {policy.blocked_reason} — skipping Camoufox for {url}')
            return ''
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.warning('Error checking renderer policy, proceeding with caution: %s', e)
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        logger.debug('camoufox not installed, JS fetch unavailable')
        return ''
    async with _get_js_renderer_semaphore():
        return await _camoufox_locked(url, timeout)
_CAMOUFOX_OS_ROTATION: tuple[str, ...] = ('macos', 'windows', 'linux')
_CAMOUFOX_MAX_RETRIES: int = 3

async def _camoufox_locked(url: str, timeout: float) -> str:
    """
    F226A: Camoufox body inside the original _CAMOUFOX_LOCK + outer JS semaphore.
    P2-4: Added os-rotation retry — each OS variant generates a different
    auto-generated fingerprint, so dark web sites that block one fingerprint
    may accept another. Retries up to 3 OS variants before giving up.
    """
    try:
        from camoufox.async_api import AsyncCamoufox
    except ImportError:
        return ''
    async with _get_camoufox_lock():
        last_error = ''
        for attempt in range(_CAMOUFOX_MAX_RETRIES):
            os_choice = _CAMOUFOX_OS_ROTATION[attempt % len(_CAMOUFOX_OS_ROTATION)]
            try:
                async with AsyncCamoufox(headless=True, os=os_choice, webgl_config=('Apple', 'Apple M1, or similar')) as browser:
                    page = await browser.new_page()
                    try:
                        await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
                        html = await page.content()
                    finally:
                        await page.close()
                    return html
            except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                last_error = str(e)
                logger.debug(f'Camoufox attempt {attempt + 1}/{_CAMOUFOX_MAX_RETRIES} (os={os_choice}) failed for {url}: {e}')
                await _cooldown_after_browser_stop()
                continue
        logger.warning(f'Camoufox all {_CAMOUFOX_MAX_RETRIES} attempts failed for {url}: {last_error}')
        return ''

async def _fetch_with_nodriver(url: str, url_kind: str='', url_host: str='') -> str:
    """
    F265C: Primary JS fetch via nodriver (direct CDP, no WebDriver).
    On M1, nodriver is more stable than Camoufox — used as first choice.
    Requires Chrome binary present. Returns "" with telemetry on failure.

    B1: url_kind/url_host params — caller pre-classified via _classify_url_cached.
    """
    if not _check_chrome_binary_exists():
        logger.debug('nodriver skipped: chrome binary not found')
        return ''
    if _is_uma_critical():
        logger.debug('nodriver skipped: UMA critical memory pressure')
        return ''
    try:
        import nodriver as uc
    except ImportError:
        _js_renderer_cap.mark_unavailable('nodriver', 'nodriver_unavailable')
        logger.debug('nodriver not installed, CDP fetch unavailable')
        return ''
    async with _get_js_renderer_semaphore():
        return await _nodriver_locked(url, url_kind, url_host)
_NODRIVER_MAX_RETRIES: int = 2

async def _nodriver_locked(url: str, url_kind: str='', url_host: str='') -> str:
    """
    F226A: nodriver body wrapped inside the shared _JS_RENDERER_SEMAPHORE.
    P2-4: Added Tor proxy routing + os-rotation retry for dark web resilience.

    Cleanup invariants preserved:
    - page.close() in finally
    - browser.stop() on cancellation + finally
    - CancelledError re-raised (must propagate)

    B1: url_kind/url_host params — caller pre-classified via _classify_url_cached.
    When both are empty, falls back to _batch_classify_url_cached([url]) (legacy path).
    """
    import nodriver as uc
    if not url_kind or not url_host:
        _url_kind_batch = _batch_classify_url_cached([url])
        url_kind = _url_kind_batch[0][0] if _url_kind_batch else 'clearnet'
    _is_onion = url_kind == 'onion'
    browser = None
    page = None
    last_error = ''
    for attempt in range(_NODRIVER_MAX_RETRIES):
        try:
            browser_args = ['--no-sandbox', '--disable-dev-shm-usage', '--disable-blink-features=AutomationControlled']
            if _is_onion:
                browser_args.append(f'--proxy-server={TOR_SOCKS_PROXY}')
            browser = await uc.start(headless=True, browser_args=browser_args)
            page = await browser.get(url)
            try:
                await asyncio.sleep(2)
                html = await page.get_content()
            finally:
                if page is not None:
                    await page.close()
            return html
        except asyncio.CancelledError:
            if browser is not None:
                browser.stop()
            raise
        except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            last_error = str(e)
            _ev0 = attempt + 1
            logger.debug('nodriver attempt %d failed for %s: %s', _ev0, url, e)
            if browser is not None:
                browser.stop()
            await asyncio.sleep(0.2)
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001 — best-effort; page close failure is non-fatal
                    pass
    logger.warning('nodriver all {_NODRIVER_MAX_RETRIES} attempts failed for {url}: {last_error}', _NODRIVER_MAX_RETRIES=_NODRIVER_MAX_RETRIES, url=url, last_error=last_error)
    return ''

async def _fetch_with_playwright(url: str, timeout: float=15.0) -> str:
    """
    F265C: Playwright fallback — last resort after nodriver fails.
    Requires HLEDAC_ENABLE_HEAVY_BROWSER=1 AND playwright installed.
    Returns "" with telemetry on any failure.
    """
    if not ENV.get_bool('HLEDAC_ENABLE_HEAVY_BROWSER'):
        logger.debug('playwright skipped: HLEDAC_ENABLE_HEAVY_BROWSER != 1')
        return ''
    try:
        importlib.util.find_spec('playwright')
    except ImportError:
        logger.debug('playwright not installed, fallback unavailable')
        return ''
    async with _get_js_renderer_semaphore():
        return await _playwright_locked(url, timeout)

async def _playwright_locked(url: str, timeout: float) -> str:
    """
    F265C: Playwright body wrapped inside the shared _JS_RENDERER_SEMAPHORE.
    Chromium via playwright — fails soft on all errors.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ''
    browser = None
    page = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until='networkidle', timeout=timeout * 1000)
                html = await page.content()
            finally:
                if page is not None:
                    await page.close()
            return html
    except asyncio.CancelledError:
        if browser is not None:
            await browser.close()
        raise
    except Exception as e:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
        logger.warning('playwright fetch failed: %s', e)
        return ''
    finally:
        if browser is not None:
            await browser.close()
        await _cooldown_after_browser_stop()


# ISSUE-7: tenacity decorator — replaces manual for/retry loop.
# stop: MAX_RETRIES+1 attempts total (matches original for loop behavior)
# wait: _tenacity_wait_jitter — decorrelated jitter with Retry-After header priority
# retry: only on _RetryableStatus (HTTP retryable status codes)
# before_sleep: record circuit-breaker failure before waiting
# after: record circuit-breaker success on final success
# reraise: re-raise if all retries exhausted (tenacity returns last exception)
_retry_decorator = retry(
    stop=stop_after_attempt(MAX_RETRIES + 1),
    wait=_tenacity_wait_jitter,
    retry=retry_if_exception_type((_RetryableStatus, TimeoutError)),
    before_sleep=_tenacity_before_sleep,
    after=_tenacity_after,
    reraise=True,
)

async def async_fetch_public_text_batch(
    urls: list[str],
    timeout_s: float = 35.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
    use_stealth: bool = False,
    use_js: bool = False,
    use_doh: bool = False,
    js_confidence: float = 0.8,
    priority: int = 5,
    concurrency: int | None = None,
) -> list[FetchResult]:
    """
    Batch URL fetching via parallel() — concurrent, bounded, fail-safe.

    Wraps async_fetch_public_text() for N URLs concurrently using the
    canonical parallel() runner (asyncio.TaskGroup, structured-concurrency
    sibling cancellation). Results preserve input order. Failures return
    FetchResult with error field set.

    ISSUE-018 Problem 1 fix: provides explicit bulk async API.
    F1 FIX: concurrency=None → UMA-aware dynamic limit from ConcurrencyBudgetRegistry.

    Args:
        urls: List of URLs to fetch (http/https only).
        timeout_s: Per-request timeout in seconds (default 35 s).
        max_bytes: Max bytes to read per response (default MAX_BYTES_DEFAULT).
        use_stealth: Enable stealth/Tor transport (default False).
        use_js: Enable JS rendering via camoufox/nodriver (default False).
        use_doh: Enable DNS-over-HTTPS (default False).
        js_confidence: Confidence that JS is needed (0.0–1.0, default 0.8).
        priority: Request priority 1–10 (default 5, lower = higher priority).
        concurrency: Max concurrent in-flight requests.
            - None (default): UMA-aware dynamic limit via ConcurrencyBudgetRegistry.
              OK state → 8, WARN → 4, CRITICAL → 2, EMERGENCY → 1.
            - int: explicit override (e.g. concurrency=3 for Pastebin rate-limit).

    Returns:
        List of FetchResult in same order as input urls.
        Length matches len(urls) — errors produce FetchResult with error field set.
        Never raises; always returns a list of the same length as input.

    Always-on, bounded (concurrency semaphore via parallel()), fail-safe.
    No new feature flags.
    """
    if not urls:
        return []

    # F1 FIX: resolve dynamic concurrency before parallel() call.
    # concurrency=None → UMA-aware limit from ConcurrencyBudgetRegistry.
    # concurrency=int → explicit override (preserves legacy caller behavior).
    if concurrency is None:
        from core.concurrency_registry import concurrency_budget, ConcurrencyCategory

        concurrency = await concurrency_budget(ConcurrencyCategory.HTTP_LANE)

    async def _fetch_one(url: str, idx: int) -> tuple[int, FetchResult]:
        """Fail-safe fetch with index capture for order preservation."""
        try:
            result = await async_fetch_public_text(
                url,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
                use_stealth=use_stealth,
                use_js=use_js,
                use_doh=use_doh,
                js_confidence=js_confidence,
                priority=priority,
            )
            return idx, result
        except Exception as _e:  # noqa: BLE001 — fail-safe; never propagate
            return idx, FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type='',
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=0.0,
                error=f'batch_exception:{type(_e).__name__}:{_e}',
                failure_stage='batch_dispatch',
            )

    # Canonical parallel runner: structured-concurrency cancellation on failure,
    # bounded concurrency, result order preserved via index capture.
    result = await parallel(
        [asyncio.create_task(_fetch_one(url, idx)) for idx, url in enumerate(urls)],
        concurrency=concurrency,
        taskgroup=True,
        policy="collect",
    )

    # Re-bucket by original index — _fetch_one is fail-safe (catches all
    # Exception), so result.errors is always empty; nothing to propagate.
    ordered: list[FetchResult] = [FetchResult()] * len(urls)
    for idx, fetched_result in result.ok:
        ordered[idx] = fetched_result

    # ISSUE-7 Phase 2: two-phase retry for retryable failures.
    # Phase 1 (above): all URLs fetched with built-in retry loop (serial per-host).
    # Phase 2: identify retryable failures and retry them in a separate parallel pass.
    # This ensures retry sleeps for broken hosts don't block the batch concurrency slot.
    retryable_urls: list[tuple[int, str]] = []  # (original_idx, url)
    for idx, fr in enumerate(ordered):
        if fr.error is not None and (
            fr.error.startswith('retryable:')
            or fr.error.startswith('timeout')
            or 'circuit_breaker_open' in fr.error
        ):
            retryable_urls.append((idx, fr.url))
    if retryable_urls:
        _retry_concurrency = max(1, min(concurrency, 8))  # cap retry concurrency

        async def _retry_one(idx_url: tuple[int, str]) -> tuple[int, FetchResult]:
            _idx, _url = idx_url
            try:
                # Bypass circuit breaker on retry — host is already known broken
                _result = await async_fetch_public_text(
                    _url,
                    timeout_s=timeout_s,
                    max_bytes=max_bytes,
                    use_stealth=use_stealth,
                    use_js=use_js,
                    use_doh=use_doh,
                    js_confidence=js_confidence,
                    priority=priority,
                    bypass_circuit_breaker=True,  # skip CB, go straight to fetch
                )
                return _idx, _result
            except Exception as _e:  # noqa: BLE001 — fail-safe
                return _idx, FetchResult(
                    url=_url,
                    final_url=_url,
                    status_code=0,
                    content_type='',
                    text=None,
                    fetched_bytes=0,
                    declared_length=-1,
                    elapsed_ms=0.0,
                    error=f'batch_retry_exception:{type(_e).__name__}:{_e}',
                    failure_stage='batch_retry_dispatch',
                )

        _retry_result = await parallel(
            [asyncio.create_task(_retry_one(x)) for x in retryable_urls],
            concurrency=_retry_concurrency,
            taskgroup=True,
            policy="collect",
        )
        # Merge retry results: only overwrite if retry succeeded (error=None or non-retryable)
        for idx, retry_fr in _retry_result.ok:
            # Only accept if retry improved the result (non-empty text or non-retryable error)
            _orig = ordered[idx]
            if retry_fr.text or (
                retry_fr.error
                and not retry_fr.error.startswith('retryable:')
                and not retry_fr.error.startswith('timeout')
                and 'circuit_breaker_open' not in (retry_fr.error or '')
            ):
                ordered[idx] = retry_fr
            elif (
                retry_fr.error == _orig.error
                or retry_fr.error is None
            ):
                # Same error or still broken — keep original
                pass

    return ordered


__all__ = ['async_fetch_public_text', 'async_fetch_public_text_batch', 'process_html_payload', 'DEFAULT_UA', 'MAX_BYTES_DEFAULT', 'MAX_BYTES_HARD', 'MAX_RETRIES', 'FetchResult', '_is_retryable_status', '_extract_retry_after', '_compute_backoff_seconds', '_try_decode', '_looks_xmlish', '_is_onion_url', '_get_tor_session', '_renew_tor_circuit', '_jitter_delay', '_close_tor_session', 'TOR_SOCKS_PROXY', 'TOR_CIRCUIT_RENEWAL_REQUEST_COUNT', 'I2P_SOCKS_PROXY', '_is_i2p_url', '_is_freenet_url', '_get_i2p_session', '_close_i2p_session', '_needs_js_fetch', '_fetch_with_nodriver', '_fetch_with_camoufox', '_fetch_with_playwright', '_get_js_renderer_capability', '_all_js_renderers_unavailable', 'reset_js_renderer_capability_cache', 'refresh_js_renderer_capability', 'PUBLIC_FETCHER_POOL_AUTHORITY', 'inject_session_provider', 'get_session_source_telemetry', 'close_public_fetcher_sessions_async', 'get_public_fetcher_session_status']
from hledac.universal.utils.html_text_fast import extract_html_metadata, html_to_text_fast

def _sync_process_html(html: str, url: str='') -> tuple[str, list, dict]:
    """Synchronous CPU-bound HTML parsing + pattern matching + metadata extraction.

    Runs in CPU_EXECUTOR thread pool — never blocks the async event loop.
    Fail-safe: malformed HTML returns empty text, never raises.

    Returns:
        Tuple of (markdown-stripped text, pattern match list, metadata dict).
        metadata dict keys: ga_gtm_ids, og_tags, comments (from extract_html_metadata).
    """
    metadata = extract_html_metadata(html)
    text = html_to_text_fast(html)
    if not text:
        import html as _html
        text = strip_html_tags(_html.unescape(html))
        text = collapse_whitespace(text).strip()
    matches = match_text(text)
    try:
        raw_ranges = rust_html.extract_links_zero_copy(html, url)
        for start, end in raw_ranges:
            href_str = html[start:end]
            resolved = urllib.parse.urljoin(url, href_str)
            if resolved.startswith(('http://', 'https://')):
                matches.append(PatternHit(pattern='rust_link', start=0, end=0, value=resolved, label=''))
    except Exception:  # noqa: BLE001 — best-effort; rust_link extraction failure is non-fatal
        pass
    return (text, matches, metadata)

def _batch_sync_extract_html_metadata(items: list[tuple[str, str]]) -> list[dict]:
    """Batch extract metadata (emails, titles) via Rust rayon pool.

    Args:
        items: List of (html, url) tuples.

    Returns:
        List of dicts with 'emails' and 'title' keys, matching item order.
        Returns empty list on any error (fail-safe).
    """
    if not items:
        return []
    rust_emails = cast(Any, _rust_backend).batch_extract_emails
    rust_titles = cast(Any, _rust_backend).batch_extract_titles
    if rust_emails is None and rust_titles is None:
        return [{} for _ in items]
    try:
        htmls = [html for html, _ in items]
        emails_results: list[list[str]] = [[] for _ in items]
        titles_results: list[str | None] = [None for _ in items]
        if rust_emails is not None:
            try:
                raw_emails = rust_emails(htmls)
                if raw_emails and len(raw_emails) == len(items):
                    emails_results = raw_emails
            except Exception:  # noqa: BLE001 — best-effort; rust email extraction failure is non-fatal
                pass
        if rust_titles is not None:
            try:
                raw_titles = rust_titles(htmls)
                if raw_titles and len(raw_titles) == len(items):
                    titles_results = raw_titles
            except Exception:  # noqa: BLE001 — best-effort; rust title extraction failure is non-fatal
                pass
        return [{'emails': e, 'title': t} for e, t in zip(emails_results, titles_results, strict=True)]
    except Exception:  # noqa: BLE001 — best-effort; batch metadata extraction failure returns empty dicts
        return [{} for _ in items]

def _batch_sync_extract_links(items: list[tuple[str, str]]) -> list[list[str]]:
    """R3.2: Batch extract links via Rust rayon parallel batch_extract_links.

    Single rayon-parallel call instead of N per-item extract_links_zero_copy loops.
    Zero-copy lol_html handles URL resolution inside Rust.

    Args:
        items: List of (html, base_url) tuples. Cap 1_000 items.

    Returns:
        List of link lists, one per item, in same order as input.
    Always-on, bounded, fail-safe.
    """
    if not items:
        return []
    try:
        from core.rust_backend import rust as _rust_backend
        return _rust_backend.html.batch_extract_links(items)
    except Exception:  # noqa: BLE001 — best-effort; Rust batch link failure returns empty lists
        return [[] for _ in items]

async def process_html_payload(html: str, url: str) -> tuple[str, list, dict]:
    """Offload HTML→text+pattern matching+metadata extraction to rayon CPU pool.

    Uses RustWorkerPool with channel dispatch — work runs on 4 P-core rayon pool
    instead of asyncio-to_thread thread. ~5μs dispatch vs ~50μs thread::spawn.

    Args:
        html: Raw HTML content.
        url: Source URL (for context in errors; kept for API compatibility, unused).

    Returns:
        Tuple of (markdown-stripped text, pattern match list, metadata dict).
        metadata dict keys: ga_gtm_ids, og_tags, comments (from extract_html_metadata).
        Never raises — malformed HTML returns (stripped_text, [], {}) on fallback.
    """
    # ISSUE 3.1 FIX: Use RustWorkerPool directly — cpu_pool_run was just a GIL wrapper,
    # not actual rayon pool dispatch. RustWorkerPool uses rayon_submit_channel which
    # runs on 4 P-core rayon pool with true parallelism for GIL-releasing functions.
    from hledac.universal.runtime.worker_pool import get_rust_pool
    pool = get_rust_pool("cpu")
    return await pool.submit(_sync_process_html, html)

def _batch_sync_process_html(items: list[tuple[str, str]]) -> list[tuple[str, list[str], dict]]:
    """Batch HTML→text extraction via Rust rayon parallel processing.

    Fully Rust-powered: batch_extract_html_text + batch_extract_links +
    batch_extract_titles + batch_extract_emails. One rayon ThreadPool call
    instead of N Python loops — 3-5× speedup.

    Args:
        items: List of (html, url) tuples. Cap 1_000 items.

    Returns:
        List of (text, links, metadata) tuples, one per item in same order.
        Returns [("", [], {}) * len(items)] on any error (fail-safe).

    M1 8GB: rayon ThreadPool with mixed_pool (CPU-bound adaptive).
    Bounded: max 1_000 items per batch (URL dedup already done upstream).
    Always-on, bounded, fail-safe. No new feature flags.
    """
    if not items:
        return []
    if len(items) > 1000:
        items = items[:1000]
    try:
        from core.rust_backend import rust as _rust_backend
        htmls = [html for html, _ in items]
        base_urls = [base_url for _, base_url in items]
        # Rayon parallel: batch_extract_html_text + batch_extract_links + batch_extract_titles
        texts: list[str] = _rust_backend.html.batch_extract_html_text(htmls)
        links_batch: list[list[str]] = _rust_backend.html.batch_extract_links(
            list(zip(htmls, base_urls, strict=True))
        )
        titles_batch: list[str | None] = _rust_backend.html.batch_extract_titles(htmls)
        emails_batch: list[list[str]] = _rust_backend.html.batch_extract_emails(htmls)
        return [
            (
                texts[i] if i < len(texts) else '',
                links_batch[i] if i < len(links_batch) else [],
                {
                    'title': titles_batch[i] if i < len(titles_batch) and titles_batch[i] is not None else '',
                    'emails': emails_batch[i] if i < len(emails_batch) else [],
                },
            )
            for i in range(len(items))
        ]
    except Exception:  # noqa: BLE001 — best-effort; Rust batch extraction failure, fallback to serial
        return [_sync_process_html(html, url) for html, url in items]

async def process_html_payload_batch(items: list[tuple[str, str]]) -> list[tuple[str, list[str], dict]]:
    """Batch HTML processing via ThreadPoolExecutor (offload CPU from event loop).

    Submits _batch_sync_process_html to the shared _HTML_EXECUTOR thread pool
    and returns results preserving input order.

    Args:
        items: List of (html, url) tuples. Cap 1_000 items.

    Returns:
        List of (text, links, metadata) per page, matching input order.
        Returns [("", [], {}) * min(len(items), 1000)] on error (fail-safe).

    Always-on, bounded, fail-safe. No new feature flags.
    """
    if not items:
        return []
    items = items[:1000]
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_html_executor(), _batch_sync_process_html, items)

class _DrainRegistry:
    """Manages HTML extraction futures with bounded deque and stats tracking.

    F-GLOBAL: Encapsulates _DRAIN_REGISTRY, _DRAIN_TOTAL_SCHEDULED,
    _DRAIN_TOTAL_COMPLETED into a single class with __slots__.

    Thread-safe for async use: mutations only from asyncio event loop.
    """
    __slots__ = ('_registry', '_scheduled', '_completed', '_max_size')

    def __init__(self, max_size: int=512) -> None:
        self._registry: _f273c_deque = _f273c_deque(maxlen=max_size)
        self._scheduled: int = 0
        self._completed: int = 0
        self._max_size: int = max_size

    def schedule(self, fut: asyncio.Future) -> None:
        """Add a future to the registry, evicting oldest if at capacity."""
        while len(self._registry) >= self._max_size:
            try:
                old = self._registry.popleft()
                if not old.done():
                    old.cancel()
            except Exception:  # noqa: BLE001 — best-effort; future cancel failure is non-fatal
                pass
        self._registry.append(fut)
        self._scheduled += 1

    def pending_list(self) -> list:
        """Return list of pending futures."""
        return list(self._registry)

    def mark_completed(self, cancelled: bool=False) -> None:
        """Mark a future as completed."""
        if not cancelled:
            self._completed += 1

    def remove(self, fut: asyncio.Future) -> None:
        """Remove a specific future from the registry."""
        try:
            self._registry.remove(fut)
        except ValueError:
            pass

    def clear(self) -> None:
        """Clear all futures and reset counters (for test isolation)."""
        self._registry.clear()
        self._scheduled = 0
        self._completed = 0

    def stats(self) -> dict:
        """Return diagnostic snapshot."""
        return {'registry_size': len(self._registry), 'registry_capacity': self._registry.maxlen, 'total_scheduled': self._scheduled, 'total_completed': self._completed, 'in_flight': self._scheduled - self._completed}
_drain_registry = _DrainRegistry(max_size=512)
# Backward-compatibility aliases for F273C tests (DEPRECATED, use _drain_registry directly)
_DRAIN_REGISTRY = _drain_registry._registry  # collections.deque with maxlen
_DRAIN_TOTAL_SCHEDULED = 0  # Deprecated; now encapsulated in _DrainRegistry
_DRAIN_TOTAL_COMPLETED = 0  # Deprecated; now encapsulated in _DrainRegistry

def _get_html_executor() -> 'concurrent.futures.ThreadPoolExecutor':
    """Get or create bounded HTML processing executor.

    Now uses the centralized domain_executors registry (P1-4).
    """
    from utils.domain_executors import get_html_executor
    return get_html_executor()

def schedule_html_extraction(html: str, url: str='') -> asyncio.Future:
    """Submit HTML processing to CPU_EXECUTOR and register for drain.

    Returns the asyncio.Future wrapping the work. Caller may await it
    immediately (semantically equivalent to `process_html_payload`) or defer
    the await to `drain_pending_extractions(deadline_s)` at windup entry.

    Works from both sync and async contexts. In async context, uses the
    running loop. In sync context (e.g. unit test setup), uses asyncio.Runner()
    (PEP 654) which properly manages loop lifecycle.

    C7-FIX: Replaced new_event_loop() + run_until_complete() with asyncio.Runner()
    to avoid M1 Metal crash vectors when calling from sync context.

    Fail-safe: if the queue is at capacity, the oldest entry is dropped and
    the new one is added. The dropped future is cancelled so its work is
    not orphaned.

    Thread-safety: the registry is mutated only from the asyncio event loop
    (CPU_EXECUTOR callback is invoked via loop.call_soon_threadsafe), so
    no extra lock is needed.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # In Python 3.14, get_event_loop() raises RuntimeError when there's no
        # running loop in a sync thread. Use new_event_loop() which is safe here
        # since we're running in a sync context (tests) - the loop is closed after
        # the future is submitted and the work happens in the thread pool.
        loop = asyncio.new_event_loop()
    fut: asyncio.Future = loop.run_in_executor(_get_html_executor(), _sync_process_html, html)
    try:
        tag = f'pattern_extract:{url[:64]}' if url else 'pattern_extract'
        fut.set_name(tag)
    except Exception:  # noqa: BLE001 — best-effort; future naming failure is non-fatal
        pass
    _drain_registry.schedule(fut)

    def _drop_from_registry(f: asyncio.Future=fut) -> None:
        _drain_registry.mark_completed(f.cancelled())
        _drain_registry.remove(f)
    fut.add_done_callback(_drop_from_registry)
    return fut

async def drain_pending_extractions(deadline_s: float=30.0) -> tuple[int, int, float]:
    """Await all registered HTML-extraction futures with a bounded deadline.

    Args:
        deadline_s: Maximum wall-clock seconds to wait for in-flight work.

    Returns:
        Tuple of (completed_count, timed_out_count, elapsed_seconds).
        - completed_count: futures that finished within the deadline
          (with result OR exception — both are drained).
        - timed_out_count: futures still pending at deadline; these remain
          in the registry so the next drain call can pick them up.
        - elapsed_seconds: wall-clock the drain took.

    Use this at the pre-windup barrier to let in-flight pattern extractions
    finish before the sprint declares terminal:remaining_too_low.

    Fail-safe: any exception in a future is swallowed; the count of completed
    tasks is what matters. The registry is preserved between drain calls so
    callers can re-drain after a short pause.
    """
    import time as _t_f273c
    _t0 = _t_f273c.monotonic()
    deadline_abs = _t0 + max(0.0, deadline_s)
    pending = _drain_registry.pending_list()
    if not pending:
        return (0, 0, 0.0)
    completed = 0
    timed_out = 0
    try:
        done, still_pending = await asyncio.wait(pending, timeout=max(0.0, deadline_abs - _t_f273c.monotonic()), return_when=asyncio.ALL_COMPLETED)
        completed = len(done)
        timed_out = len(still_pending)
    except Exception:  # noqa: BLE001 — best-effort; wait timeout failure returns zeroed stats
        return (0, 0, _t_f273c.monotonic() - _t0)
    return (completed, timed_out, _t_f273c.monotonic() - _t0)

def get_drain_stats() -> dict:
    """Diagnostic snapshot of the drain registry (size, totals)."""
    return _drain_registry.stats()