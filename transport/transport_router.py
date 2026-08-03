"""
Transport Router — Canonical Lane Selection Policy
==================================================

Sprint F206AR: Transport authority unification.

ROLE:
  TransportRouter is a stateless decision engine. It decides WHICH lane to use
  for a given fetch operation. It does NOT perform network I/O.

LANES:
  - aiohttp_default    — plain aiohttp, for general clearnet
  - nw_connection      — Apple Network.framework, user-space TCP + hw TLS (SILICON-03)
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
  6. nw_connection available + non-dark clearnet → nw_connection (SILICON-03)
  7. API-like URL + HLEDAC_ENABLE_HTTPX_H2=1 + h2 available → httpx_h2
  8. API-like URL + HLEDAC_ENABLE_HTTPX_H3=1 + h3 available → httpx_h3
  9. default           → aiohttp_default

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
import contextvars
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Literal

from hledac.universal.core.env_config import ENV

Lane = Literal['aiohttp_default', 'nw_connection', 'httpx_h2', 'httpx_h3', 'curl_cffi_stealth', 'tor_socks', 'i2p_socks', 'js_renderer', 'cache_safe_http', 'gopher']

@dataclass(frozen=True, slots=True)
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
      url_kind          — pre-classified kind: "onion"|"i2p"|"freenet"|"clearnet"|"malformed"|""
    """
    lane: Lane
    reason: str
    cache_allowed: bool = False
    selected_transport: str = ''
    max_bytes: int = 0
    timeout_s: float = 0.0
    concurrency_class: str = 'medium'
    url_kind: str = ''

    def __post_init__(self) -> None:
        if not self.selected_transport:
            object.__setattr__(self, 'selected_transport', self.lane)

