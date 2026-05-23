"""
Transport Router — Canonical Lane Selection Policy
==================================================

Sprint F206AR: Transport authority unification.

ROLE:
  TransportRouter is a stateless decision engine. It decides WHICH lane to use
  for a given fetch operation. It does NOT perform network I/O.

LANES:
  - aiohttp_default    — plain aiohttp, for general clearnet
  - httpx_h2           — HTTPX with HTTP/2, env-gated, API-like URLs only
  - curl_cffi_stealth  — JA3 fingerprint spoofing, for stealth/403/429 retry
  - tor_socks          — Tor SOCKS5 proxy, .onion domains
  - i2p_socks          — I2P SOCKS5 proxy, .i2p/.b32.i2p domains
  - js_renderer        — Camoufox/nodriver for JS-rendered pages
  - cache_safe_http    — Hishel lane (disabled: no dependency, not implemented)

DECISION RULES (in priority order):
  1. .onion / .onion/  → tor_socks
  2. .i2p / .b32.i2p   → i2p_socks
  3. use_js=True       → js_renderer
  4. use_stealth=True  → curl_cffi_stealth
  5. status 403/429    → curl_cffi_stealth (retry path)
  6. API-like URL + HLEDAC_ENABLE_HTTPX_H2=1 + h2 available → httpx_h2
  7. default           → aiohttp_default

CACHE RULE:
  cache_allowed=True ONLY when cache_safe=True AND lane is NOT
  (pastebin | breach | volatile | anonymous). Default: False.

INVARIANTS:
  [TR-1] Router is pure function — no I/O, no state mutation
  [TR-2] httpx_h2 NEVER selected for onion/i2p/freenet/stealth/js
  [TR-3] tor_socks/i2p_socks NEVER selected for plain clearnet
  [TR-4] cache_safe_http lane is always disabled (no Hishel dependency)
  [TR-5] selected_transport is the internal lane name, not the transport class
  [TR-6] CancelledError is NOT handled — caller must re-raise
"""

from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

# =============================================================================
# Lane Literals
# =============================================================================

Lane = Literal[
    "aiohttp_default",
    "httpx_h2",
    "curl_cffi_stealth",
    "tor_socks",
    "i2p_socks",
    "js_renderer",
    "cache_safe_http",
    "gopher",
]


# =============================================================================
# Router Output Dataclass
# =============================================================================

@dataclass(frozen=True)
class TransportDecision:
    """
    Output of TransportRouter.route().

    Fields:
      lane               — which lane to use
      reason             — human-readable why this lane was chosen
      cache_allowed      — True only for explicit cache_safe=True on safe URLs
      selected_transport — internal transport identifier for telemetry
      max_bytes          — response size cap (0 = no cap at router level)
      timeout_s          — suggested timeout in seconds (0 = use transport default)
      concurrency_class  — "low" | "medium" | "high" — for concurrency control
    """

    lane: Lane
    reason: str
    cache_allowed: bool = False
    selected_transport: str = ""
    max_bytes: int = 0       # 0 = delegate to transport layer
    timeout_s: float = 0.0   # 0.0 = delegate to transport layer
    concurrency_class: str = "medium"

    def __post_init__(self) -> None:
        # Resolve selected_transport from lane if not explicitly set
        if not self.selected_transport:
            object.__setattr__(self, "selected_transport", self.lane)


# =============================================================================
# Router
# =============================================================================

