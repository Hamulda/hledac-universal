# hledac/universal/fetching/public_fetcher.py
# Sprint 8AD — First live public text fetch adapter v1
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
import importlib.util
import os
import re
import threading
import time
import urllib.parse
from collections import deque as _f273c_deque
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any, Final, cast

import msgspec

from core.env_config import ENV  # noqa: E402
from hledac.universal.utils.cache import PyCacheDict
from runtime.logging_setup import get_logger  # Issue #33: structlog hot-path
from tools.regex_cache import collapse_whitespace, strip_html_tags

if TYPE_CHECKING:
    import httpx  # ISSUE-080: for Union type on _tor_session/_i2p_session


# psutil lazy import — Issue #17: use centralized psutil_shim instead of local _get_psutil()
from core.psutil_shim import process as _psutil_process

# F271 / Sprint 5.4 / Sprint ContentHasher: unified Rust backend.
# Uses core.rust_backend.rust as single source of truth.
# Property-based lazy loading: each capability resolves once, caches
# the result, and returns None on failure so callers always have a
# fallback path.
#
# Never imported at top level — preserves M1 lazy-load invariant.
from core.rust_backend import rust as _rust_backend  # noqa: E402

# ---------------------------------------------------------------------------
# PEP 562 — Module-level __getattr__ for lazy rust_backend attribute access
# Replaces deprecated per-function shims with a single transparent dispatch.
# Saves 5–15 µs per call: no tuple wrap, no getattr indirection.
# ---------------------------------------------------------------------------

_URL_OPS_WARNING = False


def __getattr__(name: str) -> Any:
    """Lazy module-level attribute access for rust_backend sub-domains.

    Supported names (transparent to callers of the removed shims):
        url_ops   -> rust_backend.url   (was _get_url_ops)
        rust_url  -> rust_backend.url   (direct access for internal use)

    Raises AttributeError for unknown names so normal module errors occur.
    """
    global _URL_OPS_WARNING
    if name == "url_ops":
        # Backward compat: _get_url_ops() contract returned the domain directly
        return _rust_backend.url
    if name == "rust_url":
        # Internal shortcut: bypasses shim, same as _rust_backend.url
        return _rust_backend.url
    if name == "rust_html":
        return _rust_backend.raw
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
_classify_url_cache: PyCacheDict[str, tuple[str, str]] = PyCacheDict(512, 300.0)

# Issue #4: Embedded Rust xxh3 cache — single GIL transition per batch
# Lazy singleton: initialized on first use (avoids import-time side effects)
# Issue A4: double-checked locking prevents race during concurrent asyncio.to_thread init
_url_classify_cache_rust: "Any" = None
_url_classify_cache_lock = threading.Lock()


def _get_rust_url_cache() -> "Any":
    """Lazy singleton for UrlClassifyCachePy — created on first call.

    Thread-safe via double-checked locking pattern:
    - Fast path: outer check without lock (no contention after init)
    - Slow path: inner check with lock (only first caller blocks)
    """
    global _url_classify_cache_rust
    if _url_classify_cache_rust is not None:
        return _url_classify_cache_rust
    with _url_classify_cache_lock:
        if _url_classify_cache_rust is None:
            # url_ops accessed via module __getattr__ -> _rust_backend.url
            _url_classify_cache_rust = url_ops.UrlClassifyCachePy(capacity=50_000, ttl_s=300.0)  # type: ignore[attr-defined]
    return _url_classify_cache_rust


def _classify_url_cached(url: str) -> tuple[str, str]:
    """Returns (kind_str, lowercase_host) using Rust when available.

    Uses rust_backend.rust.url.classify_url (unified Rust backend).
    Python fallback uses urllib.parse.urlparse — same correctness, ~3x slower.
    """
    cached = _classify_url_cache.get(url)
    if cached is not None:
        return cached
    try:
        result = rust_url.classify_url(url)
        _classify_url_cache.set(url, result)
        return result
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # fail-soft → fall through to Python
    # Python fallback — never raises (caller already wraps with try/except)
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            result = ("malformed", "")
        elif host.endswith(".onion"):
            result = ("onion", host)
        elif host.endswith(".i2p"):
            result = ("i2p", host)
        elif host.endswith(".freenet") or "freenet" in host or "hyphanet" in host:
            result = ("freenet", host)
        else:
            result = ("clearnet", host)
        _classify_url_cache.set(url, result)
        return result
    except Exception:
        return ("malformed", "")


def _python_classify_url(url: str) -> tuple[str, str]:
    """Pure-Python URL classifier — no cache, no Rust, no side effects.

    Exact equivalent of what Rust backend computes for classify_url.
    Used as fallback when Rust is unavailable or as Python-only path
    in _batch_classify_url_cached. Never raises.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return ("malformed", "")
        if host.endswith(".onion"):
            return ("onion", host)
        if host.endswith(".i2p"):
            return ("i2p", host)
        if ".freenet" in host or "freenet" in host or "hyphanet" in host:
            return ("freenet", host)
        return ("clearnet", host)
    except Exception:
        return ("malformed", "")


# NOTE: Current call sites are single-URL (per-fetch). When a bulk URL
# classification pipeline is added (e.g. dedup gate, link extraction,
# or batch fetch planning), replace sequential _is_onion_url /
# _is_i2p_url / _is_freenet_url / _validate_url calls with:
#   classifications = _batch_classify_url_cached(url_list)
#   for url, (kind, host) in zip(url_list, classifications):
#       if kind == "onion": ...
# This avoids N sequential Rust GIL transitions for N URLs.


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

    hard_cap = 50_000
    if len(urls) > hard_cap:
        urls = urls[:hard_cap]

    # Issue #4: Try Rust cache first (single GIL transition for all N URLs)
    try:
        cache = _get_rust_url_cache()
        return cache.classify_batch_cached(urls)
    except Exception:  # noqa: BLE001
        pass  # fail-soft → fall through to Python fallback

    # Fallback: Python PyCacheDict 3-stage approach
    results: list[tuple[str, str] | None] = [None] * len(urls)
    misses: list[tuple[int, str]] = []  # (original_index, url)

    for i, url in enumerate(urls):
        cached = _classify_url_cache.get(url)
        if cached is not None:
            results[i] = cached
        else:
            misses.append((i, url))

    if not misses:
        return results  # type: ignore[return-value]

    # Stage 2: batch Rust classify for misses (single GIL transition)
    miss_urls = [u for _, u in misses]
    try:
        batch_results = rust_url.batch_classify(miss_urls)
    except Exception:  # noqa: BLE001
        batch_results = [_python_classify_url(u) for u in miss_urls]

    # Stage 3: update cache + populate results
    for (orig_idx, url), classified in zip(misses, batch_results):
        _classify_url_cache.set(url, classified)
        results[orig_idx] = classified

    return results  # type: ignore[return-value]


# F4XX: aiohttp removed from default deps — httpx handles HTTP/2 transport
from hledac.universal.layers.ua_rotator import (
    build_randomized_headers as _canonical_build_randomized_headers,
)
from hledac.universal.layers.ua_rotator import (
    get_random_accept_language as _canonical_get_random_accept_language,
)

# Issue 10.2: canonical UA rotation — single source of truth (layers.ua_rotator)
from hledac.universal.layers.ua_rotator import (  # noqa: E402
    get_random_ua as _canonical_get_random_ua,
)

# Sprint F214: Centralized transport imports — protocol boundary
from hledac.universal.transport.base import (  # noqa: E402
    CircuitBreaker,
    TransportDecision,
    fetch_via_httpx_h2,
    fetch_via_tor_curl_cffi,
    get_breaker,
    route_transport,
    should_use_curl_cffi,
)

# F226: Body-cap helper — replaces inline duplicitu v httpx_h2 + aiohttp cestách
from hledac.universal.transport.body_limiter import BodyReadResult, _read_body_into  # noqa: E402
from hledac.universal.transport.curl_cffi_fetch import (  # noqa: E402
    _blocking_altsvc_probe_for_url,  # noqa: E402
    fetch_via_curl_cffi_cached,
    fetch_via_i2p_curl_cffi,
)

# F260: JA3 unification — curl_cffi wrappers for Tor/I2P, honest Accept-Encoding
from hledac.universal.transport.curl_cffi_runtime import is_curl_cffi_available  # noqa: E402
from hledac.universal.transport.decompression import build_accept_encoding_header  # noqa: E402

# F265B: conditional-cache wrapper for the curl_cffi stealth lane.
# Same signature as fetch_via_curl_cffi but with ETag/Last-Modified
# 304 short-circuit. Always-on inside the curl_cffi lane; opt-out
# via HLEDAC_CONDITIONAL_CACHE=0.
# F265B: speculative Alt-Svc probe — primes the H3 LRU so the
# second fetch (not the first) can use HttpVersion.v3.
# F273G-H3FIX: _blocking_altsvc_probe_for_url for synchronous pre-probing
# (awaits result before primary fetch); probe_altsvc_speculative kept for
# fire-and-forget call sites that do not need blocking semantics.
# ISSUE-080: httpx-socks for Tor/I2P SOCKS5 proxy
from hledac.universal.transport.session_pool import httpx_socks_client  # noqa: E402
from hledac.universal.utils.concurrency import (  # noqa: E402
    get_clearnet_semaphore,
    get_tor_semaphore,
)
from hledac.universal.utils.encoding import decode_response_bytes, parse_charset_from_content_type  # noqa: E402
from hledac.universal.utils.patterns.pattern_matcher import PatternHit, match_text  # noqa: E402
from hledac.universal.utils.uma_budget import M1_FETCH_SOFT_CEILING_GB  # noqa: E402

logger = get_logger(__name__)


# Sprint ContentHasher: lazy Rust import for TLS cert + body hashing.
# Mirrors the `_get_rust_url_ops` pattern above. On ImportError the
# helpers fall back to `hashlib` and the sprint continues unchanged.
#
# NEON-enabled on aarch64 (Apple Silicon M1+), scalar fallback on x86_64
# (Linux CI). Body hashes are NOT canonical — they live in a bounded
# module-level dict and are intended for cross-URL dedup metadata only.
_ContentHasher: object | None = None  # not yet resolved
_RUST_CONTENT_HASHER: bool = False  # default: hashlib fallback
_CONTENTHASHER_LOCK = threading.Lock()

MAX_BODY_HASHES: Final[int] = 10000  # bounded — invariant: každá kolekce má explicitní max


# F-GLOBAL: BodyHashStore — encapsulates _body_hashes dict + _body_hashes_lock
# Eliminates module-level mutable globals, uses __slots__ for memory efficiency


class _BodyHashStore:
    """Thread-safe bounded URL→hash store using FIFO eviction.

    F-GLOBAL: Encapsulates _body_hashes dict and _body_hashes_lock.
    Eliminates module-level globals for body hash tracking.

    Bounded: MAX_BODY_HASHES entries, FIFO eviction on overflow.
    Thread-safe: uses threading.Lock for compound operations.
    """

    __slots__ = ("_hashes", "_lock", "_max_size")

    def __init__(self, max_size: int = MAX_BODY_HASHES) -> None:
        self._hashes: dict[str, str] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def store(self, url: str, hash_hex: str) -> None:
        """Store url→hash mapping with FIFO eviction on overflow."""
        if not url or not hash_hex:
            return
        try:
            with self._lock:
                self._hashes[url] = hash_hex
                if len(self._hashes) > self._max_size:
                    oldest = next(iter(self._hashes))
                    del self._hashes[oldest]
        except Exception:  # noqa: BLE001
            pass

    def get(self, url: str) -> str | None:
        """Get hash for URL, or None if not found."""
        try:
            with self._lock:
                return self._hashes.get(url)
        except Exception:
            return None

    def clear(self) -> None:
        """Clear all stored hashes."""
        with self._lock:
            self._hashes.clear()


# Module-level singleton instance
_body_hash_store = _BodyHashStore(max_size=MAX_BODY_HASHES)


def _get_content_hasher() -> object | None:
    """Lazy-load Rust backend hash domain.

    Canonical RustBackend entry point — single lazy-load for all content hashing
    needs. Returns rust.hash on success, None on failure. Cached after first call.

    Thread-safe via double-checked locking:
    - Fast path: outer check without lock
    - Slow path: inner check with lock (only first caller blocks)
    """
    global _ContentHasher, _RUST_CONTENT_HASHER
    if _RUST_CONTENT_HASHER:
        return _ContentHasher
    with _CONTENTHASHER_LOCK:
        if _RUST_CONTENT_HASHER:
            return _ContentHasher
        try:
            from core.rust_backend import rust

            _ContentHasher = rust.hash
            _RUST_CONTENT_HASHER = True
        except Exception:
            _RUST_CONTENT_HASHER = False
            _ContentHasher = None
    return _ContentHasher


def _compute_body_hash(body: bytes) -> str:
    """Return 16-char hex fingerprint of a response body.

    Rust path (BLAKE3-64, NEON-accelerated on M1) is preferred; xxHash3
    (xxh64) is the fail-soft fallback. Returns empty string for
    empty/None body. Never raises.
    """
    if not body:
        return ""
    rh = _get_content_hasher()
    if rh is not None:
        try:
            return cast(Any, rh).blake3_64(body)
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # fall through to xxhash
    try:
        import xxhash

        return xxhash.xxh3_64(body).hexdigest()
    except Exception:
        return ""


def _store_body_hash(url: str, hash_hex: str) -> None:
    """Persist `url → hash_hex` in the bounded module-level dict.

    Body hash is POUZE metadata — it is never written to the DuckDB
    canonical store. FIFO eviction keeps the dict bounded (MAX_BODY_HASHES).
    Never raises.
    """
    _body_hash_store.store(url, hash_hex)


# ---------------------------------------------------------------------------
# F260+ / P1-2: HTTP/3 (QUIC) opportunistic upgrade.
#
# The bounded LRU cache, the curl_cffi HttpVersion.v3 lookup, the Alt-Svc
# parser, and the failure handling now live in ``transport/http3_lane``
# (single source of truth across ``public_fetcher``, ``stealth_manager``,
# and any future lane). This file keeps only a 1-line F271 Rust fast
# path for URL host extraction; the rest is delegation.
#
# Behaviour preserved (regression-tested in tests/probe_p12_http3_lane/):
# - disabled by default, opt-in via HLEDAC_ENABLE_HTTPX_H3=1 (or the
#   legacy HLEDAC_HTTP3=1 alias for F260 callers);
# - Alt-Svc h3 advertisement -> host recorded in shared LRU;
# - next fetch to the same host passes http_version=HttpVersion.v3 to
#   ``fetch_via_curl_cffi`` (which already accepts the kwarg and omits
#   it on None — see transport/curl_cffi_fetch.py:209-214);
# - any error / missing module / disabled gate -> None and degrade
#   to HTTP/1.1 / HTTP/2 as before.
# ---------------------------------------------------------------------------
# Lazy import: http3_lane is part of the default transport package but
# uses lazy sub-imports (curl_cffi, aioquic) so import cost stays minimal.
from hledac.universal.transport.http3_lane import (  # type: ignore[import-not-found]  # noqa: E402
    http_version_for_curl_cffi as _h3_http_version_for_url,
)
from hledac.universal.transport.http3_lane import (  # noqa: E402
    record_from_curl_cffi_result as _h3_record_from_result_headers,
)


def _altsvc_extract_host(url: str, preclassified_host: str = "") -> str:
    """Return lowercased hostname from URL, or empty string on parse failure.

    F271: Rust url_ops.extract_host fast path with urllib.parse fallback.
    B1: When caller already classified the URL via _classify_url_cached,
    pass preclassified_host to skip the FFI entirely.
    """
    if preclassified_host:
        return preclassified_host
    try:
        _uops = url_ops
        if _uops is not None:
            return _uops.extract_host(url)
        _, host = _classify_url_cached(url)
        return host
    except Exception:
        return ""


def _altsvc_http_version_for(host: str) -> Any:
    """F260 compat shim — delegates to ``http3_lane`` by reconstructing
    a synthetic URL. Returns ``None`` when the gate is closed, when the
    host has no recorded h3 advertisement, when curl_cffi is missing,
    or when the M1 8GB RSS memory guard is over budget.
    """
    if not host:
        return None
    return _h3_http_version_for_url(f"https://{host}/")


def _altsvc_record_from_result(url: str, headers: Any) -> None:
    """F260 compat shim — delegates to ``http3_lane`` which owns the
    LRU cache, the parse, and the env gate.
    """
    try:
        _h3_record_from_result_headers(url, headers)
    except Exception:  # noqa: BLE001
        # Fail-soft: telemetry writes are best-effort; never fail fetch.
        pass
        # Cache update is best-effort; never fail the hot path.
        pass


# --- F261: bounded helper — try decode_response_bytes, fall back to _try_decode ---
def _try_decode_with_charset(
    body: bytes,
    *,
    http_charset: str | None = None,
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[str, bool, int]:
    """STORAGE-FIX-4 wiring: charset_normalizer chain with fail-soft fallback.

    Tries the bounded encoding chain from utils.encoding first; on any exception,
    falls back to the legacy _try_decode (UTF-8 → windows-1252 → latin-1 → UTF-8 replace).

    Returns (text, decode_replaced, decode_replacement_count) — same shape as _try_decode.
    """
    try:
        text = decode_response_bytes(
            body,
            http_charset=http_charset,
            max_bytes=max_bytes,
        )
        replacement_count = text.count("�")
        return (text, replacement_count > 0, replacement_count)
    except Exception as e:
        logger.debug("decode_response_bytes failed, falling back to _try_decode: %s", e)
        return _try_decode(body)


# ---------------------------------------------------------------------------
# P4: Tor + stealth constants
# ---------------------------------------------------------------------------
TOR_SOCKS_PROXY: Final[str] = os.environ.get("TOR_SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
I2P_SOCKS_PROXY: Final[str] = os.environ.get("I2P_PROXY_URL", "socks5://127.0.0.1:7654")
TOR_CIRCUIT_RENEWAL_REQUEST_COUNT: Final[int] = 10
TOR_STEALTH_TIMEOUT_SCALE: Final[float] = 2.0  # Tor requests need longer timeouts
JITTER_MIN_S: Final[float] = 0.1
JITTER_MAX_S: Final[float] = 0.5

# F-GLOBAL: Session state — all Tor/I2P globals encapsulated in _SessionManager.
# CRASH-RECOVERY: After a crash, _SESSION_MGR is always in a consistent state.
# Tor and I2P sessions track their own .closed flag; no orphaned state possible.


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
    """

    __slots__ = (
        "_tor_session",
        "_i2p_session",
        "_tor_request_count",
        "_tor_session_lock",
        "_i2p_session_lock",
        "_tor_session_locally_created",
        "_i2p_session_locally_created",
        "_injected_session_provider",
        "_session_source_telemetry",
        "_tor_circuit_renewal_count",
    )

    def __init__(self) -> None:
        # F4XX: httpx-only — no aiohttp union types
        self._tor_session: httpx.AsyncClient | None = None
        self._i2p_session: httpx.AsyncClient | None = None
        self._tor_request_count: int = 0
        self._tor_session_lock: asyncio.Lock = asyncio.Lock()
        self._i2p_session_lock: asyncio.Lock = asyncio.Lock()
        self._tor_session_locally_created: bool = False
        self._i2p_session_locally_created: bool = False
        # F4XX: httpx.AsyncClient only
        self._injected_session_provider: (
            tuple[
                httpx.AsyncClient | None,
                httpx.AsyncClient | None,
            ]
            | None
        ) = None
        self._session_source_telemetry: dict[str, str] = {
            "tor": "unavailable",
            "i2p": "unavailable",
        }
        # F272: Inline circuit counter (was _tor_request_count)
        self._tor_circuit_renewal_count: int = 0

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
        self._session_source_telemetry["tor"] = source

    def record_i2p_source(self, source: str) -> None:
        self._session_source_telemetry["i2p"] = source

    def get_telemetry(self) -> dict[str, str]:
        return dict(self._session_source_telemetry)

    def reset_for_winddown(self) -> None:
        """Reset session state at sprint winddown. Idempotent."""
        self._tor_session = None
        self._i2p_session = None
        self._tor_session_locally_created = False
        self._i2p_session_locally_created = False
        self._tor_request_count = 0
        self._session_source_telemetry = {"tor": "unavailable", "i2p": "unavailable"}

    def reset_for_testing(self) -> None:
        """Reset all session state for testing isolation.

        Closes any open sessions and returns to pristine factory state.
        Unlike reset_for_winddown, this also resets circuit counters.
        """
        import asyncio

        # F4XX: Close sessions using helper methods (httpx only)
        if not self._session_is_closed(self._tor_session):
            try:
                loop = asyncio.get_running_loop()
                loop.run_until_complete(self._session_aclose(self._tor_session))
            except RuntimeError:
                pass  # No running loop
        if not self._session_is_closed(self._i2p_session):
            try:
                loop = asyncio.get_running_loop()
                loop.run_until_complete(self._session_aclose(self._i2p_session))
            except RuntimeError:
                pass  # No running loop
        self._tor_session = None
        self._i2p_session = None
        self._tor_session_locally_created = False
        self._i2p_session_locally_created = False
        self._tor_request_count = 0
        self._injected_session_provider = None
        self._session_source_telemetry = {"tor": "unavailable", "i2p": "unavailable"}
        self._tor_circuit_renewal_count = 0

    def status_snapshot(self) -> dict:
        """Return a consistent status snapshot for diagnostics."""
        return {
            "tor_present": self._tor_session is not None,
            "tor_closed": self._session_is_closed(self._tor_session),
            "tor_locally_created": self._tor_session_locally_created,
            "i2p_present": self._i2p_session is not None,
            "i2p_closed": self._session_is_closed(self._i2p_session),
            "i2p_locally_created": self._i2p_session_locally_created,
            "injected_active": self._injected_session_provider is not None,
            "telemetry": dict(self._session_source_telemetry),
        }


