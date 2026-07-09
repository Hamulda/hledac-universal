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
from __future__ import annotations


import asyncio
import itertools
import logging
import os
import threading
import time
import urllib.parse
from collections import deque
from typing import Any

from hledac.universal.core.constants import M1_BOUNDS
from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.encoding import decode_response_bytes, parse_charset_from_content_type

from .body_limiter import read_body_with_cap

# Issue 10.2: canonical UA — injects JA3-consistent User-Agent header
from hledac.universal.layers.ua_rotator import get_ua_for_profile

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10MB hard cap

# Tor SOCKS5H proxy — DNS resolved by Tor, not localhost
_TOR_CURL_PROXY: str = os.environ.get("TOR_SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")

# I2P SOCKS5H proxy — default 4447 is I2P SAM bridge port (standard install)
# F260: I2P has no NEWNYM equivalent — circuit rotation is intentionally absent
_I2P_CURL_PROXY: str = os.environ.get("I2P_SOCKS_PROXY_URL", "socks5h://127.0.0.1:4447")

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
    "chrome133",    # Chromium 133, Mar 2025 (chrome133+)
    "chrome136",    # Chromium 136, May 2025 (latest stable)
    "safari18_0",   # Safari 18.0 (macOS Sequoia 15)
    "safari17_4",   # Safari 17.4 (Sonoma 14.4)
    "firefox136",   # Firefox 136 ESR, Mar 2026
    "firefox133",   # Firefox 133 ESR, Nov 2024
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
HLEDAC_DEBUG_JA3: bool = os.environ.get("HLEDAC_DEBUG_JA3", "0") == "1"


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

    Reads `HLEDAC_DEBUG_JA3` at call time so it can be toggled by either
    `os.environ["HLEDAC_DEBUG_JA3"]=1` (process-level) or by patching the
    module attribute directly (per-test). Never raises — debug logger must
    stay on the zero-cost path in production.
    """
    try:
        enabled = bool(HLEDAC_DEBUG_JA3) or os.environ.get("HLEDAC_DEBUG_JA3", "0") == "1"
        if not enabled:
            return
        logger.debug(
            "JA3 rotation: requested=%s used=%s url=%s",
            profile, used_profile, url,
        )
    except Exception:  # noqa: BLE001
        pass


# === curl_cffi session management (canonical, moved from curl_cffi_runtime.py) ===

# Module-level guard — set once at first availability check
_CURL_CFFI_AVAILABLE: bool | None = None
_CURL_CFFI_IMPORT_ERROR: str | None = None
_CURL_CFFI_LOCK = threading.Lock()

# Bounded session cache: profile -> AsyncSession
# max 3 profiles as specified
_MAX_CURL_CFFI_PROFILES = 3
_curl_cffi_sessions: dict[str, Any] = {}
_curl_cffi_lock = asyncio.Lock()
_curl_cffi_profiles_order: deque[str] = deque()  # track access order for LRU via popleft()

# F273H: Per-host session cache — host -> (session, last_access_monotonic, profile)
# F270: Values from canonical constants
_M1_BOUNDS = M1_BOUNDS()
_MAX_HOST_SESSIONS: int = _M1_BOUNDS.curl_host_session_max
_HOST_SESSION_TTL_S: float = _M1_BOUNDS.curl_host_session_ttl_s
_host_sessions: dict[str, tuple[Any, float, str]] = {}
_host_access_order: deque[str] = deque()  # LRU: move to end on access


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

    Returns:
        (success, session_or_None, reason)
    """
    available, reason = is_curl_cffi_available()
    if not available:
        return False, None, reason

    profiles_to_try = _PROFILE_FALLBACK_ORDER if profile not in _PROFILE_FALLBACK_ORDER else [profile] + [
        p for p in _PROFILE_FALLBACK_ORDER if p != profile
    ]

    last_error = "unknown"
    for try_profile in profiles_to_try:
        try:
            session = await _get_or_create_session(try_profile)
            if session is not None:
                return True, session, try_profile
        except Exception as e:
            last_error = str(e)
            continue

    return False, None, f"session_creation_failed: {last_error}"


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
    except Exception:
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
            logger.debug(
                f"curl_cffi session acquired from prewarm pool for profile: {used}"
            )
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
            new_session = AsyncSession(
                impersonate=profile,
                timeout=10.0,
                max_clients=25,  # F273H: increased from 15 for better connection reuse
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
                    except Exception as e:
                        logger.debug(f"Failed to close evicted session: {e}")

            safe_create_task(_close_evicted(), name="curl_cffi:close_evicted")


async def close_curl_cffi_sessions_async() -> None:
    """
    Close all cached curl_cffi sessions (profile + host cache).
    Idempotent — safe to call multiple times.
    CancelledError is re-raised.
    """
    global _curl_cffi_sessions, _curl_cffi_profiles_order, _host_sessions, _host_access_order

    await asyncio.sleep(0)  # yield to event loop before closing

    async with _curl_cffi_lock:
        profile_sessions = list(_curl_cffi_sessions.values())
        _curl_cffi_sessions.clear()
        _curl_cffi_profiles_order.clear()

        host_sessions = [s for s, _, _ in _host_sessions.values()]
        _host_sessions.clear()
        _host_access_order.clear()

    for session in profile_sessions + host_sessions:
        try:
            if hasattr(session, "aclose"):
                await session.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Failed to close curl_cffi session: {e}")

    logger.debug(f"curl_cffi sessions closed: {len(profile_sessions)} profiles + {len(host_sessions)} hosts")


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
    except Exception:
        return None

    try:
        from .http3_lane import (
            _altsvc_advertises_h3,
            _cache_get,
            _cache_put,
            _resolve_enabled,
        )
        from .http3_lane import (
            extract_host as _http3_extract_host,
        )
        from hledac.universal.fetching.public_fetcher import url_ops

        _use_extract_host = url_ops.extract_host if hasattr(url_ops, 'extract_host') else _http3_extract_host
    except Exception:
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
    except Exception as e:
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
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi through Tor SOCKS5H proxy with circuit rotation.

    Args:
        url: URL to fetch (.onion only)
        headers: Optional HTTP headers
        timeout_s: Request timeout
        max_bytes: Max bytes to read
        profile: TLS profile for JA3 fingerprint
        tor_manager: TorManager instance for circuit rotation (NEWNYM via stem)
        circuit_rotation_count: Rotate circuit every N requests (default 50)
    """
    global _tor_curl_request_count
    _tor_curl_request_count += 1
    count = _tor_curl_request_count

    if tor_manager is not None and count >= circuit_rotation_count:
        _tor_curl_request_count = 0
        try:
            await tor_manager.rotate_circuit()
        except Exception as e:
            logger.warning(f"[TOR] circuit rotation failed: {e}")

    proxies = {"https": _TOR_CURL_PROXY}
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
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi through I2P SOCKS5H proxy.

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
    """
    proxies = {"https": _I2P_CURL_PROXY}
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
) -> dict[str, Any]:
    """
    Fetch URL via curl_cffi stealth lane.

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
    except Exception as e:
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
        kwargs = {"headers": _merged_headers, "timeout": timeout_s}
        if proxies:
            kwargs["proxies"] = proxies
        if http_version is not None:
            kwargs["http_version"] = http_version
        response = await session.get(url, **kwargs)

        chunks = response.iter_content(chunk_size=65536)
        content_bytes, _truncated = await read_body_with_cap(chunks, max_bytes)
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
    except Exception as e:
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
async def fetch_via_curl_cffi_cached(
    url: str,
    headers: dict[str, str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    profile: str = "chrome136",
    proxies: dict[str, str] | None = None,
    http_version: Any = None,
    *,
    ttl_s: int = 3600,
    _force_refresh: bool = False,
    _pre_probe: bool = False,
) -> dict[str, Any]:
    """fetch_via_curl_cffi with conditional-GET (304) shortcut.

    Args:
        url: Same as fetch_via_curl_cffi.
        headers: Caller headers; ``If-None-Match`` / ``If-Modified-Since``
            from the cache are MERGED in (caller wins on conflict).
        timeout_s: Same as fetch_via_curl_cffi.
        max_bytes: Same as fetch_via_curl_cffi.
        profile: TLS profile (passed through).
        proxies: Same as fetch_via_curl_cffi.
        http_version: HttpVersion.v3 from http3_lane (passed through).
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
    from .conditional_cache import (
        conditional_headers_for,
        lookup as _cc_lookup,
        record_conditional_result as _cc_record,
        store as _cc_store,
    )
    from hledac.universal.utils.rate_limiters import get_limiter

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
    if _pre_probe and http_version is None and not _force_refresh:
        _url_lower = url.lower() if url else ""
        _is_dark = _url_lower.endswith(".onion") or ".i2p" in _url_lower or ".b32.i2p" in _url_lower
        if not _is_dark:
            try:
                await _blocking_altsvc_probe_for_url(url)
                from .http3_lane import (
                    extract_host as _probe_extract_host,
                    _cache_get,
                )
                _probe_host = _probe_extract_host(url)
                if _probe_host and _cache_get(_probe_host) is True:
                    try:
                        from curl_cffi.requests import HttpVersion as _HttpVersion  # type: ignore[unresolved-import]
                        http_version = _HttpVersion.v3
                    except Exception:  # noqa: BLE001
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass

    merged_headers: dict[str, str] = dict(headers) if headers else {}
    sent_conditional = False

    if not _force_refresh:
        try:
            cache_headers = conditional_headers_for(url, ttl_s=ttl_s)
            if cache_headers:
                for k, v in cache_headers.items():
                    if k not in merged_headers:
                        merged_headers[k] = v
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
    )

    if not result.get("success"):
        return result

    status = int(result.get("status_code", 0) or 0)

    if status == 304:
        try:
            entry = _cc_lookup(url)
            if entry is not None and entry.body:
                if sent_conditional:
                    _cc_record(url, sent=True, response_status=status)
                # entry.body is already `bytes` from conditional_cache LMDB store
                result["content"] = entry.body
                result["final_url"] = url
                result["conditional_304"] = True
                return result
        except Exception:  # noqa: BLE001
            pass
        result["conditional_304"] = True
        return result

    if 200 <= status < 300:
        try:
            resp_headers = result.get("headers") or {}
            etag = ""
            last_modified = ""
            content_type = ""
            for k, v in resp_headers.items():
                kl = k.lower()
                if kl == "etag":
                    etag = str(v)
                elif kl == "last-modified":
                    last_modified = str(v)
                elif kl == "content-type":
                    content_type = str(v)
            body_bytes = result.get("content", b"") or b""
            sha_hex = ""
            try:
                import hashlib
                sha_hex = hashlib.sha256(body_bytes).hexdigest()
            except Exception:  # noqa: BLE001
                pass
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
