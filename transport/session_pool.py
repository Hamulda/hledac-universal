"""
transport/session_pool.py

ISSUE-007: aiohttp deprecation — migrated to httpx + httpx-socks (2026-07-05)

F4.3: Canonical session pool — singleton per kind.

Provides a single shared httpx.AsyncClient (HTTP/2 capable) and
httpx-socks for SOCKS5 proxy support. All transport lanes
(Tor, I2P, clearnet) share these singletons for connection reuse.

Architecture:
- 1 httpx.AsyncClient (HTTP/2) for clearnet API
- 1 httpx.AsyncClient with SOCKS5 proxy for Tor/I2P
- 1 curl_cffi session pool via curl_cffi_runtime (JA3 impersonation)

M1 8GB bounds:
- httpx clearnet: max_connections=25, max_keepalive_connections=10
- httpx SOCKS5: max_connections=10, max_keepalive_connections=5

Lazy init — no network side effects at import time.
Thread-safe via asyncio.Lock.

Usage:
    from transport.session_pool import session_pool, HTTPX, CURL_CFFI

    # httpx (HTTP/2 clearnet)
    client = await session_pool.httpx()
    resp = await client.get(url)

    # httpx with SOCKS5 proxy (Tor/I2P)
    client = await session_pool.httpx_socks(proxy_url="socks5://127.0.0.1:9050")
    resp = await client.get(url)

    # curl_cffi (JA3 stealth)
    from transport.curl_cffi_fetch import fetch_via_curl_cffi_cached
"""
from __future__ import annotations


import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


class PoolKind(Enum):
    """Session pool kind for telemetry."""

    HTTPX = "httpx"
    HTTPX_SOCKS = "httpx_socks"
    CURL_CFFI = "curl_cffi"


# =============================================================================
# Constants — M1 8GB bounded
# =============================================================================

_HTTPX_MAX_CONNECTIONS = 25
_HTTPX_MAX_KEEPALIVE = 10
_HTTPX_KEEPALIVE_EXPIRY = 30.0

_HTTPX_SOCKS_MAX_CONNECTIONS = 10
_HTTPX_SOCKS_MAX_KEEPALIVE = 5

_DEFAULT_TIMEOUT_S = 15.0
_CONNECT_TIMEOUT_S = 5.0


# =============================================================================
# Pool State
# =============================================================================

_httpx_client: httpx.AsyncClient | None = None
_httpx_lock = asyncio.Lock()
_httpx_closed = False

# ISSUE-007: SOCKS5 httpx clients — one per proxy URL (bounded cache)
# ISSUE-080: cache_key changed from str to tuple(proxy_url, rdns)
_httpx_socks_clients: dict[tuple[str, bool], httpx.AsyncClient] = {}
_httpx_socks_lock = asyncio.Lock()


# =============================================================================
# httpx Singleton
# =============================================================================