# F-GLOBAL: Single module-level instance — replaces 11 globals
_SESSION_MGR: _SessionManager = _SessionManager()

# ISSUE-007: Global singleton httpx.AsyncClient for connection reuse.
# - 200 total connections, 100 per host
# - 5-min DNS cache
# - 30s total timeout, 10s connect timeout
# httpx replaces aiohttp: better HTTP/2 support, lower memory footprint
_global_httpx_session: httpx.AsyncClient | None = None
_global_httpx_lock: asyncio.Lock = asyncio.Lock()


async def get_httpx_session() -> httpx.AsyncClient:
    """ISSUE-007: Get or create a shared httpx.AsyncClient singleton.

    Replaces aiohttp.ClientSession with httpx (HTTP/2, lower memory, better perf).

    Returns:
        Shared httpx.AsyncClient with 200-connection pool, 100-per-host limit.
    """
    import httpx

    global _global_httpx_session
    async with _global_httpx_lock:
        if _global_httpx_session is None or _global_httpx_session.is_closed:
            limits = httpx.Limits(
                max_connections=200,
                max_keepalive_connections=100,
            )
            timeout = httpx.Timeout(
                connect=10.0,
                read=30.0,
                write=10.0,
                pool=10.0,
            )
            _global_httpx_session = httpx.AsyncClient(
                limits=limits,
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                trust_env=False,
            )
    return _global_httpx_session


# ISSUE-007: Backward-compat alias — callers migrate to get_httpx_session()
async def get_aiohttp_session() -> httpx.AsyncClient:
    """ISSUE-007 deprecated: Use get_httpx_session() instead."""
    return await get_httpx_session()


async def close_httpx_session() -> None:
    """ISSUE-007: Close the global httpx session if open. Idempotent."""
    global _global_httpx_session
    async with _global_httpx_lock:
        if _global_httpx_session is not None and not _global_httpx_session.is_closed:
            await _global_httpx_session.aclose()
        _global_httpx_session = None


# ISSUE-007: Backward-compat alias
async def close_aiohttp_session() -> None:
    """ISSUE-007 deprecated: Use close_httpx_session() instead."""
    await close_httpx_session()


# F206AT: Public fetcher pool authority verdict.
# Tor and I2P sessions are LOCAL FALLBACK pools managed directly by public_fetcher.
# They are NOT coordinated through FetchCoordinator transport policy.
# When a canonical transport provider is injected, it supersedes these local pools.
PUBLIC_FETCHER_POOL_AUTHORITY: Final[str] = "local_fallback_until_transport_unified"


def inject_session_provider(
    tor_session: httpx.AsyncClient | None,
    i2p_session: httpx.AsyncClient | None,
) -> None:
    """ISSUE-007: Inject canonical session provider for Tor/I2P pools.

    When injected with non-None sessions, the provided sessions are used instead of
    local _tor_session/_i2p_session. This allows FetchCoordinator or transport layer
    to own the canonical session lifecycle.

    Calling with (None, None) resets to local-only mode — the seam is deactivated.

    Args:
        tor_session: Canonical Tor httpx session, or None to use local fallback.
        i2p_session: Canonical I2P httpx session, or None to use local fallback.
    """
    # Deactivate seam if both are None — reset to local pools
    if tor_session is None and i2p_session is None:
        _SESSION_MGR._injected_session_provider = None
    else:
        _SESSION_MGR._injected_session_provider = (tor_session, i2p_session)
        # Injected provider is active — local sessions should not be closed by us
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
    result["transport_policy_bypassed"] = "true" if _SESSION_MGR._injected_session_provider is None else "false"
    result["fallback_reason"] = (
        "injected_provider_available"
        if _SESSION_MGR._injected_session_provider is not None
        else "local_pool_until_transport_unified"
    )
    return result


# P7: Camoufox singleton lock — max 1 instance across entire fetcher
_CAMOUFOX_LOCK: asyncio.Lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Public API — single entry point
# ---------------------------------------------------------------------------

DEFAULT_UA: Final[str] = "Mozilla/5.0 (compatible; research-bot/1.0; +passive-public-fetch)"

# F229: Realistic browser User-Agent pool for header rotation
# Covers Chrome, Firefox, Safari, Edge across desktop and mobile.
_BROWSER_UA_POOL: tuple[str, ...] = (
    # Chrome 124 stable — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 124 stable — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",  # noqa: E501
    # Chrome 124 stable — Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome 120 — Android 13
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.210 Mobile Safari/537.36",  # noqa: E501
    # Firefox 133 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Firefox 133 — macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Firefox 133 — Linux
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    # Safari 17 — macOS Sonoma
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",  # noqa: E501
    # Safari 17 — iOS 17 iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",  # noqa: E501
    # Edge 124 — Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",  # noqa: E501
)

# F229: Realistic Accept-Language pool for header rotation
# Covers en-US (most common), en-GB, de-DE, fr-FR, ja-JP, zh-CN.
_ACCEPT_LANGUAGE_POOL: tuple[str, ...] = (
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8",
    "en-US,en;q=0.9,de;q=0.8",
    "en-US,en;q=0.9,fr;q=0.8",
    "en-US,en;q=0.9,es;q=0.8",
    "en-US,en;q=0.9,ja;q=0.8",
    "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    "de-DE,de;q=0.9,en;q=0.8",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "ja-JP,ja;q=0.9,en;q=0.8",
    "en-US,en;q=0.9",
    "en-AU,en;q=0.9",
    "en-CA,en;q=0.9",
    "en-IE,en;q=0.9",
    "en-NZ,en;q=0.9",
)


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
    headers["Accept-Encoding"] = build_accept_encoding_header()
    return headers


MAX_BYTES_DEFAULT: Final[int] = 2_000_000
MAX_BYTES_HARD: Final[int] = 10_000_000

# ---------------------------------------------------------------------------
# Typed result DTO
# ---------------------------------------------------------------------------

# Transport counters — per-fetch, M1-safe slots-based dataclass
_MAX_COUNT: int = 999_999


class TransportCounters:
    """Lightweight per-fetch transport counter bundle (M1-safe __slots__).

    Bounded ints — counters saturate at MAX_COUNT rather than growing unbounded.
    Not exposed in public API — aggregated by sprint coordinator from FetchResult.
    """

    __slots__ = (
        "aiohttp_count",
        "httpx_h2_count",
        "curl_cffi_count",
        "curl_cffi_tor_count",
        "curl_cffi_tor_fallback_count",
        "tor_httpx_socks_count",
        "i2p_httpx_socks_count",
        "js_renderer_count",
        "fallback_count",
        "curl_cffi_fallback_to_aiohttp_count",
        "httpx_h2_fallback_to_aiohttp_count",
        # F214Z: Static hydration telemetry (bounded, M1-safe)
        "static_hydration_attempted",
        "static_hydration_sufficient",
        "static_hydration_insufficient",
        # F214AC: macOS WebKit renderer counter (backward-compatible, bounded)
        "macos_webkit_count",
    )

    def __init__(
        self,
        aiohttp_count: int = 0,
        httpx_h2_count: int = 0,
        curl_cffi_count: int = 0,
        curl_cffi_tor_count: int = 0,
        curl_cffi_tor_fallback_count: int = 0,
        tor_httpx_socks_count: int = 0,
        i2p_httpx_socks_count: int = 0,
        js_renderer_count: int = 0,
        fallback_count: int = 0,
        curl_cffi_fallback_to_aiohttp_count: int = 0,
        httpx_h2_fallback_to_aiohttp_count: int = 0,
        static_hydration_attempted: int = 0,
        static_hydration_sufficient: int = 0,
        static_hydration_insufficient: int = 0,
        # F214AC: macOS WebKit renderer counter
        macos_webkit_count: int = 0,
    ) -> None:
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
        # F214AC: macOS WebKit renderer counter
        self.macos_webkit_count = min(macos_webkit_count, _MAX_COUNT)


