"""
HTTPX Transport Routing — Transport Capability Layer 2026
=======================================================

Sprint F206K: Per-request HTTPX H2 lane routing.

Provides:
  - _should_use_httpx_h2(): URL classification for HTTPX H2 lane
  - _route_via_httpx_h2(): execute fetch via HTTPX (if enabled)

AUTHORITY (F206K):
  HTTPX H2 is an OPTIONAL clearnet capability lane.
  It is NEVER used for:
    - Tor (.onion)
    - I2P (.i2p, .b32.i2p)
    - Freenet (.freenet)
    - JS rendering mode
    - Stealth mode
    - Any non-clearnet URL

  Default hot-path remains aiohttp for all clearnet random web crawl.
  HTTPX H2 is activated ONLY for same-host batch, API endpoints,
  and explicit allowlist candidates.

TRANSPORT ROUTING TRUTH TABLE (F206K):
  URL Type                          | Lane        | Transport
  ----------------------------------+-------------+------------------
  random clearnet HTML              | aiohttp     | TCPConnector
  same-host/API clearnet            | httpx_h2    | HTTP/2
  CT/CDX/API endpoint              | httpx_h2    | HTTP/2
  .onion                           | aiohttp_socks | ProxyConnector
  .i2p / .b32.i2p                 | aiohttp_socks | ProxyConnector
  .freenet                         | aiohttp     | HTTP proxy
  use_js=True                      | aiohttp     | TCPConnector
  use_stealth=True                 | aiohttp     | StealthSession

FAIL-SOFT BEHAVIOR:
  If HTTPX H2 is selected but h2 is not installed:
    → fall back to aiohttp (not a hard error)
    → transport_fallback_reason set
"""

from __future__ import annotations

import logging
import re
import urllib.parse

logger = logging.getLogger(__name__)

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
        path = parsed.path

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
    """Extract lowercase hostname from URL. Returns '' on parse failure."""
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

    # P3: Darknet URLs — route via aiohttp_socks
    host = _extract_host(url)
    if host.endswith(".onion"):
        return False, "darknet_url"
    if host.endswith(".i2p") or host.endswith(".b32.i2p"):
        return False, "darknet_url"
    if host.endswith(".freenet"):
        return False, "darknet_url"

    # P2: Stealth mode — uses aiohttp + StealthSession
    if use_stealth:
        return False, "stealth_required"

    # P3: JS rendering — uses Camoufox/nodriver
    if use_js:
        return False, "js_required"

    # P4: Check h2 availability
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
    _max_bytes: int = 2 * 1024 * 1024,  # reserved for future size enforcement
) -> "httpx.Response":  # type: ignore[name-defined]  # httpx imported lazily inside
    """
    Execute HTTP GET via HTTPX AsyncClient (HTTP/2 capable).

    This is the H2 lane — used for API-like and same-host batch URLs.

    Args:
        url: Target URL
        timeout_s: Per-request timeout in seconds
        max_bytes: Maximum response bytes to buffer

    Returns:
        httpx.Response — caller must check status and read body

    Raises:
        RuntimeError: if HTTPX H2 client not available
        asyncio.CancelledError: propagates (not swallowed)

    NOTE: This function returns the raw httpx.Response.
    Callers are responsible for:
      - Content-Type validation
      - Body reading with size cap
      - Redirect handling
      - Error mapping to FetchResult fields
    """
    from .httpx_client import async_get_httpx_client
    import asyncio

    client = await async_get_httpx_client()

    # Build request headers (minimal — no UA rotation for API calls)
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }

    try:
        response = await client.get(
            url,
            headers=headers,
            timeout=timeout_s,
            follow_redirects=True,
        )
        return response
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"[HTTPX] fetch error for {url}: {e}")
        raise


__all__ = [
    "should_use_httpx_h2",
    "fetch_via_httpx_h2",
    "_is_api_like_url",
]
