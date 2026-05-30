"""
runtime/sidecar_protocol_adapters.py — F350M-R: Protocol-Based Sidecar Adapters
==============================================================================

Protocol-based plugin adapters for orphaned sidecar modules.
Each adapter wraps an existing module and exposes SidecarAdapterProtocol.

Registered via @SidecarRegistry.register decorator.
Env gates and RAM budgets configured per sidecar.
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.sidecar_protocol import (
    BaseSidecarAdapter,
    SidecarContext,
    SidecarRegistry,
)

logger = logging.getLogger(__name__)


# ── Fediverse Sidecar ──────────────────────────────────────────────────────────

@SidecarRegistry.register("fediverse")
class FediverseSidecarAdapter(BaseSidecarAdapter):
    """
    Fediverse/Mastodon Intelligence Sidecar.

    Searches public Mastodon/Fediverse instances for OSINT signals.
    M1-safe: max 2 concurrent instances, 10s timeout per request.

    Env: HLEDAC_ENABLE_FEDIVERSE=1
    RAM: 50MB budget
    Priority: 6 (higher than core sidecars)
    """

    sidecar_id: str = "fediverse"
    env_gate: str = "HLEDAC_ENABLE_FEDIVERSE"
    ram_budget_mb: int = 50
    priority: int = 6

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Search Fediverse for OSINT signals based on query and findings."""
        if not ctx.findings and not ctx.query:
            return []

        try:
            from hledac.universal.discovery.fediverse_adapter import FediverseAdapter, FediverseResult
        except Exception:
            logger.debug("FediverseSidecarAdapter: import failed")
            return []

        try:
            adapter = FediverseAdapter()

            # Extract search terms from findings
            search_terms = self._extract_search_terms(ctx)
            if not search_terms:
                search_terms = [ctx.query] if ctx.query else []

            # Limit search terms for M1 safety
            search_terms = search_terms[:5]

            results: list[FediverseResult] = await adapter.search_multiple_instances(search_terms)

            # Convert to findings
            findings = []
            for result in results:
                for post in result.posts:
                    finding = self._make_finding(post, ctx)
                    if finding:
                        findings.append(finding)

            return findings[:50]  # Cap at 50 findings

        except Exception:
            logger.warning("FediverseSidecarAdapter.run: fail-soft", exc_info=True)
            return []

    def _extract_search_terms(self, ctx: SidecarContext) -> list[str]:
        """Extract domain/IOC terms from findings for Fediverse search."""
        terms: list[str] = []
        for finding in ctx.findings[:20]:  # Sample first 20
            ioc_value = getattr(finding, "ioc_value", None)
            if ioc_value and len(ioc_value) < 100:
                terms.append(ioc_value)
        return terms[:10]

    def _make_finding(self, post: dict, ctx: SidecarContext) -> dict | None:
        """Construct a CanonicalFinding-compatible dict from Fediverse post."""
        try:
            return {
                "source_type": "fediverse",
                "query": ctx.query,
                "sprint_id": ctx.sprint_id,
                "ioc_type": "social_media_post",
                "ioc_value": post.get("url", post.get("id", "")),
                "confidence": 0.6,
                "payload_text": f"{post.get('content', '')} | @{post.get('account', {}).get('username', 'unknown')}",
            }
        except Exception:
            return None


# ── DHT Sidecar ────────────────────────────────────────────────────────────────

@SidecarRegistry.register("dht")
class DHTSidecarAdapter(BaseSidecarAdapter):
    """
    DHT (BitTorrent Kademlia) Discovery Sidecar.

    Queries DHT network for torrent metadata matching keywords.
    BEP-05 based discovery for content invisible to web crawlers.

    Env: HLEDAC_ENABLE_DHT=1
    RAM: 100MB budget
    Priority: 4 (lower priority, experimental)
    """

    sidecar_id: str = "dht"
    env_gate: str = "HLEDAC_ENABLE_DHT"
    ram_budget_mb: int = 100
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Query DHT network for content hashes matching query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.discovery.dht_adapter import DHTAdapter, DHTResult
        except Exception:
            logger.debug("DHTSidecarAdapter: import failed")
            return []

        try:
            adapter = DHTAdapter()
            results = await adapter.search_dht(ctx.query)

            findings = []
            for result in results[:20]:  # Cap at 20
                finding = {
                    "source_type": "dht",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "dht_infohash",
                    "ioc_value": result.infohash,
                    "confidence": 0.5,
                    "payload_text": result.display_name or "",
                }
                findings.append(finding)

            return findings

        except Exception:
            logger.warning("DHTSidecarAdapter.run: fail-soft", exc_info=True)
            return []


# ── Academic Sidecar ──────────────────────────────────────────────────────────

@SidecarRegistry.register("academic")
class AcademicSidecarAdapter(BaseSidecarAdapter):
    """
    Academic Research Intelligence Sidecar.

    Searches academic sources: arXiv, Semantic Scholar, OpenAlex, CORE, Unpaywall.
    Supports DOI resolution, PDF discovery, citation analysis.

    Env: HLEDAC_ENABLE_ACADEMIC=1
    RAM: 80MB budget
    Priority: 5 (medium priority, research-focused profiles)
    """

    sidecar_id: str = "academic"
    env_gate: str = "HLEDAC_ENABLE_ACADEMIC"
    ram_budget_mb: int = 80
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Search academic sources for research papers matching query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.discovery.academic import (
                AcademicOrchestrator,
                search_all_academic,
            )
        except Exception:
            logger.debug("AcademicSidecarAdapter: import failed")
            return []

        try:
            results = await search_all_academic(
                query=ctx.query,
                max_results=20,
                timeout_s=45,
            )

            findings = []
            for paper in results.papers[:10]:  # Cap at 10 papers
                finding = {
                    "source_type": "academic",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "academic_paper",
                    "ioc_value": paper.get("doi", paper.get("title", "")),
                    "confidence": 0.7,
                    "payload_text": paper.get("abstract", ""),
                }
                findings.append(finding)

            return findings

        except Exception:
            logger.warning("AcademicSidecarAdapter.run: fail-soft", exc_info=True)
            return []