class FetchResult(msgspec.Struct, frozen=True):
    """Frozen msgspec result — no mutations after construction.

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
    fetched_bytes: int  # actual bytes read
    declared_length: int  # Content-Length header value, -1 if absent
    elapsed_ms: float
    # Added in F266A — Zero-Copy body preservation for Arrow IPC and forensic replay
    # Raw bytes preserved for binary content (PDF, images) and Arrow zero-copy paths.
    # Bounded by body_limiter max_bytes (2MB). None when body was never read.
    body: bytes | None = None
    error: str | None = None
    # Added in F164A — feed ingress hardening
    xml_recovered: bool = False  # True: body was XML-ish but Content-Type was wrong, body is now text
    xml_source_hint: bool = False  # F178E: True when xml_recovered=True — downstream can detect XML origin
    decode_replaced: bool = False  # True: UTF-8 decode used replacement chars
    decode_replacement_count: int = 0  # F178E: actual count of U+FFFD replacement chars inserted
    body_read_error: bool = False  # True: headers were OK but body stream failed mid-read
    # Added in F169B — access-path truth hardening
    redirected: bool = False  # True: final_url != url (explicit redirect signal)
    redirect_target: str | None = None  # redirect destination (set only when redirected=True)
    failure_stage: str | None = None  # validation | connection | tls | http | body | size
    network_error_kind: str | None = None  # dns_error | connect_error | tls_error | timeout
    # Added in F206K — Transport Capability Layer 2026 telemetry
    selected_transport: str | None = None  # httpx_h2 | httpx_socks | curl_cffi | stealth | js
    http_version: str | None = None  # h2 | http/1.1 | h2c (detected post-response)
    transport_policy_reason: str | None = (
        None  # api_like | darknet_url | stealth_required | js_required | clearnet_default | httpx_h2_disabled_env | httpx_h2_disabled | httpx_h2_fallback | freenet_not_httpx_supported | explicit_stealth | status_403_or_429 | protection_detected | default_httpx_h2  # noqa: E501
    )
    transport_fallback_reason: str | None = None  # set when fallback occurred (curl_cffi_failed:..., httpx_h2_fallback)
    # Added in F206N — Transport Telemetry Counters
    transport_counters: TransportCounters | None = None
    # Added in F207F — PUBLIC Yield: why JS renderer was skipped
    js_renderer_skipped_reason: str | None = None  # xml_or_feed_url | xml_recovered | browser_unavailable
    # Added in F214Z — Static Hydration Telemetry
    hydration_score: float | None = None  # 0.0–1.0, set when static hydration was attempted
    hydration_sources: tuple[str, ...] = ()  # e.g. ("next_data", "json_ld")
    # Added in F229 — TLS metadata and server header
    tls_cert_san: tuple[str, ...] = ()  # Subject Alternative Names from server cert
    tls_cert_issuer: str | None = None  # Issuer CN from server cert
    tls_cert_sha256: str | None = None  # SHA-256 fingerprint of server cert
    server_header: str | None = None  # Server response header value
    # Added in F274 — Pattern matches from HTML scan (carried from process_html_payload)
    matched_patterns: tuple[str, ...] = ()  # (label, pattern, value) tuples from match_text scan


# ---------------------------------------------------------------------------
# Content-type whitelist (text-ish only)
# ---------------------------------------------------------------------------

ACCEPTED_CONTENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "text/html",
        "text/plain",
        "text/xml",
        "application/xhtml+xml",
        "application/xml",
        "application/rss+xml",
        "application/atom+xml",
    }
)


# ---------------------------------------------------------------------------
# URL validation
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> str | None:
    """
    Validate URL is http/https and well-formed.
    Returns None on success, error string on failure.

    F271: Rust url_ops.classify_url fast path with urllib.parse fallback.
    classify_url returns (kind, host) where kind ∈
    {"clearnet","onion","i2p","freenet","empty","malformed"}.
    Rust path is used when the module loads; ImportError or runtime
    failure falls through to the unchanged Python branch below.
    """
    if not url or not isinstance(url, str):
        return "url_empty"
    url = url.strip()
    if not url:
        return "url_empty"
    _uops = url_ops
    if _uops is not None:
        try:
            # F320: Use _classify_url_cached — shares cache with
            # _fetch_with_nodriver / _fetch_with_curl_cffi so the same URL
            # classified here is already cached when those call sites run
            # _batch_classify_url_cached([url]) and avoid a 2nd Rust FFI call.
            kind, host = _classify_url_cached(url)
            if kind == "empty":
                return "url_empty"
            if kind == "malformed":
                return "url_malformed"
            if not host:
                return "url_no_netloc"
            # Rust confirmed parse succeeded — derive scheme from URL prefix
            # (cheaper than a second urlparse call; also keeps the http(s)
            # gate without depending on parse correctness of unsupported
            # schemes like ftp/gopher, which the Rust extension still
            # parses and returns as "clearnet" with the bare host).
            scheme_idx = url.find("://")
            if scheme_idx == -1:
                return "url_malformed"
            scheme = url[:scheme_idx].lower()
            if scheme not in ("http", "https"):
                return f"url_unsupported_scheme:{scheme}"
            return None
        except Exception:  # noqa: BLE001
            # Rust path raised — fall through to Python fallback.
            pass
    # F320: Use _python_classify_url (pure-Python equivalent of Rust
    # classify_url) instead of re-parsing with urlparse. Avoids 2nd parse
    # when Rust was already tried at lines 1040-1058.
    _kind, _host = _python_classify_url(url)
    if _kind == "empty":
        return "url_empty"
    if _kind == "malformed":
        return "url_malformed"
    if not _host:
        return "url_no_netloc"
    # Derive scheme from URL prefix (same logic as Rust path above)
    scheme_idx = url.find("://")
    if scheme_idx == -1:
        return "url_malformed"
    scheme = url[:scheme_idx].lower()
    if scheme not in ("http", "https"):
        return f"url_unsupported_scheme:{scheme}"
    return None


# ---------------------------------------------------------------------------
# Retry constants — bounded, M1-safe
# ---------------------------------------------------------------------------

MAX_RETRIES: Final[int] = 2  # two retries for 429/5xx; bounded, no infinite loops
_RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 502, 503, 504, 520})


def _is_retryable_status(status_code: int) -> bool:
    return status_code in _RETRYABLE_STATUS_CODES


def _extract_retry_after(headers) -> float | None:
    """Parse Retry-After header, return seconds or None."""
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if ra is None:
        return None
    try:
        return float(ra)
    except (ValueError, TypeError):
        return None


def _compute_backoff_seconds(
    retry_after: float | None,
    attempt: int,
    *,
    jitter: bool = True,
    _prev_sleep: float = 0.0,
) -> float:
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
        base = min(retry_after, 60.0)  # cap at 60 s to bound pause
    else:
        base = min(2.0 ** (attempt + 1), 8.0)  # 2, 4, 8, capped at 8 s
    if jitter:
        # Decorrelated jitter (AWS architecture blog)
        import random

        return min(8.0, random.uniform(0.0, max(base, _prev_sleep) * 3.0))
    return base


def _build_retry_error(status_code: int, retry_after: float | None) -> str:
    """Build retry error string with : separator between code and details.

    Adapter uses .split(":", 2) — first two parts are always prefix+code,
    any additional colons in the message body are preserved in part[2].
    """
    parts = [f"retryable:{status_code}"]
    if retry_after is not None:
        parts.append(f"retry_after={retry_after:.1f}s")
    else:
        parts.append("backoff=exp")
    return "|".join(parts)


# ---------------------------------------------------------------------------
# F169B: Access-path truth helpers — derive-only, no new transport
# ---------------------------------------------------------------------------


def _extract_tls_metadata_from_response(resp) -> dict:
    """
    Extract TLS certificate metadata and Server header from an HTTP response.
    For aiohttp response: resp is aiohttp.ClientResponse
    For httpx response: resp is httpx.Response

    Architecture (Issue B5):
        - Python pre-fetches raw SSL data in one getattr call (no fallback chain)
        - Python parses dict form of getpeercert() -> san_entries + issuer_org
        - Rust does SAN cap (20) + issuer cap (200) + SHA-256 in a single call
        - Server header: plain Python (no Rust needed)
    Memory bounds: all collections are bounded, fail-safe throughout.
    """
    result = {
        "tls_cert_san": (),
        "tls_cert_issuer": None,
        "tls_cert_sha256": None,
        "server_header": None,
    }
    # Server header -- available on all responses, no SSL needed
    try:
        server = resp.headers.get("Server") or resp.headers.get("server")
        if server:
            result["server_header"] = server[:200]
    except Exception:  # noqa: BLE001
        pass

    # --- Issue B5: single-path SSL fetch, Rust metadata extraction ---
    try:
        # Get SSL object -- single getattr, no fallback chain
        ssl_obj = getattr(resp, "connection", None)
        if ssl_obj is None:
            ssl_obj = getattr(resp, "_ssl", None)
        if ssl_obj is None:
            try:
                transport = getattr(resp, "transport", None)
                if transport is not None:
                    ssl_obj = transport.get_extra_info("ssl_object")
            except Exception:  # noqa: BLE001
                pass

        if ssl_obj is None:
            return result

        # getpeercert(dict form) -- for SANs + issuer extraction by Rust
        cert_dict: dict | None = None
        try:
            cert_dict = ssl_obj.getpeercert()
        except Exception:  # noqa: BLE001
            pass

        # getpeercert(binary form) -- for SHA-256 by Rust
        der_bytes: bytes | None = None
        try:
            der_bytes = ssl_obj.getpeercert(binary_form=True)
        except Exception:  # noqa: BLE001
            pass

        # Pre-extract issuer org from Python (avoiding Rust-side nested iteration)
        issuer_org: str | None = None
        san_entries: list[tuple[int, str]] = []
        if cert_dict:
            # SANs: convert all values to str for Rust
            san_list = cert_dict.get("subjectAltName", [])
            for typ, val in san_list:
                if isinstance(val, (str, bytes)):
                    san_entries.append((typ, str(val)))
            # Issuer org: extract once in Python
            subject = cert_dict.get("subject", ())
            for rdn in subject:
                for k, v in rdn:
                    if k == "organizationName":
                        issuer_org = str(v)
                        break
                if issuer_org:
                    break

        # Single Rust call: SAN cap + issuer cap + SHA-256
        sans, issuer, sha256 = _rust_backend.tls.extract_tls_metadata(san_entries, issuer_org, der_bytes)
        result["tls_cert_san"] = tuple(sans)
        result["tls_cert_issuer"] = issuer
        result["tls_cert_sha256"] = sha256

    except Exception:  # noqa: BLE001
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

    # URL validation errors — pre-connection, network_error_kind stays None
    if error.startswith("url_"):
        return ("validation", None)

    # Timeout — explicit in code, no ambiguity
    if error == "timeout":
        return ("connection", "timeout")

    # Size cap — structured, no network error kind
    if error == "size_cap_exceeded":
        return ("size", None)

    # content_type_rejected — HTTP response but content unacceptable
    if error.startswith("content_type_rejected:"):
        return ("http", None)

    # retryable status codes — HTTP-level
    if error.startswith("retryable:"):
        return ("http", None)

    # Generic fetch_error; prefix — connection/tls level
    if error.startswith("fetch_error;"):
        # Format: "fetch_error;ExceptionType;message"
        parts = error.split(";", 2)
        exc_type = parts[1] if len(parts) > 1 else ""

        # TLS variants
        if "SSL" in exc_type or "TLS" in exc_type or "Certificate" in exc_type:
            return ("tls", "tls_error")
        # DNS
        if "DNS" in exc_type or "Resolver" in exc_type:
            return ("connection", "dns_error")
        # Connection (refused, reset, connect timeout)
        if "Connect" in exc_type or "Connection" in exc_type or "Network" in exc_type:
            return ("connection", "connect_error")
        # Default for any other fetch_error: connection-level unknown
        return ("connection", "connect_error")

    # Unknown error format — body-level if we got here without a clear stage
    return ("body", None)


# Sprint F206AC: Fetch error taxonomy for public_branch_verdict telemetry
_FETCH_ERROR_TAXONOMY: dict[str, str] = {
    "dns_error": "dns_error",
    "connect_error": "connect_error",
    "tls_error": "tls_error",
    "timeout": "read_timeout",
    "content_type_rejected:": "content_type_rejected",
    "fetch_text_none_or_empty": "body_empty",
    "fetch_timeout_after_": "connect_timeout",
    "fetch_exception: asyncio.TimeoutError": "connect_timeout",
    "fetch_exception: TimeoutError": "read_timeout",
    "fetch_exception: ClientConnectorError": "connect_error",
    "fetch_exception: ClientSSLError": "tls_error",
    "fetch_exception: ClientProxyError": "proxy_error",
    "fetch_exception: ClientConnectorCertificateError": "tls_error",
    "circuit_breaker": "circuit_breaker_blocked",
    "resource_governor": "resource_governor_blocked",
}


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
    # ---- Handle FetchResult objects ----------------------------------------
    if hasattr(result_or_error, "status_code"):
        result = result_or_error
        # Success path
        if result.error is None and result.status_code == 200 and result.text:
            # Check for body_empty (success but no text)
            if not result.text.strip():
                return "body_empty"
            return "none"
        # Error path from FetchResult
        error_str = result.error or ""
        status_code = result.status_code or 0
        try:
            failure_stage = result.failure_stage or ""
        except AttributeError:
            failure_stage = ""
        try:
            network_kind = result.network_error_kind or ""
        except AttributeError:
            network_kind = ""

        # CancelledError — re-raise
        if "CancelledError" in error_str:
            import asyncio

            raise asyncio.CancelledError("fetch cancelled")

        # HTTP status codes (only when we got a response)
        if status_code == 403:
            return "http_403"
        if status_code == 404:
            return "http_404"
        if status_code == 429:
            return "http_429"
        if 500 <= status_code < 600:
            return "http_5xx"

        # Structural failures from failure_stage / network_error_kind
        if failure_stage == "validation":
            return "unknown_fetch_error"
        if failure_stage == "tls" or network_kind == "tls_error":
            return "tls_error"
        if network_kind == "dns_error":
            return "dns_error"
        if network_kind == "connect_error":
            return "connect_error"
        if network_kind == "timeout":
            return "read_timeout"
        if failure_stage == "http":
            if "content_type_rejected" in error_str:
                return "content_type_rejected"
            return "unknown_fetch_error"
        if failure_stage == "size":
            return "max_bytes_exceeded"

        # Circuit/resource blocks
        if "circuit_breaker" in error_str:
            return "circuit_breaker_blocked"
        if "resource_governor" in error_str:
            return "resource_governor_blocked"

        # Exception-type-based classification
        for prefix, category in (
            ("fetch_exception: asyncio.TimeoutError", "connect_timeout"),
            ("fetch_exception: TimeoutError", "read_timeout"),
            ("fetch_exception: ClientConnectorError", "connect_error"),
            ("fetch_exception: ClientSSLError", "tls_error"),
            ("fetch_exception: ClientProxyError", "proxy_error"),
            ("fetch_exception: ClientConnectorCertificateError", "tls_error"),
            ("fetch_timeout_after_", "connect_timeout"),
            ("fetch_text_none_or_empty", "body_empty"),
            ("content_type_rejected:", "content_type_rejected"),
        ):
            if error_str.startswith(prefix):
                return category

        if error_str:
            return "unknown_fetch_error"
        return "none"

    # ---- Handle plain error strings ---------------------------------------
    error_str = str(result_or_error) if result_or_error is not None else ""

    # CancelledError — re-raise
    if "CancelledError" in error_str:
        import asyncio

        raise asyncio.CancelledError("fetch cancelled")

    if not error_str:
        return "none"

    for prefix, category in _FETCH_ERROR_TAXONOMY.items():
        if error_str.startswith(prefix):
            return category

    return "unknown_fetch_error"


# ---------------------------------------------------------------------------
# XML-ish body sniffing helper — bounded, fail-safe
# ---------------------------------------------------------------------------

_XML_MARKER = b"<?xml"
_XML_TAG_RE = re.compile(rb"^\s*<[a-zA-Z]", re.IGNORECASE)


def _looks_xmlish(body: bytes) -> bool:
    """Return True if body starts like XML (<?xml or <tag).

    Strips leading ASCII whitespace so servers that prepend newlines
    before the XML declaration are correctly identified.
    """
    stripped = body.lstrip()
    if stripped.startswith(_XML_MARKER):
        return True
    return bool(_XML_TAG_RE.match(stripped))


# ---------------------------------------------------------------------------
# Decode helper — fail-soft, truth-bearing
# ---------------------------------------------------------------------------


def _try_decode(body: bytes) -> tuple[str, bool, int]:
    """Decode bytes to str, return (text, replaced_bool, replacement_count).

    F178E: replacement_count is actual U+FFFD count (not just bool).
    Charset fallback: try UTF-8 → Windows-1252 → Latin-1 before replace.

    replaced_bool=True when UTF-8 decoder used replacement chars (U+FFFD).
    This tells the adapter that the body was garbled, not truly empty.
    """
    # Try strict UTF-8 first
    try:
        text = body.decode("utf-8", errors="strict")
        return (text, False, 0)
    except UnicodeDecodeError:
        pass

    # F178E: Windows-1252 fallback (common in legacy Western feeds)
    try:
        text = body.decode("windows-1252", errors="strict")
        return (text, False, 0)
    except (UnicodeDecodeError, LookupError):
        pass

    # Latin-1 fallback (always succeeds — byte 0-255 maps 1:1)
    try:
        text = body.decode("latin-1", errors="strict")
        return (text, True, 0)  # lossy but usable
    except (UnicodeDecodeError, LookupError):
        pass

    # Final fallback: UTF-8 replace mode — count actual replacements
    text = body.decode("utf-8", errors="replace")
    count = text.count("\ufffd")
    return (text, True, count)


# ---------------------------------------------------------------------------
# P4: Tor session helpers — SOCKS5 proxy via aiohttp_socks
# ---------------------------------------------------------------------------


def _is_onion_url(url: str) -> bool:
    """Detect if URL targets a .onion darknet address.

    F271: Delegates to _classify_url_cached (Rust fast path + Python fallback).
    Single shared parse — cheaper than 3 separate urlparse calls.
    """
    try:
        kind, _ = _classify_url_cached(url)
        return kind == "onion"
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("URL parse error in _is_onion_url for %s: %s", url, e)
        return False


def _is_i2p_url(url: str) -> bool:
    """P10: Detect if URL targets an I2P address (.i2p or .b32.i2p).

    F271: Delegates to _classify_url_cached.
    """
    try:
        kind, _ = _classify_url_cached(url)
        return kind == "i2p"
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("URL parse error in _is_i2p_url for %s: %s", url, e)
        return False


def _is_freenet_url(url: str) -> bool:
    """P10: Detect if URL targets a Freenet address (.freenet or Hyphanet).

    F271: Delegates to _classify_url_cached.
    """
    try:
        kind, _ = _classify_url_cached(url)
        return kind == "freenet"
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("URL parse error in _is_freenet_url for %s: %s", url, e)
        return False


async def _get_tor_session():
    """Get Tor session for .onion URL fetches.

    F260 JA3 unification: prefers curl_cffi wrapper (chrome_120 JA3, no
    Python TLS fingerprint leak). Falls back to aiohttp_socks when curl_cffi
    is unavailable. Telemetry records the chosen path.

    F206AT: If _SESSION_MGR._injected_session_provider is set, returns the injected
    aiohttp session verbatim and records source as 'injected' — the wrapper
    path is skipped to preserve back-compat with tests using fake aiohttp.
    """
    # F206AT: Injected provider short-circuits — return as-is
    if _SESSION_MGR._injected_session_provider is not None:
        injected_tor, _ = _SESSION_MGR._injected_session_provider
        # ISSUE-080: Use helper for closed check on Union type
        if not _SESSION_MGR._session_is_closed(injected_tor):
            _SESSION_MGR.record_tor_source("injected")
            return injected_tor
    # F260: Prefer curl_cffi — JA3 impersonation through Tor SOCKS5H
    _cc_available, _cc_reason = is_curl_cffi_available()
    if _cc_available:
        _SESSION_MGR.record_tor_source("curl_cffi")
        return _TorCurlCffiWrapper()
    # ISSUE-080: Use httpx_socks_client (httpx-socks) instead of aiohttp_socks.
    # httpx-socks provides HTTP/2 support and is the canonical SOCKS5 client.
    async with _SESSION_MGR._tor_session_lock:
        if not _SESSION_MGR.tor_is_healthy():
            _SESSION_MGR._tor_session = await httpx_socks_client(TOR_SOCKS_PROXY, rdns=True)
            _SESSION_MGR._tor_session_locally_created = True
    _SESSION_MGR.record_tor_source("local_tor")
    return _SESSION_MGR._tor_session


async def _get_i2p_session():
    """
    P10: Get I2P session for .i2p/.b32.i2p URL fetches.

    F260 JA3 unification: prefers curl_cffi wrapper (chrome_120 JA3). I2P
    has no NEWNYM equivalent so circuit rotation is intentionally absent.
    Falls back to aiohttp_socks when curl_cffi is unavailable.
    """
    # F206AT: Injected provider short-circuits
    if _SESSION_MGR._injected_session_provider is not None:
        _, injected_i2p = _SESSION_MGR._injected_session_provider
        # ISSUE-080: Use helper for closed check on Union type
        if not _SESSION_MGR._session_is_closed(injected_i2p):
            _SESSION_MGR.record_i2p_source("injected")
            return injected_i2p
    # F260: Prefer curl_cffi
    _cc_available, _cc_reason = is_curl_cffi_available()
    if _cc_available:
        _SESSION_MGR.record_i2p_source("curl_cffi")
        return _I2pCurlCffiWrapper()
    # ISSUE-080: Use httpx_socks_client (httpx-socks) instead of aiohttp_socks.
    # httpx-socks provides HTTP/2 support and is the canonical SOCKS5 client.
    async with _SESSION_MGR._i2p_session_lock:
        if not _SESSION_MGR.i2p_is_healthy():
            _SESSION_MGR._i2p_session = await httpx_socks_client(I2P_SOCKS_PROXY, rdns=True)
            _SESSION_MGR._i2p_session_locally_created = True
    _SESSION_MGR.record_i2p_source("local_i2p")
    return _SESSION_MGR._i2p_session


# ---------------------------------------------------------------------------
# F260: curl_cffi wrapper classes — aiohttp-like API over JA3-impersonating fetcher
# ---------------------------------------------------------------------------


class _CurlCffiResponseAdapter:
    """Minimal aiohttp-compatible response adapter for curl_cffi fetch results.

    Provides the surface that async_fetch_public_text() needs when iterating
    over a body via `async with session.get(url) as resp:`. We do not aim
    for full aiohttp.ClientResponse parity — only the fields used by the
    aiohttp body-read loop (.url, .status, .headers, .iter_chunked()).
    """

    __slots__ = ("url", "status", "headers", "content_type", "_content")

    def __init__(
        self,
        url: str,
        status: int,
        headers: dict[str, str] | None,
        content: bytes,
    ) -> None:
        self.url = url
        self.status = status
        self.headers: dict[str, str] = dict(headers) if headers else {}
        ct = self.headers.get("Content-Type") or self.headers.get("content-type") or ""
        self.content_type = ct
        self._content = content

    async def read(self) -> bytes:
        return self._content

    async def text(self, encoding: str = "utf-8", errors: str = "strict") -> str:
        return self._content.decode(encoding, errors=errors)

    async def iter_chunked(self, n: int):
        """Yield body in n-byte chunks (matches aiohttp's iter_chunked API)."""
        data = self._content
        for i in range(0, len(data), n):
            yield data[i : i + n]


class _CurlCffiGetContextManager:
    """Async context manager wrapping an adapter-yielding object.

    Mirrors aiohttp's `session.get(...)` return value: a context manager
    you can `async with` to get the response. The wrapped object must
    implement `__aenter__` returning a _CurlCffiResponseAdapter.
    """

    def __init__(self, future: Any) -> None:
        self._future = future

    async def __aenter__(self):
        return await self._future.__aenter__()  # type: ignore[attr-defined,union-attr]

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None


class _TorCurlCffiFetchFuture:
    """Lazy adapter: defer the fetch until __aenter__.

    Wraps fetch_via_tor_curl_cffi() and exposes aiohttp-like response
    surface on completion. Created by _TorCurlCffiWrapper.get().
    """

    __slots__ = ("_url", "_kwargs", "_fetched", "_err", "_adapter")

    def __init__(self, url: str, kwargs: dict) -> None:
        self._url = url
        self._kwargs = kwargs
        self._fetched = False
        self._err: str | None = None
        self._adapter: _CurlCffiResponseAdapter | None = None

    async def __aenter__(self) -> _CurlCffiResponseAdapter:
        if not self._fetched:
            try:
                result = await fetch_via_tor_curl_cffi(
                    url=self._url,
                    headers=self._kwargs.get("headers"),
                    timeout_s=self._kwargs.get("timeout_s", 35.0),
                    max_bytes=self._kwargs.get("max_bytes", 10 * 1024 * 1024),
                    profile="chrome110",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fetched = True
                self._err = f"tor_curl_cffi_failed:{type(exc).__name__}:{exc}"
                raise httpx.HTTPError(self._err) from exc
            self._fetched = True
            if not result.get("success", False):
                self._err = f"tor_curl_cffi_failed:{result.get('error', 'unknown')}"
                raise httpx.HTTPError(self._err)
            self._adapter = _CurlCffiResponseAdapter(
                url=result.get("final_url", self._url),
                status=int(result.get("status_code", 0)),
                headers=result.get("headers"),
                content=result.get("content", b"") or b"",
            )
        if self._adapter is None:
            raise httpx.HTTPError("tor_curl_cffi_failed:no_adapter")
        return self._adapter


class _I2pCurlCffiFetchFuture:
    """Lazy adapter for I2P curl_cffi fetch. No circuit rotation (I2P invariant)."""

    __slots__ = ("_url", "_kwargs", "_fetched", "_adapter")

    def __init__(self, url: str, kwargs: dict) -> None:
        self._url = url
        self._kwargs = kwargs
        self._fetched = False
        self._adapter: _CurlCffiResponseAdapter | None = None

    async def __aenter__(self) -> _CurlCffiResponseAdapter:
        if not self._fetched:
            try:
                result = await fetch_via_i2p_curl_cffi(
                    url=self._url,
                    headers=self._kwargs.get("headers"),
                    timeout_s=self._kwargs.get("timeout_s", 35.0),
                    max_bytes=self._kwargs.get("max_bytes", 10 * 1024 * 1024),
                    profile="chrome110",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._fetched = True
                raise httpx.HTTPError(f"i2p_curl_cffi_failed:{type(exc).__name__}:{exc}") from exc
            self._fetched = True
            if not result.get("success", False):
                raise httpx.HTTPError(f"i2p_curl_cffi_failed:{result.get('error', 'unknown')}")
            self._adapter = _CurlCffiResponseAdapter(
                url=result.get("final_url", self._url),
                status=int(result.get("status_code", 0)),
                headers=result.get("headers"),
                content=result.get("content", b"") or b"",
            )
        if self._adapter is None:
            raise httpx.HTTPError("i2p_curl_cffi_failed:no_adapter")
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
        # No-op: curl_cffi session lifetime is owned by curl_cffi_runtime cache.
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
            ctrl.signal(stem.control.Signal.NEWNYM)  # type: ignore[attr-defined]
            logger.debug("Tor circuit renewed via NEWNYM signal")
            return True
    except Exception as e:
        logger.warning("Tor circuit renewal failed: {e}", e=e)
        return False


async def _maybe_renew_tor_circuit() -> None:
    """Renew Tor circuit if request count threshold reached."""
    # F310-FIX: protect counter with lock to prevent race conditions
    async with _SESSION_MGR._tor_session_lock:
        _SESSION_MGR._tor_request_count += 1
        if _SESSION_MGR._tor_request_count >= TOR_CIRCUIT_RENEWAL_REQUEST_COUNT:
            _SESSION_MGR._tor_request_count = 0
    if _SESSION_MGR._tor_request_count == 0:  # renewed above, now actually renew
        await _renew_tor_circuit()


async def _jitter_delay() -> None:
    """Apply random jitter before request (Tor/stealth anti-correlation)."""
    import random

    await asyncio.sleep(random.uniform(JITTER_MIN_S, JITTER_MAX_S))


async def _close_tor_session() -> None:
    """Close the Tor session (for cleanup)."""
    # ISSUE-080: Use helper methods for Union type support
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._tor_session) and _SESSION_MGR._tor_session_locally_created:
        await _SESSION_MGR._session_aclose(_SESSION_MGR._tor_session)
    _SESSION_MGR._tor_session = None
    _SESSION_MGR._tor_session_locally_created = False


def _close_tor_session_sync() -> None:
    """Sync wrapper for Tor session cleanup via atexit.

    F219D: Safe teardown that avoids calling run_until_complete on a running loop.
    Uses a fresh event loop in a daemon thread when no running loop exists.
    """
    import threading

    # Nothing to do if session is None or already closed
    if not _SESSION_MGR.tor_is_healthy():
        return

    # Only close locally-created sessions — never touch injected providers
    if not _SESSION_MGR._tor_session_locally_created:
        _SESSION_MGR._tor_session = None
        return

    try:
        _loop = asyncio.get_running_loop()

        # A loop is running — spawn a daemon thread to run the async cleanup
        # FIX F196A: use run_until_complete instead of asyncio.run to avoid M1 crash
        def _run_closer() -> None:
            session = _SESSION_MGR._tor_session  # capture local ref to avoid race
            if session is None:
                return
            try:
                _loop.run_until_complete(session.close())  # type: ignore[unreachable]
            except Exception as e:
                logger.warning("Error closing Tor session in thread: %s", e)
            finally:
                _SESSION_MGR._tor_session = None

        _t = threading.Thread(target=_run_closer, daemon=True)
        _t.start()
        # Don't wait — daemon thread will complete asynchronously
    except RuntimeError:
        # No running loop — use a fresh event loop synchronously
        session = _SESSION_MGR._tor_session
        if session is None:
            _SESSION_MGR._tor_session = None
            return
        try:
            _new_loop = asyncio.new_event_loop()
            # ISSUE-080: Use helper for close on Union type
            _new_loop.run_until_complete(_SESSION_MGR._session_aclose(session))
            _new_loop.close()
        except Exception as e:
            logger.warning("Error closing Tor session: %s", e)
        finally:
            _SESSION_MGR._tor_session = None
    finally:
        _SESSION_MGR._tor_session_locally_created = False


async def _close_i2p_session() -> None:
    """
    P10: Close the I2P session (for cleanup).
    """
    # ISSUE-080: Use helper methods for Union type support
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._i2p_session) and _SESSION_MGR._i2p_session_locally_created:
        await _SESSION_MGR._session_aclose(_SESSION_MGR._i2p_session)
    _SESSION_MGR._i2p_session = None
    _SESSION_MGR._i2p_session_locally_created = False


def _close_i2p_session_sync() -> None:
    """Sync wrapper for I2P session cleanup via atexit.

    F219D: Safe teardown that avoids calling run_until_complete on a running loop.
    Uses a fresh event loop in a daemon thread when no running loop exists.
    """
    import threading

    # Nothing to do if session is None or already closed
    if not _SESSION_MGR.i2p_is_healthy():
        return

    # Only close locally-created sessions — never touch injected providers
    if not _SESSION_MGR._i2p_session_locally_created:
        _SESSION_MGR._i2p_session = None
        return

    try:
        _loop = asyncio.get_running_loop()

        # A loop is running — spawn a daemon thread to run the async cleanup
        # FIX F196A: use run_until_complete instead of asyncio.run to avoid M1 crash
        def _run_closer() -> None:
            session = _SESSION_MGR._i2p_session  # capture local ref to avoid race
            if session is None:
                return
            try:
                _loop.run_until_complete(session.close())  # type: ignore[unreachable]
            except Exception as e:
                logger.warning("Error closing I2P session in thread: %s", e)
            finally:
                _SESSION_MGR._i2p_session = None

        _t = threading.Thread(target=_run_closer, daemon=True)
        _t.start()
        # Don't wait — daemon thread will complete asynchronously
    except RuntimeError:
        # No running loop — use a fresh event loop synchronously
        session = _SESSION_MGR._i2p_session
        if session is None:
            _SESSION_MGR._i2p_session = None
            return
        try:
            _new_loop = asyncio.new_event_loop()
            # ISSUE-080: Use helper for close on Union type
            _new_loop.run_until_complete(_SESSION_MGR._session_aclose(session))
            _new_loop.close()
        except Exception as e:
            logger.warning("Error closing I2P session: %s", e)
        finally:
            _SESSION_MGR._i2p_session = None
    finally:
        _SESSION_MGR._i2p_session_locally_created = False


# F219D: Canonical public teardown — closes all local fallback sessions safely.
# Injected provider sessions are NOT closed by this function (owned elsewhere).
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

    # --- Tor close ---
    _tor_attempted = False
    _tor_success = False
    _tor_error: str | None = None

    # ISSUE-080: Use helper methods for Union type support
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._tor_session):
        _tor_attempted = True
        if _SESSION_MGR._tor_session_locally_created:
            try:
                await _SESSION_MGR._session_aclose(_SESSION_MGR._tor_session)
                _tor_success = True
            except asyncio.CancelledError:
                raise
            except Exception as _e:
                _tor_error = str(_e)
                logger.warning("Error closing Tor session: %s", _e)
        # else: injected session — don't close
    _SESSION_MGR._tor_session = None
    _SESSION_MGR._tor_session_locally_created = False

    # --- I2P close ---
    _i2p_attempted = False
    _i2p_success = False
    _i2p_error: str | None = None

    # ISSUE-080: Use helper methods for Union type support
    if not _SESSION_MGR._session_is_closed(_SESSION_MGR._i2p_session):
        _i2p_attempted = True
        if _SESSION_MGR._i2p_session_locally_created:
            try:
                await _SESSION_MGR._session_aclose(_SESSION_MGR._i2p_session)
                _i2p_success = True
            except asyncio.CancelledError:
                raise
            except Exception as _e:
                _i2p_error = str(_e)
                logger.warning("Error closing I2P session: %s", _e)
        # else: injected session — don't close
    _SESSION_MGR._i2p_session = None
    _SESSION_MGR._i2p_session_locally_created = False

    # Reset telemetry to unavailable
    _SESSION_MGR._session_source_telemetry["tor"] = "unavailable"
    _SESSION_MGR._session_source_telemetry["i2p"] = "unavailable"

    return {
        "tor_close_attempted": _tor_attempted,
        "tor_close_success": _tor_success,
        "tor_close_error": _tor_error,
        "i2p_close_attempted": _i2p_attempted,
        "i2p_close_success": _i2p_success,
        "i2p_close_error": _i2p_error,
        "injected_provider_active": _injected_active,
    }


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
    # Use the manager's status_snapshot for consistent state
    status = _SESSION_MGR.status_snapshot()
    return {
        "tor_session_present": status["tor_present"],
        "tor_session_closed": status["tor_closed"],
        "i2p_session_present": status["i2p_present"],
        "i2p_session_closed": status["i2p_closed"],
        "injected_provider_active": status["injected_active"],
        "session_source_telemetry": status["telemetry"],
    }


