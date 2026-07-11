"""
TransportResolver - Autonomous transport selection based on runtime context.

Priorities: Nym > Tor > Direct > InMemory
No config toggles - all decisions based on runtime signals.

ROLE (F300P):
  This file is a POLICY CANDIDATE, not current production transport authority.
  - Production routing lives in FetchCoordinator._fetch_url() via get_transport_for_url()
  - resolve_url() / is_tor_mandatory() are lightweight classification seams
  - resolve() is DORMANT — per-request start() is not production lifecycle

NOT AUTHORITY FOR:
  - Session lifecycle management (session_manager.py, session_runtime.py)
  - Runtime fetch truth (FetchCoordinator._fetch_url())
  - Tor session pool management
"""


import logging
from dataclasses import dataclass
import msgspec
from enum import Enum, auto
from typing import Any, cast

from hledac.universal.utils.cache import PyCacheDict

logger = logging.getLogger(__name__)

# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
_extract_host_cache: PyCacheDict[str, str] = PyCacheDict(512, 300.0)
_get_transport_cache: PyCacheDict[str, Transport] = PyCacheDict(512, 300.0)
_get_transport_hint_cache: PyCacheDict[str, str] = PyCacheDict(512, 300.0)


def _extract_host(url: str) -> str:
    """F3.2: Bounded TTL cache — PyCacheDict."""
    cached = _extract_host_cache.get(url)
    if cached is not None:
        return cached
    try:
        from hledac.universal.fetching.public_fetcher import url_ops

        result = url_ops.extract_host(url)
        _extract_host_cache.set(url, result)
        return result
    except Exception:  # noqa: BLE001
        pass
    # Fallback: manual string parse (no urllib overhead in hot path)
    try:
        netloc = url.split("://", 1)[1].split("/", 1)[0]
        if "?" in netloc:
            netloc = netloc.split("?", 1)[0]
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        result = netloc.lower()
        _extract_host_cache.set(url, result)
        return result
    except Exception:
        return ""


class RouteDecision(Enum):
    """
    Issue #37: Strict fail-closed routing decisions.

    Used by FetchCoordinator._fetch_url() to determine routing behavior
    when a transport is unavailable. Fail-closed means: if the required
    transport is not available, the request is dropped — never routed
    to a less-private channel.

    I2P_UNAVAILABLE: .i2p URL but no I2P router available → DROP (strict closed)
    TOR_UNAVAILABLE: .onion URL but no Tor available → DROP (strict closed)
    I2P_OK:    .i2p URL, I2P router available → route via I2P
    TOR_OK:    .onion URL, Tor available → route via Tor
    CLEARNET:  non-darknet URL → route via clearnet
    """
    I2P_UNAVAILABLE = auto()
    TOR_UNAVAILABLE = auto()
    I2P_OK = auto()
    TOR_OK = auto()
    CLEARNET = auto()


class Transport(Enum):
    """
    Transport type enum — used by SourceTransportMap.

    SPRINT 8VX: Transport World Classification:
      DIRECT    — plain TCP world (aiohttp TCPConnector)
      TOR       — proxy-aware SOCKS5 world (ProxyConnector)
      I2P       — proxy-aware SOCKS5 world (ProxyConnector)
      FREENET   — Freenet FProxy HTTP proxy world
      INMEMORY  — test/internal only
      GOPHER    — Gopher protocol (direct TCP socket)

    curl_cffi is a SEPARATE world (JA3 fingerprint spoofing) — not in this enum.
    """
    DIRECT = auto()
    TOR = auto()
    I2P = auto()
    FREENET = auto()
    INMEMORY = auto()
    GOPHER = auto()


# B6: SourceTransportMap — mandatory onion routing, no DIRECT override
_ONION_MAP: dict[str, Transport] = {
    ".onion": Transport.TOR,       # mandatory — never override to DIRECT
    ".i2p": Transport.I2P,         # I2P SAM/SOCKS proxy (port 7656/7654)
    ".b32.i2p": Transport.I2P,    # base32 I2P addresses (e.g., v4.b32.i2p)
    ".freenet": Transport.FREENET, # Freenet FProxy HTTP proxy
}


