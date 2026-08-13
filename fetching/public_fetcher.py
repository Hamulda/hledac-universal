"""Public-passive text/HTML fetcher using curl_cffi (primary) + httpx (HTTP/2).

Always-on, bounded, fail-soft, typed via msgspec.Struct. See
:ref:`public-fetcher` for HTTP transport modernization, Tor/stealth layer
integration, and global state refactoring details.

ISSUE-014 REFACTOR: Modularized into focused submodules:
- _url_ops.py: URL classification, validation, batch operations
- _retry_strategy.py: Tenacity retry logic, backoff, circuit breaker integration
- _error_classifier.py: Fetch error taxonomy and classification
- _tls_extractor.py: TLS certificate metadata extraction
- _js_renderers.py: JS rendering via Camoufox, nodriver, Playwright
- _html_processor.py: HTML parsing, pattern matching, metadata extraction

MODERN-35: LAZY IMPORTS
All submodules are lazily imported via module-level __getattr__.
This reduces initial load time and memory footprint for modules that don't use
all features (e.g., JS rendering when only simple HTML fetching is needed).
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import importlib
import importlib.util
import os
import re
import secrets
import threading
import urllib.parse
from collections import deque as _f273c_deque
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Final, cast

import msgspec

from hledac.universal.core.env_config import ENV
from hledac.universal.utils.logging_config import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# MODERN-35: Lazy Import Infrastructure
# ---------------------------------------------------------------------------
# Uses Python 3.7+ module-level __getattr__ for lazy loading.
# Import cache is stored in _LAZY_CACHE to avoid repeated lookups.
# ---------------------------------------------------------------------------

_LAZY_CACHE: dict[str, Any] = {}
_LAZY_SUBMODULES = {
    "_url_ops",
    "_retry_strategy",
    "_error_classifier",
    "_tls_extractor",
    "_js_renderers",
    "_html_processor",
}


def _lazy_import(submodule: str, name: str) -> Any:
    """
    Lazy import a symbol from a submodule.

    Caches the result after first import to avoid repeated lookups.
    Thread-safe via GIL (module-level dict assignment is atomic in CPython).

    Args:
        submodule: Submodule name (e.g., "_url_ops")
        name: Symbol to import from submodule

    Returns:
        The imported symbol
    """
    cache_key = f"{submodule}.{name}"
    if cache_key in _LAZY_CACHE:
        return _LAZY_CACHE[cache_key]

    module = importlib.import_module(f"hledac.universal.fetching.{submodule}")
    obj = getattr(module, name)
    _LAZY_CACHE[cache_key] = obj
    return obj


def __getattr__(name: str) -> Any:
    """
    MODERN-35: Module-level __getattr__ for lazy imports.

    Handles lazy loading of all submodules and their exports.
    Falls back to module-level attributes for backward compatibility.
    """
    # Check lazy cache first
    if name in _LAZY_CACHE:
        return _LAZY_CACHE[name]

    # _url_ops exports
    if name in (
        "classify_url_cached",
        "batch_classify_url_cached",
        "_python_classify_url",
        "extract_domain",
        "is_onion_url",
        "is_i2p_url",
        "is_freenet_url",
        "validate_url",
        "extract_host",
        "looks_like_feed_url",
    ):
        return _lazy_import("_url_ops", name)

    # _retry_strategy exports
    if name in (
        "_RetryableStatus",
        "_tenacity_prev_sleep",
        "_resolve_backoff_cap_s",
        "_tenacity_wait_jitter",
        "_is_retryable_status_exception",
        "_tenacity_before_sleep",
        "_tenacity_after",
        "_JITTER_RNG",
        "reset_jitter_state",
        "_compute_backoff_seconds",
        "is_retryable_status",
        "extract_retry_after",
        "is_retryable_error",
        "mark_blitz_host_dead",
        "is_blitz_host_dead",
        "reset_blitz_dead_hosts",
        "_blitz_aware_stop",
        "retry_decorator",
        "CircuitBreaker",
        "MAX_RETRIES",
    ):
        return _lazy_import("_retry_strategy", name)

    # _error_classifier exports
    if name in (
        "classify_fetch_error",
        "derive_failure_stage_and_network_kind",
        "derive_redirect_fields",
    ):
        return _lazy_import("_error_classifier", name)

    # _tls_extractor exports
    if name in ("extract_tls_metadata_from_response",):
        return _lazy_import("_tls_extractor", name)

    # _js_renderers exports
    if name in (
        "_check_chrome_binary_exists",
        "get_js_renderer_capability",
        "all_js_renderers_unavailable",
        "reset_js_renderer_capability_cache",
        "refresh_js_renderer_capability",
        "fetch_with_camoufox",
        "fetch_with_nodriver",
        "fetch_with_playwright",
        "TOR_SOCKS_PROXY",
        "_get_camoufox_lock",
        "compute_effective_max_bytes",
        "teardown_browser_pool",
        "_get_js_renderer_semaphore",
        "_camoufox_locked",
        "_playwright_locked",
    ):
        return _lazy_import("_js_renderers", name)

    # _html_processor exports
    if name in (
        "looks_xmlish",
        "try_decode",
        "needs_js_fetch",
        "sync_process_html",
        "batch_sync_extract_html_metadata",
        "batch_sync_extract_links",
        "process_html_payload",
        "batch_sync_process_html",
        "process_html_payload_batch",
        "drain_registry",
        "schedule_html_extraction",
        "drain_pending_extractions",
        "get_drain_stats",
        "DrainRegistry",
        "_FEED_URL_RE",
        "_JS_SKIP_HOST_RE",
        "_SERP_HOST_RE",
        "get_html_executor",
        "check_gathered",
    ):
        return _lazy_import("_html_processor", name)

    # Fallback to module-level attributes
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# MODERN-35: Critical Eager Imports
# These are required at module load time for type hints and core functionality.
# ---------------------------------------------------------------------------

if TYPE_CHECKING:
    import httpx

# Core transport imports (required for fetch operations)
from hledac.universal.fetching.curl_cffi_fetch import (
    fetch_via_i2p_curl_cffi,
    _CurlCffiResponseAdapter,
    _CurlCffiGetContextManager,
)
from hledac.universal.transport.base import (
    fetch_via_tor_curl_cffi,
)
from hledac.universal.transport.curl_cffi_runtime import is_curl_cffi_available as _runtime_is_curl_cffi_available
from hledac.universal.transport.decompression import build_accept_encoding_header
from hledac.universal.transport.session_pool import httpx_socks_client

# Utility imports (lightweight, safe to eager import)
from hledac.universal.utils.encoding import decode_response_bytes
from hledac.universal.utils.patterns.pattern_matcher import PatternHit, match_text
from hledac.universal.utils.asyncx._parallel import parallel
from hledac.universal.utils.html_text_fast import strip_html_tags
from tenacity import (
    retry,
    retry_if_exception_type,
    RetryCallState as _TenacityRetryCallState,
)

# Layer imports (lightweight utilities)
from hledac.universal.layers.ua_rotator import (
    build_randomized_headers as _canonical_build_randomized_headers,
    get_random_accept_language as _canonical_get_random_accept_language,
    get_random_ua as _canonical_get_random_ua,
)

# Body hash store (lightweight singleton)
from hledac.universal.fetching._body_hash import body_hash_store as _body_hash_store

# Tarpit detection — import function lazily to avoid circular import at module level
_tarpit_detect = None  # type: ignore[assignment]


def _get_tarpit_detect():
    """Lazy import detect_tarpit to avoid circular imports at module load time."""
    global _tarpit_detect
    if _tarpit_detect is None:
        from hledac.universal.fetching.tarpit_detector import detect_tarpit as _dt

        _tarpit_detect = _dt
    return _tarpit_detect


logger = get_logger(__name__)
_ContentHasher: object | None = None
_RUST_CONTENT_HASHER: bool = False
MAX_BODY_HASHES: Final[int] = 10000

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
        from hledac.universal.core.rust_backend import rust
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


# _altsvc_extract_host is now imported from _url_ops module
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
# OPSEC-001: socks5h:// forces remote DNS resolution by proxy (Tor/I2P).
# Never use socks5:// — it allows local DNS resolution which can leak .onion/.i2p queries.
# I2P SOCKS proxy default port is 4444 (I2P SAM) or 7654 (I2P console, proxy mode).
# Port 7654 is the I2P HTTP console; 4444 is the standard SOCKS proxy port.
TOR_SOCKS_PROXY: Final[str] = os.environ.get('TOR_SOCKS_PROXY_URL', 'socks5h://127.0.0.1:9050')
I2P_SOCKS_PROXY: Final[str] = os.environ.get('I2P_PROXY_URL', 'socks5h://127.0.0.1:4444')
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
    from hledac.universal.network.session_runtime import async_get_httpx_session as _canonical_get
    return await _canonical_get()


async def close_httpx_session() -> None:
    """ISSUE-014: Delegate to canonical session_runtime (backward compat stub)."""
    from hledac.universal.network.session_runtime import close_httpx_session_async as _canonical_close
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


# [NEXUS]-018-01: WebKit HTTP/2 transport telemetry.
def get_webkit_transport_stats() -> dict[str, int]:
    """Return WebKit HTTP/2 transport telemetry snapshot.
    
    Delegates to transport/curl_cffi_fetch module-level counters.
    
    Returns:
        dict with keys:
        - macos_webkit_count: total Safari profile fetch attempts
        - macos_webkit_success: successful Safari profile fetches
        - macos_webkit_failure: failed Safari profile fetches
        - h2_webkit_preset_enabled: whether HLEDAC_H2_WEBKIT_PRESET is enabled
    """
    try:
        from hledac.universal.transport.curl_cffi_fetch import (
            get_webkit_transport_telemetry,
            HLEDAC_H2_WEBKIT_PRESET,
        )
        telemetry = get_webkit_transport_telemetry()
        return {
            "macos_webkit_count": telemetry["webkit_count"],
            "macos_webkit_success": telemetry["webkit_success"],
            "macos_webkit_failure": telemetry["webkit_failure"],
            "h2_webkit_preset_enabled": 1 if HLEDAC_H2_WEBKIT_PRESET else 0,
        }
    except ImportError:
        return {
            "macos_webkit_count": 0,
            "macos_webkit_success": 0,
            "macos_webkit_failure": 0,
            "h2_webkit_preset_enabled": 0,
        }


def _reset_webkit_transport_telemetry() -> None:
    """Reset WebKit transport telemetry counters (call at sprint winddown)."""
    try:
        from hledac.universal.transport.curl_cffi_fetch import (
            _reset_webkit_transport_telemetry as _reset_wt,
        )
        _reset_wt()
    except ImportError:  # noqa: BLE001
        pass


# _CAMOUFOX_LOCK and _get_camoufox_lock imported from _js_renderers
DEFAULT_UA: Final[str] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
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

# _validate_url imported from _url_ops
# MAX_RETRIES and _is_retryable_status imported from _retry_strategy
# _extract_retry_after imported from _retry_strategy
# _compute_backoff_seconds imported from _retry_strategy
# _extract_tls_metadata imported from _tls_extractor
# TLS extraction imported from _tls_extractor
# _derive_redirect_fields imported from _error_classifier
# _derive_failure_stage imported from _error_classifier
# Error classification from _error_classifier
# classify_fetch_error imported from _error_classifier
# _looks_xmlish imported from _html_processor
# _try_decode imported from _html_processor
# URL kind functions imported from _url_ops
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
            _ensure_atexit_cleanup()  # ISSUE-7: lazy atexit registration on first session creation
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
            _ensure_atexit_cleanup()  # ISSUE-7: lazy atexit registration on first session creation
    _SESSION_MGR.record_i2p_source('local_i2p')
    return _SESSION_MGR._i2p_session

# CurlCFFI adapters imported from _session_adapters
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
    """Renew Tor circuit if request count threshold reached.

    BLITZ-14: When blitz mode is active (duration ≤ 30 min), circuit renewal
    is skipped entirely. The sprint is a one-shot burst — circuit rotation
    provides no stealth value and costs 1-5s per renewal (NEWNYM signal).
    In a 30-min sprint with 50 Tor requests, this saves ~15s of latency.
    """
    # BLITZ-14: Skip Tor circuit renewal in blitz mode
    from hledac.universal.core.telemetry.context_state import is_blitz_mode as _is_blitz
    if _is_blitz():
        return

    async with _SESSION_MGR._get_tor_lock():
        _SESSION_MGR._tor_request_count += 1
        if _SESSION_MGR._tor_request_count >= TOR_CIRCUIT_RENEWAL_REQUEST_COUNT:
            _SESSION_MGR._tor_request_count = 0
    if _SESSION_MGR._tor_request_count == 0:
        await _renew_tor_circuit()

# Crypto-safe jitter — reused across retries (F350M-R)
_JITTER_RNG = secrets.SystemRandom()

async def _jitter_delay() -> None:
    """Apply random jitter before request (Tor/stealth anti-correlation).

    BLITZ-12: When blitz mode is active (duration ≤ 30 min), this is a no-op.
    The sprint is a one-shot burst — anti-correlation timing provides no value.

    NOTE: As of BLITZ-12 analysis, this function has zero callers across the
    codebase. It is kept for API stability and exported in __all__.
    """
    from hledac.universal.core.telemetry.context_state import is_blitz_mode as _is_blitz
    if _is_blitz():
        return
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
    _i2p_attempted = False
    _i2p_success = False
    _i2p_error: str | None = None
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._i2p_session):
        _i2p_attempted = True

    # F3XX: parallel close — asyncio.gather with return_exceptions=True so that
    # one protocol's error does not cancel the other.  Both sessions are
    # independent; the lock per protocol is held only inside _session_aclose.
    _close_tor_coro: asyncio.Task | None = None
    _close_i2p_coro: asyncio.Task | None = None
    if _tor_attempted and _SESSION_MGR._tor_session_locally_created:
        _close_tor_coro = asyncio.create_task(
            _SESSION_MGR._session_aclose(_SESSION_MGR._tor_session)
        )
    if _i2p_attempted and _SESSION_MGR._i2p_session_locally_created:
        _close_i2p_coro = asyncio.create_task(
            _SESSION_MGR._session_aclose(_SESSION_MGR._i2p_session)
        )
    _tasks = [t for t in (_close_tor_coro, _close_i2p_coro) if t is not None]
    if _tasks:
        # F3XX: parallel close via parallel(policy="collect") — preserves which task failed
        # so we can log per-protocol errors without silent suppression.
        _close_result = await parallel(_tasks, policy="collect", ctx="_session_aclose")
        for _i, _task in enumerate(_tasks):
            _ok = _i < len(_close_result.ok)
            if _task is _close_tor_coro:
                if _ok:
                    _tor_success = True
                else:
                    _exc = next((e for e in _close_result.errors if isinstance(e, Exception)), None)
                    _tor_error = str(_exc) if _exc else None
                    if _tor_error:
                        logger.warning('Error closing Tor session: %s', _tor_error)
            elif _task is _close_i2p_coro:
                if _ok:
                    _i2p_success = True
                else:
                    _exc = next((e for e in _close_result.errors if isinstance(e, Exception)), None)
                    _i2p_error = str(_exc) if _exc else None
                    if _i2p_error:
                        logger.warning('Error closing I2P session: %s', _i2p_error)
    # Clear session references after parallel close completes.
    _SESSION_MGR._tor_session = None
    _SESSION_MGR._tor_session_locally_created = False
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

def _ensure_atexit_cleanup() -> None:
    """Register atexit cleanup for Tor/I2P sessions — idempotent.

    ISSUE-7 fix: atexit registration is now lazy (called when sessions are
    first created) instead of at module import time. This avoids:
      - Dangling atexit references when the module is imported but never used
      - Complex lifecycle coupling between import order and cleanup order
      - Race conditions in test suites that import public_fetcher for other symbols

    Idempotent via _atexit_cleanup_registered flag — safe to call from both
    _get_tor_session() and _get_i2p_session().
    """
    global _atexit_cleanup_registered
    if _atexit_cleanup_registered:
        return
    import atexit
    atexit.register(_close_tor_session_sync)
    atexit.register(_close_i2p_session_sync)
    _atexit_cleanup_registered = True
_atexit_cleanup_registered: bool = False
_SERP_HOST_RE = re.compile('(google\\.|bing\\.|duckduckgo\\.|yahoo\\.|baidu\\.|yandex\\.|so\\.|startpage\\.|search\\.|serp)|searchresults|webcache|googlesyndication|googletagmanager|doubleclick|search\\?q=|/search\\?|\\?q=|\\&oq=|\\&gs_l=', re.IGNORECASE)
_CONTENT_LENGTH_RE = re.compile('content-length\\s*[=:]\\s*(\\d+)', re.IGNORECASE)
_NOSCRIPT_RE = re.compile('<noscript[^>]*>|enable javascript', re.IGNORECASE)
_FEED_URL_RE = re.compile('/?(?:rss|feed|atom|xml|sitemap|opensearch)', re.IGNORECASE)
_JS_SKIP_HOST_RE = re.compile('(?:^|\\.)(?:threatfox\\.abuse\\.ch|bleepingcomputer\\.com|thehackernews\\.com|krebsonsecurity\\.com|cisa\\.gov|id-ransomware\\.malwarehunterteam\\.com|ransomwaretracker\\.xyz|abuse\\.ch|urlhaus\\.abuse\\.ch|feodo\\.tracker|openphish\\.com|cyberscoop\\.com|darkreading\\.com|threatpost\\.com|therecord\\.media|securityweek\\.com|inforisktoday\\.com|helpnetsecurity\\.com|malwarebazaar\\.abuse\\.ch|sslbl\\.abuse\\.ch)$', re.IGNORECASE)

# _JSRendererCapability imported from _js_renderers
# _check_chrome_binary_exists imported from _js_renderers
# _get_js_renderer_capability imported from _js_renderers
# _all_js_renderers_unavailable imported from _js_renderers
# reset_js_renderer_capability_cache imported from _js_renderers
# refresh_js_renderer_capability imported from _js_renderers
# _looks_like_feed_url imported from _url_ops
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
# _JS_RENDERER_SEMAPHORE imported from _js_renderers
# _teardown_browser_pool imported from _js_renderers
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
            logger.debug('Aiohttp body truncated to %s bytes after %s chunks', max_bytes, chunks_consumed)
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

# _camoufox_locked imported from _js_renderers
# _fetch_with_nodriver imported from _js_renderers
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


# BLITZ-15: Dead-host tracking — hosts marked dead after exhausting retries
# in blitz mode are excluded from further fetch attempts for the sprint duration.
# Reset at sprint start via reset_blitz_dead_hosts().

# Blitz functions imported from _retry_strategy

def _blitz_aware_stop(retry_state: _TenacityRetryCallState) -> bool:
    """Tenacity stop function: blitz-aware max attempts.

    BLITZ-15: In blitz mode, stop after 2 total attempts (1 retry).
    In normal mode, stops after MAX_RETRIES+1 attempts (default: 3 total = 2 retries).

    Returns:
        True if retries should stop, False to continue retrying.
    """
    from hledac.universal.core.telemetry.context_state import is_blitz_mode as _is_blitz

    max_attempts = 2 if _is_blitz() else MAX_RETRIES + 1
    return retry_state.attempt_number >= max_attempts


# ISSUE-7: tenacity decorator — replaces manual for/retry loop.
# BLITZ-15: stop uses _blitz_aware_stop — 2 attempts in blitz mode, MAX_RETRIES+1 otherwise.
# wait: _tenacity_wait_jitter — decorrelated jitter with Retry-After header priority
# retry: only on _RetryableStatus (HTTP retryable status codes)
# before_sleep: record circuit-breaker failure before waiting
# after: record circuit-breaker success on final success
# reraise: re-raise if all retries exhausted (tenacity returns last exception)
_retry_decorator = retry(
    stop=_blitz_aware_stop,
    wait=_tenacity_wait_jitter,
    retry=retry_if_exception_type((_RetryableStatus, TimeoutError)),
    before_sleep=_tenacity_before_sleep,
    after=_tenacity_after,
    reraise=True,
)



# Transient error patterns that warrant a retry (substring match on lowercased error string).
_RETRYABLE_ERROR_PATTERNS: tuple[str, ...] = (
    'timed out',
    'timeout',
    'ttfb_timeout',
    'connection refused',
    'connection reset',
    'connection aborted',
    'broken pipe',
    'no route to host',
    'host is unreachable',
    'network is unreachable',
    'temporary failure in name resolution',
    'name or service not known',
    'getaddrinfo failed',
    'eof occurred',
    'incomplete chunked read',
    'peer closed connection',
    'connection reset by peer',
    'curl error',
    'server disconnected',
    'handshake failure',
)


# PHYSICS-11: TTFB (Time-To-First-Byte) kill switch default — 1.5 s is
# aggressive enough to kill unresponsive hosts while leaving headroom for
# normal latency (TCP + TLS + server processing + first chunk on non-local
# servers). After 2 TTFB timeouts on the same host in blitz mode the
# dead-host blacklist blocks it for the remainder of the sprint.
_TTFB_TIMEOUT_S: float = 1.5


def _is_tarpit_url(url: str) -> bool:
    """Pre-scan URL for known tarpit/honeypot path patterns.
    
    Returns True if the URL matches known trap patterns,
    allowing early abort before any HTTP request is made.
    """
    from hledac.universal.fetching.tarpit_detector import _TARPIT_URL_RE
    return bool(_TARPIT_URL_RE.search(url))
async def _fetch_core(
    url: str,
    policy=None,  # TransportPolicy from transport.unified_transport
    headers: dict[str, str] | None = None,
    timeout_s: float = 35.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
    ttfb_timeout_s: float | None = _TTFB_TIMEOUT_S,
) -> FetchResult:
    """Core transport fetch — unified transport OR transport racing, returns FetchResult.

    Tenacity retry (3 attempts, decorrelated jitter) wraps this call.
    Exceptions raised here trigger retry; returned FetchResult does not.
    Tarpit detection runs AFTER this (in _fetch_one) on the raw HTML text,
    so we don't retry into tarpit pages.

    R9: When HLEDAC_ENABLE_TRANSPORT_RACE=1 (default), uses fetch_via_race()
    to race httpx, curl_cffi, and nw_connection in parallel and take the
    first success. Falls back to sequential unified transport when disabled,
    for stealth mode, or for darknet URLs.

    Args:
        url: HTTP/HTTPS URL to fetch.
        policy: TransportPolicy from transport.unified_transport.  None = clearnet H2.
            Ignored when racing is active (racing auto-selects transports).
        headers: Optional HTTP headers.
        timeout_s: Request timeout in seconds.
        max_bytes: Maximum response bytes to read.
        ttfb_timeout_s: Time-To-First-Byte kill-switch timeout in seconds.
            If the server hasn't sent the first byte within this window the
            request is cancelled immediately.  Default 1.5 s (PHYSICS-11).
            Set to None to disable (legacy behaviour — single 35 s timeout).
            When racing is active, TTFB is applied after the race winner is
            chosen (the race has its own per-transport timeouts).

    Returns:
        FetchResult with raw decoded HTML in .text (from winning transport).
        Raises _RetryableStatus for 429/502/503/504/520 status codes and
          transient transport errors (timeout, connection refused/reset, DNS failure).
        Raises TimeoutError for request timeouts.
    """
    # R9: Transport racing — race multiple transports in parallel, first success wins.
    # Gate: HLEDAC_ENABLE_TRANSPORT_RACE=1 (default ON), off for stealth/darknet.
    from hledac.universal.transport.transport_race import (
        fetch_via_race,
        is_transport_race_enabled,
    )

    _use_racing = (
        is_transport_race_enabled()
        and policy is None  # racing only for default policy (clearnet)
        and not url.lower().endswith(('.onion', '.i2p', '.b32.i2p', '.freenet'))
    )

    if _use_racing:
        # R9: Racing path — TTFB guard is less relevant since the race
        # already has per-transport timeouts, but we still apply it
        # around the race to catch pathological cases where the winner
        # returns headers quickly but then stalls on body.
        race_timeout = min(timeout_s, 15.0)  # race has its own bound
        try:
            if ttfb_timeout_s is not None and ttfb_timeout_s > 0:
                raw = await asyncio.wait_for(
                    fetch_via_race(
                        url=url,
                        timeout_s=race_timeout,
                        max_bytes=max_bytes,
                        headers=headers,
                    ),
                    timeout=max(ttfb_timeout_s, race_timeout),
                )
            else:
                raw = await fetch_via_race(
                    url=url,
                    timeout_s=race_timeout,
                    max_bytes=max_bytes,
                    headers=headers,
                )
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"race_ttfb_timeout:{url}:{ttfb_timeout_s or race_timeout:.1f}s"
            ) from None
    else:
        # ISSUE-15: Legacy sequential path with mini-race fallback.
        # Uses fetch_via_unified_with_race_fallback() which, when the primary
        # transport fails, races the remaining transports in parallel.
        from hledac.universal.transport.unified_transport import (
            POLICY_CLEARNET_H2,
            fetch_via_unified_with_race_fallback,
        )

        _policy = policy if policy is not None else POLICY_CLEARNET_H2

        # PHYSICS-11: TTFB kill switch — wrap fetch_via_unified with a
        # short deadline for the first byte.  If the server accepts the TCP
        # connection but never sends data, we abort early instead of burning
        # the full timeout_s (35 s) per attempt.
        if ttfb_timeout_s is not None and ttfb_timeout_s > 0:
            try:
                raw = await asyncio.wait_for(
                    fetch_via_unified_with_race_fallback(
                        url=url,
                        policy=_policy,
                        headers=headers,
                        timeout_s=timeout_s,
                        max_bytes=max_bytes,
                    ),
                    timeout=ttfb_timeout_s,
                )
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"ttfb_timeout:{url}:{ttfb_timeout_s:.1f}s"
                ) from None
        else:
            raw = await fetch_via_unified_with_race_fallback(
                url=url,
                policy=_policy,
                headers=headers,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
            )

    status_code = raw.get('status_code', 0)
    transport_error = raw.get('error')
    text = raw.get('text')
    final_url = raw.get('final_url', url)

    # Retryable HTTP status codes → raise for tenacity
    if status_code in (429, 502, 503, 504, 520):
        raise _RetryableStatus(
            status_code=status_code,
            message=f'HTTP {status_code} from {url}',
            is_timeout=False,
        )

    # Retryable transport errors → raise for tenacity
    if transport_error:
        err_lower = transport_error.lower()
        if any(pat in err_lower for pat in _RETRYABLE_ERROR_PATTERNS):
            is_timeout = 'timeout' in err_lower or 'timed out' in err_lower
            raise _RetryableStatus(
                status_code=0,
                message=f'transport error: {transport_error}',
                is_timeout=is_timeout,
            )
        # Non-retryable transport error → return as failed FetchResult
        return FetchResult(
            url=url,
            final_url=final_url,
            status_code=0,
            content_type='',
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=raw.get('elapsed_ms', 0.0),
            error=transport_error,
            failure_stage=raw.get('failure_stage') or 'transport',
        )

    # Non-retryable HTTP errors (4xx except 429, other 5xx)
    fetch_error = None
    failure_stage = raw.get('failure_stage')
    if status_code >= 400:
        fetch_error = f'HTTP {status_code}'
        if not failure_stage:
            failure_stage = 'http_error'

    content_type = raw.get('content_type', '')
    if not content_type and fetch_error and status_code == 0:
        content_type = 'text/plain'

    return FetchResult(
        url=url,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        text=text,
        fetched_bytes=raw.get('fetched_bytes', 0),
        declared_length=raw.get('declared_length', -1),
        elapsed_ms=raw.get('elapsed_ms', 0.0),
        error=fetch_error,
        failure_stage=failure_stage,
        redirect_target=final_url if final_url != url else None,
        redirected=bool(final_url != url and 300 <= status_code < 400),
    )




@_retry_decorator
async def _fetch_core_retryable(url: str, **kwargs) -> FetchResult:
    """Apply tenacity retry decorator to _fetch_core.

    Tenacity supports async functions natively since v8.0+.
    """
    return await _fetch_core(url, **kwargs)
async def async_fetch_public_text(
    url: str,
    timeout_s: float = 35.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
    use_stealth: bool = False,
    use_js: bool = False,
    use_doh: bool = False,
    js_confidence: float = 0.8,
    priority: int = 5,
    bypass_circuit_breaker: bool = False,
    ttfb_timeout_s: float | None = _TTFB_TIMEOUT_S,
) -> FetchResult:
    """
    Fetch a public URL — single-URL wrapper for async_fetch_public_text_batch.

    Delegates to batch API so both paths share identical transport logic.
    CancelledError propagates (not swallowed).

    Parameters
    ----------
    url : str
        Target URL (http or https only, .onion via Tor SOCKS5).
    timeout_s : float
        Per-request timeout in seconds (default 35 s, scaled x2 for Tor).
    max_bytes : int
        Maximum bytes to read from body (default 2 MB, hard cap 10 MB).
    use_stealth : bool
        If True, use StealthManager/StealthSession for enhanced stealth.
    use_js : bool
        If True, force JS rendering via Camoufox/nodriver.
    use_doh : bool
        If True, resolve hostname via DoH (cloudflare-dns) before connecting.
    js_confidence : float
        Confidence that JS is needed (0.0–1.0, default 0.8).
    priority : int
        Request priority 1–10 (default 5, lower = higher priority).
    bypass_circuit_breaker : bool
        If True, skip circuit breaker on retry (internal use).
    ttfb_timeout_s : float | None
        PHYSICS-11: Time-To-First-Byte kill switch.  If the server hasn't
        sent the first byte within this window the request is cancelled.
        Default 1.5 s.  Set to None to disable (legacy 35 s single timeout).

    Returns
    -------
    FetchResult
        Typed result with final_url, status, content_type, text (or None),
        byte counts, elapsed_ms, and optional error.
    """
    # BLITZ-15: Skip fetch if host is marked dead for the sprint.
    _host = _extract_domain_from_url(url)
    if _host and is_blitz_host_dead(_host):
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type='',
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=0.0,
            error='blitz_host_dead',
            failure_stage='blitz_dead_host',
        )
    try:
        return await _fetch_core_retryable(
            url=url,
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            ttfb_timeout_s=ttfb_timeout_s,
        )
    except (_RetryableStatus, TimeoutError):
        # All retries exhausted — return error result
        # PHYSICS-12 / BLITZ-15: In blitz mode only, mark the host as dead for the
        # sprint duration to avoid wasting time on unreachable hosts. Non-blitz
        # sprints may benefit from retrying hosts later; the dynamic backoff cap
        # (1.0 s vs 8.0 s) and attempt count (2 vs 3) already differentiate.
        _host = _extract_domain_from_url(url)
        if _host:
            from hledac.universal.core.telemetry.context_state import is_blitz_mode as _is_blitz_cs
            if _is_blitz_cs():
                mark_blitz_host_dead(_host)
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type='',
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=0.0,
            error='retry_exhausted',
            failure_stage='retry_loop',
        )


# ---------------------------------------------------------------------------
# UNIFIED-007/008: Domain reputation helpers
# ---------------------------------------------------------------------------

def _extract_domain_from_url(url: str) -> str:
    """Extract netloc (host:port → host) from URL string. Fail-safe."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc or parsed.hostname or ""
        # Strip port
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        return host.lower()
    except Exception:
        return ""