class TransportRouter:
    """
    Stateless lane selection policy.

    Decision is based on URL characteristics and runtime flags only.
    No network calls, no state mutation.
    """

    __slots__ = ()

    # Darknet TLDs that must route through anonymity networks
    _DARKNET_SUFFIXES: tuple[str, ...] = (".onion", ".i2p", ".b32.i2p", ".freenet")

    # API-like URL patterns suggesting HTTP/2 multiplexing benefit
    _API_PATH_PATTERNS: tuple[str, ...] = (
        r"^https?://[^/]+/api/v\d+/",
        r"^https?://[^/]+/api/",
        r"^https?://[^/]+/v\d+/api/",
    )
    _API_HOST_PREFIXES: tuple[str, ...] = ("api.",)
    _API_HOST_SUFFIXES: tuple[str, ...] = (
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
    )

    def route(
        self,
        url: str,
        *,
        use_stealth: bool = False,
        use_js: bool = False,
        cache_safe: bool = False,
        retry_after_status: int | None = None,
        # Passthrough fields from caller
        suggested_timeout_s: float = 0.0,
        suggested_max_bytes: int = 0,
        suggested_concurrency: str | None = None,
    ) -> TransportDecision:
        """
        Select the appropriate transport lane for a URL.

        Args:
            url:                  Target URL
            use_stealth:          Stealth mode (JA3 spoofing required)
            use_js:               JS rendering required (Camoufox/nodriver)
            cache_safe:           URL is safe to cache (never True for volatile sources)
            retry_after_status:   HTTP status of a prior attempt (403/429 → curl_cffi)
            suggested_timeout_s:  Caller-suggested timeout (router may keep/override)
            suggested_max_bytes:  Caller-suggested body cap (router may keep/override)
            suggested_concurrency: Caller-suggested concurrency class

        Returns:
            TransportDecision with lane, reason, and passthrough fields

        Priority order:
          1. .onion → tor_socks
          2. .i2p/.b32.i2p → i2p_socks
          3. use_js=True → js_renderer
          4. use_stealth=True → curl_cffi_stealth
          5. retry_after_status in (403, 429) → curl_cffi_stealth
          6. API-like + HLEDAC_ENABLE_HTTPX_H2=1 + h2 available → httpx_h2
          7. default → aiohttp_default
        """
        hostname = self._extract_host(url)

        # 1. Darknet: .onion → tor_socks
        if hostname.endswith(".onion"):
            return TransportDecision(
                lane="tor_socks",
                reason="darknet_onion",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "low",
            )

        # 2. Darknet: .i2p/.b32.i2p → i2p_socks
        if hostname.endswith(".i2p") or hostname.endswith(".b32.i2p"):
            return TransportDecision(
                lane="i2p_socks",
                reason="darknet_i2p",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "low",
            )

        # Gopher protocol — before Freenet since gopher:// has no hostname suffix
        if url.startswith("gopher://"):
            return TransportDecision(
                lane="gopher",
                reason="gopher_protocol",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "low",
            )

        # Freenet — not supported, fall through to default
        if hostname.endswith(".freenet"):
            return TransportDecision(
                lane="aiohttp_default",
                reason="freenet_not_supported",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "medium",
            )

        # 3. JS rendering → js_renderer
        if use_js:
            return TransportDecision(
                lane="js_renderer",
                reason="js_required",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "low",
            )

        # 4. Stealth → curl_cffi_stealth
        if use_stealth:
            return TransportDecision(
                lane="curl_cffi_stealth",
                reason="explicit_stealth",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "medium",
            )

        # 5. Retry after 403/429 → curl_cffi_stealth (escalation)
        if retry_after_status in (403, 429):
            return TransportDecision(
                lane="curl_cffi_stealth",
                reason=f"retry_after_http_{retry_after_status}",
                cache_allowed=False,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "medium",
            )

        # 6. HTTPX H2 — env-gated, API-like, h2 available
        if self._is_httpx_h2_candidate(url):
            return TransportDecision(
                lane="httpx_h2",
                reason="api_like_httpx_h2",
                cache_allowed=cache_safe,
                max_bytes=suggested_max_bytes or 0,
                timeout_s=suggested_timeout_s or 0.0,
                concurrency_class=suggested_concurrency or "high",
            )

        # 7. Default → aiohttp_default
        return TransportDecision(
            lane="aiohttp_default",
            reason="clearnet_default",
            cache_allowed=False,
            max_bytes=suggested_max_bytes or 0,
            timeout_s=suggested_timeout_s or 0.0,
            concurrency_class=suggested_concurrency or "medium",
        )

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _extract_host(url: str) -> str:
        """Extract lowercase hostname from URL. Returns '' on parse failure."""
        try:
            netloc = urllib.parse.urlparse(url).netloc
            if ":" in netloc:
                netloc = netloc.split(":")[0]
            return netloc.lower()
        except Exception:
            return ""

    def _is_httpx_h2_candidate(self, url: str) -> bool:
        """
        Return True if URL is a candidate for HTTPX H2 lane.

        Requires ALL of:
          - HLEDAC_ENABLE_HTTPX_H2=1 (env gate)
          - h2 library installed (checked at call site via httpx_client.is_httpx_h2_enabled)
          - URL is API-like (path or host pattern matches)
          - Hostname is clearnet (checked before this call)
        """
        # Env gate
        env_val = os.environ.get("HLEDAC_ENABLE_HTTPX_H2", "").strip().lower()
        if not env_val or env_val in ("0", "false", "no", "off"):
            return False

        hostname = self._extract_host(url)
        if not hostname:
            return False

        # Check hostname-based API patterns
        for suffix in self._API_HOST_SUFFIXES:
            if hostname.endswith(suffix):
                return True

        if hostname.startswith(self._API_HOST_PREFIXES):
            return True

        # Check path-based API patterns
        try:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path
            for pattern in self._API_PATH_PATTERNS:
                import re
                if re.match(pattern, f"{parsed.scheme}://{hostname}{path}"):
                    return True
        except Exception:
            pass

        return False


# -------------------------------------------------------------------------
# Singleton
# -------------------------------------------------------------------------

_router = TransportRouter()


def route_transport(
    url: str,
    *,
    use_stealth: bool = False,
    use_js: bool = False,
    cache_safe: bool = False,
    retry_after_status: int | None = None,
    suggested_timeout_s: float = 0.0,
    suggested_max_bytes: int = 0,
    suggested_concurrency: str | None = None,
) -> TransportDecision:
    """
    Singleton route() call — delegates to TransportRouter.

    Convenience function matching the decision-engine interface used by
    FetchCoordinator and other canonical fetch entry points.
    """
    return _router.route(
        url,
        use_stealth=use_stealth,
        use_js=use_js,
        cache_safe=cache_safe,
        retry_after_status=retry_after_status,
        suggested_timeout_s=suggested_timeout_s,
        suggested_max_bytes=suggested_max_bytes,
        suggested_concurrency=suggested_concurrency,
    )


_I2P_TRANSPORT_SINGLETON: Any = None


def set_i2p_transport_singleton(transport: Any) -> None:
    """F250: Register I2PTransport singleton so all consumers share one session."""
    global _I2P_TRANSPORT_SINGLETON
    _I2P_TRANSPORT_SINGLETON = transport


def get_i2p_transport_singleton() -> Any:
    """F250: Return registered I2PTransport singleton, or None."""
    return _I2P_TRANSPORT_SINGLETON


__all__ = [
    "TransportRouter",
    "TransportDecision",
    "Lane",
    "route_transport",
]