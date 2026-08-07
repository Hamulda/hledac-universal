"""
HTTPX Transport Routing — Transport Capability Layer 2026
=======================================================


Sprint F206AF: HTTPX/H2 Auto-Fallback to curl_cffi

Provides:
  - should_use_httpx_h2(): URL classification for HTTPX H2 lane
  - fetch_via_httpx_h2(): execute fetch via HTTPX (if enabled)
  - classify_httpx_h2_error(): classify httpx exceptions into error types

F206AF INVARIANTS:
  [H2-A1] Failure counter bounded: max 3 per-process before auto-disable
  [H2-A2] httpx_h2 never used for Tor/I2P/Freenet/JS/stealth
  [H2-A3] Fallback is one-shot per URL (no infinite loops)
  [H2-A4] transport_fallback_reason set on fallback (additive, never overwrites)
  [H2-A5] CancelledError re-raised (not caught by error classifier)
  [H2-A6] Auto-disable gates: disabled after 3 failures in current process
"""



import asyncio
import functools
import ipaddress
import logging
import os
import re
import socket
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx  # used only in annotations — actual import is lazy inside fetch_via_httpx_h2

from ..utils.async_helpers import async_getaddrinfo
from hledac.universal.core.env_config import ENV  # noqa: E402

logger = logging.getLogger(__name__)

# =============================================================================
# HTTPX H2 Circuit Breaker — instance-based (S-02 fix)
# =============================================================================

_MAX_HTTPX_H2_FAILURES: int = 3       # [H2-A1] bounded

class H2CircuitBreaker:
    """Per-instance httpx H2 circuit breaker state.

    Tracks failure count and auto-disable per broker instance.
    Default singleton `_default_breaker` provides backward-compatible
    global-state behavior for existing callers.
    """

    __slots__ = ("_auto_disabled", "_failure_count")

    def __init__(self) -> None:
        self._auto_disabled: bool = False
        self._failure_count: int = 0

    @property
    def is_auto_disabled(self) -> bool:
        return self._auto_disabled

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def record_failure(self) -> None:
        """Record a failure; auto-disable after MAX_FAILURES."""
        if self._auto_disabled:
            return
        self._failure_count += 1
        if self._failure_count >= _MAX_HTTPX_H2_FAILURES:
            self._auto_disabled = True
            logger.warning(
                f"[HTTPX] httpx_h2 auto-disabled after {self._failure_count} failures "
                f"(threshold={_MAX_HTTPX_H2_FAILURES})"
            )

    def reset(self) -> None:
        """Reset state — for tests only."""
        self._auto_disabled = False
        self._failure_count = 0


# Default singleton (backward-compatible global state)
_default_breaker = H2CircuitBreaker()


# Exposed for tests — not for general public use
def get_httpx_h2_auto_disable() -> bool:
    return _default_breaker.is_auto_disabled


def get_httpx_h2_failure_count() -> int:
    return _default_breaker.failure_count


def reset_httpx_h2_state() -> None:
    """Reset httpx_h2 failure counter and auto-disable flag. For tests only."""
    _default_breaker.reset()
    logger.debug("[HTTPX] httpx_h2 state reset (failures=0, auto-disable=False)")


def record_httpx_h2_failure(_breaker: H2CircuitBreaker | None = None) -> None:
    """
    Record a httpx_h2 failure and auto-disable if threshold reached.

    Args:
        _breaker: Optional circuit breaker instance. Defaults to module singleton.
                  Pass a dedicated instance to isolate state per FetchCoordinator.
    """
    breaker = _breaker if _breaker is not None else _default_breaker
    breaker.record_failure()


