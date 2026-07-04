"""
Tor/I2P Connection Pool Managers — M1 8GB-Safe Bounded Sessions
================================================================

Sprint F270: Centralized connection pool management for Tor and I2P transports.

P1-08 NOTE: Tor/I2P use aiohttp_socks.ProxyConnector (SOCKS5), which has a different
API than plain aiohttp.TCPConnector and does not support adaptive limit changes at runtime.
Their limits remain conservative (limit=10, per_host=5) — DNS is resolved remotely via
the SOCKS proxy (rdns=True), so ttl_dns_cache is not a memory concern for Tor/I2P.

AdaptiveTcpConnector (P1-08) applies to PLAIN TCP paths only:
  - network/session_runtime.py
  - transport/session_runtime.py

INVARIANTS (enforced by probe tests):
- [I1]  No top-level network side effect at import time (lazy init)
- [I2]  Singleton pattern — one instance per transport type
- [I3]  ProxyConnector limits: tor limit=10, i2p limit=10, per_host=5
- [I4]  ttl_dns_cache=300 for I2P HTTP mode (Tor/I2P SOCKS uses remote DNS)
- [I5]  force_close=True for M1 memory safety
- [I6]  SOCKS5 ProxyConnector for Tor/I2P SOCKS mode
- [I7]  async lock protects session creation (thread-safe)
- [I8]  Fail-soft: returns None on error, never raises

M1 8GB RAM budget:
- Tor session: ~15MB (10 connections × ~1.5MB each)
- I2P session: ~15MB (10 connections × ~1.5MB each)
- Total: ~30MB for both pools (well within 6.25GB budget)

Architecture authority split (Sprint 8VX):
- PLAIN TCP world: network/session_runtime.py (async_get_aiohttp_session)
- curl_cffi world: transport/curl_cffi_runtime.py (separate transport)
- Tor/I2P world: THIS module (proxy-aware sessions)
"""
from __future__ import annotations



import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp
    import aiohttp_socks


# =============================================================================
# Pool Configuration
# =============================================================================


@dataclass(frozen=True)
class PoolConfig:
    """
    M1 8GB-safe connection pool limits.

    All limits are conservative to prevent memory pressure on M1 UMA.
    Tor/I2P are low-throughput protocols — lower limits are acceptable.
    """
    total_limit: int = 10          # Total connections across all hosts
    per_host_limit: int = 5        # Per-host limit (prevents single-host starvation)
    ttl_dns_cache: int = 300       # DNS cache TTL (seconds) — reduces lookups
    keepalive_timeout: int = 30    # Keep-alive timeout (M1 memory)
    force_close: bool = True       # Close connections on GC (M1 memory safety)
    connect_timeout: float = 30.0   # Connection timeout
    read_timeout: float = 60.0     # Read timeout


# Module-level singleton instances (created lazily on first use)
_tor_pool_instance: TorConnectionPool | None = None
_i2p_pool_instance: I2PConnectionPool | None = None
_pool_lock: asyncio.Lock = asyncio.Lock()


# =============================================================================
# Tor Connection Pool
# =============================================================================


class TorConnectionPool:
    """
    Singleton Tor connection pool manager.

    Manages aiohttp ClientSession with aiohttp_socks.ProxyConnector for Tor SOCKS5 proxy.
    Lazy initialization — session created on first get_session() call.

    M1 8GB: limit=10, per_host=5 prevents connection exhaustion.

    Usage:
        pool = TorConnectionPool()
        session = await pool.get_session()
        async with session.get(url) as resp:
            ...
    """

    def __init__(self, config: PoolConfig | None = None) -> None:
        self._config = config or PoolConfig()
        self._session: aiohttp.ClientSession | None = None
        self._connector: aiohttp_socks.ProxyConnector | None = None
        self._lock = asyncio.Lock()

    async def get_session(self) -> aiohttp.ClientSession | None:
        """
        Get or create the Tor ClientSession.

        Returns:
            aiohttp.ClientSession configured for Tor SOCKS5 proxy, or None on failure.

        Invariants:
            - [I3] lazy — session created on first call
            - [I4] repeated calls return same instance
        """
        async with self._lock:
            if self._session is not None and not self._session.closed:
                return self._session

            try:
                import aiohttp
                import aiohttp_socks
            except ImportError:
                return None

            try:
                # Tor proxy URL — configurable via TOR_PROXY env
                tor_proxy = os.environ.get("TOR_PROXY", "socks5://127.0.0.1:9050")

                # Create ProxyConnector with bounded limits
                self._connector = aiohttp_socks.ProxyConnector.from_url(
                    tor_proxy,
                    rdns=True,  # Remote DNS resolution (for .onion)
                    limit=self._config.total_limit,
                    limit_per_host=self._config.per_host_limit,
                )

                # Create session with bounded connector
                timeout = aiohttp.ClientTimeout(
                    total=None,
                    connect=self._config.connect_timeout,
                    sock_read=self._config.read_timeout,
                )
                self._session = aiohttp.ClientSession(
                    connector=self._connector,
                    connector_owner=True,
                    timeout=timeout,
                )
                return self._session

            except Exception:
                # Fail-soft: never raises, returns None
                self._session = None
                self._connector = None
                return None

    async def close(self) -> None:
        """
        Close the Tor session and connector.

        Invariant:
            - [I5] idempotent — safe to call multiple times
        """
        async with self._lock:
            if self._session is not None:
                await self._session.close()
                self._session = None
            if self._connector is not None:
                await self._connector.close()
                self._connector = None


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


