"""
Wayback CDX Deep Adapter — Sprint F234

Wraps WaybackCDXDeepSearch for the advisory sidecar seam in sidecar_orchestrator.

Role: advisory sidecar, NOT the main write path.
Wayback CDX analysis is non-blocking and fail-soft — errors never crash the sprint.

M1 8GB: No model load, pure I/O with bounded results.
"""
from __future__ import annotations


import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult

logger = logging.getLogger(__name__)


def create_wayback_cdx_deep_adapter() -> WaybackCDXDeepAdapter:
    """Factory — returns adapter instance."""
    return WaybackCDXDeepAdapter()


class WaybackCDXDeepAdapter:
    """
    Wayback CDX deep search advisory: archived URL discovery for domain indicators.

    Fail-soft: any error returns silently without crashing the sprint.
    """

    __slots__ = ("_adapter",)

    def __init__(self) -> None:
        self._adapter: object | None = None

    async def analyze(self, result: SprintSchedulerResult) -> None:
        """
        Perform Wayback CDX deep search on domain indicators from the sprint result.

        Looks at result.accepted_findings for domain-type IOCs and queries
        the Wayback Machine CDX API for archived URLs no longer on live web.

        Args:
            result: SprintSchedulerResult with accepted_findings from the sprint.
        """
        try:
            from hledac.universal.intelligence.wayback_cdx import WaybackCDXDeepSearch
        except ImportError:
            logger.debug("[WaybackCDXDeep] wayback_cdx unavailable, skipping")
            return

        try:
            findings: list = getattr(result, "accepted_findings", None) or []
            domain_values = [
                getattr(f, "ioc_value", "")
                for f in findings
                if getattr(f, "ioc_type", None) == "domain"
            ]
            if not domain_values:
                return

            domains_to_query = domain_values[:20]  # cap at 20 domains per sprint
            adapter = WaybackCDXDeepSearch()
            self._adapter = adapter

            try:
                cdx_results = await adapter.search(
                    domains_to_query,
                    match_type="domain",
                    limit_per_domain=100,
                    concurrency=3,
                )
                findings_count = len(getattr(cdx_results, "findings", []) or [])
                logger.debug(
                    "[WaybackCDXDeep] %d domains → %d archived results",
                    len(domains_to_query),
                    findings_count,
                )
            finally:
                await adapter.close()
        except Exception:
            # fail-soft: overall failure doesn't crash the sprint
            pass