class SourceTransportMap:
    """
    B6: Domain-suffix → Transport mapping.
    .onion is MANDATORY Tor (cannot be overridden to DIRECT).
    .i2p routes to I2P (currently stub, fail-open to direct).
    """
    _map: dict[str, Transport] = _ONION_MAP

    @classmethod
    def get(cls, suffix: str) -> Transport:
        return cls._map.get(suffix, Transport.DIRECT)

    @classmethod
    def is_mandatory_tor(cls, suffix: str) -> bool:
        """Return True if suffix MUST use Tor (e.g. .onion)."""
        return cls._map.get(suffix) is Transport.TOR


@dataclass
class TransportContext:
    """Runtime context for transport selection."""
    requires_anonymity: bool = False
    risk_level: str = "medium"  # "low", "medium", "high"
    allow_inmemory: bool = False  # Only for testing/internal bus


class TransportResolver:
    """
    Autonomous transport selection without config toggles.

    Decisions based on:
    - context.requires_anonymity (derived from runtime signals)
    - context.risk_level
    - autodetection of Tor/Nym availability

    SPRINT 8VX — TRANSPORT WORLD CLASSIFICATION:
      This class manages the PLAIN TCP + PROXY-AWARE SOCKS world.
      curl_cffi world is SEPARATE — managed by StealthCrawler.

    AUTHORITY NOTE (audit/8SF):
      This class is a POLICY CANDIDATE, not the current production authority.
      Current production path: FetchCoordinator._fetch_url() routes .onion/.i2p
      directly via _fetch_with_tor() / darknet_connector, and clearnet via
      curl_cffi/StealthCrawler. This class's resolve() is DORMANT.

      resolve_url() / is_tor_mandatory() are fast sync helpers used by
      SourceTransportMap callers and are safe to call.

      Migration precondition:
        Wire resolve() into FetchCoordinator._fetch_url() ONLY after
        1. TorTransport/Tor session lifecycle is managed by resolver (not per-request start/stop)
        2. FetchCoordinator._get_tor_session() pool is replaced by resolver-backed session
        3. NymTransport persistent session is established (currently start/stop per request)
    """

    def __init__(self):
        self._tor_class: type | None = None
        self._nym_class: type | None = None
        self._tor_available: bool = False
        self._checked = False

    def _check_transports(self):
        """Lazy check for transport availability."""
        if self._checked:
            return

        # Check Tor runtime availability (SOCKS port)
        self._tor_available = self._check_tor_available()
        logger.debug(f"Tor runtime available: {self._tor_available}")

        # Try to import Tor transport
        try:
            from .tor_transport import TorTransport
            self._tor_class = TorTransport
            logger.debug("Tor transport importable")
        except ImportError as e:
            logger.debug(f"Tor transport unavailable: {e}")

        # Try to import Nym transport
        try:
            from .nym_transport import NymTransport
            self._nym_class = NymTransport
            logger.debug("Nym transport available")
        except ImportError as e:
            logger.debug(f"Nym transport unavailable: {e}")

        self._checked = True

    def _check_tor_available(self) -> bool:
        """Check if Tor is running by probing the SOCKS port (9050)."""
        import socket
        try:
            s = socket.socket()
            s.settimeout(2.0)
            s.connect(("127.0.0.1", 9050))
            s.close()
            return True
        except OSError:
            return False

    def is_tor_available(self) -> bool:
        """Return Tor runtime availability (probed, cached after first call)."""
        self._check_transports()
        return self._tor_available

    def is_i2p_available(self) -> bool:
        """
        Issue #37: Check if I2P router is running.

        Probes the I2P SOCKS port (7654). This is a fast synchronous check
        (~2ms) used by get_route_decision() for fail-closed routing.

        Returns True if I2P SOCKS proxy is reachable, False otherwise.
        """
        import socket
        try:
            s = socket.socket()
            s.settimeout(2.0)
            s.connect(("127.0.0.1", 7654))
            s.close()
            return True
        except OSError:
            return False

    def resolve_url(self, url: str) -> Transport:
        """
        B6/C.4: Resolve transport for a URL based on its domain suffix.
        This is a fast synchronous classification (<50ms for 1000 calls).

        Classification logic (shared with get_transport_for_url):
          .onion  → Transport.TOR   (mandatory, never DIRECT)
          .i2p    → Transport.I2P    (stub, fail-open to direct)
          other   → Transport.DIRECT

        Returns:
            Transport enum: TOR for .onion, I2P for .i2p, DIRECT for everything else
        """
        host = _extract_host(url)
        if host.endswith('.onion'):
            return Transport.TOR
        if host.endswith('.i2p'):
            return Transport.I2P
        return Transport.DIRECT

    def is_tor_mandatory(self, url: str) -> bool:
        """Return True if URL must use Tor transport (cannot be overridden)."""
        return _extract_host(url).endswith('.onion')

    async def resolve(self, context: TransportContext) -> Transport | None:
        """
        Resolve appropriate transport based on context.

        Priority: Nym > Tor > Direct > InMemory

        AUTHORITY NOTE: DORMANT — not wired into FetchCoordinator.
        See class-level migration precondition.
        """
        self._check_transports()

        # High anonymity requirement: prefer Nym > Tor
        if context.requires_anonymity or context.risk_level == "high":
            # Try Nym first (highest anonymity)
            if self._nym_class:
                try:
                    transport = self._nym_class()
                    await transport.start()
                    logger.info("Using Nym transport for high anonymity")
                    return transport
                except Exception as e:
                    logger.warning(f"Nym transport init failed: {e}")

            # Fallback to Tor (only if runtime is available)
            if self._tor_class and self.is_tor_available():
                try:
                    transport = self._tor_class()
                    await transport.start()
                    logger.info("Using Tor transport for anonymity (Nym unavailable)")
                    return transport
                except Exception as e:
                    logger.warning(f"Tor transport init failed: {e}")

            # If anonymity required but nothing available, log warning
            logger.warning("Anonymity required but no anonymous transport available")

        # Medium risk: try to use Tor/Nym if available, but don't require
        if context.risk_level == "medium":
            if self._nym_class:
                try:
                    transport = self._nym_class()
                    await transport.start()
                    logger.info("Using Nym transport (medium risk)")
                    return transport
                except Exception:  # noqa: BLE001
                    pass

            if self._tor_class and self.is_tor_available():
                try:
                    transport = self._tor_class()
                    await transport.start()
                    logger.info("Using Tor transport (medium risk)")
                    return transport
                except Exception:  # noqa: BLE001
                    pass

        # Low risk or fallback: InMemory removed — Issue 3.4
        # allow_inmemory kept as no-op for API compat

        # No transport available - return None, caller will handle
        logger.warning("No transport available, returning None")
        return None