# ── Alt Protocols Sidecar ──────────────────────────────────────────────────────

@SidecarRegistry.register("alt_protocols")
class AltProtocolSidecarAdapter(BaseSidecarAdapter):
    """
    Alternative Protocols Sidecar.

    Accesses content via IPFS, Gopher, Gemini, I2P protocols.
    Enables discovery of content invisible to standard web crawlers.

    Env: HLEDAC_ENABLE_ALT_PROTOCOLS=1
    RAM: 60MB budget
    Priority: 4 (lower priority, experimental)
    """

    sidecar_id: str = "alt_protocols"
    env_gate: str = "HLEDAC_ENABLE_ALT_PROTOCOLS"
    ram_budget_mb: int = 60
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Fetch content via alternative protocols based on query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.fetching.alternative_protocol_fetcher import (
                AlternativeProtocolFetcher,
                AltProtocolResult,
            )
        except Exception:
            logger.debug("AltProtocolSidecarAdapter: import failed")
            return []

        try:
            fetcher = AlternativeProtocolFetcher()

            # Extract CIDs/hashes from findings for IPFS lookup
            cids = self._extract_cids(ctx)

            findings = []

            # Fetch via alternative protocols
            if cids:
                for cid in cids[:5]:  # Limit to 5 CIDs
                    try:
                        result = await fetcher.fetch_ipfs(cid)
                        if result.success:
                            findings.append({
                                "source_type": "ipfs",
                                "query": ctx.query,
                                "sprint_id": ctx.sprint_id,
                                "ioc_type": "ipfs_cid",
                                "ioc_value": cid,
                                "confidence": 0.6,
                                "payload_text": f"IPFS content: {result.findings_count} items",
                            })
                    except Exception:
                        continue

            # Also try Gemini protocol for text queries
            if ctx.query:
                try:
                    result = await fetcher.fetch_gemini(ctx.query)
                    if result.success:
                        findings.append({
                            "source_type": "gemini",
                            "query": ctx.query,
                            "sprint_id": ctx.sprint_id,
                            "ioc_type": "gemini_content",
                            "ioc_value": ctx.query[:256],
                            "confidence": 0.5,
                            "payload_text": f"Gemini content: {result.findings_count} items",
                        })
                except Exception:
                    pass

            return findings

        except Exception:
            logger.warning("AltProtocolSidecarAdapter.run: fail-soft", exc_info=True)
            return []

    def _extract_cids(self, ctx: SidecarContext) -> list[str]:
        """Extract IPFS CIDs from findings."""
        cids: list[str] = []
        for finding in ctx.findings[:30]:
            ioc_value = getattr(finding, "ioc_value", "")
            # Simple CID detection (starts with Qm or bafy)
            if ioc_value.startswith(("Qm", "bafy")):
                cids.append(ioc_value)
        return cids


# ── Leak Sentinel Sidecar ──────────────────────────────────────────────────────

@SidecarRegistry.register("leak_sentinel")
class LeakSentinelSidecarAdapter(BaseSidecarAdapter):
    """
    Leak Sentinel Sidecar.

    Monitors paste sites, GitHub secret scanner, breach databases.
    Redacts PII before storing findings.

    Env: HLEDAC_ENABLE_LEAKSENTINEL=1
    RAM: 30MB budget
    Priority: 3 (lower priority, optional enrichment)
    """

    sidecar_id: str = "leak_sentinel"
    env_gate: str = "HLEDAC_ENABLE_LEAKSENTINEL"
    ram_budget_mb: int = 30
    priority: int = 3

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Scan for leaked credentials/data related to query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.intelligence.leak_sentinel import (
                LeakSentinelAdapter,
                LeakSentinelResult,
            )
        except Exception:
            logger.debug("LeakSentinelSidecarAdapter: import failed")
            return []

        try:
            adapter = LeakSentinelAdapter()

            # Extract domains/identifiers for leak search
            targets = self._extract_targets(ctx)
            if not targets:
                targets = [ctx.query]

            results = await adapter.scan_all_sources(targets)

            findings = []
            for result in results.sources:
                for finding in result.findings[:10]:  # Cap per source
                    findings.append({
                        "source_type": f"leak_{result.source}",
                        "query": ctx.query,
                        "sprint_id": ctx.sprint_id,
                        "ioc_type": "leak_detection",
                        "ioc_value": finding.get("url", finding.get("id", "")),
                        "confidence": 0.7,
                        "payload_text": finding.get("content", ""),
                    })

            return findings[:50]  # Cap total findings

        except Exception:
            logger.warning("LeakSentinelSidecarAdapter.run: fail-soft", exc_info=True)
            return []

    def _extract_targets(self, ctx: SidecarContext) -> list[str]:
        """Extract domains/emails from findings for leak search."""
        targets: list[str] = []
        for finding in ctx.findings[:30]:
            ioc_value = getattr(finding, "ioc_value", "")
            ioc_type = getattr(finding, "ioc_type", "")
            if ioc_type in ("domain", "email", "username") and ioc_value:
                targets.append(ioc_value)
        return targets[:10]
