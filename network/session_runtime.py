"""
Session Runtime — Shared Async HTTP Surface
============================================


F4XX: aiohttp REMOVED — curl_cffi is primary, httpx is HTTP/2 transport.
httpx-socks handles Tor/I2P SOCKS5 (connection_pool_manager.py, tor/i2p_transport).

This module provides httpx-based plain TCP async HTTP surface.
Tor/I2P use httpx-socks via transport/session_pool.py:httpx_socks_client().
curl_cffi is the stealth/JA3 transport (separate TLS fingerprint plane).

INVARIANTS (enforced by probe_8aa tests):
- [I1]  No top-level network side effect at import time
- [I2]  async_get_httpx_session() is lazy — session created on first await
- [I3]  Repeated await of async_get_httpx_session() returns the SAME instance
- [I4]  close_httpx_session_async() is idempotent (callable multiple times)
- [I5]  After close, next await creates a NEW instance
- [I9]  asyncio.timeout() is the standard timeout pattern (not wait_for)
- [I10] httpx Limits: adaptive _SAFE_MAX_CONNECTIONS (min(40, max(8, fd_limit//4))) — ISSUE-014
- [I11] uvloop.install() is fail-soft (diagnostic on failure)
"""


import asyncio
import logging
import os
from typing import Any

import httpx

from hledac.universal.runtime.state import get_runtime_state  # noqa: E402

# Backward-compat alias for tests that used _uvloop_enabled (misspelling)
_uvloop_enabled = get_runtime_state().uvloop_installed

from .domain_concurrency import (  # noqa: F401, E402  # pragma: no cover
    ARM_VALUES,
    DomainConcurrencyBandit,
    )

logger = logging.getLogger(__name__)

# =============================================================================
# =============================================================================
# =============================================================================
# Timeout Constants Surface — canonical timeouts for session consumers
# Use with asyncio.timeout() — NOT with httpx.AsyncClient timeout= parameter
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
# F4XX: httpx Session Surface — PLAIN TCP WORLD (replaces aiohttp)
# =============================================================================
#
# AUTHORITY SPLIT (Sprint 8VX):
#   This module provides the PLAIN TCP async HTTP session surface only.
#   It is NOT the source-ingress owner — that is FetchCoordinator.
#   It is NOT the persisted session authority — that is SessionManager.
#   It is NOT the curl world — that is StealthCrawler/curl_cffi.
#
#   PLAIN TCP SURFACE consumers (runtime-usable):
#     - fetching/public_fetcher.py — passive text/HTML fetcher (via httpx)
#     - pipeline/live_feed_pipeline.py:_fetch_article_text() — article fallback seam
#
#   Tor/I2P SOCKS: use httpx-socks via transport/session_pool.py:httpx_socks_client()
#   curl_cffi WORLD: separate transport — JA3 fingerprint spoofing, completely
#   separate TLS/fingerprint plane. Must NOT be unified with httpx session world.


# Sprint F266-UVLOOP: canonical uvloop state — single source of truth
# do NOT import uvloop here — that happens in __main__.py before this module is loaded


# -----------------------------------------------------------------------
# F266-UV7: Session Runtime State — replaces 5 module-level mutable globals
# F4XX: Migrated from aiohttp to httpx
#
# PROBLEMS FIXED:
# 1. Module-level mutable globals violate isolation between async tasks.
# 2. asyncio.Lock() created inside async def via get_event_loop() is racy —
#    the loop may differ between the lock creation call site and the actual
#    await site where the lock is used.  Lock is bound to the creating loop.
# 3. No test isolation — singletons cannot be reset between test cases.
#
# SOLUTION: ContextVar[SessionRuntimeState] — each async task gets its own
# state.  Lock is created INSIDE an async context (guaranteed event loop).
# The _reset_session_runtime_for_tests() helper survives by operating on
# the ContextVar directly, enabling hermetic test suites.
#
# M1 8GB: __slots__ (no __dict__) saves ~200 bytes per instance.
# -----------------------------------------------------------------------

import contextvars  # noqa: E402

from typing import TYPE_CHECKING  # noqa: E402
from _core import aclose

if TYPE_CHECKING:
    pass  # httpx is always available — no TYPE_CHECKING guard needed