# =============================================================================
# Sprint 4A: Minimal Proxy-Aware Seam — Policy Gate Accessor
# =============================================================================
#
# PURPOSE: Clean policy accessor for FetchCoordinator._fetch_url() entry point.
#   Replaces hardcoded url.endswith() checks with explicit policy classification.
#
#   This is a SEAM, not a cutover:
#     - Existing hardcoded logic in _fetch_url() stays as fallback truth
#     - SourceTransportMap.get() provides the policy classification layer
#     - No changes to actual transport execution (tor pool, darknet, curl)
#
#   RUNTIME TRUTH (Sprint 4A):
#     - Policy truth: SourceTransportMap.get() — ACTIVE, fast dict lookup
#     - Plain TCP surface: session_runtime.async_get_aiohttp_session() — separate
#     - Proxy-aware surface: FetchCoordinator._get_tor_session() — separate pool
#     - curl world: StealthCrawler/curl_cffi — separate TLS plane
#     - Resolver.resolve(): DORMANT — requires lifecycle preconditions
#
#   ATTACH PATH (4B): SourceTransportMap.get() used as policy gate in
#     FetchCoordinator._fetch_url() — replacing url.endswith() checks.
#     Safe because: same boolean logic, no behavioral change.
#
# =============================================================================


def get_transport_for_url(url: str) -> Transport:
    """F3.2: Bounded TTL cache — PyCacheDict."""
    cached = _get_transport_cache.get(url)
    if cached is not None:
        return cached
    result = _get_transport_for_url_impl(url)
    _get_transport_cache.set(url, result)
    return result


