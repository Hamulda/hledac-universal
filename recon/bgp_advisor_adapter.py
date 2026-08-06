"""
BGP Advisor Adapter — Sprint F234

Wraps BGPLane for the advisory sidecar seam in sidecar_orchestrator.


Role: advisory sidecar, NOT the main write path.
BGP analysis is non-blocking and fail-soft — errors never crash the sprint.

M1 8GB: No model load, pure I/O with bounded results.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult

logger = logging.getLogger(__name__)


def create_bgp_advisor_adapter() -> BGPAdvisorAdapter:
    """Factory — returns adapter instance."""
    return BGPAdvisorAdapter()


class BGPAdvisorAdapter:
    """
    BGP advisory: ASN/path analysis for IP indicators in the sprint result.

    Fail-soft: any error returns silently without crashing the sprint.
    """

    __slots__ = ("_adapter",)

    def __init__(self) -> None:
        self._adapter: object | None = None

    def analyze(self, result: SprintSchedulerResult) -> None:
        """
        Perform BGP enrichment on IP indicators found in the sprint result.

        Looks at result.accepted_findings for IP-type IOCs and queries
        bgpview.io / RIPE stat for ASN, prefix, and org attribution.

        Args:
            result: SprintSchedulerResult with accepted_findings from the sprint.
        """
        try:
            import httpx

            from hledac.universal.recon.bgp_lane import BGPAdapter
        except ImportError:
            logger.debug("[BGPAdvisor] bgp_lane unavailable, skipping")
            return

        try:
            findings: list = getattr(result, "accepted_findings", None) or []
            ip_values = [
                getattr(f, "ioc_value", "") for f in findings if getattr(f, "ioc_type", None) in ("ip", "ipv4")
            ]
            if not ip_values:
                return

            ips_to_query = ip_values[:50]
            adapter = BGPAdapter()
            # Run async enrich in thread pool to keep analyze() sync (fire-and-forget)
            asyncio.get_running_loop().run_in_executor(  # noqa: RCO520
                None,
                _sync_enrich_ips,
                adapter,
                ips_to_query,
            )
            self._adapter = adapter
        except Exception:
            # fail-soft: overall failure doesn't crash the sprint
            pass


def _sync_enrich_ips(adapter: BGPAdapter, ips: list[str]) -> None:
    """Sync wrapper: run async enrich_ip for each IP in a dedicated session."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for ip in ips:
                try:
                    result = loop.run_until_complete(adapter.enrich_ip(ip))
                    if result and result.asn:
                        logger.debug(
                            "[BGP] %s → ASN %s / %s / %s",
                            ip,
                            result.asn,
                            result.prefix or "unknown",
                            result.org_name or "unknown",
                        )
                except Exception:
                    pass  # fail-soft per-IP
        finally:
            loop.close()
    except Exception:
        pass  # fail-soft overall
