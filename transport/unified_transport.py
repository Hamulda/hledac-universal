"""
transport/unified_transport.py

Unified HTTP Transport Factory — Sprint Issue #7

================================================

Single entry point for all HTTP fetching. Replaces 3-stack matrix:
  - aiohttp.ClientSession — REMOVED (F350M-R: migrated to httpx/curl_cffi)
  - httpx.AsyncClient HTTP/2 (primary for clearnet)
  - curl_cffi.AsyncSession JA3 (fingerprint spoofing, Tor/I2P darknet)

Note: aiohttp (StealthSession) was migrated to curl_cffi for JA3 fingerprint
  support (critical for OSINT stealth operations).

Architecture:
  TransportRuntime.get_client(policy) → appropriate client
  Policy routing is done at the factory level, not per-request

M1 8GB: Bounded session pools, lazy init, ~30MB RAM savings vs 3 separate pools
Python 3.14+: httpx 0.28+ native http2=True (no h2 extra needed for basic H2)

Invariant:
  [UT-1] No network side effect at import time
  [UT-2] Lazy session creation on first await
  [UT-3] Bounded pools: max 4 httpx, max 3 curl_cffi profiles, max 256 DNS entries
  [UT-4] Fail-safe: any error returns None, caller has fallback path
  [UT-5] Sessions closed only via close_all() at winddown
  [UT-6] DNS prefetch: fire-and-forget, never blocks transport
"""

import asyncio
import logging
import os
import time
from enum import Enum, auto
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal._core.constants import M1_BOUNDS
from hledac.universal._core.env_config import ENV
from hledac.universal.utils.asyncx import parallel, safe_create_task, safe_wait_for
from hledac.universal.utils.locks import LazyAsyncioLock

if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(__file__).rsplit("/", 4)[0])
logger = logging.getLogger(__name__)


class TransportKind(Enum):
    """HTTP client kind — determines which stack is used."""

    HTTPX_H2 = auto()
    CURL_CFFI = auto()
    CURL_CFFI_TOR = auto()
    CURL_CFFI_I2P = auto()
    # H3-aware variants: prefer HTTP/3 via ALPN negotiation + 0-RTT for known servers
    CURL_CFFI_H3 = auto()
    CURL_CFFI_H3_TOR = auto()
    CURL_CFFI_H3_I2P = auto()


class TransportPolicy(Struct, frozen=True):
    """
    Policy that determines which transport to use.

    frozen=True: hashable, can be dict key
    slots=True: ~200 bytes saved vs dataclass without slots on M1 8GB
    """

    kind: TransportKind = TransportKind.HTTPX_H2
    tls_profile: str | None = None
    timeout_s: float = 10.0
    max_connections: int = 20
    max_keepalive: int = 10
    JA3_CHROME = "chrome136"
    JA3_SAFARI = "safari17_4"
    JA3_FIREFOX = "firefox136"
    TOR_PROXY: str | None = None
    I2P_PROXY: str | None = None


POLICY_CLEARNET_H2 = TransportPolicy(kind=TransportKind.HTTPX_H2, timeout_s=10.0)
POLICY_STEALTH_CHROME = TransportPolicy(kind=TransportKind.CURL_CFFI, tls_profile="chrome136", timeout_s=15.0)
POLICY_STEALTH_SAFARI = TransportPolicy(kind=TransportKind.CURL_CFFI, tls_profile="safari17_4", timeout_s=15.0)
POLICY_TOR = TransportPolicy(kind=TransportKind.CURL_CFFI_TOR, tls_profile="chrome136", timeout_s=30.0)
POLICY_I2P = TransportPolicy(kind=TransportKind.CURL_CFFI_I2P, tls_profile="chrome136", timeout_s=30.0)
# H3-aware policies: prefer HTTP/3 via ALPN for known servers (0-RTT enabled)
POLICY_H3_CHROME = TransportPolicy(kind=TransportKind.CURL_CFFI_H3, tls_profile="chrome136", timeout_s=15.0)
POLICY_H3_SAFARI = TransportPolicy(kind=TransportKind.CURL_CFFI_H3, tls_profile="safari17_4", timeout_s=15.0)
POLICY_H3_TOR = TransportPolicy(kind=TransportKind.CURL_CFFI_H3_TOR, tls_profile="chrome136", timeout_s=30.0)
POLICY_H3_I2P = TransportPolicy(kind=TransportKind.CURL_CFFI_H3_I2P, tls_profile="chrome136", timeout_s=30.0)
_HTTPX_MAX_CLIENTS = 4