def classify_httpx_h2_error(exc_or_result) -> str:
    """
    Classify httpx_h2 failure into error category.

    CancelledError is NOT classified — it MUST be re-raised by caller.

    Args:
        exc_or_result: exception instance, or "httpx_response" dict with error field

    Returns:
        Error type string from: none | connect_timeout | read_timeout | tls_error |
        protocol_error | remote_protocol_error | too_many_connections | pool_timeout |
        http_403 | http_429 | http_5xx | empty_body | content_type_rejected |
        unknown_httpx_error

    Invariants:
      [H2-A5] CancelledError NOT in return list — caller must re-raise
    """

    # Handle CancelledError — MUST be re-raised, not classified
    if isinstance(exc_or_result, asyncio.CancelledError):
        raise exc_or_result  # [H2-A5]

    exc = exc_or_result
    exc_name = exc.__class__.__name__ if hasattr(exc, "__class__") else ""

    # Check for specific httpx exception types first (before generic TimeoutError)
    # PoolTimeout (specific httpx exception)
    if exc_name == "PoolTimeout":
        return "pool_timeout"
    # ReadTimeout (specific httpx exception)
    if exc_name == "ReadTimeout":
        return "read_timeout"
    # ConnectTimeout
    if exc_name == "ConnectTimeout":
        return "connect_timeout"
    # RemoteProtocolError
    if exc_name == "RemoteProtocolError":
        return "remote_protocol_error"
    # TooManyConnectionsError
    if exc_name == "TooManyConnectionsError":
        return "too_many_connections"

    # Generic TimeoutError / asyncio.TimeoutError
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "connect_timeout"

    # TLS errors
    if "TLS" in exc_name or "SSL" in exc_name or "SSLError" in exc_name or "TLSProtocol" in exc_name:
        return "tls_error"

    # HTTP status codes from response
    if hasattr(exc, "response") and exc.response is not None:
        status = getattr(exc.response, "status_code", 0)
        if status == 403:
            return "http_403"
        if status == 429:
            return "http_429"
        if status >= 500:
            return "http_5xx"

    # Protocol errors (invalid response format, malformed)
    if "ProtocolError" in exc_name or "InvalidURL" in exc_name or "SerializationError" in exc_name:
        return "protocol_error"

    # Unknown httpx error
    if "httpx" in exc.__class__.__module__:
        return "unknown_httpx_error"

    return "unknown_httpx_error"

# =============================================================================
# URL Classification Helpers
# =============================================================================

# API-like URL patterns — suggest same-host batch or structured API calls
_API_URL_PATTERNS: list[re.Pattern] = [
    re.compile(r"^https?://cdn\."),                                    # CDN hosts (cdn.*)
    re.compile(r"^https?://static\."),                                 # static hosts
    re.compile(r"^https?://[^/]+\.workers\.dev"),                    # Cloudflare Workers subdomain
    re.compile(r"^https?://[^/]+\.on\.microsoft\.com"),               # Azure Front Door
]
_API_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"^https?://[^/]+/api/v\d+/"),                         # /api/v1/, /api/v2/
    re.compile(r"^https?://[^/]+/api/"),                               # /api/ (exact)
    re.compile(r"^https?://[^/]+/v\d+/api/"),                         # /v1/api/, /v2/
]

# Known API host suffixes that benefit from HTTP/2 multiplexing
_KNOWN_API_HOST_SUFFIXES: frozenset[str] = frozenset({
    "cloudflare.com",
    "akamai.com",
    "fastly.com",
    "cloudfront.net",
    "workers.dev",
    "azureedge.net",
    "azure.com",
    "digitaloceanspaces.com",
    "linode.com",
    "vultr.com",
})


@functools.lru_cache(maxsize=2048)
def _is_api_like_url(url: str) -> bool:
    """
    Return True if URL looks like an API/CDN/structured endpoint.

    Uses lightweight regex — no network calls.
    Deterministic: same URL always returns same result.

    F206K: API-like URLs are candidates for HTTPX H2 lane because:
      - Multiple requests to same host benefit from HTTP/2 multiplexing
      - Structured endpoints don't need JA3 fingerprint spoofing
      - Same-host batch patterns (CT logs, CDX, passive DNS) are common
    """
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""

        # Check hostname patterns
        if not host:
            return False

        # Known API CDN host suffixes
        for suffix in _KNOWN_API_HOST_SUFFIXES:
            if host.endswith(suffix):
                return True

        # Check if hostname starts with api. (e.g., api.github.com, api.twitter.com)
        if host.startswith("api."):
            return True

        # Check hostname-based URL patterns (cdn., static., .workers.dev, etc.)
        for pattern in _API_URL_PATTERNS:
            if pattern.match(url):
                return True

        # Check path-based API patterns (/api/, /api/v1/, /v1/api/)
        for pattern in _API_PATH_PATTERNS:
            if pattern.match(url):
                return True

        return False
    except Exception:
        return False


