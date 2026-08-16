"""
runtime/sidecar_protocol_adapters.py — F350M-R: Protocol-Based Sidecar Adapters
==============================================================================




















Protocol-based plugin adapters for orphaned sidecar modules.
Each adapter wraps an existing module and exposes SidecarAdapterProtocol.

Registered via @SidecarRegistry.register decorator.
Env gates and RAM budgets configured per sidecar.
"""



import collections.abc
import logging
import os
import re
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

from hledac.universal.runtime.sidecar_protocol import (
    BaseSidecarAdapter,
    CorrelateBasedSidecarAdapter,
    GenericSidecarAdapter,
    SidecarContext,
    SidecarRegistry,
)

logger = logging.getLogger(__name__)

# ── Shared re pattern for URL extraction (compiled once, reused) ─────────────────
_URL_RE = re.compile(
    r"https?://(?:www\.)?([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z]{2,})+/?)"
)


# ── Fediverse Sidecar ──────────────────────────────────────────────────────────

# Protocol-based interface for FediverseAdapter (decouples sidecar from concrete implementation)
@runtime_checkable
class FediverseSearchEngine(Protocol):
    """Protocol for Fediverse search engines — enables testing and alternative implementations."""

    async def search_multiple_instances(
        self, terms: list[str], max_results: int = 50, instances: list[str] | None = None
    ) -> list[Any]: ...


def _default_fediverse_adapter_factory() -> Any:  # noqa: ANN401
    """Lazy factory: imports and instantiates FediverseAdapter only when called."""
    from hledac.universal.discovery.fediverse_adapter import FediverseAdapter
    return FediverseAdapter()


@SidecarRegistry.register("fediverse")
class FediverseSidecarAdapter(GenericSidecarAdapter):
    """
    Fediverse/Mastodon Intelligence Sidecar.

    Searches public Mastodon/Fediverse instances for OSINT signals.
    M1-safe: max 2 concurrent instances, 10s timeout per request.

    Env: HLEDAC_ENABLE_FEDIVERSE=1
    RAM: 50MB budget
    Priority: 6 (higher than core sidecars)

    F360M: Migrated to GenericSidecarAdapter.
    Uses extract_terms + search + result_to_finding pattern.
    result_to_finding returns list[dict] because one search result
    may contain multiple posts.
    """

    sidecar_id: str = "fediverse"
    lane_id: str = "fediverse"
    ram_budget_mb: int = 50
    priority: int = 6

    def extract_terms(self, ctx: SidecarContext) -> list[str]:
        """Extract domain/IOC terms from findings for Fediverse search."""
        terms: list[str] = []
        for finding in ctx.findings[:20]:  # Sample first 20
            ioc_value = getattr(finding, "ioc_value", None)
            if ioc_value and len(ioc_value) < 100:
                terms.append(ioc_value)
        return terms[:10]

    async def search(self, terms: list[str], ctx: SidecarContext) -> list[Any]:
        """Search Fediverse instances for each term.

        Note: ctx is accepted for interface compatibility but not used.
        """
        del ctx  # explicitly unused
        if not terms:
            return []
        try:
            adapter = self._adapter_factory()
        except Exception:
            logger.debug("FediverseSidecarAdapter: adapter factory failed")
            return []

        try:
            return await adapter.search_multiple_instances(terms[:5])
        except Exception:
            logger.warning("FediverseSidecarAdapter.search: fail-soft", exc_info=True)
            return []

    def result_to_finding(self, result: Any, ctx: SidecarContext) -> dict | list[dict] | None:  # noqa: ANN401
        """Transform Fediverse search results to finding dicts.

        One search result may contain multiple posts — returns a list of dicts.
        """
        try:
            posts = getattr(result, "posts", [result])
            if not posts:
                return None
            findings = []
            for post in posts:
                # Normalize: dataclass → dict, dict stays, anything else → None.
                if hasattr(post, "to_dict") and callable(post.to_dict):
                    post_dict = post.to_dict()
                elif isinstance(post, dict):
                    post_dict = post
                else:
                    continue
                findings.append({
                    "source_type": "fediverse",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "social_media_post",
                    "ioc_value": post_dict.get("url", post_dict.get("id", "")),
                    "confidence": 0.6,
                    "payload_text": (
                        f"{post_dict.get('content', '')} | "
                        f"@{post_dict.get('account', {}).get('username', 'unknown')}"
                    ),
                })
            return findings if findings else None
        except Exception:
            return None

    # Injectable factory for FediverseAdapter — defaults to lazy import
    _adapter_factory: collections.abc.Callable[[], Any] = _default_fediverse_adapter_factory


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
    lane_id: str = "dht"
    ram_budget_mb: int = 100
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Query DHT network for content hashes matching query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.discovery.dht_adapter import async_search_dht
        except Exception:
            logger.debug("DHTSidecarAdapter: import failed")
            return []

        try:
            result = await async_search_dht(ctx.query, max_results=20, timeout_s=30.0)

            findings = []
            for hit in result.hits[:20]:  # Cap at 20
                finding = {
                    "source_type": "dht",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "dht_infohash",
                    "ioc_value": hit.url,  # url contains infohash in DHT context
                    "confidence": 0.5,
                    "payload_text": hit.title or "",
                }
                findings.append(finding)

            return findings

        except Exception:
            logger.warning("DHTSidecarAdapter.run: fail-soft", exc_info=True)
            return []