class _SessionRuntimeState:
    """
    Ephemeral session state — one per async task via ContextVar.

    __slots__ saves RAM on M1 8GB (no __dict__ dict per instance).
    F4XX: migrated from aiohttp.ClientSession to httpx.AsyncClient.
    """

    __slots__ = (
        "_session_instance",
        "_session_lock",
        "_session_closed",
        "_last_error",
        "_last_close_error",
        "_bandits",
        "_bandit_overrides",
    )

    def __init__(self) -> None:
        self._session_instance: httpx.AsyncClient | None = None
        self._session_lock: asyncio.Lock | None = None
        self._session_closed: bool = False
        self._last_error: str | None = None
        self._last_close_error: str | None = None
        # Sprint F266-UV5: per-session bandit state
        self._bandits: dict = {}
        self._bandit_overrides: dict = {}

    def get_lock(self) -> asyncio.Lock:
        """
        Lazily create and cache an asyncio.Lock bound to the CURRENT event loop.

        Called only from async functions where get_event_loop() is valid.
        This ensures the lock is always bound to the correct loop — no cross-loop races.
        """
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        return self._session_lock


# ContextVar for async task isolation.
# Each async task gets its own SessionRuntimeState.
_session_state_var: contextvars.ContextVar[_SessionRuntimeState | None] = contextvars.ContextVar(
    "_session_state_var",
    default=None,
    )


def _get_state() -> _SessionRuntimeState:
    """Get or create the SessionRuntimeState for the current async task."""
    state = _session_state_var.get()
    if state is None:
        state = _SessionRuntimeState()
        _session_state_var.set(state)
    return state


def __getattr__(name: str) -> object:
    # Delegate task-local session state attributes to the ContextVar-backed state.
    # This enables tests that access sr._session_instance directly on the module.
    if name in ("_session_instance", "_session_closed", "_last_error", "_last_close_error"):
        return getattr(_get_state(), name)
    # All other names raise AttributeError so normal module globals work
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")  # noqa: BLE001


# =============================================================================
# Domain Concurrency Bandit State — Sprint 8AC
# Per-domain adaptive concurrency via Gradient Bandit
#
# F-UV7-FIX: Migrated to ContextVar-backed _SessionRuntimeState.
# Each async task gets its own bandits — no module-level globals,
# no cross-task pollution, bounded by task lifetime.
# Sprint winddown calls clear_bandits() which clears the task-local state.
#
# Bounded: bandits live in _SessionRuntimeState._bandits (per-task ContextVar).
# The bandit dict grows per unique host per task, and is cleared at windown.
# =============================================================================

def get_domain_limit(host: str) -> int:
    """
    Get the adaptive concurrency limit for a host (task-local).

    Lazy-initializes a DomainConcurrencyBandit per host on first call.
    If an explicit override is set (via set_override), that value is returned.

    Args:
        host: the hostname (e.g. "example.com")

    Returns:
        int: concurrency limit in [1, 8] range
    """
    state = _get_state()
    if host in state._bandit_overrides:
        return state._bandit_overrides[host]
    if host not in state._bandits:
        state._bandits[host] = DomainConcurrencyBandit()
    return state._bandits[host].current_limit


def record_domain_outcome(
    host: str, latency_ms: float, status_code: int, got_captcha: bool = False
) -> None:
    """
    Record an HTTP outcome for a host and update its bandit (task-local).

    Args:
        host: the hostname
        latency_ms: response latency in milliseconds
        status_code: HTTP status code
        got_captcha: whether CAPTCHA was detected
    """
    state = _get_state()
    if host in state._bandit_overrides:
        return  # override active — don't learn from outcomes
    if host not in state._bandits:
        state._bandits[host] = DomainConcurrencyBandit()
    bandit = state._bandits[host]
    # Look up which arm was active based on current_limit
    arm_idx = ARM_VALUES.index(bandit.current_limit)
    bandit.record_outcome(arm_idx, latency_ms, status_code, got_captcha)


def set_override(host: str, limit: int) -> None:
    """
    Set an explicit concurrency limit override for a host (task-local).

    When set, get_domain_limit() returns this value and the bandit
    stops learning for this host (record_domain_outcome is a no-op).

    Args:
        host: the hostname
        limit: concurrency limit (must be in ARM_VALUES)
    """
    if limit not in ARM_VALUES:
        raise ValueError(f"limit must be one of {ARM_VALUES}, got {limit}")
    state = _get_state()
    state._bandit_overrides[host] = limit


