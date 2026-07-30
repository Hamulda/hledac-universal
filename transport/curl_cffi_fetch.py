"""
transport/curl_cffi_fetch.py

Canonical, lazy, bounded curl_cffi stealth lane.
Fetches via JA3/TLS profile rotation (curl_cffi runtime + session cache).

No network side effects on import.
Streaming/chunked if AsyncSession supports it; hard cap at max_bytes otherwise.

Architecture (Issue 3.5 consolidation):
  - This file is the canonical module: session management + JA3 rotation + fetch API
  - curl_cffi_runtime.py is a backward-compat re-export alias (deleted in v3.0)
"""

import asyncio
import functools
import hashlib
import itertools
import logging
import threading
import time
import urllib.parse
from collections import deque
from typing import Any

from hledac.universal.core.constants import M1_BOUNDS
from hledac.universal.utils.locks import LazyAsyncioLock
from hledac.universal.core.env_config import ENV
from hledac.universal.core.locks import LockCategory, register_lock

# Issue 10.2: canonical UA — injects JA3-consistent User-Agent header
from hledac.universal.layers.ua_rotator import get_ua_for_profile
from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.encoding import decode_response_bytes, parse_charset_from_content_type

from .body_limiter import read_body_with_cap

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB hard cap

# Tor SOCKS5H proxy — DNS resolved by Tor, not localhost
_TOR_CURL_PROXY: str = ENV.get_str("TOR_SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")

# I2P SOCKS5H proxy — default 4447 is I2P SAM bridge port (standard install)
# F260: I2P has no NEWNYM equivalent — circuit rotation is intentionally absent
_I2P_CURL_PROXY: str = ENV.get_str("I2P_SOCKS_PROXY_URL", "socks5h://127.0.0.1:4447")

# Tor-specific circuit tracking for curl_cffi Tor fetcher
_tor_curl_request_count: int = 0

# --- JA3 / TLS fingerprint rotation pool (Sprint F265A) ---------------------------
# Cycle through distinct browser-family TLS profiles so the JA3 fingerprint
# varies across sequential requests — defeats naïve per-host fingerprint
# correlation. All entries are valid `curl_cffi` `tls_impersonate` identifiers
# (verified against curl_cffi >= 0.7.x; uses native rustls-ffi impersonation).
#
# Coverage: 2 × Chrome, 2 × Safari, 2 × Firefox — satisfies the "≥3 distinct
# browser families" contract enforced by tests/test_f265a_transport_audit.py
# TestJA3ProfileCycling.test_pool_contains_at_least_three_browser_families.
_JA3_ROTATION_POOL: list[str] = [
    "chrome133",  # Chromium 133, Mar 2025 (chrome133+)
    "chrome136",  # Chromium 136, May 2025 (latest stable)
    "safari18_0",  # Safari 18.0 (macOS Sequoia 15)
    "safari17_4",  # Safari 17.4 (Sonoma 14.4)
    "firefox136",  # Firefox 136 ESR, Mar 2026
    "firefox133",  # Firefox 133 ESR, Nov 2024
]

# Bounded, thread-safe round-robin iterator. ISSUE-010 FIX: itertools.cycle
# is GIL-atomic in CPython — single bytecode __next__ cannot be torn across
# threads. Lock removed as redundant; itertools.cycle maintains internal
# iterator state safely via GIL.
# NOTE: If this assumption breaks in future Python versions, reintroduce lock.
_ja3_iter: itertools.cycle[str] = itertools.cycle(_JA3_ROTATION_POOL)

# Debug log gate — opt-in via env var. Read lazily at call time so tests can
# toggle either by patching `curl_cffi_fetch.HLEDAC_DEBUG_JA3` (direct) or by
# patching `os.environ` (process-wide). Defaults to OFF in production to keep
# the hot path zero-cost.
HLEDAC_DEBUG_JA3: bool = ENV.get_bool("HLEDAC_DEBUG_JA3")


def next_ja3_profile() -> str:
    """Return the next JA3/TLS profile from the rotation pool (thread-safe).

    ISSUE-010 FIX: GIL-atomic — `itertools.cycle.__next__` is a single bytecode
    instruction; cannot be torn across threads on CPython. Lock removed.
    """
    return next(_ja3_iter)


def reset_ja3_cycle() -> None:
    """Reset the JA3 rotation cycle back to the start (for tests).

    Idempotent. Thread-safe — `itertools.cycle` constructor is atomic.
    """
    global _ja3_iter
    _ja3_iter = itertools.cycle(_JA3_ROTATION_POOL)


def _ja3_log(*, profile: str, url: str, used_profile: str) -> None:
    """Optional debug logger for JA3 profile selection (no-op when disabled).

    Reads `HLEDAC_DEBUG_JA3` at call time. Never raises — debug logger must
    stay on the zero-cost path in production.
    """
    try:
        if not HLEDAC_DEBUG_JA3:
            return
        logger.debug(
            "JA3 rotation: requested=%s used=%s url=%s",
            profile,
            used_profile,
            url,
        )
    except Exception:  # noqa: BLE001
        pass


# === curl_cffi session management (canonical, moved from curl_cffi_runtime.py) ===

# Module-level guard — set once at first availability check
_CURL_CFFI_AVAILABLE: bool | None = None
_CURL_CFFI_IMPORT_ERROR: str | None = None
_CURL_CFFI_LOCK = threading.Lock()
register_lock(LockCategory.NETWORK, _CURL_CFFI_LOCK, "curl_cffi_fetch._CURL_CFFI_LOCK")