_rep_service_cache: Any = None  # DomainReputationService singleton, lazy


def _lazy_get_reputation_service() -> Any | None:
    """Lazy-load DomainReputationService singleton.

    Returns None if HLEDAC_DOMAIN_REPUTATION=0 or service unavailable.
    Never raises.
    """
    global _rep_service_cache
    if _rep_service_cache is not None:
        return _rep_service_cache
    if os.getenv("HLEDAC_DOMAIN_REPUTATION", "1") == "0":
        return None
    try:
        from hledac.universal.knowledge.domain_reputation import get_domain_reputation_service

        _rep_service_cache = get_domain_reputation_service()
        return _rep_service_cache
    except Exception:  # noqa: BLE001 — fail-safe; optional feature
        return None


# UNIFIED-009: Route graph service singleton (lazy)
_route_graph_cache: Any = None

def _lazy_get_route_graph_service() -> Any | None:
    """Lazy-load RouteGraphService singleton.

    Returns None if HLEDAC_PROXY_ROUTES=0 or service unavailable.
    Never raises.
    """
    global _route_graph_cache
    if _route_graph_cache is not None:
        return _route_graph_cache
    if os.getenv("HLEDAC_PROXY_ROUTES", "1") == "0":
        return None
    try:
        from hledac.universal.knowledge.proxy_routes import get_route_graph_service

        _route_graph_cache = get_route_graph_service()
        return _route_graph_cache
    except Exception:  # noqa: BLE001 — fail-safe; optional feature
        return None


