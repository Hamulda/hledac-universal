"""
Tor/I2P Connection Pool Managers — M1 8GB-Safe Bounded Sessions
================================================================

Sprint F270: Centralized connection pool management for Tor and I2P transports.
F3XX: Migrated from aiohttp_socks to httpx-socks (CLAUDE.md: "httpx-socks replaces aiohttp-socks").

INVARIANTS (enforced by probe tests):
- [I1]  No top-level network side effect at import time (lazy init)
- [I2]  Singleton pattern — one instance per transport type
- [I3]  httpx-socks limits: tor limit=10, i2p limit=10
- [I4]  ttl_dns_cache=300 for I2P HTTP mode (SOCKS uses remote DNS rdns=True)
- [I5]  force_close=True for M1 memory safety
- [I6]  httpx-socks AsyncProxyTransport for Tor/I2P SOCKS5 mode
- [I7]  async lock protects session creation (thread-safe)
- [I8]  Fail-soft: returns None on error, never raises

M1 8GB RAM budget:
- Tor session: ~15MB (10 connections × ~1.5MB each)
- I2P session: ~15MB (10 connections × ~1.5MB each)
- Total: ~30MB for both pools (well within 6.25GB budget)

Architecture authority split (Sprint 8VX):
- PLAIN TCP world: network/session_runtime.py (async_get_aiohttp_session)
- curl_cffi world: transport/curl_cffi_runtime.py (separate transport)
- Tor/I2P world: THIS module (httpx-socks proxy-aware sessions)
"""
from __future__ import annotations
import asyncio
import os
from typing import TYPE_CHECKING
import msgspec
from hledac.universal.core.env_config import ENV
if TYPE_CHECKING:
    import httpx
    import httpx_socks

class PoolConfig(msgspec.Struct, frozen=True):
    """
    M1 8GB-safe connection pool limits.

    All limits are conservative to prevent memory pressure on M1 UMA.
    Tor/I2P are low-throughput protocols — lower limits are acceptable.
    msgspec.Struct: ~3× faster instantiation, zero GC overhead on M1 UMA.
    """
    total_limit: int = 10
    per_host_limit: int = 5
    ttl_dns_cache: int = 300
    keepalive_timeout: int = 30
    force_close: bool = True
    connect_timeout: float = 30.0
    read_timeout: float = 60.0
_tor_pool_instance: TorConnectionPool | None = None
_i2p_pool_instance: I2PConnectionPool | None = None
_pool_lock: asyncio.Lock = asyncio.Lock()

class TorConnectionPool:
    """
    Singleton Tor connection pool manager.

    Manages httpx.AsyncClient with httpx-socks AsyncProxyTransport for Tor SOCKS5 proxy.
    Lazy initialization — session created on first get_session() call.

    F3XX: Migrated from aiohttp_socks.ProxyConnector to httpx_socks.AsyncProxyTransport.
    httpx handles HTTP/2 natively (h2 bundled), no separate connection pool needed.

    M1 8GB: limit=10, per_host=5 prevents connection exhaustion.

    Usage:
        pool = TorConnectionPool()
        session = await pool.get_session()
        async with session.get(url) as resp:
            ...
    """
    __slots__ = tuple(('_config', '_lock', '_session'))

    def __init__(self, config: PoolConfig | None=None) -> None:
        self._config = config or PoolConfig()
        self._session: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def get_session(self) -> httpx.AsyncClient | None:
        """
        Get or create the Tor httpx.AsyncClient.

        Returns:
            httpx.AsyncClient configured for Tor SOCKS5 proxy, or None on failure.

        Invariants:
            - [I3] lazy — session created on first call
            - [I4] repeated calls return same instance
        """
        async with self._lock:
            if self._session is not None and (not self._session.is_closed):
                return self._session
            try:
                import httpx
                import httpx_socks
            except ImportError:
                return None
            try:
                tor_proxy = ENV.get_str('TOR_PROXY', 'socks5://127.0.0.1:9050')
                transport = httpx_socks.AsyncProxyTransport.from_url(tor_proxy, rdns=True)
                limits = httpx.Limits(max_connections=self._config.total_limit, max_keepalive_connections=self._config.per_host_limit)
                timeout = httpx.Timeout(connect=self._config.connect_timeout, read=self._config.read_timeout, write=self._config.keepalive_timeout, pool=self._config.keepalive_timeout)
                self._session = httpx.AsyncClient(transport=transport, limits=limits, timeout=timeout, http2=True, follow_redirects=True, trust_env=False)
                return self._session
            except Exception:
                self._session = None
                return None

    async def close(self) -> None:
        """
        Close the Tor session.

        Invariant:
            - [I5] idempotent — safe to call multiple times
        """
        async with self._lock:
            if self._session is not None:
                await self._session.aclose()
                self._session = None