# Bounded session cache: profile -> AsyncSession
# max 3 profiles as specified
_MAX_CURL_CFFI_PROFILES = 3
_curl_cffi_sessions: dict[str, Any] = {}
_curl_cffi_lock = LazyAsyncioLock()
_curl_cffi_profiles_order: deque[str] = deque()  # track access order for LRU via popleft()

# F273H: Per-host session cache — host -> (session, last_access_monotonic, profile)
# F270: Values from canonical constants
_M1_BOUNDS = M1_BOUNDS()
_MAX_HOST_SESSIONS: int = _M1_BOUNDS.curl_host_session_max
_HOST_SESSION_TTL_S: float = _M1_BOUNDS.curl_host_session_ttl_s
_host_sessions: dict[str, tuple[Any, float, str]] = {}
_host_access_order: deque[str] = deque()  # LRU: move to end on access

# ISSUE-8.1 / F-03: Per-(host, resolve_target) session cache for DNS rebinding protection
# Key: (host, frozenset of resolve bindings) -> AsyncSession
# When resolve is needed, we need a dedicated session with CURLOPT_RESOLVE set
_resolved_sessions: dict[tuple[str, frozenset[tuple[str, int, str]]], tuple[Any, float]] = {}
_resolved_sessions_order: deque[tuple[str, frozenset[tuple[str, int, str]]]] = deque()
_MAX_RESOLVED_SESSIONS: int = 64  # Max unique resolve bindings
_RESOLVED_SESSION_TTL_S: float = 300.0  # 5 min TTL


async def _get_or_create_resolved_session(
    resolve: dict[str, str],
    profile: str,
    timeout_s: float,
) -> tuple[Any, str]:
    """
    Get or create a curl_cffi AsyncSession with CURLOPT_RESOLVE bound.

    Sessions are cached by (host, frozenset of resolve bindings) so repeated
    requests to the same (hostname, IP) pair reuse the TLS session ticket
    instead of paying for a new handshake on every request.

    F-03: Fixes DNS-rebinding protection creating per-request sessions by
    caching resolved sessions and reusing them for identical resolve bindings.
    """
    # Build canonical cache key from resolve dict
    resolve_bindings = frozenset((host, 443, ip) for host, ip in resolve.items())
    host = next((h for h in resolve), "")

    # F-03: Empty resolve dict — no CURLOPT_RESOLVE needed, fall back to host cache
    if not resolve_bindings:
        return None, profile

    async with _curl_cffi_lock:
        now = time.monotonic()

        # Check cache hit
        cache_key = (host, resolve_bindings)
        if cache_key in _resolved_sessions:
            session, last_access = _resolved_sessions[cache_key]
            if now - last_access < _RESOLVED_SESSION_TTL_S:
                # Move to end of LRU
                if cache_key in _resolved_sessions_order:
                    _resolved_sessions_order.remove(cache_key)
                _resolved_sessions_order.append(cache_key)
                _resolved_sessions[cache_key] = (session, now)
                logger.debug(f"[F-03] resolved session cache hit: {host}")
                return session, profile
            else:
                # Expired — evict
                try:
                    if hasattr(session, "aclose"):
                        safe_create_task(
                            session.aclose(),
                            name=f"curl_cffi:resolved_expire:{host}",
                        )
                except Exception:  # noqa: BLE001
                    pass
                del _resolved_sessions[cache_key]
                if cache_key in _resolved_sessions_order:
                    _resolved_sessions_order.remove(cache_key)

    # Miss: create new session with CURLOPT_RESOLVE
    try:
        from curl_cffi import curl
        from curl_cffi.requests import AsyncSession as _AsyncSession

        resolve_str_list = _resolve_dict_to_curl_format(resolve)
        session = _AsyncSession(
            impersonate=profile,
            timeout=timeout_s,
            max_clients=10,
            curl_options={curl.CurlOpt.RESOLVE: resolve_str_list},
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"[F-03] resolve session creation failed: {e}")
        raise

    # Store in cache with LRU eviction
    async with _curl_cffi_lock:
        now = time.monotonic()
        while len(_resolved_sessions) >= _MAX_RESOLVED_SESSIONS and _resolved_sessions_order:
            oldest_key = _resolved_sessions_order.popleft()
            if oldest_key in _resolved_sessions:
                old_session, _ = _resolved_sessions.pop(oldest_key)
                try:
                    if hasattr(old_session, "aclose"):
                        safe_create_task(
                            old_session.aclose(),
                            name=f"curl_cffi:resolved_evict:{oldest_key[0]}",
                        )
                except Exception:  # noqa: BLE001
                    pass

        _resolved_sessions[cache_key] = (session, now)
        _resolved_sessions_order.append(cache_key)
        logger.debug(f"[F-03] resolved session cached: {host} (profile={profile})")

    return session, profile


def _resolve_dict_to_curl_format(resolve: dict[str, str]) -> list[str]:
    """
    Convert Python resolve dict to curl_cffi CURLOPT_RESOLVE format.

    Input:  {"example.com": "1.2.3.4"}
    Output: ["example.com:443:1.2.3.4"]

    curl_cffi expects list of "host:port:ip" strings for CURLOPT_RESOLVE.
    Default port 443 for HTTPS.
    """
    result = []
    for host, ip in resolve.items():
        result.append(f"{host}:443:{ip}")
    return result


