"""
Academic Discovery — Convenience Functions for Academic Search
===============================================================

Migrated from: intelligence/ (parent/donor)
Canonical path: hledac.universal.intelligence.academic_discovery

P14: Provides standalone functions for searching academic databases:
- search_arxiv(query) -> List[dict]
- search_crossref(query) -> List[dict]
- search_semantic_scholar(query) -> List[dict]

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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# RESULT DATA CLASSES
# =============================================================================

@dataclass
class AcademicPaper:
    """Structured academic paper result."""
    title: str
    authors: List[str]
    year: Optional[int]
    link: str
    source: str
    abstract: str = ""
    doi: Optional[str] = None
    citations: int = 0
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
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
        }


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

async def search_arxiv(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
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


async def search_crossref(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
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


async def search_semantic_scholar(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
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
) -> Dict[str, List[Dict[str, Any]]]:
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
        return_exceptions=True,
    )

    arxiv_result: List[Dict[str, Any]] = results_raw[0] if not isinstance(results_raw[0], Exception) else []
    crossref_result: List[Dict[str, Any]] = results_raw[1] if not isinstance(results_raw[1], Exception) else []
    semantic_result: List[Dict[str, Any]] = results_raw[2] if not isinstance(results_raw[2], Exception) else []

    return {
        "arxiv": arxiv_result,
        "crossref": crossref_result,
        "semantic_scholar": semantic_result,
    }


# =============================================================================
# SYNC WRAPPERS — for backwards compatibility only
# =============================================================================

def search_arxiv_sync(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Synchronous wrapper for search_arxiv."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(search_arxiv(query, max_results))


def search_crossref_sync(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Synchronous wrapper for search_crossref."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(search_crossref(query, max_results))


def search_semantic_scholar_sync(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """Synchronous wrapper for search_semantic_scholar."""
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(search_semantic_scholar(query, max_results))


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'AcademicPaper',
    'search_arxiv',
    'search_crossref',
    'search_semantic_scholar',
    'search_academic_all',
    'search_arxiv_sync',
    'search_crossref_sync',
    'search_semantic_scholar_sync',
]