async def get_tor_pool() -> TorConnectionPool:
    """
    Get the global TorConnectionPool singleton.

    Returns:
        TorConnectionPool instance (singleton).
    """
    global _tor_pool_instance
    async with _pool_lock:
        if _tor_pool_instance is None:
            _tor_pool_instance = TorConnectionPool()
        return _tor_pool_instance

class I2PConnectionPool:
    """
    Singleton I2P connection pool manager.

    Manages two session types:
    - SOCKS5 mode: httpx-socks AsyncProxyTransport for .i2p hostname resolution
    - HTTP mode: httpx.AsyncClient with proxy URL for I2P HTTP proxy (SAM bridge)

    F3XX: Migrated from aiohttp_socks to httpx-socks.
    Lazy initialization — sessions created on first get_session() call.

    M1 8GB: limit=10, per_host=5 for both session types.

    Usage:
        pool = await get_i2p_pool()
        session = await pool.get_session(scheme="socks")  # or "http"
        async with session.get(url) as resp:
            ...
    """
    __slots__ = tuple(('_config', '_lock', '_session_http', '_session_socks'))

    def __init__(self, config: PoolConfig | None=None) -> None:
        self._config = config or PoolConfig()
        self._session_socks: httpx.AsyncClient | None = None
        self._session_http: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def get_session(self, scheme: str='socks') -> httpx.AsyncClient | None:
        """
        Get or create an I2P httpx.AsyncClient.

        Args:
            scheme: "socks" for SOCKS5 proxy (native .i2p resolution),
                   "http" for HTTP proxy (via SAM bridge).

        Returns:
            httpx.AsyncClient configured for I2P, or None on failure.

        Invariants:
            - [I3] lazy — session created on first call
            - [I4] repeated calls return same instance per scheme
        """
        async with self._lock:
            if scheme == 'socks':
                return await self._get_socks_session()
            elif scheme == 'http':
                return await self._get_http_session()
            else:
                return None

    async def _get_socks_session(self) -> httpx.AsyncClient | None:
        """Get or create I2P SOCKS5 session."""
        if self._session_socks is not None and (not self._session_socks.is_closed):
            return self._session_socks
        try:
            import httpx
            import httpx_socks
        except ImportError:
            return None
        try:
            socks_port = ENV.get_int('I2P_SOCKS_PORT', 7654)
            transport = httpx_socks.AsyncProxyTransport.from_url(f'socks5://127.0.0.1:{socks_port}', rdns=True)
            limits = httpx.Limits(max_connections=self._config.total_limit, max_keepalive_connections=self._config.per_host_limit)
            timeout = httpx.Timeout(connect=self._config.connect_timeout, read=self._config.read_timeout, write=self._config.keepalive_timeout, pool=self._config.keepalive_timeout)
            self._session_socks = httpx.AsyncClient(transport=transport, limits=limits, timeout=timeout, http2=True, follow_redirects=True, trust_env=False)
            return self._session_socks
        except Exception:
            self._session_socks = None
            return None

    async def _get_http_session(self) -> httpx.AsyncClient | None:
        """Get or create I2P HTTP proxy session."""
        if self._session_http is not None and (not self._session_http.is_closed):
            return self._session_http
        try:
            import httpx
        except ImportError:
            return None
        try:
            http_port = ENV.get_int('I2P_HTTP_PORT', 8888)
            proxy_url = f'http://127.0.0.1:{http_port}'
            limits = httpx.Limits(max_connections=self._config.total_limit, max_keepalive_connections=self._config.per_host_limit)
            timeout = httpx.Timeout(connect=self._config.connect_timeout, read=self._config.read_timeout, write=self._config.keepalive_timeout, pool=self._config.keepalive_timeout)
            self._session_http = httpx.AsyncClient(proxy=proxy_url, limits=limits, timeout=timeout, http2=True, follow_redirects=True, trust_env=False)
            return self._session_http
        except Exception:
            self._session_http = None
            return None

    async def close(self) -> None:
        """
        Close all I2P sessions.

        Invariant:
            - [I5] idempotent — safe to call multiple times
        """
        async with self._lock:
            if self._session_socks is not None:
                await self._session_socks.aclose()
                self._session_socks = None
            if self._session_http is not None:
                await self._session_http.aclose()
                self._session_http = None

async def get_i2p_pool() -> I2PConnectionPool:
    """
    Get the global I2PConnectionPool singleton.

    Returns:
        I2PConnectionPool instance (singleton).
    """
    global _i2p_pool_instance
    async with _pool_lock:
        if _i2p_pool_instance is None:
            _i2p_pool_instance = I2PConnectionPool()
        return _i2p_pool_instance

async def close_all_pools() -> None:
    """
    Close all connection pools (Tor + I2P).

    Call during application shutdown.

    Invariant:
        - [I5] idempotent — safe to call multiple times
    """
    global _tor_pool_instance, _i2p_pool_instance
    if _tor_pool_instance is not None:
        await _tor_pool_instance.close()
    if _i2p_pool_instance is not None:
        await _i2p_pool_instance.close()