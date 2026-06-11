"""
transport/curl_cffi_runtime.py

Canonical, lazy, bounded curl_cffi session runtime.
Optional stealth escalation lane — project falls back gracefully if curl_cffi is missing.

Invariant: lazy import inside functions, never module-level.
Invariant: bounded LRU session cache, max 3 profiles.
Invariant: await aclose() outside lock.
Invariant: close is idempotent.

F273H: Per-host session cache — keeps TCP+TLS connections warm per host.
 - _host_sessions: host → (session, last_access, profile)
  - _MAX_HOST_SESSIONS = 20 (LRU eviction, ~1MB RAM on M1 8GB)
  - _HOST_SESSION_TTL_S = 300 (5 min idle, resets on each request)
  - session.get() reuses persistent connection to same host
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

# Module-level guard — set once at first availability check
_CURL_CFFI_AVAILABLE: bool | None = None
_CURL_CFFI_IMPORT_ERROR: str | None = None

# Bounded session cache: profile -> AsyncSession
# max 3 profiles as specified
_MAX_CURL_CFFI_PROFILES = 3
_curl_cffi_sessions: dict[str, Any] = {}
_curl_cffi_lock = asyncio.Lock()
_curl_cffi_profiles_order: deque[str] = deque()  # track access order for LRU via popleft()

# F273H: Per-host session cache — host -> (session, last_access_monotonic, profile)
_MAX_HOST_SESSIONS = 20
_HOST_SESSION_TTL_S = 300.0
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
    """
    global _CURL_CFFI_AVAILABLE, _CURL_CFFI_IMPORT_ERROR

    if _CURL_CFFI_AVAILABLE is not None:
        return _CURL_CFFI_AVAILABLE, _CURL_CFFI_IMPORT_ERROR or "ok"

    try:
        from curl_cffi.requests import AsyncSession  # noqa: F401  # curl_cffi.requests.AsyncSession

        _CURL_CFFI_AVAILABLE = True
        _CURL_CFFI_IMPORT_ERROR = None
        logger.debug("curl_cffi is available")
        return True, "ok"
    except ImportError as e:
        _CURL_CFFI_AVAILABLE = False
        _CURL_CFFI_IMPORT_ERROR = str(e)
        logger.debug(f"curl_cffi not available: {e}")
        return False, f"import_error: {e}"


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

    # Normalize profile — try preferred, fall back through chain
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
    # Lazy import — urllib.parse is stdlib, no extra cost
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
        # Fall back to profile-based session if URL is unparseable
        ok, sess, prof = await async_get_curl_cffi_session(profile)
        return ok, sess, prof, ""

    # Fast path: check host cache under lock
    async with _curl_cffi_lock:
        now = time.monotonic()
        if host in _host_sessions:
            session, last_access, cached_profile = _host_sessions[host]
            # TTL check
            if now - last_access < _HOST_SESSION_TTL_S:
                # Move to end (refresh LRU)
                if host in _host_access_order:
                    _host_access_order.remove(host)
                _host_access_order.append(host)
                # Update last access time
                _host_sessions[host] = (session, now, cached_profile)
                logger.debug(f"[F273H] host cache hit: {host}")
                return True, session, cached_profile, host
            else:
                # Expired — evict
                try:
                    if hasattr(session, "aclose"):
                        asyncio.create_task(
                            session.aclose(),
                            name=f"curl_cffi:host_expire:{host}",
                        )
                except Exception:
                    pass
                del _host_sessions[host]
                if host in _host_access_order:
                    _host_access_order.remove(host)

    # Miss: get or create profile session, then cache per-host
    ok, session, used_profile = await async_get_curl_cffi_session(profile)
    if not ok or session is None:
        return False, None, used_profile, host

    # Store in host cache with LRU eviction
    async with _curl_cffi_lock:
        now = time.monotonic()
        # Evict oldest if at capacity
        while len(_host_sessions) >= _MAX_HOST_SESSIONS and _host_access_order:
            oldest_host = _host_access_order.popleft()
            if oldest_host in _host_sessions:
                old_session, _, _ = _host_sessions.pop(oldest_host)
                try:
                    if hasattr(old_session, "aclose"):
                        asyncio.create_task(
                            old_session.aclose(),
                            name=f"curl_cffi:host_evict:{oldest_host}",
                        )
                except Exception:
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
    # Fast path: already cached
    if profile in _curl_cffi_sessions:
        # Move to end (most recently used)
        if profile in _curl_cffi_profiles_order:
            _curl_cffi_profiles_order.remove(profile)
        _curl_cffi_profiles_order.append(profile)
        session = _curl_cffi_sessions[profile]
        # Verify session is not closed
        if hasattr(session, "closed") and not session.closed:
            return session
        # Session was closed — remove from cache
        del _curl_cffi_sessions[profile]

    # F265B: try prewarm pool first. The pool returns a session that
    # has already completed TCP+TLS handshake against a known-good
    # host; the curl_cffi connection-pool inside the session is warm
    # for any host the caller fetches next. If the pool is disabled
    # or fails, fall through to the original lazy path.
    try:
        from .prewarm_pool import acquire_session as _prewarm_acquire

        ok, sess, used = await _prewarm_acquire(profile)
        if ok and sess is not None:
            # Warm session from the pool. Promote it into the local
            # LRU so subsequent same-profile calls hit the fast path.
            # The pool keeps its own reference for the round-robin
            # swap; we share the session object (idempotent close).
            if profile in _curl_cffi_sessions:
                # Evict any existing entry for this profile.
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
        # Fail-soft: never let a prewarm failure block the lazy path.
        logger.debug(f"prewarm pool acquire failed (fallback to lazy): {e}")

    # Sessions to close after releasing lock (evicted during creation)
    _sessions_to_close: list[Any] = []

    try:
        # Need to create new session
        async with _curl_cffi_lock:
            # Re-check after acquiring lock
            if profile in _curl_cffi_sessions:
                return _curl_cffi_sessions[profile]

            # Evict oldest if at capacity — extract sessions to close OUTSIDE lock
            if len(_curl_cffi_sessions) >= _MAX_CURL_CFFI_PROFILES:
                if _curl_cffi_profiles_order:
                    oldest = _curl_cffi_profiles_order.popleft()  # O(1) vs list.pop(0) O(n)
                    if oldest in _curl_cffi_sessions:
                        _sessions_to_close.append(_curl_cffi_sessions.pop(oldest))

            # Create new session
            from curl_cffi.requests import AsyncSession

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
        # F206AJ: Close evicted sessions AFTER releasing lock.
        # await inside try/finally would still hold the lock during await,
        # blocking all other coroutines. Use create_task to defer.
        if _sessions_to_close:
            async def _close_evicted():
                for _sess in _sessions_to_close:
                    try:
                        if hasattr(_sess, "aclose"):
                            await _sess.aclose()
                    except Exception as e:
                        logger.debug(f"Failed to close evicted session: {e}")

            asyncio.create_task(_close_evicted(), name="curl_cffi:close_evicted")


async def close_curl_cffi_sessions_async() -> None:
    """
    Close all cached curl_cffi sessions (profile + host cache).
    Idempotent — safe to call multiple times.
    CancelledError is re-raised.
    """
    global _curl_cffi_sessions, _curl_cffi_profiles_order, _host_sessions, _host_access_order

    await asyncio.sleep(0)  # yield to event loop before closing

    async with _curl_cffi_lock:
        # Collect profile sessions
        profile_sessions = list(_curl_cffi_sessions.values())
        _curl_cffi_sessions.clear()
        _curl_cffi_profiles_order.clear()

        # F273H: Collect host sessions
        host_sessions = [s for s, _, _ in _host_sessions.values()]
        _host_sessions.clear()
        _host_access_order.clear()

    # Close outside the lock
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
        # F273H: host cache stats
        "host_cache_size": len(_host_sessions),
        "host_cache_capacity": _MAX_HOST_SESSIONS,
        "host_cache_ttl_s": _HOST_SESSION_TTL_S,
    }
