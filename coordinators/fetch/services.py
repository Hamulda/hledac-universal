"""
Fetch Services — Service Layer for FetchCoordinator
=================================================

Provides isolated, testable components for the fetch pipeline:
- TransportRouter: transport selection (Tor/I2P/clearnet/gopher)
- RateLimiter: per-host and domain rate limiting
- DNSCache: DNS resolution with single-flight pattern
- CircuitBreaker: circuit breaker state management
- RetryPolicy: retry budget tracking

M1 8GB: All services use lazy initialization — transports that are
not enabled via environment variables are never loaded.

Usage:
    from coordinators.fetch.services import FetchServiceRegistry

    registry = FetchServiceRegistry(config=config)
    await registry.initialize()

    # Get service
    dns = registry.dns
    result = await dns.resolve(host)
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# =============================================================================
# Protocol Definitions
# =============================================================================

@runtime_checkable
class FetchTransport(Protocol):
    """Protocol for fetch transports."""

    name: str

    async def fetch(self, url: str, options: FetchOptions) -> FetchResult:
        """Fetch URL via this transport."""
        ...

    def is_available(self) -> bool:
        """Check if transport is available."""
        ...


@dataclass(frozen=True)
class FetchOptions:
    """Options for fetch operation."""
    timeout: float = 30.0
    max_retries: int = 3
    user_agent: str | None = None
    headers: dict[str, str] | None = None
    privacy_level: int = 0  # 0=clearnet, 1=TOR, 2=I2P


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Result of fetch operation."""
    success: bool
    status_code: int = 0
    content: bytes = b''
    content_type: str = ''
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    transport: str = 'unknown'
    fetch_time_ms: float = 0.0


# =============================================================================
# DNS Cache Service
# =============================================================================

@dataclass(slots=True)
class DNSCacheService:
    """
    DNS cache with single-flight pattern.

    Prevents duplicate DNS resolutions for concurrent requests to the same host.
    M1 8GB: TTLCache with bounded size (2048 entries, 5min TTL).
    """
    _cache: TTLCache[str, list[str]] = field(default_factory=lambda: TTLCache(maxsize=2048, ttl=300))
    _inflight: dict[str, asyncio.Future[list[str] | None]] = field(default_factory=dict)
    _dedup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _per_host_gate: Any = field(default=None)
    _private_nets: tuple = field(default_factory=lambda: tuple(
        ipaddress.ip_network(n) for n in [
            '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
            '127.0.0.0/8', '169.254.0.0/16', '100.64.0.0/10'
        ]
    ))

    def __post_init__(self) -> None:
        # Import here to avoid circular deps
        try:
            from hledac.universal.utils.async_helpers import BoundedPerHostGate
            self._per_host_gate = BoundedPerHostGate(max_hosts=512, per_host_limit=4)
        except ImportError:
            self._per_host_gate = None

    @staticmethod
    def _is_ip_public(ip_str: str) -> bool:
        """Check if IP is public."""
        try:
            ip = ipaddress.ip_address(ip_str)
            for net in [
                ipaddress.ip_network('10.0.0.0/8'),
                ipaddress.ip_network('172.16.0.0/12'),
                ipaddress.ip_network('192.168.0.0/16'),
                ipaddress.ip_network('127.0.0.0/8'),
                ipaddress.ip_network('169.254.0.0/16'),
                ipaddress.ip_network('100.64.0.0/10'),
            ]:
                if ip in net:
                    return False
            return not (ip.is_multicast or ip.is_unspecified or ip.is_loopback)
        except (ValueError, TypeError):
            return False

    async def resolve(self, host: str) -> tuple[bool, list[str]]:
        """
        Resolve hostname with single-flight pattern.

        Returns (success, ips).
        """
        if not host:
            return (False, [])

        # Check for IP literal
        try:
            ip = ipaddress.ip_address(host)
            if not self._is_ip_public(str(ip)):
                return (False, [str(ip)])
            return (True, [str(ip)])
        except ValueError:
            pass

        cache_key = host.lower()

        # Check cache
        cached = self._cache.get(cache_key)
        if cached is not None:
            if not cached:
                return (False, [])
            return (True, list(cached))

        # Single-flight: wait for in-flight resolution
        if cache_key in self._inflight:
            ips = await self._inflight[cache_key]
            if not ips:
                return (False, [])
            return (True, list(ips))

        # Start new resolution
        return await self._resolve_host(host, cache_key)

    async def _resolve_host(self, host: str, cache_key: str) -> tuple[bool, list[str]]:
        """Perform actual DNS resolution with single-flight tracking."""
        if self._per_host_gate:
            sem, _ = await self._per_host_gate.acquire(host)

        try:
            loop = asyncio.get_event_loop()
            fut: asyncio.Future[list[str] | None] = loop.create_future()
            self._inflight[cache_key] = fut

            try:
                raw_results = await loop.getaddrinfo(
                    host, 0, proto=socket.IPPROTO_TCP
                )
            finally:
                if self._per_host_gate:
                    self._per_host_gate.release(sem)

            ips = sorted({str(r[4][0]) for r in raw_results})

            async with self._dedup_lock:
                if ips:
                    self._cache[cache_key] = ips
                fut.set_result(ips if ips else None)
                self._inflight.pop(cache_key, None)

            if not ips:
                return (False, [])

            return (True, ips)

        except (TimeoutError, OSError):
            return (False, [])

    async def prefetch(self, hosts: list[str]) -> None:
        """Prefetch multiple hosts concurrently."""
        if not hosts:
            return
        await asyncio.gather(*[self.resolve(h) for h in hosts], return_exceptions=True)


