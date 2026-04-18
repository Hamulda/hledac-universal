"""
Circuit Breaker — transport resilience pattern.

Prevents cascading failures by opening the circuit after repeated
consecutive failures/timeouts for a given domain.

Sprint 8VB — Transport Resilience + Self-Hosted Search
Sprint 8VE C — Transport fallback chain
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class CBState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    domain: str
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    _state: CBState = field(default=CBState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _consecutive_timeouts: int = field(default=0, init=False)

    def is_open(self) -> bool:
        if self._state == CBState.OPEN:
            if time.monotonic() - self._last_failure_time > self.recovery_timeout:
                self._state = CBState.HALF_OPEN
                return False
            return True
        return False

    def record_success(self):
        self._failure_count = 0
        self._consecutive_timeouts = 0
        self._state = CBState.CLOSED
        self.recovery_timeout = 60.0  # reset to baseline after successful recovery

    def record_failure(self, is_timeout: bool = False):
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if is_timeout:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= 3:
                self.recovery_timeout = min(
                    self.recovery_timeout * 2, 3600.0
                )
                self._consecutive_timeouts = 0
        else:
            self._consecutive_timeouts = 0
        if self._failure_count >= self.failure_threshold:
            self._state = CBState.OPEN

    def get_state(self) -> str:
        return self._state.value


_BREAKERS: dict[str, CircuitBreaker] = {}


def get_breaker(domain: str) -> CircuitBreaker:
    if domain not in _BREAKERS:
        _BREAKERS[domain] = CircuitBreaker(domain=domain)
    return _BREAKERS[domain]


def get_all_breaker_states() -> dict[str, str]:
    return {d: b.get_state() for d, b in _BREAKERS.items()}


# =============================================================================
# Sprint 8VE C.2: Transport fallback chain
# Sprint 8UX: Authority clarification
# =============================================================================
#
# PRODUCTION AUTHORITY (ACTIVE):
#   - CircuitBreaker class IS used by FetchCoordinator domain CB logic.
#   - get_breaker(domain) is the canonical domain circuit breaker accessor.
#   - get_all_breaker_states() is used for runtime diagnostics.
#
# TEST-SEAM ONLY (NOT called from production):
#   - get_transport_for_domain() — fallback chain logic, exercised by probe_8ve only.
#   - resilient_fetch() — fallback chain fetch, exercised by probe_8ve only.
#
# Split rationale:
#   CircuitBreaker class itself is a SHARED STATE OBJECT (owned by _BREAKERS global).
#   It lives here but is consumed by both FetchCoordinator (production) and
#   resilient_fetch() (test-seam). The class is production-safe; the helper
#   functions using it are test-seam.
#
#   Migration: AFTER TransportResolver.resolve() is wired into FetchCoordinator,
#   resilient_fetch() should be removed and get_transport_for_domain() replaced
#   by the resolver-based path.
#
#   Production path:
#     FetchCoordinator._fetch_url() handles .onion/.i2p directly via
#     _fetch_with_tor() / _fetch_with_lightpanda() / _fetch_with_curl().
#
#   Donor/Compat:
#     circuit_breaker.py CircuitBreaker class IS used by other code (shared state).
#     get_breaker() is the canonical domain circuit breaker accessor.
#
#   Migration precondition:
#     Remove this module's fallback-chain functions only AFTER
#     TransportResolver.resolve() is wired into FetchCoordinator._fetch_url()
#     and probe_8ve tests are redirected to the wired path.
# =============================================================================

async def get_transport_for_domain(domain: str) -> str:
    """
    Fallback chain: clearnet → Tor → Nym.
    Nym má 2-10s latenci — používej POUZE pro anonymity_required tasky.
    Rozhoduje podle Circuit Breaker stavů.
    """
    cb_clearnet = get_breaker(domain)
    if not cb_clearnet.is_open():
        return "clearnet"
    cb_tor = get_breaker(f"tor:{domain}")
    if not cb_tor.is_open():
        return "tor"
    return "nym"


async def resilient_fetch(
    url: str,
    anonymity_required: bool = False,
    **kwargs
) -> str | None:
    """
    Fetch s automatickým transport fallback.
    anonymity_required=True → preskočí clearnet, jde rovnou na Tor/Nym.
    Nym NIKDY v automatickém fallback pro normální tasky — 2-10s latence
    by zablokovala semaphore slot a snížila throughput sprintu.

    Transport separation (F192C):
      - clearnet: uses shared session_runtime aiohttp session (TCPConnector pool)
      - tor: owns its own ProxyConnector-based session — ProxyConnector is
        architecturally incompatible with the shared plain-TCP TCPConnector pool
      - nym: uses NymTransport directly

    F192C: clearnet path now uses async_get_aiohttp_session() instead of
    creating an ad-hoc per-call ClientSession. This reduces session proliferation
    and consolidates TCP connector state onto the shared pool.
    """
    from urllib.parse import urlparse
    domain = urlparse(url).netloc

    if anonymity_required:
        transport = "tor"
    else:
        transport = await get_transport_for_domain(domain)
        if transport == "nym":
            # Nym pouze pro explicitní anonymity_required=True
            logger.debug(f"[TRANSPORT] Nym skipped for {domain} (use anonymity_required=True)")
            return None

    if transport == "clearnet":
        # F192C: use shared session_runtime session instead of ad-hoc ClientSession
        try:
            from hledac.universal.network.session_runtime import (
                async_get_aiohttp_session,
                HTML_CONNECT_TIMEOUT_S,
                HTML_READ_TIMEOUT_S,
            )
            session = await async_get_aiohttp_session()
            timeout_s = kwargs.get("timeout", HTML_READ_TIMEOUT_S)
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(
                    connect=HTML_CONNECT_TIMEOUT_S,
                    sock_read=timeout_s,
                ),
            ) as resp:
                if resp.status == 200:
                    return await resp.text()
                return None
        except asyncio.TimeoutError:
            return None
        except Exception:
            return None

    elif transport == "tor":
        cb = get_breaker(f"tor:{domain}")
        try:
            # Tor fetch pres SOCKS5 proxy (Tor daemon musí běžet na 9050).
            # ProxyConnector owns its connector — incompatible with shared TCPConnector.
            # Each Tor fetch gets its own session; Tor is low-volume so no pooling needed.
            from aiohttp_socks import ProxyConnector
            timeout = kwargs.get("timeout", 15.0)
            connector = ProxyConnector.from_url("socks5://127.0.0.1:9050", rdns=True)
            async with aiohttp.ClientSession(connector=connector,
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as session:
                async with session.get(url) as resp:
                    cb.record_success()
                    if resp.status == 200:
                        return await resp.text()
                    return None
        except Exception:
            cb.record_failure()
            if anonymity_required:
                # Tor selhal + anonymity required → zkus Nym
                try:
                    from hledac.universal.transport.nym_transport import NymTransport
                    nym = NymTransport()
                    await nym.start()
                    try:
                        result = await nym.send_message(url, "fetch", {}, "", "")
                        return result
                    finally:
                        await nym.stop()
                except Exception:
                    pass
            return None

    return None