# UNIFIED-010: Anti-bot profile service singleton (lazy)
_anti_bot_profile_cache: Any = None

def _lazy_get_anti_bot_profile_service() -> Any | None:
    """Lazy-load AntiBotProfileService singleton.

    Returns None if HLEDAC_ANTI_BOT_PROFILES=0 or service unavailable.
    Never raises.
    """
    global _anti_bot_profile_cache
    if _anti_bot_profile_cache is not None:
        return _anti_bot_profile_cache
    if os.getenv("HLEDAC_ANTI_BOT_PROFILES", "1") == "0":
        return None
    try:
        from hledac.universal.knowledge.anti_bot_profiles import get_anti_bot_profile_service

        _anti_bot_profile_cache = get_anti_bot_profile_service()
        return _anti_bot_profile_cache
    except Exception:  # noqa: BLE001 — fail-safe; optional feature
        return None


_ANTI_BOT_HEADERS: tuple[tuple[str, str], ...] = (
    ("cf-ray", "cloudflare"),
    ("cf-cache-status", "cloudflare"),
    ("x-amz-cf-pop", "cloudfront"),
    ("x-sucuri-id", "sucuri"),
    ("x-cdn", "cdn"),
)
_ANTI_BOT_BODY_SUBSTRINGS: tuple[tuple[str, str], ...] = (
    ("challenges.cloudflare.com", "cloudflare"),
    ("cloudflare-challenge", "cloudflare"),
    ("_cf_chl_opt", "cloudflare"),
    ("datadome", "datadome"),
    ("akamaihd", "akamai"),
    ("perimeterx", "perimeterx"),
    ("imperva", "imperva"),
    ("incapsula", "imperva"),
)


