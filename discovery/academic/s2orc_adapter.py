"""
discovery/academic/s2orc_adapter.py — Semantic Scholar S2ORC Full Text Adapter

Sprint F259: Academic Intelligence Layer — Semantic Scholar Academic Graph API.

Features:
- S2AG API: https://api.semanticscholar.org/graph/v1
- TLDR generation via /paper/{id}/tldr endpoint (free, 100rps)
- 2-hop citation graph traversal: seed -> citing -> cited (2 hops)
- Rate limit: 100 req/s unauthenticated -> asyncio.Semaphore(10) safe cap

M1 8GB: async, bounded results, fail-soft.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import NamedTuple

import orjson

from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

S2AG_BASE = "https://api.semanticscholar.org/graph/v1"
S2AG_PAPER_FIELDS = (
    "paperId,title,authors,year,abstract,venue,citationCount,"
    "referenceCount,openAccessPdf,externalIds,influentialCitationCount"
)
S2AG_AUTHOR_FIELDS = "authorId,name,hIndex,paperCount,citationCount"
RATE_LIMIT = 10  # Safe cap for 100rps limit
REQUEST_TIMEOUT_S = 25.0


@dataclass
class S2Paper:
    """Semantic Scholar paper."""
    paper_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    venue: str | None
    citation_count: int
    reference_count: int
    influential_citations: int
    open_access_pdf: str | None
    doi: str | None
    tldr: str | None


@dataclass
class CitationEdge:
    """Citation edge between papers."""
    source_id: str
    target_id: str
    citation_context: str | None


class S2Result(NamedTuple):
    """Result of S2ORC search."""
    papers: list[S2Paper]
    citations: list[CitationEdge]
    error: str | None


# ---------------------------------------------------------------------------
# S2ORC Client
# ---------------------------------------------------------------------------

class S2ORCAdapter:
    """Semantic Scholar S2ORC full text adapter."""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(RATE_LIMIT)
        self._tldr_semaphore = asyncio.Semaphore(5)  # TLDR has separate limit
        self._cache: dict[str, tuple[float, list[S2Paper]]] = {}
        self._cache_ttl = 1800.0  # 30 min

    async def _fetch(
        self,
        endpoint: str,
        params: dict | None = None,
    ) -> dict | None:
        """Fetch from S2AG API with rate limiting."""
        async with self._semaphore:
            try:
                from hledac.universal.fetching.public_fetcher import async_fetch_public_text

                url = f"{S2AG_BASE}{endpoint}"
                if params:
                    import urllib.parse
                    url += "?" + urllib.parse.urlencode(params)

                result = await async_fetch_public_text(url, timeout_s=REQUEST_TIMEOUT_S, use_stealth=True)
                if not result or not result.content:
                    return None

                return orjson.loads(result.content)
            except Exception as e:
                logger.debug(f"S2AG fetch error {endpoint}: {e}")
                return None

    async def search_papers(
        self,
        query: str,
        max_results: int = 20,
    ) -> list[S2Paper]:
        """Search papers by query."""
        try:
            data = await self._fetch(
                "/paper/search",
                params={
                    "query": query,
                    "limit": max_results,
                    "fields": S2AG_PAPER_FIELDS,
                }
            )
            if not data:
                return []

            papers = []
            for item in data.get("data", [])[:max_results]:
                ext_ids = item.get("externalIds", {}) or {}
                pdf = item.get("openAccessPdf", {}) or {}
                papers.append(S2Paper(
                    paper_id=item.get("paperId", ""),
                    title=item.get("title", ""),
                    authors=[a.get("name", "") for a in item.get("authors", [])],
                    year=item.get("year"),
                    abstract=item.get("abstract", "") or "",
                    venue=item.get("venue"),
                    citation_count=item.get("citationCount", 0),
                    reference_count=item.get("referenceCount", 0),
                    influential_citations=item.get("influentialCitationCount", 0),
                    open_access_pdf=pdf.get("url") if isinstance(pdf, dict) else None,
                    doi=ext_ids.get("DOI"),
                    tldr=None,
                ))
            return papers

        except Exception as e:
            logger.error(f"S2ORC search error: {e}")
            return []

    async def get_paper_tldr(self, paper_id: str) -> str | None:
        """Get TLDR (Too Long; Didn't Read) summary for a paper."""
        async with self._tldr_semaphore:
            try:
                from hledac.universal.fetching.public_fetcher import async_fetch_public_text

                url = f"{S2AG_BASE}/paper/{paper_id}/tldr"
                result = await async_fetch_public_text(url, timeout_s=REQUEST_TIMEOUT_S, use_stealth=True)
                if not result or not result.content:
                    return None

                data = orjson.loads(result.content)
                return data.get("text") or data.get("extendedText")

            except Exception as e:
                logger.debug(f"S2ORC TLDR error: {e}")
                return None

    async def get_citations(
        self,
        paper_id: str,
        limit: int = 10,
    ) -> list[S2Paper]:
        """Get papers that cite the given paper (outgoing citations)."""
        data = await self._fetch(
            f"/paper/{paper_id}/citations",
            params={"limit": limit, "fields": S2AG_PAPER_FIELDS}
        )
        if not data:
            return []

        papers = []
        for item in data.get("data", [])[:limit]:
            citing = item.get("citingPaper", {}) or item.get("citedPaper", {})
            if not citing:
                continue
            ext_ids = citing.get("externalIds", {}) or {}
            papers.append(S2Paper(
                paper_id=citing.get("paperId", ""),
                title=citing.get("title", ""),
                authors=[a.get("name", "") for a in citing.get("authors", [])],
                year=citing.get("year"),
                abstract=citing.get("abstract", "") or "",
                venue=citing.get("venue"),
                citation_count=citing.get("citationCount", 0),
                reference_count=citing.get("referenceCount", 0),
                influential_citations=citing.get("influentialCitationCount", 0),
                open_access_pdf=None,
                doi=ext_ids.get("DOI"),
                tldr=None,
            ))
        return papers

    async def traverse_citation_graph(
        self,
        seed_papers: list[S2Paper],
        max_hops: int = 2,
        max_papers: int = 50,
    ) -> tuple[list[S2Paper], list[CitationEdge]]:
        """
        Traverse citation graph 2 hops from seed papers.

        Hop 1: Get citations of seeds (papers that cite seeds)
        Hop 2: Get citations of hop1 papers
        """
        all_papers: list[S2Paper] = []
        all_edges: list[CitationEdge] = []
        visited: set[str] = {p.paper_id for p in seed_papers if p.paper_id}

        # Ensure we have something to start with
        seed_ids = [p.paper_id for p in seed_papers if p.paper_id][:10]
        if not seed_ids:
            return [], []

        # Hop 1
        hop1_papers: list[S2Paper] = []
        for pid in seed_ids:
            if len(all_papers) >= max_papers:
                break
            cited = await self.get_citations(pid, limit=5)
            for p in cited:
                if p.paper_id and p.paper_id not in visited and p.title:
                    visited.add(p.paper_id)
                    hop1_papers.append(p)
                    all_papers.append(p)
                    all_edges.append(CitationEdge(source_id=pid, target_id=p.paper_id, citation_context=None))

        # Hop 2
        if max_hops >= 2:
            for p in hop1_papers[:5]:
                if len(all_papers) >= max_papers:
                    break
                cited = await self.get_citations(p.paper_id, limit=3)
                for p2 in cited:
                    if p2.paper_id and p2.paper_id not in visited and p2.title:
                        visited.add(p2.paper_id)
                        all_papers.append(p2)
                        all_edges.append(CitationEdge(source_id=p.paper_id, target_id=p2.paper_id, citation_context=None))

        return all_papers[:max_papers], all_edges

    async def enrich_with_tldr(
        self,
        papers: list[S2Paper],
        max_enrich: int = 10,
    ) -> list[S2Paper]:
        """Add TLDR summaries to papers (parallel, limited)."""
        tasks = []
        for p in papers[:max_enrich]:
            if not p.tldr:
                tasks.append(self._enrich_one(p))
            else:
                tasks.append(asyncio.sleep(0, p))  # Already has TLDR

        results = await asyncio.gather(*tasks, return_exceptions=True)
        enriched = []
        for r in results:
            if isinstance(r, S2Paper):
                enriched.append(r)
            else:
                # Fallback: add original paper without TLDR
                if papers:
                    papers[0].tldr = None
                    enriched.append(papers[0])

        # Fill rest without TLDR
        for p in papers[len(enriched):]:
            p.tldr = None
            enriched.append(p)

        return enriched[:len(papers)]

    async def _enrich_one(self, paper: S2Paper) -> S2Paper:
        """Enrich one paper with TLDR."""
        tldr = await self.get_paper_tldr(paper.paper_id)
        paper.tldr = tldr
        return paper

    def to_canonical_findings(
        self,
        papers: list[S2Paper],
        query: str,
    ) -> list[CanonicalFinding]:
        """Convert S2ORC papers to CanonicalFinding."""
        findings = []
        for paper in papers:
            import hashlib
            fid = hashlib.sha256(
                f"{query}\x00{paper.paper_id}\x00s2orc".encode()
            ).hexdigest()[:16]

            payload = "\n".join([
                f"title: {paper.title}",
                f"authors: {', '.join(paper.authors[:5])}{'...' if len(paper.authors) > 5 else ''}",
                f"year: {paper.year or 'N/A'}",
                f"venue: {paper.venue or 'N/A'}",
                f"citations: {paper.citation_count}",
                f"influential: {paper.influential_citations}",
                f"doi: {paper.doi or 'N/A'}",
                f"pdf: {paper.open_access_pdf or 'N/A'}",
                f"tldr: {paper.tldr or 'N/A'}",
                f"abstract: {paper.abstract[:800] if paper.abstract else 'N/A'}",
            ])

            findings.append(CanonicalFinding(
                finding_id=fid,
                query=query,
                source_type="s2orc",
                confidence=0.85,
                ts=time.time(),
                provenance=("semantic_scholar", paper.paper_id, paper.title[:50]),
                payload_text=payload,
            ))
        return findings


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

_adapter: S2ORCAdapter | None = None


async def search_s2orc(
    query: str,
    max_results: int = 20,
    include_citations: bool = True,
) -> list[CanonicalFinding]:
    """
    Search S2ORC and optionally traverse citation graph.

    Args:
        query: Search query
        max_results: Max papers to return
        include_citations: If True, do 2-hop citation traversal

    Returns:
        CanonicalFinding list
    """
    global _adapter
    if _adapter is None:
        _adapter = S2ORCAdapter()

    # Initial search
    papers = await _adapter.search_papers(query, max_results=max_results)

    if include_citations and papers:
        # Traverse citation graph
        cited, _ = await _adapter.traverse_citation_graph(papers, max_hops=2, max_papers=30)
        papers.extend(cited)

    # Enrich with TLDR
    papers = await _adapter.enrich_with_tldr(papers, max_enrich=min(10, len(papers)))

    return _adapter.to_canonical_findings(papers, query)


__all__ = ["S2ORCAdapter", "S2Paper", "S2Result", "CitationEdge", "search_s2orc"]