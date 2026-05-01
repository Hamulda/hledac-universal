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
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)


def _extract_host(url: str) -> str:
    """Extract hostname from URL. Returns lowercase host or empty string on parse failure."""
    try:
        netloc = url.split("://", 1)[1].split("/", 1)[0]
        if "?" in netloc:
            netloc = netloc.split("?", 1)[0]
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc.lower()
    except Exception:
        return ""


class Transport(Enum):
    """
    Transport type enum — used by SourceTransportMap.

    SPRINT 8VX: Transport World Classification:
      DIRECT    — plain TCP world (aiohttp TCPConnector)
      TOR       — proxy-aware SOCKS5 world (ProxyConnector)
      I2P       — proxy-aware SOCKS5 world (ProxyConnector)
      FREENET   — Freenet FProxy HTTP proxy world
      INMEMORY  — test/internal only

    curl_cffi is a SEPARATE world (JA3 fingerprint spoofing) — not in this enum.
    """
    DIRECT = auto()
    TOR = auto()
    I2P = auto()
    FREENET = auto()
    INMEMORY = auto()


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
        self._tor_class: Optional[type] = None
        self._nym_class: Optional[type] = None
        self._checked = False

    def _check_transports(self):
        """Lazy check for transport availability."""
        if self._checked:
            return

        # Try to import Tor transport
        try:
            from .tor_transport import TorTransport
            self._tor_class = TorTransport
            logger.debug("Tor transport available")
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

    async def resolve(self, context: TransportContext) -> Optional['Transport']:
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

            # Fallback to Tor
            if self._tor_class:
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
                except Exception:
                    pass

            if self._tor_class:
                try:
                    transport = self._tor_class()
                    await transport.start()
                    logger.info("Using Tor transport (medium risk)")
                    return transport
                except Exception:
                    pass

        # Low risk or fallback: use InMemory for testing/internal
        if context.allow_inmemory:
            logger.info("Using InMemory transport (fallback)")
            return Transport.INMEMORY

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


def get_transport_for_url(url: str) -> 'Transport':
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
    return Transport.DIRECT


def get_transport_hint_string(url: str) -> str:
    """
    F202H: Return transport hint string for opsec_policy.

    Maps Transport enum to string for the policy engine.
    Used by callers that need to build OPSECContext.
    """
    transport = get_transport_for_url(url)
    if transport == Transport.TOR:
        return "tor"
    if transport == Transport.I2P:
        return "i2p"
    if transport == Transport.FREENET:
        return "clearnet"  # Freenet uses HTTP proxy, treated as clearnet
    return "clearnet"


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
#   TransportResolver.resolve(context: TransportContext) -> Optional[Transport]
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