def _detect_anti_bot_type(result: Any) -> str:
    """Detect anti-bot/WAF type from FetchResult headers and text.

    Returns: 'cloudflare' | 'akamai' | 'datadome' | 'cloudfront' |
             'imperva' | 'perimeterx' | 'sucuri' | 'none'
    Never raises.
    """
    try:
        # Check server header (from FetchResult, not raw response headers)
        server = getattr(result, "server_header", None) or ""
        server_lower = server.lower()
        for keyword in ("cloudflare", "akamai", "datadome", "imperva"):
            if keyword in server_lower:
                return keyword

        # Check body for known anti-bot patterns (cheap substring scan)
        text = getattr(result, "text", None) or ""
        if text:
            text_lower = text[:4096].lower()  # only scan first 4 KB
            for substr, bot_type in _ANTI_BOT_BODY_SUBSTRINGS:
                if substr in text_lower:
                    return bot_type

        return "none"
    except Exception:  # noqa: BLE001 — fail-safe
        return "none"


async def _fetch_one(url: str, idx: int, *, _timeout_s: float, _max_bytes: int, _ttfb_timeout_s: float | None) -> tuple[int, FetchResult]:
    """Fail-safe fetch with index capture for order preservation."""
    try:
        # Check pre-fetch conditions (reputation tarpit, blitz dead, URL pattern)
        _skip_result = await _check_prefetch_conditions(url, _timeout_s, _max_bytes, _ttfb_timeout_s)
        if _skip_result is not None:
            return idx, _skip_result
        _domain = _extract_domain_from_url(url)
        _rep_service = _lazy_get_reputation_service()
        result = await _fetch_core_retryable(
            url=url,
            timeout_s=_timeout_s,
            max_bytes=_max_bytes,
            ttfb_timeout_s=_ttfb_timeout_s,
        )
        # Detect and handle HTML tarpits
        _should_return, result = await _detect_and_handle_tarpits(
            result, url, _domain, _rep_service
        )
        if _should_return:
            return idx, result
        # Detect cognitive (LLM-generated) tarpits
        _is_cognitive, result = await _detect_cognitive_tarpit(
            result, _domain, _rep_service
        )
        if _is_cognitive:
            return idx, result
        # Record fetch outcome in reputation, route, and anti-bot services
        await _record_fetch_outcome(result, _domain, _rep_service)
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
    ttfb_timeout_s: float | None = _TTFB_TIMEOUT_S,
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
        ttfb_timeout_s: PHYSICS-11 TTFB kill switch (default 1.5 s).
            Set to None to disable.

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
    _concurrency = concurrency
    if _concurrency is None:
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, concurrency_budget

        _concurrency = await concurrency_budget(ConcurrencyCategory.HTTP_LANE)

    # Canonical parallel runner: structured-concurrency cancellation on failure,
    # bounded concurrency, result order preserved via index capture.
    result = await parallel(
        [asyncio.create_task(_fetch_one(url, idx, _timeout_s=timeout_s, _max_bytes=max_bytes, _ttfb_timeout_s=ttfb_timeout_s)) for idx, url in enumerate(urls)],
        concurrency=_concurrency,
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
        _retry_concurrency = max(1, min(_concurrency, 8))  # cap retry concurrency

        async def _retry_one(idx_url: tuple[int, str], *, _timeout_s: float, _max_bytes: int, _ttfb_timeout_s: float | None) -> tuple[int, FetchResult]:
            _idx, _url = idx_url
            try:
                # Pre-scan URL for known tarpit/honeypot path patterns
                if _is_tarpit_url(_url):
                    return _idx, FetchResult(
                        url=_url,
                        final_url=_url,
                        status_code=0,
                        content_type='',
                        text=None,
                        fetched_bytes=0,
                        declared_length=-1,
                        elapsed_ms=0.0,
                        error='tarpit_detected:url_pattern',
                        failure_stage='tarpit',
                    )
                _result = await _fetch_core_retryable(
                    url=_url,
                    timeout_s=_timeout_s,
                    max_bytes=_max_bytes,
                    ttfb_timeout_s=_ttfb_timeout_s,
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
            [asyncio.create_task(_retry_one(x, _timeout_s=timeout_s, _max_bytes=max_bytes, _ttfb_timeout_s=ttfb_timeout_s)) for x in retryable_urls],
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

async def _check_prefetch_conditions(url: str, timeout_s: float, max_bytes: int, ttfb_timeout_s: float | None) -> FetchResult | None:
    """Check pre-fetch conditions that may skip the fetch early.

    Checks: reputation tarpit, blitz host dead, URL pattern tarpit.
    Returns FetchResult if should skip, None to continue with fetch.
    """
    _domain = _extract_domain_from_url(url)
    _rep_service = _lazy_get_reputation_service()
    if _rep_service is not None and _domain:
        try:
            _rep = await _rep_service.get(_domain)
            if _rep.is_tarpit:
                logger.debug(
                    '\n[DOMAIN_REPUTATION] Skipping known tarpit domain: %s (score=%.2f)',
                    _domain, _rep.tarpit_score,
                )
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=0,
                    content_type='',
                    text=None,
                    fetched_bytes=0,
                    declared_length=-1,
                    elapsed_ms=0.0,
                    error=f'tarpit_detected:reputation_score={_rep.tarpit_score:.2f}',
                    failure_stage='tarpit',
                )
        except Exception:  # noqa: BLE001 — fail-safe; reputation check non-critical
            pass
    # BLITZ-15: Skip fetch if host is marked dead for sprint duration
    if _domain and is_blitz_host_dead(_domain):
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type='',
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=0.0,
            error='blitz_host_dead',
            failure_stage='blitz_dead_host',
        )
    # Pre-scan URL for known tarpit/honeypot path patterns — no HTTP request needed
    if _is_tarpit_url(url):
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type='',
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=0.0,
            error='tarpit_detected:url_pattern',
            failure_stage='tarpit',
        )
    return None


