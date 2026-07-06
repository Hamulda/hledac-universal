"""
transport/unified_transport.py

Unified HTTP Transport Factory — Sprint Issue #7
================================================

Single entry point for all HTTP fetching. Replaces 3-stack matrix:
  - aiohttp.ClientSession (DEPRECATED, disabled)
  - httpx.AsyncClient HTTP/2 (primary for clearnet)
  - curl_cffi.AsyncSession JA3 (fingerprint spoofing only)

Architecture:
  TransportRuntime.get_client(policy) → appropriate client
  Policy routing is done at the factory level, not per-request

M1 8GB: Bounded session pools, lazy init, ~30MB RAM savings vs 3 separate pools
Python 3.14+: httpx 0.28+ native http2=True (no h2 extra needed for basic H2)

Invariant:
  [UT-1] No network side effect at import time
  [UT-2] Lazy session creation on first await
  [UT-3] Bounded pools: max 4 httpx, max 3 curl_cffi profiles
  [UT-4] Fail-safe: any error returns None, caller has fallback path
  [UT-5] Sessions closed only via close_all() at winddown
"""
from __future__ import annotations

import asyncio
import logging
import time

from hledac.universal.utils.async_helpers import safe_gather_fire_and_forget
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from hledac.universal.core.constants import M1_BOUNDS

if __name__ == "__main__":
    # Allow running as script for smoke test
    import sys
    sys.path.insert(0, str(__file__).rsplit("/", 4)[0])

logger = logging.getLogger(__name__)

# =============================================================================
# Transport Policy — routing decision envelope
# =============================================================================

class TransportKind(Enum):
    """HTTP client kind — determines which stack is used."""
    HTTPX_H2 = auto()      # httpx AsyncClient, HTTP/2 capable
    CURL_CFFI = auto()     # curl_cffi with JA3 fingerprint spoofing
    CURL_CFFI_TOR = auto() # curl_cffi via Tor SOCKS5H
    CURL_CFFI_I2P = auto() # curl_cffi via I2P SOCKS5H


@dataclass(frozen=True, slots=True)
class TransportPolicy:
    """
    Policy that determines which transport to use.

    frozen=True: hashable, can be dict key
    slots=True: ~200 bytes saved vs dataclass without slots on M1 8GB
    """
    kind: TransportKind = TransportKind.HTTPX_H2
    tls_profile: str | None = None  # None=default, "chrome136"=JA3 Chrome, etc.
    timeout_s: float = 10.0
    max_connections: int = 20
    max_keepalive: int = 10

    # JA3 profile aliases (user-facing names)
    JA3_CHROME = "chrome136"
    JA3_SAFARI = "safari17_4"
    JA3_FIREFOX = "firefox136"

    # Proxy URLs (class-level for PolicyRouting)
    TOR_PROXY: str | None = None  # Set via env TOR_SOCKS_PROXY_URL
    I2P_PROXY: str | None = None  # Set via env I2P_SOCKS_PROXY_URL


# =============================================================================
# Canonical Policy instances (pre-built, reuse)
# =============================================================================

# Primary clearnet HTTP/2 — httpx
POLICY_CLEARNET_H2 = TransportPolicy(
    kind=TransportKind.HTTPX_H2,
    timeout_s=10.0,
)

# JA3 Chrome impersonation — curl_cffi stealth
POLICY_STEALTH_CHROME = TransportPolicy(
    kind=TransportKind.CURL_CFFI,
    tls_profile="chrome136",
    timeout_s=15.0,
)

# JA3 Safari impersonation — curl_cffi for academic/gov targets
POLICY_STEALTH_SAFARI = TransportPolicy(
    kind=TransportKind.CURL_CFFI,
    tls_profile="safari17_4",
    timeout_s=15.0,
)

# Tor transport — curl_cffi via SOCKS5H
POLICY_TOR = TransportPolicy(
    kind=TransportKind.CURL_CFFI_TOR,
    tls_profile="chrome136",
    timeout_s=30.0,
)