class _HttpxPool:
    """Bounded httpx AsyncClient pool with lazy init."""

    __slots__ = ("_access_order", "_clients", "_lock")

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._access_order: list[str] = []

    async def get_client(
        self, key: str, timeout_s: float = 10.0, max_connections: int = 20, max_keepalive: int = 10
    ) -> Any | None:
        """Get or create httpx AsyncClient for key."""
        async with self._lock:
            if key in self._clients:
                client = self._clients[key]
                if hasattr(client, "closed") and (not client.closed):
                    return client
                del self._clients[key]
                if key in self._access_order:
                    self._access_order.remove(key)
            while len(self._clients) >= _HTTPX_MAX_CLIENTS and self._access_order:
                oldest_key = self._access_order.pop(0)
                if oldest_key in self._clients:
                    old_client = self._clients.pop(oldest_key)
                    try:
                        if hasattr(old_client, "aclose"):
                            safe_create_task(old_client.aclose(), name=f"httpx:evict:{oldest_key}")
                    except AttributeError:  # aclose not available on evicted client  # noqa: BLE001
                        pass
            try:
                import httpx
            except ImportError:
                logger.debug("[HTTPX] httpx not available")
                return None
            client = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_s),
                http2=True,
                limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive),
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
            except AttributeError:  # aclose not available on closed client  # noqa: BLE001
                pass
        logger.debug(f"[HTTPX] {len(clients)} clients closed")


_CURL_CFFI_MAX_PROFILES = 3


class _CurlCffiPool:
    """Bounded curl_cffi AsyncSession pool with per-host caching.

    Proxy is stored alongside session (session, proxy) since AsyncSession
    doesn't accept proxies in constructor — proxies are per-request.
    """

    __slots__ = (
        "_host_order",
        "_host_sessions",
        "_host_ttl_s",
        "_lock",
        "_max_host_sessions",
        "_profile_order",
        "_sessions",
    )

    def __init__(self) -> None:
        self._sessions: dict[str, tuple[Any, str | None]] = {}
        self._host_sessions: dict[str, tuple[Any, float, str]] = {}
        self._lock = asyncio.Lock()
        self._profile_order: list[str] = []
        self._host_order: list[str] = []
        try:
            _m1 = M1_BOUNDS()
            self._max_host_sessions = _m1.curl_host_session_max
            self._host_ttl_s = _m1.curl_host_session_ttl_s
        except AttributeError, KeyError, TypeError:  # M1_BOUNDS() attributes missing or invalid
            self._max_host_sessions = 64
            self._host_ttl_s = 300.0

    async def get_session(
        self, host: str, profile: str = "chrome110", proxy: str | None = None, http_version: Any = None
    ) -> tuple[bool, Any, str]:
        """
        Get or create curl_cffi AsyncSession.

        Proxy is baked into the cache key so Tor vs I2P sessions are distinct.
        http_version is included in cache key so H3 vs H2 sessions are distinct.

        Args:
            host: Target host for H3 ALPN negotiation.
            profile: TLS JA3 profile (e.g., 'chrome136').
            proxy: SOCKS proxy URL for Tor/I2P.
            http_version: Optional HttpVersion.v3 hint for H3-capable servers.
                          When set, curl_cffi will attempt HTTP/3 via ALPN.

        Returns:
            (success, session_or_None, used_profile)
        """
        h3_hint = "h3" if http_version else "h2"
        cache_key = f"{profile}:{proxy or ''}:{h3_hint}"
        async with self._lock:
            if cache_key in self._sessions:
                cached_session, _cached_proxy = self._sessions[cache_key]
                if hasattr(cached_session, "closed") and (not cached_session.closed):
                    await self._cache_host_locked(host, cached_session, profile)
                    return (True, cached_session, profile)
                del self._sessions[cache_key]
                if cache_key in self._profile_order:
                    self._profile_order.remove(cache_key)
            while len(self._sessions) >= _CURL_CFFI_MAX_PROFILES and self._profile_order:
                oldest_key = self._profile_order.pop(0)
                if oldest_key in self._sessions:
                    old_session, _old_proxy = self._sessions.pop(oldest_key)
                    try:
                        if hasattr(old_session, "aclose"):
                            safe_create_task(old_session.aclose(), name=f"curl:profile_evict:{oldest_key}")
                    except AttributeError:  # aclose not available on evicted session  # noqa: BLE001
                        pass
            try:
                from curl_cffi.requests import AsyncSession
            except ImportError:
                return (False, None, "import_error")
            try:
                # http_version enables HTTP/3 ALPN negotiation in curl_cffi >= 0.7
                session = AsyncSession(impersonate=profile, timeout=10.0, max_clients=25, http_version=http_version)
                self._sessions[cache_key] = (session, proxy)
                self._profile_order.append(cache_key)
                await self._cache_host_locked(host, session, profile)
                return (True, session, profile)
            except (ValueError, OSError) as e:  # invalid impersonate profile or network failure
                return (False, None, str(e))

    async def _cache_host_locked(self, host: str, session: Any, profile: str) -> None:
        """Cache session per-host (must hold lock)."""
        now = time.monotonic()
        if host in self._host_sessions:
            _expired_sess, old_time, _ = self._host_sessions[host]
            if now - old_time > self._host_ttl_s:
                del self._host_sessions[host]
                if host in self._host_order:
                    self._host_order.remove(host)
        while len(self._host_sessions) >= self._max_host_sessions and self._host_order:
            oldest_host = self._host_order.pop(0)
            if oldest_host in self._host_sessions:
                _evicted_sess, _, _ = self._host_sessions.pop(oldest_host)
                try:
                    if hasattr(_evicted_sess, "aclose"):
                        safe_create_task(_evicted_sess.aclose(), name=f"curl:host_evict:{oldest_host}")
                except AttributeError:  # aclose not available on evicted session  # noqa: BLE001
                    pass
        self._host_sessions[host] = (session, now, profile)
        self._host_order.append(host)

    async def close_all(self) -> None:
        """Close all curl_cffi sessions. Idempotent."""
        async with self._lock:
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
            except AttributeError:  # aclose not available on session  # noqa: BLE001
                pass
        logger.debug(f"[curl_cffi] {len(profile_sessions)} profiles + {len(host_sessions)} hosts closed")


