"""
transport/session_pool.py

ISSUE-007 / ISSUE-010: Canonical session pool — unified HTTP session entry point.

F4.3 / F350M-R: Canonical session pool — singleton per kind.
F4XX / ISSUE-013: Adaptive connection limits based on UMA memory pressure.

This module is the SINGLE CANONICAL entry point for ALL HTTP session types:
  - httpx.AsyncClient (HTTP/2) for clearnet API/text fetching
  - httpx-socks (SOCKS5) for Tor/I2P
  - curl_cffi for JA3 stealth fingerprinting

M1 8GB bounds:
- httpx clearnet: max_connections=25, max_keepalive_connections=10 (normal)
- httpx SOCKS5: max_connections=10, max_keepalive_connections=5 (normal)
- Adaptive: reduced at elevated/critical memory pressure

Lazy init — no network side effects at import time.
Thread-safe via asyncio.Lock.

DEPRECATION PATH (ISSUE-010):
- network/session_runtime.py: DEPRECATED — import from here instead
  (its httpx singleton will delegate to session_pool.httpx())
- transport/connection_pool_manager.py: DEPRECATED — import from here instead

Usage:
    from hledac.universal.transport.session_pool import session_pool

    # httpx (HTTP/2 clearnet) — CANONICAL
    client = await session_pool.httpx()
    resp = await client.get(url)

    # httpx with SOCKS5H proxy (Tor/I2P) — OPSEC-001: remote DNS resolution
    client = await session_pool.httpx_socks(proxy_url="socks5h://127.0.0.1:9050")
    resp = await client.get(url)

    # curl_cffi (JA3 stealth)
    from hledac.universal.transport.curl_cffi_fetch import fetch_via_curl_cffi_cached
"""


import asyncio
import logging
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

import msgspec

from hledac.universal.utils.locks import LazyAsyncioLock

if TYPE_CHECKING:
    import httpx
    from httpx import AsyncClient

logger = logging.getLogger(__name__)


class PoolKind(Enum):
    """Session pool kind for telemetry."""

    HTTPX = "httpx"
    HTTPX_SOCKS = "httpx_socks"
    CURL_CFFI = "curl_cffi"


# =============================================================================
# ConnectionPreset — Adaptive limits based on UMA memory pressure (ISSUE-013)
# =============================================================================

class ConnectionPreset(msgspec.Struct, frozen=True, gc=False):
    """
    ISSUE-013: Immutable connection preset derived from UMA memory pressure state.

    Parallel to ConcurrencyPreset in core/resource_governor.py.
    Single source of truth for HTTP connection limits derived from
    M1 8GB UMA state.

    M1 8GB calibrated values:
        critical:   max_conn=10, max_keep=4  — active memory pressure, OOM risk
        elevated:   max_conn=15, max_keep=6  — approaching limit
        normal:     max_conn=25, max_keep=10  — normal operation
    """

    max_connections: int
    max_keepalive: int
    keepalive_expiry: float

    @classmethod
    def from_uma_state(cls, state: str) -> ConnectionPreset:
        """Derive preset from UMA state string."""
        match state:
            case "critical" | "emergency":
                return cls(max_connections=10, max_keepalive=4, keepalive_expiry=15.0)
            case "warn":
                return cls(max_connections=15, max_keepalive=6, keepalive_expiry=20.0)
            case "soft_warn":
                return cls(max_connections=20, max_keepalive=8, keepalive_expiry=25.0)
            case _:
                return cls(max_connections=25, max_keepalive=10, keepalive_expiry=30.0)


# =============================================================================
# UMA-aware adaptive limits (ISSUE-013)
# =============================================================================

# LRU cache for UMA state — 1s TTL to avoid sampling overhead
_uma_state_cache: tuple[str, float] | None = None  # (state, timestamp)


def _get_cached_uma_state() -> str:
    """
    Get cached UMA state with 1s TTL.
    Falls back to 'ok' if sampling fails.
    """
    global _uma_state_cache
    now = time.monotonic()

    if _uma_state_cache is not None:
        state, cached_at = _uma_state_cache
        if now - cached_at < 1.0:
            return state

    # Sample UMA status
    try:
        from hledac.universal.core.resource_governor import sample_uma_status

        uma = sample_uma_status()
        state = uma.state
    except Exception:
        state = "ok"

    _uma_state_cache = (state, now)
    return state


def _get_connection_preset() -> ConnectionPreset:
    """Get connection preset based on current UMA memory pressure."""
    state = _get_cached_uma_state()
    return ConnectionPreset.from_uma_state(state)