# Preferred profile fallback order
# Targets: academia (Safari 17 Apple Silicon), government (Firefox 133+), mobile/android (Chrome Android 99+)
_PROFILE_FALLBACK_ORDER = [
    "chrome136",
    "chrome131",
    "chrome124",
    "chrome120",
    "chrome110",
    "safari17_0",
    "firefox135",
    "firefox133",
    "chrome99_android",
]


def is_curl_cffi_available() -> tuple[bool, str]:
    """
    Check if curl_cffi is available for import.
    Lazy — checks and caches on first call.
    Thread-safe: double-checked locking with threading.Lock.
    """
    global _CURL_CFFI_AVAILABLE, _CURL_CFFI_IMPORT_ERROR

    # Fast path: lock-free read after first initialization
    if _CURL_CFFI_AVAILABLE is not None:
        return _CURL_CFFI_AVAILABLE, _CURL_CFFI_IMPORT_ERROR or "ok"

    # Slow path: acquire lock and double-check
    with _CURL_CFFI_LOCK:
        if _CURL_CFFI_AVAILABLE is None:
            try:
                from curl_cffi.requests import AsyncSession  # type: ignore[unresolved-import]  # noqa: F401

                _CURL_CFFI_AVAILABLE = True
                _CURL_CFFI_IMPORT_ERROR = None
                logger.debug("curl_cffi is available")
            except ImportError as e:
                _CURL_CFFI_AVAILABLE = False
                _CURL_CFFI_IMPORT_ERROR = str(e)
                logger.debug(f"curl_cffi not available: {e}")
                return False, f"import_error: {e}"

    return _CURL_CFFI_AVAILABLE, _CURL_CFFI_IMPORT_ERROR or "ok"


async def async_get_curl_cffi_session(profile: str = "chrome110") -> tuple[bool, Any, str]:
    """
    Get or create a cached curl_cffi AsyncSession for the given profile.
    Lazy singleton with bounded LRU eviction.

    F350M-R: Uses race_first_success to parallelize profile fallback chain.
    All profiles race to create a session; first success wins, losers cancelled.
    ~3× faster than sequential fallback (was O(n) sequential, now O(1) parallel).

    Returns:
        (success, session_or_None, used_profile)
    """
    available, reason = is_curl_cffi_available()
    if not available:
        return False, None, reason

    profiles_to_try = (
        _PROFILE_FALLBACK_ORDER
        if profile not in _PROFILE_FALLBACK_ORDER
        else [profile] + [p for p in _PROFILE_FALLBACK_ORDER if p != profile]
    )

    # F350M-R: Race-first success — parallel profile creation.
    # Creates a session for each profile concurrently; first working profile wins.
    # Losers are cancelled immediately, saving CPU/RAM vs sequential O(n) delay.
    from hledac.universal.utils.async_helpers import race_first_success

    coros: list[tuple[Awaitable[tuple[bool, Any]], str]] = []
    for p in profiles_to_try:

        async def _try_profile(prof: str = p) -> tuple[bool, Any]:
            sess = await _get_or_create_session(prof)
            return sess is not None, sess

        coros.append((_try_profile(p), p))

    # 5s global timeout for profile race — if all profiles fail, return failure
    result = await race_first_success(*coros, timeout=5.0, label="curl_cffi_profile_race")

    if result.result is not None and result.result[0]:
        return True, result.result[1], result.winner_label

    return False, None, f"session_creation_failed: {len(result.errors)} profiles failed"


# F273H: Public API — get session keyed by (url, profile) for keepalive reuse
async def async_get_curl_cffi_session_for_host(
    url: str,
    profile: str = "chrome110",
) -> tuple[bool, Any, str, str]:
    """
    Get or create a curl_cffi AsyncSession for a specific host.

    Per-host caching keeps the TCP connection + TLS session ticket warm
    across requests to the same host. First request pays full TCP+TLS cost;
    subsequent requests to the same host reuse the persistent connection
    (~200-400 ms saved per request on M1).

    Args:
        url: Full URL to extract host from.
        profile: TLS impersonation profile (chrome110, firefox133, etc.).

    Returns:
        (success, session_or_None, used_profile, host_or_empty_str)
    """
    from urllib.parse import urlparse

    available, reason = is_curl_cffi_available()
    if not available:
        return False, None, reason, ""

    try:
        parsed = urlparse(url)
        host = parsed.netloc or ""
    except Exception:  # noqa: BLE001
        host = ""

    if not host:
        ok, sess, prof = await async_get_curl_cffi_session(profile)
        return ok, sess, prof, ""

    # Fast path: check host cache under lock
    async with _curl_cffi_lock:
        now = time.monotonic()
        if host in _host_sessions:
            session, last_access, cached_profile = _host_sessions[host]
            if now - last_access < _HOST_SESSION_TTL_S:
                if host in _host_access_order:
                    _host_access_order.remove(host)
                _host_access_order.append(host)
                _host_sessions[host] = (session, now, cached_profile)
                logger.debug(f"[F273H] host cache hit: {host}")
                return True, session, cached_profile, host
            else:
                # Expired — evict
                try:
                    if hasattr(session, "aclose"):
                        safe_create_task(
                            session.aclose(),
                            name=f"curl_cffi:host_expire:{host}",
                        )
                except Exception:  # noqa: BLE001
                    pass
                del _host_sessions[host]
                if host in _host_access_order:
                    _host_access_order.remove(host)

    # Miss: get or create profile session, then cache per-host
    ok, session, used_profile = await async_get_curl_cffi_session(profile)
    if not ok or session is None:
        return False, None, used_profile, host

    async with _curl_cffi_lock:
        now = time.monotonic()
        while len(_host_sessions) >= _MAX_HOST_SESSIONS and _host_access_order:
            oldest_host = _host_access_order.popleft()
            if oldest_host in _host_sessions:
                old_session, _, _ = _host_sessions.pop(oldest_host)
                try:
                    if hasattr(old_session, "aclose"):
                        safe_create_task(
                            old_session.aclose(),
                            name=f"curl_cffi:host_evict:{oldest_host}",
                        )
                except Exception:  # noqa: BLE001
                    pass

        _host_sessions[host] = (session, now, used_profile)
        _host_access_order.append(host)
        logger.debug(f"[F273H] host cache store: {host} (profile={used_profile})")

    return True, session, used_profile, host