_httpx_pool = _HttpxPool()
_curl_pool = _CurlCffiPool()
_initialized = False
_init_lock = LazyAsyncioLock()

# DNS cache — extracted to dns_cache.py (F350M-R refactor)

from .dns_cache import get_dns_cache

_dns_cache = get_dns_cache()


async def close_all_transports() -> None:
    """Close all transport pools. Call at winddown."""
    global _initialized
    await parallel(
        [_httpx_pool.close_all(), _curl_pool.close_all(), _dns_cache.close()],
        taskgroup=True,
        policy="log",
        ctx="close_all_transports",
        logger_instance=logger,
    )
    _initialized = False
    logger.debug("[TransportRuntime] all transports closed")


async def prefetch_dns(urls: list[str]) -> None:
    """
    Prefetch DNS for top-500 unique hosts from URL list.

    OPTIMIZATION #2: Fire-and-forget DNS prefetch — NEVER blocks transport.

    Implementation details:
      - Bounded semaphore (50 concurrent) prevents DoT resolver overload
      - Skips darknet hosts (.onion, .i2p) — Tor/I2P handle DNS internally
      - Bounded to 500 unique hosts per call (prevents memory bloat)
      - Rust DNS via DoT (bypasses mDNSResponder) when available
      - Falls back to async_getaddrinfo() if rust.dns unavailable

    Invariant [UT-6]: This never blocks the transport — caller continues
    immediately while DNS resolution happens in background tasks.

    Usage:
        await prefetch_dns([url1, url2, ...])  # Fire-and-forget
        # Transport continues immediately, DNS resolves in background
    """
    await _dns_cache.prefetch(urls)


def dns_cache_status() -> dict[str, Any]:
    """Return DNS cache telemetry snapshot."""
    return _dns_cache.status()


_TOR_PROXY = "socks5h://127.0.0.1:9050"
_I2P_PROXY = "socks5h://127.0.0.1:4447"