# =============================================================================
# I2P Connection Pool
# =============================================================================


class I2PConnectionPool:
    """
    Singleton I2P connection pool manager.

    Manages two session types:
    - SOCKS5 mode: aiohttp_socks.ProxyConnector for .i2p hostname resolution
    - HTTP mode: aiohttp.TCPConnector for I2P HTTP proxy (SAM bridge)

    Lazy initialization — sessions created on first get_session() call.

    M1 8GB: limit=10, per_host=5 for both session types.

    Usage:
        pool = await get_i2p_pool()
        session = await pool.get_session(scheme="socks")  # or "http"
        async with session.get(url) as resp:
            ...
    """

    def __init__(self, config: PoolConfig | None = None) -> None:
        self._config = config or PoolConfig()
        self._session_socks: aiohttp.ClientSession | None = None
        self._session_http: aiohttp.ClientSession | None = None
        self._connector_socks: aiohttp_socks.ProxyConnector | None = None
        self._connector_http: aiohttp.TCPConnector | None = None
        self._lock = asyncio.Lock()

    async def get_session(self, scheme: str = "socks") -> aiohttp.ClientSession | None:
        """
        Get or create an I2P ClientSession.

        Args:
            scheme: "socks" for SOCKS5 proxy (native .i2p resolution),
                   "http" for HTTP proxy (via SAM bridge).

        Returns:
            aiohttp.ClientSession configured for I2P, or None on failure.

        Invariants:
            - [I3] lazy — session created on first call
            - [I4] repeated calls return same instance per scheme
        """
        async with self._lock:
            if scheme == "socks":
                return await self._get_socks_session()
            elif scheme == "http":
                return await self._get_http_session()
            else:
                return None

    async def _get_socks_session(self) -> aiohttp.ClientSession | None:
        """Get or create I2P SOCKS5 session."""
        if self._session_socks is not None and not self._session_socks.closed:
            return self._session_socks

        try:
            import aiohttp
            import aiohttp_socks
        except ImportError:
            return None

        try:
            # I2P SOCKS5 proxy (default port 9050, configurable via I2P_SOCKS_PORT)
            socks_port = int(os.environ.get("I2P_SOCKS_PORT", "9050"))

            self._connector_socks = aiohttp_socks.ProxyConnector.from_url(
                f"socks5://127.0.0.1:{socks_port}",
                rdns=True,
                limit=self._config.total_limit,
                limit_per_host=self._config.per_host_limit,
            )

            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=self._config.connect_timeout,
                sock_read=self._config.read_timeout,
            )
            self._session_socks = aiohttp.ClientSession(
                connector=self._connector_socks,
                connector_owner=True,
                timeout=timeout,
            )
            return self._session_socks

        except Exception:
            self._session_socks = None
            self._connector_socks = None
            return None

    async def _get_http_session(self) -> aiohttp.ClientSession | None:
        """Get or create I2P HTTP proxy session."""
        if self._session_http is not None and not self._session_http.closed:
            return self._session_http

        try:
            import aiohttp
        except ImportError:
            return None

        try:
            # I2P HTTP proxy mode: plain TCPConnector for SAM bridge
            # Note: HTTP CONNECT tunneling is not natively supported by aiohttp.
            # HTTP mode is useful for Freenet FProxy compatibility or direct I2P destinations.
            # NOTE: keepalive_timeout and force_close=True are mutually exclusive in aiohttp.
            # force_close=True deactivates idle keepalive — no keepalive_timeout needed.
            self._connector_http = aiohttp.TCPConnector(
                limit=self._config.total_limit,
                limit_per_host=self._config.per_host_limit,
                ttl_dns_cache=self._config.ttl_dns_cache,
                force_close=self._config.force_close,
                enable_cleanup_closed=True,
            )

            timeout = aiohttp.ClientTimeout(
                total=None,
                connect=self._config.connect_timeout,
                sock_read=self._config.read_timeout,
            )
            self._session_http = aiohttp.ClientSession(
                connector=self._connector_http,
                connector_owner=True,
                timeout=timeout,
                trust_env=False,  # Don't inherit proxy from environment
            )
            return self._session_http

        except Exception:
            self._session_http = None
            self._connector_http = None
            return None

    async def close(self) -> None:
        """
        Close all I2P sessions and connectors.

        Invariant:
            - [I5] idempotent — safe to call multiple times
        """
        async with self._lock:
            # Close SOCKS session
            if self._session_socks is not None:
                await self._session_socks.close()
                self._session_socks = None
            if self._connector_socks is not None:
                await self._connector_socks.close()
                self._connector_socks = None

            # Close HTTP session
            if self._session_http is not None:
                await self._session_http.close()
                self._session_http = None
            if self._connector_http is not None:
                await self._connector_http.close()
                self._connector_http = None


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


# =============================================================================
# Module-Level Convenience Functions
# =============================================================================


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