# F219D: Register atexit only after defining the safe sync wrappers
def _register_atexit_cleanup() -> None:
    """Lazy atexit registration to defer import cost."""
    import atexit

    atexit.register(_close_tor_session_sync)
    atexit.register(_close_i2p_session_sync)

_register_atexit_cleanup()


# ---------------------------------------------------------------------------
# P7: JS detection and Camoufox/nodriver rendering
# ---------------------------------------------------------------------------

# JS detection patterns — trigger Camoufox retry
# P0-FIX: SERP JS detection — known JS-heavy search/discovery domains.
# These domains render via JavaScript but lack <noscript> tags,
# so _NOSCRIPT_RE alone misses 100% of SERP pages.
_SERP_HOST_RE = re.compile(
    r"(google\.|bing\.|duckduckgo\.|yahoo\.|baidu\.|yandex\.|so\.|startpage\.|search\.|serp)"
    r"|searchresults|webcache|googlesyndication|googletagmanager| DoubleClick"
    r"|search\?q=|/search\?|\?q=|\&oq=|\&gs_l=",
    re.IGNORECASE,
)
# Content-length ratio heuristic: if response is very small relative to
# declared Content-Length, the real content is JS-rendered.
_CONTENT_LENGTH_RE = re.compile(r"content-length\s*[=:]\s*(\d+)", re.IGNORECASE)

_NOSCRIPT_RE = re.compile(r"<noscript[^>]*>|enable javascript", re.IGNORECASE)

# F207F: Feed/RSS URL detection — skip JS renderer for XML-ish feeds
_FEED_URL_RE = re.compile(
    r"/?(?:rss|feed|atom|xml|sitemap|opensearch)",
    re.IGNORECASE,
)

# F265C: Known non-JS-heavy domains — curl_cffi works fine without browser
# These are standard CTI/news sites that don't require JavaScript rendering.
# When Chrome binary is missing and all JS renderers are unavailable,
# these domains still work perfectly via curl_cffi.
_JS_SKIP_HOST_RE = re.compile(
    r"(?:^|\.)"
    r"(?:threatfox\.abuse\.ch|bleepingcomputer\.com|thehackernews\.com|"
    r"krebsonsecurity\.com|cisa\.gov|id-ransomware\.malwarehunterteam\.com|"
    r"ransomwaretracker\.xyz|abuse\.ch|urlhaus\.abuse\.ch|feodo\.tracker|"
    r"openphish\.com|cyberscoop\.com|darkreading\.com|threatpost\.com|"
    r"therecord\.media|securityweek\.com|inforisktoday\.com|helpnetsecurity\.com|"
    r"malwarebazaar\.abuse\.ch|sslbl\.abuse\.ch)$",
    re.IGNORECASE,
)

# F207F: JS renderer capability tracking — process-level dict
# F-GLOBAL: Replaced with _JSRendererCapability class


class _JSRendererCapability:
    """Thread-safe JS renderer capability tracker.

    F-GLOBAL: Encapsulates _js_renderer_capability dict and
    _js_renderer_capability_lock.

    Tracks availability of camoufox, nodriver, and playwright.
    Uses threading.Lock for thread-safe access.
    Cached after first check — use reset() to force re-check.
    """

    __slots__ = ("_capability", "_lock")

    def __init__(self) -> None:
        self._capability: dict[str, str | None] = {
            "camoufox": None,
            "nodriver": None,
            "playwright": None,
        }
        self._lock = threading.Lock()

    def get(self) -> dict[str, str | None]:
        """Get current capability snapshot (copy)."""
        with self._lock:
            return dict(self._capability)

    def reset(self) -> None:
        """Reset all capabilities to unknown (force re-check)."""
        with self._lock:
            self._capability = {"camoufox": None, "nodriver": None, "playwright": None}

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
        if self._capability["camoufox"] is not None:
            return
        try:
            import camoufox

            _ = camoufox.Session  # type: ignore[attr-defined]
            self._capability["camoufox"] = None
        except Exception as e:
            self._capability["camoufox"] = f"camoufox_unavailable: {e}"

    def _check_nodriver(self) -> None:
        """Check nodriver availability."""
        if self._capability["nodriver"] is not None:
            return
        if not _check_chrome_binary_exists():
            self._capability["nodriver"] = "chrome_binary_missing"
            return
        try:
            import nodriver

            _ = nodriver.start
            self._capability["nodriver"] = None
        except Exception as e:
            self._capability["nodriver"] = f"nodriver_unavailable: {e}"

    def _check_playwright(self) -> None:
        """Check playwright availability."""
        if self._capability["playwright"] is not None:
            return
        heavy_browser_enabled = ENV.get_bool("HLEDAC_ENABLE_HEAVY_BROWSER")
        if not heavy_browser_enabled:
            self._capability["playwright"] = "heavy_browser_disabled"
            return
        try:
            import playwright

            _ = playwright.async_api
            self._capability["playwright"] = None
        except Exception as e:
            self._capability["playwright"] = f"playwright_unavailable: {e}"

    def is_any_available(self) -> bool:
        """Check if any JS renderer is available."""
        with self._lock:
            return any(v is None for v in self._capability.values())


# Module-level singleton
_js_renderer_cap = _JSRendererCapability()


