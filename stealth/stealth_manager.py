"""
StealthManager - Complete Stealth System for Universal

Integrates all stealth components from stealth_toolkit:
- RateLimiter: Token bucket with adaptive throttling
- HeaderSpoofer: HTTP header rotation
- FingerprintRandomizer: Browser fingerprint randomization
- BehaviorSimulator: Human-like behavior (from layers/stealth_layer)

Provides unified stealth interface for research operations.

Migrated from: hledac/stealth_toolkit/
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import aiohttp

# Sprint 80: curl_cffi per-profil sessions
try:
    from curl_cffi.requests import AsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False
    AsyncSession = None

# Sprint 80: Per-profil sessions profiles
_IMPERSONATE_PROFILES = ["chrome120", "safari17_0"]

# Import from universal (internal)
from ..intelligence.stealth_crawler import HeaderConfig, HeaderSpoofer
from ..layers.stealth_layer import BrowserProfile, FingerprintConfig, FingerprintRandomizer
from ..utils.rate_limiter import RateLimitConfig, RateLimiter, RateLimitExceeded

logger = logging.getLogger(__name__)

# Sprint F206BE: Phase 2 Circuit Breaker Seam — Verdict constants
STEALTH_MANAGER_TRANSPORT_AUTHORITY = "local_stealth_pool_until_transport_unified"
STEALTH_MANAGER_PHASE = "phase2_breaker_seam"

# Sprint F206BA: Hard caps (explicit bounds — do NOT increase)
MAX_STEALTH_PROFILES = len(_IMPERSONATE_PROFILES)  # 2 profiles
MAX_CLIENTS_PER_PROFILE = 15  # per AsyncSession max_clients
MAX_SESSION_POOL_SIZE = 5  # _max_sessions LRU bound
MAX_ETAG_CACHE_ENTRIES = 500  # BoundedHostState(maxlen=500)
MAX_HOST_TELEMETRY_ENTRIES = 500  # BoundedHostState(maxlen=500)
# Estimated max connections: MAX_SESSION_POOL_SIZE × MAX_CLIENTS_PER_PROFILE = 5 × 15 = 75
ESTIMATED_MAX_CONNECTIONS = MAX_SESSION_POOL_SIZE * MAX_CLIENTS_PER_PROFILE  # 75

# M1 8GB: Hard limity pro HTTP fetchování
DEFAULT_MAX_BYTES = 256 * 1024  # 256KB preview limit
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_TOTAL_TIMEOUT = 60.0

# Retry configuration
RETRY_TRANSIENT_STATUSES = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_ATTEMPTS = 3
BASE_RETRY_DELAY = 1.0  # seconds
RETRY_JITTER_PCT = 0.20  # +/- 20%

# TCPConnector settings
TCP_TTL_DNS_CACHE = 300
TCP_LIMIT = 20
TCP_LIMIT_PER_HOST = 4
TCP_KEEPALIVE_TIMEOUT = 30


@dataclass
class StealthManagerConfig:
    """Configuration for complete stealth system"""
    # Enable/disable components
    enable_rate_limiter: bool = True
    enable_header_spoofer: bool = True
    enable_fingerprint_randomizer: bool = True

    # Component configs
    rate_limit_config: RateLimitConfig | None = None
    header_config: HeaderConfig | None = None
    fingerprint_config: FingerprintConfig | None = None

    # Global settings
    auto_rotate: bool = True
    rotation_interval: int = 100  # requests
    safety_mode: bool = True  # Extra cautious


class StealthManager:
    """
    Complete stealth system for research operations.

    Integrates rate limiting, header rotation, fingerprint randomization,
    and behavior simulation for comprehensive stealth.

    Example:
        >>> stealth = StealthManager()
        >>> async with stealth.session() as session:
        ...     headers = session.get_headers()
        ...     await session.request('https://example.com')
    """

    def __init__(self, config: StealthManagerConfig | None = None):
        self.config = config or StealthManagerConfig()

        # Initialize components
        self.rate_limiter: RateLimiter | None = None
        self.header_spoofer: HeaderSpoofer | None = None
        self.fingerprint_randomizer: FingerprintRandomizer | None = None

        if self.config.enable_rate_limiter:
            cfg = self.config.rate_limit_config
            if cfg is None:
                rate, capacity = 10.0, 30
            else:
                rate = getattr(cfg, "base_rate", 10.0)
                capacity = getattr(cfg, "burst_size", 30)
            self.rate_limiter = RateLimiter(rate=rate, capacity=capacity)

        if self.config.enable_header_spoofer:
            self.header_spoofer = HeaderSpoofer(self.config.header_config)

        if self.config.enable_fingerprint_randomizer:
            self.fingerprint_randomizer = FingerprintRandomizer(
                self.config.fingerprint_config
            )

        # Sprint 80: Per-profil sessions (LRU cache)
        self._sessions: Ordereddict[str, AsyncSession] = OrderedDict()
        self._max_sessions = 5
        self._profile_index = 0
        self._sessions_lock = asyncio.Lock()

        # Sprint 80: Bounded host state (LRU)
        self._hosts: BoundedHostState = BoundedHostState(maxlen=500)

        # Sprint 80: ETag cache
        self._cache: BoundedHostState = BoundedHostState(maxlen=500)
        self._cache_ttl = 300
        self._cache_lock = asyncio.Lock()

        # Sprint 80: Token bucket concurrency
        self._concurrency = TokenBucketController(rate=5, capacity=10)

        # Statistics
        self._request_count = 0
        self._success_count = 0
        self._failure_count = 0
        self._domain_stats: dict[str, dict[str, Any]] = {}

        # Sprint F206BE: Circuit breaker telemetry
        self._cb_blocks = 0
        self._cb_fallbacks = 0
        self._cb_last_reason: str | None = None
        self._cb_available: bool | None = None  # lazily determined

    async def initialize(self) -> bool:
        """Initialize stealth manager"""
        logger.info("Initializing StealthManager...")

        try:
            # Components are initialized on creation
            logger.info("✓ StealthManager initialized")
            return True
        except Exception as e:
            logger.warning(f"Stealth initialization failed: {e}")
            return False

    def get_headers(
        self,
        domain: str = 'default',
        content_type: str = 'html',
        preserve: dict[str, str] | None = None
    ) -> dict[str, str]:
        """
        Get stealth headers for request.

        Args:
            domain: Target domain
            content_type: Type of content
            preserve: Headers to preserve

        Returns:
            Stealth HTTP headers
        """
        if not self.header_spoofer:
            return {}

        headers = self.header_spoofer.get_headers(
            content_type=content_type,
            preserve=preserve
        )

        # Maybe rotate
        self._request_count += 1
        if (self.config.auto_rotate and
            self._request_count % self.config.rotation_interval == 0):
            logger.info(f"Auto-rotating stealth profile (request #{self._request_count})")
            if self.fingerprint_randomizer:
                self.fingerprint_randomizer.rotate()
            if self.header_spoofer:
                self.header_spoofer.rotate()

        return headers

    async def acquire_rate_limit(self, domain: str = 'default') -> bool:
        """Acquire rate limit permission"""
        if self.rate_limiter:
            return await self.rate_limiter.acquire(domain)
        return True

    async def execute(
        self,
        coro: Callable,
        domain: str = 'default',
        timeout: float | None = None
    ) -> Any:
        """
        Execute request with full stealth protection.

        Args:
            coro: Coroutine to execute
            domain: Target domain
            timeout: Request timeout

        Returns:
            Result of coroutine
        """
        # Apply rate limiting
        if self.rate_limiter:
            try:
                await self.rate_limiter.acquire(domain)
            except RateLimitExceeded:
                logger.warning(f"Rate limit exceeded for {domain}")
                raise

        # Execute
        try:
            if timeout:
                result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                result = await coro

            self._success_count += 1

            # Update domain stats
            if domain not in self._domain_stats:
                self._domain_stats[domain] = {
                    'requests': 0,
                    'success': 0,
                    'failure': 0
                }
            self._domain_stats[domain]['requests'] += 1
            self._domain_stats[domain]['success'] += 1

            return result

        except Exception as e:
            self._failure_count += 1

            if domain in self._domain_stats:
                self._domain_stats[domain]['failure'] += 1

            # In safety mode, back off on failure
            if self.config.safety_mode and self.rate_limiter:
                logger.warning(f"Request failed, backing off: {e}")
                await asyncio.sleep(2.0)

            raise

    @asynccontextmanager
    async def session(self):
        """
        Create stealth session context.

        Yields:
            StealthSession object
        """
        session = StealthSession(self)
        try:
            yield session
        finally:
            await session.close()

    def get_js_protection(self) -> str:
        """Get JavaScript fingerprint protection"""
        if self.fingerprint_randomizer:
            return self.fingerprint_randomizer.get_js_protection_script()
        return ''

    def get_browser_profile(self) -> BrowserProfile | None:
        """Get current browser fingerprint profile"""
        if self.fingerprint_randomizer:
            return self.fingerprint_randomizer.get_profile()
        return None

    def rotate_all(self):
        """Force rotation of all stealth components"""
        if self.header_spoofer:
            self.header_spoofer.rotate()
        if self.fingerprint_randomizer:
            self.fingerprint_randomizer.rotate()
        logger.info("All stealth components rotated")

    def get_statistics(self) -> dict[str, Any]:
        """Get comprehensive stealth statistics"""
        stats = {
            'requests_total': self._request_count,
            'success_count': self._success_count,
            'failure_count': self._failure_count,
            'success_rate': (
                self._success_count / self._request_count
                if self._request_count > 0 else 1.0
            ),
            'domain_stats': self._domain_stats,
            'components': {
                'rate_limiter': self.rate_limiter is not None,
                'header_spoofer': self.header_spoofer is not None,
                'fingerprint_randomizer': self.fingerprint_randomizer is not None,
            }
        }

        if self.rate_limiter:
            stats['rate_limits'] = {'tokens': self.rate_limiter.available_tokens}

        if self.header_spoofer:
            stats['headers'] = self.header_spoofer.get_statistics()

        if self.fingerprint_randomizer:
            stats['fingerprint'] = self.fingerprint_randomizer.get_statistics()

        return stats

    def get_stealth_transport_telemetry(self) -> dict[str, Any]:
        """
        Sprint F206BA Phase 1: Get truthful telemetry about stealth transport layer.

        Returns bounded telemetry WITHOUT altering transport behavior.
        No live network calls. No session creation.
        """
        # Session pool telemetry
        session_count = len(self._sessions) if hasattr(self, '_sessions') else 0
        max_sessions = getattr(self, '_max_sessions', MAX_SESSION_POOL_SIZE)

        # Cache telemetry (BoundedHostState)
        cache_count = len(self._cache) if hasattr(self, '_cache') and isinstance(self._cache, dict) else 0

        # Estimated cache bytes (rough: avg 50KB per text entry)
        estimated_cache_bytes = cache_count * 50 * 1024

        # M1 memory risk assessment
        # ETag cache stores full response text — at 500 entries × 50KB avg = ~25MB
        # This is bounded by BoundedHostState(maxlen=500) — eviction always available
        m1_memory_risk = "medium" if estimated_cache_bytes > 20 * 1024 * 1024 else "low"

        return {
            "phase": STEALTH_MANAGER_PHASE,
            "transport_authority": STEALTH_MANAGER_TRANSPORT_AUTHORITY,
            "profile_count": MAX_STEALTH_PROFILES,
            "max_clients_per_profile": MAX_CLIENTS_PER_PROFILE,
            "session_pool_size_current": session_count,
            "session_pool_size_max": max_sessions,
            "estimated_max_connections": ESTIMATED_MAX_CONNECTIONS,
            "circuit_breaker_used": False,  # Not integrated yet — Phase 2
            "canonical_curl_runtime_used": False,  # FetchCoordinator not wired — later sprint
            "cache_entry_count": cache_count,
            "cache_entries_max": MAX_ETAG_CACHE_ENTRIES,
            "estimated_cache_bytes": estimated_cache_bytes,
            "m1_memory_risk": m1_memory_risk,
            "fallback_reason": "stealth_manager has independent session pool; not wired to FetchCoordinator transport seam",
            # Constants for verification
            "MAX_STEALTH_PROFILES": MAX_STEALTH_PROFILES,
            "MAX_CLIENTS_PER_PROFILE": MAX_CLIENTS_PER_PROFILE,
            "MAX_SESSION_POOL_SIZE": MAX_SESSION_POOL_SIZE,
            "MAX_ETAG_CACHE_ENTRIES": MAX_ETAG_CACHE_ENTRIES,
            # Sprint F206BE: Circuit breaker telemetry
            "circuit_breaker_used": self._cb_available is True,
            "circuit_breaker_blocks": self._cb_blocks,
            "circuit_breaker_fallbacks": self._cb_fallbacks,
            "last_circuit_breaker_reason": self._cb_last_reason,
        }

    def _stealth_domain_allowed(self, url_or_domain: str) -> tuple[bool, str | None]:
        """
        Sprint F206BE: Check if domain is allowed by circuit breaker.

        Lazily imports transport.circuit_breaker.domain_breaker_check.
        Fail-soft: returns (True, None) if breaker unavailable or on error.
        No network calls. No session creation at import time.

        Returns:
            (allowed: bool, reason: str | None)
            - (True, None) = allowed to proceed
            - (False, reason) = blocked by circuit breaker
        """
        # Extract domain from URL if needed
        domain = url_or_domain
        try:
            parsed = urlparse(url_or_domain)
            if parsed.netloc:
                domain = parsed.netloc
                # Strip tor-portal prefix
                if domain.startswith("tor:"):
                    domain = domain[4:]
            elif parsed.path and ".onion" in parsed.path:
                domain = parsed.path
        except Exception:
            pass

        if not domain:
            return (True, None)  # skip check for empty

        # Lazily determine breaker availability and import
        if self._cb_available is None:
            try:
                from transport.circuit_breaker import domain_breaker_check
                self._cb_available = True
            except ImportError:
                self._cb_available = False
                return (True, None)  # fail-soft: breaker unavailable

        if not self._cb_available:
            return (True, None)  # fail-soft: breaker not available

        # Perform the check
        try:
            from transport.circuit_breaker import domain_breaker_check
            decision = domain_breaker_check(domain)
            if decision.allowed:
                return (True, None)
            else:
                self._cb_blocks += 1
                self._cb_last_reason = decision.reason
                return (False, decision.reason)
        except Exception:
            # Fail-soft: any error means allow
            self._cb_fallbacks += 1
            return (True, None)

    async def close(self):
        """Cleanup resources"""
        logger.info("Closing StealthManager...")
        # Sprint 80: Cleanup per-profil sessions
        if hasattr(self, '_sessions'):
            for session in self._sessions.values():
                try:
                    if hasattr(session, 'aclose'):
                        await session.aclose()
                except Exception as e:
                    logger.debug(f"Failed to close session during cleanup: {e}")
            self._sessions.clear()
        logger.info("✓ StealthManager closed")


class SkipFetch(Exception):
    """
    Sprint F206BE: Raised when circuit breaker blocks a fetch.

    This is NOT a network error — it indicates the circuit breaker
    prevented the attempt. Fail-soft callers should catch and handle
    without incrementing failure counters.
    """
    pass


@dataclass
class StealthResponse:
    """Response from stealth HTTP request - M1 8GB optimized (no large bodies in RAM)."""
    status: int
    final_url: str
    headers: dict[str, str]
    body_bytes: bytes
    content_type: str | None = None
    fetched_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    truncated: bool = False  # True pokud byl obsah uříznut max_bytes limitem

    def text_preview(self, max_chars: int = 5000) -> str:
        """Vrátí textový preview - dekóduje jen potřebnou část pro RAM šetření."""
        try:
            # Zkus UTF-8
            text = self.body_bytes[:max_chars * 2].decode('utf-8', errors='ignore')
            if len(text) > max_chars:
                return text[:max_chars] + "..."
            return text
        except Exception:
            return ""

    @property
    def success(self) -> bool:
        """True pokud je status 2xx."""
        return 200 <= self.status < 300


class StealthSession:
    """
    Stealth session for making real HTTP requests with M1 8GB optimization.

    Features:
    - Shared aiohttp.ClientSession for connection pooling
    - Streaming read s hard limitem max_bytes (žádné velké stringy v RAM)
    - Timeouty pro connect/read/total
    - Automatic cookie handling
    """

    def __init__(self, manager: StealthManager):
        self.manager = manager
        self._cookies: dict[str, str] = {}
        self._session: aiohttp.ClientSession | None = None
        self._closed = False
        # HTTP/3 autodetection cache: domain -> (timestamp, supported)
        self._http3_cache: dict[str, tuple[float, bool]] = {}
        # P7: Request counter for Tor identity rotation (every 10 requests)
        self._request_count = 0

    async def _supports_http3(self, url: str) -> bool:
        """Check if server supports HTTP/3 by looking for Alt-Svc header. Cache 24h."""
        domain = urlparse(url).netloc
        now = time.time()
        HTTP3_CACHE_TTL = 86400  # 24 hours

        # Check cache with TTL
        if domain in self._http3_cache:
            cached_time, supported = self._http3_cache[domain]
            if now - cached_time < HTTP3_CACHE_TTL:
                return supported

        # Detect HTTP/3 support
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=2.0)) as resp:
                    alt_svc = resp.headers.get("Alt-Svc", "")
                    supports_http3 = "h3" in alt_svc.lower()
                    self._http3_cache[domain] = (now, supports_http3)
                    if supports_http3:
                        logger.debug(f"HTTP/3 supported for {domain}")
                    return supports_http3
        except Exception as e:
            logger.debug(f"HTTP/3 detection failed for {domain}: {e}")
            self._http3_cache[domain] = (now, False)
            return False

    async def _http3_request(self, method: str, url: str, headers: dict | None = None) -> bytes | None:
        """Make HTTP/3 request using aioquic (if available)."""
        try:
            from aioquic.asyncio import connect
            from aioquic.h3.connection import H3Connection
            from aioquic.quic.configuration import QuicConfiguration

            parsed = urlparse(url)
            host = parsed.netloc.split(':')[0]
            port = parsed.port or 443

            configuration = QuicConfiguration(is_client=True)
            async with connect(host, port, configuration=configuration, create_protocol=H3Connection) as protocol:
                # Simple request - just get the response body
                request_headers = [(b":method", method.upper().encode()),
                                  (b":path", parsed.path or b"/"),
                                  (b":authority", host.encode())]
                if headers:
                    for k, v in headers.items():
                        request_headers.append((k.encode(), v.encode()))

                stream_id = protocol.make_request(request_headers)
                # Čekat na response
                await protocol.wait_for_response(stream_id)
                # Získat data
                data = await protocol.receive_data(stream_id)
                return data
        except ImportError:
            logger.debug("aioquic not available for HTTP/3")
            return None
        except Exception as e:
            logger.debug(f"HTTP/3 request failed: {e}")
            return None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Lazy initialization of shared ClientSession with TCP tuning."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                connect=DEFAULT_CONNECT_TIMEOUT,
                sock_read=DEFAULT_READ_TIMEOUT,
                total=DEFAULT_TOTAL_TIMEOUT
            )
            connector = aiohttp.TCPConnector(
                ttl_dns_cache=TCP_TTL_DNS_CACHE,
                limit=TCP_LIMIT,
                limit_per_host=TCP_LIMIT_PER_HOST,
                keepalive_timeout=TCP_KEEPALIVE_TIMEOUT,
                enable_cleanup_closed=True
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                cookie_jar=aiohttp.CookieJar(),
                connector=connector
            )
        return self._session

    def get_headers(self, domain: str = 'default') -> dict[str, str]:
        """Get headers for request"""
        preserve = {}
        if self._cookies:
            preserve['Cookie'] = '; '.join(
                f"{k}={v}" for k, v in self._cookies.items()
            )

        return self.manager.get_headers(domain, preserve=preserve)

    def update_cookies(self, cookies: dict[str, str]):
        """Update session cookies"""
        self._cookies.update(cookies)

    def _is_transient_error(self, status: int, exception: Exception | None = None) -> bool:
        """Check if error is transient and should be retried."""
        if status in RETRY_TRANSIENT_STATUSES:
            return True
        # Network errors that indicate transient issues
        if exception is not None:
            error_str = str(exception).lower()
            transient_network_errors = [
                'connection reset', 'connection refused', 'broken pipe',
                'temporary failure', 'name resolution', 'dns',
                'connect timeout', 'read timeout'
            ]
            return any(err in error_str for err in transient_network_errors)
        return False

    def _calculate_retry_delay(self, attempt: int, retry_after: str | None = None) -> float:
        """Calculate retry delay with exponential backoff and jitter."""
        # Respect Retry-After header if present
        if retry_after is not None:
            try:
                # Retry-After can be seconds or HTTP date
                delay = float(retry_after)
                return delay
            except (ValueError, TypeError):
                pass  # Fall back to exponential backoff

        # Exponential backoff: 1s, 2s, 4s
        base_delay = BASE_RETRY_DELAY * (2 ** attempt)

        # Add jitter: +/- 20%
        jitter = base_delay * RETRY_JITTER_PCT * (2 * random.random() - 1)

        return base_delay + jitter

    async def request(
        self,
        method: str,
        url: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        allow_redirects: bool = True,
        headers: dict[str, str] | None = None,
        data: Any = None,
        **kwargs
    ) -> StealthResponse:
        """
        Make real stealth HTTP request with M1 8GB constraints and retry policy.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Target URL
            max_bytes: Maximum bytes to read (default 256KB for preview)
            allow_redirects: Follow redirects
            headers: Additional headers
            data: Request body

        Returns:
            StealthResponse with truncated body (never exceeds max_bytes)
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        domain = urlparse(url).netloc or 'default'
        last_exception: Exception | None = None

        # Sprint 50: HTTP/3 autodetection - try HTTP/3 first for GET requests
        if method.upper() == "GET":
            if await self._supports_http3(url):
                http3_body = await self._http3_request(method, url, headers)
                if http3_body is not None:
                    # HTTP/3 successful - return response
                    body_bytes = http3_body[:max_bytes]
                    truncated = len(http3_body) > max_bytes
                    return StealthResponse(
                        status=200,
                        final_url=url,
                        headers={"X-Protocol": "HTTP/3"},
                        body_bytes=body_bytes,
                        content_type='application/octet-stream',
                        truncated=truncated
                    )
                # HTTP/3 failed - fall through to aiohttp

        # Sprint F206BE: Circuit breaker preflight — fail-soft, not a network error
        allowed, reason = self.manager._stealth_domain_allowed(url)
        if not allowed:
            raise SkipFetch(f"circuit_breaker_open:{reason}")

        for attempt in range(MAX_RETRY_ATTEMPTS):
            # P7: Human-like jitter before each request (anti-correlation)
            await asyncio.sleep(random.uniform(0.3, 1.8))

            # P7: Tor identity rotation every 10 requests
            self._request_count += 1
            if self._request_count >= 10:
                self._request_count = 0
                await self._rotate_tor_identity()

            # Rate limiting (only on first attempt, backoff handles retries)
            if attempt == 0 and self.manager.rate_limiter:
                try:
                    await self.manager.rate_limiter.acquire()
                except RateLimitExceeded:
                    logger.warning(f"Rate limit exceeded for {domain}")
                    raise

            # Prepare headers
            stealth_headers = self.get_headers(domain)
            if headers:
                stealth_headers.update(headers)

            logger.debug(f"Stealth {method} request to {url} (attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS}, max_bytes={max_bytes})")

            try:
                session = await self._get_session()

                async with session.request(
                    method=method.upper(),
                    url=url,
                    headers=stealth_headers,
                    allow_redirects=allow_redirects,
                    data=data,
                    **kwargs
                ) as response:

                    # Check for transient error status codes
                    if self._is_transient_error(response.status) and attempt < MAX_RETRY_ATTEMPTS - 1:
                        retry_after = response.headers.get('Retry-After')
                        delay = self._calculate_retry_delay(attempt, retry_after)
                        logger.warning(f"Transient error {response.status}, retrying in {delay:.2f}s (attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS})")
                        await asyncio.sleep(delay)
                        continue

                    # Read content s limitem - streaming pro RAM šetření
                    # F196B: Use bytearray.extend() — O(1) amortized vs bytes += O(n²)
                    body_bytes = bytearray()
                    truncated = False

                    if response.content:
                        chunk_size = min(8192, max_bytes)  # 8KB chunks
                        remaining = max_bytes

                        async for chunk in response.content.iter_chunked(chunk_size):
                            if len(chunk) > remaining:
                                body_bytes.extend(chunk[:remaining])
                                truncated = True
                                logger.debug(f"Response truncated at {max_bytes} bytes")
                                break
                            body_bytes.extend(chunk)
                            remaining -= len(chunk)

                            if remaining <= 0:
                                truncated = True
                                break

                    # Update cookies from response
                    if response.cookies:
                        for name, cookie in response.cookies.items():
                            self._cookies[name] = cookie.value

                    result = StealthResponse(
                        status=response.status,
                        final_url=str(response.url),
                        headers=dict(response.headers),
                        body_bytes=body_bytes,
                        content_type=response.headers.get('Content-Type'),
                        truncated=truncated
                    )

                    # Update stats
                    self.manager._request_count += 1
                    if result.success:
                        self.manager._success_count += 1
                    else:
                        self.manager._failure_count += 1

                    logger.debug(f"Request completed: {response.status} ({len(body_bytes)} bytes)")
                    return result

            except TimeoutError as e:
                last_exception = e
                if attempt < MAX_RETRY_ATTEMPTS - 1 and self._is_transient_error(0, e):
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(f"Timeout error, retrying in {delay:.2f}s (attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS})")
                    await asyncio.sleep(delay)
                    continue
                logger.warning(f"Request timeout: {url}")
                self.manager._failure_count += 1
                raise

            except Exception as e:
                last_exception = e
                if attempt < MAX_RETRY_ATTEMPTS - 1 and self._is_transient_error(0, e):
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(f"Transient error {e}, retrying in {delay:.2f}s (attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS})")
                    await asyncio.sleep(delay)
                    continue
                logger.warning(f"Request failed: {e}")
                self.manager._failure_count += 1
                raise

        # All retries exhausted
        if last_exception:
            raise last_exception
        raise RuntimeError(f"Request failed after {MAX_RETRY_ATTEMPTS} attempts")

    async def get(
        self,
        url: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        **kwargs
    ) -> StealthResponse:
        """Convenience method for GET requests."""
        return await self.request('GET', url, max_bytes=max_bytes, **kwargs)

    async def post(
        self,
        url: str,
        data: Any = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        **kwargs
    ) -> StealthResponse:
        """Convenience method for POST requests."""
        return await self.request('POST', url, data=data, max_bytes=max_bytes, **kwargs)

    async def head(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float | None = None
    ) -> tuple[int, dict[str, str], str]:
        """
        Lightweight HEAD request with redirect following.

        Args:
            url: Target URL
            headers: Additional headers
            timeout: Request timeout override

        Returns:
            Tuple of (status_code, response_headers, final_url)
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        domain = urlparse(url).netloc or 'default'

        # Rate limiting
        if self.manager.rate_limiter:
            try:
                await self.manager.rate_limiter.acquire()
            except RateLimitExceeded:
                logger.warning(f"Rate limit exceeded for {domain}")
                raise

        # Prepare headers
        stealth_headers = self.get_headers(domain)
        if headers:
            stealth_headers.update(headers)

        logger.debug(f"Stealth HEAD request to {url}")

        try:
            session = await self._get_session()

            # Use custom timeout if provided
            req_kwargs = {}
            if timeout is not None:
                req_kwargs['timeout'] = aiohttp.ClientTimeout(total=timeout)

            async with session.head(
                url=url,
                headers=stealth_headers,
                allow_redirects=True,
                **req_kwargs
            ) as response:
                self.manager._request_count += 1

                if 200 <= response.status < 300:
                    self.manager._success_count += 1
                else:
                    self.manager._failure_count += 1

                return (
                    response.status,
                    dict(response.headers),
                    str(response.url)
                )

        except TimeoutError:
            logger.warning(f"HEAD request timeout: {url}")
            self.manager._failure_count += 1
            raise
        except Exception as e:
            logger.warning(f"HEAD request failed: {e}")
            self.manager._failure_count += 1
            raise

    async def get_preview(
        self,
        url: str,
        max_bytes: int = DEFAULT_MAX_BYTES,
        range_bytes: int = 65536,
        **kwargs
    ) -> dict[str, Any]:
        """
        Fetch partial content with Range header for preview.

        Args:
            url: Target URL
            max_bytes: Hard maximum bytes to read (safety limit)
            range_bytes: Bytes to request in Range header (0 to range_bytes-1)
            **kwargs: Additional request options

        Returns:
            Dict with: body_bytes (truncated), headers, final_url, status
        """
        if self._closed:
            raise RuntimeError("Session is closed")

        domain = urlparse(url).netloc or 'default'

        # Rate limiting
        if self.manager.rate_limiter:
            try:
                await self.manager.rate_limiter.acquire()
            except RateLimitExceeded:
                logger.warning(f"Rate limit exceeded for {domain}")
                raise

        # Prepare headers with Range
        stealth_headers = self.get_headers(domain)
        if range_bytes > 0:
            stealth_headers['Range'] = f'bytes=0-{range_bytes - 1}'

        # Merge additional headers from kwargs
        extra_headers = kwargs.pop('headers', None)
        if extra_headers:
            stealth_headers.update(extra_headers)

        logger.debug(f"Stealth GET preview request to {url} (range=0-{range_bytes - 1})")

        try:
            session = await self._get_session()

            async with session.get(
                url=url,
                headers=stealth_headers,
                allow_redirects=True,
                **kwargs
            ) as response:
                # Streaming read with hard max_bytes limit
                # F196A: Use bytearray.extend() — O(1) amortized vs bytes += O(n²)
                body_bytes = bytearray()
                truncated = False

                if response.content:
                    chunk_size = min(8192, max_bytes)
                    remaining = max_bytes

                    async for chunk in response.content.iter_chunked(chunk_size):
                        if len(chunk) > remaining:
                            body_bytes.extend(chunk[:remaining])
                            truncated = True
                            logger.debug(f"Preview truncated at {max_bytes} bytes")
                            break
                        body_bytes.extend(chunk)
                        remaining -= len(chunk)

                        if remaining <= 0:
                            truncated = True
                            break

                # Update cookies from response
                if response.cookies:
                    for name, cookie in response.cookies.items():
                        self._cookies[name] = cookie.value

                # Update stats
                self.manager._request_count += 1
                if 200 <= response.status < 300 or response.status == 206:  # 206 = Partial Content
                    self.manager._success_count += 1
                else:
                    self.manager._failure_count += 1

                return {
                    'body_bytes': body_bytes,
                    'headers': dict(response.headers),
                    'final_url': str(response.url),
                    'status': response.status,
                    'truncated': truncated
                }

        except TimeoutError:
            logger.warning(f"GET preview timeout: {url}")
            self.manager._failure_count += 1
            raise
        except Exception as e:
            logger.warning(f"GET preview failed: {e}")
            self.manager._failure_count += 1
            raise

    # ========================================================================
    # P7: Tor identity rotation via STEM controller
    # ========================================================================

    async def _rotate_tor_identity(self) -> None:
        """
        Request new Tor circuit via STEM controller.
        Called every 10 requests when Tor is active.

        Security: Relies on localhost-only Tor control port + cookie authentication.
        Tor MUST be configured with either:
        - CookieAuthSocket (default on macOS) - requires filesystem access to cookie file
        - HashedControlPassword - requires stem authenticate(password=...) call
        NEVER expose the Tor control port to non-localhost without password auth.
        """
        try:
            from stem import Signal
            from stem.control import Controller
            from stem.protocol import ProtocolError
            # Explicitly connect to localhost to avoid accidental exposure
            with Controller.from_port(address="127.0.0.1", port=9051) as controller:
                try:
                    controller.authenticate()
                except ProtocolError as e:
                    if "authentication failed" in str(e).lower():
                        logger.warning(
                            "Tor authentication failed. Ensure torrc config uses "
                            "CookieAuthSocket or HashedControlPassword and the control "
                            "port is accessible."
                        )
                    raise
                controller.signal(Signal.NEWNYM)
                logger.debug("Tor identity rotated via NEWNYM signal")
                await asyncio.sleep(1)  # Wait for new circuit
        except ImportError:
            logger.debug("stem library not available for Tor rotation")
        except Exception as e:
            logger.warning(f"Tor identity rotation failed: {e}")

    async def close(self):
        """Close session and cleanup."""
        self._closed = True
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        logger.debug("StealthSession closed")


# =============================================================================
# Sprint 80: Helper Classes
# =============================================================================

class BoundedHostState(OrderedDict):
    """LRU bounded dictionary s maxlen."""

    def __init__(self, maxlen: int = 500):
        super().__init__()
        self._maxlen = maxlen

    def __setitem__(self, key, val):
        super().__setitem__(key, val)
        if len(self) > self._maxlen:
            self.popitem(last=False)


class HostTelemetry:
    """Host telemetry pro backoff a retry rozhodování."""

    __slots__ = ('semaphore', 'errors', 'latencies', 'last_success', 'last_error')

    def __init__(self, semaphore: asyncio.Semaphore):
        self.semaphore = semaphore
        self.errors = 0
        self.latencies = []
        self.last_success = time.time()
        self.last_error = 0.0


class TokenBucketController:
    """Token Bucket pro řízení concurrency."""

    def __init__(self, rate: int = 5, capacity: int = 10):
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.time()
        self._cond = asyncio.Condition()

    async def acquire(self):
        async with self._cond:
            while True:
                now = time.time()
                elapsed = now - self._last_refill
                new_tokens = int(elapsed * self._rate)
                if new_tokens > 0:
                    self._tokens = min(self._capacity, self._tokens + new_tokens)
                    self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._cond.wait()

    async def release(self):
        pass


# =============================================================================
# Sprint 80: StealthManager Extensions
# =============================================================================

class StealthManagerExtensions:
    """Rozšíření StealthManager o per-profil sessions a ETag cache."""

    async def _get_session(self, profile: str) -> AsyncSession:
        """Získat nebo vytvořit session pro profil."""
        async with self._sessions_lock:
            if profile in self._sessions:
                self._sessions.move_to_end(profile)
                return self._sessions[profile]

            if len(self._sessions) >= self._max_sessions:
                oldest_profile, oldest_session = self._sessions.popitem(last=False)
                try:
                    await oldest_session.aclose()
                except Exception as e:
                    logger.debug(f"Failed to close oldest session for profile {oldest_profile}: {e}")

            if CURL_CFFI_AVAILABLE and AsyncSession:
                new_session = AsyncSession(
                    impersonate=profile,
                    timeout=10.0,
                    max_clients=15
                )
            else:
                raise RuntimeError("curl_cffi not available")

            self._sessions[profile] = new_session
            return new_session

    def _next_profile(self) -> str:
        """Rotace profilu."""
        p = _IMPERSONATE_PROFILES[self._profile_index % len(_IMPERSONATE_PROFILES)]
        self._profile_index += 1
        return p

    async def _get_host_telemetry(self, host: str) -> HostTelemetry:
        """Získat telemetry pro host."""
        if host not in self._hosts:
            sem = asyncio.Semaphore(2)
            self._hosts[host] = HostTelemetry(sem)
        self._hosts.move_to_end(host)
        return self._hosts[host]

    async def get_with_cache(self, url: str, **kwargs) -> str:
        """GET s ETag/Last-Modified cache."""
        parsed_url = urlparse(url)
        domain = parsed_url.netloc

        async with self._cache_lock:
            if url in self._cache:
                text, ts, etag, last_modified = self._cache[url]
                if time.time() - ts < self._cache_ttl:
                    return text

        ht = await self._get_host_telemetry(domain)

        if ht.errors > 0:
            backoff = min(60, 2 ** ht.errors)
            jitter = random.uniform(0.5, 1.5) * backoff
            await asyncio.sleep(jitter)

        await ht.semaphore.acquire()
        await self._concurrency.acquire()

        try:
            start = time.time()
            prof = self._next_profile()
            session = await self._get_session(prof)

            headers = {}
            async with self._cache_lock:
                if url in self._cache:
                    _, _, etag, last_modified = self._cache[url]
                    if etag:
                        headers['If-None-Match'] = etag
                    elif last_modified:
                        headers['If-Modified-Since'] = last_modified

            resp = await session.get(url, headers=headers, follow_redirects=True, **kwargs)

            if resp.status_code == 304:
                async with self._cache_lock:
                    text, ts, _, _ = self._cache[url]
                return text

            resp.raise_for_status()
            text = resp.text
            lat = time.time() - start

            ht.latencies.append(lat)
            if len(ht.latencies) > 100:
                ht.latencies = ht.latencies[-100:]
            ht.errors = 0
            ht.last_success = time.time()

            async with self._cache_lock:
                self._cache[url] = (text, time.time(), resp.headers.get('etag'), resp.headers.get('last-modified'))
            return text
        except Exception:
            ht.errors += 1
            ht.last_error = time.time()
            raise
        finally:
            await self._concurrency.release()
            ht.semaphore.release()


# Convenience function
async def with_stealth(
    coro,
    domain: str = 'default',
    config: StealthManagerConfig | None = None
):
    """
    Execute coroutine with stealth protection.

    Args:
        coro: Coroutine to execute
        domain: Target domain
        config: Stealth configuration

    Returns:
        Result of coroutine
    """
    stealth = StealthManager(config)
    try:
        return await stealth.execute(coro, domain)
    finally:
        await stealth.close()