async def get_transport_client(policy: TransportPolicy, url: str) -> tuple[bool, Any, str]:
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
    host = ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.netloc or ""
    except ValueError, OSError:  # urlparse: ValueError for malformed URLs, OSError for IDN encoding  # noqa: BLE001
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
            return (False, None, "httpx_unavailable")
        return (True, client, "httpx_h2")
    elif kind in (
        TransportKind.CURL_CFFI,
        TransportKind.CURL_CFFI_TOR,
        TransportKind.CURL_CFFI_I2P,
        TransportKind.CURL_CFFI_H3,
        TransportKind.CURL_CFFI_H3_TOR,
        TransportKind.CURL_CFFI_H3_I2P,
    ):
        profile = policy.tls_profile or "chrome110"
        proxy: str | None = None
        is_h3 = kind in (TransportKind.CURL_CFFI_H3, TransportKind.CURL_CFFI_H3_TOR, TransportKind.CURL_CFFI_H3_I2P)
        if kind in (TransportKind.CURL_CFFI_TOR, TransportKind.CURL_CFFI_H3_TOR):
            proxy = ENV.get_str("TOR_SOCKS_PROXY_URL", _TOR_PROXY)
        elif kind in (TransportKind.CURL_CFFI_I2P, TransportKind.CURL_CFFI_H3_I2P):
            proxy = ENV.get_str("I2P_SOCKS_PROXY_URL", _I2P_PROXY)
        # Resolve HTTP/3 hint: for H3 variants or when Alt-Svc cache knows the host supports h3
        http_version: Any = None
        if is_h3:
            try:
                from hledac.universal.transport.http3_lane import http_version_for_curl_cffi as _h3_resolver

                http_version = _h3_resolver(url)
                # Fire-and-forget speculative probe to prime Alt-Svc cache for future requests.
                # Safe: no-op if cache already warm, no-op outside event loop.
                try:
                    from hledac.universal.transport.http3_lane import probe_altsvc_speculative as _probe

                    _probe(url)
                except Exception:  # noqa: BLE001 — fail-soft: speculative Alt-Svc probe
                    pass
            except Exception:  # noqa: BLE001 — fail-soft: proceed without H3
                pass
        ok, session, used_profile = await _curl_pool.get_session(
            host=host, profile=profile, proxy=proxy, http_version=http_version
        )
        if not ok:
            return (False, None, f"curl_cffi_error:{session}" if session else "curl_cffi_unavailable")
        # session_kind reflects the TRANSPORT POLICY (is_h3), not the transient http_version.
        # This is intentional: is_h3=True means "prefer H3" — the session_kind should reflect
        # that intent even if memory pressure temporarily suppressed the http_version hint.
        session_kind = "curl_cffi_h3" if is_h3 else "curl_cffi"
        return (True, session, f"{session_kind}:{used_profile}")
    else:
        return (False, None, f"unknown_policy:{kind}")


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
        if kind.startswith("httpx"):
            extra_headers = headers or {}
            resp = await client.get(url, headers=extra_headers, timeout=timeout_s, follow_redirects=True)
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
        elif kind.startswith("curl_cffi"):
            proxies = None
            if policy.kind in (TransportKind.CURL_CFFI_TOR, TransportKind.CURL_CFFI_H3_TOR):
                # P0-2 MODERN-02 FIX: Both http and https schemes required!
                # .onion URLs can use http:// or https:// and BOTH must go through proxy.
                proxy = ENV.get_str("TOR_SOCKS_PROXY_URL", _TOR_PROXY)
                proxies = {"http": proxy, "https": proxy}
            elif policy.kind in (TransportKind.CURL_CFFI_I2P, TransportKind.CURL_CFFI_H3_I2P):
                # P0-2 MODERN-02 FIX: Both http and https schemes required!
                # .i2p URLs can use http:// or https:// and BOTH must go through proxy.
                proxy = ENV.get_str("I2P_SOCKS_PROXY_URL", _I2P_PROXY)
                proxies = {"http": proxy, "https": proxy}
            extra_headers = headers or {}
            resp = await client.get(
                url, headers=extra_headers, timeout=timeout_s, proxies=proxies, follow_redirects=True
            )
            body = resp.content[:max_bytes]
            elapsed_ms = (time.monotonic() - t0) * 1000
            # Record Alt-Svc h3 advertisement for future H3 ALPN negotiation
            try:
                from hledac.universal.transport.http3_lane import record_from_curl_cffi_result as _record_h3

                _record_h3(url, resp.headers)
            except Exception:  # noqa: BLE001 — fail-soft: Alt-Svc recording is best-effort
                pass
            # final_url: curl_cffi stores final URL after redirects in resp.url
            final_url = url
            try:
                if hasattr(resp, "url") and resp.url:
                    final_url = str(resp.url)
            except ValueError, AttributeError:  # str(resp.url) can raise ValueError or AttributeError  # noqa: BLE001
                pass
            return {
                "url": url,
                "final_url": final_url,
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


# ISSUE-15: Mini-race fallback — when the primary transport fails, race the
# remaining transports in parallel instead of returning immediately. Bounded:
# at most 2 fallback transports tried concurrently, 8s total timeout.
_MINI_RACE_TIMEOUT_S: float = min(float(os.environ.get("HLEDAC_MINI_RACE_TIMEOUT_S", "8.0")), 15.0)


def _is_darknet_url(url: str) -> bool:
    """Check if URL targets darknet (Tor/I2P/Freenet). Local copy to avoid circular import."""
    url_lower = url.lower()
    return (
        url_lower.endswith(".onion")
        or url_lower.endswith(".i2p")
        or url_lower.endswith(".b32.i2p")
        or url_lower.endswith(".freenet")
    )


async def fetch_via_unified_with_race_fallback(
    url: str,
    policy: TransportPolicy | None = None,
    headers: dict[str, str] | None = None,
    timeout_s: float = 10.0,
    max_bytes: int = 10 * 1024 * 1024,
) -> dict[str, Any]:
    """
    ISSUE-15: Fetch with mini-race fallback — fires remaining transports
    when the primary transport fails.

    This wraps fetch_via_unified() with a lightweight concurrent retry:
      1. Try the requested policy's transport
      2. If it fails, race httpx AND curl_cffi in parallel as fallback
      3. Return first success, or the original error if all fail

    For clearnet URLs, the fallback transport race uses POLICY_CLEARNET_H2
    and POLICY_STEALTH_CHROME concurrently. For darknet/stealth URLs, only
    Tor/I2P-capable transports are used.

    Bounds: 8.0 s mini-race timeout, 2 transports max.
    """
    result = await fetch_via_unified(
        url,
        policy=policy,
        headers=headers,
        timeout_s=timeout_s,
        max_bytes=max_bytes,
    )

    # Success or non-retryable outcome → return immediately
    if result.get("error") is None and result.get("status_code", 0) >= 200:
        return result

    # 4xx errors are non-retryable (client error, not transport error)
    if 400 <= result.get("status_code", 0) < 500 and result["status_code"] != 429:
        return result

    from hledac.universal.transport.unified_transport import (
        POLICY_CLEARNET_H2,
        POLICY_STEALTH_CHROME,
    )

    # Determine which fallback transports to try
    fallback_policies: list[tuple[str, TransportPolicy]] = []
    is_dark = _is_darknet_url(url)
    primary_kind = policy.kind if policy else TransportKind.HTTPX_H2

    if is_dark:
        # Darknet: only Tor/I2P transports
        fallback_policies.append(
            ("tor", TransportPolicy(kind=TransportKind.CURL_CFFI_TOR, tls_profile="chrome136", timeout_s=30.0))
        )
    else:
        # Clearnet: race httpx and curl_cffi
        if primary_kind != TransportKind.HTTPX_H2:
            fallback_policies.append(("httpx", POLICY_CLEARNET_H2))
        if primary_kind != TransportKind.CURL_CFFI:
            fallback_policies.append(("curl_cffi", POLICY_STEALTH_CHROME))

    if not fallback_policies:
        return result

    race_timeout = min(_MINI_RACE_TIMEOUT_S, timeout_s)

    async def _try_fallback(name: str, fb_policy: TransportPolicy) -> dict[str, Any] | None:
        try:
            fb_result = await safe_wait_for(
                fetch_via_unified(
                    url,
                    policy=fb_policy,
                    headers=headers,
                    timeout_s=race_timeout,
                    max_bytes=max_bytes,
                ),
                timeout=race_timeout,
            )
            if fb_result.get("error") is None and fb_result.get("status_code", 0) >= 200:
                # Tag the winning transport
                fb_result["_mini_race_winner"] = name
                return fb_result
            return None
        except TimeoutError:
            return None
        except Exception:
            return None

    try:
        tasks = [
            safe_create_task(_try_fallback(name, fb_policy), name=f"transport_fallback:{name}")
            for name, fb_policy in fallback_policies
        ]
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
            timeout=race_timeout,
        )
        for task in done:
            try:
                fb_result = task.result()
                if fb_result is not None:
                    # Cancel remaining fallbacks
                    for p in pending:
                        p.cancel()
                    return fb_result
            except Exception:  # noqa: BLE001
                pass
        # All fallbacks failed — cancel pending and return original error
        for p in pending:
            p.cancel()
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        raise

    return result


if __name__ == "__main__":

    async def _smoke() -> None:
        print("[SMOKE] Testing TransportRuntime...")
        p = TransportPolicy(kind=TransportKind.HTTPX_H2)
        print(f"  Policy created: {p}")
        p2 = TransportPolicy(kind=TransportKind.CURL_CFFI, tls_profile="chrome136")
        print(f"  JA3 Policy: {p2}")
        print(f"  POLICY_CLEARNET_H2: {POLICY_CLEARNET_H2}")
        print(f"  POLICY_STEALTH_CHROME: {POLICY_STEALTH_CHROME}")
        print("[SMOKE] TransportRuntime OK")
        await close_all_transports()

    asyncio.run(_smoke())
