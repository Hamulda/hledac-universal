"""
Session Runtime — Shared Async HTTP Surface
============================================

DEPRECATED: As of Sprint F265+, curl_cffi is the primary transport.
This module is kept for Tor/I2P SOCKS fallback ONLY when
HLEDAC_ENABLE_AIOHTTP_FALLBACK=1 (default: 0/disabled).

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


import asyncio
import logging
import os

import aiohttp

# Sprint F265-F265B: Deprecation gate
# Default: DISABLED (0) — curl_cffi is primary transport
# Enable with: HLEDAC_ENABLE_AIOHTTP_FALLBACK=1
_AIOHTTP_FALLBACK_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_AIOHTTP_FALLBACK", "0") == "1"


def is_aiohttp_fallback_enabled() -> bool:
    """Check if legacy aiohttp fallback is enabled (default: False)."""
    return _AIOHTTP_FALLBACK_ENABLED

from runtime_state import _uvloop_installed

# Backward-compat alias for tests that used _uvloop_enabled (misspelling)
_uvloop_enabled = _uvloop_installed

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

# Sprint F266-UVLOOP: canonical uvloop state — single source of truth
# do NOT import uvloop here — that happens in __main__.py before this module is loaded


# -----------------------------------------------------------------------
# F266-UV7: Session Runtime State — replaces 5 module-level mutable globals
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

import contextvars

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp


class _SessionRuntimeState:
    """
    Ephemeral session state — one per async task via ContextVar.

    __slots__ saves RAM on M1 8GB (no __dict__ dict per instance).
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
        self._session_instance: "aiohttp.ClientSession | None" = None
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
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# =============================================================================
# Sprint 8AA: Standard gather result helper — invariant I6-I8
# =============================================================================

def _check_gathered(results: list) -> tuple[list, list]:
    """
    Process gather(return_exceptions=True) results.

    Returns:
        Tuple of (ok_results, error_exceptions)

    Raises:
        asyncio.CancelledError: if any result was cancelled
        BaseException: if any result was a non-Exception BaseException

    Invariants enforced:
    - [I6] re-raises asyncio.CancelledError
    - [I7] re-raises BaseException (not Exception)
    - [I8] routes Exception objects to error_exceptions list (not strings)
    """
    ok_results = []
    error_exceptions = []

    for result in results:
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, BaseException) and not isinstance(result, Exception):
            raise result
        if isinstance(result, Exception):
            error_exceptions.append(result)
        else:
            ok_results.append(result)

    return ok_results, error_exceptions

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
    Called automatically from close_aiohttp_session_async() at windown,
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


async def async_get_aiohttp_session() -> aiohttp.ClientSession:
    """
    Get or create the task-local aiohttp.ClientSession instance (async).

    DEPRECATED: This function is deprecated. Use curl_cffi-based fetching instead.
    Only functional when HLEDAC_ENABLE_AIOHTTP_FALLBACK=1 (default: disabled).

    F266-UV7: Session state is now task-local via ContextVar[_SessionRuntimeState].
    Each async task gets its own isolated session — no cross-task pollution.

    Lazily creates the session on first await.
    Subsequent awaits return the same instance until close is called.
    Thread-safe via per-task asyncio.Lock.

    Returns:
        aiohttp.ClientSession: the shared session instance

    Invariants:
        [I2] lazy — no session created until first await
        [I3] repeated awaits return same instance
    """
    if not _AIOHTTP_FALLBACK_ENABLED:
        logger.debug(
            "[SESSION] aiohttp fallback is DISABLED (HLEDAC_ENABLE_AIOHTTP_FALLBACK=0). "
            "Use curl_cffi-based fetching instead."
        )

    state = _get_state()
    async with state.get_lock():
        if state._session_instance is None or state._session_instance.closed:
            # NOTE: keepalive_timeout and force_close=True are mutually exclusive in aiohttp.
            # force_close=True closes connections immediately after response — no idle keepalive.
            # keepalive_timeout is only meaningful when force_close=False (persistent connections).
            # For M1 8GB memory safety, we use force_close=True (immediate cleanup).
            connector = aiohttp.TCPConnector(
                limit=25,               # total connection pool size
                limit_per_host=get_default_limit(),  # per-host limit (conservative default 8)
                ttl_dns_cache=300,     # DNS cache TTL in seconds
                use_dns_cache=True,    # aiohttp 3.9+ requires explicit opt-in
                force_close=True,      # Close connections on GC (M1 memory safety)
                enable_cleanup_closed=True,  # Clean up closed connections
            )
            # Default timeout: HTML-style (connect + read)
            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=HTML_CONNECT_TIMEOUT_S,
                sock_read=HTML_READ_TIMEOUT_S,
            )
            state._session_instance = aiohttp.ClientSession(
                connector=connector,
                connector_owner=True,
                timeout=timeout,
            )
            state._session_closed = False
            logger.debug("[SESSION] aiohttp.ClientSession created (async lazy)")
        return state._session_instance


