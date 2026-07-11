"""
intelligence/network_reconnaissance_lane.py — F320+: Network Reconnaissance Lane

Thin subclass of BaseIntelligenceLane for DNS/WHOIS/SSL enumeration.
Wraps DNSEnumerator + WHOISLookup + SSLAnalyzer from network_reconnaissance.py.

LaneSpec:
    concurrent_queries=4 (DNS is parallelizable, moderate cost)
    cost_estimate_per_query=1 (lightweight per-query cost)
"""


import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from hledac.universal.intelligence.lane import (
    BaseIntelligenceLane,
    FetchResult,
    IPV4_PATTERN,
    IPV6_PATTERN,
    LaneContext,
    LaneSpec,
    ParsedResult,
    ResolveResult,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class NetworkReconnaissanceLane(BaseIntelligenceLane):
    """
    Network reconnaissance lane for DNS/WHOIS/SSL enumeration.

    Env gate: HLEDAC_ENABLE_NETWORK_RECON (default: off — opt-in)
    Priority: 5 (medium — supplementary to main acquisition lanes)
    RAM budget: 50 MB

    Phase implementation:
        resolve: classify target as domain/ipv4/ipv6/hostname
        fetch: perform DNS query, WHOIS lookup, or SSL analysis
        parse: extract IPs, ASNs, nameservers, contacts, cert data
        dedup: inherited — uses target+type as key
        emit: inherited — one finding per IOC type
    """

    __slots__ = ("_dns", "_whois", "_ssl", "_passive_dns")

    sidecar_id: str = "network_recon"
    env_gate: str = "HLEDAC_ENABLE_NETWORK_RECON"
    ram_budget_mb: int = 50
    priority: int = 5
    lane_spec: LaneSpec = LaneSpec(concurrent_queries=4, cost_estimate_per_query=1)

    MAX_BLOOM_ENTRIES: int = 5000
    MAX_CACHE_SIZE: int = 500

    def __init__(self) -> None:
        super().__init__()
        self._dns: Any | None = None
        self._whois: Any | None = None
        self._ssl: Any | None = None
        self._passive_dns: Any | None = None

    # -------------------------------------------------------------------------
    # Phase 1: Resolve
    # -------------------------------------------------------------------------

    async def resolve(self, target: str, ctx: LaneContext) -> ResolveResult:
        """
        Classify target as domain, IPv4, IPv6, or hostname.

        Returns ResolveResult with kind and resolved string.
        Uses sprint_mode from ctx to adjust resolution scope.
        """
        import socket

        target = target.strip()
        aggressive = ctx.sprint_mode == "aggressive"

        # IPv4
        try:
            socket.inet_pton(socket.AF_INET, target)
            return ResolveResult(resolved=target, kind="ipv4", metadata={"aggressive": aggressive})
        except OSError:
            pass

        # IPv6
        try:
            socket.inet_pton(socket.AF_INET6, target)
            return ResolveResult(resolved=target, kind="ipv6", metadata={"aggressive": aggressive})
        except OSError:
            pass

        # Domain / hostname
        return ResolveResult(resolved=target, kind="domain", metadata={"aggressive": aggressive})

    # -------------------------------------------------------------------------
    # Phase 2: Fetch
    # -------------------------------------------------------------------------

    async def fetch(self, resolved: ResolveResult, ctx: LaneContext) -> FetchResult:
        """
        Perform network reconnaissance for the resolved target.

        Resolves DNS records, WHOIS data, or SSL certificate info
        based on the target kind.
        """
        dns = await self._get_dns()
        if dns is None:
            return FetchResult(url=resolved.resolved, status_code=0, error="dns_unavailable")

        semaphore = self._get_semaphore()
        async with semaphore:
            start = time.monotonic()
            try:
                kind = resolved.kind

                if kind == "ipv4" or kind == "ipv6":
                    # Reverse DNS lookup
                    result = await dns.reverse_lookup(resolved.resolved)
                    elapsed_ms = (time.monotonic() - start) * 1000
                    return FetchResult(
                        url=resolved.resolved,
                        status_code=200 if result else 404,
                        body="\n".join(result) if result else "",
                        elapsed_ms=elapsed_ms,
                    )

                else:  # domain
                    # Comprehensive DNS enumeration (lightweight — no brute force by default)
                    aggressive = ctx.sprint_mode == "aggressive"
                    result = await dns.enumerate_all(
                        resolved.resolved,
                        include_subdomains=aggressive,
                    )
                    elapsed_ms = (time.monotonic() - start) * 1000
                    return FetchResult(
                        url=resolved.resolved,
                        status_code=200,
                        body=str(result),  # Simple string repr
                        elapsed_ms=elapsed_ms,
                    )

            except TimeoutError:
                return FetchResult(
                    url=resolved.resolved,
                    status_code=0,
                    error="timeout",
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )
            except Exception as exc:
                logger.debug("network_recon_lane.fetch error: %s", exc)
                return FetchResult(
                    url=resolved.resolved,
                    status_code=0,
                    error=f"fetch_error:{type(exc).__name__}",
                    elapsed_ms=(time.monotonic() - start) * 1000,
                )

    # -------------------------------------------------------------------------
    # Phase 3: Parse
    # -------------------------------------------------------------------------

    async def parse(self, fetch_result: FetchResult, ctx: LaneContext) -> ParsedResult:
        """
        Parse network reconnaissance results for IOCs.

        Extracts: IP addresses, domains, nameservers, ASN info.
        memory_pressure from ctx limits IOC extraction scope when under pressure.
        """
        if fetch_result.error:
            return ParsedResult(raw_payload="", confidence=0.0)

        body = fetch_result.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="ignore")

        # Memory-pressure-adaptive: cap max items per type
        mp = ctx.memory_pressure
        max_per_type = 100 if mp < 0.5 else (30 if mp < 0.8 else 10)

        import re

        iocs: dict[str, list[str]] = {}

        # IPv4 addresses (shared pattern from lane.py)
        iocs["ipv4"] = list(set(IPV4_PATTERN.findall(body)))[:max_per_type]

        # IPv6 addresses (shared pattern from lane.py)
        iocs["ipv6"] = list(set(IPV6_PATTERN.findall(body)))[:max_per_type]

        # Domain names (common TLDs)
        domain_pattern = re.compile(
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
            r"(?:com|net|org|io|co|ai|dev|app|xyz|info|biz|ru|cn|de|uk|fr|nl|br|au|ca|jp|in)\b"
        )
        iocs["domain"] = list(set(domain_pattern.findall(body)))[:max_per_type]

        # Nameservers (ns1., ns2., etc.)
        ns_pattern = re.compile(r"\bns[0-9]?\.[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", re.IGNORECASE)
        iocs["nameserver"] = list(set(ns_pattern.findall(body)))[:max_per_type]

        # Mail servers (mx1., mail., etc.)
        mx_pattern = re.compile(r"\bmx[0-9]?\.[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b", re.IGNORECASE)
        iocs["mailserver"] = list(set(mx_pattern.findall(body)))[:max_per_type]

        # Remove empty
        iocs = {k: v for k, v in iocs.items() if v}

        return ParsedResult(
            iocs=iocs,
            raw_payload=body[:2000],
            title=f"Network recon: {fetch_result.url}",
            confidence=0.8 if iocs else 0.3,
            metadata={"kind": "dns_enumeration", "status": fetch_result.status_code},
        )

    # -------------------------------------------------------------------------
    # Lazy initialization helpers
    # -------------------------------------------------------------------------

    async def _get_dns(self) -> Any | None:
        """Lazy-initialize DNSEnumerator."""
        if self._dns is not None:
            return self._dns
        try:
            from intelligence.network_reconnaissance import DNSEnumerator
            self._dns = DNSEnumerator()
            return self._dns
        except ImportError:
            return None

    async def _get_whois(self) -> Any | None:
        """Lazy-initialize WHOISLookup."""
        if self._whois is not None:
            return self._whois
        try:
            from intelligence.network_reconnaissance import WHOISLookup
            self._whois = WHOISLookup()
            return self._whois
        except ImportError:
            return None

    async def _get_ssl(self) -> Any | None:
        """Lazy-initialize SSLAnalyzer."""
        if self._ssl is not None:
            return self._ssl
        try:
            from intelligence.network_reconnaissance import SSLAnalyzer
            self._ssl = SSLAnalyzer()
            return self._ssl
        except ImportError:
            return None

    async def _get_passive_dns(self) -> Any | None:
        """Lazy-initialize PassiveDNSClient."""
        if self._passive_dns is not None:
            return self._passive_dns
        try:
            from intelligence.network_reconnaissance import PassiveDNSClient
            self._passive_dns = PassiveDNSClient()
            return self._passive_dns
        except ImportError:
            return None


__all__ = ["NetworkReconnaissanceLane"]
