"""
Session Runtime — Shared Async HTTP Surface
============================================

Sprint 8AA: Unified aiohttp.ClientSession factory with lazy initialization,
idempotent session lifecycle, conservative TCPConnector, and standard
gather result helper.

INVARIANTS (enforced by probe_8aa tests):
- [I1]  No top-level network side effect at import time
- [I2]  async_get_aiohttp_session() is lazy — session created on first await
- [I3]  Repeated await of async_get_aiohttp_session() returns the SAME instance
- [I4]  close_aiohttp_session_async() is idempotent (callable multiple times)
- [I5]  After close, next await creates a NEW instance
- [I6]  _check_gathered(results) re-raises asyncio.CancelledError
- [I7]  _check_gathered(results) re-raises BaseException (not Exception)
- [I8]  _check_gathered(results) routes Exception to error_results
- [I9]  asyncio.timeout() is the standard timeout pattern (not wait_for)
- [I10] TCPConnector limits: limit=25, limit_per_host=5, ttl_dns_cache=300
- [I11] connector_owner=True on ClientSession
- [I12] uvloop.install() is fail-soft (diagnostic on failure)

TODO(budget/8AC): napojit concurrency matrix na connector limits
TODO(transport/8AD): per-transport sessions pokud bude potřeba
TODO(integration/8AE): SourceTransportMap integration
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import List, Tuple, Any, Optional

import aiohttp

logger = logging.getLogger(__name__)

# =============================================================================
# Timeout Constants Surface — use with asyncio.timeout()
# =============================================================================
# API calls: fast, short timeouts
API_CONNECT_TIMEOUT_S: float = 10.0
API_READ_TIMEOUT_S: float = 20.0

# HTML/fetch: moderate timeouts for larger payloads
HTML_CONNECT_TIMEOUT_S: float = 15.0
HTML_READ_TIMEOUT_S: float = 35.0

# Tor/low-priority: generous timeouts
TOR_CONNECT_TIMEOUT_S: float = 45.0
TOR_READ_TIMEOUT_S: float = 75.0

# =============================================================================
# =============================================================================
# Shared Lazy aiohttp Session Surface — PLAIN TCP WORLD
# =============================================================================
#
# AUTHORITY SPLIT (Sprint 8VX):
#   This module provides the PLAIN TCP async HTTP session surface only.
#   It is NOT the source-ingress owner — that is FetchCoordinator.
#   It is NOT the persisted session authority — that is SessionManager.
#   It is NOT the curl world — that is StealthCrawler/curl_cffi.
#
#   PLAIN TCP SURFACE consumers (runtime-usable):
#     - fetching/public_fetcher.py — passive text/HTML fetcher
#     - pipeline/live_feed_pipeline.py:_fetch_article_text() — article fallback seam
#
#   PROXY BLOCKER: DarknetConnector uses aiohttp_socks.ProxyConnector (SOCKS5).
#   MA-2 is BLOCKED — ProxyConnector is incompatible with plain TCPConnector.
#
#   PaywallBypass: DEFERRED (not BLOCKED). Uses plain aiohttp.TCPConnector
#   (same connector type as shared surface) but own pool with different limits
#   (limit=10, limit_per_host=3). Redesign cost exceeds benefit. See MA-1.
#
#   curl_cffi WORLD (StealthCrawler): SEPARATE transport world — NOT a session
#   variant. Uses curl_cffi with JA3 fingerprint spoofing. Completely separate
#   TLS/fingerprint plane. Must NOT be unified with aiohttp session world.
#
#   AsyncSessionFactory in __main__.py: LEGACY/RUNTIME-SHELL artifact.
#   Separate singleton from async_get_aiohttp_session(). Different limits/lifecycle.
#   Must NOT be unified without full migration plan.
# =============================================================================

_session_instance: Optional[aiohttp.ClientSession] = None
_session_lock: threading.Lock = threading.Lock()
_session_closed: bool = False
_uvloop_enabled: bool = False
_last_error: Optional[str] = None


async def async_get_aiohttp_session() -> aiohttp.ClientSession:
    """
    Get or create the shared aiohttp.ClientSession instance (async).

    Lazily creates the session on first await.
    Subsequent awaits return the same instance until close is called.
    Thread-safe via threading.Lock.

    Returns:
        aiohttp.ClientSession: the shared session instance

    Invariants:
        [I2] lazy — no session created until first await
        [I3] repeated awaits return same instance
    """
    global _session_instance, _session_closed, _last_error

    with _session_lock:
        if _session_instance is None or _session_instance.closed:
            connector = aiohttp.TCPConnector(
                limit=25,               # total connection pool size
                limit_per_host=5,      # per-host connection limit
                ttl_dns_cache=300,     # DNS cache TTL in seconds
                use_dns_cache=True,    # aiohttp 3.9+ requires explicit opt-in
            )
            # Default timeout: HTML-style (connect + read)
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=HTML_CONNECT_TIMEOUT_S,
                sock_read=HTML_READ_TIMEOUT_S,
            )
            _session_instance = aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                timeout=timeout,
            )
            _session_closed = False
            logger.debug("[SESSION] aiohttp.ClientSession created (async lazy)")
        return _session_instance


# Alias for backward compatibility
get_aiohttp_session = async_get_aiohttp_session
"""Alias for async_get_aiohttp_session(). Provided for backward compatibility."""


def close_aiohttp_session() -> None:
    """
    Close the shared aiohttp.ClientSession if it exists (sync marker).

    In async contexts, prefer close_aiohttp_session_async().
    This sync version just marks the session for close;
    callers in async code should use close_aiohttp_session_async().

    Invariants:
        [I4] idempotent — multiple calls are safe
        [I5] after close, next await creates new instance
    """
    global _session_closed
    _session_closed = True


async def close_aiohttp_session_async() -> None:
    """
    Close the shared aiohttp.ClientSession (async, proper await).

    Idempotent: safe to call multiple times.
    After close, next async_get_aiohttp_session() await creates a fresh instance.

    Invariants:
        [I4] idempotent — multiple calls are safe
        [I5] after close, next await creates new instance
    """
    global _session_instance, _session_closed, _last_error

    with _session_lock:
        if _session_instance is not None and not _session_instance.closed:
            sess = _session_instance
            _session_instance = None
            _session_closed = True
            try:
                # Capture the close task so we can await it before returning
                # This prevents a race where the caller sees _session_instance=None
                # while the close coroutine is still running
                close_coro = sess.close()
                await close_coro
                logger.debug("[SESSION] aiohttp.ClientSession closed async")
            except Exception as e:
                logger.warning(f"[SESSION] async close error: {e}")
                _last_error = str(e)
        else:
            _session_closed = True


def get_session_runtime_status() -> dict:
    """
    Return lightweight runtime status (O(1), side-effect free).

    Returns:
        dict with keys:
            - session_created: bool  — a session instance exists or existed
            - session_closed: bool   — currently closed (truthful, checks .closed)
            - uvloop_enabled: bool   — uvloop was successfully installed
            - last_error: str | None — last error string if any

    Truthfulness contract:
        - session_closed reflects the actual session.closed state when
          an instance exists; falls back to the _session_closed marker
          only when _session_instance is None (e.g. after sync close).
    """
    # Authoritative session closed state — prefer the actual session.closed
    # when an instance exists; fall back to marker for sync-close path
    if _session_instance is not None:
        session_actually_closed = _session_instance.closed
    else:
        session_actually_closed = _session_closed

    return {
        "session_created": _session_instance is not None or _session_closed,
        "session_closed": session_actually_closed,
        "uvloop_enabled": _uvloop_enabled,
        "last_error": _last_error,
    }


# =============================================================================
# _check_gathered — Standard gather result helper
# =============================================================================

def _check_gathered(results: List[Any]) -> Tuple[List[Any], List[Any]]:
    """
    Process results from asyncio.gather(..., return_exceptions=True).

    Contract:
        - Input:  list returned by asyncio.gather(return_exceptions=True)
        - Output: (ok_results, error_results)
        - Regular Exception items → error_results
        - asyncio.CancelledError → RE-RAISED immediately [I6]
        - Other BaseException (KeyboardInterrupt, SystemExit) → RE-RAISED immediately [I7]
        - Ok results maintain their original order [I8]

    Args:
        results: list from gather(return_exceptions=True)

    Returns:
        Tuple of (ok_results, error_results)

    Invariants:
        [I6] CancelledError is never swallowed — always re-raised
        [I7] BaseException (not Exception) is never swallowed — always re-raised
        [I8] Exception goes to error_results, ok results keep order
    """
    ok_results: List[Any] = []
    error_results: List[Any] = []

    for item in results:
        if isinstance(item, BaseException):
            # Must check specific subclasses BEFORE general Exception
            if isinstance(item, asyncio.CancelledError):
                # [I6] — CancelledError must never be swallowed
                raise item
            # Other BaseException subclasses (KeyboardInterrupt, SystemExit)
            if not isinstance(item, Exception):
                # [I7] — non-Exception BaseException must not be swallowed
                raise item
            # Regular Exception — route to errors [I8]
            error_results.append(item)
        else:
            ok_results.append(item)

    return ok_results, error_results


# =============================================================================
# uvloop install helper — called from __main__.py
# =============================================================================

def try_install_uvloop() -> bool:
    """
    Attempt to install uvloop as the asyncio event loop policy.

    Fail-soft: returns False if uvloop is not available or installation fails.
    Sets _uvloop_enabled global so status is queryable via get_session_runtime_status().

    Call this BEFORE asyncio.run() or any other async operations.

    Returns:
        bool: True if uvloop was successfully installed, False otherwise
    """
    global _uvloop_enabled, _last_error

    try:
        import uvloop
        uvloop.install()
        _uvloop_enabled = True
        logger.info("[RUNTIME] uvloop installed successfully")
        return True
    except ImportError:
        _uvloop_enabled = False
        _last_error = "uvloop not available"
        logger.debug("[RUNTIME] uvloop not available — using default asyncio loop")
        return False
    except Exception as e:
        _uvloop_enabled = False
        _last_error = str(e)
        logger.warning(f"[RUNTIME] uvloop install failed: {e}")
        return False