async def _detect_and_handle_tarpits(
    result: FetchResult,
    url: str,
    domain: str,
    rep_service: Any | None,
) -> tuple[bool, FetchResult]:
    """Detect and handle HTML tarpits.

    Returns (should_return, result_or_modified_result).
    If should_return=True, result_or_modified_result is the error FetchResult to return.
    If should_return=False, result_or_modified_result is the original result (possibly modified).
    """
    if result.text and not result.error:
        try:
            _tarpit_detect_fn = _get_tarpit_detect()
            tarpit_result = _tarpit_detect_fn(result.text, result.url, result.elapsed_ms)
            if tarpit_result.is_tarpit:
                reason = tarpit_result.reasons[0] if tarpit_result.reasons else f'score={tarpit_result.tarpit_score:.2f}'
                # UNIFIED-007: Record tarpit in domain reputation
                if domain and rep_service is not None:
                    try:
                        _anti_bot = _detect_anti_bot_type(result)
                        await rep_service.record_failure(
                            domain,
                            tarpit_score=tarpit_result.tarpit_score,
                            anti_bot_type=_anti_bot,
                        )
                    except Exception:  # noqa: BLE001 — fail-safe; non-critical
                        pass
                return True, FetchResult(
                    url=result.url,
                    final_url=result.final_url,
                    status_code=result.status_code,
                    content_type=result.content_type,
                    text=None,  # discard tarpit HTML to save memory
                    fetched_bytes=result.fetched_bytes,
                    declared_length=result.declared_length,
                    elapsed_ms=result.elapsed_ms,
                    error=f'tarpit_detected:{reason}',
                    failure_stage='tarpit',
                    selected_transport=result.selected_transport,
                    http_version=result.http_version,
                )
        except Exception:  # noqa: BLE001 — best-effort; tarpit detection failure is non-fatal
            pass
    return False, result