# I2P transport — curl_cffi via SOCKS5H
POLICY_I2P = TransportPolicy(
    kind=TransportKind.CURL_CFFI_I2P,
    tls_profile="chrome136",
    timeout_s=30.0,
)


# =============================================================================
# HTTPX Client Pool — bounded, lazy singleton per policy key
# =============================================================================

_HTTPX_MAX_CLIENTS = 4  # max distinct AsyncClient instances


class _HttpxPool:
    """Bounded httpx AsyncClient pool with lazy init."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}  # key → AsyncClient
        self._lock = asyncio.Lock()
        self._access_order: list[str] = []  # LRU tracking

    async def get_client(
        self,
        key: str,
        timeout_s: float = 10.0,
        max_connections: int = 20,
        max_keepalive: int = 10,
    ) -> Any | None:
        """Get or create httpx AsyncClient for key."""
        async with self._lock:
            if key in self._clients:
                client = self._clients[key]
                if hasattr(client, "closed") and not client.closed:
                    return client
                # Closed — remove
                del self._clients[key]
                if key in self._access_order:
                    self._access_order.remove(key)

            # Evict LRU if at capacity
            while len(self._clients) >= _HTTPX_MAX_CLIENTS and self._access_order:
                oldest_key = self._access_order.pop(0)
                if oldest_key in self._clients:
                    old_client = self._clients.pop(oldest_key)
                    try:
                        if hasattr(old_client, "aclose"):
                            asyncio.create_task(
                                old_client.aclose(),
                                name=f"httpx:evict:{oldest_key}",
                            )
                    except Exception:  # noqa: BLE001
                        pass

            # Create new client (lazy import)
            try:
                import httpx
            except ImportError:
                logger.debug("[HTTPX] httpx not available")
                return None

            client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s),
                http2=True,  # HTTP/2 enabled (h2 extra required)
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive,
                ),
            )
            self._clients[key] = client
            self._access_order.append(key)
            logger.debug(f"[HTTPX] client created: {key}")
            return client

    async def close_all(self) -> None:
        """Close all httpx clients. Idempotent."""
        async with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._access_order.clear()

        for client in clients:
            try:
                if hasattr(client, "aclose"):
                    await client.aclose()
            except Exception:  # noqa: BLE001
                pass
        logger.debug(f"[HTTPX] {len(clients)} clients closed")


# =============================================================================
# curl_cffi Session Pool — bounded, keyed by (host, profile)
# =============================================================================

_CURL_CFFI_MAX_PROFILES = 3  # max distinct JA3 profiles cached


class _CurlCffiPool:
    """Bounded curl_cffi AsyncSession pool with per-host caching.

    Proxy is stored alongside session (session, proxy) since AsyncSession
    doesn't accept proxies in constructor — proxies are per-request.
    """

    def __init__(self) -> None:
        # (profile, proxy) → (AsyncSession, proxy_str)
        self._sessions: dict[str, tuple[Any, str | None]] = {}
        self._host_sessions: dict[str, tuple[Any, float, str]] = {}  # host → (session, time, profile)
        self._lock = asyncio.Lock()
        self._profile_order: list[str] = []  # LRU
        self._host_order: list[str] = []  # LRU

        # Load M1 bounds
        try:
            _m1 = M1_BOUNDS()
            self._max_host_sessions = _m1.curl_host_session_max
            self._host_ttl_s = _m1.curl_host_session_ttl_s
        except Exception:  # noqa: BLE001
            self._max_host_sessions = 64
            self._host_ttl_s = 300.0

    async def get_session(
        self,
        host: str,
        profile: str = "chrome110",
        proxy: str | None = None,
    ) -> tuple[bool, Any, str]:
        """
        Get or create curl_cffi AsyncSession.

        Proxy is baked into the cache key so Tor vs I2P sessions are distinct.

        Returns:
            (success, session_or_None, used_profile)
        """
        from urllib.parse import urlparse

        cache_key = f"{profile}:{proxy or ''}"  # unique per (profile, proxy) pair

        async with self._lock:
            # Check profile cache
            if cache_key in self._sessions:
                cached_session, _cached_proxy = self._sessions[cache_key]
                if hasattr(cached_session, "closed") and not cached_session.closed:
                    # Update host cache
                    await self._cache_host_locked(host, cached_session, profile)
                    return True, cached_session, profile
                del self._sessions[cache_key]
                if cache_key in self._profile_order:
                    self._profile_order.remove(cache_key)

            # Evict LRU profile if at capacity
            while len(self._sessions) >= _CURL_CFFI_MAX_PROFILES and self._profile_order:
                oldest_key = self._profile_order.pop(0)
                if oldest_key in self._sessions:
                    old_session, _old_proxy = self._sessions.pop(oldest_key)
                    try:
                        if hasattr(old_session, "aclose"):
                            asyncio.create_task(
                                old_session.aclose(),
                                name=f"curl:profile_evict:{oldest_key}",
                            )
                    except Exception:  # noqa: BLE001
                        pass

            # Create new session
            try:
                from curl_cffi.requests import AsyncSession  # type: ignore[unresolved-import]
            except ImportError:
                return False, None, "import_error"

            try:
                session = AsyncSession(
                    impersonate=profile,
                    timeout=10.0,
                    max_clients=25,
                )
                self._sessions[cache_key] = (session, proxy)
                self._profile_order.append(cache_key)

                await self._cache_host_locked(host, session, profile)
                return True, session, profile
            except Exception as e:
                return False, None, str(e)

    async def _cache_host_locked(
        self,
        host: str,
        session: Any,
        profile: str,
    ) -> None:
        """Cache session per-host (must hold lock)."""
        now = time.monotonic()

        # Evict expired
        if host in self._host_sessions:
            _expired_sess, old_time, _ = self._host_sessions[host]
            if now - old_time > self._host_ttl_s:
                del self._host_sessions[host]
                if host in self._host_order:
                    self._host_order.remove(host)

        # Evict LRU host if at capacity
        while len(self._host_sessions) >= self._max_host_sessions and self._host_order:
            oldest_host = self._host_order.pop(0)
            if oldest_host in self._host_sessions:
                _evicted_sess, _, _ = self._host_sessions.pop(oldest_host)
                try:
                    if hasattr(_evicted_sess, "aclose"):
                        asyncio.create_task(
                            _evicted_sess.aclose(),
                            name=f"curl:host_evict:{oldest_host}",
                        )
                except Exception:  # noqa: BLE001
                    pass

        self._host_sessions[host] = (session, now, profile)
        self._host_order.append(host)

    async def close_all(self) -> None:
        """Close all curl_cffi sessions. Idempotent."""
        async with self._lock:
            # _sessions values are (session, proxy) tuples
            profile_sessions = [s for s, _ in self._sessions.values()]
            self._sessions.clear()
            self._profile_order.clear()

            host_sessions = [s for s, _, _ in self._host_sessions.values()]
            self._host_sessions.clear()
            self._host_order.clear()

        for session in profile_sessions + host_sessions:
            try:
                if hasattr(session, "aclose"):
                    await session.aclose()
            except Exception:  # noqa: BLE001
                pass
        logger.debug(f"[curl_cffi] {len(profile_sessions)} profiles + {len(host_sessions)} hosts closed")


# =============================================================================
# Transport Runtime — unified factory
# =============================================================================

_httpx_pool = _HttpxPool()
_curl_pool = _CurlCffiPool()
_initialized = False
_init_lock = asyncio.Lock()


async def close_all_transports() -> None:
    """Close all transport pools. Call at winddown."""
    global _initialized
    # F320: asyncio.gather -> safe_gather_fire_and_forget (fail-soft, no return value needed)
    await safe_gather_fire_and_forget(
        _httpx_pool.close_all(),
        _curl_pool.close_all(),
        label="close_all_transports",
    )
    _initialized = False
    logger.debug("[TransportRuntime] all transports closed")


# --- Proxy constants (from curl_cffi_fetch.py) ---
_TOR_PROXY = "socks5h://127.0.0.1:9050"
_I2P_PROXY = "socks5h://127.0.0.1:4447"


async def get_transport_client(
    policy: TransportPolicy,
    url: str,
) -> tuple[bool, Any, str]:
    """
    Get appropriate transport client for the policy + URL.

    Returns:
        (success, client_or_None, transport_kind_str)

    Idempotent, fail-safe. Any error returns (False, None, error_reason).
    """
    global _initialized

    async with _init_lock:
        if not _initialized:
            _initialized = True
            logger.debug("[TransportRuntime] initialized")

    # Extract host from URL for per-host caching
    host = ""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.netloc or ""
    except Exception:  # noqa: BLE001
        pass

    kind = policy.kind

    if kind == TransportKind.HTTPX_H2:
        key = f"h2:{policy.timeout_s}:{policy.max_connections}"
        client = await _httpx_pool.get_client(
            key=key,
            timeout_s=policy.timeout_s,
            max_connections=policy.max_connections,
            max_keepalive=policy.max_keepalive,
        )
        if client is None:
            return False, None, "httpx_unavailable"
        return True, client, "httpx_h2"

    elif kind in (TransportKind.CURL_CFFI, TransportKind.CURL_CFFI_TOR, TransportKind.CURL_CFFI_I2P):
        profile = policy.tls_profile or "chrome110"

        # Determine proxy based on policy kind
        proxy: str | None = None
        if kind == TransportKind.CURL_CFFI_TOR:
            import os
            proxy = os.environ.get("TOR_SOCKS_PROXY_URL", _TOR_PROXY)
        elif kind == TransportKind.CURL_CFFI_I2P:
            import os
            proxy = os.environ.get("I2P_SOCKS_PROXY_URL", _I2P_PROXY)

        ok, session, used_profile = await _curl_pool.get_session(
            host=host,
            profile=profile,
            proxy=proxy,
        )
        if not ok:
            return False, None, f"curl_cffi_error:{session}" if session else "curl_cffi_unavailable"
        return True, session, f"curl_cffi:{used_profile}"

    else:
        return False, None, f"unknown_policy:{kind}"


# =============================================================================
# Unified Fetch API — single entry point for all HTTP fetching
# =============================================================================

async def fetch_via_unified(
    url: str,
    policy: TransportPolicy | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
    max_bytes: int = 10 * 1024 * 1024,
) -> dict[str, Any]:
    """
    Unified fetch API — single entry point replacing ad-hoc fetch functions.

    Args:
        url: Target URL
        policy: TransportPolicy to use. Defaults to POLICY_CLEARNET_H2.
        headers: Optional HTTP headers
        timeout_s: Request timeout
        max_bytes: Maximum response bytes to read

    Returns:
        FetchResult-compatible dict with keys:
        - url, final_url, status_code, content_type, text, fetched_bytes,
          declared_length, elapsed_ms, error, failure_stage, headers
    """
    if policy is None:
        policy = POLICY_CLEARNET_H2

    ok, client, kind = await get_transport_client(policy, url)
    if not ok or client is None:
        return {
            "url": url,
            "final_url": url,
            "status_code": 0,
            "content_type": "",
            "text": None,
            "fetched_bytes": 0,
            "declared_length": -1,
            "elapsed_ms": 0,
            "error": f"transport_unavailable:{kind}",
            "failure_stage": "transport_dispatch",
            "headers": {},
        }

    t0 = time.monotonic()

    try:
        # httpx path
        if kind.startswith("httpx"):
            import httpx

            extra_headers = headers or {}
            resp = await client.get(
                url,
                headers=extra_headers,
                timeout=timeout_s,
                follow_redirects=True,
            )
            body = resp.content[:max_bytes]
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "url": url,
                "final_url": str(resp.url),
                "status_code": resp.status_code,
                "content_type": resp.headers.get("Content-Type", ""),
                "text": body.decode("utf-8", errors="replace") if body else "",
                "fetched_bytes": len(body),
                "declared_length": int(resp.headers.get("Content-Length", -1)),
                "elapsed_ms": elapsed_ms,
                "error": None,
                "failure_stage": None,
                "headers": dict(resp.headers),
            }

        # curl_cffi path
        elif kind.startswith("curl_cffi"):
            proxies = None
            if policy.kind == TransportKind.CURL_CFFI_TOR:
                import os
                proxies = {"https": os.environ.get("TOR_SOCKS_PROXY_URL", _TOR_PROXY)}
            elif policy.kind == TransportKind.CURL_CFFI_I2P:
                import os
                proxies = {"https": os.environ.get("I2P_SOCKS_PROXY_URL", _I2P_PROXY)}

            extra_headers = headers or {}
            resp = await client.get(
                url,
                headers=extra_headers,
                timeout=timeout_s,
                proxies=proxies,
            )
            body = resp.content[:max_bytes]
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "url": url,
                "final_url": url,
                "status_code": resp.status_code,
                "content_type": resp.headers.get("Content-Type", ""),
                "text": body.decode("utf-8", errors="replace") if body else "",
                "fetched_bytes": len(body),
                "declared_length": -1,
                "elapsed_ms": elapsed_ms,
                "error": None,
                "failure_stage": None,
                "headers": dict(resp.headers) if hasattr(resp, "headers") else {},
            }

        else:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return {
                "url": url,
                "final_url": url,
                "status_code": 0,
                "content_type": "",
                "text": None,
                "fetched_bytes": 0,
                "declared_length": -1,
                "elapsed_ms": elapsed_ms,
                "error": f"unknown_transport:{kind}",
                "failure_stage": "transport_dispatch",
                "headers": {},
            }

    except asyncio.CancelledError:
        raise
    except Exception as e:
        elapsed_ms = (time.monotonic() - t0) * 1000
        return {
            "url": url,
            "final_url": url,
            "status_code": 0,
            "content_type": "",
            "text": None,
            "fetched_bytes": 0,
            "declared_length": -1,
            "elapsed_ms": elapsed_ms,
            "error": str(e),
            "failure_stage": "fetch",
            "headers": {},
        }


# =============================================================================
# Backward-compat aliases — redirect old call sites to new factory
# =============================================================================

# Re-exportJA3 helpers from curl_cffi_fetch for existing consumers
from .curl_cffi_fetch import (  # noqa: E402, F401
    is_curl_cffi_available,
    next_ja3_profile,
    reset_ja3_cycle,
    get_curl_cffi_runtime_status,
    close_curl_cffi_sessions_async,
    fetch_via_tor_curl_cffi,
    fetch_via_i2p_curl_cffi,
    fetch_via_curl_cffi_cached,
)


# =============================================================================
# Smoke test
# =============================================================================

if __name__ == "__main__":
    async def _smoke() -> None:
        print("[SMOKE] Testing TransportRuntime...")

        # Test policy creation
        p = TransportPolicy(kind=TransportKind.HTTPX_H2)
        print(f"  Policy created: {p}")

        p2 = TransportPolicy(kind=TransportKind.CURL_CFFI, tls_profile="chrome136")
        print(f"  JA3 Policy: {p2}")

        # Test canonical policies exist
        print(f"  POLICY_CLEARNET_H2: {POLICY_CLEARNET_H2}")
        print(f"  POLICY_STEALTH_CHROME: {POLICY_STEALTH_CHROME}")

        print("[SMOKE] TransportRuntime OK")
        await close_all_transports()

    asyncio.run(_smoke())
