"""
discovery/academic/core_adapter.py — CORE.ac.uk Full Text Search Adapter

Sprint F259: Academic Intelligence Layer — CORE.ac.uk open access aggregator.

Features:
- CORE aggregates 200M+ open access papers from 10,000+ repositories
- API: https://api.core.ac.uk/v3/
- Requires free API key (add to .env.example)
- Full text search over ACTUAL PAPER CONTENT (not just abstracts)
- highlight=True returns passages with context

M1 8GB: async, bounded, fail-soft.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import NamedTuple

import orjson

from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CORE_API_BASE = "https://api.core.ac.uk/v3"
RATE_LIMIT = 5  # 10 req/min on free tier
REQUEST_TIMEOUT_S = 30.0
MAX_RESULTS = 20


def _get_api_key() -> str | None:
    """Get CORE API key from environment."""
    return os.environ.get("CORE_API_KEY") or os.environ.get("HLEDAC_CORE_API_KEY")


@dataclass
class COREWork:
    """CORE.ac.uk academic work."""
    id: int
    title: str
    authors: list[str]
    year: int | None
    abstract: str | None
    doi: str | None
    fulltext: str | None
    highlight: str | None  # Matched passages with context
    download_url: str | None
    repositories: list[str]
    topics: list[str]
    oai_ids: list[str]


@dataclass
class COREPageResult:
    """A page of text with highlight markers."""
    text: str
    score: float
    source: str


class COREResult(NamedTuple):
    """Result of CORE search."""
    works: list[COREWork]
    page_results: list[COREPageResult]
    total_hits: int
    error: str | None


# ---------------------------------------------------------------------------
# CORE Adapter
# ---------------------------------------------------------------------------

class COREAdapter:
    """CORE.ac.uk API adapter with full-text search."""

    def __init__(self) -> None:
        self._api_key = _get_api_key()
        self._semaphore = asyncio.Semaphore(RATE_LIMIT)
        self._cache: dict[str, tuple[float, list[COREWork]]] = {}
        self._cache_ttl = 1800.0  # 30 min

    @property
    def has_api_key(self) -> bool:
        """Check if API key is configured."""
        return bool(self._api_key)

    def _auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        if self._api_key:
            return {"Authorization": f"ApiKey {self._api_key}"}
        return {}

    async def _fetch(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | None = None,
    ) -> dict | None:
        """Fetch from CORE API."""
        async with self._semaphore:
            try:
                from hledac.universal.fetching.public_fetcher import async_fetch_public_text

                url = f"{CORE_API_BASE}{endpoint}"
                headers = self._auth_headers()
                headers["Content-Type"] = "application/json"

                import aiohttp
                async with aiohttp.ClientSession() as session:
                    if method == "POST" and data:
                        async with asyncio.timeout(REQUEST_TIMEOUT_S):
                            async with session.post(url, json=data, headers=headers) as resp:
                                if resp.status != 200:
                                    text = await resp.text()
                                    logger.warning(f"CORE API error {resp.status}: {text[:200]}")
                                    return None
                                return await resp.json()
                    else:
                        async with asyncio.timeout(REQUEST_TIMEOUT_S):
                            async with session.get(url, headers=headers) as resp:
                                if resp.status != 200:
                                    text = await resp.text()
                                    logger.warning(f"CORE API error {resp.status}: {text[:200]}")
                                    return None
                                return await resp.json()

            except asyncio.TimeoutError:
                logger.warning("CORE API timeout")
                return None
            except Exception as e:
                logger.debug(f"CORE fetch error: {e}")
                return None

    async def fulltext_search(
        self,
        query: str,
        max_results: int = MAX_RESULTS,
        highlight: bool = True,
    ) -> COREResult:
        """
        Full-text search over paper content (CORE's unique feature).

        Args:
            query: Search query
            max_results: Max results
            highlight: Include highlighted passages with context

        Returns:
            COREResult with works and page highlights
        """
        if not self._api_key:
            logger.warning("CORE API key not configured. Set CORE_API_KEY in .env")
            return COREResult([], [], 0, "no_api_key")

        try:
            # POST search endpoint for full-text search
            payload = {
                "query": query,
                "page": 1,
                "pageSize": max_results,
                "minFullTextRelevance": 0.1,  # Minimum relevance for full-text matches
            }
            if highlight:
                payload["includeHighlightTags"] = True

            data = await self._fetch("/search/works", method="POST", data=payload)
            if not data:
                return COREResult([], [], 0, "no_data")

            works = []
            page_results = []
            total_hits = data.get("totalHits", 0)

            for item in data.get("results", [])[:max_results]:
                # Extract authors
                authors = []
                for auth in item.get("authors", [])[:10]:
                    if isinstance(auth, dict):
                        name = auth.get("name", "")
                    else:
                        name = str(auth)
                    if name:
                        authors.append(name)

                # Full text snippet
                fulltext = None
                highlight_text = None

                if highlight:
                    # Get highlighted passages
                    for field in ["fullText", "abstract"]:
                        ht = item.get(field, {}).get("text", "")
                        if ht:
                            highlight_text = ht[:500]
                            break

                    # Full text from separate endpoint if available
                    ft = item.get("fullText", {})
                    if isinstance(ft, dict):
                        fulltext = ft.get("text")

                # Download URL
                download_url = item.get("downloadUrl") or item.get("doiUrl")

                # Repositories
                repos = []
                for repo in item.get("repositories", [])[:5]:
                    if isinstance(repo, dict):
                        repos.append(repo.get("name", ""))
                    else:
                        repos.append(str(repo))

                works.append(COREWork(
                    id=item.get("id", 0),
                    title=item.get("title", ""),
                    authors=authors,
                    year=item.get("year"),
                    abstract=item.get("abstract"),
                    doi=item.get("doi"),
                    fulltext=fulltext,
                    highlight=highlight_text,
                    download_url=download_url,
                    repositories=repos,
                    topics=item.get("topics", []),
                    oai_ids=item.get("oaiIds", []),
                ))

                # Page-level results for full-text search
                if highlight_text and highlight_text != item.get("abstract"):
                    page_results.append(COREPageResult(
                        text=highlight_text[:300],
                        score=item.get("fullText", {}).get("score", 0.0) or item.get("score", 0.0),
                        source=f"CORE:{item.get('id', 0)}",
                    ))

            return COREResult(works, page_results, total_hits, None)

        except Exception as e:
            logger.error(f"CORE fulltext search error: {e}")
            return COREResult([], [], 0, str(e))

    async def search_by_doi(self, doi: str) -> COREWork | None:
        """Search for a specific work by DOI."""
        try:
            # URL encode DOI
            import urllib.parse
            encoded_doi = urllib.parse.quote(doi, safe="")

            data = await self._fetch(f"/works/doi:{encoded_doi}")
            if not data:
                return None

            authors = []
            for auth in data.get("authors", [])[:10]:
                if isinstance(auth, dict):
                    name = auth.get("name", "")
                else:
                    name = str(auth)
                if name:
                    authors.append(name)

            return COREWork(
                id=data.get("id", 0),
                title=data.get("title", ""),
                authors=authors,
                year=data.get("year"),
                abstract=data.get("abstract"),
                doi=data.get("doi"),
                fulltext=data.get("fullText", {}).get("text") if isinstance(data.get("fullText"), dict) else None,
                highlight=None,
                download_url=data.get("downloadUrl"),
                repositories=[r.get("name", "") for r in data.get("repositories", [])[:5]],
                topics=data.get("topics", []),
                oai_ids=data.get("oaiIds", []),
            )

        except Exception as e:
            logger.debug(f"CORE DOI lookup error: {e}")
            return None

    def to_canonical_findings(
        self,
        works: list[COREWork],
        query: str,
    ) -> list[CanonicalFinding]:
        """Convert CORE works to CanonicalFinding."""
        findings = []
        for work in works:
            import hashlib
            fid = hashlib.sha256(
                f"{query}\x00{work.id}\x00core".encode()
            ).hexdigest()[:16]

            payload_parts = [
                f"title: {work.title}",
                f"authors: {', '.join(work.authors[:5])}{'...' if len(work.authors) > 5 else ''}",
                f"year: {work.year or 'N/A'}",
                f"doi: {work.doi or 'N/A'}",
                f"download: {work.download_url or 'N/A'}",
                f"repositories: {', '.join(work.repositories[:3])}",
                f"topics: {', '.join(work.topics[:5])}",
            ]

            # Add highlight or abstract
            if work.highlight:
                payload_parts.append(f"fulltext_match: {work.highlight[:400]}")
            elif work.abstract:
                payload_parts.append(f"abstract: {work.abstract[:400]}")

            findings.append(CanonicalFinding(
                finding_id=fid,
                query=query,
                source_type="core_fulltext",
                confidence=0.85,
                ts=time.time(),
                provenance=("core", str(work.id), work.title[:50]),
                payload_text="\n".join(payload_parts),
            ))
        return findings


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

_adapter: COREAdapter | None = None


async def search_core_fulltext(
    query: str,
    max_results: int = MAX_RESULTS,
    highlight: bool = True,
) -> list[CanonicalFinding]:
    """
    Full-text search via CORE and return CanonicalFinding list.

    Requires CORE_API_KEY in environment.
    """
    global _adapter
    if _adapter is None:
        _adapter = COREAdapter()

    if not _adapter.has_api_key:
        logger.warning("CORE API key not configured. Skipping CORE search.")
        logger.info("Get free API key at: https://core.ac.uk/join/developer")
        return []

    result = await _adapter.fulltext_search(query, max_results, highlight)
    if result.error:
        logger.warning(f"CORE search failed: {result.error}")
        return []

    return _adapter.to_canonical_findings(result.works, query)


async def lookup_core_doi(doi: str) -> COREWork | None:
    """Lookup a specific work by DOI."""
    global _adapter
    if _adapter is None:
        _adapter = COREAdapter()

    return await _adapter.search_by_doi(doi)


__all__ = [
    "COREAdapter",
    "COREWork",
    "COREPageResult",
    "COREResult",
    "search_core_fulltext",
    "lookup_core_doi",
]