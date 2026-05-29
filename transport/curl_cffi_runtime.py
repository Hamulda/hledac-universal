"""
transport/curl_cffi_runtime.py

Canonical, lazy, bounded curl_cffi session runtime.
Optional stealth escalation lane — project falls back gracefully if curl_cffi is missing.

Invariant: lazy import inside functions, never module-level.
Invariant: bounded LRU session cache, max 3 profiles.
Invariant: await aclose() outside lock.
Invariant: close is idempotent.
"""

from __future__ import annotations

import asyncio
import logging
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

# Preferred profile fallback order
# Targets: academia (Safari 17 Apple Silicon), government (Firefox 133+), mobile/android (Chrome Android 99+)
_PROFILE_FALLBACK_ORDER = [
    "chrome136",
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
        from curl_cffi.requests import AsyncSession

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


async def _get_or_create_session(profile: str) -> Any | None:
    """Internal: get from cache or create new, with bounded LRU."""
    global _curl_cffi_sessions, _curl_cffi_profiles_order

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
                max_clients=15,
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
    Close all cached curl_cffi sessions.
    Idempotent — safe to call multiple times.
    CancelledError is re-raised.
    """
    global _curl_cffi_sessions, _curl_cffi_profiles_order

    await asyncio.sleep(0)  # yield to event loop before closing

    async with _curl_cffi_lock:
        sessions_to_close = list(_curl_cffi_sessions.values())
        _curl_cffi_sessions.clear()
        _curl_cffi_profiles_order.clear()

    # Close outside the lock
    for session in sessions_to_close:
        try:
            if hasattr(session, "aclose"):
                await session.aclose()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Failed to close curl_cffi session: {e}")

    logger.debug(f"curl_cffi sessions closed: {len(sessions_to_close)}")


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
    }
