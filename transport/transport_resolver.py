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
import asyncio
from hledac.universal.utils.async_helpers import safe_wait_for
import logging
from dataclasses import dataclass
import msgspec
from enum import Enum, auto
from typing import Any, cast
from hledac.universal.utils.cache import PyCacheDict
logger = logging.getLogger(__name__)
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
    except Exception:
        pass
    try:
        netloc = url.split('://', 1)[1].split('/', 1)[0]
        if '?' in netloc:
            netloc = netloc.split('?', 1)[0]
        if ':' in netloc:
            netloc = netloc.split(':')[0]
        result = netloc.lower()
        _extract_host_cache.set(url, result)
        return result
    except Exception:
        return ''

def _probe_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """
    Parameterized TCP port probe — replaces 4 duplicated socket probing fragments.

    Duplication targets (Type-2 renamed):
      1. _check_tor_available()        — host=127.0.0.1, port=9050, timeout=0.5
      2. _check_tor_available_async()  — host=127.0.0.1, port=9050, timeout=0.5
      3. is_i2p_available()            — host=127.0.0.1, port=7654, timeout=2.0
      4. _is_i2p_available_uncached()  — host=127.0.0.1, port=7654, timeout=2.0

    Invariants:
      [PROBE-I1] Fail-closed — returns False on any error (OSError, timeout)
      [PROBE-I2] No side effects — socket always closed, even on exception
      [PROBE-I3] Thread-safe — no shared state
      [PROBE-I4] Socket always closed — no fd leak on connect error
    """
    import socket
    s = socket.socket()
    try:
        s.settimeout(timeout)
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            s.close()
        except OSError:
            pass

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
_ONION_MAP: dict[str, Transport] = {'.onion': Transport.TOR, '.i2p': Transport.I2P, '.b32.i2p': Transport.I2P, '.freenet': Transport.FREENET}

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

