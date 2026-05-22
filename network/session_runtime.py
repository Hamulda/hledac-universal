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
- [I10] TCPConnector limits: limit=25, limit_per_host=get_default_limit(), ttl_dns_cache=300
- [I11] connector_owner=True on ClientSession
- [I12] uvloop.install() is fail-soft (diagnostic on failure)

# FUTURE(8AC): napojit concurrency matrix na connector limits — DomainConcurrencyBandit (network/domain_concurrency.py)
# FUTURE(8AD): per-transport sessions — implementovat až bude potřeba (SourceTransportMap je k dispozici)
# FUTURE(8AE): SourceTransportMap integration — již částečně integrováno v FetchCoordinator; rozšířit až bude potřeba
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Tuple, Any, Optional

import aiohttp

from hledac.universal.utils.async_helpers import _check_gathered

from .domain_concurrency import (  # noqa: F401  # pragma: no cover
    ARM_VALUES,
    DomainConcurrencyBandit,
)

logger = logging.getLogger(__name__)

# =============================================================================
# =============================================================================
# =============================================================================
# Timeout Constants Surface — canonical timeouts for session consumers
# Use with asyncio.timeout() — NOT with ClientSession timeout= parameter
# =============================================================================
# API calls: fast, short timeouts
API_CONNECT_TIMEOUT_S: float = 10.0
API_READ_TIMEOUT_S: float = 20.0

# HTML/fetch: moderate timeouts for larger payloads
HTML_CONNECT_TIMEOUT_S: float = 15.0
HTML_READ_TIMEOUT_S: float = 35.0

# CT/cert transparency: lightweight JSON, bounded response
CT_CONNECT_TIMEOUT_S: float = 10.0
CT_READ_TIMEOUT_S: float = 15.0

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
_session_lock: asyncio.Lock | None = None
_uvloop_enabled: bool = False


async def _get_session_lock() -> asyncio.Lock:
    """Lazily create the async lock (must be called after event loop is running)."""
    global _session_lock
    if _session_lock is None:
        _session_lock = asyncio.Lock()
    return _session_lock
_last_error: Optional[str] = None
_last_close_error: Optional[str] = None

# =============================================================================
# Domain Concurrency Bandit State — Sprint 8AC
# Per-domain adaptive concurrency via Gradient Bandit
# =============================================================================
_domain_bandits: Dict[str, DomainConcurrencyBandit] = {}
_bandit_overrides: Dict[str, int] = {}  # host → explicit limit override


def get_domain_limit(host: str) -> int:
    """
    Get the adaptive concurrency limit for a host.

    Lazy-initializes a DomainConcurrencyBandit per host on first call.
    If an explicit override is set (via set_override), that value is returned.

    Args:
        host: the hostname (e.g. "example.com")

    Returns:
        int: concurrency limit in [1, 8] range
    """
    if host in _bandit_overrides:
        return _bandit_overrides[host]
    if host not in _domain_bandits:
        _domain_bandits[host] = DomainConcurrencyBandit()
    return _domain_bandits[host].current_limit


def record_domain_outcome(
    host: str, latency_ms: float, status_code: int, got_captcha: bool = False
) -> None:
    """
    Record an HTTP outcome for a host and update its bandit.

    Args:
        host: the hostname
        latency_ms: response latency in milliseconds
        status_code: HTTP status code
        got_captcha: whether CAPTCHA was detected
    """
    if host in _bandit_overrides:
        return  # override active — don't learn from outcomes
    if host not in _domain_bandits:
        _domain_bandits[host] = DomainConcurrencyBandit()
    bandit = _domain_bandits[host]
    # Look up which arm was active based on current_limit
    arm_idx = ARM_VALUES.index(bandit.current_limit)
    bandit.record_outcome(arm_idx, latency_ms, status_code, got_captcha)


def set_override(host: str, limit: int) -> None:
    """
    Set an explicit concurrency limit override for a host.

    When set, get_domain_limit() returns this value and the bandit
    stops learning for this host (record_domain_outcome is a no-op).

    Args:
        host: the hostname
        limit: concurrency limit (must be in ARM_VALUES)
    """
    if limit not in ARM_VALUES:
        raise ValueError(f"limit must be one of {ARM_VALUES}, got {limit}")
    _bandit_overrides[host] = limit


def clear_override(host: str) -> None:
    """Remove the explicit override for a host, reverting to bandit control."""
    _bandit_overrides.pop(host, None)


def get_default_limit() -> int:
    """
    Return the default per-host concurrency limit for new sessions.

    Returns the highest arm value (most conservative setting) as the session-level
    default. Individual hosts may run lower based on their bandit learning.
    """
    return ARM_VALUES[-1]  # 8 — highest/conservative default


async def async_get_aiohttp_session() -> aiohttp.ClientSession:
    """
    Get or create the shared aiohttp.ClientSession instance (async).

    Lazily creates the session on first await.
    Subsequent awaits return the same instance until close is called.
    Thread-safe via asyncio.Lock.

    Returns:
        aiohttp.ClientSession: the shared session instance

    Invariants:
        [I2] lazy — no session created until first await
        [I3] repeated awaits return same instance
    """
    global _session_instance, _session_closed, _last_error

    async with await _get_session_lock():
        if _session_instance is None or _session_instance.closed:
            connector = aiohttp.TCPConnector(
                limit=25,               # total connection pool size
                limit_per_host=get_default_limit(),  # per-host limit (conservative default 8)
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
    global _session_instance, _session_closed, _last_error, _last_close_error

    async with await _get_session_lock():
        if _session_instance is not None and not _session_instance.closed:
            sess = _session_instance
            _session_instance = None
            _session_closed = True
        else:
            _session_closed = True
            return  # No session to close

    # await OUTSIDE lock — close() is fast but we must not hold the lock during await
    try:
        await sess.close()
        logger.debug("[SESSION] aiohttp.ClientSession closed async")
    except Exception as e:
        logger.warning(f"[SESSION] async close error: {e}")
        _last_close_error = str(e)
        _last_error = str(e)


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
        "last_close_error": _last_close_error,
    }


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


# =============================================================================
# Test-Only Cleanup Helper — F208G
# =============================================================================

def _reset_session_runtime_for_tests() -> None:
    """
    Reset all session_runtime module globals to pristine state.

    THIS METHOD IS FOR TEST USE ONLY.
    It exists solely to enable hermetic test isolation.
    It MUST NOT be called from any production code path.

    Usage:
        # In test fixture:
        sr._reset_session_runtime_for_tests()

    This resets: _session_instance, _session_closed, _session_lock
    (NOT _uvloop_enabled — that is a runtime env flag that persists across tests).

    Idempotent: safe to call multiple times within a test.
    After reset, the next await of async_get_aiohttp_session() creates a fresh
    session with pristine connector state.
    """
    global _session_instance, _session_closed, _last_error, _last_close_error, _domain_bandits, _bandit_overrides

    # First ensure any existing session is properly closed
    if _session_instance is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_session_instance.close())
        except Exception:
            pass
        finally:
            loop.close()
            _session_instance = None

    _session_closed = False
    _last_error = None
    _last_close_error = None
    _domain_bandits.clear()
    _bandit_overrides.clear()
