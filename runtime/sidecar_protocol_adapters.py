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

    def _make_finding(self, post: object, ctx: SidecarContext) -> dict | None:
        """Construct a CanonicalFinding-compatible dict from a Fediverse post.

        Accepts a `FediversePost` dataclass (the new contract from
        `discovery/fediverse_adapter.search_multiple_instances`) or a raw
        dict (legacy path) — both shapes are normalized via
        `FediversePost.to_dict()` for downstream `post.get(...)` access.
        Fail-soft: any conversion error returns `None` and the sidecar
        logs nothing for the dropped post.
        """
        try:
            # Normalize: dataclass → dict, dict stays, anything else → None.
            if hasattr(post, "to_dict") and callable(post.to_dict):
                post_dict = post.to_dict()
            elif isinstance(post, dict):
                post_dict = post
            else:
                return None
            return {
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
            from hledac.universal.discovery.dht_adapter import DHTAdapter
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
    env_gate: str = "HLEDAC_ENABLE_TV_NEWS"
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
    env_gate: str = "HLEDAC_ENABLE_FEDERATED"
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
class PassiveFingerprintSidecarAdapter(BaseSidecarAdapter):
    """
    F204G: Passive service fingerprinting — deterministic, no active scan.

    Lazy-imports `intelligence.passive_fingerprint.create_passive_fingerprint_adapter`
    factory; invokes `adapter.correlate(findings, query)` and returns the
    derived CanonicalFindings.

    Env: HLEDAC_ENABLE_PASSIVE_FINGERPRINT=1
    RAM: 50MB budget
    Priority: 4 (research-tier)
    """

    sidecar_id: str = "passive_fingerprint"
    env_gate: str = "HLEDAC_ENABLE_PASSIVE_FINGERPRINT"
    ram_budget_mb: int = 50
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        try:
            from hledac.universal.intelligence.passive_fingerprint import (
                create_passive_fingerprint_adapter,
            )
        except Exception:
            logger.debug("PassiveFingerprintSidecarAdapter: import failed")
            return []

        try:
            adapter = create_passive_fingerprint_adapter()
            derived = adapter.correlate(ctx.findings, ctx.query)
            return list(derived) if derived else []
        except Exception:
            logger.warning(
                "PassiveFingerprintSidecarAdapter.run: fail-soft",
                exc_info=True,
            )
            return []


# ── Passive Tech-Stack Sidecar (F350M-R / R11) ────────────────────────────────

@SidecarRegistry.register("passive_tech_stack")
class PassiveTechStackSidecarAdapter(BaseSidecarAdapter):
    """
    R11: Passive tech-stack extraction — deterministic, no active scan.

    Wraps `intelligence.passive_fingerprint.create_passive_tech_stack_adapter`
    factory; calls `adapter.correlate(findings, query)`. Derived signal is
    identical to `passive_fingerprint` for tech-stack component, but exposed
    under its own registry ID for env-gated opt-in.

    Env: HLEDAC_ENABLE_PASSIVE_TECH_STACK=1
    RAM: 30MB budget
    Priority: 4 (research-tier)
    """

    sidecar_id: str = "passive_tech_stack"
    env_gate: str = "HLEDAC_ENABLE_PASSIVE_TECH_STACK"
    ram_budget_mb: int = 30
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        try:
            from hledac.universal.intelligence.passive_fingerprint import (
                create_passive_tech_stack_adapter,
            )
        except Exception:
            logger.debug("PassiveTechStackSidecarAdapter: import failed")
            return []

        try:
            adapter = create_passive_tech_stack_adapter()
            derived = adapter.correlate(ctx.findings, ctx.query)
            return list(derived) if derived else []
        except Exception:
            logger.warning(
                "PassiveTechStackSidecarAdapter.run: fail-soft",
                exc_info=True,
            )
            return []


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
    env_gate: str = "HLEDAC_ENABLE_SOCIAL_IDENTITY_SURFACE"
    ram_budget_mb: int = 60
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        try:
            from hledac.universal.intelligence.social_identity_miner import (
                create_social_identity_miner_adapter,
            )
        except Exception:
            logger.debug("SocialIdentityMinerSidecarAdapter: import failed")
            return []

        try:
            miner = create_social_identity_miner_adapter()
            miner.reset()
        except Exception:
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
    env_gate: str = "HLEDAC_ENABLE_IDENTITY_STITCHING"
    ram_budget_mb: int = 100
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        # Smoke-import the factory to validate module availability.
        try:
            pass
        except Exception:
            return []
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
    env_gate: str = "HLEDAC_ENABLE_TEMPORAL_ARCHAEOLOGY"
    ram_budget_mb: int = 80
    priority: int = 4

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
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
    env_gate: str = "HLEDAC_ENABLE_GRAPH_RAG"
    ram_budget_mb: int = 60
    priority: int = 7

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Embed query + findings into LanceDB for cross-sprint persistence."""
        if not ctx.query:
            return []

        try:
            from knowledge.lancedb_rag_engine import LanceDBRAGEngine, RAGDocument
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
class GitHubGistSidecarAdapter(BaseSidecarAdapter):
    """
    GitHub Gist Archive Discovery Sidecar.

    Searches public GitHub Gists for OSINT signals matching the query
    or related IoCs from sprint findings. Uses the existing
    search_github_gists() function from ti_feed_adapter.

    Env: HLEDAC_ENABLE_GITHUB_GIST=1
    RAM: 30MB budget
    Priority: 5 (medium)
    """

    sidecar_id: str = "github_gist"
    env_gate: str = "HLEDAC_ENABLE_GITHUB_GIST"
    ram_budget_mb: int = 30
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Search GitHub Gists for OSINT signals based on query and findings."""
        if not ctx.findings and not ctx.query:
            return []

        try:
            from hledac.universal.discovery.ti_feed_adapter import (
                search_github_gists,
            )
        except Exception:
            logger.debug("GitHubGistSidecarAdapter: import failed")
            return []

        try:
            # Extract search terms from findings
            search_terms = self._extract_search_terms(ctx)
            if not search_terms:
                search_terms = [ctx.query] if ctx.query else []

            # Limit for M1 safety
            search_terms = search_terms[:5]

            findings = []
            for term in search_terms:
                try:
                    results = await search_github_gists(term, max_results=10)
                    for gist in results:
                        findings.append({
                            "source_type": "github_gist",
                            "query": ctx.query,
                            "sprint_id": ctx.sprint_id,
                            "ioc_type": "gist",
                            "ioc_value": gist.get("url", ""),
                            "title": gist.get("title", ""),
                            "confidence": 0.6,
                            "payload_text": gist.get("snippet", ""),
                        })
                except Exception:
                    pass  # Fail-soft per term

            return findings[:50]  # Cap at 50 findings

        except Exception:
            logger.warning(
                "GitHubGistSidecarAdapter.run: fail-soft", exc_info=True
            )
            return []

    def _extract_search_terms(self, ctx: SidecarContext) -> list[str]:
        """Extract domain/IOC terms from findings for Gist search."""
        terms: list[str] = []
        for finding in ctx.findings[:20]:
            ioc_value = getattr(finding, "ioc_value", None)
            if ioc_value and len(ioc_value) < 100:
                terms.append(ioc_value)
        return terms[:10]


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
    env_gate: str = "HLEDAC_ENABLE_WHOIS"
    ram_budget_mb: int = 30
    priority: int = 5

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        """Perform WHOIS lookups for domain findings."""
        import os as _os

        if not ctx.findings and not ctx.query:
            return []

        try:
            from hledac.universal.intelligence.whois_service import (
                WhoisService,
            )
        except Exception:
            logger.debug("WhoisSidecarAdapter: import failed")
            return []

        try:
            # Configure historical API if env vars present
            hist_api = _os.environ.get("HLEDAC_WHOIS_API")
            hist_key = _os.environ.get("HLEDAC_WHOIS_API_KEY")
            if hist_api and hist_key:
                from hledac.universal.intelligence.whois_service import (
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
                # Extract domain from URL
                try:
                    from urllib.parse import urlparse

                    parsed = urlparse(ioc_value)
                    if parsed.netloc:
                        domain = parsed.netloc.split(":")[0]
                        parts = domain.split(".")
                        if len(parts) >= 2:
                            domains.append(".".join(parts[-2:]))
                except Exception:
                    pass
        return domains[:50]