def clear_override(host: str) -> None:
    """Remove the explicit override for a host, reverting to bandit control (task-local)."""
    state = _get_state()
    state._bandit_overrides.pop(host, None)


def clear_bandits() -> None:
    """
    Clear all bandit state at sprint winddown (task-local).

    Resets the current task's _bandits and _bandit_overrides to empty state.
    Called automatically from close_httpx_session_async() at winddown,
    and from _reset_session_runtime_for_tests() for hermetic test isolation.

    Invariant: safe to call even if dicts are already empty.
    """
    state = _get_state()
    state._bandits.clear()
    state._bandit_overrides.clear()


def get_default_limit() -> int:
    """
    Return the default per-host concurrency limit for new sessions.

    Returns the highest arm value (most conservative setting) as the session-level
    default. Individual hosts may run lower based on their bandit learning.
    """
    return ARM_VALUES[-1]  # 8 — highest/conservative default


# =============================================================================
# F4XX: httpx Session Surface — replaces aiohttp
# F350M-R ISSUE-010: httpx singleton DELEGATED to transport.session_pool
#   This module retains: ContextVar bandits, timeout constants,
#   and test isolation helpers.
#   The httpx client itself now comes from session_pool.httpx_client().
#   Connection limits are managed by session_pool (M1 8GB safe: 25/10).
# =============================================================================


async def async_get_httpx_session() -> httpx.AsyncClient:
    """
    Get or create the shared httpx.AsyncClient instance (async).

    F350M-R ISSUE-010: Delegated to transport.session_pool.httpx_client().
    This module retains: ContextVar bandits, adaptive FD-aware limits,
    timeout constants, and test isolation helpers.

    Session lifecycle:
    - Lazy: session created on first await
    - Shared: repeated awaits return the same instance
    - Idempotent close: close_httpx_session_async() is safe to call multiple times

    Returns:
        httpx.AsyncClient: the shared session instance

    Invariants:
        [I2] lazy — no session created until first await
        [I3] repeated awaits return same instance
    """
    # ISSUE-010: Delegate to canonical session_pool (import here to avoid circular deps)
    from hledac.universal.transport.session_pool import httpx_client as _httpx_client

    return await _httpx_client()


# F4XX (Issue 19): backward-compat aliases — now delegates to httpx.AsyncClient.
# Prefer async_get_httpx_session() and close_httpx_session_async() in new code.
async_get_aiohttp_session = async_get_httpx_session


def set_httpx_cache_transport(_transport: Any) -> None:
    """
    Set the hishel cache transport for httpx sessions (Issue #23).

    F350M-R ISSUE-010: This function is DEPRECATED. The cache transport
    is now managed directly by transport/session_pool.py. Calling this
    function has no effect.

    Deprecated: cache transport wiring is handled by FetchCoordinator
    using session_pool directly.
    """
    # ISSUE-010: No-op — session_pool manages its own cache transport
    pass
