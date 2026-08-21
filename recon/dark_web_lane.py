"""
intelligence/dark_web_lane.py — F320+: Dark Web Intelligence Lane

Thin subclass of BaseIntelligenceLane for Tor/.onion crawling.

Wraps DarkWebCrawler + TorProxyManager from dark_web_intelligence.py.

LaneSpec:
    concurrent_queries=2 (Tor latency is high, parallelization has diminishing returns)
    cost_estimate_per_query=3 (Tor circuit setup is expensive)
"""

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, Any

from hledac.universal.recon.lane import (
    BTC_ADDRESS_PATTERN,
    XMR_ADDRESS_PATTERN,
    BaseIntelligenceLane,
    FetchResult,
    LaneContext,
    LaneSpec,
    ParsedResult,
    ResolveResult,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Compiled regex patterns (module-level for reuse)
_ONION_V3_PATTERN = re.compile(r"[a-z2-7]{56}\.onion")
_ONION_V2_PATTERN = re.compile(r"[a-z2-7]{16}\.onion")


class DarkWebLane(BaseIntelligenceLane):
    """
    Dark web intelligence lane for Tor/.onion crawling.

    Env gate: HLEDAC_ENABLE_DARK_PIVOTS
    Priority: 7 (high — dark web is high-value OSINT source)
    RAM budget: 80 MB

    Phase implementation:
        resolve: normalize .onion address, validate format
        fetch: crawl via Tor session (via dark_web_intelligence.TorProxyManager)
        parse: extract crypto addrs, emails, PGP keys, links
        dedup: inherited — uses URL as key via RotatingBloomFilter
        emit: inherited — one finding per IOC type
    """

    __slots__ = ("_tor_proxy", "_crawler")

    sidecar_id: str = "dark_web"
    env_gate: str = "HLEDAC_ENABLE_DARK_PIVOTS"
    ram_budget_mb: int = 80
    priority: int = 7
    lane_spec: LaneSpec = LaneSpec(concurrent_queries=2, cost_estimate_per_query=3)

    MAX_CONTENT_CACHE: int = 200
    MAX_VISITED_URLS: int = 1000
    MAX_BLOOM_ENTRIES: int = 3000

    def __init__(self) -> None:
        super().__init__()
        self._tor_proxy: Any | None = None  # TorProxyManager, lazy
        self._crawler: Any | None = None  # DarkWebCrawler, lazy

    def is_available(self) -> bool:
        """Check env gate + Tor dependency."""
        if not super().is_available():
            return False
        try:
            import importlib

            return importlib.util.find_spec("httpx_socks") is not None
        except Exception:
            return False

    async def resolve(self, target: str, ctx: LaneContext) -> ResolveResult:
        """
        Resolve a target to a .onion URL.

        Accepts: raw .onion address, full URL, or search term.
        Returns: ResolveResult with kind='onion' and resolved as http://<addr>.onion
        """
        target = target.strip()

        # sprint_mode can widen the search scope
        aggressive = ctx.sprint_mode == "aggressive"

        # Check if already a v3 or v2 onion address
        v3_match = _ONION_V3_PATTERN.search(target)
        v2_match = _ONION_V2_PATTERN.search(target)

        if v3_match:
            addr = v3_match.group(0)
        elif v2_match:
            addr = v2_match.group(0)
        elif ".onion" in target:
            # Full URL like http://xxx.onion/path
            addr = target.split(".onion")[0] + ".onion"
        else:
            # Treat as search term — return as-is with kind='search'
            return ResolveResult(
                resolved=target,
                kind="search",
                metadata={"original": target, "aggressive": aggressive},
            )

        # Normalize to v3 or v2 kind
        kind = "v3_onion" if len(addr.split(".")[0]) == 56 else "v2_onion"
        url = f"http://{addr}" if not addr.startswith("http") else addr

        return ResolveResult(
            resolved=url,
            kind=kind,
            metadata={"address": addr, "aggressive": aggressive},
        )

    async def fetch(self, resolved: ResolveResult, ctx: LaneContext) -> FetchResult:
        """
        Fetch .onion page via Tor session.

        Uses DarkWebCrawler internally with bounded visited-url cache.
        Circuit breaker: checks domain_breaker_check before request.
        memory_pressure from ctx is used to adjust request timeout.
        """
        if resolved.kind == "search":
            return FetchResult(
                url=resolved.resolved,
                status_code=0,
                error="search_kind_not_fetchable",
            )

        domain = resolved.resolved.split("/")[2] if "//" in resolved.resolved else resolved.resolved

        # Circuit breaker preflight
        decision = self._circuit_breaker_check(domain)
        if decision is not None and not decision.allowed:
            return FetchResult(
                url=resolved.resolved,
                status_code=0,
                error=f"circuit_breaker_open:{decision.reason}",
            )

        # Lazy init Tor proxy + crawler
        crawler = await self._get_crawler()
        if crawler is None:
            return FetchResult(
                url=resolved.resolved,
                status_code=0,
                error="tor_proxy_unavailable",
            )

        # Adaptive timeout based on memory pressure (high pressure = shorter timeout)
        base_timeout = 120.0
        timeout = base_timeout * (1.0 - ctx.memory_pressure * 0.3) if ctx.memory_pressure else base_timeout

        semaphore = self._get_semaphore()
        async with semaphore:
            start = time.monotonic()
            try:
                async with asyncio.timeout(timeout):
                    # DarkWebCrawler returns AsyncIterator[DarkWebContent]
                    content_list: list[Any] = []
                    async for content in crawler.crawl_onion(
                        resolved.metadata.get("address", resolved.resolved),
                        depth=0,
                    ):
                        content_list.append(content)
                        break  # Only first page for lane semantics

                    elapsed_ms = (time.monotonic() - start) * 1000

                    if not content_list:
                        self._record_failure(domain, kind="no_content")
                        return FetchResult(
                            url=resolved.resolved,
                            status_code=404,
                            error="no_content",
                            elapsed_ms=elapsed_ms,
                        )

                    content = content_list[0]
                    self._record_success(domain)

                    return FetchResult(
                        url=resolved.resolved,
                        status_code=200,
                        body=content.raw_html or content.text_content,
                        headers={"Content-Type": content.content_type},
                        elapsed_ms=elapsed_ms,
                    )

            except TimeoutError:
                self._record_failure(domain, is_timeout=True, kind="timeout")
                return FetchResult(
                    url=resolved.resolved,
                    status_code=0,
                    error="timeout",
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )
            except Exception as exc:
                self._record_failure(domain, kind="exception")
                logger.debug("dark_web_lane.fetch error: %s", exc)
                return FetchResult(
                    url=resolved.resolved,
                    status_code=0,
                    error=f"fetch_error:{type(exc).__name__}",
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )

    async def parse(self, fetch_result: FetchResult, ctx: LaneContext) -> ParsedResult:
        """
        Parse dark web content for IOCs.

        Extracts: bitcoin addresses, monero addresses, emails, PGP blocks.
        Falls back to selectolax or BeautifulSoup for HTML parsing.
        memory_pressure from ctx is used to limit IOC extraction scope.
        """
        if fetch_result.error:
            return ParsedResult(raw_payload="", confidence=0.0)

        body = fetch_result.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")

        # Memory-pressure-adaptive extraction: cap max items when under memory pressure
        mp = ctx.memory_pressure
        max_per_type = 100 if mp < 0.5 else (20 if mp < 0.8 else 5)

        btc_addrs = BTC_ADDRESS_PATTERN.findall(body)
        xmr_addrs = XMR_ADDRESS_PATTERN.findall(body)

        email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
        emails = email_pattern.findall(body)

        # Apply memory-pressure cap
        btc_addrs = btc_addrs[:max_per_type]
        xmr_addrs = xmr_addrs[:max_per_type]
        emails = emails[:max_per_type]

        # Extract title from HTML if present
        title: str | None = None
        if "<title" in body.lower():
            title_match = re.search(r"<title[^>]*>([^<]+)</title>", body, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()

        iocs: dict[str, list[str]] = {
            "bitcoin": list(set(btc_addrs)),
            "monero": list(set(xmr_addrs)),
            "email": list(set(emails)),
        }

        iocs = {k: v for k, v in iocs.items() if v}

        return ParsedResult(
            iocs=iocs,
            raw_payload=body,
            title=title,
            confidence=0.75 if iocs else 0.5,
            metadata={"url": fetch_result.url, "status": fetch_result.status_code},
        )

    async def _get_crawler(self) -> Any | None:
        """Lazy-initialize TorProxyManager + DarkWebCrawler."""
        if self._crawler is not None:
            return self._crawler

        try:
            from hledac.universal.recon.dark_web_intelligence import DarkWebCrawler, TorProxyManager
        except ImportError:
            return None

        self._tor_proxy = TorProxyManager()
        initialized = await self._tor_proxy.initialize()
        if not initialized:
            logger.warning("dark_web_lane: TorProxyManager init failed")
            return None

        self._crawler = DarkWebCrawler(
            tor_proxy=self._tor_proxy,
            max_depth=2,
            max_pages_per_site=5,
            request_delay=3.0,
        )
        await self._crawler.initialize()
        return self._crawler

    async def close(self) -> None:
        """Close Tor proxy and crawler."""
        if self._crawler is not None:
            await self._crawler.close()
            self._crawler = None
        if self._tor_proxy is not None:
            await self._tor_proxy.close()
            self._tor_proxy = None


__all__ = ["DarkWebLane"]
