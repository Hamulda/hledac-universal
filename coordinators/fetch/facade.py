"""
FetchCoordinatorFacade — Skinny Coordinator using Service Layer
============================================================



Lightweight facade that delegates to isolated services.

This is the REFACTORED FetchCoordinator — all heavy lifting is done
by services in coordinators.fetch.services.

M1 8GB: Services are lazy-loaded. Only enabled transports consume RAM.

Usage:
    facade = FetchCoordinatorFacade()
    await facade.initialize()

    result = await facade.fetch('https://example.com')
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any, cast

from hledac.universal.compat.msgspec_gc_compat import Struct


from hledac.universal.core.feature_flags import FeatureFlag, FeatureFlags
from hledac.universal.utils.asyncx import parallel

from .services import (
from core import aclose
    FetchOptions,
    FetchResult,
    FetchServiceConfig,
    FetchServiceRegistry,
)

logger = logging.getLogger(__name__)


class FetchCoordinatorConfig(Struct, frozen=True):
    """Configuration for FetchCoordinatorFacade. M1 8GB: msgspec.Struct for fast init."""
    max_concurrent: int = 10
    max_retries: int = 3
    timeout: float = 30.0
    rate_limit_rps: float = 0.5


class FetchCoordinatorFacade:
    """
    Skinny facade coordinator for fetch operations.

    Responsibilities:
    - URL frontier management
    - Delegation to services (DNS, rate limit, circuit breaker, retry)
    - Evidence packet creation

    All heavy logic is delegated to services in coordinators.fetch.services.
    """
    __slots__ = (
        '_config',
        '_services',
        '_frontier',
        '_processed_count',
        '_urls_fetched',
        '_evidence_ids',
        '_running',
    )

    def __init__(
        self,
        config: FetchCoordinatorConfig | None = None,
        _pivot_queue_provider: Callable[[], Any] = lambda: None,
        _pivot_stats_provider: Callable[[], dict[str, Any]] | None = None,
        _evidence_sink: object | None = None,
    ) -> None:
        self._config = config or FetchCoordinatorConfig()
        self._services: FetchServiceRegistry | None = None
        self._frontier: deque[str] = deque(maxlen=1000)
        self._processed_count: int = 0
        self._urls_fetched: int = 0
        self._evidence_ids: deque[str] = deque(maxlen=500)
        self._running: bool = False

    async def initialize(self) -> bool:
        """Initialize services lazily."""
        if self._services is not None:
            return True

        config = FetchServiceConfig(
            enable_tor=FeatureFlags.get(FeatureFlag.TOR),
            enable_i2p=FeatureFlags.get(FeatureFlag.I2P),
            enable_gopher=FeatureFlags.get(FeatureFlag.GOPHER),
            enable_captcha=FeatureFlags.get(FeatureFlag.CAPTCHA_DETECTION),
            rate_limit_rps=self._config.rate_limit_rps,
            max_retries=self._config.max_retries,
            timeout=self._config.timeout,
        )

        self._services = FetchServiceRegistry(config=config)
        await self._services.initialize()

        logger.info("FetchCoordinatorFacade initialized", extra={
            'transports': self._services.get_enabled_transports(),
        })
        return True

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """
        Fetch a single URL using the service layer.

        This is the main entry point — all logic is delegated to services.
        """
        if self._services is None:
            await self.initialize()
        # Type narrowing: after initialize(), _services is guaranteed non-None
        assert self._services is not None
        services = self._services

        options = options or FetchOptions(
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
        )

        # Determine transport
        transport_name = services.get_transport(url)

        # Check circuit breaker
        allowed, reason, _retry_after = services.circuit_breaker.check_domain(
            url.split('/')[2] if '://' in url else url
        )
        if not allowed:
            return FetchResult(
                success=False,
                error=f"circuit_breaker_blocked:{reason}",
                transport=transport_name,
            )

        # Check rate limiter
        domain = url.split('/')[2] if '://' in url else url
        allowed, reason = await services.rate_limiter.acquire(domain)
        if not allowed:
            return FetchResult(
                success=False,
                error=f"rate_limited:{reason}",
                transport=transport_name,
            )

        # Get transport and fetch
        start_time = time.monotonic()
        transport = services.get_transport_object(transport_name)

        try:
            if transport is None:
                # Clearnet fetch via httpx
                result = await self._fetch_clearnet(url, options)
            else:
                # Darknet transport
                result = await transport.fetch(url, options)

            fetch_time_ms = (time.monotonic() - start_time) * 1000

            # Record success
            self._services.rate_limiter.record_success(domain)
            self._services.circuit_breaker.record_domain_success(domain)

            return FetchResult(
                success=result.success,
                status_code=result.status_code,
                content=result.content,
                content_type=result.content_type,
                headers=result.headers,
                transport=transport_name,
                fetch_time_ms=fetch_time_ms,
            )

        except Exception as e:
            fetch_time_ms = (time.monotonic() - start_time) * 1000
            self._services.rate_limiter.record_failure(domain)
            self._services.circuit_breaker.record_domain_failure(domain, failure_kind=str(e))

            return FetchResult(
                success=False,
                error=str(e),
                transport=transport_name,
                fetch_time_ms=fetch_time_ms,
            )

    async def _fetch_clearnet(
        self, url: str, options: FetchOptions
    ) -> FetchResult:
        """Fetch via clearnet (httpx)."""
        import httpx

        timeout = httpx.Timeout(options.timeout, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return FetchResult(
                success=True,
                status_code=response.status_code,
                content=response.content,
                content_type=response.headers.get('content-type', ''),
                headers=dict(response.headers),
            )

    # -------------------------------------------------------------------------
    # Coordinator Interface
    # -------------------------------------------------------------------------

    async def start(self, _ctx: dict[str, Any]) -> None:
        """Start the coordinator."""
        await self.initialize()
        self._running = True

    async def step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one step — process URLs from frontier.

        Returns bounded dict with counts and IDs.
        """
        if not self._running:
            await self.start(ctx)

        urls_to_fetch = list(self._frontier)[:self._config.max_concurrent]

        if not urls_to_fetch:
            return {
                'urls_fetched': 0,
                'evidence_ids': [],
                'clusters_updated': 0,
                'stop_reason': None,
            }

        # Clear frontier
        self._frontier.clear()

        # Fetch concurrently
        async def fetch_one(url: str) -> FetchResult:
            return await self.fetch(url)

        # ISSUE ASYNC-001: asyncio.gather → parallel() with bounded concurrency
        # Fetches are I/O-bound HTTP requests, bounded to prevent overwhelming the system
        from hledac.universal.utils.asyncx import ParallelResult

        _result = await parallel(
            *[fetch_one(url) for url in urls_to_fetch],
            policy="log",
            concurrency=16,
        )
        # Type checker limitation: cast to resolve ParallelResult overload correctly
        results: list[FetchResult] = cast(ParallelResult, _result).ok

        # Process results
        evidence_ids = []
        for _url, result in zip(urls_to_fetch, results, strict=True):
            self._urls_fetched += 1

            if isinstance(result, Exception):
                continue

            # isinstance check narrows to FetchResult inside this block
            fr = cast(FetchResult, result)
            if not fr.success or not fr.content:
                continue

            evidence_id = f"ev_{self._urls_fetched:08d}"
            evidence_ids.append(evidence_id)
            self._evidence_ids.append(evidence_id)

        return {
            'urls_fetched': len([r for r in results if isinstance(r, FetchResult) and r.success]),
            'evidence_ids': evidence_ids[:10],  # Bounded
            'clusters_updated': 0,
            'stop_reason': None,
        }

    async def shutdown(self, _ctx: dict[str, Any] | None = None) -> None:
        """Shutdown the coordinator."""
        self._running = False
        self._services = None

    def enqueue_url(self, url: str) -> None:
        """Add URL to frontier."""
        self._frontier.append(url)

    def get_stats(self) -> dict[str, Any]:
        """Get coordinator statistics."""
        return {
            'frontier_size': len(self._frontier),
            'urls_fetched': self._urls_fetched,
            'evidence_ids_count': len(self._evidence_ids),
            'running': self._running,
        }