def _extract_host(url: str) -> str:
    """Extract lowercase hostname from URL. Returns '' on parse failure.

    F271: Uses Rust url_ops.extract_host() when available (fast path),
    falls back to urllib.parse on ImportError.
    """
    try:
        from hledac.universal.fetching.public_fetcher import url_ops

        return url_ops.extract_host(url)
    except Exception:  # noqa: BLE001
        pass
    # Fallback: urllib.parse
    try:
        netloc = urllib.parse.urlparse(url).netloc
        # Remove port
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc.lower()
    except Exception:
        return ""


# =============================================================================
# Transport Selection Policy
# =============================================================================


def should_use_httpx_h2(
    url: str,
    use_stealth: bool = False,
    use_js: bool = False,
    _breaker: H2CircuitBreaker | None = None,
) -> tuple[bool, str]:
    """
    Determine if URL should use HTTPX H2 lane.

    HTTPX H2 is only selected when ALL of:
      1. URL is clearnet (not .onion/.i2p/.b32.i2p/.freenet)
      2. use_stealth is False (stealth uses aiohttp/StealthSession)
      3. use_js is False (JS rendering uses Camoufox/nodriver)
      4. URL is API-like OR same-host pattern detected

    Args:
        url: Target URL
        use_stealth: Stealth mode flag (from async_fetch_public_text)
        use_js: JS rendering flag (from async_fetch_public_text)
        _breaker: Optional circuit breaker instance. Defaults to module singleton.
        use_js: JS rendering flag (from async_fetch_public_text)

    Returns:
        Tuple of (should_use_httpx: bool, reason: str)
        reason values:
          - "api_like" — URL matches API/CDN pattern
          - "same_host_candidate" — URL may benefit from HTTP/2 multiplexing
          - "stealth_required" — stealth mode active, HTTPX not allowed
          - "js_required" — JS rendering active, HTTPX not allowed
          - "darknet_url" — Tor/I2P/Freenet URL, HTTPX not allowed
          - "httpx_h2_disabled" — h2 not installed, fallback to aiohttp

    Invariants:
      [H2-P1] Tor/I2P/Freenet URLs NEVER select HTTPX H2
      [H2-P2] use_stealth=True NEVER selects HTTPX H2
      [H2-P3] use_js=True NEVER selects HTTPX H2
      [H2-P4] HTTPX H2 requires h2 to be installed
    """
    from .httpx_client import is_httpx_h2_enabled

    # P4: Env gate — HLEDAC_ENABLE_HTTPX_H2 must be set (default: disabled)
    if not ENV.get_bool("HLEDAC_ENABLE_HTTPX_H2"):
        return False, "httpx_h2_disabled_env"

    # F206AF: Auto-disable check — after 3 failures, disable for rest of process
    breaker = _breaker if _breaker is not None else _default_breaker
    if breaker.is_auto_disabled:
        return False, "httpx_h2_auto_disabled"

    # P3: Darknet URLs — route via curl_cffi (SOCKS5H via CURL_CFFI_PROXY env)
    # F3XX: Tor/I2P now use httpx-socks in connection_pool_manager.py
    host = _extract_host(url)
    if host.endswith(".onion"):
        return False, "darknet_url"
    if host.endswith(".i2p") or host.endswith(".b32.i2p"):
        return False, "darknet_url"
    if host.endswith(".freenet"):
        return False, "freenet_not_httpx_supported"

    # P2: Stealth mode — uses aiohttp + StealthSession
    if use_stealth:
        return False, "stealth_required"

    # P3: JS rendering — uses Camoufox/nodriver
    if use_js:
        return False, "js_required"

    # P4: Check h2 availability (only reached when env is enabled and not auto-disabled)
    if not is_httpx_h2_enabled():
        return False, "httpx_h2_disabled"

    # P1: API-like or same-host candidate
    if _is_api_like_url(url):
        return True, "api_like"

    # Default: use aiohttp for random web crawl
    return False, "clearnet_default"