async def _get_or_create_session(profile: str) -> Any | None:
    """Internal: get from cache or create new, with bounded LRU.

    F265B: delegates to ``prewarm_pool`` first when the prewarm lane
    is enabled. The prewarm pool keeps 2 sessions warm
    (TCP+TLS handshakes pre-completed) so the first request to a
    profile does not pay the 200-400 ms cold-start cost. On any
    prewarm failure, falls back to the original lazy path.
    """
    if profile in _curl_cffi_sessions:
        if profile in _curl_cffi_profiles_order:
            _curl_cffi_profiles_order.remove(profile)
        _curl_cffi_profiles_order.append(profile)
        session = _curl_cffi_sessions[profile]
        if hasattr(session, "closed") and not session.closed:
            return session
        del _curl_cffi_sessions[profile]

    # F265B: try prewarm pool first
    try:
        from .prewarm_pool import acquire_session as _prewarm_acquire

        ok, sess, used = await _prewarm_acquire(profile)
        if ok and sess is not None:
            if profile in _curl_cffi_sessions:
                if profile in _curl_cffi_profiles_order:
                    _curl_cffi_profiles_order.remove(profile)
                _curl_cffi_sessions.pop(profile, None)
            _curl_cffi_sessions[profile] = sess
            _curl_cffi_profiles_order.append(profile)
            logger.debug(f"curl_cffi session acquired from prewarm pool for profile: {used}")
            return sess
    except Exception as e:  # noqa: BLE001
        logger.debug(f"prewarm pool acquire failed (fallback to lazy): {e}")

    _sessions_to_close: list[Any] = []

    try:
        async with _curl_cffi_lock:
            if profile in _curl_cffi_sessions:
                return _curl_cffi_sessions[profile]

            if len(_curl_cffi_sessions) >= _MAX_CURL_CFFI_PROFILES:
                if _curl_cffi_profiles_order:
                    oldest = _curl_cffi_profiles_order.popleft()
                    if oldest in _curl_cffi_sessions:
                        _sessions_to_close.append(_curl_cffi_sessions.pop(oldest))

            from curl_cffi.requests import AsyncSession  # type: ignore[unresolved-import]

            # P3-04 FIX: Explicit connection pooling + HTTP/2 for connection reuse.
            # http2=True enables HTTP/2 multiplexing over persistent connections.
            # max_clients=25 matches max_connections intent; max_keepalive implicit via HTTP/2.
            new_session = AsyncSession(
                impersonate=profile,
                timeout=10.0,
                max_clients=25,  # connection pool size (keepalive connections)
                http2=True,  # P3-04: HTTP/2 for multiplexing and connection reuse
            )
            _curl_cffi_sessions[profile] = new_session
            _curl_cffi_profiles_order.append(profile)
            logger.debug(f"curl_cffi session created for profile: {profile}")
            return new_session
    finally:
        if _sessions_to_close:

            async def _close_evicted():
                for _sess in _sessions_to_close:
                    try:
                        if hasattr(_sess, "aclose"):
                            await _sess.aclose()
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"Failed to close evicted session: {e}")

            safe_create_task(_close_evicted(), name="curl_cffi:close_evicted")


async def close_curl_cffi_sessions_async() -> None:
    """
    Close all cached curl_cffi sessions (profile + host cache).
    Idempotent — safe to call multiple times.
    CancelledError is re-raised.
    """
    global _curl_cffi_sessions, _curl_cffi_profiles_order, _host_sessions, _host_access_order
    global _resolved_sessions, _resolved_sessions_order

    await asyncio.sleep(0)  # yield to event loop before closing

    async with _curl_cffi_lock:
        profile_sessions = list(_curl_cffi_sessions.values())
        _curl_cffi_sessions.clear()
        _curl_cffi_profiles_order.clear()

        host_sessions = [s for s, _, _ in _host_sessions.values()]
        _host_sessions.clear()
        _host_access_order.clear()

        # F-03: Close all resolved sessions (not just fire-and-forget on eviction)
        resolved_sessions = [s for s, _ in _resolved_sessions.values()]
        _resolved_sessions.clear()
        _resolved_sessions_order.clear()

    for session in profile_sessions + host_sessions + resolved_sessions:
        try:
            if hasattr(session, "aclose"):
                await session.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Failed to close curl_cffi session: {e}")

    logger.debug(f"curl_cffi sessions closed: {len(profile_sessions)} profiles + {len(host_sessions)} hosts + {len(resolved_sessions)} resolved")