# =============================================================================
# Pool State
# =============================================================================

_httpx_client: httpx.AsyncClient | None = None
_httpx_lock = LazyAsyncioLock()

# ISSUE-007: SOCKS5 httpx clients — one per proxy URL (bounded cache)
# ISSUE-080: cache_key changed from str to tuple(proxy_url, rdns)
_httpx_socks_clients: dict[tuple[str, bool], httpx.AsyncClient] = {}
_httpx_socks_lock = LazyAsyncioLock()

# ISSUE-013: Active connection tracking for metrics
_active_connections: int = 0
_pool_metrics_lock = asyncio.Lock()


def _record_pool_metrics() -> None:
    """Record pool metrics to metrics registry (fail-soft)."""
    try:
        from hledac.universal.core.metrics_registry import get_metrics_registry

        registry = get_metrics_registry()
        preset = _get_connection_preset()
        registry.record_gauge("session_pool_active_connections", float(_active_connections))
        registry.record_gauge("session_pool_httpx_max_connections", float(preset.max_connections))
        registry.record_gauge("session_pool_httpx_max_keepalive", float(preset.max_keepalive))
        registry.record_gauge("session_pool_uma_pressure", float(hash(preset) % 100))  # 0-99
    except Exception:
        pass  # Fail-soft: metrics are diagnostic only


# =============================================================================
# httpx Singleton (ISSUE-013: Adaptive limits)
# =============================================================================

_CONNECT_TIMEOUT_S = 5.0


async def httpx_client() -> httpx.AsyncClient:
    """
    Get or create the shared httpx.AsyncClient (HTTP/2 capable).

    Singleton — same instance returned on every call until close.
    HTTP/2 enabled when h2 is installed; falls back to 1.1 otherwise.

    ISSUE-013: Connection limits are adaptive based on UMA memory pressure.
    At critical/emergency pressure, max_connections is reduced from 25 to 10
    to prevent OOM on M1 8GB.

    Returns:
        httpx.AsyncClient: HTTP/2 capable async client

    Raises:
        RuntimeError: if httpx is not installed
    """
    global _httpx_client

    # Lazy capability check
    try:
        import httpx
        import h2  # noqa: F401
    except ImportError as e:
        raise RuntimeError(f"httpx with HTTP/2 not available: {e}") from e

    async with _httpx_lock:
        if _httpx_client is None or _httpx_client.is_closed:
            # ISSUE-013: Adaptive limits from UMA state
            preset = _get_connection_preset()
            limits = httpx.Limits(
                max_connections=preset.max_connections,
                max_keepalive_connections=preset.max_keepalive,
                keepalive_expiry=preset.keepalive_expiry,
            )
            timeout = httpx.Timeout(
                connect=_CONNECT_TIMEOUT_S,
                read=20.0,
                write=10.0,
                pool=10.0,
            )
            _httpx_client = httpx.AsyncClient(
                limits=limits,
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                cookies=None,
                trust_env=False,
            )
            logger.debug(
                f"[SessionPool] httpx.AsyncClient created (HTTP/2, "
                f"max_conn={preset.max_connections}, max_keep={preset.max_keepalive})"
            )
            _record_pool_metrics()
        return _httpx_client


async def close_httpx() -> None:
    """
    Close httpx client if open (idempotent).

    After close, next httpx_client() creates a fresh instance.
    """
    global _httpx_client, _active_connections

    client = None
    async with _httpx_lock:
        if _httpx_client is not None and not _httpx_client.is_closed:
            client = _httpx_client
            _httpx_client = None

    if client is not None:
        try:
            await client.aclose()
            logger.debug("[SessionPool] httpx.AsyncClient closed")
        except Exception as e:
            logger.warning(f"[SessionPool] httpx close error: {e}")

    # ISSUE-013: Update metrics on close
    async with _pool_metrics_lock:
        _active_connections = max(0, _active_connections - 1)
        _record_pool_metrics()


# =============================================================================
# httpx-socks SOCKS5 Singleton Pool (ISSUE-007 / ISSUE-013: Adaptive limits)
# =============================================================================


