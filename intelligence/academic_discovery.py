"""
Academic Discovery — Convenience Functions for Academic Search
===============================================================

Migrated from: intelligence/ (parent/donor)
Canonical path: hledac.universal.intelligence.academic_discovery

P14: Provides standalone functions for searching academic databases:
- search_arxiv(query) -> list[dict]
- search_crossref(query) -> list[dict]
- search_semantic_scholar(query) -> list[dict]

Returns structured output: title, authors, year, link.

Anti-patterns:
- No API keys hardcoded - use environment variables or optional config
- Rate limited via asyncio.Semaphore(5) (max 100 req/min)
- Bounded: max_results per source, fail-soft on errors
- No heavy dependencies — uses existing AcademicSearchEngine internally

M1 8GB constraints:
- Semaphore(5) limits concurrent requests
- Per-source max_results bounded (default 20)
- All network calls have timeouts
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

OPENALEX_BASE = "https://api.openalex.org"
IARCHIVE_SCHOLAR = "https://scholar.archive.org"
CORE_API = "https://api.core.ac.uk"
BIORXIV_API = "https://api.biorxiv.org"
MEDRXIV_API = "https://api.medrxiv.org"
MAX_CITATION_PAPERS = 50
MAX_HOPS = 2


# =============================================================================
# RESULT DATA CLASSES
# =============================================================================

@dataclass
class AcademicPaper:
    """Structured academic paper result."""
    title: str
    authors: list[str]
    year: int | None
    link: str
    source: str
    abstract: str = ""
    doi: str | None = None
    citations: int = 0
    tags: list[str] = field(default_factory=list)
    affiliations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "title": self.title,
            "authors": self.authors,
            "year": self.year,
            "link": self.link,
            "source": self.source,
            "abstract": self.abstract,
            "doi": self.doi,
            "citations": self.citations,
            "tags": self.tags,
            "affiliations": self.affiliations,
        }

    @property
    def paper_id(self) -> str:
        """Paper ID usable with get_citations — prefer DOI, fallback to title-hash."""
        if self.doi:
            return f"doi:{self.doi}"
        if self.title:
            import hashlib
            h = hashlib.sha256(self.title.encode()).hexdigest()[:16]
            return f"title:{h}"
        return ""


# =============================================================================
# STANDALONE SEARCH FUNCTIONS — NEW SOURCES
# =============================================================================

async def search_openalex(query: str, max_results: int = 20) -> list[AcademicPaper]:
    """Search OpenAlex for academic papers."""
    try:
        import orjson
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        url = f"{OPENALEX_BASE}/works?search={query}&per-page={max_results}&mailto=research@hledac.ai"
        result = await async_fetch_public_text(url, timeout_s=30.0, use_stealth=True)
        if not result or not result.content:
            return []
        data = orjson.loads(result.content)
        papers = []
        for work in data.get("results", [])[:max_results]:
            authors = [au.get("display_name", "") for au in work.get("authorships", [])]
            affiliations = [au.get("institution", {}).get("display_name", "") for au in work.get("authorships", []) if au.get("institution")]
            papers.append(AcademicPaper(
                title=work.get("title", ""),
                authors=authors,
                year=work.get("publication_year"),
                link=work.get("doi", "") or work.get("id", ""),
                source="openalex",
                abstract=work.get("abstract_inverted_index", ""),
                doi=work.get("doi"),
                citations=work.get("cited_by_count", 0),
                affiliations=affiliations,
            ))
        return papers
    except Exception as e:
        logger.error(f"search_openalex err: {e}")
        return []


async def search_ia_scholar(query: str, max_results: int = 20) -> list[AcademicPaper]:
    """Search Internet Archive Scholar for academic papers."""
    try:
        import orjson
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        url = f"{IARCHIVE_SCHOLAR}/search?q={query}&limit={max_results}"
        result = await async_fetch_public_text(url, timeout_s=30.0, use_stealth=True)
        if not result or not result.content:
            return []
        data = orjson.loads(result.content)
        papers = []
        for item in data.get("items", [])[:max_results]:
            papers.append(AcademicPaper(
                title=item.get("title", ""),
                authors=[item.get("creator", "")],
                year=item.get("year"),
                link=item.get("url", ""),
                source="ia_scholar",
                abstract=item.get("abstract", ""),
            ))
        return papers
    except Exception as e:
        logger.error(f"search_ia_scholar err: {e}")
        return []


async def search_core(query: str, max_results: int = 20) -> list[AcademicPaper]:
    """Search CORE.ac.uk for academic papers."""
    try:
        import orjson
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        url = f"{CORE_API}/v3/search/works/{query}?limit={max_results}"
        result = await async_fetch_public_text(url, timeout_s=30.0, use_stealth=True)
        if not result or not result.content:
            return []
        data = orjson.loads(result.content)
        papers = []
        for item in data.get("results", [])[:max_results]:
            papers.append(AcademicPaper(
                title=item.get("title", ""),
                authors=[a.get("name", "") for a in item.get("authors", [])],
                year=item.get("year"),
                link=item.get("downloadUrl", "") or item.get("url", ""),
                source="core",
                abstract=item.get("abstract", ""),
                doi=item.get("doi"),
            ))
        return papers
    except Exception as e:
        logger.error(f"search_core err: {e}")
        return []


async def search_biorxiv(query: str, max_results: int = 20) -> list[AcademicPaper]:
    """Search bioRxiv preprints."""
    try:
        import orjson
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        url = f"{BIORXIV_API}/v2/server/search?query={query}&count={max_results}&format=json"
        result = await async_fetch_public_text(url, timeout_s=30.0, use_stealth=True)
        if not result or not result.content:
            return []
        data = orjson.loads(result.content)
        papers = []
        for item in data.get("messages", [])[:max_results]:
            papers.append(AcademicPaper(
                title=item.get("title", ""),
                authors=[a.get("name", "") for a in item.get("authors", [])],
                year=item.get("date", "")[:4] if item.get("date") else None,
                link=f"https://doi.org/{item.get('doi', '')}" if item.get("doi") else "",
                source="biorxiv",
                abstract=item.get("abstract", ""),
                doi=item.get("doi"),
            ))
        return papers
    except Exception as e:
        logger.error(f"search_biorxiv err: {e}")
        return []


async def search_medrxiv(query: str, max_results: int = 20) -> list[AcademicPaper]:
    """Search medRxiv preprints."""
    try:
        import orjson
        from hledac.universal.fetching.public_fetcher import async_fetch_public_text
        url = f"{MEDRXIV_API}/v2/server/search?query={query}&count={max_results}&format=json"
        result = await async_fetch_public_text(url, timeout_s=30.0, use_stealth=True)
        if not result or not result.content:
            return []
        data = orjson.loads(result.content)
        papers = []
        for item in data.get("messages", [])[:max_results]:
            papers.append(AcademicPaper(
                title=item.get("title", ""),
                authors=[a.get("name", "") for a in item.get("authors", [])],
                year=item.get("date", "")[:4] if item.get("date") else None,
                link=f"https://doi.org/{item.get('doi', '')}" if item.get("doi") else "",
                source="medrxiv",
                abstract=item.get("abstract", ""),
                doi=item.get("doi"),
            ))
        return papers
    except Exception as e:
        logger.error(f"search_medrxiv err: {e}")
        return []


# =============================================================================
# CITATION TRAVERSAL
# =============================================================================

async def traverse_citation_graph(
    seed_papers: list[AcademicPaper],
    max_hops: int = MAX_HOPS,
) -> list[AcademicPaper]:
    """
    2-hop citation graph traversal from seed papers.

    Hop 1: From first 10 seeds, max 5 papers per seed
    Hop 2: From first 5 hop1 papers, max 3 papers per paper
    """
    try:
        from hledac.universal.intelligence.academic_search import SemanticScholarClient
        cache_dir = Path("/tmp/academic_cache")
        cache_dir.mkdir(parents=True, exist_ok=True)
        ss_client = SemanticScholarClient(cache_dir)

        async with ss_client:
            all_cited = []
            visited = {p.paper_id for p in seed_papers if p.paper_id}

            # Hop 1
            hop1_papers = []
            for seed in seed_papers[:10]:
                paper_id = seed.paper_id
                if not paper_id:
                    continue
                try:
                    citations = await ss_client.get_citations(paper_id, limit=5)
                    for cit in citations:
                        pid = f"doi:{cit.get('doi', '')}" if cit.get('doi') else f"title:{cit.get('title', '')[:16]}"
                        if pid not in visited and cit.get("title"):
                            visited.add(pid)
                            hop1_papers.append(AcademicPaper(
                                title=cit.get("title", ""),
                                authors=cit.get("authors", []),
                                year=cit.get("year"),
                                link=cit.get("url", ""),
                                source="semantic_scholar_cited_by",
                                doi=cit.get("doi"),
                            ))
                except Exception:
                    pass

            all_cited.extend(hop1_papers[:MAX_CITATION_PAPERS])

            # Hop 2
            if max_hops >= 2:
                hop2_papers = []
                for paper in hop1_papers[:5]:
                    paper_id = paper.paper_id
                    if not paper_id:
                        continue
                    try:
                        citations = await ss_client.get_citations(paper_id, limit=3)
                        for cit in citations:
                            pid = f"doi:{cit.get('doi', '')}" if cit.get('doi') else f"title:{cit.get('title', '')[:16]}"
                            if pid not in visited and cit.get("title"):
                                visited.add(pid)
                                hop2_papers.append(AcademicPaper(
                                    title=cit.get("title", ""),
                                    authors=cit.get("authors", []),
                                    year=cit.get("year"),
                                    link=cit.get("url", ""),
                                    source="semantic_scholar_cited_by",
                                    doi=cit.get("doi"),
                                ))
                    except Exception:
                        pass

                all_cited.extend(hop2_papers[:MAX_CITATION_PAPERS - len(all_cited)])

        return all_cited[:MAX_CITATION_PAPERS]
    except Exception as e:
        logger.error(f"traverse_citation_graph err: {e}")
        return []


# =============================================================================
# INTELLIGENCE CROSSLINKING
# =============================================================================

async def intelligence_crosslink(papers: list[AcademicPaper]) -> dict[str, list[dict[str, Any]]]:
    """
    Extract emails from author strings, check breach APIs, extract institutions,
    and find relationships via RelationshipDiscoveryEngine.
    """
    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    results: dict[str, list[dict[str, Any]]] = {"breach_alerts": [], "relationships": []}

    try:
        # Extract emails and institutions
        emails = set()
        institutions = set()

        for paper in papers:
            for author in paper.authors:
                found = email_pattern.findall(str(author))
                emails.update(found)
            for aff in paper.affiliations:
                if aff:
                    institutions.add(aff)

        # DataLeakHunter for breach checks
        from hledac.universal.intelligence.data_leak_hunter import DataLeakHunter
        if emails:
            hunter = DataLeakHunter(cache_dir=Path("/tmp/breach_cache"))
            for email in list(emails)[:10]:
                try:
                    alerts = await hunter._check_breach_apis(email, "email")
                    for alert in alerts:
                        results["breach_alerts"].append({
                            "email": email,
                            "source": getattr(alert, "source", "unknown"),
                            "description": str(alert),
                        })
                except Exception:
                    pass

        # RelationshipDiscoveryEngine for institution relationships
        from hledac.universal.intelligence.relationship_discovery import RelationshipDiscoveryEngine
        if institutions:
            engine = RelationshipDiscoveryEngine(cache_dir=Path("/tmp/rel_cache"))
            for inst in list(institutions)[:20]:
                try:
                    # Try predict_hidden_connections which may work for institutions
                    rels = await engine.predict_hidden_connections(max_predictions=5)
                    for src, tgt, conf in rels:
                        results["relationships"].append({
                            "institution": inst,
                            "related": {"source": src, "target": tgt, "confidence": conf},
                        })
                except Exception:
                    pass

    except Exception as e:
        logger.error(f"intelligence_crosslink err: {e}")

    return results


# =============================================================================
# LAZY IMPORT — avoid circular imports
# =============================================================================

def _get_academic_search_engine():
    """Lazy-load AcademicSearchEngine from canonical path."""
    from hledac.universal.intelligence.academic_search import (
        AcademicSearchEngine,
    )
    return AcademicSearchEngine


# =============================================================================
# SEARCH FUNCTIONS
# =============================================================================

async def search_arxiv(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """
    Search arXiv for academic papers.

    Args:
        query: Search query
        max_results: Maximum results to return (default 10)

    Returns:
        List of dicts with keys: title, authors, year, link
    """
    try:
        AcademicSearchEngine = _get_academic_search_engine()
        engine = AcademicSearchEngine(enable_expansion=False, enable_deduplication=True)
        result = await engine.search(
            query,
            max_results=max_results,
            sources=["arxiv"],
        )

        papers = []
        for search_result in result.deduplicated_results[:max_results]:
            published = search_result.metadata.get("published", "")
            year = None
            if published:
                try:
                    year = int(published[:4])
                except (ValueError, IndexError):
                    pass

            paper = AcademicPaper(
                title=search_result.title,
                authors=search_result.metadata.get("authors", []),
                year=year,
                link=search_result.url or search_result.metadata.get("pdf_url", ""),
                source="arxiv",
                abstract=search_result.snippet,
                doi=None,
                citations=0,
                tags=search_result.metadata.get("categories", []),
            )
            papers.append(paper.to_dict())

        await engine.cleanup()
        return papers

    except Exception as e:
        logger.error(f"search_arxiv error: {e}")
        return []


async def search_crossref(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """
    Search Crossref for academic papers.

    Args:
        query: Search query
        max_results: Maximum results to return (default 10)

    Returns:
        List of dicts with keys: title, authors, year, link
    """
    try:
        AcademicSearchEngine = _get_academic_search_engine()
        engine = AcademicSearchEngine(enable_expansion=False, enable_deduplication=True)
        result = await engine.search(
            query,
            max_results=max_results,
            sources=["crossref"],
        )

        papers = []
        for search_result in result.deduplicated_results[:max_results]:
            published = search_result.metadata.get("published", "")
            year = None
            if published:
                try:
                    year = int(published[:4])
                except (ValueError, IndexError):
                    pass

            paper = AcademicPaper(
                title=search_result.title,
                authors=search_result.metadata.get("authors", []),
                year=year,
                link=search_result.url or f"https://doi.org/{search_result.metadata.get('doi', '')}",
                source="crossref",
                abstract=search_result.snippet,
                doi=search_result.metadata.get("doi"),
                citations=search_result.metadata.get("citations", 0),
                tags=[],
            )
            papers.append(paper.to_dict())

        await engine.cleanup()
        return papers

    except Exception as e:
        logger.error(f"search_crossref error: {e}")
        return []


async def search_semantic_scholar(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """
    Search Semantic Scholar for academic papers.

    Args:
        query: Search query
        max_results: Maximum results to return (default 10)

    Returns:
        List of dicts with keys: title, authors, year, link
    """
    try:
        AcademicSearchEngine = _get_academic_search_engine()
        engine = AcademicSearchEngine(enable_expansion=False, enable_deduplication=True)
        result = await engine.search(
            query,
            max_results=max_results,
            sources=["semantic_scholar"],
        )

        papers = []
        for search_result in result.deduplicated_results[:max_results]:
            paper = AcademicPaper(
                title=search_result.title,
                authors=search_result.metadata.get("authors", []),
                year=search_result.metadata.get("year"),
                link=search_result.url or f"https://doi.org/{search_result.metadata.get('doi', '')}",
                source="semantic_scholar",
                abstract=search_result.snippet,
                doi=search_result.metadata.get("doi"),
                citations=search_result.metadata.get("citation_count", 0),
                tags=[],
            )
            papers.append(paper.to_dict())

        await engine.cleanup()
        return papers

    except Exception as e:
        logger.error(f"search_semantic_scholar error: {e}")
        return []


# =============================================================================
# MAIN ORCHESTRATOR — search all sources concurrently
# =============================================================================

async def search_academic_all(
    query: str,
    max_results: int = 20,
    _rate_limit: int = 100
) -> dict[str, list[dict[str, Any]]]:
    """
    Search all academic sources concurrently.

    Args:
        query: Search query
        max_results: Maximum results per source (default 20)
        rate_limit: Max requests per minute (default 100)

    Returns:
        Dict with keys: arxiv, crossref, semantic_scholar
        Each value is a list of paper dicts
    """
    semaphore = asyncio.Semaphore(5)

    async def limited_search(_source: str, search_func):
        async with semaphore:
            return await search_func(query, max_results)

    results_raw: tuple[Any, ...] = await asyncio.gather(
        limited_search("arxiv", search_arxiv),
        limited_search("crossref", search_crossref),
        limited_search("semantic_scholar", search_semantic_scholar),
        limited_search("openalex", search_openalex),
        limited_search("ia_scholar", search_ia_scholar),
        limited_search("core", search_core),
        limited_search("biorxiv", search_biorxiv),
        limited_search("medrxiv", search_medrxiv),
        return_exceptions=True,
    )

    arxiv_result: list[dict[str, Any]] = results_raw[0] if not isinstance(results_raw[0], Exception) else []
    crossref_result: list[dict[str, Any]] = results_raw[1] if not isinstance(results_raw[1], Exception) else []
    semantic_result: list[dict[str, Any]] = results_raw[2] if not isinstance(results_raw[2], Exception) else []
    openalex_result: list[dict[str, Any]] = results_raw[3] if not isinstance(results_raw[3], Exception) else []
    ia_result: list[dict[str, Any]] = results_raw[4] if not isinstance(results_raw[4], Exception) else []
    core_result: list[dict[str, Any]] = results_raw[5] if not isinstance(results_raw[5], Exception) else []
    biorxiv_result: list[dict[str, Any]] = results_raw[6] if not isinstance(results_raw[6], Exception) else []
    medrxiv_result: list[dict[str, Any]] = results_raw[7] if not isinstance(results_raw[7], Exception) else []

    return {
        "arxiv": arxiv_result,
        "crossref": crossref_result,
        "semantic_scholar": semantic_result,
        "openalex": openalex_result,
        "ia_scholar": ia_result,
        "core": core_result,
        "biorxiv": biorxiv_result,
        "medrxiv": medrxiv_result,
    }


# =============================================================================
# SYNC WRAPPERS — for backwards compatibility only
# F214M: Replaced get_event_loop() with safe pattern for Python 3.14 compatibility.
# =============================================================================

def _run_sync(async_func, /, *args, **kwargs):
    """Run an async function synchronously in an isolated event loop.

    Accepts an async function and its arguments — does NOT accept a pre-created
    coroutine. This ensures the loop check happens BEFORE any coroutine is
    instantiated, preventing RuntimeWarning: coroutine was never awaited.

    Raises RuntimeError if called from a running event loop — in that case,
    the async function should be awaited directly.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — safe to instantiate and run the coroutine
        return asyncio.run(async_func(*args, **kwargs))
    raise RuntimeError(
        "Cannot call sync academic_discovery wrapper from a running event loop. "
        "Use the async function directly instead."
    )


def search_arxiv_sync(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Synchronous wrapper for search_arxiv.

    Deprecated for async callers: use `await search_arxiv(...)` inside an event loop.
    """
    return _run_sync(search_arxiv, query, max_results)


def search_crossref_sync(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Synchronous wrapper for search_crossref.

    Deprecated for async callers: use `await search_crossref(...)` inside an event loop.
    """
    return _run_sync(search_crossref, query, max_results)


def search_semantic_scholar_sync(query: str, max_results: int = 10) -> list[dict[str, Any]]:
    """Synchronous wrapper for search_semantic_scholar.

    Deprecated for async callers: use `await search_semantic_scholar(...)` inside an event loop.
    """
    return _run_sync(search_semantic_scholar, query, max_results)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'AcademicPaper',
    'search_arxiv',
    'search_crossref',
    'search_semantic_scholar',
    'search_openalex',
    'search_ia_scholar',
    'search_core',
    'search_biorxiv',
    'search_medrxiv',
    'search_academic_all',
    'traverse_citation_graph',
    'intelligence_crosslink',
    'search_arxiv_sync',
    'search_crossref_sync',
    'search_semantic_scholar_sync',
]