def get_curl_cffi_runtime_status() -> dict[str, Any]:
    """
    Return runtime status for telemetry.
    """
    available, reason = is_curl_cffi_available()
    return {
        "curl_cffi_available": available,
        "availability_reason": reason,
        "cached_profiles": list(_curl_cffi_sessions.keys()),
        "cache_capacity": _MAX_CURL_CFFI_PROFILES,
        "cache_used": len(_curl_cffi_sessions),
        "host_cache_size": len(_host_sessions),
        "host_cache_capacity": _MAX_HOST_SESSIONS,
        "host_cache_ttl_s": _HOST_SESSION_TTL_S,
        # F-03: resolved session cache
        "resolved_cache_size": len(_resolved_sessions),
        "resolved_cache_capacity": _MAX_RESOLVED_SESSIONS,
        "resolved_cache_ttl_s": _RESOLVED_SESSION_TTL_S,
    }


# === Fetch API (from original curl_cffi_fetch.py) ===


# F265C: blocking Alt-Svc pre-probe for first-fetch H3 priming.
async def _blocking_altsvc_probe_for_url(url: str) -> Any:
    """Perform a blocking HEAD probe to prime the H3 LRU before first fetch.

    Returns ``curl_cffi.requests.HttpVersion.v3`` if the server advertises h3,
    else ``None``. All errors are swallowed and ``None`` is returned so the
    caller always proceeds on HTTP/1.1/2 without H3.

    M1 8GB: uses a dedicated session (max_clients=2) so it cannot starve
    the live fetch path. Timeout is4s — well under the fetch timeout budget.
    """
    try:
        from curl_cffi.requests import AsyncSession, HttpVersion  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    try:
        from hledac.universal.fetching.public_fetcher import url_ops

        from .http3_lane import (
            _altsvc_advertises_h3,
            _cache_get,
            _cache_put,
            _resolve_enabled,
        )
        from .http3_lane import (
            extract_host as _http3_extract_host,
        )

        _use_extract_host = url_ops.extract_host if hasattr(url_ops, "extract_host") else _http3_extract_host
    except Exception:  # noqa: BLE001
        _use_extract_host = None

    if _use_extract_host is None:
        return None

    if not _resolve_enabled():
        return None

    host = _use_extract_host(url)
    if not host:
        return None
    if _cache_get(host) is not None:
        return None

    try:
        sess: Any = AsyncSession(impersonate="chrome124", timeout=4.0, max_clients=2)
        try:
            # ISSUE-044: asyncio.wait_for → asyncio.timeout (Python 3.11+)
            # PEP 654 asyncio.TimeoutError is NOT subclass of CancelledError,
            # preserving TaskGroup cancellation semantics correctly.
            try:
                async with asyncio.timeout(5.0):
                    resp = await sess.head(url, timeout=4.0)
            except TimeoutError:
                # asyncio.timeout raises TimeoutError, not CancelledError
                resp = None
            if resp is not None and resp.headers and _altsvc_advertises_h3(resp.headers):
                _cache_put(host, True)
                return HttpVersion.v3
        finally:
            try:
                await sess.aclose()
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return None