def _get_transport_for_url_impl(url: str) -> Transport:
    """
    Sprint 4A: Get Transport classification for a URL.

    This is the MINIMAL SEAM — a policy gate that wraps resolve_url()
    for explicit transport classification without changing execution.

    P10: Extended for .b32.i2p (base32 I2P) and .freenet addresses.
    F202H: Also populates the transport_hint string consumed by opsec_policy.

    Args:
        url: URL string to classify

    Returns:
        Transport.TOR for .onion, Transport.I2P for .i2p/.b32.i2p,
        Transport.FREENET for .freenet, Transport.DIRECT otherwise

    Invariants:
        [4A-I1] Fast dict lookup — no network, no transport init
        [4A-I2] Deterministic — same URL always returns same Transport
        [4A-I3] No side effects — pure function, thread-safe
    """
    host = _extract_host(url)
    # Check longer suffixes first (more specific)
    if host.endswith('.b32.i2p'):
        return Transport.I2P
    if host.endswith('.onion'):
        return Transport.TOR
    if host.endswith('.i2p'):
        return Transport.I2P
    if host.endswith('.freenet'):
        return Transport.FREENET
    # Gopher protocol
    if url.startswith('gopher://'):
        return Transport.GOPHER
    return Transport.DIRECT


# Issue #37: Bounded availability cache — 5s TTL to avoid hammering ports
_I2P_AVAILABLE_CACHE_TTL: float = 5.0
_i2p_available_cache: tuple[bool, float] | None = None  # (result, timestamp)


def _is_i2p_available_uncached() -> bool:
    """Probe I2P SOCKS port 7654 — internal uncached check."""
    import socket
    try:
        s = socket.socket()
        s.settimeout(2.0)
        s.connect(("127.0.0.1", 7654))
        s.close()
        return True
    except OSError:
        return False


def is_i2p_available() -> bool:
    """
    Issue #37: Check if I2P router is running (SOCKS port 7654).

    Uses a 5-second TTL cache to avoid hammering the port on repeated calls.
    Thread-safe via non-blocking socket check.

    Returns:
        True if I2P SOCKS proxy is reachable, False otherwise.
    """
    global _i2p_available_cache
    import time
    now = time.monotonic()
    if _i2p_available_cache is not None:
        result, timestamp = _i2p_available_cache
        if now - timestamp < _I2P_AVAILABLE_CACHE_TTL:
            return result
    result = _is_i2p_available_uncached()
    _i2p_available_cache = (result, now)
    return result


def get_route_decision(url: str) -> RouteDecision:
    """
    Issue #37: Strict fail-closed route decision combining suffix + runtime availability.

    This is the canonical fail-closed gate for .i2p and .onion URLs:
      - .i2p URL + I2P unavailable → RouteDecision.I2P_UNAVAILABLE (DROP)
      - .i2p URL + I2P available  → RouteDecision.I2P_OK
      - .onion URL + Tor unavailable → RouteDecision.TOR_UNAVAILABLE (DROP)
      - .onion URL + Tor available  → RouteDecision.TOR_OK
      - clearnet URL            → RouteDecision.CLEARNET

    Returns:
        RouteDecision enum — caller MUST handle I2P_UNAVAILABLE / TOR_UNAVAILABLE
        by dropping the request (never fall back to clearnet).
    """
    transport = get_transport_for_url(url)
    if transport is Transport.I2P:
        return RouteDecision.I2P_OK if is_i2p_available() else RouteDecision.I2P_UNAVAILABLE
    if transport is Transport.TOR:
        # Use cached Tor availability from resolver singleton
        resolver = _get_transport_resolver()
        return RouteDecision.TOR_OK if resolver.is_tor_available() else RouteDecision.TOR_UNAVAILABLE
    return RouteDecision.CLEARNET


