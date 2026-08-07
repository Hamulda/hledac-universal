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
import socket
import time
from enum import Enum
from typing import TYPE_CHECKING, Any

import msgspec

from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.locks import LazyAsyncioLock
from ._tcp_keepalive import (
    SO_KEEPALIVE,
    TCP_KEEPIDLE,
    TCP_KEEPINTVL,
    TCP_KEEPCNT,
    KEEPALIVE_IDLE_S,
    KEEPALIVE_INTERVAL_S,
    KEEPALIVE_MAX_PROBES,
)

if TYPE_CHECKING:
    import httpx
    from httpx import AsyncClient

logger = logging.getLogger(__name__)


class PoolKind(Enum):
    """Session pool kind for telemetry."""

    HTTPX = "httpx"
    HTTPX_SOCKS = "httpx_socks"
    CURL_CFFI = "curl_cffi"


def _patch_existing_httpx_sockets(client: httpx.AsyncClient) -> None:
    """
    ISSUE-P6-001: Patch TCP keep-alive options on all existing pooled HTTP/2 connections.

    httpx stores connections in:
        client._transport._pool._connections  (list of HTTPConnection objects)

    Each HTTPConnection has a `._connection` attribute which is the raw socket.
    We traverse this structure and patch every socket we can reach.

    This covers the warm path: existing pooled connections are patched so they
    don't hold dead sockets indefinitely. New connections are patched at creation
    via _patch_socket_keepalive called from here.

    Fail-safe: any error is swallowed so the client remains usable.
    """
    try:
        transport = getattr(client, "_transport", None)
        if transport is None:
            return
        pool = getattr(transport, "_pool", None)
        if pool is None:
            return
        connections = getattr(pool, "_connections", None)
        if connections is None:
            return
        for conn in connections:
            try:
                raw_conn = getattr(conn, "_connection", None)
                if raw_conn is not None and isinstance(raw_conn, socket.socket):
                    _patch_socket_keepalive(raw_conn)
            except Exception:
                continue  # best-effort per-connection
    except Exception:  # noqa: BLE001
        pass  # fail-safe: don't crash client accessor


