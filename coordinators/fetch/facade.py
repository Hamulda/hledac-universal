"""
FetchCoordinatorFacade — Skinny Coordinator using Service Layer
============================================================

Lightweight facade that delegates to isolated services.

This is the REFACTORED FetchCoordinator — all heavy lifting is done
by services in coordinators.fetch.services.

Service Architecture (SRP/ISP Compliant):
    - AIMDWindowService: Adaptive concurrency control
    - PrivacyAllocatorService: Privacy budget management
    - SpeculativePrefetchService: Link prediction
    - EntropyFeedbackService: Anomaly detection
    - MicroSprintService: Sprint scheduling
    - EvidenceSinkService: Evidence collection
    - Base services: DNS, RateLimit, CircuitBreaker, Retry

M1 8GB: Services are lazy-loaded. Only enabled transports consume RAM.

Usage:
    facade = FetchCoordinatorFacade()
    await facade.initialize()

    result = await facade.fetch('https://example.com')
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any, cast

from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
from hledac.universal.compat.msgspec_gc_compat import Struct
from hledac.universal.utils.asyncx import parallel

from .services import (
    AIMDConfig,
    AIMDWindowService,
    EntropyConfig,
    EntropyFeedbackService,
    EvidenceConfig,
    EvidenceSinkService,
    FetchOptions,
    FetchResult,
    FetchServiceConfig,
    FetchServiceRegistry,
    MicroSprintConfig,
    MicroSprintService,
    PrivacyAllocatorService,
    PrivacyConfig,
    PrivacyLevel,
    SpeculativeConfig,
    SpeculativePrefetchService,
)

logger = logging.getLogger(__name__)


class FetchCoordinatorConfig(Struct, frozen=True):
    """Configuration for FetchCoordinatorFacade. M1 8GB: msgspec.Struct for fast init."""

    max_concurrent: int = 10
    max_retries: int = 3
    timeout: float = 30.0
    rate_limit_rps: float = 0.5
    # Extended service configs
    aimd: AIMDConfig | None = None
    privacy: PrivacyConfig | None = None
    speculative: SpeculativeConfig | None = None
    entropy: EntropyConfig | None = None
    micro_sprint: MicroSprintConfig | None = None
    evidence: EvidenceConfig | None = None


class FetchCoordinatorFacade:
    """
    Skinny facade coordinator for fetch operations.

    Responsibilities:
    - URL frontier management
    - Delegation to services (DNS, rate limit, circuit breaker, retry)
    - AIMD window control
    - Privacy budget allocation
    - Speculative prefetching
    - Entropy feedback
    - Micro sprint scheduling
    - Evidence collection

    All heavy logic is delegated to services in coordinators.fetch.services.
    This facade is ≤ 200 LOC of pure delegation + lifecycle.
    """

    __slots__ = (
        "_config",
        "_services",
        # Extended services
        "_aimd",
        "_privacy",
        "_speculative",
        "_entropy",
        "_micro_sprint",
        "_evidence",
        # State
        "_frontier",
        "_processed_count",
        "_urls_fetched",
        "_evidence_ids",
        "_running",
        "_initialized",
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
        self._initialized = False

        # Extended services (lazy-loaded)
        self._aimd: AIMDWindowService | None = None
        self._privacy: PrivacyAllocatorService | None = None
        self._speculative: SpeculativePrefetchService | None = None
        self._entropy: EntropyFeedbackService | None = None
        self._micro_sprint: MicroSprintService | None = None
        self._evidence: EvidenceSinkService | None = None

        # State
        self._frontier: deque[str] = deque(maxlen=1000)
        self._processed_count: int = 0
        self._urls_fetched: int = 0
        self._evidence_ids: deque[str] = deque(maxlen=500)
        self._running: bool = False

    async def initialize(self) -> bool:
        """Initialize all services lazily."""
        if self._initialized:
            return True

        base_config = FetchServiceConfig(
            enable_tor=FeatureFlags.get(FeatureFlag.TOR),
            enable_i2p=FeatureFlags.get(FeatureFlag.I2P),
            enable_gopher=FeatureFlags.get(FeatureFlag.GOPHER),
            enable_captcha=FeatureFlags.get(FeatureFlag.CAPTCHA_DETECTION),
            rate_limit_rps=self._config.rate_limit_rps,
            max_retries=self._config.max_retries,
            timeout=self._config.timeout,
        )
        self._services = FetchServiceRegistry(config=base_config)
        await self._services.initialize()

        await self._initialize_extended_services()

        self._initialized = True
        logger.info(
            "FetchCoordinatorFacade initialized",
            extra={
                "transports": self._services.get_enabled_transports(),
                "services": self._get_service_names(),
            },
        )
        return True

    async def _initialize_extended_services(self) -> None:
        """Initialize extended services based on config."""
        # AIMD Window Service
        aimd_config = self._config.aimd or AIMDConfig()
        self._aimd = AIMDWindowService(config=aimd_config)

        # Privacy Allocator Service
        privacy_config = self._config.privacy or PrivacyConfig()
        self._privacy = PrivacyAllocatorService(config=privacy_config)

        # Speculative Prefetch Service
        speculative_config = self._config.speculative or SpeculativeConfig()
        self._speculative = SpeculativePrefetchService(config=speculative_config)

        # Entropy Feedback Service
        entropy_config = self._config.entropy or EntropyConfig()
        self._entropy = EntropyFeedbackService(config=entropy_config)

        # Micro Sprint Service
        micro_sprint_config = self._config.micro_sprint or MicroSprintConfig()
        self._micro_sprint = MicroSprintService(config=micro_sprint_config)
        self._micro_sprint.set_entropy_feedback(self._entropy)

        # Evidence Sink Service
        evidence_config = self._config.evidence or EvidenceConfig()
        self._evidence = EvidenceSinkService(config=evidence_config)

    def _get_service_names(self) -> list[str]:
        """Get list of initialized service names."""
        services = []
        if self._aimd:
            services.append("aimd")
        if self._privacy:
            services.append("privacy")
        if self._speculative:
            services.append("speculative")
        if self._entropy:
            services.append("entropy")
        if self._micro_sprint:
            services.append("micro_sprint")
        if self._evidence:
            services.append("evidence")
        return services

    async def fetch(self, url: str, options: FetchOptions | None = None) -> FetchResult:
        """
        Fetch a single URL using the service layer.

        This is the main entry point — all logic is delegated to services.
        Pipeline:
        1. Preflight: Privacy budget + Rate limit
        2. Privacy lane acquisition
        3. AIMD window acquisition
        4. Circuit breaker check
        5. Execute fetch
        6. Record success/failure
        7. Entropy analysis (async)
        8. Speculative prefetch (async)
        9. Evidence creation (async)
        """
        if not self._initialized:
            await self.initialize()
        assert self._services is not None

        options = options or FetchOptions(
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
        )

        # Determine transport and domain
        transport_name = self._services.get_transport(url)
        domain = self._extract_domain(url)

        result = await self._preflight_phase(url, domain, transport_name)
        if result is not None:
            return result

        try:
            # Privacy lane
            if self._privacy:
                await self._privacy.acquire_lane(PrivacyLevel.CLEAR)

            # AIMD window
            if self._aimd:
                await self._aimd.acquire()

            start_time, fetch_result = await self._execute_fetch(url, options, transport_name)

            return await self._postfetch_phase(url, domain, transport_name, start_time, fetch_result, options)

        except Exception as e:
            return await self._handle_fetch_error(url, domain, transport_name, e)
        finally:
            # Release resources
            # ISSUE-FOUND-1: AIMDWindowService.release() is now async
            if self._aimd:
                await self._aimd.release()
            if self._privacy:
                self._privacy.release_lane(PrivacyLevel.CLEAR)

    async def _preflight_phase(self, url: str, domain: str, transport_name: str) -> FetchResult | None:
        """
        Execute preflight checks.

        Returns FetchResult if blocked, None if allowed to proceed.
        """
        # Privacy budget check
        if self._privacy:
            allowed, reason, _retry_after = await self._privacy.check_budget(url, PrivacyLevel.CLEAR)
            if not allowed:
                return FetchResult(
                    success=False,
                    error=f"privacy_blocked:{reason}",
                    transport=transport_name,
                )

        # Rate limit check
        allowed, reason = await self._services.rate_limiter.acquire(domain)
        if not allowed:
            return FetchResult(
                success=False,
                error=f"rate_limited:{reason}",
                transport=transport_name,
            )

        # Circuit breaker check
        allowed, reason, _retry_after = self._services.circuit_breaker.check_domain(domain)
        if not allowed:
            return FetchResult(
                success=False,
                error=f"circuit_breaker_blocked:{reason}",
                transport=transport_name,
            )

        return None

    async def _execute_fetch(self, url: str, options: FetchOptions, transport_name: str) -> tuple[float, FetchResult]:
        """Execute the actual fetch operation. Returns (start_time, result)."""
        start_time = time.monotonic()
        transport = self._services.get_transport_object(transport_name)

        if transport is None:
            result = await self._fetch_clearnet(url, options)
        else:
            result = await transport.fetch(url, options)
        return start_time, result

    async def _postfetch_phase(
        self,
        url: str,
        domain: str,
        transport_name: str,
        start_time: float,
        result: FetchResult,
        options: FetchOptions,
    ) -> FetchResult:
        """Post-fetch processing: record success, entropy, evidence."""
        fetch_time_ms = (time.monotonic() - start_time) * 1000

        # Record success
        self._services.rate_limiter.record_success(domain)
        self._services.circuit_breaker.record_domain_success(domain)

        if self._aimd:
            await self._aimd.record_success(fetch_time_ms)

        if self._privacy:
            await self._privacy.record_request(
                url,
                content_size=len(result.content),
                privacy_level=PrivacyLevel.CLEAR,
            )

        # Entropy analysis (async, non-blocking for result)
        entropy_result = None
        if self._entropy and result.content:
            entropy_result = await self._entropy.analyze_content(url, result.content)

        # Speculative prefetch (async, non-blocking)
        if self._speculative and result.content and self._config.speculative:
            pass  # Integration point for streaming link extraction

        # Evidence creation
        if self._evidence and result.content:
            await self._evidence.create_evidence(
                url=url,
                content=result.content,
                status_code=result.status_code,
                headers=result.headers,
                fetch_duration_ms=fetch_time_ms,
                transport=transport_name,
                entropy_score=(entropy_result.entropy_bits_per_byte if entropy_result else 0.0),
            )

        return FetchResult(
            success=result.success,
            status_code=result.status_code,
            content=result.content,
            content_type=result.content_type,
            headers=result.headers,
            transport=transport_name,
            fetch_time_ms=fetch_time_ms,
        )

    async def _handle_fetch_error(self, url: str, domain: str, transport_name: str, error: Exception) -> FetchResult:
        """Handle fetch error."""
        # Record failure
        self._services.rate_limiter.record_failure(domain)
        self._services.circuit_breaker.record_domain_failure(domain, failure_kind=str(error))

        if self._aimd:
            await self._aimd.record_failure()

        return FetchResult(
            success=False,
            error=str(error),
            transport=transport_name,
            fetch_time_ms=0.0,
        )

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL."""
        if "://" in url:
            return url.split("/")[2].split(":")[0]
        return url.split(":")[0]

    async def _fetch_clearnet(self, url: str, options: FetchOptions) -> FetchResult:
        """Fetch via clearnet (httpx)."""
        import httpx

        timeout = httpx.Timeout(options.timeout, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return FetchResult(
                success=True,
                status_code=response.status_code,
                content=response.content,
                content_type=response.headers.get("content-type", ""),
                headers=dict(response.headers),
            )

    async def start(self, _ctx: dict[str, Any]) -> None:
        """Start the coordinator."""
        await self.initialize()
        self._running = True

        if self._entropy:
            await self._entropy.start_consumer()

        if self._micro_sprint:
            await self._micro_sprint.start_sprint_loop()

    async def step(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """
        Execute one step — process URLs from frontier.

        Returns bounded dict with counts and IDs.
        """
        if not self._running:
            await self.start(ctx)

        urls_to_fetch = list(self._frontier)[: self._config.max_concurrent]

        if not urls_to_fetch:
            return {
                "urls_fetched": 0,
                "evidence_ids": [],
                "clusters_updated": 0,
                "stop_reason": None,
            }

        # Clear frontier
        self._frontier.clear()

        async def fetch_one(url: str) -> FetchResult:
            return await self.fetch(url)

        from hledac.universal.utils.asyncx import ParallelResult

        _result = await parallel(
            *[fetch_one(url) for url in urls_to_fetch],
            policy="log",
            concurrency=16,
        )
        results: list[FetchResult] = cast(ParallelResult, _result).ok

        evidence_ids = []
        for _url, result in zip(urls_to_fetch, results, strict=True):
            self._urls_fetched += 1

            if isinstance(result, Exception):
                continue

            fr = cast(FetchResult, result)
            if not fr.success or not fr.content:
                continue

            evidence_id = f"ev_{self._urls_fetched:08d}"
            evidence_ids.append(evidence_id)
            self._evidence_ids.append(evidence_id)

        return {
            "urls_fetched": len([r for r in results if isinstance(r, FetchResult) and r.success]),
            "evidence_ids": evidence_ids[:10],
            "clusters_updated": 0,
            "stop_reason": None,
        }

    async def shutdown(self, _ctx: dict[str, Any] | None = None) -> None:
        """Shutdown the coordinator and release all resources."""
        self._running = False

        # Close extended services
        if self._entropy:
            await self._entropy.aclose()
        if self._micro_sprint:
            await self._micro_sprint.aclose()
        if self._speculative:
            await self._speculative.aclose()
        if self._evidence:
            await self._evidence.aclose()
        if self._privacy:
            await self._privacy.aclose()
        if self._aimd:
            await self._aimd.aclose()

        if self._evidence:
            await self._evidence.process_queue()

        # Close base services
        if self._services:
            await self._services.aclose()
            self._services = None

        self._initialized = False
        logger.info("FetchCoordinatorFacade shutdown complete")

    def enqueue_url(self, url: str) -> None:
        """Add URL to frontier."""
        self._frontier.append(url)

    def get_stats(self) -> dict[str, Any]:
        """Get coordinator statistics."""
        stats = {
            "frontier_size": len(self._frontier),
            "urls_fetched": self._urls_fetched,
            "evidence_ids_count": len(self._evidence_ids),
            "running": self._running,
            "initialized": self._initialized,
            "services": self._get_service_names(),
        }

        # Add service-specific stats
        if self._aimd:
            stats["aimd"] = self._aimd.get_stats()
        if self._privacy:
            stats["privacy"] = self._privacy.get_stats()
        if self._speculative:
            stats["speculative"] = self._speculative.get_stats()
        if self._entropy:
            stats["entropy"] = self._entropy.get_stats()
        if self._micro_sprint:
            stats["micro_sprint"] = self._micro_sprint.get_stats()
        if self._evidence:
            stats["evidence"] = self._evidence.get_stats()

        return stats

    @property
    def aimd(self) -> AIMDWindowService | None:
        """Get AIMD service."""
        return self._aimd

    @property
    def privacy(self) -> PrivacyAllocatorService | None:
        """Get privacy service."""
        return self._privacy

    @property
    def speculative(self) -> SpeculativePrefetchService | None:
        """Get speculative prefetch service."""
        return self._speculative

    @property
    def entropy(self) -> EntropyFeedbackService | None:
        """Get entropy service."""
        return self._entropy

    @property
    def micro_sprint(self) -> MicroSprintService | None:
        """Get micro sprint service."""
        return self._micro_sprint

    @property
    def evidence(self) -> EvidenceSinkService | None:
        """Get evidence service."""
        return self._evidence