async def httpx_socks_client(
    proxy_url: str,
    *,
    rdns: bool = True,
) -> httpx.AsyncClient:
    """
    Get or create a shared httpx.AsyncClient with SOCKS5 proxy.

    ISSUE-007: Replaces aiohttp_socks.ProxyConnector with httpx-socks.
    ISSUE-080: Adds rdns=True for remote DNS resolution (Tor anonymity).
    ISSUE-013: Connection limits are adaptive based on UMA memory pressure.

    Each unique (proxy_url, rdns) tuple gets its own client
    (bounded by 8 SOCKS proxies max on M1 8GB).

    Args:
        proxy_url: SOCKS5 proxy URL (e.g., "socks5h://127.0.0.1:9050")
            OPSEC-001: Use "socks5h://" prefix for SOCKS5H (hostname-only, remote DNS by proxy).
            Use "socks5h://" prefix for SOCKS5H (hostname-only, no DNS leak).
        rdns: Remote DNS resolution (default True for Tor anonymity).
            When True, DNS resolution happens on the proxy side.

    Returns:
        httpx.AsyncClient: SOCKS5-capable async client
    """
    global _httpx_socks_clients

    # ISSUE-080: rdns affects cache key — same proxy with different rdns = different client
    cache_key = (proxy_url, rdns)

    # Lazy import httpx_socks
    try:
        import httpx
        import httpx_socks  # noqa: F401
    except ImportError as e:
        raise RuntimeError(f"httpx-socks not available: {e}") from e

    async with _httpx_socks_lock:
        if cache_key not in _httpx_socks_clients:
            if len(_httpx_socks_clients) >= 8:
                # Evict oldest client when at capacity (M1 8GB safety)
                oldest = next(iter(_httpx_socks_clients))
                old_client = _httpx_socks_clients.pop(oldest)
                try:
                    await old_client.aclose()
                except Exception:
                    pass

            # ISSUE-013: Adaptive limits from UMA state (50% of httpx preset)
            preset = _get_connection_preset()
            socks_max_conn = max(5, preset.max_connections // 2)
            socks_max_keep = max(2, preset.max_keepalive // 2)

            limits = httpx.Limits(
                max_connections=socks_max_conn,
                max_keepalive_connections=socks_max_keep,
                keepalive_expiry=preset.keepalive_expiry,
            )
            timeout = httpx.Timeout(
                connect=_CONNECT_TIMEOUT_S,
                read=20.0,
                write=10.0,
                pool=10.0,
            )
            # ISSUE-080: Pass rdns to httpx-socks for remote DNS resolution
            transport = httpx_socks.AsyncProxyTransport.from_url(
                proxy_url,
                rdns=rdns,
            )
            _httpx_socks_clients[cache_key] = httpx.AsyncClient(
                limits=limits,
                http2=False,  # SOCKS5 tunnel doesn't support HTTP/2 ALPN negotiation
                timeout=timeout,
                follow_redirects=True,
                transport=transport,
                trust_env=False,
            )
            logger.debug(
                f"[SessionPool] httpx-socks client created for {proxy_url} "
                f"(rdns={rdns}, max_conn={socks_max_conn})"
            )
            _record_pool_metrics()
        return _httpx_socks_clients[cache_key]


async def close_httpx_socks() -> None:
    """Close all httpx-socks clients (idempotent)."""
    global _httpx_socks_clients

    clients = []
    async with _httpx_socks_lock:
        for client in _httpx_socks_clients.values():
            clients.append(client)
        _httpx_socks_clients.clear()

    for client in clients:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
    logger.debug("[SessionPool] all httpx-socks clients closed")


# =============================================================================
# curl_cffi Delegation
# =============================================================================


async def curl_cffi_session(profile: str = "chrome110") -> tuple[bool, Any, str]:
    """
    Get curl_cffi session via existing curl_cffi_runtime pool.

    Delegates to curl_cffi_runtime.async_get_curl_cffi_session().
    Profile and host caching handled by curl_cffi_runtime.

    Returns:
        (success, session_or_None, profile_or_error_reason)
    """
    from .curl_cffi_runtime import async_get_curl_cffi_session

    return await async_get_curl_cffi_session(profile)


async def close_curl_cffi() -> None:
    """
    Close all curl_cffi sessions via existing runtime.
    """
    from .curl_cffi_runtime import close_curl_cffi_sessions_async

    await close_curl_cffi_sessions_async()


# =============================================================================
# Unified Pool API
# =============================================================================


class SessionPool:
    """
    Unified session pool — singleton facade for all HTTP clients.

    ISSUE-007: aiohttp removed — httpx + httpx-socks for all HTTP needs.

    Provides type-safe access to httpx, httpx-socks (SOCKS5), and curl_cffi
    session singletons with proper lifecycle management.

    Usage:
        pool = SessionPool()

        # httpx (HTTP/2 clearnet)
        client = await pool.httpx()
        resp = await client.get("https://api.example.com")

        # httpx-socks (SOCKS5H for Tor/I2P) — OPSEC-001: remote DNS resolution
        client = await pool.httpx_socks("socks5h://127.0.0.1:9050")
        resp = await client.get("http://example.onion")

        # curl_cffi (JA3 stealth)
        ok, session, profile = await pool.curl_cffi("chrome136")
    """

    __slots__ = ()

    async def httpx(self) -> AsyncClient:
        """Get httpx.AsyncClient singleton (HTTP/2 clearnet)."""
        return await httpx_client()

    async def httpx_socks(self, proxy_url: str) -> AsyncClient:
        """Get httpx.AsyncClient with SOCKS5 proxy."""
        return await httpx_socks_client(proxy_url)

    async def curl_cffi(self, profile: str = "chrome110") -> tuple[bool, Any, str]:
        """Get curl_cffi session (profile-cached)."""
        return await curl_cffi_session(profile)

    async def close_all(self) -> dict[str, str]:
        """
        Close all pooled sessions (idempotent).

        Returns:
            dict of pool kind -> close status
        """
        results: dict[str, str] = {}

        # Close httpx
        try:
            await close_httpx()
            results["httpx"] = "closed"
        except Exception as e:
            results["httpx"] = f"error: {e}"

        # Close httpx-socks
        try:
            await close_httpx_socks()
            results["httpx_socks"] = "closed"
        except Exception as e:
            results["httpx_socks"] = f"error: {e}"

        # Close curl_cffi
        try:
            await close_curl_cffi()
            results["curl_cffi"] = "closed"
        except Exception as e:
            results["curl_cffi"] = f"error: {e}"

        return results

    def get_status(self) -> dict[str, Any]:
        """
        Get pool status for telemetry.

        ISSUE-013: Returns adaptive limits based on current UMA state.

        Returns:
            dict with pool state and bounds
        """
        preset = _get_connection_preset()
        uma_state = _get_cached_uma_state()
        socks_max_conn = max(5, preset.max_connections // 2)
        socks_max_keep = max(2, preset.max_keepalive // 2)

        return {
            "httpx": {
                "available": True,
                "initialized": _httpx_client is not None and not _httpx_client.is_closed,
                "max_connections": preset.max_connections,
                "max_keepalive": preset.max_keepalive,
                "uma_state": uma_state,
                "active_connections": _active_connections,
            },
            "httpx_socks": {
                "available": True,
                "active_proxies": len(_httpx_socks_clients),
                "max_connections": socks_max_conn,
                "max_keepalive": socks_max_keep,
            },
            "curl_cffi": {
                "delegated_to": "curl_cffi_runtime",
            },
        }


# =============================================================================
# Module-level Singleton
# =============================================================================

session_pool = SessionPool()


# =============================================================================
# Backward-compatibility Aliases
# =============================================================================

# For files already importing from transport.httpx_client
async def get_httpx_client() -> httpx.AsyncClient:
    """Backward-compat: delegate to session_pool.httpx()."""
    return await session_pool.httpx()


# F4XX: aiohttp removed — backward-compat aliases moved to network.session_runtime


# For files already importing from transport.curl_cffi_runtime
async def get_curl_cffi_session(profile: str = "chrome110") -> tuple[bool, Any, str]:
    """Backward-compat: delegate to session_pool.curl_cffi()."""
    return await session_pool.curl_cffi(profile)


# Backward-compat: Tor/I2P pool factory functions
# OPSEC-001: socks5h:// forces remote DNS resolution by proxy.
async def get_tor_pool() -> httpx.AsyncClient:
    """Backward-compat: get httpx client via SOCKS5H proxy for Tor. Use httpx_socks_client() directly."""
    return await httpx_socks_client("socks5h://127.0.0.1:9050")


async def get_i2p_pool() -> httpx.AsyncClient:
    """Backward-compat: get httpx client via SOCKS5H proxy for I2P. Use httpx_socks_client() directly."""
    return await httpx_socks_client("socks5h://127.0.0.1:4444")


__all__ = [
    "session_pool",
    "SessionPool",
    "PoolKind",
    # Backward compat
    "httpx_client",
    "httpx_socks_client",
    "curl_cffi_session",
    "close_httpx",
    "close_httpx_socks",
    "close_curl_cffi",
    "get_httpx_client",
    "get_curl_cffi_session",
    # Backward-compat Tor/I2P
    "get_tor_pool",
    "get_i2p_pool",
]