# Alias for backward compatibility
get_aiohttp_session = async_get_aiohttp_session
"""Alias for async_get_aiohttp_session(). Provided for backward compatibility."""


def close_aiohttp_session() -> None:
    """
    Close the task-local aiohttp.ClientSession if it exists (sync marker).

    F266-UV7: Session state is task-local — each async task has its own session.
    This call only affects the current task's session state.

    In async contexts, prefer close_aiohttp_session_async().
    This sync version just marks the session for close;
    callers in async code should use close_aiohttp_session_async().

    Invariants:
        [I4] idempotent — multiple calls are safe
        [I5] after close, next await creates new instance
    """
    state = _get_state()
    state._session_closed = True


async def close_aiohttp_session_async() -> None:
    """
    Close the task-local aiohttp.ClientSession (async, proper await).

    F266-UV7: Session state is task-local via ContextVar.
    Each async task has its own isolated session — no cross-task pollution.

    Idempotent: safe to call multiple times.
    After close, next async_get_aiohttp_session() await creates a fresh instance.

    Invariants:
        [I4] idempotent — multiple calls are safe
        [I5] after close, next await creates new instance
    """
    state = _get_state()

    async with state.get_lock():
        if state._session_instance is not None and not state._session_instance.closed:
            sess = state._session_instance
            state._session_instance = None
            state._session_closed = True
        else:
            state._session_closed = True
            return  # No session to close

    # await OUTSIDE lock — close() is fast but we must not hold the lock during await
    try:
        await sess.close()
        logger.debug("[SESSION] aiohttp.ClientSession closed async")
        # Sprint F266-UV5: clear bandits at winddown to prevent unbounded dict growth
        clear_bandits()
    except Exception as e:
        logger.warning(f"[SESSION] async close error: {e}")
        state._last_close_error = str(e)
        state._last_error = str(e)


def get_session_runtime_status() -> dict:
    """
    Return lightweight runtime status of the CURRENT task's session (O(1), side-effect free).

    F266-UV7: Session state is now task-local via ContextVar.
    This function reports the status of the calling task's session state.
    When called from an async context, it reflects that async task's session.
    When called from a sync context without a task override, it reflects the
    default task's session state.

    Returns:
        dict with keys:
            - session_created: bool  — a session instance exists or existed
            - session_closed: bool   — currently closed (truthful, checks .closed)
            - uvloop_enabled: bool   — uvloop was successfully installed
            - last_error: str | None — last error string if any

    Truthfulness contract:
        - session_closed reflects the actual session.closed state when
          an instance exists; falls back to the state._session_closed marker
          only when state._session_instance is None.
    """
    state = _get_state()

    # Authoritative session closed state — prefer the actual session.closed
    # when an instance exists; fall back to marker for sync-close path.
    if state._session_instance is not None:
        session_actually_closed = state._session_instance.closed
    else:
        session_actually_closed = state._session_closed

    return {
        "session_created": state._session_instance is not None or state._session_closed,
        "session_closed": session_actually_closed,
        "uvloop_enabled": _uvloop_installed,  # from runtime_state (canonical)
        "last_error": state._last_error,
        "last_close_error": state._last_close_error,
    }


# =============================================================================
# Test-Only Cleanup Helper — F208G / F266-UV7
# =============================================================================

def _reset_session_runtime_for_tests() -> None:
    """
    Reset the SessionRuntimeState to pristine state for test isolation.

    THIS METHOD IS FOR TEST USE ONLY.
    It exists solely to enable hermetic test isolation.
    It MUST NOT be called from any production code path.

    F266-UV7: Resets the current task's ContextVar state, plus the
    ContextVar default so that new tasks (and any code using the default)
    also get a fresh state.

    Usage:
        # In test fixture:
        from network import session_runtime as sr
        sr._reset_session_runtime_for_tests()

    This resets the current task's session state: _session_instance,
    _session_closed, _session_lock, _last_error, _last_close_error.
    Also resets the ContextVar default for new tasks.

    NOT _uvloop_enabled — that is in runtime_state and persists across tests.

    Idempotent: safe to call multiple times within a test.
    After reset, the next await of async_get_aiohttp_session() creates a fresh
    session with pristine connector state.
    """
    # Close the current session and replace the ContextVar with a FRESH state.
    # Returning a NEW state object (not in-place reset) is critical because
    # the test fixture saves a reference to the OLD state and restores it
    # on teardown.  With an in-place reset, the saved reference would still
    # point to the same (now corrupted) object after teardown.
    state = _get_state()

    # Close any existing session
    if state._session_instance is not None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(state._session_instance.close())
        except Exception:  # noqa: BLE001
            pass
        finally:
            loop.close()

    # Reset fields IN-PLACE so the same state object remains the ContextVar value.
    # This ensures any reference captured by the fixture's save/restore sees the
    # same object — the module-level __getattr__ always returns this object's fields.
    state._session_instance = None
    state._session_lock = None
    state._session_closed = False
    state._last_error = None
    state._last_close_error = None
    state._bandits.clear()
    state._bandit_overrides.clear()