async def httpx_client() -> httpx.AsyncClient:
    """
    Get or create the shared httpx.AsyncClient (HTTP/2 capable).

    Singleton — same instance returned on every call until close.
    HTTP/2 enabled when h2 is installed; falls back to 1.1 otherwise.

    M1 8GB bounds:
        max_connections=25, max_keepalive_connections=10
        ~25MB RAM for connection states (well within 6.25GB budget)

    Returns:
        httpx.AsyncClient: HTTP/2 capable async client

    Raises:
        RuntimeError: if httpx is not installed
    """
    global _httpx_client, _httpx_closed

    # Lazy capability check
    try:
        import httpx
        import h2  # noqa: F401
    except ImportError as e:
        raise RuntimeError(f"httpx with HTTP/2 not available: {e}") from e

    async with _httpx_lock:
        if _httpx_client is None or _httpx_closed:
            limits = httpx.Limits(
                max_connections=_HTTPX_MAX_CONNECTIONS,
                max_keepalive_connections=_HTTPX_MAX_KEEPALIVE,
                keepalive_expiry=_HTTPX_KEEPALIVE_EXPIRY,
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
            _httpx_closed = False
            logger.debug("[SessionPool] httpx.AsyncClient created (HTTP/2, singleton)")
        return _httpx_client


async def close_httpx() -> None:
    """
    Close httpx client if open (idempotent).

    After close, next httpx_client() creates a fresh instance.
    """
    global _httpx_client, _httpx_closed

    client = None
    async with _httpx_lock:
        if _httpx_client is not None and not _httpx_closed:
            client = _httpx_client
            _httpx_client = None
            _httpx_closed = True

    if client is not None:
        try:
            await client.aclose()
            logger.debug("[SessionPool] httpx.AsyncClient closed")
        except Exception as e:
            logger.warning(f"[SessionPool] httpx close error: {e}")


# =============================================================================
# httpx-socks SOCKS5 Singleton Pool (ISSUE-007)
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

    Each unique (proxy_url, rdns) tuple gets its own client
    (bounded by _HTTPX_SOCKS_MAX_PROXIES).

    M1 8GB bounds:
        max_connections=10, max_keepalive_connections=5
        ~10MB RAM per SOCKS5 client

    Args:
        proxy_url: SOCKS5 proxy URL (e.g., "socks5://127.0.0.1:9050")
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

            limits = httpx.Limits(
                max_connections=_HTTPX_SOCKS_MAX_CONNECTIONS,
                max_keepalive_connections=_HTTPX_SOCKS_MAX_KEEPALIVE,
                keepalive_expiry=_HTTPX_KEEPALIVE_EXPIRY,
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
                http2=True,
                timeout=timeout,
                follow_redirects=True,
                transport=transport,
                trust_env=False,
            )
            logger.debug(
                f"[SessionPool] httpx-socks client created for {proxy_url} "
                f"(rdns={rdns})"
            )
        return _httpx_socks_clients[cache_key]


async def close_httpx_socks() -> None:
    """Close all httpx-socks clients (idempotent)."""
    global _httpx_socks_clients

    clients = []
    async with _httpx_socks_lock:
        # ISSUE-080: cache_key is now (proxy_url, rdns) tuple
        for cache_key, client in _httpx_socks_clients.items():
            _httpx_socks_clients[cache_key] = None  # type: ignore
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

        # httpx-socks (SOCKS5 for Tor/I2P)
        client = await pool.httpx_socks("socks5://127.0.0.1:9050")
        resp = await client.get("http://example.onion")

        # curl_cffi (JA3 stealth)
        ok, session, profile = await pool.curl_cffi("chrome136")
    """

    __slots__ = ()

    async def httpx(self) -> httpx.AsyncClient:
        """Get httpx.AsyncClient singleton (HTTP/2 clearnet)."""
        return await httpx_client()

    async def httpx_socks(self, proxy_url: str) -> httpx.AsyncClient:
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

        Returns:
            dict with pool state and bounds
        """
        return {
            "httpx": {
                "available": True,
                "initialized": _httpx_client is not None and not _httpx_closed,
                "max_connections": _HTTPX_MAX_CONNECTIONS,
                "max_keepalive": _HTTPX_MAX_KEEPALIVE,
            },
            "httpx_socks": {
                "available": True,
                "active_proxies": len(_httpx_socks_clients),
                "max_connections": _HTTPX_SOCKS_MAX_CONNECTIONS,
                "max_keepalive": _HTTPX_SOCKS_MAX_KEEPALIVE,
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


# For files already importing from transport.curl_cffi_runtime
async def get_curl_cffi_session(profile: str = "chrome110") -> tuple[bool, Any, str]:
    """Backward-compat: delegate to session_pool.curl_cffi()."""
    return await session_pool.curl_cffi(profile)


__all__ = [
    "session_pool",
    "SessionPool",
    "PoolKind",
    # Backward compat
    "httpx_client",
    "aiohttp_session",
    "curl_cffi_session",
    "close_httpx",
    "close_aiohttp",
    "close_curl_cffi",
    "get_httpx_client",
    "get_curl_cffi_session",
]