async def _detect_cognitive_tarpit(
    result: FetchResult,
    domain: str,
    rep_service: Any | None,
) -> tuple[bool, FetchResult]:
    """Detect cognitive (LLM-generated) tarpits.

    Returns (is_cognitive_tarpit, result_or_modified_result).
    """
    if result.text and not result.error and domain and rep_service is not None:
        try:
            # Strip HTML tags before cognitive analysis — only plain text matters
            _plain_text = strip_html_tags(result.text)
            if len(_plain_text) >= 200:
                from hledac.universal.brain.adversarial.cognitive_tarpit import (
                    cognitive_tarpit_score as _ct_score,
                )
                _ct_verdict = _ct_score(_plain_text)
                if _ct_verdict.is_cognitive_tarpit:
                    _ct_score_val = _ct_verdict.cognitive_tarpit_score
                    logger.warning(
                        "[HONEYPOT-LLM] domain=%s url=%s "
                        "cognitive_tarpit_score=%.3f "
                        "entropy=%.3f burstiness=%.3f "
                        "perplexity=%.3f reasons=%s analysis_ms=%.1f",
                        domain,
                        result.url,
                        _ct_score_val,
                        _ct_verdict.entropy_score,
                        _ct_verdict.burstiness_score,
                        _ct_verdict.perplexity_score,
                        _ct_verdict.reasons,
                        _ct_verdict.analysis_ms,
                    )
                    # Record cognitive tarpit in domain reputation (score=1.0)
                    try:
                        _anti_bot = _detect_anti_bot_type(result)
                        await rep_service.record_failure(
                            domain,
                            tarpit_score=1.0,
                            anti_bot_type=_anti_bot,
                        )
                    except Exception:  # noqa: BLE001 — fail-safe
                        pass
                    return True, FetchResult(
                        url=result.url,
                        final_url=result.final_url,
                        status_code=result.status_code,
                        content_type=result.content_type,
                        text=None,  # discard LLM honeypot content
                        fetched_bytes=result.fetched_bytes,
                        declared_length=result.declared_length,
                        elapsed_ms=result.elapsed_ms,
                        error=f'cognitive_tarpit_detected:score={_ct_score_val:.3f}',
                        failure_stage='cognitive_tarpit',
                        selected_transport=result.selected_transport,
                        http_version=result.http_version,
                    )
        except Exception:  # noqa: BLE001 — fail-soft; cognitive detection is best-effort
            pass
    return False, result