class TransportRouter:
    """
    Stateless lane selection policy.

    Decision is based on URL characteristics and runtime flags only.
    No network calls, no state mutation.
    """
    __slots__ = ()
    _DARKNET_SUFFIXES: tuple[str, ...] = ('.onion', '.i2p', '.b32.i2p', '.freenet')
    _API_PATH_PATTERNS: tuple[str, ...] = ('^https?://[^/]+/api/v\\d+/', '^https?://[^/]+/api/', '^https?://[^/]+/v\\d+/api/')
    _API_HOST_PREFIXES: tuple[str, ...] = ('api.',)
    _API_HOST_SUFFIXES: tuple[str, ...] = ('cloudflare.com', 'akamai.com', 'fastly.com', 'cloudfront.net', 'workers.dev', 'azureedge.net', 'azure.com', 'digitaloceanspaces.com', 'linode.com', 'vultr.com')

    def route(self, url: str, *, use_stealth: bool=False, use_js: bool=False, cache_safe: bool=False, retry_after_status: int | None=None, suggested_timeout_s: float=0.0, suggested_max_bytes: int=0, suggested_concurrency: str | None=None, preclassified_kind: str | None=None, preclassified_host: str | None=None) -> TransportDecision:
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
            preclassified_kind:   Optional pre-classified kind ("onion"/"i2p"/"freenet"/"clearnet"/"malformed")
            preclassified_host:   Optional pre-classified lowercase host (required if kind is provided)

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
        if preclassified_kind is not None and preclassified_host is not None:
            hostname = preclassified_host
            kind = preclassified_kind
        else:
            kind, hostname = self._classify_url(url)

        def _d(lane: Lane, reason: str, concurrency: str, cache: bool=False) -> TransportDecision:
            return TransportDecision(lane=lane, reason=reason, cache_allowed=cache, max_bytes=suggested_max_bytes or 0, timeout_s=suggested_timeout_s or 0.0, concurrency_class=suggested_concurrency or concurrency, url_kind=kind)
        if kind == 'onion' or hostname.endswith('.onion'):
            return _d('tor_socks', 'darknet_onion', 'low')
        if kind == 'i2p' or hostname.endswith('.i2p') or hostname.endswith('.b32.i2p'):
            return _d('i2p_socks', 'darknet_i2p', 'low')
        if url.startswith('gopher://'):
            return _d('gopher', 'gopher_protocol', 'low')
        if kind == 'freenet' or hostname.endswith('.freenet'):
            return _d('aiohttp_default', 'freenet_not_supported', 'medium')
        if use_js:
            return _d('js_renderer', 'js_required', 'low')
        if use_stealth:
            return _d('curl_cffi_stealth', 'explicit_stealth', 'medium')
        if retry_after_status in (403, 429):
            return _d('curl_cffi_stealth', f'retry_after_http_{retry_after_status}', 'medium')
        if self._is_nw_connection_candidate(url, hostname):
            return _d('nw_connection', 'nw_framework_user_space_tcp', 'high', cache=cache_safe)
        if self._is_httpx_h2_candidate(url, hostname):
            return _d('httpx_h2', 'api_like_httpx_h2', 'high', cache=cache_safe)
        if self._is_httpx_h3_candidate(url, hostname):
            return _d('httpx_h3', 'api_like_httpx_h3', 'high', cache=cache_safe)
        return _d('aiohttp_default', 'clearnet_default', 'medium')

    def _classify_url(self, url: str) -> tuple[str, str]:
        """F271: Classify URL via Rust cache — single GIL transition.

        Delegates to public_fetcher._classify_url_cached so the cache is shared
        across callers (transport_router + public_fetcher). Never raises.
        """
        if url.startswith('gopher://'):
            return ('gopher', '')
        try:
            from hledac.universal.fetching.public_fetcher import _classify_url_cached
            return _classify_url_cached(url)
        except Exception:  # noqa: BLE001 — fail-soft: Rust cache miss → fallback to urllib
            pass
        try:
            parsed = urllib.parse.urlparse(url)
            host = (parsed.hostname or '').lower()
            if not host:
                return ('malformed', '')
            if host.endswith('.onion'):
                return ('onion', host)
            if host.endswith('.i2p') or host.endswith('.b32.i2p'):
                return ('i2p', host)
            if host.endswith('.freenet') or 'freenet' in host:
                return ('freenet', host)
            return ('clearnet', host)
        except (ValueError, OSError):  # urllib raises ValueError for malformed URLs; OSError for IDN encoding failures
            return ('malformed', '')

    def _is_nw_connection_candidate(self, url: str, hostname: str = "") -> bool:
        """
        Return True if URL is a candidate for the Network.framework lane.

        SILICON-03: Network.framework is the PREFERRED path for all
        non-stealth, non-dark-web clearnet traffic because it eliminates
        BSD socket kernel transitions and uses hardware-accelerated TLS.

        Preconditions:
          - Target is clearnet (not .onion/.i2p/.freenet — checked before)
          - HLEDAC_ENABLE_NW_CONNECTION is not set to 0
          - Platform is darwin (macOS — Network.framework is macOS-only)
          - Rust extension with nw_framework feature is importable

        Unlike H2/H3 which require API-like URL patterns, NW connection
        is suitable for ALL clearnet URLs — it's a general-purpose
        replacement for BSD socket networking.
        """
        if not ENV.get_bool("HLEDAC_ENABLE_NW_CONNECTION"):
            return False
        import sys
        if sys.platform != "darwin":
            return False
        # Lazy check: only probe the Rust extension if we pass the cheap gates
        if not hostname:
            hostname = self._classify_url(url)[1]
        if not hostname:
            return False
        # Dark web check — already done before this call, but double-check
        if hostname.endswith((".onion", ".i2p", ".b32.i2p", ".freenet")):
            return False
        try:
            from hledac.universal.transport.nw_connection_lane import is_nw_connection_available
            return is_nw_connection_available()
        except (ValueError, OSError):
            return False

    def _is_httpx_h2_candidate(self, url: str, hostname: str='') -> bool:
        """
        Return True if URL is a candidate for HTTPX H2 lane.

        Requires ALL of:
          - HLEDAC_ENABLE_HTTPX_H2=1 (env gate)
          - h2 library installed (checked at call site via httpx_client.is_httpx_h2_enabled)
          - URL is API-like (path or host pattern matches)
          - Hostname is clearnet (checked before this call)

        Args:
            url: Target URL
            hostname: Pre-extracted lowercase hostname (avoids redundant FFI in B1 path)
        """
        if not ENV.get_bool('HLEDAC_ENABLE_HTTPX_H2'):
            return False
        if not hostname:
            hostname = self._classify_url(url)[1]
        if not hostname:
            return False
        for suffix in self._API_HOST_SUFFIXES:
            if hostname.endswith(suffix):
                return True
        if hostname.startswith(self._API_HOST_PREFIXES):
            return True
        try:
            parsed = urllib.parse.urlparse(url)
            path = parsed.path
            for pattern in self._API_PATH_PATTERNS:
                if re.match(pattern, f'{parsed.scheme}://{hostname}{path}'):
                    return True
        except re.error:  # invalid regex pattern in _API_PATH_PATTERNS
            pass
        return False

    def _is_httpx_h3_candidate(self, url: str, hostname: str='') -> bool:
        """
        Return True if URL is a candidate for the HTTP/3 (QUIC) lane.

        P1-2: complements ``_is_httpx_h2_candidate``. H3 has TWO extra
        preconditions on top of H2:

          1. ``HLEDAC_ENABLE_HTTPX_H3=1`` env gate (parallels
             ``HLEDAC_ENABLE_HTTPX_H2``). Default off; the lane is
             opt-in because real QUIC carries operational cost and
             a small memory footprint (aioquic + cryptography).
          2. The host must have already advertised ``h3`` via Alt-Svc
             in a prior response. We read the shared LRU in
             ``http3_lane`` (bounded 512, 24h TTL) instead of probing
             on every call: H3 negotiation is opportunistic, and a
             router that ran a HEAD per URL would double the latency
             of the lane it's trying to promote.

        URL pattern is the same API-like set as H2. The actual H3
        handshake still happens inside the curl_cffi wrapper via
        ``http_version=HttpVersion.v3``; the router merely records
        the lane choice so telemetry reflects the intended transport.

        Args:
            url: Target URL
            hostname: Pre-extracted lowercase hostname (avoids redundant FFI in B1 path)
        """
        if not ENV.get_bool('HLEDAC_ENABLE_HTTPX_H3') and (not ENV.get_bool('HLEDAC_HTTP3')):
            return False
        if not hostname:
            hostname = self._classify_url(url)[1]
        if not hostname:
            return False
        try:
            from hledac.universal.transport.http3_lane import _cache_get as _h3_cache_get
            if _h3_cache_get(hostname) is not True:
                return False
        except (ValueError, OSError):  # cache lookup: ValueError for type errors, OSError for backend failures
            return False
        for suffix in self._API_HOST_SUFFIXES:
            if hostname.endswith(suffix):
                return True
        if hostname.startswith(self._API_HOST_PREFIXES):
            return True
        try:
            parsed = urllib.parse.urlparse(url)
            for pattern in self._API_PATH_PATTERNS:
                if re.match(pattern, f'{parsed.scheme}://{hostname}{parsed.path}'):
                    return True
        except re.error:  # invalid regex pattern in _API_PATH_PATTERNS
            pass
        return False
