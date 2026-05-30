"""
discovery/academic/unpaywall_adapter.py — Unpaywall / OA Button PDF Resolver

Sprint F259: Academic Intelligence Layer — DOI → free PDF resolution.

Features:
- Given a DOI, find the free legal full-text version
- API: https://api.unpaywall.org/v2/{doi}?email=
- "Last mile" resolver — find actual PDF for any paper
- Complementary to OA Button API

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

UNPAYWALL_BASE = "https://api.unpaywall.org/v2"
RATE_LIMIT = 10
REQUEST_TIMEOUT_S = 20.0
MAX_DOI_LOOKUPS = 50


def _get_email() -> str:
    """Get email for polite pool."""
    return os.environ.get("HLEDAC_CONTACT_EMAIL", "research@hledac.ai")


@dataclass
class OAPaper:
    """Open Access paper info from Unpaywall."""
    doi: str
    title: str
    is_oa: bool
    oa_status: str | None  # green, gold, bronze, hybrid, closed
    best_oa_url: str | None  # Best free PDF URL
    best_oa_license: str | None
    journal_name: str | None
    published_date: str | None
    authors: list[str]
    year: int | None
    publisher: str | None
    repository: str | None  # Where the OA version is hosted


@dataclass
class UnpaywallResult(NamedTuple):
    """Result of Unpaywall DOI lookup."""
    paper: OAPaper | None
    error: str | None


# ---------------------------------------------------------------------------
# Unpaywall Adapter
# ---------------------------------------------------------------------------

class UnpaywallAdapter:
    """Unpaywall DOI → free PDF resolver."""

    def __init__(self) -> None:
        self._email = _get_email()
        self._semaphore = asyncio.Semaphore(RATE_LIMIT)
        self._cache: dict[str, tuple[float, OAPaper]] = {}
        self._cache_ttl = 86400.0  # 24 hours (DOIs don't change often)

    async def _fetch(self, doi: str) -> dict | None:
        """Fetch paper data from Unpaywall."""
        async with self._semaphore:
            try:
                from hledac.universal.fetching.public_fetcher import async_fetch_public_text

                import urllib.parse
                encoded_doi = urllib.parse.quote(doi, safe="")
                url = f"{UNPAYWALL_BASE}/{encoded_doi}?email={self._email}"

                result = await async_fetch_public_text(url, timeout_s=REQUEST_TIMEOUT_S, use_stealth=True)
                if not result or not result.content:
                    return None

                return orjson.loads(result.content)

            except Exception as e:
                logger.debug(f"Unpaywall fetch error for {doi}: {e}")
                return None

    async def resolve_doi(self, doi: str) -> UnpaywallResult:
        """
        Resolve a DOI to open access info.

        Args:
            doi: DOI string (e.g., "10.1038/nature12373")

        Returns:
            UnpaywallResult with OAPaper or error
        """
        # Check cache
        if doi in self._cache:
            ts, paper = self._cache[doi]
            if time.time() - ts < self._cache_ttl:
                return UnpaywallResult(paper, None)

        try:
            data = await self._fetch(doi)
            if not data:
                return UnpaywallResult(None, "not_found")

            # Extract best OA location
            best_oa = data.get("best_oa_location", {}) or {}
            if isinstance(best_oa, list):
                best_oa = best_oa[0] if best_oa else {}

            # Authors
            authors = []
            for auth in data.get("z_authors", [])[:10]:
                name = auth.get("given", "") + " " + auth.get("family", "")
                name = name.strip()
                if name:
                    authors.append(name)

            paper = OAPaper(
                doi=doi,
                title=data.get("title", "") or "",
                is_oa=data.get("is_oa", False),
                oa_status=data.get("oa_status"),
                best_oa_url=best_oa.get("url_for_pdf") or best_oa.get("url"),
                best_oa_license=best_oa.get("license"),
                journal_name=data.get("container_title", [""])[0] if data.get("container_title") else None,
                published_date=data.get("published_date"),
                authors=authors,
                year=data.get("year"),
                publisher=data.get("publisher"),
                repository=best_oa.get("repository"),
            )

            # Update cache
            self._cache[doi] = (time.time(), paper)

            return UnpaywallResult(paper, None)

        except Exception as e:
            logger.error(f"Unpaywall resolve error for {doi}: {e}")
            return UnpaywallResult(None, str(e))

    async def resolve_multiple(
        self,
        dois: list[str],
        max_concurrent: int = 5,
    ) -> list[OAPaper]:
        """
        Resolve multiple DOIs in parallel.

        Args:
            dois: List of DOI strings
            max_concurrent: Max concurrent lookups

        Returns:
            List of resolved OAPaper (with None for failures)
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def lookup_one(doi: str) -> OAPaper | None:
            async with semaphore:
                result = await self.resolve_doi(doi)
                return result.paper

        results = await asyncio.gather(
            *[lookup_one(doi) for doi in dois[:MAX_DOI_LOOKUPS]],
            return_exceptions=True,
        )

        return [r if isinstance(r, OAPaper) else None for r in results]

    def to_canonical_findings(
        self,
        papers: list[OAPaper],
        query: str,
    ) -> list[CanonicalFinding]:
        """Convert Unpaywall papers to CanonicalFinding."""
        findings = []
        for paper in papers:
            if not paper.is_oa:
                continue  # Skip closed papers

            import hashlib
            fid = hashlib.sha256(
                f"{query}\x00{paper.doi}\x00unpaywall".encode()
            ).hexdigest()[:16]

            payload_parts = [
                f"title: {paper.title}",
                f"doi: {paper.doi}",
                f"oa_status: {paper.oa_status or 'unknown'}",
                f"free_pdf: {paper.best_oa_url or 'N/A'}",
                f"license: {paper.best_oa_license or 'N/A'}",
                f"journal: {paper.journal_name or 'N/A'}",
                f"year: {paper.year or 'N/A'}",
                f"publisher: {paper.publisher or 'N/A'}",
                f"repository: {paper.repository or 'N/A'}",
            ]

            if paper.authors:
                payload_parts.append(f"authors: {', '.join(paper.authors[:5])}")

            findings.append(CanonicalFinding(
                finding_id=fid,
                query=query,
                source_type="unpaywall",
                confidence=0.9,  # High confidence for free PDFs
                ts=time.time(),
                provenance=("unpaywall", paper.doi, paper.title[:50]),
                payload_text="\n".join(payload_parts),
            ))
        return findings


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

_adapter: UnpaywallAdapter | None = None


async def resolve_doi(doi: str) -> OAPaper | None:
    """Resolve a single DOI to open access info."""
    global _adapter
    if _adapter is None:
        _adapter = UnpaywallAdapter()

    result = await _adapter.resolve_doi(doi)
    return result.paper


async def resolve_multiple_dois(dois: list[str]) -> list[OAPaper]:
    """Resolve multiple DOIs in parallel."""
    global _adapter
    if _adapter is None:
        _adapter = UnpaywallAdapter()

    return await _adapter.resolve_multiple(dois)


async def find_free_pdf(doi: str) -> str | None:
    """
    Get the free PDF URL for a DOI.

    Returns:
        PDF URL or None if not available
    """
    paper = await resolve_doi(doi)
    return paper.best_oa_url if paper else None


__all__ = [
    "UnpaywallAdapter",
    "OAPaper",
    "UnpaywallResult",
    "resolve_doi",
    "resolve_multiple_dois",
    "find_free_pdf",
]