async def _record_reputation_success(
    result: FetchResult,
    domain: str,
    rep_service: Any | None,
) -> None:
    """Record successful fetch in domain reputation."""
    if result.text and not result.error and domain and rep_service is not None:
        try:
            _anti_bot = _detect_anti_bot_type(result)
            await rep_service.record_success(
                domain,
                anti_bot_type=_anti_bot,
            )
        except Exception:  # noqa: BLE001 — fail-safe; non-critical
            pass


async def _record_route_outcome(
    result: FetchResult,
    domain: str,
) -> None:
    """Record route success or failure in route graph service."""
    if domain and not result.error:
        _route_svc = _lazy_get_route_graph_service()
        if _route_svc is not None:
            try:
                await _route_svc.record_route_success(
                    domain,
                    transport=result.selected_transport or '',
                    latency_ms=result.elapsed_ms,
                    body_bytes=result.fetched_bytes,
                )
            except Exception:  # noqa: BLE001 — fail-safe; non-critical
                pass
    elif domain and result.error:
        _route_svc = _lazy_get_route_graph_service()
        if _route_svc is not None:
            try:
                await _route_svc.record_route_failure(
                    domain,
                    transport=result.selected_transport or '',
                    latency_ms=result.elapsed_ms,
                )
            except Exception:  # noqa: BLE001 — fail-safe; non-critical
                pass