def _check_chrome_binary_exists() -> bool:
    """Check if Chrome/Chromium binary is available on the system (macOS + Linux)."""
    import os

    candidates = [
        # macOS
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        # Linux
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    from pathlib import Path

    return any(Path(p).exists() and os.access(p, os.X_OK) for p in candidates)


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
    return all(v is not None for v in cap.values())


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

    F271: Rust url_ops.looks_like_feed_url fast path with urllib.parse fallback.
    The Rust function is a direct drop-in for the regex check on
    urlparse(url).path.rstrip("/"). ImportError or runtime failure
    falls through to the unchanged Python branch.
    """
    try:
        _uops = url_ops
        if _uops is not None:
            return _uops.looks_like_feed_url(url)
        # F320: Rust fast path failed — fall through to Python path
        # (only when rust_url.looks_like_feed_url raises)
    except Exception:
        # rust_url unavailable — Python fallback
        pass
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip("/")
        return bool(_FEED_URL_RE.search(path))
    except Exception:
        return False


def _needs_js_fetch(text: str, *, url: str = "", content_length: int = 0, declared_length: int = -1) -> bool:
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
    # F265C: Check known non-JS-heavy CTI/news domains FIRST
    # These standard threat intel and news sites work fine with curl_cffi
    # without requiring Chrome/nodriver/camoufox. Bypass all other heuristics.
    if url:
        try:
            from urllib.parse import urlparse

            host = urlparse(url).hostname or ""
            if host and _JS_SKIP_HOST_RE.search(host):
                return False
        except Exception:  # noqa: BLE001
            pass
    # Check noscript tag second
    if _NOSCRIPT_RE.search(text):
        return True
    # P0-FIX: SERP domain heuristic — bypass <noscript> check for known search engines
    if url:
        try:
            from urllib.parse import urlparse

            host = urlparse(url).hostname or ""
            if host and _SERP_HOST_RE.search(host + "/" + url):
                return True
            # F265C: Known non-JS-heavy CTI/news domains — curl_cffi works without browser
            # Skip JS detection for these standard threat intel and news sites.
            if _JS_SKIP_HOST_RE.search(host):
                return False
        except Exception:  # noqa: BLE001
            pass
    # P0-FIX: content-length ratio heuristic
    # If declared_length >> content_length (e.g. declared 50KB, received <5KB),
    # the server is telling us the real content is much larger and JS-rendered.
    if declared_length > 0 and content_length > 0:
        if declared_length > content_length * 3 and content_length < 20_000:
            return True
    return False


# F226A: Adaptive MAX_BYTES cap — halves size on UMA critical (M1 8GB budget).
# 25 in-flight × 10MB = 250MB worst case; under pressure → 5MB × 25 = 125MB.
# Mirrors transport/decompression.py:31 self-contained 10MB ceiling pattern.
try:
    from hledac.universal.utils.uma_budget import is_uma_critical as _is_uma_critical
except Exception:  # fail-soft: never crash import

    def _is_uma_critical() -> bool:  # type: ignore[no-redef]
        return False


MAX_BYTES_HARD_PRESSURE: Final[int] = 5_000_000  # 5MB on UMA critical
# MAX_BYTES_HARD stays 10MB (final declared below); computed dynamically here.


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
    except Exception:
        hard = MAX_BYTES_HARD
    if requested <= 0:
        return hard
    return min(max(requested, 1), hard)


# F226A: Shared JS-renderer semaphore — never more than 1 browser process alive.
# Camoufox AND nodriver MUST serialize; both are RAM-heavy (250-500MB each on M1).
# The original _CAMOUFOX_LOCK (asyncio.Lock) is kept for backward compat as a
# second-tier intra-Camoufox guard, but cross-renderer serialization goes through
# this bounded Semaphore(1) so we never hit 2 browser processes simultaneously.
#
# F226A: Lazy init — Semaphore must be created in the running event loop, otherwise
# we'd hit "bound to a different event loop" errors in tests / alt loops. The
# getter guarantees a per-loop singleton.
_JS_RENDERER_SEMAPHORE: asyncio.Semaphore | None = None
_JSRENDERER_SEMAPHORE_LOCK = threading.Lock()

# P14 FIX: Cooldown after browser.stop() before releasing semaphore.
# On macOS, browser.stop() is async and returns before the child process is fully
# reaped by the OS. Without this delay, the next renderer (camoufox→nodriver→playwright)
# races with the dying process and sees TargetClosedError / "browser has been closed".
_JSC_RENDERER_COOLDOWN_S = 0.5  # P2-4: increased from 0.15 — macOS browser helper processes may linger 200-400ms


def _get_js_renderer_semaphore() -> asyncio.Semaphore:
    """F226A: Lazily-initialized, per-event-loop JS renderer Semaphore(1).

    Thread-safe via double-checked locking:
    - Fast path: outer check without lock
    - Slow path: inner check with lock (only first caller blocks)
    """
    global _JS_RENDERER_SEMAPHORE
    if _JS_RENDERER_SEMAPHORE is not None:
        return _JS_RENDERER_SEMAPHORE
    with _JSRENDERER_SEMAPHORE_LOCK:
        if _JS_RENDERER_SEMAPHORE is None:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing

            _JS_RENDERER_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
    return _JS_RENDERER_SEMAPHORE  # type: ignore[return-value]


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

    # Release + clear the shared semaphore so next sprint re-initializes cleanly
    try:
        _sem = _JS_RENDERER_SEMAPHORE
        if _sem is not None:
            try:
                # Drain the semaphore in case a browser is still alive inside it
                for _ in range(_sem._value + 1):  # type: ignore[attr-defined]
                    await asyncio.sleep(0)
            except Exception:  # noqa: BLE001
                pass
            _JS_RENDERER_SEMAPHORE = None
    except Exception as _e:
        logger.debug("[browser_pool] semaphore teardown skipped: %s", _e)

    # Reset renderer capability map so next sprint re-probes availability
    try:
        _js_renderer_cap.reset()
    except Exception as _e:
        logger.debug("[browser_pool] capability reset skipped: %s", _e)

    # Cooldown so OS can fully reap any lingering browser processes
    try:
        await asyncio.sleep(_JSC_RENDERER_COOLDOWN_S)
    except Exception:  # noqa: BLE001
        pass

    logger.debug("[winddown] browser pool torn down")


# F226B: aiohttp body-cap helper — single source of truth for the chunked
# read loop. Originally duplicated at public_fetcher.py ~2207-2294 alongside
# the httpx_h2 inline copy (~1768-1796). Now both call into helpers in
# transport.body_limiter; this wrapper adds the aiohttp-specific first-chunk
# XML peek (used by content_type rejection recovery).
class AiohttpBodyOutcome(msgspec.Struct, frozen=True):
    """F226B: aiohttp body read outcome with peek + size cap."""

    body: bytes
    total_read: int
    truncated: bool
    chunks_consumed: int
    xml_recovered: bool
    first_chunk_peeked: bool


async def _read_aiohttp_body_with_peek(
    chunks: AsyncIterator[bytes],
    max_bytes: int,
    *,
    enable_peek: bool,
) -> AiohttpBodyOutcome:
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
            logger.warning(
                f"Aiohttp body read hit CHUNKS_BUDGET={CHUNKS_BUDGET}; truncating at {len(content_bytes)} bytes"
            )
            truncated = True
            break

        chunks_consumed += 1

        # First-chunk peek for XML recovery (used by CT-rejection fast path).
        if enable_peek and not first_chunk_peeked:
            first_chunk_peeked = True
            if _looks_xmlish(chunk):
                xml_recovered = True

        # Size cap — mirror body_limiter semantics (O(1) truncation).
        if max_bytes > 0 and (len(content_bytes) + len(chunk)) > max_bytes:
            remaining = max_bytes - len(content_bytes)
            if remaining > 0:
                content_bytes.extend(chunk[:remaining])
            logger.debug(
                "Aiohttp body truncated to {max_bytes} bytes after {chunks_consumed} chunks",
                max_bytes=max_bytes,
                chunks_consumed=chunks_consumed,
            )
            truncated = True
            break

        content_bytes.extend(chunk)

    return AiohttpBodyOutcome(
        body=bytes(content_bytes),
        total_read=len(content_bytes),
        truncated=truncated,
        chunks_consumed=chunks_consumed,
        xml_recovered=xml_recovered,
        first_chunk_peeked=first_chunk_peeked,
    )


async def _peek_aiohttp_first_chunk(
    chunks: AsyncIterator[bytes],
) -> tuple[bool, bytes | None]:
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
        return False, None
    return _looks_xmlish(first_chunk), first_chunk


async def _fetch_with_camoufox(url: str, timeout: float = 15.0) -> str:
    """
    Fetch JS-heavy page via Camoufox (Firefox-based anti-detect).
    Max 1 instance, protected by _CAMOUFOX_LOCK singleton.
    M1-optimized: headless, WebGL spoofed for Apple M1.

    F202H: Uses opsec_policy.get_renderer_policy() for M1 conflict guard —
    replaces inline is_embedding_context_active() check with centralized policy.
    """
    # F202H: Use opsec_policy for M1 model+renderer conflict guard
    try:
        from hledac.universal.embedding_pipeline import is_embedding_context_active
        from hledac.universal.runtime.opsec_policy import OPSECContext, get_renderer_policy

        has_model = is_embedding_context_active()
        ctx = OPSECContext(has_model_context=has_model)
        policy = get_renderer_policy(ctx)
        if not policy.allowed:
            logger.warning(
                f"[F202H] Renderer blocked by opsec_policy: {policy.blocked_reason} — skipping Camoufox for {url}"
            )
            return ""
    except Exception as e:
        logger.warning("Error checking renderer policy, proceeding with caution: %s", e)

    try:
        from camoufox.async_api import AsyncCamoufox  # noqa: F401  # camoufox.async_api.AsyncCamoufox
    except ImportError:
        logger.debug("camoufox not installed, JS fetch unavailable")
        return ""

    # F226A: Outer guard — serialize against nodriver so we never run 2 browsers.
    async with _get_js_renderer_semaphore():
        # Inner guard — original _CAMOUFOX_LOCK (intra-Camoufox consistency).
        return await _camoufox_locked(url, timeout)


_CAMOUFOX_OS_ROTATION: tuple[str, ...] = (
    "macos",
    "windows",
    "linux",
)
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
        return ""
    async with _CAMOUFOX_LOCK:
        last_error = ""
        for attempt in range(_CAMOUFOX_MAX_RETRIES):
            os_choice = _CAMOUFOX_OS_ROTATION[attempt % len(_CAMOUFOX_OS_ROTATION)]
            try:
                async with AsyncCamoufox(
                    headless=True,
                    os=os_choice,
                    webgl_config=("Apple", "Apple M1, or similar"),
                ) as browser:
                    page = await browser.new_page()
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                        html = await page.content()
                    finally:
                        await page.close()
                    return html
            except Exception as e:
                last_error = str(e)
                logger.debug(
                    f"Camoufox attempt {attempt + 1}/{_CAMOUFOX_MAX_RETRIES} (os={os_choice}) failed for {url}: {e}"
                )
                # P2-4: cooldown between retries so OS can fully reap
                await _cooldown_after_browser_stop()
                continue
        logger.warning(f"Camoufox all {_CAMOUFOX_MAX_RETRIES} attempts failed for {url}: {last_error}")
        return ""


async def _fetch_with_nodriver(url: str, url_kind: str = "", url_host: str = "") -> str:
    """
    F265C: Primary JS fetch via nodriver (direct CDP, no WebDriver).
    On M1, nodriver is more stable than Camoufox — used as first choice.
    Requires Chrome binary present. Returns "" with telemetry on failure.

    B1: url_kind/url_host params — caller pre-classified via _classify_url_cached.
    """
    # Check chrome binary
    if not _check_chrome_binary_exists():
        logger.debug("nodriver skipped: chrome binary not found")
        return ""

    # F221-FIX: Block nodriver launch when M1 is in critical memory — Chrome
    # ~400-600MB resident; running it under critical memory pressure causes OOM
    # crashes that kill the entire sprint. Skip silently, let caller fall back.
    if _is_uma_critical():
        logger.debug("nodriver skipped: UMA critical memory pressure")
        return ""

    try:
        import nodriver as uc  # noqa: F401  # type: ignore[import]  # nodriver
    except ImportError:
        _js_renderer_cap.mark_unavailable("nodriver", "nodriver_unavailable")
        logger.debug("nodriver not installed, CDP fetch unavailable")
        return ""

    # F226A: Serialize against Camoufox — never run 2 browser processes on M1.
    # Combined ~500-900MB RAM; halving that risk is the whole point.
    async with _get_js_renderer_semaphore():
        return await _nodriver_locked(url, url_kind, url_host)


_NODRIVER_MAX_RETRIES: int = 2


async def _nodriver_locked(url: str, url_kind: str = "", url_host: str = "") -> str:
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
    import nodriver as uc  # type: ignore[import]  # already imported by caller; here for isolation

    # B1: Use pre-classified result when available.
    if not url_kind or not url_host:
        _url_kind_batch = _batch_classify_url_cached([url])
        url_kind = _url_kind_batch[0][0] if _url_kind_batch else "clearnet"
    _is_onion = url_kind == "onion"
    browser = None
    page = None
    last_error = ""
    for attempt in range(_NODRIVER_MAX_RETRIES):
        try:
            browser_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # P2-4: anti-fingerprint hardening
                "--disable-blink-features=AutomationControlled",
            ]
            # P2-4: Route .onion through Tor SOCKS5 proxy
            if _is_onion:
                browser_args.append(f"--proxy-server={TOR_SOCKS_PROXY}")
            browser = await uc.start(
                headless=True,
                browser_args=browser_args,
            )
            page = await browser.get(url)  # type: ignore[attr-defined]
            try:
                await asyncio.sleep(2)  # jitter for bot detection
                html = await page.get_content()
            finally:
                if page is not None:
                    await page.close()
            return html
        except asyncio.CancelledError:
            if browser is not None:
                browser.stop()  # type: ignore[attr-defined]
            raise
        except Exception as e:
            last_error = str(e)
            _ev0 = attempt + 1
            logger.debug("nodriver attempt {attempt + 1} failed for {url}: {e}", url=url, e=e, _ev0=attempt + 1)
            if browser is not None:
                browser.stop()  # type: ignore[attr-defined]
            await asyncio.sleep(0.2)  # brief pause between retries
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass
    logger.warning(
        "nodriver all {_NODRIVER_MAX_RETRIES} attempts failed for {url}: {last_error}",
        _NODRIVER_MAX_RETRIES=_NODRIVER_MAX_RETRIES,
        url=url,
        last_error=last_error,
    )
    return ""


async def _fetch_with_playwright(url: str, timeout: float = 15.0) -> str:
    """
    F265C: Playwright fallback — last resort after nodriver fails.
    Requires HLEDAC_ENABLE_HEAVY_BROWSER=1 AND playwright installed.
    Returns "" with telemetry on any failure.
    """

    if not ENV.get_bool("HLEDAC_ENABLE_HEAVY_BROWSER"):
        logger.debug("playwright skipped: HLEDAC_ENABLE_HEAVY_BROWSER != 1")
        return ""

    try:
        importlib.util.find_spec("playwright")
    except ImportError:
        logger.debug("playwright not installed, fallback unavailable")
        return ""

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
        return ""

    browser = None
    page = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
                html = await page.content()
            finally:
                if page is not None:
                    await page.close()
            return html
    except asyncio.CancelledError:
        if browser is not None:
            await browser.close()
        raise
    except Exception as e:
        logger.warning("playwright fetch failed: {e}", e=e)
        return ""
    finally:
        if browser is not None:
            await browser.close()
        # P14 FIX: yield to event loop so OS can fully reap the browser process
        # before the next renderer acquires the semaphore and races with it
        await _cooldown_after_browser_stop()


# ---------------------------------------------------------------------------
# Main fetch function
# ---------------------------------------------------------------------------


async def async_fetch_public_text(
    url: str,
    timeout_s: float = 35.0,
    max_bytes: int = MAX_BYTES_DEFAULT,
    use_stealth: bool = False,
    use_js: bool = False,
    use_doh: bool = False,
    js_confidence: float = 0.8,
    priority: int = 5,
) -> FetchResult:
    """
    Fetch a public URL using the shared aiohttp session.

    P4 stealth mode: optional StealthManager/StealthSession for enhanced privacy.
    P4 Tor mode: .onion URLs automatically routed via Tor SOCKS5 proxy.
    P7 JS mode: Camoufox (primary) with nodriver fallback for JS-heavy pages.
    Chunked streaming with hard size cap.
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
        If True, use StealthManager/StealthSession for enhanced stealth
        (header rotation, fingerprint randomization, rate limiting).
    use_js : bool
        If True, force JS rendering via Camoufox/nodriver.
    use_doh : bool
        P16: If True, resolve hostname via DoH (cloudflare-dns) before
        connecting. Falls back to system DNS if DoH fails. Configurable
        via hledac.universal.config.PrivacyConfig.use_doh.

    Returns
    -------
    FetchResult
        Typed result with final_url, status, content_type, text (or None),
        byte counts, elapsed_ms, and optional error.
    """
    # -------------------------------------------------------------------------
    # PHASE 1: Fast-fail guards (lines 1296-1327)
    # -------------------------------------------------------------------------
    # Non-string URL or validation failure → return immediately
    # No transport setup needed, no side effects.

    t0 = time.monotonic()
    _tc = TransportCounters()

    # Issue #046: propagate request_id to ContextVar for fetch chain correlation
    _request_id: str = ""
    try:
        from core.telemetry.context_state import reset_request_id, set_request_id

        _request_id = set_request_id()
    except Exception:
        pass

    # --- Type guard: non-string input fails fast, fail-soft ---
    if not isinstance(url, str):
        elapsed_ms = (time.monotonic() - t0) * 1000
        return FetchResult(
            url=str(url) if url is not None else "",
            final_url=str(url) if url is not None else "",
            status_code=0,
            content_type="",
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=elapsed_ms,
            error="url_empty",
            failure_stage="validation",
        )

    # --- URL validation (strip happens inside _validate_url) ---
    validation_error = _validate_url(url)
    if validation_error is not None:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return FetchResult(
            url=url,
            final_url=url,
            status_code=0,
            content_type="",
            text=None,
            fetched_bytes=0,
            declared_length=-1,
            elapsed_ms=elapsed_ms,
            error=validation_error,
            failure_stage="validation",
        )

    # -------------------------------------------------------------------------
    # PHASE 2: URL classification — B1: single FFI, reused by all consumers.
    # Eliminates 3 sequential Rust FFI calls (circuit_breaker + route_transport
    # + DoH host extract) down to 1. Expected gain: 66% FFI overhead reduction.
    # _classify_url_cached uses Rust fast path with Python fallback.
    # -------------------------------------------------------------------------

    _url_kind: str = "malformed"
    _url_host: str = ""
    try:
        _url_kind, _url_host = _classify_url_cached(url)
    except Exception as _e:
        logger.debug("URL classification failed (non-fatal): {_e}", _e=_e)

    # --- Circuit breaker: fail-fast on domains with too many failures ---
    _circuit_breaker_domain: str = _url_host
    _circuit_breaker: CircuitBreaker | None = None
    try:
        if _circuit_breaker_domain:
            _circuit_breaker = get_breaker(_circuit_breaker_domain)
            # Fail-open: no breaker for domain = no throttling, proceed normally
            if _circuit_breaker is None:
                _circuit_breaker = None  # explicitly None so later code skips breaker
            else:
                decision = _circuit_breaker.check_circuit()
                if not decision.allowed:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    return FetchResult(
                        url=url,
                        final_url=url,
                        status_code=0,
                        content_type="",
                        text=None,
                        fetched_bytes=0,
                        declared_length=-1,
                        elapsed_ms=elapsed_ms,
                        error=f"circuit_breaker_open:{decision.state}:{decision.reason}",
                        failure_stage="circuit_breaker",
                        selected_transport="httpx_h2",
                        transport_policy_reason="clearnet_default",
                    )
    except Exception as e:
        logger.debug("Circuit breaker check failed (non-fatal): {e}", e=e)

    # -------------------------------------------------------------------------
    # PHASE 2: Size cap enforcement (F226A — adaptive)
    # -------------------------------------------------------------------------
    # Adaptive cap: MAX_BYTES_HARD (10MB) normally, MAX_BYTES_HARD_PRESSURE (5MB)
    # when UMA is critical. Bounds the 25-in-flight worst case from 250MB to
    # 125MB under M1 8GB pressure. Caller's max_bytes is honored within the cap.

    # --- F226A: Size cap enforcement (UMA-aware) ---
    max_bytes = _compute_effective_max_bytes(max_bytes)

    # -------------------------------------------------------------------------
    # PHASE 4: Transport routing (lines 1423-1444)
    # -------------------------------------------------------------------------
    # route_transport() is a PURE function — no I/O, no side effects.
    # Derives lane selection from URL characteristics and runtime flags.

    # --- F206AR: Route transport via canonical router (B1: pass pre-classified) ---
    _router_decision: TransportDecision = route_transport(
        url,
        use_stealth=use_stealth,
        use_js=use_js,
        cache_safe=False,
        retry_after_status=None,  # Escalation from 403/429 handled by curl_cffi path
        suggested_timeout_s=timeout_s,
        suggested_max_bytes=max_bytes,
        suggested_concurrency=None,
        # B1: Pre-classified — router skips internal _extract_host FFI.
        # Single FFI now covers: circuit_breaker + route_transport + DoH host.
        preclassified_kind=_url_kind,
        preclassified_host=_url_host,
    )
    _router_lane = _router_decision.lane
    _router_reason = _router_decision.reason
    _original_policy_reason: str | None = None  # For additive fallback tracking

    # --- F206AR: Fallback reason tracking (defined early for all paths) ---
    _httpx_fallback_reason: str | None = None
    _curl_fallback_reason: str | None = None

    # Transport telemetry — initialized early so early returns are safe
    _actual_transport: str = ""
    _fallback_info: str | None = None

    # Derive use_tor/use_i2p from router decision (replaces manual URL parsing)
    use_tor = _router_lane == "tor_socks"
    use_i2p = _router_lane == "i2p_socks"

    # --- F265C: Transport policy decision — single authority for tier selection ---
    # Determine whether H2/H3 candidates based on router lane (already classified)
    _is_h2_candidate = _router_lane == "httpx_h2"
    _is_h3_candidate = _router_lane == "httpx_h3"
    try:
        from hledac.universal.transport.policy import (
            get_transport_policy,
        )

        _policy_decision = get_transport_policy(
            use_stealth=use_stealth,
            use_js=use_js,
            retry_after_status=None,  # Escalation handled via curl_cffi path
            js_confidence=js_confidence,
            priority=priority,
            is_httpx_h2_candidate=_is_h2_candidate,
            is_httpx_h3_candidate=_is_h3_candidate,
        )
        _tier = _policy_decision.tier
        _t0_allowed = True  # [TP-1] T0 is always-on
        _t3_allowed = _policy_decision.js_allowed
        _h2_allowed = _policy_decision.h2_allowed
        _h3_allowed = _policy_decision.h3_allowed
    except Exception as _policy_e:
        # [TP-5] Fail-safe: any error returns T0 decision
        _tier = "T0_curl_cffi"
        _t0_allowed = True
        _t3_allowed = False
        _h2_allowed = False
        _h3_allowed = False
        logger.debug("[policy] get_transport_policy failed (falling back to T0): {_policy_e}", _policy_e=_policy_e)

    # -------------------------------------------------------------------------
    # PHASE 4B: Explicit JS rendering mode
    # -------------------------------------------------------------------------
    # use_js=True bypasses ALL transport selection — goes straight to browser.
    # NOTE: This must come AFTER _t3_allowed / _policy_decision are defined.

    if use_js:
        # F265C: Policy-gated JS rendering
        if not _t3_allowed:
            logger.warning(
                "BROWSER_DEFERRED url=%s priority=%d js_confidence=%.2f — policy_js_blocked",
                url,
                priority,
                js_confidence,
            )
            # Fall through: return curl_cffi result even if thin (partial > OOM crash)
        else:
            logger.info("JS rendering requested for {url}", url=url)
            # F265C: nodriver primary (M1-stable), camoufox secondary, playwright last resort
            js_html = await _fetch_with_nodriver(url, _url_kind, _url_host)
            if not js_html:
                logger.warning("nodriver failed, trying Camoufox: {url}", url=url)
                js_html = await _fetch_with_camoufox(url, timeout=timeout_s)
            if not js_html:
                logger.warning("Camoufox failed, trying Playwright: {url}", url=url)
                js_html = await _fetch_with_playwright(url, timeout=timeout_s)
            if js_html:
                js_text, js_matches, js_meta = await process_html_payload(js_html, url)
                elapsed_ms = (time.monotonic() - t0) * 1000
                _tc.js_renderer_count += 1
                _meta_sources = list(js_meta.get("ga_gtm_ids", ()))
                if js_meta.get("og_tags"):
                    _meta_sources.append("og_tags")
                if js_meta.get("comments"):
                    _meta_sources.append("html_comments")
                if js_text:
                    _store_body_hash(url, _compute_body_hash(js_text.encode("utf-8", errors="replace")))
                _matched: tuple[str, ...] = tuple(
                    (m.label or "") + "|" + m.pattern + "|" + m.value for m in (js_matches or [])
                )
                return FetchResult(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content_type="text/html",
                    text=js_text,
                    fetched_bytes=len(js_html),
                    declared_length=-1,
                    elapsed_ms=elapsed_ms,
                    error=None,
                    selected_transport="js",
                    transport_policy_reason="js_required",
                    transport_counters=_tc,
                    hydration_sources=tuple(_meta_sources),
                    matched_patterns=_matched,
                )
            # JS rendering completely failed
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.js_renderer_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error="js_render_failed",
                failure_stage="fetching",
                selected_transport="js",
                transport_policy_reason="js_required",
                transport_counters=_tc,
            )

    # -------------------------------------------------------------------------
    # PHASE 5: httpx_h2 lane execution (lines 1452-1545)
    # -------------------------------------------------------------------------
    # Router selected httpx_h2 when: API-like URL + HLEDAC_ENABLE_HTTPX_H2=1 + h2 available.
    # Falls back to aiohttp_default on any error (including classification + record).

    # --- F206AR: H2 lane — router selected httpx_h2? ---
    # F265C: Policy gate — H2 lane additionally gated by _h2_allowed (env + memory)
    _use_httpx_h2: bool = _router_lane == "httpx_h2" and _h2_allowed
    if _use_httpx_h2:
        logger.debug("[HTTPX] H2 lane selected for {url}: {_router_reason}", url=url, _router_reason=_router_reason)
        _original_policy_reason = _router_reason
        try:
            # F273G-H3FIX: prime H3 LRU synchronously BEFORE the httpx fetch.
            # Blocking await (~200-400ms) ensures the LRU is warm for any
            # subsequent curl_cffi request to the same host within the sprint.
            # httpx itself cannot use H3, so the probe is purely for the
            # curl_cffi fallback path that runs if httpx gets 403/429.
            try:
                await _blocking_altsvc_probe_for_url(url)
            except Exception:  # noqa: BLE001
                pass

            _httpx_resp = await fetch_via_httpx_h2(url, timeout_s=timeout_s)
            _httpx_final_url = str(_httpx_resp.url)
            _httpx_resp_cast = cast(Any, _httpx_resp)
            _httpx_status = _httpx_resp_cast.status
            _httpx_content_type = _httpx_resp.headers.get("Content-Type", "")
            _httpx_raw_ct = _httpx_content_type.split(";")[0].strip().lower()

            # Note: probe_altsvc_speculative is called BEFORE the fetch
            # (above) so the LRU is warm for any follow-up curl_cffi
            # requests in the same sprint. httpx itself cannot use H3,
            # so no blocking pre-probe is needed here.

            # Detect HTTP version from response
            _http_ver: str | None = None
            try:
                _http_ver = _httpx_resp.extensions.get("http_version", None)
                if _http_ver:
                    _http_ver = f"http/{_http_ver.decode() if isinstance(_http_ver, bytes) else _http_ver}"
            except AttributeError:
                pass
                _http_ver = _httpx_resp.extensions.get("http_version", None)
                if _http_ver:
                    _http_ver = f"http/{_http_ver.decode() if isinstance(_http_ver, bytes) else _http_ver}"

            # F226B: Body cap — delegated to body_limiter._read_body_into (single source
            # of truth for chunked body reads). The previous inline loop duplicated
            # body_limiter.read_body_with_cap logic with a different return contract;
            # _read_body_into returns BodyReadResult(body, total_read, truncated, chunks)
            # which is enough context to build the FetchResult below without inline math.
            _body: BodyReadResult = await _read_body_into(_httpx_resp_cast.aiter_chunked(65536), max_bytes)
            if _body.truncated:
                elapsed_ms = (time.monotonic() - t0) * 1000
                _tc.httpx_h2_count += 1
                return FetchResult(
                    url=url,
                    final_url=_httpx_final_url,
                    status_code=_httpx_status,
                    content_type=_httpx_content_type,
                    text=None,
                    fetched_bytes=_body.total_read,
                    declared_length=-1,
                    elapsed_ms=elapsed_ms,
                    error="size_cap_exceeded",
                    failure_stage="size",
                    selected_transport="httpx_h2",
                    http_version=_http_ver,
                    transport_policy_reason=_router_reason,
                    transport_counters=_tc,
                )

            _text, _decode_replaced, _decode_replacement_count = _try_decode(_body.body)

            elapsed_ms = (time.monotonic() - t0) * 1000
            redirected, redirect_target = _derive_redirect_fields(url, _httpx_final_url)
            _tc.httpx_h2_count += 1
            if _text:
                _store_body_hash(url, _compute_body_hash(_text.encode("utf-8", errors="replace")))
            return FetchResult(
                url=url,
                final_url=_httpx_final_url,
                status_code=_httpx_status,
                content_type=_httpx_content_type,
                text=_text,
                fetched_bytes=_body.total_read,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=None,
                decode_replaced=_decode_replaced,
                decode_replacement_count=_decode_replacement_count,
                redirected=redirected,
                redirect_target=redirect_target,
                selected_transport="httpx_h2",
                http_version=_http_ver,
                transport_policy_reason=_router_reason,
                transport_counters=_tc,
                body=_body.body,  # F266A: raw bytes preserved for Arrow zero-copy
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raise
        except Exception as _e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            # F206AF: Record httpx_h2 failure and classify
            try:
                from hledac.universal.transport.httpx_transport import (
                    classify_httpx_h2_error,
                    record_httpx_h2_failure,
                )

                _httpx_err_type = classify_httpx_h2_error(_e)
                record_httpx_h2_failure()
            except asyncio.CancelledError:
                # F206AF: CancelledError MUST be re-raised, not caught
                raise
            except Exception:
                _httpx_err_type = "unknown_httpx_error"
            # HTTPX H2 failed — fallback to aiohttp with telemetry
            # F265C: Policy enforces T0 (curl_cffi_stealth) as escalation target
            logger.warning(
                "[HTTPX] H2 lane failed for {url} ({_httpx_err_type}), falling back to aiohttp: {_e}",
                url=url,
                _httpx_err_type=_httpx_err_type,
                _e=_e,
            )
            _use_httpx_h2 = False
            # F206AF: Set transport_fallback_reason for this URL
            # F265C: T0 is always the escalation tier per [TP-4]
            _httpx_fallback_reason: str | None = "httpx_h2_fallback"

    # -------------------------------------------------------------------------
    # PHASE 6: DoH + stealth + Tor/I2P session setup (lines 1560-1620)
    # -------------------------------------------------------------------------
    # No network I/O yet — setup only. Failures here return early with error.

    # Apply longer timeout for anonymized networks (Tor/I2P)
    if use_tor or use_i2p:
        timeout_s = timeout_s * TOR_STEALTH_TIMEOUT_SCALE

    # --- P16: Optional DoH resolution before connect ---
    _resolved_ip: str | None = None
    if use_doh:
        try:
            from hledac.universal.security.passive_dns import get_random_doh_provider, resolve_doh

            # B1: Reuse _url_kind/_url_host from PHASE 2 — no additional FFI.
            hostname = _url_host if _url_host else ""
            if hostname:
                # F229: Randomize DoH provider per request — eliminates provider-level tracking
                _doh_provider = get_random_doh_provider()
                ips = await resolve_doh(hostname, provider=_doh_provider)
                if ips:
                    _resolved_ip = ips[0]
                    logger.debug(
                        "DoH [{_doh_provider}] resolved {hostname} → {_resolved_ip}",
                        _doh_provider=_doh_provider,
                        hostname=hostname,
                        _resolved_ip=_resolved_ip,
                    )
                else:
                    logger.debug(
                        "DoH [{_doh_provider}] returned no IPs for {hostname}, falling back to system DNS",
                        _doh_provider=_doh_provider,
                        hostname=hostname,
                    )
        except Exception as e:
            logger.debug("DoH resolution failed for {url}: {e}", url=url, e=e)

    # --- P4: Canonical stealth session setup ---
    stealth_session = None
    if use_stealth:
        try:
            from hledac.universal.stealth.stealth_session import StealthSession

            stealth_session = StealthSession()
        except Exception as e:
            logger.warning("Stealth session unavailable, proceeding without: {e}", e=e)

    # -------------------------------------------------------------------------
    # PHASE 7: curl_cffi stealth lane (lines 1595-1650)
    # -------------------------------------------------------------------------
    # Router selected curl_cffi_stealth when: use_stealth=True OR retry 403/429.
    # Falls back to aiohttp_default on failure.

    # --- F206AR: curl_cffi stealth lane — router selected curl_cffi_stealth? ---
    # Router already enforced: no darknet/JS/Freenet. Falls back to aiohttp on failure.
    if _router_lane == "curl_cffi_stealth":
        _original_policy_reason = _router_reason
        # F229: Randomized headers for curl_cffi stealth lane — eliminates UA/language tracking
        _stealth_headers = build_randomized_headers()
        try:
            # F260+: opportunistic HTTP/3 (QUIC) upgrade via Alt-Svc cache.
            # None when host has no h3 advertisement or feature disabled.
            _curl_http_version = _altsvc_http_version_for(_altsvc_extract_host(url, _url_host))
            # F265B conditional-cache wrapper. Uses ETag/Last-Modified
            # from the cache to send If-None-Match/If-Modified-Since;
            # 304 responses return the cached body (0 bytes transferred).
            # _pre_probe=True handles the cold-start H3 priming:
            #   - LRU cold → blocking HEAD probe (~200-400ms) → LRU warm
            #   - LRU warm → no-op, proceeds with cached http_version
            # probe_altsvc_speculative is NOT needed here (would be redundant)
            # because _pre_probe is synchronous and runs BEFORE fetch.
            _curl_result = await fetch_via_curl_cffi_cached(
                url=url,
                headers=_stealth_headers,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
                profile="chrome110",
                http_version=_curl_http_version,
                _pre_probe=True,  # F265C: block first fetch to prime H3 LRU
            )
            # Record Alt-Svc h3 advertisement for future fetches to this host.
            _altsvc_record_from_result(url, _curl_result.get("headers"))
            # Build FetchResult from curl_cffi dict — mirrors httpx_h2 success path
            _curl_text: str | None
            _curl_bytes = _curl_result.get("content", b"")
            _curl_decode_replaced = False
            _curl_decode_replacement_count = 0
            _curl_error = _curl_result.get("error", None)
            # P0-FIX: extract declared_length from headers for JS detection heuristic
            _curl_declared_length: int = -1
            _curl_headers = _curl_result.get("headers", {}) or {}
            if _curl_headers:
                _cl = _curl_headers.get("content-length", _curl_headers.get("Content-Length", "-1"))
                try:
                    _curl_declared_length = int(_cl)
                except (ValueError, TypeError):
                    _curl_declared_length = -1
            if _curl_bytes:
                _curl_text, _curl_decode_replaced, _curl_decode_replacement_count = _try_decode(_curl_bytes)
            else:
                _curl_text = None

            # P0-2: curl_cffi fallback — after fast lane success, check JS sufficiency
            # before returning. Re-use curl_cffi result if static HTML is sufficient;
            # otherwise try WKWebView → heavy browser. Eliminates 40-50% of nodriver
            # calls (~100 MB RAM saved on M1 8GB).
            if _curl_text and _needs_js_fetch(
                _curl_text,
                url=url,
                content_length=len(_curl_bytes),
                declared_length=_curl_declared_length,
            ):
                # Static hydration check — fastest first
                from hledac.universal.utils.hydration_extractor import (
                    extract_static_hydration as _extract_static_hydration,
                )

                _hydration = _extract_static_hydration(_curl_text)
                _tc.static_hydration_attempted += 1
                if _hydration.sufficient:
                    _tc.static_hydration_sufficient += 1
                    logger.info("curl_cffi static hydration sufficient for {url}", url=url)
                    _curl_elapsed_ms = (time.monotonic() - t0) * 1000
                    _tc.curl_cffi_count += 1
                    # Compute redirect info inline — normally done at line 3137, needed here for early return
                    _curl_final_url = _curl_result.get("final_url", url)
                    _curl_redirected, _curl_redirect_target = _derive_redirect_fields(url, _curl_final_url)
                    return FetchResult(
                        url=url,
                        final_url=_curl_final_url,
                        status_code=_curl_result.get("status_code", 0),
                        content_type=_curl_result.get("content_type", ""),
                        text=_hydration.text if _hydration.text else _curl_text,
                        fetched_bytes=len(_curl_bytes),
                        declared_length=-1,
                        elapsed_ms=_curl_elapsed_ms,
                        error=_curl_error,
                        decode_replaced=_curl_decode_replaced,
                        decode_replacement_count=_curl_decode_replacement_count,
                        redirected=_curl_redirected,
                        redirect_target=_curl_redirect_target,
                        failure_stage=_curl_result.get("failure_stage", None),
                        network_error_kind=_curl_result.get("network_error_kind", None),
                        selected_transport="curl_cffi",
                        http_version=None,
                        transport_policy_reason=_router_reason,
                        transport_fallback_reason=None,
                        transport_counters=_tc,
                        js_renderer_skipped_reason=f"static_hydration_sufficient:{_hydration.reason}",
                        hydration_score=_hydration.hydration_score,
                        hydration_sources=tuple(_hydration.sources) if hasattr(_hydration, "sources") else (),
                    )
                elif _hydration.found:
                    _tc.static_hydration_insufficient += 1

                # WKWebView before heavy browser — ~0 RAM cost if unavailable
                from hledac.universal.rendering.macos_webkit_renderer import fetch_with_macos_webkit

                _wkr = await fetch_with_macos_webkit(url, timeout_s=timeout_s)
                if _wkr.ok and _wkr.html:
                    _wkr_text, _wkr_matches, _wkr_meta = await process_html_payload(_wkr.html, url)
                    _wkr_elapsed_ms = (time.monotonic() - t0) * 1000
                    _tc.js_renderer_count += 1
                    _tc.macos_webkit_count += 1
                    _wkr_sources = list(_wkr_meta.get("ga_gtm_ids", ()))
                    if _wkr_meta.get("og_tags"):
                        _wkr_sources.append("og_tags")
                    if _wkr_meta.get("comments"):
                        _wkr_sources.append("html_comments")
                    logger.info("WKWebView succeeded for {url} (curl_cffi fallback)", url=url)
                    return FetchResult(
                        url=url,
                        final_url=url,
                        status_code=200,
                        content_type="text/html",
                        text=_wkr_text,
                        fetched_bytes=_wkr.rendered_bytes,
                        declared_length=-1,
                        elapsed_ms=_wkr_elapsed_ms,
                        error=None,
                        selected_transport="js",
                        transport_policy_reason="js_required",
                        transport_counters=_tc,
                        hydration_sources=tuple(_wkr_sources),
                    )

                # WKWebView unavailable → heavy browser (nodriver → camoufox → playwright)
                if not _all_js_renderers_unavailable():
                    _js_html = await _fetch_with_nodriver(url, _url_kind, _url_host)
                    if _js_html:
                        _js_text, _js_matches, _ = await process_html_payload(_js_html, url)
                        _js_elapsed_ms = (time.monotonic() - t0) * 1000
                        _tc.js_renderer_count += 1
                        logger.info("nodriver succeeded for {url} (curl_cffi fallback)", url=url)
                        _matched = tuple((m.label or "") + "|" + m.pattern + "|" + m.value for m in (_js_matches or []))
                        return FetchResult(
                            url=url,
                            final_url=url,
                            status_code=200,
                            content_type="text/html",
                            text=_js_text,
                            fetched_bytes=len(_js_html),
                            declared_length=-1,
                            elapsed_ms=_js_elapsed_ms,
                            error=None,
                            selected_transport="js",
                            transport_policy_reason="js_required",
                            transport_counters=_tc,
                            matched_patterns=_matched,
                        )
                    # nodriver failed → camoufox fallback
                    _js_html = await _fetch_with_camoufox(url, timeout=timeout_s)
                    if _js_html:
                        _js_text, _js_matches, _ = await process_html_payload(_js_html, url)
                        _js_elapsed_ms = (time.monotonic() - t0) * 1000
                        _tc.js_renderer_count += 1
                        logger.info("Camoufox succeeded for {url} (curl_cffi fallback)", url=url)
                        _matched = tuple((m.label or "") + "|" + m.pattern + "|" + m.value for m in (_js_matches or []))
                        return FetchResult(
                            url=url,
                            final_url=url,
                            status_code=200,
                            content_type="text/html",
                            text=_js_text,
                            fetched_bytes=len(_js_html),
                            declared_length=-1,
                            elapsed_ms=_js_elapsed_ms,
                            error=None,
                            selected_transport="js",
                            transport_policy_reason="js_required",
                            transport_counters=_tc,
                            matched_patterns=_matched,
                        )
                    # camoufox failed → playwright last resort
                    _js_html = await _fetch_with_playwright(url, timeout=timeout_s)
                    if _js_html:
                        _js_text, _js_matches, _ = await process_html_payload(_js_html, url)
                        _js_elapsed_ms = (time.monotonic() - t0) * 1000
                        _tc.js_renderer_count += 1
                        logger.info("Playwright succeeded for {url} (curl_cffi fallback)", url=url)
                        _matched = tuple((m.label or "") + "|" + m.pattern + "|" + m.value for m in (_js_matches or []))
                        return FetchResult(
                            url=url,
                            final_url=url,
                            status_code=200,
                            content_type="text/html",
                            text=_js_text,
                            fetched_bytes=len(_js_html),
                            declared_length=-1,
                            elapsed_ms=_js_elapsed_ms,
                            error=None,
                            selected_transport="js",
                            transport_policy_reason="js_required",
                            transport_counters=_tc,
                            matched_patterns=_matched,
                        )

            # curl_cffi result returned (either no JS need, or JS rendering failed → return static)
            # F265C: curl_cffi is T0 — always-on per [TP-1]; fallback to aiohttp respects policy decision
            elapsed_ms = (time.monotonic() - t0) * 1000
            _curl_final_url = _curl_result.get("final_url", url)
            _curl_redirected, _curl_redirect_target = _derive_redirect_fields(url, _curl_final_url)
            _tc.curl_cffi_count += 1
            return FetchResult(
                url=url,
                final_url=_curl_final_url,
                status_code=_curl_result.get("status_code", 0),
                content_type=_curl_result.get("content_type", ""),
                text=_curl_text,
                fetched_bytes=len(_curl_bytes),
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=_curl_error,
                decode_replaced=_curl_decode_replaced,
                decode_replacement_count=_curl_decode_replacement_count,
                redirected=_curl_redirected,
                redirect_target=_curl_redirect_target,
                failure_stage=_curl_result.get("failure_stage", None),
                network_error_kind=_curl_result.get("network_error_kind", None),
                selected_transport="curl_cffi",
                http_version=None,  # curl_cffi doesn't expose HTTP version
                transport_policy_reason=_router_reason,
                transport_fallback_reason=None,
                transport_counters=_tc,
                body=_curl_bytes,  # F266A: raw bytes preserved for Arrow zero-copy
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raise
        except Exception as _curl_e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            # F265C: T0 failure → aiohttp fallback (policy enforced via _t3_allowed/_h2_allowed in this function)
            logger.warning(
                "[curl_cffi] stealth lane failed for {url}, falling back to aiohttp: {_curl_e}",
                url=url,
                _curl_e=_curl_e,
            )
            _curl_fallback_reason = f"curl_cffi_failed:{type(_curl_e).__name__}"
            _tc.curl_cffi_fallback_to_aiohttp_count += 1
            _tc.fallback_count += 1

    # --- F251: curl_cffi Tor fetch path for .onion URLs ---
    # Activated by HLEDAC_ENABLE_TOR=1 or when URL is .onion and Tor curl available
    # B1: _url_kind already computed in PHASE 2 — reuse it, no re-classification.
    if use_tor and ENV.get_bool("HLEDAC_ENABLE_TOR") and _url_kind == "onion":
        try:
            _stealth_headers = build_randomized_headers()
            _tor_curl_result = await fetch_via_tor_curl_cffi(
                url=url,
                headers=_stealth_headers,
                timeout_s=timeout_s * TOR_STEALTH_TIMEOUT_SCALE,
                max_bytes=max_bytes,
                profile="chrome110",
                tor_manager=None,  # circuit rotation via TOR_SOCKS_PROXY env + request counter
                circuit_rotation_count=TOR_CIRCUIT_RENEWAL_REQUEST_COUNT,
            )
            _tor_curl_bytes = _tor_curl_result.get("content", b"")
            _tor_curl_text: str | None
            _tor_curl_decode_replaced = False
            _tor_curl_decode_replacement_count = 0
            _tor_curl_error = _tor_curl_result.get("error", None)
            if _tor_curl_bytes:
                _tor_curl_text, _tor_curl_decode_replaced, _tor_curl_decode_replacement_count = _try_decode(
                    _tor_curl_bytes
                )  # noqa: E501
            else:
                _tor_curl_text = None
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.curl_cffi_tor_count += 1
            if _tor_curl_text and not _tor_curl_error:
                _store_body_hash(url, _compute_body_hash(_tor_curl_text.encode("utf-8", errors="replace")))
            return FetchResult(
                url=url,
                final_url=_tor_curl_result.get("final_url", url),
                status_code=_tor_curl_result.get("status_code", 0),
                content_type=_tor_curl_result.get("content_type", ""),
                text=_tor_curl_text,
                fetched_bytes=len(_tor_curl_bytes) if _tor_curl_bytes else 0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=_tor_curl_error,
                decode_replaced=_tor_curl_decode_replaced,
                decode_replacement_count=_tor_curl_decode_replacement_count,
                failure_stage=_tor_curl_result.get("failure_stage", None),
                network_error_kind=_tor_curl_result.get("network_error_kind", None),
                selected_transport="curl_cffi_tor",
                transport_policy_reason="tor_curl_cffi",
                transport_counters=_tc,
            )
        except asyncio.CancelledError:
            raise
        except Exception as _tor_curl_e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                "[curl_cffi_tor] onion fetch failed for {url}, falling back to aiohttp_socks: {_tor_curl_e}",
                url=url,
                _tor_curl_e=_tor_curl_e,
            )
            _tc.curl_cffi_tor_fallback_count += 1
            # fall through to aiohttp_socks path
    elif use_tor and _url_kind == "onion":
        # .onion URL without HLEDAC_ENABLE_TOR → skip with warning
        logger.warning("onion_url_skipped: tor_not_enabled {url}", url=url)

    # --- P4: Tor session setup for .onion URLs ---
    tor_session = None  # Always defined for use_tor check below
    if use_tor:
        try:
            tor_session = await _get_tor_session()
        except RuntimeError as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.tor_httpx_socks_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=f"tor_unavailable;{type(e).__name__};{e}",
                failure_stage="connection",
                selected_transport="httpx_socks",
                transport_policy_reason="darknet_url",
                transport_counters=_tc,
            )

    # --- P10: I2P session setup for .i2p/.b32.i2p URLs ---
    i2p_session = None  # Always defined for use_i2p check below
    if use_i2p:
        try:
            i2p_session = await _get_i2p_session()
        except RuntimeError as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            _tc.i2p_httpx_socks_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=f"i2p_unavailable;{type(e).__name__};{e}",
                failure_stage="connection",
                selected_transport="httpx_socks",
                transport_policy_reason="darknet_url",
                transport_counters=_tc,
            )

    # --- Retryable status tracking ---
    retry_after: float | None = None
    last_status_code: int = 0
    last_error: str | None = None

    for attempt in range(MAX_RETRIES + 1):
        # P4: Apply jitter before each request (Tor/stealth/I2P anti-correlation)
        if use_tor or use_i2p:
            await _jitter_delay()
        elif stealth_session is not None:
            # Canonical stealth: timing variance via StealthSession
            await stealth_session.apply_jitter()

        # P4: Maybe renew Tor circuit every N requests
        if use_tor:
            await _maybe_renew_tor_circuit()

        # F273G-H3FIX: When H3 is enabled and this is a clearnet fetch,
        # use curl_cffi with blocking pre-probe for H3 support.
        # aiohttp itself does NOT support HTTP/3, so without this the
        # aiohttp path always falls back to HTTP/1.1/2.
        _use_curl_cffi_for_h3 = not use_tor and not use_i2p and _h3_allowed and ENV.get_bool("HLEDAC_ENABLE_CURL_CFFI")
        if _use_curl_cffi_for_h3:
            _curl_h3_version = _altsvc_http_version_for(_altsvc_extract_host(url, _url_host))
            if _curl_h3_version is not None:
                # Host advertises H3 via Alt-Svc — use curl_cffi with H3.
                _curl_h3_result = await fetch_via_curl_cffi_cached(
                    url=url,
                    headers=None,
                    timeout_s=timeout_s,
                    max_bytes=max_bytes,
                    profile="chrome110",
                    http_version=_curl_h3_version,
                    _pre_probe=False,  # Already warm from _altsvc_http_version_for above
                )
                _curl_h3_bytes = _curl_h3_result.get("content", b"")
                _curl_h3_text: str | None
                if _curl_h3_bytes:
                    _curl_h3_text = _curl_h3_bytes.decode("utf-8", errors="replace")
                else:
                    _curl_h3_text = None
                _curl_h3_elapsed_ms = (time.monotonic() - t0) * 1000
                _curl_h3_final_url = _curl_h3_result.get("final_url", url)
                _curl_h3_redirected, _curl_h3_redirect_target = _derive_redirect_fields(url, _curl_h3_final_url)
                _curl_h3_decode_replaced = False
                _curl_h3_decode_replacement_count = 0
                if _curl_h3_text:
                    _store_body_hash(url, _compute_body_hash(_curl_h3_text.encode("utf-8", errors="replace")))
                return FetchResult(
                    url=url,
                    final_url=_curl_h3_final_url,
                    status_code=_curl_h3_result.get("status_code", 0),
                    content_type=_curl_h3_result.get("content_type", ""),
                    text=_curl_h3_text,
                    fetched_bytes=len(_curl_h3_bytes),
                    declared_length=-1,
                    elapsed_ms=_curl_h3_elapsed_ms,
                    error=_curl_h3_result.get("error", None),
                    decode_replaced=_curl_h3_decode_replaced,
                    decode_replacement_count=_curl_h3_decode_replacement_count,
                    redirected=_curl_h3_redirected,
                    redirect_target=_curl_h3_redirect_target,
                    failure_stage=_curl_h3_result.get("failure_stage", None),
                    network_error_kind=_curl_h3_result.get("network_error_kind", None),
                    selected_transport="curl_cffi",
                    http_version="h3",
                    transport_policy_reason="h3_clearnet",
                    transport_counters=_tc,
                )
            # H3 not available for this host — fall through to httpx.

        session = tor_session if use_tor else (i2p_session if use_i2p else await async_get_aiohttp_session())
        # F191B: Use separate semaphore pools — Tor/I2P cannot starve clearnet
        _semaphore = get_tor_semaphore() if (use_tor or use_i2p) else get_clearnet_semaphore()
        # Canonical stealth: use StealthSession UA rotation
        if stealth_session is not None:
            headers = {"User-Agent": stealth_session.rotate_ua()}
        else:
            headers = {"User-Agent": DEFAULT_UA}

        # P16: DoH resolution provides IP for logging/fallback but does NOT
        # override the Host header. The Host header must always be derived
        # from the URL's hostname to prevent host header injection.
        # P1-5: SSRF NOTE - aiohttp auto-follows redirects without validating
        # redirect targets. This is a known gap; httpx path has manual redirect
        # validation. The aiohttp path trusts the OS-level DNS which provides
        # some protection against DNS rebinding, but explicit redirect URL
        # validation would be safer. For now, auto-redirect is kept to avoid
        # breaking functionality; SSRF risk is partially mitigated by:
        #   1. OS DNS resolution returning public IPs for legitimate domains
        #   2. Tor/I2P sessions routing through proxies that block private IPs
        #   3. fetch_coordinator._validate_fetch_target() validating initial URL
        request_kwargs: dict = {"headers": headers, "allow_redirects": True}

        # F191B: Lightweight backpressure when RAM > M1_FETCH_SOFT_CEILING_GB — don't resize semaphore, just slow down
        # Sprint F206AL: 5.5GB ceiling now unified via uma_budget.M1_FETCH_SOFT_CEILING_GB
        if not use_tor and not use_i2p and not use_stealth:
            try:
                _ps = _psutil_process()
                if _ps is not None:
                    rss_gb = _ps.memory_info().rss / 1e9
                    if rss_gb > M1_FETCH_SOFT_CEILING_GB:
                        await asyncio.sleep(0.05)
            except Exception as e:
                logger.debug("Memory check failed (non-fatal): {e}", e=e)

        try:
            async with asyncio.timeout(timeout_s):
                async with _semaphore:
                    # --- F214Q: Timing jitter — non-blocking, fail-soft ---
                    if ENV.get_bool("HLEDAC_ENABLE_STEALTH_LAYER"):
                        try:
                            from layers import get_stealth_layer

                            _sl = get_stealth_layer()
                            if _sl:
                                await asyncio.sleep(_sl.get_timing_jitter())
                        except Exception:  # noqa: BLE001
                            pass  # noqa: BLE001  # fail-soft
                    async with session.get(url, **request_kwargs) as resp:  # type: ignore[union-attr]
                        final_url = str(resp.url)
                        last_status_code = resp.status
                        content_type = resp.headers.get("Content-Type", "") or ""
                        raw_content_type = content_type.split(";")[0].strip().lower()

                        # --- F206AJ: 403/429 one-shot curl_cffi escalation ---
                        # One-shot: aiohttp got 403/429 → try curl_cffi once before retry/body.
                        # Protected: darknet/JS/Freenet already handled upstream.
                        # No loop: escalation only on first attempt (attempt==0).
                        _escalated_to_curl = False
                        if last_status_code in (403, 429) and attempt == 0:
                            _env_curl = ENV.get("HLEDAC_ENABLE_CURL_CFFI")
                            if _env_curl == "1":
                                _esc_use_curl, _esc_curl_reason = should_use_curl_cffi(
                                    url, use_stealth=use_stealth, use_js=use_js, prior_status=last_status_code
                                )
                                if _esc_use_curl:
                                    try:
                                        # F260+: opportunistic HTTP/3 (QUIC) upgrade via Alt-Svc cache.
                                        _esc_http_version = _altsvc_http_version_for(
                                            _altsvc_extract_host(url, _url_host)
                                        )
                                        # F265B: conditional-cache wrapper.
                                        # The URL already 403/429'd in the prior
                                        # httpx attempt, so the cache is likely
                                        # cold here, but if a previous sprint
                                        # cached it we still save the RTT.
                                        _esc_result = await fetch_via_curl_cffi_cached(
                                            url=url,
                                            headers=None,
                                            timeout_s=timeout_s,
                                            max_bytes=max_bytes,
                                            profile="chrome110",
                                            http_version=_esc_http_version,
                                            _pre_probe=True,  # F273G-H3FIX: block to prime H3 LRU before fallback fetch
                                        )
                                        # Record Alt-Svc h3 advertisement for future fetches to this host.
                                        _altsvc_record_from_result(url, _esc_result.get("headers"))
                                        if _esc_result.get("status_code", 0) // 100 == 2:
                                            # curl succeeded with 2xx → return immediately
                                            _escalated_to_curl = True
                                            _tc.curl_cffi_count += 1
                                            _esc_bytes = _esc_result.get("content", b"")
                                            _esc_text: str | None
                                            _esc_decode_replaced = False
                                            _esc_decode_replacement_count = 0
                                            if _esc_bytes:
                                                # F261: STORAGE-FIX-4 wiring — charset_normalizer chain
                                                # (decode_response_bytes) with _try_decode fallback.
                                                # Charset hint parsed from curl_cffi content-type header.
                                                _esc_charset = parse_charset_from_content_type(
                                                    _esc_result.get("content_type", "")
                                                )
                                                _esc_text, _esc_decode_replaced, _esc_decode_replacement_count = (
                                                    _try_decode_with_charset(  # noqa: E501
                                                        _esc_bytes,
                                                        http_charset=_esc_charset,
                                                    )
                                                )
                                            else:
                                                _esc_text = None
                                            _esc_elapsed_ms = (time.monotonic() - t0) * 1000
                                            _esc_final_url = _esc_result.get("final_url", url)
                                            _esc_redirected, _esc_redirect_target = _derive_redirect_fields(
                                                url, _esc_final_url
                                            )  # noqa: E501
                                            return FetchResult(
                                                url=url,
                                                final_url=_esc_final_url,
                                                status_code=_esc_result.get("status_code", 0),
                                                content_type=_esc_result.get("content_type", ""),
                                                text=_esc_text,
                                                fetched_bytes=len(_esc_bytes),
                                                declared_length=-1,
                                                elapsed_ms=_esc_elapsed_ms,
                                                error=_esc_result.get("error", None),
                                                decode_replaced=_esc_decode_replaced,
                                                decode_replacement_count=_esc_decode_replacement_count,
                                                redirected=_esc_redirected,
                                                redirect_target=_esc_redirect_target,
                                                failure_stage=_esc_result.get("failure_stage", None),
                                                network_error_kind=_esc_result.get("network_error_kind", None),
                                                selected_transport="curl_cffi",
                                                http_version=None,
                                                transport_policy_reason=_router_reason,
                                                transport_fallback_reason="aiohttp_status_403_or_429_to_curl_cffi",
                                                transport_counters=_tc,
                                            )
                                        else:
                                            # curl returned non-2xx → fall through to aiohttp retry
                                            _curl_fallback_reason = (
                                                f"curl_cffi_status_{_esc_result.get('status_code', 0)}_to_aiohttp"  # noqa: E501
                                            )
                                            _tc.curl_cffi_fallback_to_aiohttp_count += 1
                                            _tc.fallback_count += 1
                                    except asyncio.CancelledError:
                                        raise
                                    except Exception as _esc_e:
                                        _curl_fallback_reason = f"curl_cffi_failed:{type(_esc_e).__name__}"
                                        _tc.curl_cffi_fallback_to_aiohttp_count += 1
                                        _tc.fallback_count += 1

                        # --- Retryable status → wait and retry once ---
                        if _circuit_breaker and _is_retryable_status(last_status_code):
                            _circuit_breaker.record_failure(failure_kind=str(last_status_code))
                            last_error = _build_retry_error(last_status_code, retry_after)
                            if attempt < MAX_RETRIES:
                                retry_after = _extract_retry_after(resp.headers)
                                backoff = _compute_backoff_seconds(retry_after, attempt)
                                await asyncio.sleep(backoff)
                                continue
                        # --- Content-type gate with XML-ish body recovery (Feed ingress hardening F164A) ---
                        xml_recovered = False
                        rejected_ct = raw_content_type not in ACCEPTED_CONTENT_TYPES

                        raw_declared = resp.headers.get("Content-Length")
                        try:
                            declared_length = int(raw_declared) if raw_declared else -1
                        except (ValueError, TypeError):
                            declared_length = -1

                        # --- F226B: Chunked body read delegated to body_limiter helpers ---
                        # Peek on first chunk (for CT recovery), then read remainder with cap.
                        # The previous inline loop (~90 lines) duplicated body_limiter logic;
                        # this refactor splits the two concerns and reuses the helper.
                        total_read = 0
                        accumulated_ok = True
                        first_chunk_peeked = False

                        # F226B Phase 1: Peek at the first chunk for XML recovery.
                        # The full body read below will re-include this chunk in body.
                        if rejected_ct:
                            first_chunk_peeked = True
                            is_xmlish, first_chunk = await _peek_aiohttp_first_chunk(resp.content.iter_chunked(65536))
                            if is_xmlish:
                                # Feed ingress recovery: wrong CT but XML body — accept it
                                xml_recovered = True
                            else:
                                # First chunk is not XML-ish under wrong CT: reject fast
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                redirected, redirect_target = _derive_redirect_fields(url, final_url)
                                if _httpx_fallback_reason == "httpx_h2_fallback":
                                    _tc.httpx_h2_fallback_to_aiohttp_count += 1
                                    _tc.fallback_count += 1
                                elif _curl_fallback_reason is not None:
                                    _tc.fallback_count += 1
                                elif use_tor:
                                    _tc.tor_httpx_socks_count += 1
                                elif use_i2p:
                                    _tc.i2p_httpx_socks_count += 1
                                else:
                                    _tc.aiohttp_count += 1
                                return FetchResult(
                                    url=url,
                                    final_url=final_url,
                                    status_code=last_status_code,
                                    content_type=content_type,
                                    text=None,
                                    fetched_bytes=0,
                                    declared_length=declared_length,
                                    elapsed_ms=elapsed_ms,
                                    error=f"content_type_rejected:{raw_content_type}",
                                    redirected=redirected,
                                    redirect_target=redirect_target,
                                    failure_stage="http",
                                    selected_transport="httpx_h2"
                                    if _use_httpx_h2
                                    else ("httpx_socks" if (use_tor or use_i2p) else "httpx_h2"),  # noqa: E501
                                    transport_policy_reason=_router_reason
                                    if _use_httpx_h2
                                    else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),  # noqa: E501
                                    transport_fallback_reason="httpx_h2_fallback"
                                    if _httpx_fallback_reason == "httpx_h2_fallback"
                                    else None,  # noqa: E501
                                    transport_counters=_tc,
                                )

                        # F226B Phase 2: Read remainder with size cap. If we already peeked,
                        # prepend the first chunk to the stream so the helper sees the full body.
                        async def _read_with_first_chunk() -> AiohttpBodyOutcome:
                            if not first_chunk_peeked:  # noqa: B023
                                # P15: try/finally ensures iter_chunked is closed on cancel/exception
                                iter_chunks = resp.content.iter_chunked(8192)
                                try:
                                    return await _read_aiohttp_body_with_peek(iter_chunks, max_bytes, enable_peek=False)
                                finally:
                                    await _aclose_aiohttp_stream(iter_chunks)

                            # Already consumed first chunk — prepend it via a chain.
                            async def _prepended() -> AsyncGenerator[bytes | None]:  # type: ignore[return]
                                yield first_chunk  # noqa: B023
                                # P15: wrap inner iter_chunked for cleanup on early break
                                inner = resp.content.iter_chunked(65536)
                                try:
                                    async for c in inner:
                                        yield c
                                finally:
                                    await _aclose_aiohttp_stream(inner)

                            return await _read_aiohttp_body_with_peek(
                                _prepended(),
                                max_bytes,
                                enable_peek=False,  # type: ignore[arg-type]
                            )

                        outcome = await _read_with_first_chunk()
                        total_read = outcome.total_read
                        accumulated_ok = not outcome.truncated

                        if outcome.truncated:
                            elapsed_ms = (time.monotonic() - t0) * 1000
                            redirected, redirect_target = _derive_redirect_fields(url, final_url)
                            if _httpx_fallback_reason == "httpx_h2_fallback":
                                _tc.httpx_h2_fallback_to_aiohttp_count += 1
                                _tc.fallback_count += 1
                            elif _curl_fallback_reason is not None:
                                _tc.fallback_count += 1
                            elif use_tor:
                                _tc.tor_httpx_socks_count += 1
                            elif use_i2p:
                                _tc.i2p_httpx_socks_count += 1
                            else:
                                _tc.aiohttp_count += 1
                            return FetchResult(
                                url=url,
                                final_url=final_url,
                                status_code=last_status_code,
                                content_type=content_type,
                                text=None,
                                fetched_bytes=total_read,
                                declared_length=declared_length,
                                elapsed_ms=elapsed_ms,
                                error="size_cap_exceeded",
                                redirected=redirected,
                                redirect_target=redirect_target,
                                failure_stage="size",
                                selected_transport="httpx_h2"
                                if _use_httpx_h2
                                else ("httpx_socks" if (use_tor or use_i2p) else "httpx_h2"),  # noqa: E501
                                transport_policy_reason=_router_reason
                                if _use_httpx_h2
                                else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),  # noqa: E501
                                transport_fallback_reason="httpx_h2_fallback"
                                if _httpx_fallback_reason == "httpx_h2_fallback"
                                else None,  # noqa: E501
                                transport_counters=_tc,
                            )

                        if accumulated_ok and outcome.body:
                            try:
                                # F226B: body already collected by _read_aiohttp_body_with_peek.
                                body_bytes = outcome.body
                                # F261: STORAGE-FIX-4 wiring — charset_normalizer chain
                                # (decode_response_bytes) with _try_decode fallback.
                                # Charset hint from aiohttp response header (parsed from
                                # Content-Type) for accuracy on non-UTF-8 OSINT sources.
                                _charset_hint = parse_charset_from_content_type(content_type)
                                text, decode_replaced, decode_replacement_count = _try_decode_with_charset(
                                    body_bytes,
                                    http_charset=_charset_hint,
                                )
                                # --- F214Q: ContentLayer HTML cleaning — fail-soft ---
                                if text and ENV.get_bool("HLEDAC_ENABLE_CONTENT_LAYER"):
                                    try:
                                        from layers import get_content_layer

                                        _cl = get_content_layer()
                                        if _cl:
                                            _cleaned = _cl.clean_html(text)  # type: ignore[attr-defined]
                                            # preserve cleaned text if successful
                                            if _cleaned and _cleaned.content:  # type: ignore[attr-defined]
                                                text = _cleaned.content  # type: ignore[attr-defined]
                                    except Exception:  # noqa: BLE001
                                        pass  # noqa: BLE001  # fail-soft: preserve original text
                            except Exception as e:
                                logger.warning("Decode error in _try_decode: %s", e)
                                text = None
                                decode_replaced = False
                                decode_replacement_count = 0
                        else:
                            text = None
                            decode_replaced = False
                            decode_replacement_count = 0

                        # P7: Auto-detect JS need and retry via Camoufox → nodriver
                        # F207F: Skip JS retry for feed URLs, XML content-types, or when all JS renderers unavailable
                        skip_js_reason: str | None = None
                        if (
                            text
                            and not use_js
                            and _needs_js_fetch(
                                text,
                                url=url,
                                content_length=len(body_bytes) if body_bytes else 0,
                                declared_length=declared_length,
                            )
                        ):
                            if _all_js_renderers_unavailable():
                                # Use first unavailable reason for telemetry
                                cap = _get_js_renderer_capability()
                                unavailable_reasons = [v for v in cap.values() if v is not None]
                                skip_js_reason = (
                                    unavailable_reasons[0] if unavailable_reasons else "all_js_renderers_unavailable"
                                )  # noqa: E501
                            elif _looks_like_feed_url(url):
                                skip_js_reason = "xml_or_feed_url"
                            elif xml_recovered:
                                skip_js_reason = "xml_recovered"

                            # F214Y: Try static hydration before expensive JS rendering
                            from hledac.universal.utils.hydration_extractor import (
                                extract_static_hydration as _extract_static_hydration,
                            )

                            hydration = _extract_static_hydration(text)
                            # F214Z: Update hydration counters
                            _tc.static_hydration_attempted += 1
                            if hydration.sufficient:
                                _tc.static_hydration_sufficient += 1
                                # Use static result — skip JS renderer entirely
                                logger.info(f"Static hydration sufficient for {url}: reason={hydration.reason}")
                                skip_js_reason = f"static_hydration_sufficient:{hydration.reason}"
                            elif hydration.found:
                                _tc.static_hydration_insufficient += 1
                                # Hydration found but not sufficient — proceed to JS rendering
                                logger.debug(f"Static hydration found but insufficient for {url}: {hydration.reason}")
                                skip_js_reason = None
                            else:
                                # No hydration found — proceed to JS rendering
                                skip_js_reason = None

                            if skip_js_reason and skip_js_reason.startswith("static_hydration_sufficient"):
                                # Return enriched result from static extraction — NO js_renderer_count increment
                                # because static hydration is NOT a JS/browser renderer
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                # F229: Extract HTML metadata from original text for static hydration path
                                # F256K: offload sync CPU-bound extraction to thread pool
                                from utils.rayon_pool import run_in_cpu_pool_async

                                _meta = await run_in_cpu_pool_async(extract_html_metadata, text or "")
                                _static_sources = (
                                    list(hydration.sources)
                                    if hasattr(hydration, "sources")
                                    else list(hydration.sources)
                                )  # noqa: E501
                                if _meta["ga_gtm_ids"]:
                                    _static_sources.append("ga_gtm")
                                if _meta["og_tags"]:
                                    _static_sources.append("og_tags")
                                if _meta["comments"]:
                                    _static_sources.append("html_comments")
                                return FetchResult(
                                    url=url,
                                    final_url=final_url,
                                    status_code=last_status_code,
                                    content_type=content_type,
                                    text=hydration.text if hydration.text else text,
                                    fetched_bytes=total_read,
                                    declared_length=declared_length,
                                    elapsed_ms=elapsed_ms,
                                    error=None,
                                    xml_recovered=xml_recovered,
                                    xml_source_hint=xml_recovered,
                                    decode_replaced=decode_replaced,
                                    decode_replacement_count=decode_replacement_count,
                                    redirected=redirected,
                                    redirect_target=redirect_target,
                                    selected_transport=_actual_transport,
                                    http_version="http/1.1",
                                    transport_policy_reason=_router_reason if _use_httpx_h2 else "clearnet_default",
                                    transport_fallback_reason=_fallback_info,
                                    transport_counters=_tc,
                                    js_renderer_skipped_reason=skip_js_reason,
                                    hydration_score=hydration.hydration_score,
                                    hydration_sources=tuple(_static_sources),
                                )

                            # F214AC: Try macOS WKWebView renderer BEFORE heavy browser (camoufox/nodriver)
                            # Order: 1. normal HTTP → 2. static hydration → 3. WKWebView → 4. heavy browser
                            from hledac.universal.rendering.macos_webkit_renderer import fetch_with_macos_webkit

                            wkr = await fetch_with_macos_webkit(url, timeout_s=timeout_s)
                            if wkr.ok and wkr.html:
                                js_text, js_matches, js_metadata = await process_html_payload(wkr.html, url)
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                _tc.js_renderer_count += 1
                                _tc.macos_webkit_count += 1
                                _ga_ids = js_metadata.get("ga_gtm_ids", ())
                                _og = js_metadata.get("og_tags", ())
                                _cmts = js_metadata.get("comments", ())
                                _addl_sources = list(hydration.sources) if hasattr(hydration, "sources") else []  # noqa: E501
                                if _ga_ids:
                                    _addl_sources.append("ga_gtm")
                                if _og:
                                    _addl_sources.append("og_tags")
                                if _cmts:
                                    _addl_sources.append("html_comments")
                                _matched = tuple(
                                    (m.label or "") + "|" + m.pattern + "|" + m.value for m in (js_matches or [])
                                )
                                return FetchResult(
                                    url=url,
                                    final_url=url,
                                    status_code=200,
                                    content_type="text/html",
                                    text=js_text,
                                    fetched_bytes=wkr.rendered_bytes,
                                    declared_length=-1,
                                    elapsed_ms=elapsed_ms,
                                    error=None,
                                    selected_transport="js",
                                    transport_policy_reason="js_required",
                                    transport_counters=_tc,
                                    hydration_sources=tuple(_addl_sources),
                                    matched_patterns=_matched,
                                )

                            # WKWebView unavailable or failed — fall through to heavy browser (camoufox → nodriver)
                            # F214AC: Surface WKWebView failure in telemetry if it was tried but failed
                            wkr_reason = wkr.reason if wkr else "macos_webkit_unavailable"
                            if wkr_reason not in (
                                "macos_webkit_success",
                                "macos_webkit_non_darwin",
                                "macos_webkit_unavailable",
                            ):
                                # WKWebView was available (darwin) but failed — record it
                                logger.warning(
                                    "WKWebView render failed ({wkr_reason}), falling back to heavy browser: {url}",
                                    wkr_reason=wkr_reason,
                                    url=url,
                                )

                            logger.info("JS need detected, retrying with Camoufox: {url}", url=url)
                            js_html = await _fetch_with_camoufox(url, timeout=timeout_s)
                            if js_html:
                                # Process JS-rendered HTML
                                js_text, js_matches, _ = await process_html_payload(js_html, url)
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                _tc.js_renderer_count += 1
                                _matched = tuple(
                                    (m.label or "") + "|" + m.pattern + "|" + m.value for m in (js_matches or [])
                                )
                                return FetchResult(
                                    url=url,
                                    final_url=url,
                                    status_code=200,
                                    content_type="text/html",
                                    text=js_text,
                                    fetched_bytes=len(js_html),
                                    declared_length=-1,
                                    elapsed_ms=elapsed_ms,
                                    error=None,
                                    selected_transport="js",
                                    transport_policy_reason="js_required",
                                    transport_counters=_tc,
                                    matched_patterns=_matched,
                                )
                            # Camoufox failed → try nodriver fallback
                            logger.warning("Camoufox failed, trying nodriver: {url}", url=url)
                            js_html = await _fetch_with_nodriver(url, _url_kind, _url_host)
                            if js_html:
                                js_text, js_matches, _ = await process_html_payload(js_html, url)
                                elapsed_ms = (time.monotonic() - t0) * 1000
                                _tc.js_renderer_count += 1
                                _matched = tuple(
                                    (m.label or "") + "|" + m.pattern + "|" + m.value for m in (js_matches or [])
                                )
                                return FetchResult(
                                    url=url,
                                    final_url=url,
                                    status_code=200,
                                    content_type="text/html",
                                    text=js_text,
                                    fetched_bytes=len(js_html),
                                    declared_length=-1,
                                    elapsed_ms=elapsed_ms,
                                    error=None,
                                    selected_transport="js",
                                    transport_policy_reason="js_required",
                                    transport_counters=_tc,
                                    matched_patterns=_matched,
                                )
                                # F207F: Both JS renders failed — update capability tracking
                                if not js_html:
                                    # Set all renderers as unavailable with reasons
                                    cap = _get_js_renderer_capability()
                                    logger.warning("All JS renders failed for {url}, returning aiohttp result", url=url)

                        elapsed_ms = (time.monotonic() - t0) * 1000
                        if _circuit_breaker and last_status_code >= 200 and last_status_code < 300:
                            _circuit_breaker.record_success()
                        redirected, redirect_target = _derive_redirect_fields(url, final_url)
                        # Determine actual transport used
                        _actual_transport = "httpx_h2" if _use_httpx_h2 else "httpx_h2"
                        _fallback_info: str | None = None
                        # curl_cffi fallback takes priority — set when curl lane failed and aiohttp succeeded
                        if _curl_fallback_reason:
                            _fallback_info = _curl_fallback_reason
                        elif not _use_httpx_h2 and _httpx_fallback_reason == "httpx_h2_fallback":
                            _fallback_info = "httpx_h2_fallback"
                        # --- F206N: Transport counter for aiohttp success ---
                        if _curl_fallback_reason:
                            # curl fallback counter already incremented in curl except block
                            pass
                        elif _fallback_info == "httpx_h2_fallback":
                            _tc.httpx_h2_fallback_to_aiohttp_count += 1
                            _tc.fallback_count += 1
                        elif use_tor:
                            _tc.tor_httpx_socks_count += 1
                        elif use_i2p:
                            _tc.i2p_httpx_socks_count += 1
                        else:
                            _tc.aiohttp_count += 1
                        if text:
                            _store_body_hash(url, _compute_body_hash(text.encode("utf-8", errors="replace")))
                        # F274: Run pattern matching on decoded text to avoid re-matching in pipeline
                        from utils.rayon_pool import run_in_cpu_pool_async

                        _aiohttp_matches = await run_in_cpu_pool_async(match_text, text or "")
                        _aiohttp_matched = tuple(
                            (m.label or "") + "|" + m.pattern + "|" + m.value for m in (_aiohttp_matches or [])
                        )
                        return FetchResult(
                            url=url,
                            final_url=final_url,
                            status_code=last_status_code,
                            content_type=content_type,
                            text=text,
                            fetched_bytes=total_read,
                            declared_length=declared_length,
                            elapsed_ms=elapsed_ms,
                            error=None,
                            xml_recovered=xml_recovered,
                            xml_source_hint=xml_recovered,  # F178E: xml_source_hint mirrors xml_recovered
                            decode_replaced=decode_replaced,
                            decode_replacement_count=decode_replacement_count,  # F178E
                            redirected=redirected,
                            redirect_target=redirect_target,
                            selected_transport=_actual_transport,
                            http_version="http/1.1",  # aiohttp always HTTP/1.1
                            transport_policy_reason=_router_reason if _use_httpx_h2 else "clearnet_default",
                            transport_fallback_reason=_fallback_info,
                            transport_counters=_tc,
                            js_renderer_skipped_reason=skip_js_reason,  # F207F
                            body=body_bytes,  # F266A: raw bytes preserved for Arrow zero-copy
                            # F274: Carry matched patterns from aiohttp fetch
                            matched_patterns=_aiohttp_matched,
                        )

        except TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if _circuit_breaker:
                _circuit_breaker.record_failure(is_timeout=True, failure_kind="timeout")
            # --- F206N: Transport counter for timeout ---
            if _curl_fallback_reason:
                pass  # curl fallback counter already incremented
            elif _httpx_fallback_reason == "httpx_h2_fallback":
                _tc.httpx_h2_fallback_to_aiohttp_count += 1
                _tc.fallback_count += 1
            elif use_tor:
                _tc.tor_httpx_socks_count += 1
            elif use_i2p:
                _tc.i2p_httpx_socks_count += 1
            else:
                _tc.aiohttp_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error="timeout",
                failure_stage="connection",
                network_error_kind="timeout",
                selected_transport="httpx_h2"
                if _use_httpx_h2
                else ("httpx_socks" if (use_tor or use_i2p) else "httpx_h2"),  # noqa: E501
                transport_policy_reason=_router_reason
                if _use_httpx_h2
                else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),  # noqa: E501
                transport_fallback_reason="httpx_h2_fallback"
                if _httpx_fallback_reason == "httpx_h2_fallback"
                else None,  # noqa: E501
                transport_counters=_tc,
            )
        except asyncio.CancelledError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            raise
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.monotonic() - t0) * 1000
            if _circuit_breaker:
                _circuit_breaker.record_failure(failure_kind="fetch_error")
            err_str = f"fetch_error;{type(exc).__name__};{exc}"
            failure_stage, network_error_kind = _derive_failure_stage_and_network_kind(err_str)
            # body_read_error=True only when body stream was actually entered and failed.
            # For connection/tls/http stages the body was never reached — flag stays False.
            body_read_error = failure_stage in ("body", "size")
            # --- F206N: Transport counter for exception ---
            if _curl_fallback_reason:
                pass  # curl fallback counter already incremented
            elif _httpx_fallback_reason == "httpx_h2_fallback":
                _tc.httpx_h2_fallback_to_aiohttp_count += 1
                _tc.fallback_count += 1
            elif use_tor:
                _tc.tor_httpx_socks_count += 1
            elif use_i2p:
                _tc.i2p_httpx_socks_count += 1
            else:
                _tc.aiohttp_count += 1
            return FetchResult(
                url=url,
                final_url=url,
                status_code=0,
                content_type="",
                text=None,
                fetched_bytes=0,
                declared_length=-1,
                elapsed_ms=elapsed_ms,
                error=err_str,
                body_read_error=body_read_error,
                failure_stage=failure_stage,
                network_error_kind=network_error_kind,
                selected_transport="httpx_h2"
                if _use_httpx_h2
                else ("httpx_socks" if (use_tor or use_i2p) else "httpx_h2"),  # noqa: E501
                transport_policy_reason=_router_reason
                if _use_httpx_h2
                else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),  # noqa: E501
                transport_fallback_reason="httpx_h2_fallback"
                if _httpx_fallback_reason == "httpx_h2_fallback"
                else None,  # noqa: E501
                transport_counters=_tc,
            )

    # Should not reach here, but as safeguard (retry exhaustion after loop):
    elapsed_ms = (time.monotonic() - t0) * 1000
    err_str = last_error or "retry_exhausted"
    failure_stage, network_error_kind = _derive_failure_stage_and_network_kind(err_str)
    body_read_error = failure_stage in ("body", "size")
    # --- F206N: Transport counter (same logic as retry exhausted) ---
    if _curl_fallback_reason:
        pass  # curl fallback counter already incremented
    elif _httpx_fallback_reason == "httpx_h2_fallback":
        _tc.httpx_h2_fallback_to_aiohttp_count += 1
        _tc.fallback_count += 1
    elif use_tor:
        _tc.tor_httpx_socks_count += 1
    elif use_i2p:
        _tc.i2p_httpx_socks_count += 1
    else:
        _tc.aiohttp_count += 1
    return FetchResult(
        url=url,
        final_url=url,
        status_code=last_status_code,
        content_type="",
        text=None,
        fetched_bytes=0,
        declared_length=-1,
        elapsed_ms=elapsed_ms,
        error=err_str,
        body_read_error=body_read_error,
        failure_stage=failure_stage,
        network_error_kind=network_error_kind,
        selected_transport="httpx_h2" if _use_httpx_h2 else ("httpx_socks" if (use_tor or use_i2p) else "httpx_h2"),
        transport_policy_reason=_router_reason
        if _use_httpx_h2
        else ("darknet_url" if (use_tor or use_i2p) else "clearnet_default"),  # noqa: E501
        transport_fallback_reason="httpx_h2_fallback" if _httpx_fallback_reason == "httpx_h2_fallback" else None,
        transport_counters=_tc,
    )

    # Issue #046: reset request_id after fetch completes
    try:
        from core.telemetry.context_state import reset_request_id

        reset_request_id()
    except Exception:
        pass


__all__ = [
    "async_fetch_public_text",
    "process_html_payload",
    "DEFAULT_UA",
    "MAX_BYTES_DEFAULT",
    "MAX_BYTES_HARD",
    "MAX_RETRIES",
    "FetchResult",
    "_is_retryable_status",
    "_extract_retry_after",
    "_compute_backoff_seconds",
    "_try_decode",
    "_looks_xmlish",
    # P4: Tor + stealth helpers
    "_is_onion_url",
    "_get_tor_session",
    "_renew_tor_circuit",
    "_jitter_delay",
    "_close_tor_session",
    "TOR_SOCKS_PROXY",
    "TOR_CIRCUIT_RENEWAL_REQUEST_COUNT",
    "I2P_SOCKS_PROXY",
    # P10: I2P + Freenet helpers
    "_is_i2p_url",
    "_is_freenet_url",
    "_get_i2p_session",
    "_close_i2p_session",
    # P7: JS rendering helpers
    "_needs_js_fetch",
    "_fetch_with_nodriver",
    "_fetch_with_camoufox",
    "_fetch_with_playwright",
    "_get_js_renderer_capability",
    "_all_js_renderers_unavailable",
    "reset_js_renderer_capability_cache",
    "refresh_js_renderer_capability",
    # F206AT: Pool authority seam
    "PUBLIC_FETCHER_POOL_AUTHORITY",
    "inject_session_provider",
    "get_session_source_telemetry",
    # F219D: Public teardown surface
    "close_public_fetcher_sessions_async",
    "get_public_fetcher_session_status",
]

# ---------------------------------------------------------------------------
# HTML → text + pattern matching (CPU-bound, runs in shared CPU_EXECUTOR)
# ---------------------------------------------------------------------------
from hledac.universal.utils.html_text_fast import extract_html_metadata, html_to_text_fast  # noqa: E402


def _sync_process_html(html: str, url: str = "") -> tuple[str, list, dict]:
    """Synchronous CPU-bound HTML parsing + pattern matching + metadata extraction.

    Runs in CPU_EXECUTOR thread pool — never blocks the async event loop.
    Fail-safe: malformed HTML returns empty text, never raises.

    Returns:
        Tuple of (markdown-stripped text, pattern match list, metadata dict).
        metadata dict keys: ga_gtm_ids, og_tags, comments (from extract_html_metadata).
    """
    # Note: PatternMatcher is bootstrapped once at startup via
    # configure_default_bootstrap_patterns_if_empty() in pattern_matcher.py.
    # Re-configuring on every call wastes CPU — removed per F184B.

    # F229: Extract HTML metadata BEFORE text extraction
    metadata = extract_html_metadata(html)

    # F214OPT-A: selectolax-first HTML→text extraction
    text = html_to_text_fast(html)
    if not text:
        # Fail-soft: empty result on malformed HTML
        import html as _html

        text = strip_html_tags(_html.unescape(html))
        text = collapse_whitespace(text).strip()

    # Pattern scan
    matches = match_text(text)

    # R3.2: Rust lol_html zero-copy link extraction — byte-range indices into
    # input HTML, Python resolves URLs. ~60% less memory vs extract_links which
    # allocates Vec<String> per link. Fail-soft: pattern matches are authoritative.
    try:
        raw_ranges = rust_html.extract_links_zero_copy(html, url)
        for start, end in raw_ranges:
            href_str = html[start:end]
            resolved = urllib.parse.urljoin(url, href_str)
            if resolved.startswith(("http://", "https://")):
                matches.append(PatternHit(pattern="rust_link", start=0, end=0, value=resolved, label=""))
    except Exception:  # noqa: BLE001
        pass  # fail-soft

    return (text, matches, metadata)


# ---------------------------------------------------------------------------
# F275: Batch Rust HTML extraction — rayon-parallel link/email/title extraction
# ---------------------------------------------------------------------------


def _batch_sync_extract_html_metadata(
    items: list[tuple[str, str]],
) -> list[dict]:
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
            except Exception:  # noqa: BLE001
                pass  # fail-soft: return empty emails

        if rust_titles is not None:
            try:
                raw_titles = rust_titles(htmls)
                if raw_titles and len(raw_titles) == len(items):
                    titles_results = raw_titles
            except Exception:  # noqa: BLE001
                pass  # fail-soft: return None titles

        return [{"emails": e, "title": t} for e, t in zip(emails_results, titles_results, strict=True)]
    except Exception:
        return [{} for _ in items]


def _batch_sync_extract_links(
    items: list[tuple[str, str]],
) -> list[list[str]]:
    """R3.2: Batch extract links via Rust zero-copy lol_html.

    Args:
        items: List of (html, base_url) tuples. Cap 1_000 items.

    Returns:
        List of link lists, one per item, in same order as input.
    """
    if not items:
        return []

    try:
        from core.rust_backend import rust as _rust_backend

        results: list[list[str]] = [[] for _ in items]

        for i, (html, base_url) in enumerate(items):
            raw_ranges = _rust_backend.raw.extract_links_zero_copy(html, base_url)
            page_links: list[str] = []
            for start, end in raw_ranges:
                href_str = html[start:end]
                resolved = urllib.parse.urljoin(base_url, href_str)
                if resolved.startswith(("http://", "https://")):
                    page_links.append(resolved)
            results[i] = page_links
        return results
    except Exception:
        return [[] for _ in items]


async def process_html_payload(html: str, url: str) -> tuple[str, list, dict]:
    """Offload HTML→text+pattern matching+metadata extraction to shared CPU_EXECUTOR.

    Args:
        html: Raw HTML content.
        url: Source URL (for context in errors; not used for fetching).

    Returns:
        Tuple of (markdown-stripped text, pattern match list, metadata dict).
        metadata dict keys: ga_gtm_ids, og_tags, comments (from extract_html_metadata).
        Never raises — malformed HTML returns (stripped_text, [], {}) on fallback.
    """
    from utils.rayon_pool import run_in_cpu_pool_async

    return await run_in_cpu_pool_async(_sync_process_html, html)


# ---------------------------------------------------------------------------
# F3XX: Batch HTML processing via selectolax + ThreadPoolExecutor
# ---------------------------------------------------------------------------


def _batch_sync_process_html(
    items: list[tuple[str, str]],
) -> list[tuple[str, list[str], dict]]:
    """Batch HTML→text extraction via selectolax in ThreadPoolExecutor.

    Strategy (per tools/content_miner.py):
      1. selectolax CSS selectors (fastest, pure Python, M1 8GB friendly)
      2. lxml XPath fallback (only for complex cases selectolax can't handle)

    Args:
        items: List of (html, url) tuples. Cap 1_000 items.

    Returns:
        List of (text, links, metadata) tuples, one per item in same order.
        Returns [("", [], {}) * len(items)] on any error (fail-safe).

    M1 8GB: 4 workers, each page parsed independently, no shared state.
    Bounded: max 1_000 items per batch (URL dedup already done upstream).
    """
    if not items:
        return []
    if len(items) > 1_000:
        items = items[:1_000]

    try:
        from selectolax.parser import HTMLParser as SelectolaxParser
    except Exception:
        # selectolax unavailable: fall back to per-item stdlib HTMLParser
        return [_sync_process_html(html, url) for html, url in items]

    results: list[tuple[str, list[str], dict]] = []

    for html, base_url in items:
        try:
            parser = SelectolaxParser(html)
            # Extract text via CSS (body, article, main — skip nav/header/footer)
            text_nodes = parser.css("body article main p li")
            if not text_nodes:
                text_nodes = parser.css("body div p")
            if not text_nodes:
                text_nodes = parser.css("body")
            text_parts = [node.text() for node in text_nodes if node.text()]
            text = " ".join(text_parts)[:200_000] if text_parts else ""

            # Extract links via selectolax (href attribute)
            link_nodes = parser.css("a[href]")
            links: list[str] = []
            for node in link_nodes:
                href = node.attributes.get("href", "")
                if href:
                    resolved = urllib.parse.urljoin(base_url, href)
                    if resolved.startswith(("http://", "https://")):
                        links.append(resolved)

            # Metadata: title
            title_node = parser.css_first("title")
            title = title_node.text().strip() if title_node else ""
            metadata = {"title": title}

            results.append((text, links, metadata))
        except Exception:
            # Fall back to stdlib
            text, _links, meta = _sync_process_html(html, base_url)
            results.append((text, _links, meta))

    return results


async def process_html_payload_batch(
    items: list[tuple[str, str]],
) -> list[tuple[str, list[str], dict]]:
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
    # Cap at 1_000 to bound memory
    items = items[:1_000]

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _get_html_executor(),
        _batch_sync_process_html,
        items,
    )


# ---------------------------------------------------------------------------
# F273C: Drainable pattern-extraction registry
# ---------------------------------------------------------------------------
# Solves the "16/16 fetched → 0 matched patterns → 0 stored" failure mode
# where the public fetcher completes the HTTP fetch but the CPU-bound HTML
# parsing + pattern matching step gets cancelled when `_MIN_BRANCH_REMAINING_S`
# drops below the floor during windup transition.
#
# Mechanism:
#   1. Caller submits HTML processing via `schedule_html_extraction()` instead
#      of awaiting `process_html_payload()`. The CPU_EXECUTOR work starts
#      immediately (so fetch latency is hidden) and the asyncio.Future is
#      registered in `_DRAIN_REGISTRY`.
#   2. Caller may then await the future, or defer the await to
#      `drain_pending_extractions(deadline_s)` at pre-windup barrier.
#   3. The drain helper awaits all registered futures with a bounded deadline
#      so the windup phase never blocks forever on CPU_EXECUTOR backlog.
#
# Always-on, bounded, fail-soft: futures past the deadline stay in the
# registry for the next drain call. Registry is module-level (shared across
# fetchers within a single process) with a 512-slot cap.
# F-GLOBAL: Replaced with _DrainRegistry class


class _DrainRegistry:
    """Manages HTML extraction futures with bounded deque and stats tracking.

    F-GLOBAL: Encapsulates _DRAIN_REGISTRY, _DRAIN_TOTAL_SCHEDULED,
    _DRAIN_TOTAL_COMPLETED into a single class with __slots__.

    Thread-safe for async use: mutations only from asyncio event loop.
    """

    __slots__ = ("_registry", "_scheduled", "_completed", "_max_size")

    def __init__(self, max_size: int = 512) -> None:
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
            except Exception:  # noqa: BLE001
                pass
        self._registry.append(fut)
        self._scheduled += 1

    def pending_list(self) -> list:
        """Return list of pending futures."""
        return list(self._registry)

    def mark_completed(self, cancelled: bool = False) -> None:
        """Mark a future as completed."""
        if not cancelled:
            self._completed += 1

    def remove(self, fut: asyncio.Future) -> None:
        """Remove a specific future from the registry."""
        try:
            self._registry.remove(fut)
        except ValueError:
            pass

    def stats(self) -> dict:
        """Return diagnostic snapshot."""
        return {
            "registry_size": len(self._registry),
            "registry_capacity": self._registry.maxlen,
            "total_scheduled": self._scheduled,
            "total_completed": self._completed,
            "in_flight": self._scheduled - self._completed,
        }


# Module-level singleton
_drain_registry = _DrainRegistry(max_size=512)

# Bounded executor for HTML processing (replaces unbounded default ThreadPoolExecutor)
_HTML_EXECUTOR: "concurrent.futures.ThreadPoolExecutor | None" = None
_HTML_EXECUTOR_LOCK = threading.Lock()


def _get_html_executor() -> "concurrent.futures.ThreadPoolExecutor":
    """Get or create bounded HTML processing executor.

    Thread-safe via double-checked locking:
    - Fast path: outer check without lock
    - Slow path: inner check with lock (only first caller blocks)
    """
    import concurrent.futures
    import os

    global _HTML_EXECUTOR
    if _HTML_EXECUTOR is not None:
        return _HTML_EXECUTOR
    with _HTML_EXECUTOR_LOCK:
        if _HTML_EXECUTOR is None:
            workers = int(os.environ.get("HLEDAC_HTML_EXTRACTOR_WORKERS", "4"))
            _HTML_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="html-extract",
            )
    return _HTML_EXECUTOR


def schedule_html_extraction(html: str, url: str = "") -> asyncio.Future:
    """Submit HTML processing to CPU_EXECUTOR and register for drain.

    Returns the asyncio.Future wrapping the work. Caller may await it
    immediately (semantically equivalent to `process_html_payload`) or defer
    the await to `drain_pending_extractions(deadline_s)` at windup entry.

    Works from both sync and async contexts. In async context, uses the
    running loop. In sync context (e.g. unit test setup), creates a private
    loop reference. The Future returned is always awaitable from a loop.

    Fail-safe: if the queue is at capacity, the oldest entry is dropped and
    the new one is added. The dropped future is cancelled so its work is
    not orphaned.

    Thread-safety: the registry is mutated only from the asyncio event loop
    (CPU_EXECUTOR callback is invoked via loop.call_soon_threadsafe), so
    no extra lock is needed.
    """
    # Python 3.10+ prefers get_running_loop() inside async.
    # Python 3.14+: get_event_loop() in sync context raises RuntimeError.
    # Use new_event_loop() as fallback for sync callers (unit tests).
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in current thread -- create a private loop.
        # The Future runs in this loop's executor; callers awaiting it
        # from a different loop may need to re-await.
        loop = asyncio.new_event_loop()
    fut: asyncio.Future = loop.run_in_executor(_get_html_executor(), _sync_process_html, html)
    try:
        tag = f"pattern_extract:{url[:64]}" if url else "pattern_extract"
        fut.set_name(tag)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # set_name may not be available on all Python versions
    _drain_registry.schedule(fut)

    def _drop_from_registry(f: asyncio.Future = fut) -> None:
        _drain_registry.mark_completed(f.cancelled())
        _drain_registry.remove(f)

    fut.add_done_callback(_drop_from_registry)
    return fut


async def drain_pending_extractions(deadline_s: float = 30.0) -> tuple[int, int, float]:
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
        done, still_pending = await asyncio.wait(
            pending,
            timeout=max(0.0, deadline_abs - _t_f273c.monotonic()),
            return_when=asyncio.ALL_COMPLETED,
        )
        completed = len(done)
        timed_out = len(still_pending)
    except Exception:  # noqa: BLE001
        return (0, 0, _t_f273c.monotonic() - _t0)
    return (completed, timed_out, _t_f273c.monotonic() - _t0)


def get_drain_stats() -> dict:
    """Diagnostic snapshot of the drain registry (size, totals)."""
    return _drain_registry.stats()