# =============================================================================
# HTTPX H2 Fetch Path
# =============================================================================


async def fetch_via_httpx_h2(
    url: str,
    timeout_s: float = 20.0,
    _max_bytes: int = 2 * 1024 * 1024,  # noqa: F841  # reserved; body cap deferred — see TRANSPORT_COMMON_POLICY_AUDIT.md
    _max_redirects: int = 10,  # noqa: F841  # kept for API compat; limit set on client via limits.max_keepalive_connections
) -> httpx.Response:  # TYPE_CHECKING-only import at top; lazy real import below
    """
    Execute HTTP GET via HTTPX AsyncClient (HTTP/2 capable).

    This is the H2 lane — used for API-like and same-host batch URLs.

    Args:
        url: Target URL
        timeout_s: Per-request timeout in seconds
        max_bytes: Maximum response bytes to buffer
        max_redirects: Maximum number of redirects to follow

    Returns:
        httpx.Response — caller must check status and read body

    Raises:
        RuntimeError: if HTTPX H2 client not available
        asyncio.CancelledError: propagates (not swallowed)

    NOTE: This function returns the raw httpx.Response.
    Callers are responsible for:
      - Content-Type validation
      - Body reading with size cap
      - Error mapping to FetchResult fields
    """
    from .httpx_client import async_get_httpx_client

    client = await async_get_httpx_client()

    # SEC-08: Standardized browser-like headers to avoid client fingerprinting
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",  # noqa: E501
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

    # OPSEC-002: SSRF pre-check for initial URL
    await _validate_redirect_url(url)

    # OPSEC-002: Manual redirect handling with SSRF validation for each redirect
    # (follow_redirects=True doesn't validate redirect targets)
    current_url = url
    last_response: httpx.Response | None = None

    for _redirect_count in range(_max_redirects):
        response = await client.get(
            current_url,
            headers=headers,
            timeout=timeout_s,
            follow_redirects=False,  # Manual handling required for SSRF validation
        )
        last_response = response

        # Check for redirect status codes
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location")
            if not location:
                break  # Redirect without location header

            # Resolve relative URLs
            if not location.startswith(("http://", "https://")):
                parsed = urllib.parse.urlparse(current_url)
                if location.startswith("/"):
                    location = f"{parsed.scheme}://{parsed.netloc}{location}"
                else:
                    location = f"{parsed.scheme}://{parsed.netloc}/{location}"

            # OPSEC-002: Validate redirect target BEFORE following
            await _validate_redirect_url(location)
            current_url = location
        else:
            break  # Not a redirect, return response

    if last_response is None:
        # Should never happen, but satisfies type checker
        raise RuntimeError("No response received during fetch")

    return last_response


class _SSRFBlockError(Exception):
    """Raised when redirect URL fails SSRF validation."""
    pass


