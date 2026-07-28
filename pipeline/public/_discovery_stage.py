"""
Discovery stage — URL generation for public OSINT pipeline.

Responsibilities:
- Generate bootstrap URLs for domain/URL queries
- Generate rescue URLs for non-domain threat queries
- Generate keyword-based search engine URLs
- Run discovery search (DuckDuckGo adapter)

Input: query string
Output: PageBatch with urls, titles, snippets, ranks, discovery_scores
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
from hledac.universal.pipeline._soa_types import PageBatch

logger = logging.getLogger(__name__)

# Re-use constants from live_public_pipeline
_MAX_BOOTSTRAP_URLS: int = 5
_MAX_KEYWORD_BOOTSTRAP: int = 8
_MAX_RESCUE_URLS: int = 8


class DiscoveryStage:
    """
    Discovery stage: query → list of candidate URLs.

    Generates candidate URLs via:
    1. Bootstrap (domain → well-known URLs)
    2. Rescue (threat query → static CTI sources)
    3. Keyword search (search engine query)
    4. Live search (DuckDuckGo adapter)
    """

    __slots__ = ()

    @property
    def name(self) -> str:
        return "discovery"

    async def process(
        self, input_batch: str | None = None
    ) -> tuple[PageBatch, dict[str, Any]]:
        """
        Process a query string into a PageBatch.

        Args:
            input_batch: The OSINT query string (or None for initialization)

        Returns:
            Tuple of (PageBatch, telemetry_dict)
        """
        # This stage is special — it takes a query string, not a batch
        query = input_batch if isinstance(input_batch, str) else ""
        return await self.run_discovery(query)

    async def run_discovery(
        self,
        query: str,
        *,
        public_bootstrap_enabled: bool = True,
        max_results: int = 10,
    ) -> tuple[PageBatch, dict[str, Any]]:
        """
        Run discovery for a query.

        Args:
            query: The OSINT query string.
            public_bootstrap_enabled: Whether to run bootstrap URLs.
            max_results: Maximum discovery hits to process.

        Returns:
            Tuple of (PageBatch, telemetry)
        """
        telemetry: dict[str, Any] = {
            "discovery_attempted": False,
            "discovery_result": None,
            "discovery_error": None,
            "discovery_error_type": None,
            "discovery_elapsed_s": None,
            "discovery_cache_hit": 0,
            "discovery_query_count": 0,
            "bootstrap_candidates": 0,
            "rescue_candidates": 0,
            "keyword_candidates": 0,
            "live_candidates": 0,
        }

        urls: list[str] = []
        titles: list[str] = []
        snippets: list[str] = []
        ranks: list[int] = []
        discovery_scores: list[float] = []
        fetch_blocked_reasons: list[str | None] = []
        errors: list[str | None] = []

        # Run all discovery strategies concurrently
        bootstrap_hits: list[Any] = []
        rescue_hits: list[Any] = []
        keyword_hits: list[Any] = []
        live_hits: tuple[Any, ...] = ()

        try:
            # Bootstrap — generates well-known URLs for a domain
            if public_bootstrap_enabled:
                from hledac.universal.pipeline.live_public_pipeline import (
                    generate_bootstrap_urls,
                )
                bootstrap_urls = generate_bootstrap_urls(query, max_urls=_MAX_BOOTSTRAP_URLS)
                telemetry["bootstrap_candidates"] = len(bootstrap_urls)
                for url in bootstrap_urls:
                    urls.append(url)
                    titles.append(f"Bootstrap: {url}")
                    snippets.append(f"Bootstrap URL for {query}")
                    ranks.append(0)
                    discovery_scores.append(0.5)
                    fetch_blocked_reasons.append(None)
                    errors.append(None)

            # Rescue — static CTI/news sources for threat queries
            rescue_hits = _generate_rescue_hits(query, max_urls=_MAX_RESCUE_URLS)
            telemetry["rescue_candidates"] = len(rescue_hits)
            for hit in rescue_hits:
                urls.append(hit.url)
                titles.append(hit.title)
                snippets.append(hit.snippet)
                ranks.append(hit.rank)
                discovery_scores.append(hit.score)
                fetch_blocked_reasons.append(None)
                errors.append(None)

            # Keyword search — search engine URLs
            keyword_urls = _generate_keyword_urls(query, max_urls=_MAX_KEYWORD_BOOTSTRAP)
            telemetry["keyword_candidates"] = len(keyword_urls)
            for url in keyword_urls:
                urls.append(url)
                titles.append(f"Keyword: {url}")
                snippets.append(f"Keyword search for {query}")
                ranks.append(-1)
                discovery_scores.append(0.3)
                fetch_blocked_reasons.append(None)
                errors.append(None)

            # Live search — DuckDuckGo
            _discovery_start = time.monotonic()
            try:
                live_hits = await async_search_public_web(query, max_results=max_results)
                telemetry["discovery_attempted"] = True
                telemetry["discovery_elapsed_s"] = time.monotonic() - _discovery_start
                # Handle DiscoveryBatchResult or list
                live_list = list(live_hits) if hasattr(live_hits, "__iter__") else []
                telemetry["live_candidates"] = len(live_list)
                for hit in live_list:
                    urls.append(hit.url)
                    titles.append(hit.title)
                    snippets.append(hit.snippet)
                    ranks.append(hit.rank)
                    discovery_scores.append(hit.score)
                    fetch_blocked_reasons.append(None)
                    errors.append(None)
            except Exception as exc:
                telemetry["discovery_error"] = str(exc)
                telemetry["discovery_error_type"] = type(exc).__name__
                logger.warning(f"Discovery live search failed: {exc}")

            telemetry["discovery_result"] = "success"

        except Exception as exc:
            telemetry["discovery_error"] = str(exc)
            telemetry["discovery_error_type"] = "bootstrap_error"
            logger.exception(f"Discovery stage failed: {exc}")

        batch = PageBatch(
            urls=urls,
            titles=titles,
            snippets=snippets,
            ranks=ranks,
            discovery_scores=discovery_scores,
            fetch_blocked_reasons=fetch_blocked_reasons,
            errors=errors,
        )

        return batch, telemetry


def _generate_rescue_hits(query: str, max_urls: int = 8) -> list[Any]:
    """Generate rescue DiscoveryHits for threat queries."""
    # Lazy import to avoid circular dependency
    try:
        from hledac.universal.pipeline.live_public_pipeline import (
            _is_threat_query,
            generate_rescue_urls,
        )

        if not _is_threat_query(query):
            return []
        return generate_rescue_urls(query, max_urls=max_urls)
    except Exception:
        return []


def _generate_keyword_urls(query: str, max_urls: int = 8) -> list[str]:
    """Generate keyword-based search engine URLs."""
    import urllib.parse

    # Static search engine candidates
    engines = [
        "https://duckduckgo.com/?q=",
        "https://www.google.com/search?q=",
        "https://search.brave.com/search?q=",
    ]

    urls = []
    for engine in engines[:max_urls]:
        encoded = urllib.parse.quote(query.strip())
        urls.append(f"{engine}{encoded}")
    return urls