# =============================================================================
# Rate Limiter Service
# =============================================================================

@dataclass(slots=True)
class RateLimiterService:
    """
    Per-domain rate limiter with retry budget tracking.

    CB-02: Per-domain rate limiter — 0.5 RPS default (1 req / 2s)
    CB-04: Retry budget per domain — max 20 retries per 60s window
    """
    _rate_limit_rps: float = field(default=0.5)
    _max_hosts: int = field(default=512)
    _domain_limiter: Any = field(default=None)
    _retry_budget: dict[str, list[float]] = field(default_factory=dict)
    _retry_budget_lock: threading.Lock = field(default_factory=threading.Lock)
    _retry_budget_max: int = field(default=20)
    _retry_budget_window: float = field(default=60.0)

    def __post_init__(self) -> None:
        try:
            from hledac.universal.utils.async_helpers import DomainRateLimiter
            self._domain_limiter = DomainRateLimiter(
                rate=self._rate_limit_rps,
                max_hosts=self._max_hosts
            )
        except ImportError:
            self._domain_limiter = None

    async def acquire(self, domain: str) -> tuple[bool, str]:
        """
        Acquire rate limit slot for domain.

        Returns (allowed, reason).
        """
        if not domain:
            return (True, "empty_domain")

        # Check retry budget
        with self._retry_budget_lock:
            now = time.monotonic()
            if domain in self._retry_budget:
                self._retry_budget[domain] = [
                    ts for ts in self._retry_budget[domain]
                    if now - ts < self._retry_budget_window
                ]
                if len(self._retry_budget[domain]) >= self._retry_budget_max:
                    return (False, f"retry_budget_exceeded:{len(self._retry_budget[domain])}/{self._retry_budget_max}")

        # Check domain rate limit
        if self._domain_limiter:
            allowed, reason = self._domain_limiter.check(domain)
            if not allowed:
                return (False, reason)

        return (True, "ok")

    def record_success(self, domain: str) -> None:
        """Record successful request."""
        if self._domain_limiter:
            self._domain_limiter.record(domain)

    def record_failure(self, domain: str, is_retry: bool = False) -> None:
        """Record failed request."""
        if is_retry:
            now = time.monotonic()
            with self._retry_budget_lock:
                if domain not in self._retry_budget:
                    self._retry_budget[domain] = []
                self._retry_budget[domain].append(now)

        if self._domain_limiter:
            self._domain_limiter.record_failure(domain)


# =============================================================================
# Circuit Breaker Service
# =============================================================================