# OPSEC-002: SSRF protection — comprehensive private network ranges
# IPv4: RFC 1918 + link-local + loopback + benchmark + reserved
# IPv6: RFC 4193 (ULA) + RFC 4291 link-local + loopback + Teredo
_PRIVATE_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (  # noqa: N806
    ipaddress.ip_network("10.0.0.0/8"),       # RFC 1918: private
    ipaddress.ip_network("172.16.0.0/12"),    # RFC 1918: private
    ipaddress.ip_network("192.168.0.0/16"),   # RFC 1918: private
    ipaddress.ip_network("127.0.0.0/8"),      # RFC 1122: loopback
    ipaddress.ip_network("169.254.0.0/16"),   # RFC 3927: link-local
    ipaddress.ip_network("100.64.0.0/10"),    # RFC 6598: CG-NAT
    ipaddress.ip_network("0.0.0.0/8"),        # RFC 1122: "this" network
    ipaddress.ip_network("224.0.0.0/4"),     # RFC 1112: multicast
    ipaddress.ip_network("240.0.0.0/4"),     # RFC 1112: reserved
    # IPv6
    ipaddress.ip_network("::1/128"),          # RFC 4291: loopback
    ipaddress.ip_network("fc00::/7"),         # RFC 4193: unique local (fc00::/8 + fd00::/8)
    ipaddress.ip_network("fe80::/10"),        # RFC 4291: link-local unicast
    ipaddress.ip_network("2001::/32"),        # RFC 4380: Teredo tunnel
    ipaddress.ip_network("ff00::/8"),         # RFC 4291: multicast
)


def _is_ssrf_safe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """
    Check if IP is safe (not private/reserved/loopback).

    Returns True if IP is public and safe to connect to.
    """
    # Fast path: check built-in flags first
    if ip.is_loopback:
        return False
    if ip.is_multicast:
        return False
    if ip.is_unspecified:
        return False
    # Check against private network ranges
    for net in _PRIVATE_NETS:
        if ip in net:
            return False
    return True


async def _validate_redirect_url(redirect_url: str) -> None:
    """
    Validate redirect URL is safe (no private IPs, no data: URIs, etc.).

    Performs DNS resolution for domain names to detect DNS rebinding attacks.
    Comprehensive: covers IPv4, IPv6, DNS rebinding, and unsafe schemes.

    Raises:
        _SSRFBlockError: if redirect target is unsafe
    """
    parsed = urllib.parse.urlparse(redirect_url)

    # Block data: and javascript: URIs (no network access)
    if parsed.scheme.lower() in ("data", "javascript", "vbscript"):
        raise _SSRFBlockError(f"Unsafe redirect scheme blocked: {redirect_url}")

    hostname = parsed.hostname
    if not hostname:
        raise _SSRFBlockError(f"No hostname in redirect URL: {redirect_url}")

    # Check if hostname is a literal IP (IPv4 or IPv6)
    try:
        ip = ipaddress.ip_address(hostname)
        if not _is_ssrf_safe_ip(ip):
            raise _SSRFBlockError(f"Redirect to private/reserved IP blocked: {redirect_url}")
        # Literal IP is valid and safe — no DNS resolution needed
        return
    except ValueError:  # noqa: BLE001
        pass  # Not a literal IP, must be domain — resolve DNS below

    # Resolve DNS and check all resolved IPs for private ranges
    # This prevents DNS rebinding attacks where a domain initially resolves
    # to a public IP but later redirects to a private IP
    try:
        raw_results = await async_getaddrinfo(hostname, 0, proto=socket.IPPROTO_TCP)
        resolved_ips = sorted({str(r[4][0]) for r in raw_results})
        if not resolved_ips:
            raise _SSRFBlockError(f"DNS resolution failed for redirect URL: {redirect_url}")

        for ip_str in resolved_ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if not _is_ssrf_safe_ip(ip_obj):
                    raise _SSRFBlockError(
                        f"Redirect to private/reserved IP via DNS rebinding blocked: {redirect_url} "
                        f"(resolved to {ip_str})"
                    )
            except ValueError:  # noqa: BLE001
                pass  # Not an IP format, skip (shouldn't happen from getaddrinfo)
    except _SSRFBlockError:
        raise
    except Exception as exc:
        # Fail-safe: block on any resolution error
        raise _SSRFBlockError(f"DNS resolution error for redirect URL: {redirect_url}: {exc}") from exc


__all__ = [
    "should_use_httpx_h2",
    "fetch_via_httpx_h2",
    "classify_httpx_h2_error",
    "_is_api_like_url",
    # F206AF state management (exposed for tests)
    "get_httpx_h2_auto_disable",
    "get_httpx_h2_failure_count",
    "reset_httpx_h2_state",
]