_resolver_instance: "TransportResolver | None" = None


def _get_transport_resolver() -> "TransportResolver":
    """Get or create the module-level TransportResolver singleton."""
    global _resolver_instance
    if _resolver_instance is None:
        _resolver_instance = TransportResolver()
    return _resolver_instance


def get_transport_hint_string(url: str) -> str:
    """F3.2: Bounded TTL cache — PyCacheDict."""
    cached = _get_transport_hint_cache.get(url)
    if cached is not None:
        return cached
    transport = get_transport_for_url(url)
    if transport == Transport.TOR:
        result = "tor"
    elif transport == Transport.I2P:
        result = "i2p"
    elif transport == Transport.FREENET:
        result = "clearnet"
    else:
        result = "clearnet"
    _get_transport_hint_cache.set(url, result)
    return result


_I2P_TRANSPORT_SINGLETON: Any = None


def set_i2p_transport_singleton(transport: Any) -> None:
    """F250: Register I2PTransport singleton so all consumers share one session."""
    global _I2P_TRANSPORT_SINGLETON
    _I2P_TRANSPORT_SINGLETON = transport


def get_i2p_transport_singleton() -> Any:
    """F250: Return registered I2PTransport singleton, or None."""
    return _I2P_TRANSPORT_SINGLETON


# Backwards compatibility alias
Transport = Transport  # re-export via module-level alias


# =============================================================================
# F206AV VERDICT — TRANSPORT RESOLVER DORMANT PATH DECISION
# =============================================================================
#
# ACTIVE API (sealed, Sprint 4A canonical production path):
#   get_transport_for_url(url: str) -> Transport
#     Fast sync suffix classifier — no lifecycle, no network, thread-safe
#     Production call site: FetchCoordinator._fetch_url() via SourceTransportMap.get()
#     Supports: .onion, .i2p, .b32.i2p, .freenet, clearnet
#
#   get_transport_hint_string(url: str) -> str
#     Maps Transport -> opsec_policy string ("tor", "i2p", "clearnet")
#
# INSTANCE METHODS on TransportResolver (active but NOT the canonical path):
#   resolver.resolve_url(url: str) -> Transport
#     Fast sync helper — SUBSET of get_transport_for_url()
#     Supports: .onion, .i2p (including .b32.i2p via .i2p suffix), clearnet
#     KNOWN GAP: .freenet falls through to DIRECT (not classified)
#     Not used by FetchCoordinator — get_transport_for_url() is used instead
#
#   resolver.is_tor_mandatory(url: str) -> bool
#     True for .onion only — SourceTransportMap.is_mandatory_tor() facade
#
# DORMANT API (NOT recommended, not wired):
#   TransportResolver.resolve(context: TransportContext) -> Transport | None
#     WHY DORMANT: attempts per-request transport.start() lifecycle
#     which is incompatible with FetchCoordinator's pooled session model.
#     NOT called from any production path.
#
# MIGRATION RECOMMENDATION (future only, requires lifecycle preconditions):
#   1. TorTransport session pool must be managed by resolver
#   2. FetchCoordinator._get_tor_session() pool replaced by resolver-backed session
#   3. NymTransport persistent session established
#
# F206AR FINDING: CLOSED — resolve() explicitly documented as DORMANT since Sprint 8VX
# =============================================================================

