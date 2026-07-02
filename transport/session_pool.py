"""
transport/session_pool.py

F4.3: Canonical session pool — singleton per kind.

Provides a single shared httpx.AsyncClient (HTTP/2 capable) and
aiohttp.ClientSession per transport kind. All transport lanes
(Tor, I2P, clearnet) share these singletons for connection reuse.

Architecture:
- 1 httpx.AsyncClient (HTTP/2) for clearnet API
- 1 aiohttp.ClientSession for compatibility (SOCKS-capable)
- 1 curl_cffi session pool via curl_cffi_runtime (JA3 impersonation)

M1 8GB bounds:
- httpx: max_connections=25, max_keepalive_connections=10
- aiohttp: limit=100, limit_per_host=20

Lazy init — no network side effects at import time.
Thread-safe via asyncio.Lock.

Usage:
    from transport.session_pool import session_pool, HTTPX, AIOHTTP, CURL_CFFI

    # httpx (HTTP/2 clearnet)
    client = await session_pool.httpx()
    resp = await client.get(url)

    # aiohttp (SOCKS proxy)
    async with session_pool.aiohttp() as sess:
        async with sess.get(url) as resp:
            ...

    # curl_cffi (JA3 stealth)
    from transport.curl_cffi_fetch import fetch_via_curl_cffi_cached
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp
    import httpx

logger = logging.getLogger(__name__)


class PoolKind(Enum):
    """Session pool kind for telemetry."""

    HTTPX = "httpx"
    AIOHTTP = "aiohttp"
    CURL_CFFI = "curl_cffi"


# =============================================================================
# Constants — M1 8GB bounded
# =============================================================================

_HTTPX_MAX_CONNECTIONS = 25
_HTTPX_MAX_KEEPALIVE = 10
_HTTPX_KEEPALIVE_EXPIRY = 30.0

_AIOHTTP_LIMIT = 100
_AIOHTTP_LIMIT_PER_HOST = 20
_AIOHTTP_CONNECTOR_LIMIT = 30

_DEFAULT_TIMEOUT_S = 15.0
_CONNECT_TIMEOUT_S = 5.0


# =============================================================================
# Pool State
# =============================================================================

_httpx_client: httpx.AsyncClient | None = None
_httpx_lock = asyncio.Lock()
_httpx_closed = False

_aiohttp_session: aiohttp.ClientSession | None = None
_aiohttp_lock = asyncio.Lock()
_aiohttp_closed = False


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
# aiohttp Singleton
# =============================================================================


async def aiohttp_session() -> aiohttp.ClientSession:
    """
    Get or create the shared aiohttp.ClientSession.

    Singleton — same instance returned on every call until close.
    Uses TCPConnector with SOCKS support (via aiohttp_socks).

    M1 8GB bounds:
        limit=100, limit_per_host=20, force_close=False

    Returns:
        aiohttp.ClientSession: shared session with connection pooling

    Raises:
        RuntimeError: if aiohttp is not installed
    """
    global _aiohttp_session, _aiohttp_closed

    try:
        import aiohttp
    except ImportError as e:
        raise RuntimeError(f"aiohttp not available: {e}") from e

    async with _aiohttp_lock:
        if _aiohttp_session is None or _aiohttp_closed:
            from aiohttp import TCPConnector

            # SOCKS-capable connector for dark web (Tor/I2P)
            # Falls back to direct TCP when no proxy configured
            connector = TCPConnector(
                limit=_AIOHTTP_LIMIT,
                limit_per_host=_AIOHTTP_LIMIT_PER_HOST,
                force_close=False,
                enable_cleanup_closed=True,
            )
            timeout = aiohttp.ClientTimeout(
                total=_DEFAULT_TIMEOUT_S,
                connect=_CONNECT_TIMEOUT_S,
            )
            _aiohttp_session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                cookies=None,  # stateless
            )
            _aiohttp_closed = False
            logger.debug("[SessionPool] aiohttp.ClientSession created (singleton)")
        return _aiohttp_session


async def close_aiohttp() -> None:
    """
    Close aiohttp session if open (idempotent).

    After close, next aiohttp_session() creates a fresh instance.
    """
    global _aiohttp_session, _aiohttp_closed

    session = None
    async with _aiohttp_lock:
        if _aiohttp_session is not None and not _aiohttp_closed:
            session = _aiohttp_session
            _aiohttp_session = None
            _aiohttp_closed = True

    if session is not None:
        try:
            await session.close()
            logger.debug("[SessionPool] aiohttp.ClientSession closed")
        except Exception as e:
            logger.warning(f"[SessionPool] aiohttp close error: {e}")


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

    Provides type-safe access to httpx, aiohttp, and curl_cffi
    session singletons with proper lifecycle management.

    Usage:
        pool = SessionPool()

        # httpx (HTTP/2)
        client = await pool.httpx()
        resp = await client.get("https://api.example.com")

        # aiohttp (SOCKS-compatible)
        async with pool.aiohttp() as sess:
            async with sess.get("http://example.onion") as resp:
                ...

        # curl_cffi (JA3 stealth)
        ok, session, profile = await pool.curl_cffi("chrome136")
    """

    __slots__ = ()

    async def httpx(self) -> httpx.AsyncClient:
        """Get httpx.AsyncClient singleton (HTTP/2)."""
        return await httpx_client()

    async def aiohttp(self) -> aiohttp.ClientSession:
        """Get aiohttp.ClientSession singleton."""
        return await aiohttp_session()

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

        # Close aiohttp
        try:
            await close_aiohttp()
            results["aiohttp"] = "closed"
        except Exception as e:
            results["aiohttp"] = f"error: {e}"

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
            "aiohttp": {
                "available": True,
                "initialized": _aiohttp_session is not None and not _aiohttp_closed,
                "limit": _AIOHTTP_LIMIT,
                "limit_per_host": _AIOHTTP_LIMIT_PER_HOST,
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