class TransportContext(msgspec.Struct):
    """Runtime context for transport selection."""
    requires_anonymity: bool = False
    risk_level: str = 'medium'
    allow_inmemory: bool = False

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
    __slots__ = tuple(('_checked', '_nym_class', '_tor_available', '_tor_class'))

    def __init__(self):
        self._tor_class: type | None = None
        self._nym_class: type | None = None
        self._tor_available: bool = False
        self._checked = False

    def _check_transports(self):
        """Lazy check for transport availability."""
        if self._checked:
            return
        self._tor_available = self._check_tor_available()
        logger.debug(f'Tor runtime available: {self._tor_available}')
        try:
            from .tor_transport import TorTransport
            self._tor_class = TorTransport
            logger.debug('Tor transport importable')
        except ImportError as e:
            logger.debug(f'Tor transport unavailable: {e}')
        try:
            from .nym_transport import NymTransport
            self._nym_class = NymTransport
            logger.debug('Nym transport available')
        except ImportError as e:
            logger.debug(f'Nym transport unavailable: {e}')
        self._checked = True

    def _check_tor_available(self) -> bool:
        """Check if Tor is running by probing the SOCKS port (9050).
        D-22 fix: reduced timeout from 2.0s to 0.5s — only called once at init
        via _check_transports(), cached thereafter.
        Parameterized via _probe_tcp_port (PROBE-I1..I4 invariants).
        """
        return _probe_tcp_port('127.0.0.1', 9050, timeout=0.5)

    async def _check_tor_available_async(self) -> bool:
        """Check if Tor is running by probing the SOCKS port (9050).
        D-22 fix: asyncio.to_thread + 0.5s timeout to avoid blocking the
        event loop when called from async routing contexts.
        Single timeout source: asyncio.wait_for (socket timeout redundant).
        """
        try:
            return await safe_wait_for(
                asyncio.to_thread(_probe_tcp_port, '127.0.0.1', 9050, 0.5),
                timeout=0.6,
                label="tor_probe",
            )
        except asyncio.TimeoutError:
            return False

    def is_tor_available(self) -> bool:
        """Return Tor runtime availability (probed, cached after first call)."""
        self._check_transports()
        return self._tor_available

    async def async_is_tor_available(self) -> bool:
        """Async version of is_tor_available for use in async routing decisions.
        D-22 fix: avoids blocking the event loop with 2s socket timeout.
        Probes dynamically if not yet checked; uses cached value otherwise.

        Also populates _tor_class (like _check_transports does) so that
        resolve() can safely use self._tor_class after this returns True.
        """
        if not self._checked:
            self._tor_available = await self._check_tor_available_async()
            logger.debug(f'Tor runtime available (async): {self._tor_available}')
            try:
                from .tor_transport import TorTransport
                self._tor_class = TorTransport
                logger.debug('Tor transport importable')
            except ImportError:
                self._tor_class = None
            try:
                from .nym_transport import NymTransport
                self._nym_class = NymTransport
                logger.debug('Nym transport available')
            except ImportError:
                self._nym_class = None
            self._checked = True
        return self._tor_available

    def is_i2p_available(self) -> bool:
        """
        Issue #37: Check if I2P router is running.

        Probes the I2P SOCKS port (7654). This is a fast synchronous check
        (~2ms) used by get_route_decision() for fail-closed routing.

        Returns True if I2P SOCKS proxy is reachable, False otherwise.
        """
        return _probe_tcp_port('127.0.0.1', 7654, timeout=2.0)

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
        if context.requires_anonymity or context.risk_level == 'high':
            if self._nym_class:
                try:
                    transport = self._nym_class()
                    await transport.start()
                    logger.info('Using Nym transport for high anonymity')
                    return transport
                except Exception as e:
                    logger.warning(f'Nym transport init failed: {e}')
            if self._tor_class and await self.async_is_tor_available():
                try:
                    transport = self._tor_class()
                    await transport.start()
                    logger.info('Using Tor transport for anonymity (Nym unavailable)')
                    return transport
                except Exception as e:
                    logger.warning(f'Tor transport init failed: {e}')
            logger.warning('Anonymity required but no anonymous transport available')
        if context.risk_level == 'medium':
            if self._nym_class:
                try:
                    transport = self._nym_class()
                    await transport.start()
                    logger.info('Using Nym transport (medium risk)')
                    return transport
                except Exception:
                    pass
            if self._tor_class and await self.async_is_tor_available():
                try:
                    transport = self._tor_class()
                    await transport.start()
                    logger.info('Using Tor transport (medium risk)')
                    return transport
                except Exception:
                    pass
        logger.warning('No transport available, returning None')
        return None

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
    if host.endswith('.b32.i2p'):
        return Transport.I2P
    if host.endswith('.onion'):
        return Transport.TOR
    if host.endswith('.i2p'):
        return Transport.I2P
    if host.endswith('.freenet'):
        return Transport.FREENET
    if url.startswith('gopher://'):
        return Transport.GOPHER
    return Transport.DIRECT
_I2P_AVAILABLE_CACHE_TTL: float = 5.0
_i2p_available_cache: tuple[bool, float] | None = None

def _is_i2p_available_uncached() -> bool:
    """Probe I2P SOCKS port 7654 — internal uncached check."""
    return _probe_tcp_port('127.0.0.1', 7654, timeout=2.0)

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
        resolver = _get_transport_resolver()
        return RouteDecision.TOR_OK if resolver.is_tor_available() else RouteDecision.TOR_UNAVAILABLE
    return RouteDecision.CLEARNET


async def async_get_route_decision(url: str) -> RouteDecision:
    """
    D-22 fix: async version of get_route_decision for use in async contexts.
    Uses async_is_tor_available() to avoid blocking the event loop.
    """
    transport = get_transport_for_url(url)
    if transport is Transport.I2P:
        # is_i2p_available is sync (~2ms) so not worth async-ifying
        return RouteDecision.I2P_OK if is_i2p_available() else RouteDecision.I2P_UNAVAILABLE
    if transport is Transport.TOR:
        resolver = _get_transport_resolver()
        return RouteDecision.TOR_OK if await resolver.async_is_tor_available() else RouteDecision.TOR_UNAVAILABLE
    return RouteDecision.CLEARNET
_resolver_instance: 'TransportResolver | None' = None

def _get_transport_resolver() -> 'TransportResolver':
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
        result = 'tor'
    elif transport == Transport.I2P:
        result = 'i2p'
    else:
        result = 'clearnet'
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
Transport = Transport