def decode_curl_cffi_result(result: dict, *, max_bytes: int = 5 * 1024 * 1024) -> str | None:
    """F261 helper: decode raw bytes from a curl_cffi result dict to str.

    Uses the bounded encoding chain from utils.encoding (charset_normalizer →
    chardet → UTF-8 → latin-1). Honours the http_charset_hint produced by
    fetch_via_curl_cffi when present.

    Returns decoded str, or None when the dict has no content / is an error.
    Never raises — bounded by max_bytes.
    """
    if not isinstance(result, dict):
        return None
    if not result.get("success"):
        return None
    content = result.get("content", b"")
    if not content:
        return None
    try:
        return decode_response_bytes(
            content,
            http_charset=result.get("http_charset_hint"),
            max_bytes=max_bytes,
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("decode_curl_cffi_result failed (fail-soft): %s", e)
        return None


async def fetch_via_tor_curl_cffi(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome136",
    tor_manager: Any = None,
    circuit_rotation_count: int = 50,
    use_conditional_cache: bool = True,
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi through Tor SOCKS5H proxy with circuit rotation.

    GRAPH-02 FIX: Now uses fetch_via_curl_cffi_cached for conditional-GET
    support (ETag/Last-Modified 304 shortcuts). Darknet content changes
    less frequently than clearnet; 1h TTL balances freshness vs bandwidth.

    Args:
        url: URL to fetch (.onion only)
        headers: Optional HTTP headers
        timeout_s: Request timeout
        max_bytes: Max bytes to read
        profile: TLS profile for JA3 fingerprint
        tor_manager: TorManager instance for circuit rotation (NEWNYM via stem)
        circuit_rotation_count: Rotate circuit every N requests (default 50)
        use_conditional_cache: Use conditional-GET caching (default True, GRAPH-02)
    """
    # GRAPH-03: Validate onion v3 address BEFORE circuit allocation.
    # Rejecting an invalid address here saves 10-30s (Tor circuit timeout).
    # Only validate .onion URLs — I2P uses different address format.
    if ".onion" in url:
        try:
            from hledac.universal.rust_extensions import rust_validate_onion_v3_detailed

            validation_result = rust_validate_onion_v3_detailed(url)
            if validation_result != "valid":
                return _make_error_result(
                    url=url,
                    error=f"onion_v3_validation_failed: {validation_result}",
                    failure_stage="onion_validation",
                    network_error_kind="validation_error",
                    selected_transport="tor_curl_cffi",
                    tls_impersonate=profile,
                )
        except Exception:  # noqa: BLE001
            # rust_extensions unavailable (build not present) — skip validation
            pass

    global _tor_curl_request_count
    _tor_curl_request_count += 1
    count = _tor_curl_request_count

    if tor_manager is not None and count >= circuit_rotation_count:
        _tor_curl_request_count = 0
        try:
            await tor_manager.rotate_circuit()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[TOR] circuit rotation failed: {e}")

    proxies = {"https": _TOR_CURL_PROXY}

    if use_conditional_cache:
        try:
            return await fetch_via_curl_cffi_cached(
                url=url,
                headers=headers,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
                profile=profile,
                proxies=proxies,
                ttl_s=3600,  # Darknet content: 1h freshness window
            )
        except Exception:  # noqa: BLE001
            # Fallback to uncached on any error
            pass

    return await fetch_via_curl_cffi(
        url=url,
        headers=headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        profile=profile,
        proxies=proxies,
    )


async def fetch_via_i2p_curl_cffi(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome136",
    use_conditional_cache: bool = True,
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi through I2P SOCKS5H proxy.

    GRAPH-02 FIX: Now uses fetch_via_curl_cffi_cached for conditional-GET
    support (ETag/Last-Modified 304 shortcuts). I2P content is typically
    more stable than Tor; 2h TTL reflects lower change frequency.

    F260: I2P has no NEWNYM equivalent — circuit rotation is intentionally
    absent. I2P tunnels are inherently e2e and short-lived; explicit rotation
    would break the destination lookup. Documented invariant: this function
    takes no `tor_manager` / `circuit_rotation_count` parameters.

    Args:
        url: URL to fetch (.i2p or .b32.i2p only)
        headers: Optional HTTP headers
        timeout_s: Request timeout
        max_bytes: Max bytes to read
        profile: TLS profile for JA3 fingerprint
        use_conditional_cache: Use conditional-GET caching (default True, GRAPH-02)
    """
    proxies = {"https": _I2P_CURL_PROXY}

    if use_conditional_cache:
        try:
            return await fetch_via_curl_cffi_cached(
                url=url,
                headers=headers,
                timeout_s=timeout_s,
                max_bytes=max_bytes,
                profile=profile,
                proxies=proxies,
                ttl_s=7200,  # I2P content: 2h freshness window (more stable than Tor)
            )
        except Exception:  # noqa: BLE001
            # Fallback to uncached on any error
            pass

    return await fetch_via_curl_cffi(
        url=url,
        headers=headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        profile=profile,
        proxies=proxies,
    )


async def fetch_via_curl_cffi(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome136",
    proxies: dict[str, str] | None = None,
    http_version: Any = None,
    *,
    resolve: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi stealth lane.

    ISSUE-8.1 FIX: DNS Rebinding Protection via pre-resolved IP binding.

    When ``resolve`` dict is provided (hostname -> IP), curl's RESOLVE option
    is used to bind the connection to the pre-validated IP before DNS lookup.
    This eliminates the TOCTOU window between DNS validation and fetch:
    1. _validate_fetch_target() resolves hostname to IP via async_getaddrinfo
    2. resolved IPs are passed here via ``resolve`` dict
    3. curl connects directly to the pre-validated IP — no DNS re-resolution

    This prevents DNS rebinding attacks where a hostname might resolve to
    a public IP during validation but switch to a private IP before fetch.

    Args:
        resolve: Dict of hostname -> IP address to pre-bind.
                 Example: {"example.com": "1.2.3.4"}
                 When provided, curl will connect to 1.2.3.4:443 for example.com
                 instead of performing a fresh DNS lookup.

    Returns FetchResult-compatible dict:
        url, final_url, content (bytes), status_code, content_type,
        headers, success, error, selected_transport, tls_impersonate,
        failure_stage, network_error_kind

    Failure stages: "resolve", "connect", "tls", "response", "read", "unknown"
    Network error kinds: "timeout", "connection_refused", "dns_failure",
                         "connection_reset", "too_many_redirects", "other"

    CancelledError is re-raised.
    """
    available, avail_reason = is_curl_cffi_available()
    if not available:
        return _make_error_result(
            url,
            error=f"curl_cffi_not_available: {avail_reason}",
            failure_stage="unknown",
            network_error_kind="other",
            selected_transport="curl_cffi",
            tls_impersonate=profile,
        )

    # ISSUE-8.1 / F-03: DNS Rebinding Protection — resolve requires dedicated session
    # curl_cffi does NOT support per-request resolve parameter; CURLOPT_RESOLVE
    # must be set at session creation time via curl_options.
    # F-03 Fix: Session is now cached by (host, frozenset of resolve bindings)
    # so repeated requests to the same (hostname, IP) reuse the TLS session.
    if resolve:
        try:
            session, used_profile = await _get_or_create_resolved_session(
                resolve, profile, timeout_s
            )
            _ja3_log(profile=profile, url=url, used_profile=used_profile)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            return _make_error_result(
                url,
                error=f"resolve_session_error: {e}",
                failure_stage="unknown",
                network_error_kind="other",
                selected_transport="curl_cffi",
                tls_impersonate=profile,
            )
    else:
        try:
            ok, session, used_profile, _host = await async_get_curl_cffi_session_for_host(url, profile)
            _ja3_log(profile=profile, url=url, used_profile=used_profile)
            if not ok or session is None:
                return _make_error_result(
                    url,
                    error=f"session_creation_failed: {used_profile}",
                    failure_stage="unknown",
                    network_error_kind="other",
                    selected_transport="curl_cffi",
                    tls_impersonate=used_profile,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            return _make_error_result(
                url,
                error=f"session_error: {e}",
                failure_stage="unknown",
                network_error_kind="other",
                selected_transport="curl_cffi",
                tls_impersonate=profile,
            )

    try:
        # Issue 10.2: JA3 consistency — inject User-Agent matching TLS profile
        # when caller did not provide one. Callers passing custom headers keep
        # full control (e.g., build_randomized_headers sets Sec-Ch-Ua, etc.).
        _merged_headers: dict[str, str] = dict(headers) if headers else {}
        if "User-Agent" not in _merged_headers:
            _merged_headers["User-Agent"] = get_ua_for_profile(used_profile)
        # ISSUE-8.1: DNS Rebinding Protection — resolve is now bound to session via curl_options
        # No need to pass resolve per-request; it's already set on the session
        response = await session.get(url, headers=_merged_headers, timeout=timeout_s)

        # curl_cffi iter_content() returns a sync generator, not async iterator.
        # Use response.content directly (already bytes) and truncate manually if needed.
        _raw_content = response.content or b""
        _truncated = len(_raw_content) > max_bytes
        content_bytes = _raw_content[:max_bytes] if _truncated else _raw_content
        if _truncated:
            logger.debug(f"curl_cffi body truncated to {max_bytes} bytes for {url}")

        content_type = ""
        if response.headers:
            content_type = response.headers.get("content-type", "")

        http_charset_hint = parse_charset_from_content_type(content_type)

        return {
            "url": url,
            "final_url": url,
            # content_bytes is already `bytes` from read_body_with_cap (bytearray.collect)
            # bytes(content_bytes) would be redundant copy (~256B-2MB per fetch)
            "content": content_bytes,
            "status_code": response.status_code,
            "content_type": content_type,
            "http_charset_hint": http_charset_hint,
            "headers": dict(response.headers) if response.headers else {},
            "success": True,
            "error": None,
            "selected_transport": "curl_cffi",
            "tls_impersonate": used_profile,
            "failure_stage": None,
            "network_error_kind": None,
        }

    except TimeoutError:
        return _make_error_result(
            url,
            error="timeout",
            failure_stage="response",
            network_error_kind="timeout",
            selected_transport="curl_cffi",
            tls_impersonate=used_profile,
        )
    except ConnectionRefusedError:
        return _make_error_result(
            url,
            error="connection_refused",
            failure_stage="connect",
            network_error_kind="connection_refused",
            selected_transport="curl_cffi",
            tls_impersonate=used_profile,
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        error_str = str(e).lower()
        if "timeout" in error_str:
            network_kind = "timeout"
            failure_stage = "response"
        elif "dns" in error_str or "name or service not known" in error_str:
            network_kind = "dns_failure"
            failure_stage = "resolve"
        elif "connection reset" in error_str:
            network_kind = "connection_reset"
            failure_stage = "connect"
        else:
            network_kind = "other"
            failure_stage = "unknown"

        return _make_error_result(
            url,
            error=str(e),
            failure_stage=failure_stage,
            network_error_kind=network_kind,
            selected_transport="curl_cffi",
            tls_impersonate=used_profile,
        )


def _make_error_result(
    url: str,
    error: str,
    failure_stage: str,
    network_error_kind: str,
    selected_transport: str,
    tls_impersonate: str,
) -> dict[str, Any]:
    """Build an error result dict."""
    return {
        "url": url,
        "final_url": url,
        "content": b"",
        "status_code": 0,
        "content_type": "",
        "headers": {},
        "success": False,
        "error": error,
        "selected_transport": selected_transport,
        "tls_impersonate": tls_impersonate,
        "failure_stage": failure_stage,
        "network_error_kind": network_error_kind,
    }


# F273G: Issue #39 — with_rate_limit bypass fix.
# Rate limiting is now enforced at the canonical entry point so that
# every fetch path (public_fetcher, fetch_coordinator, base_fetcher,
# stealth_browser) is automatically rate-limited without per-caller wrapping.
@functools.lru_cache(maxsize=2048)
def _extract_domain_from_url(url: str) -> str:
    """Extract effective domain from URL for rate-limiter bucketing."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        # Strip port
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        # Special-case darknet — only exact/suffix matches, not substring contains
        if host.endswith(".onion"):
            return "tor"
        if host.endswith(".i2p") or host.endswith(".b32.i2p"):
            return "i2p"
        return host
    except Exception:  # noqa: BLE001
        return "default"


# F265B: conditional cache wrapper for the curl_cffi stealth lane.


# F350M-R: module-level helpers eliminate function-in-function nesting (CC=-2)
def _extract_resp_headers(resp_headers: dict) -> tuple[str, str, str]:
    """Extract etag, last_modified, content_type from response headers."""
    etag = last_modified = content_type = ""
    for k, v in resp_headers.items():
        kl = k.lower()
        if kl == "etag":
            etag = str(v)
        elif kl == "last-modified":
            last_modified = str(v)
        elif kl == "content-type":
            content_type = str(v)
    return etag, last_modified, content_type


def _hash_body_bytes(body_bytes: bytes) -> str:
    """Compute sha256 hex of body bytes; empty string on error."""
    try:
        return hashlib.sha256(body_bytes).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


# F350M-R: extracted helper eliminates nested try/runtime-import inside pre-probe block (CC=-3, depth=-2)
async def _try_probe_h3(url: str) -> Any | None:
    """Probe Alt-Svc for H3; return HttpVersion.v3 on success, None on failure."""
    try:
        from curl_cffi.requests import HttpVersion as _HttpVersion  # type: ignore[unresolved-import]

        await _blocking_altsvc_probe_for_url(url)
        from .http3_lane import _cache_get, extract_host as _probe_extract_host

        _probe_host = _probe_extract_host(url)
        if _probe_host and _cache_get(_probe_host) is True:
            return _HttpVersion.v3
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        pass
    return None


async def _maybe_preprobe_h3(
    url: str, http_version: Any, _force_refresh: bool, _pre_probe: bool
) -> Any:
    """Probe for H3 if conditions met; returns (possibly updated) http_version. Darknet skipped."""
    if not _pre_probe or http_version is not None or _force_refresh:
        return http_version
    _url_lower = url.lower() if url else ""
    if _url_lower.endswith(".onion") or ".i2p" in _url_lower or ".b32.i2p" in _url_lower:
        return http_version
    _http_version = await _try_probe_h3(url)
    return _http_version if _http_version is not None else http_version


async def fetch_via_curl_cffi_cached(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome136",
    proxies: dict[str, str] | None = None,
    http_version: Any = None,
    *,
    resolve: dict[str, str] | None = None,
    ttl_s: int = 3600,
    _force_refresh: bool = False,
    _pre_probe: bool = False,
) -> dict[str, Any]:
    """fetch_via_curl_cffi with conditional-GET (304) shortcut.

    ISSUE-8.1 FIX: Passes ``resolve`` dict through to fetch_via_curl_cffi
    for DNS rebinding protection via pre-bound IP addresses.

    Args:
        url: Same as fetch_via_curl_cffi.
        headers: Caller headers; ``If-None-Match`` / ``If-Modified-Since``
            from the cache are MERGED in (caller wins on conflict).
        timeout_s: Same as fetch_via_curl_cffi.
        max_bytes: Same as fetch_via_curl_cffi.
        profile: TLS profile (passed through).
        proxies: Same as fetch_via_curl_cffi.
        http_version: HttpVersion.v3 from http3_lane (passed through).
        resolve: ISSUE-8.1 — hostname -> IP dict for DNS rebinding protection.
        ttl_s: Cache freshness window in seconds. Default 1h.
        _force_refresh: Skip the cache entirely (always send no
            validators). For tests and one-off live fetches.
        _pre_probe: If True and the H3 LRU is cold for this host,
            perform a blocking HEAD probe (~200-400ms) to prime the
            LRU before the first fetch. On H3 detection the fetch
            itself uses HttpVersion.v3 immediately (no round-trip
            wasted). Fail-soft: any probe error falls through to
            normal fetch without H3.

    Returns:
        Same FetchResult dict as fetch_via_curl_cffi. On 304, the
        ``content`` field is the cached bytes and the result carries
        ``conditional_304=True`` so callers can log the hit.
    """
    from hledac.universal.utils.rate_limiters import get_limiter

    from .conditional_cache import (
        conditional_headers_for,
    )
    from .conditional_cache import (
        lookup as _cc_lookup,
    )
    from .conditional_cache import (
        record_conditional_result as _cc_record,
    )
    from .conditional_cache import (
        store as _cc_store,
    )

    # F273G: Issue #39 — Rate limiting envelope.  Every caller of this
    # function is now automatically rate-limited without needing a separate
    # with_rate_limit() wrapper.  Domain is extracted from the URL so
    # separate domains don't share buckets; unknown domains fall back to
    # the "default" TokenBucket (10 req/s, capacity 30).
    _domain = _extract_domain_from_url(url)
    try:
        await get_limiter(_domain).acquire()
    except Exception:  # noqa: BLE001
        # Rate limiter unavailable — proceed without blocking (fail-soft)
        pass

    # F273G-H3FIX: Blocking pre-probe BEFORE primary fetch.
    # F350M-R: extracted helper eliminates depth-2 darknet pass-block (CC -2, depth -2)
    http_version = await _maybe_preprobe_h3(url, http_version, _force_refresh, _pre_probe)

    merged_headers: dict[str, str] = dict(headers) if headers else {}
    sent_conditional = False

    if not _force_refresh:
        try:
            cache_headers = conditional_headers_for(url, ttl_s=ttl_s)
            if cache_headers:
                # F350M-R: comprehension eliminates for-loop nesting (depth -1)
                merged_headers.update({k: v for k, v in cache_headers.items() if k not in merged_headers})
                sent_conditional = True
        except Exception:  # noqa: BLE001
            pass

    result = await fetch_via_curl_cffi(
        url=url,
        headers=merged_headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        profile=profile,
        proxies=proxies,
        http_version=http_version,
        resolve=resolve,
    )

    if not result.get("success"):
        return result

    status = int(result.get("status_code", 0) or 0)

    # F350M-R: flatten 304 handler — guard clause for sent_conditional (CC -1, depth -1)
    if status == 304:
        if sent_conditional:
            try:
                _cc_record(url, sent=True, response_status=status)
            except Exception:  # noqa: BLE001
                pass
        try:
            entry = _cc_lookup(url)
        except Exception:  # noqa: BLE001
            entry = None
        if entry is not None and entry.body:
            result["content"] = entry.body
        result["final_url"] = url
        result["conditional_304"] = True
        return result

    # F350M-R: success path — use module-level helpers
    if 200 <= status < 300:
        try:
            resp_headers = result.get("headers") or {}
            etag, last_modified, content_type = _extract_resp_headers(resp_headers)
            body_bytes = result.get("content", b"") or b""
            sha_hex = _hash_body_bytes(body_bytes)
            _cc_store(
                url,
                etag=etag,
                last_modified=last_modified,
                body=body_bytes,
                sha256=sha_hex,
                status_code=status,
                content_type=content_type,
            )
        except Exception:  # noqa: BLE001
            pass
    return result