_router = TransportRouter()

def route_transport(url: str, *, use_stealth: bool=False, use_js: bool=False, cache_safe: bool=False, retry_after_status: int | None=None, suggested_timeout_s: float=0.0, suggested_max_bytes: int=0, suggested_concurrency: str | None=None, preclassified_kind: str | None=None, preclassified_host: str | None=None) -> TransportDecision:
    """
    Singleton route() call — delegates to TransportRouter.

    Convenience function matching the decision-engine interface used by
    FetchCoordinator and other canonical fetch entry points.
    """
    return _router.route(url, use_stealth=use_stealth, use_js=use_js, cache_safe=cache_safe, retry_after_status=retry_after_status, suggested_timeout_s=suggested_timeout_s, suggested_max_bytes=suggested_max_bytes, suggested_concurrency=suggested_concurrency, preclassified_kind=preclassified_kind, preclassified_host=preclassified_host)
_i2p_transport_var: contextvars.ContextVar[Any] = contextvars.ContextVar('i2p_transport', default=None)

def set_i2p_transport_singleton(transport: Any) -> None:
    """F250: Register I2PTransport singleton so all consumers share one session."""
    _i2p_transport_var.set(transport)

def get_i2p_transport_singleton() -> Any:
    """F250: Return registered I2PTransport singleton, or None."""
    return _i2p_transport_var.get()
__all__ = ['TransportRouter', 'TransportDecision', 'Lane', 'route_transport']