@dataclass(slots=True)
class CircuitBreakerService:
    """
    Circuit breaker facade for fetch operations.

    Wraps transport/circuit_breaker.py for domain and transport-level
    circuit breakers.
    """

    def check_domain(self, domain: str) -> tuple[bool, str, float]:
        """
        Check domain circuit breaker.

        Returns (allowed, reason, retry_after_s).
        """
        try:
            from hledac.universal.transport import circuit_breaker as cb
            decision = cb.domain_breaker_check(domain)
            return (decision.allowed, decision.reason, decision.retry_after_s)
        except (ImportError, AttributeError, OSError):
            return (True, "cb_unavailable", 0.0)

    def record_domain_success(self, domain: str) -> None:
        """Record domain success."""
        try:
            from hledac.universal.transport import circuit_breaker as cb
            cb.domain_breaker_record_success(domain)
        except (ImportError, AttributeError):
            pass

    def record_domain_failure(
        self, domain: str, is_timeout: bool = False, failure_kind: str = ''
    ) -> None:
        """Record domain failure."""
        try:
            from hledac.universal.transport import circuit_breaker as cb
            cb.domain_breaker_record_failure(
                domain, is_timeout=is_timeout,
                failure_kind=failure_kind or 'fetch_error'
            )
        except (ImportError, AttributeError, OSError):
            pass

    def check_transport(self, transport: str) -> tuple[bool, str, float]:
        """
        Check transport-level circuit breaker (Tor/I2P).

        Returns (allowed, reason, retry_after_s).
        """
        if transport not in ("tor", "i2p"):
            return (True, f"unknown_transport:{transport}", 0.0)

        try:
            from hledac.universal.transport import circuit_breaker as cb
            breaker = cb.get_transport_breaker(transport)
            if breaker is None:
                return (True, f"transport_breaker_not_found:{transport}", 0.0)
            decision = breaker.check_circuit()
            return (decision.allowed, decision.reason, decision.retry_after_s)
        except (ImportError, AttributeError, OSError):
            return (True, "transport_cb_unavailable", 0.0)

    def record_transport_success(self, transport: str) -> None:
        """Record transport success."""
        if transport not in ("tor", "i2p"):
            return
        try:
            from hledac.universal.transport import circuit_breaker as cb
            breaker = cb.get_transport_breaker(transport)
            if breaker is not None:
                breaker.record_success()
        except (ImportError, AttributeError):
            pass

    def record_transport_failure(self, transport: str, is_timeout: bool = False) -> None:
        """Record transport failure."""
        if transport not in ("tor", "i2p"):
            return
        try:
            from hledac.universal.transport import circuit_breaker as cb
            breaker = cb.get_transport_breaker(transport)
            if breaker is not None:
                breaker.record_failure(is_timeout=is_timeout)
        except (ImportError, AttributeError, OSError):
            pass


# =============================================================================
# Retry Policy Service
# =============================================================================

@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Configuration for retry policy."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    budget_max: int = 20
    budget_window: float = 60.0


