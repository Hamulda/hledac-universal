"""
Base fetcher abstraction for HTTP fetching.
Provides retry policy, timeout normalization, circuit breaker per fetcher.
M1 8GB safe: bounded retry loop, no unbounded growth.

Architecture:
- RetryPolicy: bounded exponential backoff, Retry-After support
- CircuitBreakerState/CircuitBreakerCache: per-domain circuit breaker, max 512 domains, LRU eviction
- BaseFetcher: ABC with retry loop, timeout normalization, stats
- CurlCFFIFetcher / AiohttpFetcher / HybridFetcher / TorCurlCFFIFetcher / I2PCurlCFFIFetcher / JsFetcher
- FetchRouter: central routing, converts to FetchResult

No circular deps: this module is independent of fetching/public_fetcher.py.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
import msgspec
from typing import Any, NamedTuple, Self

from hledac.universal.transport.url_utils import cached_urlparse

logger = logging.getLogger(__name__)
from hledac.universal.core.constants import HTTP
_RETRYABLE_STATUS_CODES: frozenset[int] = HTTP().retryable
_DEFAULT_TIMEOUT: float = 35.0
_TOR_TIMEOUT_SCALE: float = 2.0
_RETRY_BACKOFF_BASE: float = 1.5
_RETRY_BACKOFF_MAX: float = 8.0
_RETRY_MAX_ATTEMPTS: int = 3
_CIRCUIT_BREAKER_MAX_DOMAINS: int = 512
_CIRCUIT_BREAKER_FAILURE_THRESHOLD: int = 5
_CIRCUIT_BREAKER_RECOVERY_SECONDS: float = 30.0

# Crypto-safe jitter — F350M-R
_JITTER_RNG = secrets.SystemRandom()

class RetryPolicy(msgspec.Struct, frozen=True):
    """Bounded retry policy for fetch operations.

    M1 8GB: max_attempts=3 keeps memory bounded.
    Exponential backoff capped at 8s prevents thundering herd.
    NOTE: frozen=True ensures immutability — shared instances are safe.
    """
    max_attempts: int = _RETRY_MAX_ATTEMPTS
    backoff_base: float = _RETRY_BACKOFF_BASE
    backoff_max: float = _RETRY_BACKOFF_MAX
    retryable_statuses: frozenset[int] = _RETRYABLE_STATUS_CODES

    def is_retryable_status(self, status_code: int) -> bool:
        return status_code in self.retryable_statuses

    def compute_backoff(self, attempt: int, retry_after: float | None=None) -> float:
        """Compute bounded backoff in seconds.

        Uses Retry-After if available, otherwise exponential backoff capped at 8s.
        Attempt 0 = no backoff (first failure already counted).
        """
        if retry_after is not None and retry_after > 0:
            return min(retry_after, 60.0)
        backoff = min(self.backoff_base ** attempt, self.backoff_max)
        return backoff + _JITTER_RNG.uniform(0, 0.5)

    def extract_retry_after(self, headers: dict[str, str] | None) -> float | None:
        """Parse Retry-After header, return seconds or None."""
        if not headers:
            return None
        ra = headers.get('Retry-After') or headers.get('retry-after')
        if not ra:
            return None
        try:
            return float(ra)
        except (ValueError, TypeError):
            return None

class CircuitBreakerState(NamedTuple):
    """Per-domain circuit breaker state (immutable, functional update).

    M1 8GB: NamedTuple overhead negligible (~48 bytes vs msgspec.Struct ~56).
    Thread-safe: record_failure/success vrací novou instanci.
    """
    failure_count: int
    last_failure: float
    is_open: bool

    @classmethod
    def fresh(cls) -> Self:
        """Create a new circuit breaker in closed state."""
        return cls(failure_count=0, last_failure=0.0, is_open=False)

    def record_failure(self) -> Self:
        """Record a failure and return new state (functional update)."""
        new_count = self.failure_count + 1
        return CircuitBreakerState(  # type: ignore
            failure_count=new_count,
            last_failure=time.monotonic(),
            is_open=new_count >= _CIRCUIT_BREAKER_FAILURE_THRESHOLD,
        )

    def record_success(self) -> Self:
        """Record a success and return new state (resets circuit)."""
        return CircuitBreakerState(  # type: ignore
            failure_count=0,
            last_failure=self.last_failure,
            is_open=False,
        )

    def is_available(self) -> bool:
        """Check if circuit is closed (available for requests) — pure read-only."""
        if not self.is_open:
            return True
        if time.monotonic() - self.last_failure > _CIRCUIT_BREAKER_RECOVERY_SECONDS:
            return True  # recovery window elapsed — circuit can close
        return False


class CircuitBreakerCache:
    """Global circuit breaker cache per domain.

    M1 8GB: bounded OrderedDict, max 512 domains.
    Thread-safe: per-entry lock stored alongside state — single atomic dict operation.

    LRU eviction: move_to_end() on access preserves recency order.
    When full, popitem(last=False) evicts the least-recently-used domain.

    Invariant: _data key → (state, lock) — both stored together, never out of sync.
    """
    __slots__ = ('_data', '_lock')

    _data: OrderedDict[str, tuple[CircuitBreakerState, threading.Lock]]
    _lock: threading.Lock

    def __init__(self) -> None:
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get_breaker(self, domain: str) -> tuple[CircuitBreakerState, threading.Lock]:
        """Get circuit breaker state AND its lock for atomic read-modify-write.

        Returns tuple of (state, lock). Caller MUST hold the lock during updates.
        LRU: access refreshes recency — most-recently-used survives eviction.

        Pattern:
            state, lock = cache.get_breaker(domain)
            with lock:
                new_state = state.record_failure()
                cache.update_breaker(domain, new_state)
        """
        with self._lock:
            if domain in self._data:
                # LRU touch — promote to most-recently-used
                self._data.move_to_end(domain)
                return self._data[domain]
            # Evict LRU domain when at capacity
            if len(self._data) >= _CIRCUIT_BREAKER_MAX_DOMAINS:
                self._data.popitem(last=False)  # O(1) LRU eviction
            self._data[domain] = (CircuitBreakerState.fresh(), threading.Lock())
            return self._data[domain]

    def update_breaker(self, domain: str, new_state: CircuitBreakerState) -> None:
        """Atomically update breaker state (caller must hold domain lock)."""
        with self._lock:
            if domain in self._data:
                _old_state, lock = self._data[domain]
                self._data[domain] = (new_state, lock)

    def clear(self) -> None:
        """Clear all circuit breaker state (testing only)."""
        with self._lock:
            self._data.clear()


# Module-level singleton — shared across all BaseFetcher subclasses
_circuit_breaker_cache = CircuitBreakerCache()

class FetcherResult(msgspec.Struct, frozen=True):
    """Internal fetch result for base_fetcher.py abstraction.

    Independent of FetchResult (msgspec.Struct) to avoid circular deps.
    Fields are minimal: success, error, status, body, headers, url.
    """
    url: str
    success: bool
    error: str | None = None
    status: int | None = None
    body: bytes | str | None = None
    headers: dict[str, str] | None = None
    network_error_kind: str | None = None
    failure_stage: str | None = None

    @property
    def is_retryable(self) -> bool:
        if not self.success and self.status:
            return self.status in _RETRYABLE_STATUS_CODES
        return False

class BaseFetcher(ABC):
    """Abstract base class for HTTP fetchers.

    Provides:
    - Retry policy with bounded exponential backoff
    - Timeout normalization (Tor/I2P scaled ×2)
    - Per-domain circuit breaker
    - Statistics tracking

    M1 8GB: all operations are bounded, no unbounded growth.
    """
    DEFAULT_TIMEOUT: float = _DEFAULT_TIMEOUT
    TOR_TIMEOUT_SCALE: float = _TOR_TIMEOUT_SCALE

    def __init__(self, retry_policy: RetryPolicy | None=None, default_timeout: float=_DEFAULT_TIMEOUT):
        self.retry_policy = retry_policy or RetryPolicy()
        self.default_timeout = default_timeout
        self._stats: dict[str, int] = {'attempts': 0, 'successes': 0, 'failures': 0, 'retries': 0}

    def normalize_timeout(self, timeout_s: float | None, network: str='clearnet') -> float:
        """Normalize timeout based on network type.

        Args:
            timeout_s: Requested timeout or None for default
            network: 'clearnet', 'tor', 'i2p', or 'freenet'

        Returns:
            Normalized timeout in seconds
        """
        base = timeout_s if timeout_s is not None else self.default_timeout
        if network in ('tor', 'i2p', 'freenet'):
            return base * self.TOR_TIMEOUT_SCALE
        return base

    def get_circuit_breaker(self, url: str) -> tuple[CircuitBreakerState, threading.Lock]:
        """Get circuit breaker state AND lock for the domain of given URL.

        Thread-safe: caller MUST hold the returned lock when updating state.

        Pattern:
            state, lock = self.get_circuit_breaker(url)
            with lock:
                new_state = state.record_failure()
                CircuitBreakerRegistry.update_breaker(domain, new_state)
        """
        parsed = cached_urlparse(url)
        return _circuit_breaker_cache.get_breaker(parsed.netloc)

    @abstractmethod
    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        """Execute a single fetch attempt.

        Override in subclasses to implement specific HTTP client logic.

        Args:
            url: URL to fetch
            **kwargs: Additional fetcher-specific arguments

        Returns:
            FetcherResult with success/failure info
        """
        ...

    async def fetch_with_retry(self, url: str, timeout_s: float | None=None, network: str='clearnet', **kwargs) -> FetcherResult:
        """Execute fetch with retry policy.

        M1 8GB: max 3 attempts, bounded backoff.
        Thread-safe: circuit breaker uses ONE lock per request — the lock is
        acquired ONCE before the retry loop and held through all attempts to
        prevent race conditions where a second get_breaker() call could
        return a different lock object after domain eviction.

        Args:
            url: URL to fetch
            timeout_s: Requested timeout
            network: Network type for timeout scaling
            **kwargs: Additional arguments passed to fetch_once

        Returns:
            FetcherResult from final attempt
        """
        effective_timeout = self.normalize_timeout(timeout_s, network)
        parsed_url = cached_urlparse(url)
        domain = parsed_url.netloc
        last_result: FetcherResult | None = None

        # Single lock acquisition for the entire request — prevents:
        # 1. Domain eviction between is_available() check and record_failure()
        # 2. Two different get_breaker() calls returning different lock objects
        # 3. Lost updates when multiple threads retry the same domain
        breaker_state, lock = _circuit_breaker_cache.get_breaker(domain)

        with lock:
            for attempt in range(self.retry_policy.max_attempts):
                self._stats['attempts'] += 1

                if not breaker_state.is_available():
                    logger.debug(f'Circuit open for {url}, skipping')
                    return FetcherResult(url=url, success=False, error='circuit_breaker_blocked', network_error_kind='circuit_breaker', failure_stage='circuit_breaker')

                try:
                    # NOTE: The lock is held for the ENTIRE retry loop for this domain
                    # (including await fetch_once and await asyncio.sleep). This serialises
                    # retries for the SAME domain — but domains are independent, so
                    # concurrent requests to OTHER domains proceed unaffected.
                    # Other threads calling get_breaker(same domain) will block here
                    # until the loop exits or the domain's lock is released.
                    result = await self.fetch_once(url, timeout_s=effective_timeout, **kwargs)
                    last_result = result

                    if result.success:
                        breaker_state = breaker_state.record_success()
                        _circuit_breaker_cache.update_breaker(domain, breaker_state)
                        self._stats['successes'] += 1
                        return result

                    if not result.is_retryable:
                        breaker_state = breaker_state.record_failure()
                        _circuit_breaker_cache.update_breaker(domain, breaker_state)
                        self._stats['failures'] += 1
                        return result

                    # Retryable failure — record it and backoff
                    breaker_state = breaker_state.record_failure()
                    _circuit_breaker_cache.update_breaker(domain, breaker_state)

                    if attempt < self.retry_policy.max_attempts - 1:
                        self._stats['retries'] += 1
                        retry_after = self.retry_policy.extract_retry_after(result.headers)
                        backoff = self.retry_policy.compute_backoff(attempt, retry_after)
                        logger.debug(f'Retryable failure for {url}, backing off {backoff:.2f}s')
                        await asyncio.sleep(backoff)

                except Exception as e:
                    logger.warning(f'Fetch exception for {url}: {e}')
                    breaker_state = breaker_state.record_failure()
                    _circuit_breaker_cache.update_breaker(domain, breaker_state)
                    last_result = FetcherResult(url=url, success=False, error=str(e), failure_stage='exception')
                    if attempt < self.retry_policy.max_attempts - 1:
                        self._stats['retries'] += 1
                        backoff = self.retry_policy.compute_backoff(attempt)
                        await asyncio.sleep(backoff)

            self._stats['failures'] += 1
            return last_result or FetcherResult(url=url, success=False, error='retry_exhausted', failure_stage='retry')

    def get_stats(self) -> dict[str, int]:
        """Return fetcher statistics."""
        return self._stats.copy()

    def reset_stats(self) -> None:
        """Reset fetcher statistics."""
        self._stats = {'attempts': 0, 'successes': 0, 'failures': 0, 'retries': 0}

class CurlCFFIFetcher(BaseFetcher):
    """Curl-CFFI based fetcher with conditional cache.

    M1 8GB: ~60MB resident for prewarm pool.
    """

    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        from transport.curl_cffi_fetch import fetch_via_curl_cffi_cached
        result = await fetch_via_curl_cffi_cached(url, **kwargs)
        return self._convert_result(result)

    def _convert_result(self, result: dict[str, Any]) -> FetcherResult:
        return FetcherResult(url=result.get('url', ''), success=result.get('success', False), error=result.get('error'), status=result.get('status'), headers=result.get('headers'), body=result.get('body'), network_error_kind=result.get('network_error_kind'), failure_stage=result.get('failure_stage'))

class AiohttpFetcher(BaseFetcher):
    """Aiohttp/H2 based fetcher."""

    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        from transport.httpx_transport import fetch_via_httpx_h2
        timeout_s = kwargs.pop('timeout_s', self.default_timeout)
        response = await fetch_via_httpx_h2(url, timeout_s=timeout_s)
        return self._convert_response(response)

    def _convert_response(self, response) -> FetcherResult:
        return FetcherResult(url=str(response.url), success=True, status=response.status_code, headers=dict(response.headers), body=response.text())

class HybridFetcher(BaseFetcher):
    """Hybrid fetcher: tries aiohttp first, escalates to curl_cffi on 403/429.

    Used for stealth mode and when aiohttp gets blocked.
    """

    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        timeout_s = kwargs.pop('timeout_s', self.default_timeout)
        try:
            from transport.httpx_transport import fetch_via_httpx_h2
            response = await fetch_via_httpx_h2(url, timeout_s=timeout_s)
            if response.status_code in (403, 429):
                from transport.curl_cffi_fetch import fetch_via_curl_cffi_cached
                result = await fetch_via_curl_cffi_cached(url, timeout_s=timeout_s, **kwargs)
                return self._convert_curl_result(result)
            return self._convert_response(response)
        except Exception:
            from transport.curl_cffi_fetch import fetch_via_curl_cffi_cached
            result = await fetch_via_curl_cffi_cached(url, timeout_s=timeout_s, **kwargs)
            return self._convert_curl_result(result)

    def _convert_response(self, response) -> FetcherResult:
        return FetcherResult(url=str(response.url), success=True, status=response.status_code, headers=dict(response.headers), body=response.text())

    def _convert_curl_result(self, result: dict[str, Any]) -> FetcherResult:
        return FetcherResult(url=result.get('url', ''), success=result.get('success', False), error=result.get('error'), status=result.get('status'), headers=result.get('headers'), body=result.get('body'), network_error_kind=result.get('network_error_kind'), failure_stage=result.get('failure_stage'))

class TorCurlCFFIFetcher(BaseFetcher):
    """Tor-anonymized curl_cffi fetcher."""
    TOR_TIMEOUT_SCALE: float = 2.0

    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        from transport.curl_cffi_fetch import fetch_via_tor_curl_cffi
        timeout_s = kwargs.pop('timeout_s', self.default_timeout)
        result = await fetch_via_tor_curl_cffi(url, timeout_s=timeout_s, **kwargs)
        return self._convert_result(result)

    def _convert_result(self, result: dict[str, Any]) -> FetcherResult:
        return FetcherResult(url=result.get('url', ''), success=result.get('success', False), error=result.get('error'), status=result.get('status'), headers=result.get('headers'), body=result.get('body'), network_error_kind=result.get('network_error_kind'), failure_stage=result.get('failure_stage'))

class I2PCurlCFFIFetcher(BaseFetcher):
    """I2P-anonymized curl_cffi fetcher."""
    TOR_TIMEOUT_SCALE: float = 2.0

    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        from transport.curl_cffi_fetch import fetch_via_i2p_curl_cffi
        timeout_s = kwargs.pop('timeout_s', self.default_timeout)
        result = await fetch_via_i2p_curl_cffi(url, timeout_s=timeout_s, **kwargs)
        return self._convert_result(result)

    def _convert_result(self, result: dict[str, Any]) -> FetcherResult:
        return FetcherResult(url=result.get('url', ''), success=result.get('success', False), error=result.get('error'), status=result.get('status'), headers=result.get('headers'), body=result.get('body'), network_error_kind=result.get('network_error_kind'), failure_stage=result.get('failure_stage'))

class JsFetcher(BaseFetcher):
    """JavaScript-rendering fetcher (Camoufox/Playwright/nodriver/macOS WebKit).

    M1 8GB: bounded semaphore for browser pool, cooldown after use.
    """
    BROWSER_TIMEOUT: float = 15.0
    __slots__ = tuple(('_semaphore',))

    def __init__(self) -> None:
        super().__init__()
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
            self._semaphore = get_semaphore_for_testing(ConcurrencyCategory.SCRAPE_GENERAL)
        return self._semaphore

    async def fetch_once(self, url: str, **kwargs) -> FetcherResult:
        timeout_s = kwargs.pop('timeout_s', self.BROWSER_TIMEOUT)
        renderer = kwargs.pop('renderer', 'camoufox')
        semaphore = self._get_semaphore()
        async with semaphore:
            try:
                if renderer == 'camoufox':
                    from fetching.public_fetcher import _fetch_with_camoufox
                    html = await _fetch_with_camoufox(url, timeout=timeout_s)
                    return FetcherResult(url=url, success=True, body=html)
                elif renderer == 'playwright':
                    from fetching.public_fetcher import _fetch_with_playwright
                    html = await _fetch_with_playwright(url, timeout=timeout_s)
                    return FetcherResult(url=url, success=True, body=html)
                elif renderer == 'nodriver':
                    from fetching.public_fetcher import _fetch_with_nodriver
                    html = await _fetch_with_nodriver(url)
                    return FetcherResult(url=url, success=True, body=html)
                elif renderer == 'macos_webkit':
                    from rendering.macos_webkit_renderer import fetch_with_macos_webkit
                    result = await fetch_with_macos_webkit(url, timeout_s=timeout_s)
                    return FetcherResult(url=url, success=True, body=result.html)
                else:
                    raise ValueError(f'Unknown JS renderer: {renderer}')
            except Exception as e:
                return FetcherResult(url=url, success=False, error=str(e), network_error_kind='js_renderer_error', failure_stage='browser')

class FetchRouter:
    """Routes fetch requests to appropriate fetcher based on URL type and options.

    Centralizes the if/elif chain from the monolithic fetch function.
    Converts FetcherResult -> FetchResult at the boundary.
    """
    __slots__ = ('_curl_cffi', '_aiohttp', '_hybrid', '_tor', '_i2p', '_js')

    def __init__(self):
        self._curl_cffi = CurlCFFIFetcher()
        self._aiohttp = AiohttpFetcher()
        self._hybrid = HybridFetcher()
        self._tor = TorCurlCFFIFetcher()
        self._i2p = I2PCurlCFFIFetcher()
        self._js = JsFetcher()

    def _classify_url(self, url: str) -> str:
        """Classify URL by network type."""
        parsed = cached_urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.endswith(('.onion', '.onion.ws', '.onion.ly')):
            return 'tor'
        elif netloc.endswith('.i2p'):
            return 'i2p'
        elif netloc.endswith('.freenet'):
            return 'freenet'
        return 'clearnet'

    async def fetch(self, url: str, timeout_s: float=35.0, use_stealth: bool=False, use_js: bool=False, **kwargs) -> FetcherResult:
        """Route and execute fetch request.

        Args:
            url: URL to fetch
            timeout_s: Requested timeout
            use_stealth: Use curl_cffi with JA3 fingerprinting
            use_js: Use JavaScript renderer
            **kwargs: Additional fetcher-specific arguments

        Returns:
            FetcherResult from appropriate fetcher
        """
        network = self._classify_url(url)
        if use_js:
            renderer = kwargs.pop('renderer', 'camoufox')
            return await self._js.fetch_with_retry(url, timeout_s=timeout_s, network=network, renderer=renderer, **kwargs)
        if use_stealth:
            if network == 'tor':
                return await self._tor.fetch_with_retry(url, timeout_s=timeout_s, network=network, **kwargs)
            elif network == 'i2p':
                return await self._i2p.fetch_with_retry(url, timeout_s=timeout_s, network=network, **kwargs)
            else:
                return await self._curl_cffi.fetch_with_retry(url, timeout_s=timeout_s, network=network, **kwargs)
        if network in ('tor', 'i2p'):
            fetcher = self._tor if network == 'tor' else self._i2p
            return await fetcher.fetch_with_retry(url, timeout_s=timeout_s, network=network, **kwargs)
        return await self._hybrid.fetch_with_retry(url, timeout_s=timeout_s, network=network, **kwargs)

    def get_stats(self) -> dict[str, dict[str, int]]:
        """Return stats from all fetchers."""
        return {'curl_cffi': self._curl_cffi.get_stats(), 'aiohttp': self._aiohttp.get_stats(), 'hybrid': self._hybrid.get_stats(), 'tor': self._tor.get_stats(), 'i2p': self._i2p.get_stats(), 'js': self._js.get_stats()}
_router: FetchRouter | None = None

def get_fetch_router() -> FetchRouter:
    """Get global FetchRouter instance."""
    global _router
    if _router is None:
        _router = FetchRouter()
    return _router

async def fetch_via_router(url: str, timeout_s: float=35.0, use_stealth: bool=False, use_js: bool=False, **kwargs) -> FetcherResult:
    """Convenience function for fetching via router.

    Replaces inline fetch logic from public_fetcher.py.
    """
    router = get_fetch_router()
    return await router.fetch(url, timeout_s=timeout_s, use_stealth=use_stealth, use_js=use_js, **kwargs)