# ── DHT Leak Harvest Sidecar (ISSUE-006) ───────────────────────────────────────

@SidecarRegistry.register("dht_leak_harvest")
class DHTLeakHarvestSidecarAdapter(BaseSidecarAdapter):
    """
    DHT Leak Metadata Harvest Sidecar — ISSUE-006.

    Extends standard DHT discovery with full metadata harvesting:
      DHT keyword crawl -> BEP-9 metadata fetch -> IOC extraction

    Discovers info_hashes via DHT keyword crawling, then harvests full
    torrent metadata (file names, tracker URLs, creator comments) using
    BEP-9 metadata extension. Extracts IOCs from harvested metadata.

    Env: HLEDAC_ENABLE_DHT=1 AND HLEDAC_ENABLE_DHT_METADATA_HARVEST=1
    RAM: 150MB budget (metadata fetch is I/O-bound but caches heavy)
    Priority: 5 (lowest priority — optional enrichment sidecar)
    """

    sidecar_id: str = "dht_leak_harvest"
    lane_id: str = "dht"
    ram_budget_mb: int = 150
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Run DHT keyword crawl with metadata harvesting and IOC extraction."""
        if not ctx.query:
            return []

        # Both gates must be enabled
        import os
        if os.getenv("HLEDAC_ENABLE_DHT", "0").lower() not in ("1", "true", "yes", "on"):
            return []
        if os.getenv("HLEDAC_ENABLE_DHT_METADATA_HARVEST", "0").lower() not in ("1", "true", "yes", "on"):
            return []

        try:
            from hledac.universal.dht.kademlia_node import crawl_dht_for_keyword
        except Exception:
            logger.debug("DHTLeakHarvestSidecarAdapter: kademlia_node import failed")
            return []

        try:
            # Run DHT crawl with metadata harvesting enabled (ISSUE-006)
            crawl_results = await crawl_dht_for_keyword(
                ctx.query,
                duration_s=60,  # Shorter duration for sidecar context
                max_results=50,
                harvest_metadata=True,  # This triggers post-crawl IOC extraction
            )

            # Convert crawl results to findings format expected by sidecar framework
            findings = []
            for hit in crawl_results[:30]:
                name = hit.get('name', '')
                info_hash = hit.get('info_hash', '')
                finding = {
                    "source_type": "dht_metadata",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "dht_torrent_metadata",
                    "ioc_value": info_hash,
                    "confidence": 0.7,
                    "payload_text": (
                        f"Name: {name}\n"
                        f"InfoHash: {info_hash}\n"
                        f"Files: {len(hit.get('files', []))}\n"
                        f"Size: {hit.get('size_bytes', 0):,} bytes\n"
                        f"Peers: {hit.get('peers', 0)}"
                    ),
                }
                findings.append(finding)

            if findings:
                logger.info(
                    "DHTLeakHarvestSidecar: %d findings for query '%s'",
                    len(findings), ctx.query[:50],
                )
            return findings

        except Exception:
            logger.warning("DHTLeakHarvestSidecarAdapter.run: fail-soft", exc_info=True)
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
    lane_id: str = "academic"
    ram_budget_mb: int = 80
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Search academic sources for research papers matching query."""
        # P1-1: Skip academic sidecar in aggressive mode — saves ~50s from prelude
        if ctx.sprint_mode == "aggressive":
            return []

        if not ctx.query:
            return []

        try:
            from hledac.universal.discovery.academic import (
                search_all_academic,
            )
        except Exception:
            logger.debug("AcademicSidecarAdapter: import failed")
            return []

        try:
            results = await search_all_academic(
                query=ctx.query,
                max_results_per_source=5,  # P1-1: 5 per source × 4 sources = 20 total
            )

            findings = []
            # results is dict[str, list[CanonicalFinding]]
            for source, papers in results.items():
                for paper in papers[:3]:  # Cap 3 per source
                    payload = getattr(paper, "payload_text", "") or ""
                    finding = {
                        "source_type": source,
                        "query": ctx.query,
                        "sprint_id": ctx.sprint_id,
                        "ioc_type": "academic_paper",
                        "ioc_value": payload[:100] if payload else source,
                        "confidence": 0.7,
                        "payload_text": payload[:500],
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
    lane_id: str = "alt_protocols"
    ram_budget_mb: int = 60
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:  # noqa: C901
        """Fetch content via alternative protocols based on query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.fetching.alternative_protocol_fetcher import (
                fetch_gemini_only,
                fetch_ipfs_only,
            )
        except Exception:
            logger.debug("AltProtocolSidecarAdapter: import failed")
            return []

        try:
            # Extract CIDs/hashes from findings for IPFS lookup
            cids = self._extract_cids(ctx)

            findings = []

            # Fetch via IPFS using the correct API
            if cids:
                for cid in cids[:5]:  # Limit to 5 CIDs
                    try:
                        results = await fetch_ipfs_only(cid)
                        if results:
                            findings.append({
                                "source_type": "ipfs",
                                "query": ctx.query,
                                "sprint_id": ctx.sprint_id,
                                "ioc_type": "ipfs_cid",
                                "ioc_value": cid,
                                "confidence": 0.6,
                                "payload_text": f"IPFS content: {len(results)} items",
                            })
                    except Exception:
                        continue

            # Also try Gemini protocol for text queries
            if ctx.query:
                try:
                    results = await fetch_gemini_only(ctx.query)
                    if results:
                        findings.append({
                            "source_type": "gemini",
                            "query": ctx.query,
                            "sprint_id": ctx.sprint_id,
                            "ioc_type": "gemini_content",
                            "ioc_value": ctx.query[:256],
                            "confidence": 0.5,
                            "payload_text": f"Gemini content: {len(results)} items",
                        })
                except Exception:  # noqa: BLE001
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
    lane_id: str = "leak_sentinel"
    ram_budget_mb: int = 30
    priority: int = 3

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Scan for leaked credentials/data related to query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.recon.leak_sentinel import (
                LeakSentinelAdapter,
            )
        except Exception:
            logger.debug("LeakSentinelSidecarAdapter: import failed")
            return []

        try:
            adapter = LeakSentinelAdapter()

            # scan() takes a single query string, returns list[CanonicalFinding]
            findings = await adapter.scan(ctx.query)

            # Convert CanonicalFinding objects to dicts
            result = []
            for finding in findings[:50]:  # Cap at 50
                result.append({
                    "source_type": getattr(finding, "source_type", "leak"),
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "leak_detection",
                    "ioc_value": getattr(finding, "ioc_value", "") or getattr(finding, "finding_id", ""),
                    "confidence": getattr(finding, "confidence", 0.7),
                    "payload_text": getattr(finding, "payload_text", ""),
                })

            return result

        except Exception:
            logger.warning("LeakSentinelSidecarAdapter.run: fail-soft", exc_info=True)
            return []

# ── TV News Sidecar ─────────────────────────────────────────────────────────────

@SidecarRegistry.register("tvnews")
class TVNewsSidecarAdapter(BaseSidecarAdapter):
    """
    Internet Archive TV News Sidecar.

    Searches TV News Archive for broadcast content matching OSINT queries.
    Uses Archive.org Advanced Search API with collection:tv filter.

    Env: HLEDAC_ENABLE_TV_NEWS=1
    RAM: 40MB budget
    Priority: 5 (medium priority, research/academic profiles)
    """

    sidecar_id: str = "tvnews"
    lane_id: str = "tvnews"
    ram_budget_mb: int = 40
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Search TV News Archive for broadcast content matching query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.discovery.tvnews_adapter import (
                search_tvnews_for_query,
            )
        except Exception:
            logger.debug("TVNewsSidecarAdapter: import failed")
            return []

        try:
            results = await search_tvnews_for_query(
                query=ctx.query,
                max_results=20,
                timeout_s=20.0,
            )

            findings = []
            for result in results[:20]:  # Cap at 20
                finding = {
                    "source_type": "tvnews",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": result.get("ioc_type", "tv_broadcast"),
                    "ioc_value": result.get("ioc_value", ""),
                    "confidence": result.get("confidence", 0.6),
                    "payload_text": f"Title: {result.get('title', '')}\nSnippet: {result.get('snippet', '')}",
                }
                if finding["ioc_value"]:
                    findings.append(finding)

            return findings

        except Exception:
            logger.warning("TVNewsSidecarAdapter.run: fail-soft", exc_info=True)
            return []


# ── Federated Research Sidecar (F350M-FED) ───────────────────────────────────

@SidecarRegistry.register("federated_research")
class FederatedResearchSidecarAdapter:  # duck-typed SidecarAdapterProtocol
    """
    Federated Multi-Node Research Sidecar.

    Wraps FederatedResearchCoordinator to expose the federated pattern
    (multi-virtual-node, parallel, dedup) through the canonical
    SidecarAdapterProtocol pipeline. Output is converted to
    CanonicalFinding (or dict fallback) with source_type="federated_research".

    This adapter does NOT inherit from BaseSidecarAdapter to keep the
    federated/ package zero-coupled to runtime.sidecar_protocol. The
    duck-typed subset of SidecarAdapterProtocol is sufficient:
        - sidecar_id, env_gate, ram_budget_mb, priority  (class attrs)
        - is_available()                                     (method)
        - async run(ctx) -> list                            (method, fail-soft)

    Env: HLEDAC_ENABLE_FEDERATED=1
    RAM: 30MB budget
    Priority: 5 (medium, runs alongside other research sidecars)
    """

    sidecar_id: str = "federated_research"
    lane_id: str = "federated"
    ram_budget_mb: int = 30
    priority: int = 5

    def is_available(self) -> bool:
        """Env-gated check delegating to the federated module's gate."""
        try:
            from hledac.universal.federated import is_federated_enabled
            return is_federated_enabled()
        except Exception:
            return False

    async def run(self, ctx: SidecarContext) -> list[Any]:
        """Fail-soft wrapper that delegates to the federated sidecar adapter."""
        try:
            from hledac.universal.federated.sidecar_adapter import (
                FederatedSidecarAdapter,
            )
            adapter = FederatedSidecarAdapter()
            return await adapter.run(ctx)
        except Exception:
            logger.warning(
                "FederatedResearchSidecarAdapter.run: fail-soft",
                exc_info=True,
            )
            return []


# ── Passive Fingerprint Sidecar (F350M-R) ───────────────────────────────────────

@SidecarRegistry.register("passive_fingerprint")
class PassiveFingerprintSidecarAdapter(CorrelateBasedSidecarAdapter):
    """
    F204G: Passive service fingerprinting — deterministic, no active scan.

    Migrated to CorrelateBasedSidecarAdapter (F360M).
    Wraps `intelligence.passive_fingerprint.create_passive_fingerprint_adapter`
    factory; invokes `adapter.correlate(findings, query)` and returns the
    derived CanonicalFindings.

    Env: HLEDAC_ENABLE_PASSIVE_FINGERPRINT=1
    RAM: 50MB budget
    Priority: 4 (research-tier)
    """

    sidecar_id: str = "passive_fingerprint"
    lane_id: str = "passive_fingerprint"
    ram_budget_mb: int = 50
    priority: int = 4

    def create_adapter(self) -> Any:  # noqa: ANN401
        from hledac.universal.recon.passive_fingerprint import (
            create_passive_fingerprint_adapter,
        )
        return create_passive_fingerprint_adapter()


# ── Passive Tech-Stack Sidecar (F350M-R / R11) ────────────────────────────────

@SidecarRegistry.register("passive_tech_stack")
class PassiveTechStackSidecarAdapter(CorrelateBasedSidecarAdapter):
    """
    R11: Passive tech-stack extraction — deterministic, no active scan.

    Migrated to CorrelateBasedSidecarAdapter (F360M).
    Wraps `intelligence.passive_fingerprint.create_passive_tech_stack_adapter`
    factory; calls `adapter.correlate(findings, query)`. Derived signal is
    identical to `passive_fingerprint` for tech-stack component, but exposed
    under its own registry ID for env-gated opt-in.

    Env: HLEDAC_ENABLE_PASSIVE_TECH_STACK=1
    RAM: 30MB budget
    Priority: 4 (research-tier)
    """

    sidecar_id: str = "passive_tech_stack"
    lane_id: str = "passive_tech_stack"
    ram_budget_mb: int = 30
    priority: int = 4

    def create_adapter(self) -> Any:  # noqa: ANN401
        from hledac.universal.recon.passive_fingerprint import (
            create_passive_tech_stack_adapter,
        )
        return create_passive_tech_stack_adapter()


# ── Social Identity Surface Sidecar (F350M-R / F204I) ────────────────────────

@SidecarRegistry.register("social_identity_surface")
class SocialIdentityMinerSidecarAdapter(BaseSidecarAdapter):
    """
    F204I: Social identity surface miner — extract usernames/profiles from findings.

    Wraps `intelligence.social_identity_miner.create_social_identity_miner_adapter`
    factory. `mine()` requires a `DuckDBShadowStore` instance which is not in
    SidecarContext, so the adapter is **wiring-only**: registers the sidecar
    for availability + env-gate, but returns `[]` from `run_async` so the
    canonical execution path (SprintScheduler with store handle) remains
    authoritative. This avoids double-execution of the social identity scan.

    Env: HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE=1
    RAM: 60MB budget
    Priority: 5
    """

    sidecar_id: str = "social_identity_surface"
    lane_id: str = "social_identity_surface"
    ram_budget_mb: int = 60
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        # ctx is accepted for SidecarAdapterProtocol compatibility but not used (wiring-only)
        del ctx  # explicitly unused — wiring-only pattern
        try:
            pass
        except Exception:
            logger.debug("SocialIdentityMinerSidecarAdapter: import failed")
            return []

        # mine() requires store handle (not in SidecarContext) — wiring-only
        return []


# ── Identity Stitching Sidecar (F350M-R / F202B) ──────────────────────────────

@SidecarRegistry.register("identity_stitching")
class IdentityStitchingSidecarAdapter(BaseSidecarAdapter):
    """
    F202B: Identity stitching engine — heavy, RAM-guarded by bus.

    Wraps `intelligence.identity_stitching.create_identity_stitching_engine`
    factory. The engine exposes a builder API (`add_profile`, `find_matches`,
    `find_all_matches`) that does not match the unified `correlate(findings,
    query)` contract used by other F350M-R adapters, so the adapter is
    **wiring-only**: registers the sidecar for availability + env-gate, with
    actual execution routed through the canonical
    `intelligence.identity_stitching_canonical.create_identity_stitching_adapter`
    path which the SprintScheduler invokes directly.

    Env: HLEDAC_ENABLE_IDENTITY_STITCHING=1
    RAM: 100MB budget
    Priority: 5
    """

    sidecar_id: str = "identity_stitching"
    lane_id: str = "identity_stitching"
    ram_budget_mb: int = 100
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        del ctx  # explicitly unused — wiring-only pattern
        # builder API mismatch — wiring-only, return empty
        return []


# ── Temporal Archaeology Sidecar (F350M-R / F202E) ────────────────────────────

@SidecarRegistry.register("temporal_archaeology")
class TemporalArchaeologySidecarAdapter(BaseSidecarAdapter):
    """
    F202E: Temporal archaeology timeline synthesis.

    Wraps `intelligence.temporal_archaeologist.create_temporal_archaeologist`
    factory. The archaeologist exposes a context-managed async API
    (`__aenter__`/`recover_deleted_content`/`reconstruct_version_history`)
    that does not match the unified `correlate(findings, query)` contract,
    so the adapter is **wiring-only**: registers the sidecar for
    availability + env-gate. Actual execution is routed through
    `intelligence.temporal_archaeologist_adapter.create_temporal_archaeologist_adapter`
    invoked by SprintScheduler with a CT-findings slice.

    Env: HLEDAC_ENABLE_TEMPORAL_ARCHAEOLOGY=1
    RAM: 80MB budget
    Priority: 4
    """

    sidecar_id: str = "temporal_archaeology"
    lane_id: str = "temporal_archaeology"
    ram_budget_mb: int = 80
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        del ctx  # explicitly unused — wiring-only pattern
        # Smoke-import the factory to validate module availability.
        try:
            pass
        except Exception:
            return []
        # context-managed API mismatch — wiring-only, return empty
        return []


# ── LanceDB RAG Sidecar — Sprint P2-3 Layer A ──────────────────────────────────
# Registry imports must be at bottom (after class definition).
# We import here to avoid circular deps.


@SidecarRegistry.register("lancedb_rag")
class LanceDBRAGSidecarAdapter(BaseSidecarAdapter):
    """
    Sprint P2-3 Layer A: Cross-sprint corpus mining sidecar.

    Embeds current sprint query + top findings into LanceDB "documents" table.
    Next sprint will retrieve similar queries as advisory seeds.

    Env: HLEDAC_ENABLE_GRAPH_RAG=1 (shares gate with GraphRAGOrchestrator)
    RAM: 60MB budget (M1 8GB safe)
    Priority: 7 (runs early — results available to next sprint)
    """

    sidecar_id: str = "lancedb_rag"
    lane_id: str = "lancedb_rag"
    ram_budget_mb: int = 60
    priority: int = 7

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Embed query + findings into LanceDB for cross-sprint persistence."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.knowledge.lancedb_rag_engine import LanceDBRAGEngine, RAGDocument
        except Exception:
            logger.debug("LanceDBRAGSidecarAdapter: import failed")
            return []

        try:
            rag = LanceDBRAGEngine()

            # Add current query as anchor document
            query_doc = RAGDocument(
                id=f"query:{ctx.sprint_id}",
                content=ctx.query,
                metadata={
                    "sprint_id": ctx.sprint_id,
                    "sprint_mode": ctx.sprint_mode,
                    "type": "query_anchor",
                },
            )
            await rag.add_document(query_doc)

            # Add top findings (up to 20) as evidence documents
            finding_docs = []
            for f in ctx.findings[:20]:
                content = getattr(f, "payload_text", "") or getattr(f, "query", "") or ""
                if not content:
                    continue
                doc = RAGDocument(
                    id=f"finding:{getattr(f, 'finding_id', 'unknown')}",
                    content=content[:2000],  # cap at 2KB
                    metadata={
                        "sprint_id": ctx.sprint_id,
                        "ioc_type": getattr(f, "ioc_type", "unknown"),
                        "confidence": getattr(f, "confidence", 0.5),
                        "type": "finding_evidence",
                    },
                )
                finding_docs.append(doc)

            if finding_docs:
                await rag.add_documents_batch(finding_docs, batch_size=16)

            # Search for similar past queries (for advisory output)
            similar = await rag.search(query=ctx.query, top_k=5, use_mmr=True)
            findings = []
            for i, chunk in enumerate(similar[:5]):
                findings.append({
                    "source_type": "lancedb_rag_corpus",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "corpus_similar",
                    "ioc_value": f"similar_query_{i}",
                    "confidence": chunk.final_score,
                    "payload_text": chunk.chunk_text[:1024],
                })

            return findings

        except Exception:
            logger.debug("LanceDBRAGSidecarAdapter.run: fail-soft", exc_info=True)
            return []


@SidecarRegistry.register("github_gist")
class GitHubGistSidecarAdapter(GenericSidecarAdapter):
    """
    GitHub Gist Archive Discovery Sidecar.

    Searches public GitHub Gists for OSINT signals matching the query
    or related IoCs from sprint findings. Uses the existing
    search_github_gists() function from ti_feed_adapter.

    Env: HLEDAC_ENABLE_GITHUB_GIST=1
    RAM: 30MB budget
    Priority: 5 (medium)

    F360M: Migrated to GenericSidecarAdapter — reduces ~65 LOC to ~40 LOC.
    """

    sidecar_id: str = "github_gist"
    lane_id: str = "github_gist"
    ram_budget_mb: int = 30
    priority: int = 5
    _max_results: int = 50

    def extract_terms(self, ctx: SidecarContext) -> list[str]:
        """Extract domain/IOC terms from findings for Gist search."""
        terms: list[str] = []
        for finding in ctx.findings[:20]:
            ioc_value = getattr(finding, "ioc_value", None)
            if ioc_value and len(ioc_value) < 100:
                terms.append(ioc_value)
        return terms[:10]

    async def search(self, terms: list[str], ctx: SidecarContext) -> list[dict]:
        """Search GitHub Gists for each term.

        Note: ctx is accepted for interface compatibility but not used.
        """
        del ctx  # explicitly unused
        from hledac.universal.discovery.ti_feed_adapter import search_github_gists

        all_results: list[dict] = []
        for term in terms[:5]:  # Limit for M1 safety
            try:
                results = await search_github_gists(term, max_results=10)
                all_results.extend(results)
            except Exception:  # noqa: BLE001 — fail-soft per term
                pass
        return all_results

    def result_to_finding(self, result: dict, ctx: SidecarContext) -> dict | None:
        """Transform a gist result to a finding dict."""
        return {
            "source_type": "github_gist",
            "query": ctx.query,
            "sprint_id": ctx.sprint_id,
            "ioc_type": "gist",
            "ioc_value": result.get("url", ""),
            "title": result.get("title", ""),
            "confidence": 0.6,
            "payload_text": result.get("snippet", ""),
        }


# ── JA4 TLS Fingerprint Collector Sidecar (F350M-R) ────────────────────────────

@SidecarRegistry.register("ja4_collector")
class JA4CollectorSidecarAdapter(BaseSidecarAdapter):
    """
    F350M-R: JA4 TLS fingerprint collector — server-side TCP fingerprinting.

    Performs TLS handshake to extract JA4 (Salesforce) fingerprint from target
    servers. JA4 = 13-char TCP fingerprint derived from TLS ClientHello.
    ECH (Encrypted Client Hello) detection included.

    Uses Rust tls13 module (rustls) for <1ms fingerprint extraction.
    Falls back to Python ssl analysis when Rust unavailable.

    Env: HLEDAC_ENABLE_JA4_COLLECTOR=1
    RAM: 30MB budget
    Priority: 5 (active reconnaissance tier)

    Integration:
        - recon/network_reconnaissance.py: SSLAnalyzer.ja4_fingerprint()
        - Rust tls13 module: rust.tls.connect_and_ja4() + ja4_from_client_hello()
    """

    sidecar_id: str = "ja4_collector"
    lane_id: str = "ja4_collector"
    ram_budget_mb: int = 30
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """
        Extract JA4 fingerprints from domain IOC values in findings.

        Returns list of findings with JA4 fingerprint data attached.
        """
        from hledac.universal.recon.network_reconnaissance import SSLAnalyzer

        # Extract domains from findings
        domains = self._extract_domains(ctx)
        if not domains:
            return []

        # Limit for M1 safety (max 20 concurrent connections)
        domains = domains[:20]

        try:
            ssl = SSLAnalyzer()
            results = await ssl.batch_ja4(
                [(d, 443) for d in domains],
                timeout_ms=5000,
            )

            findings = []
            for result in results:
                ja4 = result.get('ja4', '')
                if ja4 and ja4 != 'unknown':
                    findings.append({
                        'source_type': 'ja4_fingerprint',
                        'query': ctx.query,
                        'sprint_id': ctx.sprint_id,
                        'ioc_type': 'ja4_fingerprint',
                        'ioc_value': ja4,
                        'title': f"JA4: {result.get('host', 'unknown')} — TLS {result.get('tls_version', '?')}",
                        'confidence': 0.9,
                        'payload_text': (
                            f"JA4={ja4} TLS={result.get('tls_version', '?')} "
                            f"ECH={'yes' if result.get('ech_detected') else 'no'} "
                            f"ALPN={result.get('alpn', 'none')}"
                        ),
                        'extra_data': {
                            'host': result.get('host', ''),
                            'port': result.get('port', 443),
                            'ech_detected': result.get('ech_detected', False),
                            'tls_version': result.get('tls_version', ''),
                            'server_ciphers': result.get('server_ciphers', []),
                            'alpn': result.get('alpn', ''),
                            'cert_verified': result.get('cert_verified', False),
                        },
                    })

            return findings

        except Exception:
            logger.debug("JA4CollectorSidecarAdapter.run: fail-soft", exc_info=True)
            return []

    def _extract_domains(self, ctx: SidecarContext) -> list[str]:
        """Extract domain IOC values from findings."""
        domains: list[str] = []
        for finding in ctx.findings:
            ioc_type = getattr(finding, 'ioc_type', '')
            ioc_value = getattr(finding, 'ioc_value', '')

            # Accept domain types
            if ioc_type in ('domain', 'hostname', 'url', 'fqdn'):
                if ioc_value and len(ioc_value) < 253:
                    domains.append(ioc_value)
            # Accept URL-like values ( URLs with :// )
            elif ioc_value and '://' in ioc_value:
                # Extract domain from URL
                try:
                    parsed = urlparse(ioc_value)
                    if parsed.netloc:
                        domains.append(parsed.netloc.split(':')[0].split('@')[-1])
                except Exception:  # noqa: BLE001
                    pass

        return list(dict.fromkeys(domains))  # Dedupe preserve order


@SidecarRegistry.register("whois")
class WhoisSidecarAdapter(BaseSidecarAdapter):
    """
    Historical WHOIS/RDAP Intelligence Sidecar.

    Consolidated async WHOIS/RDAP client providing domain registration
    intelligence with historical data support. Replaces fragmented
    network_reconnaissance.WHOISLookup, rir_correlator._whois_lookup_domain,
    and ipv6_recon WHOIS fallback.

    Features:
      - RDAP (RFC 9224) primary — structured JSON, RIR bootstrap
      - WHOIS port 43 fallback for legacy TLDs
      - ipwhois RDAP fallback (blocking, last resort)
      - Historical WHOIS API opt-in (whoisxmlapi, domainiq, whoisology)
      - Bounded TTL cache (500 entries, 1h)
      - Circuit breakers on all external calls

    Env: HLEDAC_ENABLE_WHOIS=1
    Env (historical): HLEDAC_WHOIS_API + HLEDAC_WHOIS_API_KEY
    RAM: 30MB budget
    Priority: 5 (medium, runs alongside passive DNS and CT lanes)
    """

    sidecar_id: str = "whois"
    lane_id: str = "whois"
    ram_budget_mb: int = 30
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:  # noqa: C901
        """Perform WHOIS lookups for domain findings."""
        if not ctx.findings and not ctx.query:
            return []

        try:
            from hledac.universal.intel.whois_service import (
                WhoisService,
            )
        except Exception:
            logger.debug("WhoisSidecarAdapter: import failed")
            return []

        try:
            # Configure historical API if env vars present
            hist_api = os.environ.get("HLEDAC_WHOIS_API")
            hist_key = os.environ.get("HLEDAC_WHOIS_API_KEY")
            if hist_api and hist_key:
                from hledac.universal.intel.whois_service import (
                    configure_historical_api,
                )
                configure_historical_api(hist_api, hist_key)

            service = WhoisService(
                historical_api=hist_api,
                historical_api_key=hist_key,
            )

            # Extract domain IOCs from findings
            domains = self._extract_domains(ctx)
            if not domains:
                domains = [ctx.query] if ctx.query else []
            domains = domains[:50]  # Cap at MAX_TARGETS

            results = await service.lookup_batch(domains)

            findings = []
            for res in results:
                if not res.registrar and not res.creation_date:
                    continue  # Skip failed lookups

                # Build payload text
                lines = [
                    f"Registrar: {res.registrar or 'N/A'}",
                    f"Created: {res.creation_date or 'N/A'}",
                    f"Expires: {res.expiration_date or 'N/A'}",
                    f"Updated: {res.updated_date or 'N/A'}",
                ]
                if res.name_servers:
                    lines.append(f"Nameservers: {', '.join(res.name_servers[:5])}")
                if res.asn:
                    lines.append(f"ASN: {res.asn} ({res.asn_name or ''})")
                if res.org:
                    lines.append(f"Org: {res.org}")
                if res.dnssec:
                    lines.append("DNSSEC: signed")
                if res.historical:
                    lines.append("(historical record)")

                findings.append({
                    "source_type": "whois",
                    "query": ctx.query,
                    "sprint_id": ctx.sprint_id,
                    "ioc_type": "domain",
                    "ioc_value": res.domain,
                    "confidence": 0.75 if res.source == "rdap" else 0.65,
                    "payload_text": "\n".join(lines),
                    "whois_registrar": res.registrar,
                    "whois_created": res.creation_date,
                    "whois_expires": res.expiration_date,
                    "whois_updated": res.updated_date,
                    "whois_nameservers": res.name_servers,
                    "whois_dnssec": res.dnssec,
                    "whois_asn": res.asn,
                    "whois_org": res.org,
                    "whois_source": res.source,
                    "whois_historical": res.historical,
                })

            return findings[:100]

        except Exception:
            logger.warning("WhoisSidecarAdapter.run: fail-soft", exc_info=True)
            return []

    def _extract_domains(self, ctx: SidecarContext) -> list[str]:
        """Extract domain IOCs from findings."""
        domains: list[str] = []
        for finding in ctx.findings[:50]:
            ioc_value = getattr(finding, "ioc_value", "")
            ioc_type = getattr(finding, "ioc_type", "")
            if ioc_type == "domain" and ioc_value:
                domains.append(ioc_value)
            elif ioc_type == "url":
                # Extract domain from URL — urlparse is module-level import
                try:
                    parsed = urlparse(ioc_value)
                    if parsed.netloc:
                        domain = parsed.netloc.split(":")[0]
                        parts = domain.split(".")
                        if len(parts) >= 2:
                            domains.append(".".join(parts[-2:]))
                except Exception:  # noqa: BLE001
                    pass
        return domains[:50]


@SidecarRegistry.register("threat_intel")
class ThreatIntelSidecarAdapter(BaseSidecarAdapter):
    """
    Threat Intelligence Feed Sidecar — F266-U5.

    Wires up orphaned TI feed functions from ti_feed_adapter.py:
      - fetch_threatfox()    — ThreatFox IOC feed (API, no key)
      - fetch_feodo_c2()     — Feodo Tracker C2 feed (API, no key)
      - fetch_urlhaus()      — URLhaus malware URL feed (RSS already wired,
                                but sidecar adds query-filtered variant)

    These functions were defined but NEVER called from anywhere in the codebase.
    This sidecar activates for threat_intel profile and provides IoCs matching
    the sprint query (ransomware, malware names, C2 IPs, etc.).

    Env: HLEDAC_ENABLE_THREAT_INTEL=1
    RAM: 40MB budget
    Priority: 7 (high — threat intel is primary signal for threat_intel profile)
    """

    sidecar_id: str = "threat_intel"
    lane_id: str = "ti_feeds"
    ram_budget_mb: int = 40
    priority: int = 7

    async def run_async(self, ctx: SidecarContext) -> list[Any]:  # noqa: C901
        """Fetch threat intel IoCs matching the query."""
        if not ctx.query:
            return []

        try:
            from hledac.universal.discovery.ti_feed_adapter import (
                fetch_feodo_c2,
                fetch_threatfox,
                fetch_urlhaus,
            )
        except Exception:
            logger.debug("ThreatIntelSidecarAdapter: import failed")
            return []

        findings: list[Any] = []

        # 1. ThreatFox — most relevant for ransomware/malware named queries
        try:
            threatfox_results = await fetch_threatfox(days=7)
            query_lower = ctx.query.lower()
            for entry in threatfox_results[:100]:
                # entry keys: ioc, ioc_type, malware, threat_type, confidence_level
                malware = entry.get("malware", "") or ""
                ioc = entry.get("ioc", "")
                threat_type = entry.get("threat_type", "") or ""
                confidence = (entry.get("confidence_level", 50) or 50) / 100
                # Filter entries matching the query (malware name, actor, etc.)
                if not query_lower or query_lower in malware.lower() or query_lower in ioc.lower():
                    findings.append({
                        "source_type": "threatfox",
                        "query": ctx.query,
                        "sprint_id": ctx.sprint_id,
                        "ioc_type": entry.get("ioc_type", "unknown"),
                        "ioc_value": ioc,
                        "confidence": confidence,
                        "payload_text": (
                            f"Malware: {malware}\n"
                            f"IOC: {ioc}\n"
                            f"Threat type: {threat_type}\n"
                            f"Confidence: {confidence:.0%}"
                        ),
                        "malware": malware,
                        "threat_type": threat_type,
                    })
        except Exception:
            logger.debug("ThreatIntelSidecarAdapter: ThreatFox fetch failed", exc_info=True)

        # 2. Feodo C2 — botnet C2 IPs
        try:
            feodo_results = await fetch_feodo_c2()
            for entry in feodo_results[:100]:
                # entry keys: ioc (ip_address), ioc_type (ip), port, status
                ip_address = entry.get("ioc", "") or entry.get("ip_address", "")
                if ip_address:
                    findings.append({
                        "source_type": "feodo_tracker",
                        "query": ctx.query,
                        "sprint_id": ctx.sprint_id,
                        "ioc_type": "ip",
                        "ioc_value": ip_address,
                        "confidence": 0.8,
                        "payload_text": (
                            f"Feodo C2: {ip_address}\n"
                            f"Port: {entry.get('port', 'N/A')}\n"
                            f"Status: {entry.get('status', 'active')}"
                        ),
                        "port": entry.get("port"),
                        "status": entry.get("status"),
                    })
        except Exception:
            logger.debug("ThreatIntelSidecarAdapter: Feodo fetch failed", exc_info=True)

        # 3. URLhaus — malware URLs (query-filtered)
        try:
            urlhaus_results = await fetch_urlhaus(max_items=200)
            query_lower = ctx.query.lower()
            for entry in urlhaus_results[:100]:
                # entry keys: ioc (url), threat, url_status
                url = entry.get("ioc", "") or entry.get("url", "")
                if url and query_lower and query_lower in url.lower():
                    findings.append({
                        "source_type": "urlhaus",
                        "query": ctx.query,
                        "sprint_id": ctx.sprint_id,
                        "ioc_type": "url",
                        "ioc_value": url,
                        "confidence": 0.6,
                        "payload_text": (
                            f"URLhaus: {url}\n"
                            f"Threat: {entry.get('threat', 'N/A')}\n"
                            f"Status: {entry.get('url_status', 'N/A')}"
                        ),
                        "threat": entry.get("threat"),
                        "url_status": entry.get("url_status"),
                    })
        except Exception:
            logger.debug("ThreatIntelSidecarAdapter: URLhaus fetch failed", exc_info=True)

        return findings[:200]  # Cap at 200 total


# ── ShadowWalker Sidecar ───────────────────────────────────────────────────────

@SidecarRegistry.register("shadow_walker")
class ShadowWalkerSidecarAdapter(BaseSidecarAdapter):
    """
    ShadowWalker URL path prediction sidecar.

    Uses ShadowWalkerAlgorithm to predict hidden/unlisted URL paths on a target
    domain, based on observed path patterns. One-shot per sprint — no persistent
    state. Results returned as CanonicalFinding with source_type="shadow_walker".

    Env gate: HLEDAC_ENABLE_SHADOW_WALKER=1 (default: 0, dormant)
    RAM budget: ~20MB
    Priority: 4 (runs early in advisory phase)
    """

    sidecar_id: str = "shadow_walker"
    lane_id: str = "shadow_walker"
    ram_budget_mb: int = 20
    priority: int = 4

    def is_available(self) -> bool:
        """Available only when feature flag is enabled."""
        import os
        return os.getenv("HLEDAC_ENABLE_SHADOW_WALKER", "0").lower() in ("1", "true", "yes", "on")

    def _extract_base_url(self, query: str) -> str | None:
        """Extract base URL from query string."""
        match = _URL_RE.search(query)
        if match:
            return match.group(0).rstrip("/")
        return None

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """
        Run ShadowWalker path prediction for the sprint query.

        1. Extract base URL from query
        2. Run ShadowWalkerAlgorithm to predict hidden paths
        3. Convert predictions to findings
        """
        import hashlib
        import time

        from deep_research.path_discovery import ShadowWalkerAlgorithm
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        base_url = self._extract_base_url(ctx.query)
        if not base_url:
            return []

        walker = ShadowWalkerAlgorithm()
        try:
            predictions = walker.predict_next_paths(
                base_url=base_url,
                known_paths=[],
                max_predictions=20,
            )
        except Exception:
            return []

        findings = []
        for path, confidence in predictions:
            try:
                fid = hashlib.sha256(
                    f"shadow:{base_url}:{path}".encode()
                ).hexdigest()[:16]
                finding = CanonicalFinding(
                    finding_id=fid,
                    query=ctx.query,
                    source_type="shadow_walker",
                    confidence=float(confidence),
                    ts=time.time(),
                    provenance=("deep_research", "shadow_walker", base_url),
                    payload_text=path,
                )
                findings.append(finding)
            except Exception:
                continue

        return findings