@dataclass(slots=True)
class RetryPolicyService:
    """
    Retry policy with budget tracking.

    Tracks retry attempts per domain to prevent amplification attacks.
    """
    config: RetryConfig = field(default_factory=RetryConfig)
    _budget: dict[str, list[float]] = field(default_factory=dict)
    _budget_lock: threading.Lock = field(default_factory=threading.Lock)

    def can_retry(self, domain: str, attempt: int) -> tuple[bool, str]:
        """
        Check if retry is allowed.

        Returns (allowed, reason).
        """
        if attempt >= self.config.max_retries:
            return (False, f"max_retries_exceeded:{attempt}")

        now = time.monotonic()
        with self._budget_lock:
            if domain in self._budget:
                self._budget[domain] = [
                    ts for ts in self._budget[domain]
                    if now - ts < self.config.budget_window
                ]
                if len(self._budget[domain]) >= self.config.budget_max:
                    return (False, f"budget_exceeded:{len(self._budget[domain])}")

        return (True, "ok")

    def record_retry(self, domain: str) -> None:
        """Record retry attempt."""
        now = time.monotonic()
        with self._budget_lock:
            if domain not in self._budget:
                self._budget[domain] = []
            self._budget[domain].append(now)

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt with exponential backoff + jitter."""
        import random
        delay = min(self.config.base_delay * (2 ** attempt), self.config.max_delay)
        # Add jitter (±10%)
        jitter = delay * 0.1 * (random.random() * 2 - 1)
        return delay + jitter


# =============================================================================
# Fetch Service Registry
# =============================================================================

@dataclass(frozen=True)
class FetchServiceConfig:
    """Configuration for fetch services."""
    enable_tor: bool = False
    enable_i2p: bool = False
    enable_gopher: bool = False
    enable_captcha: bool = False
    rate_limit_rps: float = 0.5
    max_retries: int = 3
    timeout: float = 30.0

    @classmethod
    def from_env(cls) -> FetchServiceConfig:
        """Create config from environment variables."""
        return cls(
            enable_tor=os.environ.get('HLEDAC_ENABLE_TOR') == '1',
            enable_i2p=os.environ.get('HLEDAC_ENABLE_I2P') == '1',
            enable_gopher=os.environ.get('HLEDAC_ENABLE_GOPHER') == '1',
            enable_captcha=os.environ.get('HLEDAC_ENABLE_CAPTCHA_DETECTION') == '1',
            rate_limit_rps=float(os.environ.get('HLEDAC_RATE_LIMIT_RPS', '0.5')),
            max_retries=int(os.environ.get('HLEDAC_MAX_RETRIES', '3')),
            timeout=float(os.environ.get('HLEDAC_FETCH_TIMEOUT', '30.0')),
        )


class FetchServiceRegistry:
    """
    Registry for all fetch services.

    Provides lazy initialization of transports and services.
    M1 8GB: Only enabled transports are loaded.
    """
    __slots__ = ('_config', '_dns', '_rate_limiter', '_circuit_breaker',
                 '_retry_policy', '_robots_checker', '_transports', '_initialized')

    def __init__(self, config: FetchServiceConfig | None = None) -> None:
        self._config = config or FetchServiceConfig.from_env()
        self._dns: DNSCacheService | None = None
        self._rate_limiter: RateLimiterService | None = None
        self._circuit_breaker: CircuitBreakerService | None = None
        self._retry_policy: RetryPolicyService | None = None
        self._robots_checker: Any = None
        self._transports: dict[str, Any] = {}
        self._initialized = False

    async def initialize(self) -> bool:
        """Initialize all services lazily."""
        if self._initialized:
            return True

        # Initialize core services
        self._dns = DNSCacheService()
        self._rate_limiter = RateLimiterService(
            _rate_limit_rps=self._config.rate_limit_rps
        )
        self._circuit_breaker = CircuitBreakerService()
        self._retry_policy = RetryPolicyService(
            config=RetryConfig(max_retries=self._config.max_retries)
        )

        # Lazy-load transports based on config
        await self._initialize_transports()

        self._initialized = True
        logger.info("FetchServiceRegistry initialized", extra={
            'transports': list(self._transports.keys()),
            'dns_cache_size': 2048,
            'rate_limit_rps': self._config.rate_limit_rps,
        })
        return True

    async def _initialize_transports(self) -> None:
        """Initialize transports based on enabled flags."""
        # Clearnet always available
        self._transports['clearnet'] = None  # Placeholder for actual transport

        # Tor
        if self._config.enable_tor:
            try:
                from hledac.universal.transport.tor_transport import TorTransport
                tor = TorTransport()
                if tor.available:
                    self._transports['tor'] = tor
                    logger.info('TorTransport enabled')
            except ImportError:
                pass

        # I2P
        if self._config.enable_i2p:
            try:
                from hledac.universal.transport.i2p_transport import I2PTransport
                i2p = I2PTransport()
                if i2p.available:
                    self._transports['i2p'] = i2p
                    logger.info('I2PTransport enabled')
            except ImportError:
                pass

        # Gopher
        if self._config.enable_gopher:
            try:
                from hledac.universal.transport.gopher_transport import GopherTransport
                self._transports['gopher'] = GopherTransport()
                logger.info('GopherTransport enabled')
            except ImportError:
                pass

    # -------------------------------------------------------------------------
    # Service Accessors
    # -------------------------------------------------------------------------

    @property
    def dns(self) -> DNSCacheService:
        """Get DNS cache service."""
        if self._dns is None:
            raise RuntimeError("FetchServiceRegistry not initialized")
        return self._dns

    @property
    def rate_limiter(self) -> RateLimiterService:
        """Get rate limiter service."""
        if self._rate_limiter is None:
            raise RuntimeError("FetchServiceRegistry not initialized")
        return self._rate_limiter

    @property
    def circuit_breaker(self) -> CircuitBreakerService:
        """Get circuit breaker service."""
        if self._circuit_breaker is None:
            raise RuntimeError("FetchServiceRegistry not initialized")
        return self._circuit_breaker

    @property
    def retry_policy(self) -> RetryPolicyService:
        """Get retry policy service."""
        if self._retry_policy is None:
            raise RuntimeError("FetchServiceRegistry not initialized")
        return self._retry_policy

    def get_transport_object(self, transport_name: str) -> FetchTransport | None:
        """
        Get transport object by name.

        Returns the transport object or None for clearnet.
        """
        return self._transports.get(transport_name)

    def get_transport(self, url: str) -> str:
        """
        Determine which transport to use for URL.

        Returns transport name: 'clearnet', 'tor', 'i2p', 'gopher'.
        """
        # Parse URL scheme
        if url.startswith('i2p://'):
            if 'i2p' in self._transports:
                return 'i2p'
            return 'clearnet'

        if url.startswith('gopher://'):
            if 'gopher' in self._transports:
                return 'gopher'
            return 'clearnet'

        # Check for darknet indicators
        lower_url = url.lower()
        if ('.onion/' in lower_url or url.startswith('tor://')) and 'tor' in self._transports:
            return 'tor'

        return 'clearnet'

    def is_transport_available(self, transport: str) -> bool:
        """Check if transport is available."""
        return transport in self._transports

    def get_enabled_transports(self) -> list[str]:
        """Get list of enabled transport names."""
        return list(self._transports.keys())

    def get_transport_stats(self) -> dict[str, Any]:
        """Get statistics for all transports."""
        stats = {}
        for name, transport in self._transports.items():
            if transport is None:
                stats[name] = {'available': True, 'type': 'clearnet'}
            elif hasattr(transport, 'get_stats'):
                stats[name] = transport.get_stats()
            else:
                stats[name] = {'available': True}
        return stats
