"""
discovery/academic/openalex_adapter.py — OpenAlex API Adapter

Sprint F259: Academic Intelligence Layer — OpenAlex scholarly graph.

Features:
- Concept-based search: topic -> related works by concept hierarchy
- Institution network: author/institution -> collaboration network
- filter=concepts.id: for sub-field specific searches
- Polite pool: mailto= param for 10 req/s instead of 5

M1 8GB: async, bounded, fail-soft.
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

OPENALEX_BASE = "https://api.openalex.org"
RATE_LIMIT = 10  # Polite pool: 10 req/s with mailto
REQUEST_TIMEOUT_S = 25.0
MAX_RESULTS = 20

# Known concept IDs for common fields (subset)
# https://api.openalex.org/concepts
FIELD_CONCEPTS = {
    "cs": "C164176025",  # Computer Science
    "ai": "C39432361",   # Artificial Intelligence
    "ml": "C185592260",  # Machine Learning
    "security": "C162324750",  # Computer Security
    "crypto": "C2777199784",   # Cryptography
}


@dataclass
class OpenAlexWork:
    """OpenAlex academic work."""
    id: str
    title: str
    authors: list[str]
    year: int | None
    doi: str | None
    concepts: list[str]
    citation_count: int
    cited_by_count: int
    open_access: bool
    related_works: list[str]
    abstract: str
    publication_date: str | None


@dataclass
class OpenAlexInstitution:
    """OpenAlex institution."""
    id: str
    display_name: str
    country_code: str | None
    type: str | None
    homepage: str | None
    works_count: int


@dataclass
class OpenAlexAuthor:
    """OpenAlex author."""
    id: str
    display_name: str
    orcid: str | None
    institutions: list[str]
    works_count: int


@dataclass
class InstitutionNetwork:
    """Collaboration network for an institution."""
    institution: OpenAlexInstitution
    collaborators: list[tuple[OpenAlexInstitution, int]]  # (institution, shared_works)
    top_works: list[OpenAlexWork]


class OpenAlexResult(NamedTuple):
    """Result of OpenAlex search."""
    works: list[OpenAlexWork]
    concepts: list[str]
    error: str | None


# ---------------------------------------------------------------------------
# OpenAlex Adapter
# ---------------------------------------------------------------------------

class OpenAlexAdapter:
    """OpenAlex API adapter."""

    def __init__(self) -> None:
        self._semaphore = asyncio.Semaphore(RATE_LIMIT)
        self._cache: dict[str, tuple[float, list[OpenAlexWork]]] = {}
        self._cache_ttl = 1800.0  # 30 min

    def _mailto_param(self) -> str:
        """Get mailto param for polite pool."""
        import os
        return os.environ.get("HLEDAC_CONTACT_EMAIL", "research@hledac.ai")

    async def _fetch(self, endpoint: str) -> dict | None:
        """Fetch from OpenAlex API."""
        async with self._semaphore:
            try:
                from hledac.universal.fetching.public_fetcher import async_fetch_public_text

                url = f"{OPENALEX_BASE}{endpoint}"
                if "?" in url:
                    url += f"&mailto={self._mailto_param()}"
                else:
                    url += f"?mailto={self._mailto_param()}"

                result = await async_fetch_public_text(url, timeout_s=REQUEST_TIMEOUT_S, use_stealth=True)
                if not result or not result.content:
                    return None

                return orjson.loads(result.content)
            except Exception as e:
                logger.debug(f"OpenAlex fetch error: {e}")
                return None

    async def search_by_query(
        self,
        query: str,
        max_results: int = MAX_RESULTS,
        concepts_filter: list[str] | None = None,
    ) -> OpenAlexResult:
        """
        Search works by query, optionally filtered by concepts.

        Args:
            query: Search query
            max_results: Max results
            concepts_filter: List of concept IDs to filter by

        Returns:
            OpenAlexResult with works
        """
        try:
            params = [
                f"search={query.replace(' ', '+')}",
                f"per_page={max_results}",
            ]
            if concepts_filter:
                for cid in concepts_filter:
                    params.append(f"filter=concepts.id:{cid}")

            endpoint = "/works?" + "&".join(params)
            data = await self._fetch(endpoint)
            if not data:
                return OpenAlexResult([], [], "no data")

            works = []
            concepts = set()
            for item in data.get("results", [])[:max_results]:
                # Extract concepts
                item_concepts = [c.get("display_name", "") for c in item.get("concepts", [])]
                concepts.update(item_concepts)

                # Authors
                authors = []
                for auth in item.get("authorships", [])[:10]:
                    name = auth.get("author", {}).get("display_name", "")
                    if name:
                        authors.append(name)

                works.append(OpenAlexWork(
                    id=item.get("id", "").replace("https://openalex.org/", ""),
                    title=item.get("title", "") or "",
                    authors=authors,
                    year=item.get("publication_year"),
                    doi=item.get("doi"),
                    concepts=item_concepts[:5],
                    citation_count=item.get("citation_count", 0),
                    cited_by_count=item.get("cited_by_count", 0),
                    open_access=item.get("open_access", {}).get("is_oa", False) if isinstance(item.get("open_access"), dict) else False,
                    related_works=[w.replace("https://openalex.org/", "") for w in item.get("related_works", [])],
                    abstract="",  # OpenAlex doesn't provide abstracts by default
                    publication_date=item.get("publication_date"),
                ))

            return OpenAlexResult(works, list(concepts), None)

        except Exception as e:
            logger.error(f"OpenAlex search error: {e}")
            return OpenAlexResult([], [], str(e))

    async def search_by_concepts(
        self,
        concept_ids: list[str],
        max_results: int = MAX_RESULTS,
    ) -> OpenAlexResult:
        """Search works by concept IDs (sub-field specific)."""
        try:
            concept_filter = ",".join(concept_ids)
            params = f"filter=concepts.id:{concept_filter}&per_page={max_results}"
            endpoint = f"/works?{params}"
            data = await self._fetch(endpoint)
            if not data:
                return OpenAlexResult([], [], "no data")

            works = []
            for item in data.get("results", [])[:max_results]:
                authors = []
                for auth in item.get("authorships", [])[:10]:
                    name = auth.get("author", {}).get("display_name", "")
                    if name:
                        authors.append(name)

                works.append(OpenAlexWork(
                    id=item.get("id", "").replace("https://openalex.org/", ""),
                    title=item.get("title", "") or "",
                    authors=authors,
                    year=item.get("publication_year"),
                    doi=item.get("doi"),
                    concepts=[c.get("display_name", "") for c in item.get("concepts", [])[:5]],
                    citation_count=item.get("citation_count", 0),
                    cited_by_count=item.get("cited_by_count", 0),
                    open_access=item.get("open_access", {}).get("is_oa", False) if isinstance(item.get("open_access"), dict) else False,
                    related_works=[],
                    abstract="",
                    publication_date=item.get("publication_date"),
                ))

            return OpenAlexResult(works, concept_ids, None)

        except Exception as e:
            logger.error(f"OpenAlex concept search error: {e}")
            return OpenAlexResult([], [], str(e))

    async def get_institution_network(
        self,
        institution_name: str | None = None,
        institution_id: str | None = None,
        max_works: int = 30,
    ) -> InstitutionNetwork | None:
        """
        Get collaboration network for an institution.

        Args:
            institution_name: Institution name to search for
            institution_id: Direct OpenAlex institution ID

        Returns:
            InstitutionNetwork or None
        """
        try:
            # Find or use institution
            if institution_id:
                inst_id = institution_id.replace("https://openalex.org/", "")
            else:
                # Search for institution
                inst_data = await self._fetch(f"/institutions?search={institution_name}")
                if not inst_data or not inst_data.get("results"):
                    return None
                inst = inst_data["results"][0]
                inst_id = inst["id"].replace("https://openalex.org/", "")

            # Get institution details
            inst_data = await self._fetch(f"/institutions/{inst_id}")
            if not inst_data:
                return None

            institution = OpenAlexInstitution(
                id=inst_id,
                display_name=inst_data.get("display_name", ""),
                country_code=inst_data.get("country_code"),
                type=inst_data.get("type"),
                homepage=inst_data.get("homepage"),
                works_count=inst_data.get("works_count", 0),
            )

            # Get top works from institution
            works_data = await self._fetch(
                f"/works?filter=institutions.id:{inst_id}&sort=citation_count:desc&per_page={max_works}"
            )
            top_works = []
            if works_data:
                for item in works_data.get("results", [])[:10]:
                    authors = []
                    for auth in item.get("authorships", [])[:5]:
                        name = auth.get("author", {}).get("display_name", "")
                        if name:
                            authors.append(name)

                    top_works.append(OpenAlexWork(
                        id=item.get("id", "").replace("https://openalex.org/", ""),
                        title=item.get("title", "") or "",
                        authors=authors,
                        year=item.get("publication_year"),
                        doi=item.get("doi"),
                        concepts=[c.get("display_name", "") for c in item.get("concepts", [])[:3]],
                        citation_count=item.get("citation_count", 0),
                        cited_by_count=item.get("cited_by_count", 0),
                        open_access=False,
                        related_works=[],
                        abstract="",
                        publication_date=item.get("publication_date"),
                    ))

            # Get collaborators (institutions that co-author with this one)
            collaborators: list[tuple[OpenAlexInstitution, int]] = []
            collab_data = await self._fetch(
                f"/institutions/{inst_id}/co-institutions?per_page=10"
            )
            if collab_data:
                for item in collab_data.get("results", [])[:10]:
                    collaborators.append((
                        OpenAlexInstitution(
                            id=item.get("id", "").replace("https://openalex.org/", ""),
                            display_name=item.get("display_name", ""),
                            country_code=item.get("country_code"),
                            type=item.get("type"),
                            homepage=item.get("homepage"),
                            works_count=item.get("works_count", 0),
                        ),
                        item.get("works_count", 0),
                    ))

            return InstitutionNetwork(
                institution=institution,
                collaborators=collaborators,
                top_works=top_works,
            )

        except Exception as e:
            logger.error(f"OpenAlex institution network error: {e}")
            return None

    def to_canonical_findings(
        self,
        works: list[OpenAlexWork],
        query: str,
    ) -> list[CanonicalFinding]:
        """Convert OpenAlex works to CanonicalFinding."""
        findings = []
        for work in works:
            import hashlib
            fid = hashlib.sha256(
                f"{query}\x00{work.id}\x00openalex".encode()
            ).hexdigest()[:16]

            payload = "\n".join([
                f"title: {work.title}",
                f"authors: {', '.join(work.authors[:5])}{'...' if len(work.authors) > 5 else ''}",
                f"year: {work.year or 'N/A'}",
                f"doi: {work.doi or 'N/A'}",
                f"concepts: {', '.join(work.concepts[:5])}",
                f"citations: {work.cited_by_count}",
                f"open_access: {'yes' if work.open_access else 'no'}",
                f"publication_date: {work.publication_date or 'N/A'}",
            ])

            findings.append(CanonicalFinding(
                finding_id=fid,
                query=query,
                source_type="openalex",
                confidence=0.8,
                ts=time.time(),
                provenance=("openalex", work.id, work.title[:50]),
                payload_text=payload,
            ))
        return findings


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

_adapter: OpenAlexAdapter | None = None


async def search_openalex(
    query: str,
    max_results: int = MAX_RESULTS,
    concept_ids: list[str] | None = None,
) -> list[CanonicalFinding]:
    """Search OpenAlex and return CanonicalFinding list."""
    global _adapter
    if _adapter is None:
        _adapter = OpenAlexAdapter()

    if concept_ids:
        result = await _adapter.search_by_concepts(concept_ids, max_results)
    else:
        result = await _adapter.search_by_query(query, max_results)

    if result.error:
        logger.warning(f"OpenAlex search failed: {result.error}")
        return []

    return _adapter.to_canonical_findings(result.works, query)


async def get_institution_network(
    institution_name: str,
) -> InstitutionNetwork | None:
    """Get institution collaboration network."""
    global _adapter
    if _adapter is None:
        _adapter = OpenAlexAdapter()

    return await _adapter.get_institution_network(institution_name=institution_name)


__all__ = [
    "OpenAlexAdapter",
    "OpenAlexWork",
    "OpenAlexInstitution",
    "OpenAlexAuthor",
    "OpenAlexResult",
    "InstitutionNetwork",
    "search_openalex",
    "get_institution_network",
    "FIELD_CONCEPTS",
]