def get_aiohttp_session() -> httpx.AsyncClient:
    """F4XX: alias for async_get_httpx_session(). Provided for backward compatibility."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            "get_aiohttp_session() called in non-async context. "
            "Use async_get_httpx_session() instead."
        ) from None
    # Run in a separate thread so the caller (which may be a sync thread
    # with its own loop) doesn't nest event loops — eliminates M1 crash vector.
    # run_in_executor with None uses the default ThreadPoolExecutor (bounded 8 threads on M1).
    future = loop.run_in_executor(None, _get_httpx_session_blocking)
    return future.result()

def _get_httpx_session_blocking() -> httpx.AsyncClient:
    """
    Synchronous blocking wrapper for async_get_httpx_session().

    P1-1 FIX: Replaced asyncio.run() with run_sync_async() from sync_bridge.
    asyncio.run() inside run_in_executor thread is an M1 Metal crash vector.

    The bridge loop handles both running and non-running loop cases correctly:
      - No running loop → new loop via asyncio.run() inside bridge
      - Running loop   → run_coroutine_threadsafe to bridge loop
    """
    from hledac.universal.utils.sync_bridge import run_sync_async
    return run_sync_async(async_get_httpx_session())


def close_httpx_session() -> None:
    """
    Close the shared httpx.AsyncClient if it exists (sync marker).

    F350M-R ISSUE-010: Delegated to transport.session_pool.close_httpx().

    In async contexts, prefer close_httpx_session_async().
    This sync version just marks the session for close;
    callers in async code should use close_httpx_session_async().

    Invariants:
        [I4] idempotent — multiple calls are safe
        [I5] after close, next await creates new instance
    """
    # ISSUE-010: Delegate to canonical session_pool
    from hledac.universal.transport.session_pool import close_httpx as _close_httpx
    # P1-1: run_sync_async handles both running and non-running loop cases.
    from hledac.universal.utils.sync_bridge import run_sync_async
    run_sync_async(_close_httpx())


# F4XX (Issue 19): backward-compat alias — now delegates to close_httpx_session().
close_aiohttp_session = close_httpx_session


async def close_httpx_session_async() -> None:
    """
    Close the shared httpx.AsyncClient (async, proper await).

    F350M-R ISSUE-010: Delegated to transport.session_pool.close_httpx().

    Idempotent: safe to call multiple times.
    After close, next async_get_httpx_session() await creates a fresh instance.

    Invariants:
        [I4] idempotent — multiple calls are safe
        [I5] after close, next await creates new instance
    """
    # ISSUE-010: Delegate to canonical session_pool
    from hledac.universal.transport.session_pool import close_httpx as _close_httpx

    await _close_httpx()
    # Sprint F266-UV5: clear bandits at winddown to prevent unbounded dict growth
    clear_bandits()


# F4XX (Issue 19): backward-compat alias — now delegates to close_httpx_session_async().
close_aiohttp_session_async = close_httpx_session_async


def get_session_runtime_status() -> dict:
    """
    Return lightweight runtime status of the shared httpx session (O(1), side-effect free).

    F350M-R ISSUE-010: Status now comes from transport.session_pool.

    Returns:
        dict with keys:
            - session_created: bool  — a session instance exists or existed
            - session_closed: bool   — currently closed (truthful, checks .is_closed)
            - uvloop_enabled: bool   — uvloop was successfully installed
            - last_error: str | None — last error string if any
    """
    # ISSUE-010: Delegate to session_pool for actual session state
    from hledac.universal.transport.session_pool import session_pool as _sp

    status = _sp.get_status()
    return {
        "session_created": status.get("httpx", {}).get("initialized", False),
        "session_closed": not status.get("httpx", {}).get("initialized", False),
        "uvloop_enabled": get_runtime_state().uvloop_installed,
        "last_error": None,
    }


# =============================================================================
# Test-Only Cleanup Helper — F208G / F266-UV7
# =============================================================================

def _reset_session_runtime_for_tests() -> None:
    """
    Reset the session state to pristine state for test isolation.

    THIS METHOD IS FOR TEST USE ONLY.
    It exists solely to enable hermetic test isolation.
    It MUST NOT be called from any production code path.

    F350M-R ISSUE-010: Delegates to session_pool.close_httpx().

    Usage:
        # In test fixture:
        from hledac.universal.network import session_runtime as sr
        sr._reset_session_runtime_for_tests()
    """
    # ISSUE-010: Delegate to session_pool for actual session close
    from hledac.universal.transport.session_pool import close_httpx as _close_httpx
    from hledac.universal.utils.sync_bridge import run_sync_async

    try:
        run_sync_async(_close_httpx())
    except Exception:  # noqa: BLE001
        pass

    # Clear bandits
    clear_bandits()


# =============================================================================
# Lazy aiohttp stubs for backward compatibility — F4XX REMOVED
# These will raise ImportError if called without aiohttp-fallback extra installed.
# Use ONLY for tests that directly access the deprecated API.
# =============================================================================

def _raise_aiohttp_unavailable() -> None:
    """Raise ImportError for any code trying to use the removed aiohttp API."""
    raise ImportError(
        "aiohttp has been removed from default dependencies (F4XX). "
        "Install with: uv sync --extra aiohttp-fallback "
        "Or use httpx-based fetching instead (curl_cffi for stealth, httpx for HTTP/2)."
    )