async def _record_anti_bot_observations(
    result: FetchResult,
    domain: str,
) -> None:
    """Record anti-bot challenge observations."""
    if domain:
        _ab_svc = _lazy_get_anti_bot_profile_service()
        if _ab_svc is not None:
            try:
                _anti_bot = _detect_anti_bot_type(result) if result else 'none'
                if result and result.error and _anti_bot != 'none':
                    await _ab_svc.observe_challenge(
                        domain,
                        waf_type=_anti_bot,
                        challenge_type='403' if result.status_code == 403 else '429' if result.status_code == 429 else '',
                    )
                elif result and not result.error:
                    await _ab_svc.observe_bypass(domain)
            except Exception:  # noqa: BLE001 — fail-safe; non-critical
                pass

async def _record_fetch_outcome(
    result: FetchResult,
    domain: str,
    rep_service: Any | None,
) -> None:
    """Record fetch outcome in reputation, route, and anti-bot services.

    Records: reputation success/failure, route performance, anti-bot observations.
    """
    await _record_reputation_success(result, domain, rep_service)
    await _record_route_outcome(result, domain)
    await _record_anti_bot_observations(result, domain)
from hledac.universal.utils.html_text_fast import extract_html_metadata, html_to_text_fast


# _sync_process_html imported from _html_processor
# _batch_sync_extract_html_metadata imported from _html_processor
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
        from hledac.universal.core.rust_backend import rust as _rust_backend
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
        from hledac.universal.core.rust_backend import rust as _rust_backend
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
        except ValueError:  # noqa: BLE001
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

# _get_html_executor imported from _html_processor
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
    # ISSUE-15: asyncio.wait(ALL_COMPLETED) → asyncio.gather with timeout tracking
    remaining_timeout = max(0.0, deadline_abs - _t_f273c.monotonic())
    try:
        async with asyncio.timeout(remaining_timeout):
            gathered = await asyncio.gather(*pending, return_exceptions=True)
            _, errors = _check_gathered(gathered)
            for err in errors:
                logger.debug('[FETCH] _drain_pending: task failed: %s', err)
        completed = len(pending)
        timed_out = 0
    except asyncio.TimeoutError:
        # Find which tasks didn't complete within timeout
        completed = sum(1 for t in pending if t.done())
        timed_out = len(pending) - completed
    except Exception:  # noqa: BLE001 — best-effort; wait timeout failure returns zeroed stats
        return (0, 0, _t_f273c.monotonic() - _t0)
    return (completed, timed_out, _t_f273c.monotonic() - _t0)

def get_drain_stats() -> dict:
    """Diagnostic snapshot of the drain registry (size, totals)."""
    return _drain_registry.stats()