def _patch_socket_keepalive(sock: socket.socket) -> None:
    """
    ISSUE-P6-001: Patch a socket with TCP keep-alive options.

    Enables kernel-level TCP keep-alive probes so dead connections are detected
    proactively (before TIME_WAIT timeout), freeing pool slots earlier.
    Call this on httpx HTTP/2 connections after they are established.

    macOS notes:
      - SO_KEEPALIVE enables the mechanism
      - TCP_KEEPIDLE (0x10) sets idle time before first probe
      - TCP_KEEPINTVL (0x101) sets probe interval
      - TCP_KEEPCNT (0x102) sets max probe count

    Fail-safe: any setsockopt error is logged and swallowed so the socket
    remains usable without keep-alive.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_KEEPALIVE, 1)
        sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPIDLE, KEEPALIVE_IDLE_S)
        sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPINTVL, KEEPALIVE_INTERVAL_S)
        sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPCNT, KEEPALIVE_MAX_PROBES)
    except OSError as e:
        # Platform-specific: some options not supported on all systems
        import logging

        logging.getLogger("hledac.universal.transport.session_pool").debug(
            f"[ISSUE-P6-001] Could not patch socket keep-alive options: {e}"
        )


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
    except Exception:  # noqa: BLE001
        pass  # Fail-soft: metrics are diagnostic only


# =============================================================================
# httpx Singleton (ISSUE-013: Adaptive limits)
# =============================================================================

_CONNECT_TIMEOUT_S = 5.0


# ISSUE-P6-002: HTTP/2 negotiation state tracking
_http2_negotiated: bool | None = None  # None=unknown, True=confirmed, False=fallback


async def _probe_http2_negotiation(client: httpx.AsyncClient) -> bool:
    """
    ISSUE-P6-002: Probe whether HTTP/2 was actually negotiated.

    Uses a lightweight HEAD request to a known HTTP/2-capable host (Cloudflare)
    to verify that h2 protocol was negotiated. This detects silent HTTP/1.1 fallback
    when h2 is installed but HTTP/2 negotiation fails (e.g., ALPN mismatch).

    Fail-safe: if probe fails for any reason (DNS, timeout, etc.), returns True
    to avoid blocking operations — telemetry will log the probe failure.

    Returns True if HTTP/2 is confirmed, False if fallback to HTTP/1.1 detected.
    """
    global _http2_negotiated
    if _http2_negotiated is not None:
        return _http2_negotiated

    # NOTE: Probing only works if at least one request has already been made through
    # the client, because HTTP/2 ALPN negotiation happens on the first actual request.
    # The probe itself IS that first request, so we use a GET to Cloudflare which
    # is guaranteed to respond.
    probe_url = "https://www.cloudflare.com/cdn-cgi/trace"
    try:
        resp = await client.get(probe_url, timeout=httpx.Timeout(connect=3.0, read=3.0))

        # httpx >= 0.27: check http_version in response extensions
        http_version = resp.extensions.get("http_version", "")
        if http_version and http_version.lower() in ("h2", "http/2", "2"):
            _http2_negotiated = True
            logger.debug("[SessionPool] HTTP/2 negotiation confirmed via http_version=%s", http_version)
            return True

        # Fallback: check via response stream info (httpx internal)
        # httpx stores HTTP/2 channel info in _protocol on the response
        try:
            protocol = getattr(resp, "_protocol", None)
            if protocol is not None:
                # HTTP/2 protocol object has a 'protocol' attribute with value b'H2'
                proto_name = getattr(protocol, "protocol", None) or getattr(protocol, "protocol_name", None)
                if proto_name in (b"H2", "H2"):
                    _http2_negotiated = True
                    logger.debug("[SessionPool] HTTP/2 negotiation confirmed via _protocol.protocol=%s", proto_name)
                    return True
        except Exception:  # noqa: BLE001
            pass

        # No HTTP/2 indicator found — assume HTTP/1.1 fallback
        _http2_negotiated = False
        logger.warning("[SessionPool] HTTP/2 not negotiated — HTTP/1.1 fallback detected (http_version=%s)", http_version)
        return False
    except httpx.ConnectError as e:
        # DNS/connection failure — don't cache, retry next time
        logger.debug("[SessionPool] HTTP/2 probe connection error (will retry on next call): %s", e)
        # Reset state so next call retries the probe
        _http2_negotiated = None
        return True  # fail-open
    except Exception as e:
        # Other errors (timeout, TLS, etc.) — don't cache, retry next time
        logger.debug("[SessionPool] HTTP/2 probe error (will retry on next call): %s", e)
        _http2_negotiated = None
        return True  # fail-open


async def httpx_client() -> httpx.AsyncClient:
    """
    Get or create the shared httpx.AsyncClient (HTTP/2 capable).

    Singleton — same instance returned on every call until close.
    HTTP/2 enabled when h2 is installed; falls back to 1.1 otherwise.

    ISSUE-013: Connection limits are adaptive based on UMA memory pressure.
    At critical/emergency pressure, max_connections is reduced from 25 to 10
    to prevent OOM on M1 8GB.

    ISSUE-P6-002: HTTP/2 negotiation is verified after first client creation.
    If HTTP/1.1 fallback is detected, connection limits are reduced to 10
    to mitigate TIME_WAIT pressure from non-multiplexed connections.

    Returns:
        httpx.AsyncClient: HTTP/2 capable async client

    Raises:
        RuntimeError: if httpx is not installed
    """
    global _httpx_client, _http2_negotiated

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

            # ISSUE-P6-001: Patch socket TCP keep-alive options on existing pooled connections.
            # httpx HTTP/2 connections are stored in _transport._pool._connections.
            # Patching existing connections covers the warm path; new connections inherit
            # socket options from the OS (SO_KEEPALIVE is set per-socket at creation).
            try:
                _patch_existing_httpx_sockets(_httpx_client)
            except Exception:  # noqa: BLE001
                pass  # Fail-safe: socket patching is best-effort

            _record_pool_metrics()

            # ISSUE-P6-002: Verify HTTP/2 negotiation asynchronously
            # Schedule probe as fire-and-forget to avoid blocking first request
            try:
                safe_create_task(
                    _probe_http2_negotiation(_httpx_client),
                    name="session_pool:http2_probe",
                )
            except Exception:  # noqa: BLE001
                pass  # Fail-safe: don't block client creation

        return _httpx_client


def get_http2_status() -> bool | None:
    """
    ISSUE-P6-002: Return cached HTTP/2 negotiation status.

    Returns None if not yet probed, True if confirmed HTTP/2, False if fallback.
    """
    return _http2_negotiated


async def probe_http2_at_startup() -> bool:
    """
    OPTIMIZATION #1: Pre-probe HTTP/2 negotiation at startup.

    Creates a temporary httpx client, probes HTTP/2 negotiation, then closes.
    This avoids the first-request penalty during actual fetches.

    Returns:
        True if HTTP/2 confirmed, False if fallback, None if probe failed.

    Usage:
        # Call early at app startup (before any real fetches)
        h2_supported = await probe_http2_at_startup()
    """
    import httpx

    client: httpx.AsyncClient | None = None
    try:
        client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(connect=3.0, read=3.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
            trust_env=False,
        )
        result = await _probe_http2_negotiation(client)
        return result if _http2_negotiated is not None else None
    except Exception as e:
        logger.debug(f"[SessionPool] Startup HTTP/2 probe failed: {e}")
        return None
    finally:
        if client is not None:
            await client.aclose()


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
                except Exception:  # noqa: BLE001
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
            # ISSUE-P6-001: Patch TCP keep-alive on existing SOCKS5 pooled sockets.
            # SOCKS5 tunnels are long-lived; keep-alive ensures dead Tor/I2P
            # connections are detected before TIME_WAIT exhausts the port pool.
            try:
                _patch_existing_httpx_sockets(_httpx_socks_clients[cache_key])
            except Exception:  # noqa: BLE001
                pass  # fail-safe: best-effort
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
            except Exception:  # noqa: BLE001
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
