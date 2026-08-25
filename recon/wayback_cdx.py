"""
wayback_cdx — Sprint F234: CDX deep search extension
=====================================================

Extends the existing Wayback lane with CDX fulltext discovery.
Finds archived URLs that no longer exist on live web (deleted content,
old endpoints, historical paths).

CDX API endpoint:
    https://web.archive.org/cdx/search/cdx

Key capabilities:
    matchType=domain    — all subdomains + paths (*.example.com)
    filter=!statuscode:404 — only live/archived responses
    fl=timestamp,original,mimetype,length — metadata mining
    collapse=urlkey     — deduplicate identical content
    from=YYYYMMDD       — date range filter

Bounds:
    MAX_CDX_RESULTS = 500    — max rows returned
    RATE_LIMIT_S = 2.0       — 2s between requests
    TIMEOUT_PER_REQUEST = 60.0 — 60s for large CDX responses

No API key required — purely public Wayback Machine data.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import field
from typing import Any

import httpx

from compat.msgspec_gc_compat import Struct
from hledac.universal.transport.session_pool import session_pool
from hledac.universal.utils.asyncx import parallel_ok
from hledac.universal.utils.bloom_filter import RotatingBloomFilter  # M-2026-FIX: bounded URL dedup
from hledac.universal.utils.optional_imports import lazy_import

CanonicalFinding = lazy_import("hledac.universal.knowledge.duckdb_store:CanonicalFinding", default=None)
logger = logging.getLogger(__name__)
MAX_CDX_RESULTS: int = 500
MAX_CDX_RESULTS_FULL: int = 5000  # P8-003: paginated deep search cap
RATE_LIMIT_S: float = 2.0
TIMEOUT_PER_REQUEST: float = 60.0
CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL = "https://web.archive.org"


class CDXSearchResult(Struct):
    """
    Single row from CDX deep search.

    Fields:
        original:    Original URL that was archived
        timestamp:   CDX timestamp (YYYYMMDDHHMMSS)
        mimetype:    Content-Type of the snapshot
        status_code: HTTP status code
        length:      Content length in bytes
        digest:      Content digest (Memento)
        replay_url:  Full Wayback Machine replay URL
    """

    original: str
    timestamp: str
    mimetype: str
    status_code: str
    length: str
    digest: str
    replay_url: str = ""

    def __post_init__(self) -> None:
        if self.replay_url and (not self.timestamp):
            self.replay_url = ""
        elif self.timestamp and self.original:
            safe_url = self.original[:500]
            self.replay_url = f"{WAYBACK_BASE_URL}/web/{self.timestamp}/{safe_url}"

    def to_finding_dict(self) -> dict:
        return {
            "source": "wayback_cdx",
            "url": self.original,
            "timestamp": self.timestamp,
            "mimetype": self.mimetype,
            "status_code": self.status_code,
            "length": self.length,
            "replay_url": self.replay_url,
        }

    def to_canonical_finding(self, query: str, _sprint_id: str = "") -> CanonicalFinding | None:
        if CanonicalFinding is None:
            return None
        try:
            payload = self._build_payload()
            return CanonicalFinding(
                finding_id=f"cdx-{(self.digest[:16] if self.digest else self.timestamp[:12])}",
                source_type="wayback_cdx",
                confidence=0.8,
                query=query[:128],
                ts=self._parse_timestamp(),
                payload_text=payload,
                provenance=(
                    f"url:{self.original}",
                    f"ts:{self.timestamp}",
                    f"mimetype:{self.mimetype}",
                    f"status:{self.status_code}",
                ),
            )
        except Exception:
            return None

    def _parse_timestamp(self) -> float:
        from datetime import datetime

        try:
            return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S").timestamp()
        except Exception:
            return 0.0

    def _build_payload(self) -> str:
        parts = [
            f"[CDX Deep Search] {self.original}",
            f"Archived: {self.timestamp}",
            f"Type: {self.mimetype}",
            f"Status: {self.status_code}",
            f"Size: {self.length} bytes",
            f"Replay: {self.replay_url}",
        ]
        return "\n".join(parts)


class CDXDeepSearchResult(Struct, frozen=True):
    """Result of a cdx_deep_search() call."""

    query: str
    match_type: str
    total_rows: int = 0
    results: list[CDXSearchResult] = field(default_factory=list)
    error: str | None = None
    timeout: bool = False
    duration_s: float = 0.0
    rate_limited: bool = False

    def to_findings(self, query: str, sprint_id: str) -> list:
        if self.error:
            return []
        return [
            r.to_canonical_finding(query, sprint_id) for r in self.results if r.to_canonical_finding(query, sprint_id)
        ]


async def cdx_deep_search(
    domain: str,
    session: httpx.AsyncClient,
    *,
    match_type: str = "domain",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = MAX_CDX_RESULTS,
) -> list[CDXSearchResult]:
    """
    CDX fulltext discovery — finds archived URLs for a domain.

    Unlike simple snapshot lookups, this discovers:
      - Subdomains (*.example.com)
      - Historical paths no longer on live web
      - Content deleted from live site
      - Old endpoints and API routes

    Args:
        domain:      Domain to search (e.g. "example.com")
        session:     httpx.AsyncClient
        match_type:  CDX match type:
                       "exact"   = exact URL match
                       "prefix"  = URL prefix match
                       "host"    = exact host match
                       "domain"  = domain + all subdomains (default)
        from_date:   Start date YYYYMMDD (optional)
        to_date:     End date YYYYMMDD (optional)
        limit:       Max rows to return (default 500)

    Returns:
        List of CDXSearchResult with original URL, timestamp, mimetype, etc.
    """
    if match_type == "domain":
        url_param = f"*.{domain}"
    elif match_type == "host":
        url_param = domain
    elif match_type == "prefix":
        url_param = f"http://{domain}/*"
    else:
        url_param = domain
    params: dict[str, Any] = {
        "url": url_param,
        "matchType": match_type,
        "output": "json",
        "fl": "timestamp,original,mimetype,statuscode,length,digest",
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "limit": str(limit),
    }
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    try:
        resp = await session.get(CDX_API, params=params, timeout=httpx.Timeout(TIMEOUT_PER_REQUEST))
        if resp.status_code == 429:
            logger.warning(f"CDX rate limited for {domain}")
            return []
        if resp.status_code != 200:
            logger.debug(f"CDX {domain} → HTTP {resp.status_code}")
            return []
        raw: list[list[str]] = resp.json()
        return _parse_cdx_response(raw)
    except TimeoutError:
        logger.debug(f"CDX deep search timeout for {domain}")
        return []
    except Exception as e:
        logger.debug(f"CDX deep search error for {domain}: {e}")
        return []


async def cdx_deep_search_full(
    domain: str,
    session: httpx.AsyncClient,
    *,
    match_type: str = "domain",
    from_date: str | None = None,
    to_date: str | None = None,
    max_total: int = MAX_CDX_RESULTS_FULL,
) -> list[CDXSearchResult]:
    """
    P8-003: CDX deep search with full pagination.

    Fetches ALL archived URLs for a domain by paginating through CDX API
    results using the `resumeKey` cursor. Unlike cdx_deep_search() which
    returns at most 500 results, this function continues until all pages
    are exhausted or max_total is reached.

    CDX API pagination:
        - Each request returns up to 500 results (CDX API max limit)
        - When more results exist, response includes `cdx-next-resume-key` header
        - Pass this value as `resumeKey` param to get the next page
        - Continue until header is absent (no more pages)

    Args:
        domain:      Domain to search (e.g. "example.com")
        session:     httpx.AsyncClient
        match_type:  CDX match type (default: "domain")
        from_date:   Start date YYYYMMDD (optional)
        to_date:     End date YYYYMMDD (optional)
        max_total:   Maximum total results to return (default: 5000)

    Returns:
        List of CDXSearchResult with all paginated results.
    """
    if match_type == "domain":
        url_param = f"*.{domain}"
    elif match_type == "host":
        url_param = domain
    elif match_type == "prefix":
        url_param = f"http://{domain}/*"
    else:
        url_param = domain

    all_results: list[CDXSearchResult] = []
    resume_key: str | None = None

    while len(all_results) < max_total:
        params: dict[str, Any] = {
            "url": url_param,
            "matchType": match_type,
            "output": "json",
            "fl": "timestamp,original,mimetype,statuscode,length,digest",
            "filter": "statuscode:200",
            "collapse": "urlkey",
            "limit": "500",  # CDX API max per page
        }
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        if resume_key:
            params["resumeKey"] = resume_key

        try:
            resp = await session.get(
                CDX_API,
                params=params,
                timeout=httpx.Timeout(TIMEOUT_PER_REQUEST),
            )
            if resp.status_code == 429:
                logger.warning(f"CDX rate limited for {domain}, stopping pagination")
                break
            if resp.status_code != 200:
                logger.debug(f"CDX {domain} → HTTP {resp.status_code}, stopping pagination")
                break

            raw: list[list[str]] = resp.json()
            page_results = _parse_cdx_response(raw)

            if not page_results:
                break  # No more results

            all_results.extend(page_results)

            next_resume_key = resp.headers.get("cdx-next-resume-key")
            if not next_resume_key:
                break  # Last page reached

            # Guard against infinite pagination (stuck resumeKey)
            if next_resume_key == resume_key:
                logger.warning(f"CDX pagination stuck for {domain} (same resumeKey), breaking")
                break
            resume_key = next_resume_key

            # Rate limit between pages
            # Note: caller (_fetch_one) already enforces RATE_LIMIT_S, so this
            # is additional spacing within the pagination of a single domain
            await asyncio.sleep(RATE_LIMIT_S)

        except httpx.PoolTimeout:
            logger.debug(f"CDX connection pool exhausted for {domain}")
            break
        except TimeoutError:
            logger.debug(f"CDX deep search full timeout for {domain}")
            break
        except Exception as e:
            logger.debug(f"CDX deep search full error for {domain}: {e}")
            break

    return all_results[:max_total]


def _parse_cdx_response(raw: list[list[str]]) -> list[CDXSearchResult]:
    """Parse CDX JSON response into CDXSearchResult list."""
    if not raw or len(raw) < 2:
        return []
    raw[0]
    results: list[CDXSearchResult] = []
    for row in raw[1:]:
        if len(row) < 6:
            continue
        result = CDXSearchResult(
            timestamp=row[0],
            original=row[1],
            mimetype=row[2],
            status_code=row[3],
            length=row[4],
            digest=row[5] if len(row) > 5 else "",
        )
        results.append(result)
    return results


async def cdx_deep_search_batch(
    domains: list[str],
    session: httpx.AsyncClient,
    *,
    match_type: str = "domain",
    concurrency: int = 3,
    rate_limit_s: float = RATE_LIMIT_S,
) -> list[CDXSearchResult]:
    """
    Batch CDX deep search across multiple domains with rate limiting.

    Args:
        domains:      List of domain strings
        session:      httpx.AsyncClient
        match_type:   CDX match type (passed to each domain query)
        concurrency:  Max concurrent CDX requests (Semaphore)
        rate_limit_s: Minimum seconds between requests

    Returns:
        All CDXSearchResult across all domains (deduplicated by original URL).
    """
    if not domains:
        return []
    semaphore = asyncio.Semaphore(concurrency)
    last_request = 0.0
    all_results: list[CDXSearchResult] = []
    # M-2026-FIX: RotatingBloomFilter replaces unbounded set[str] URL dedup.
    seen_urls: RotatingBloomFilter = RotatingBloomFilter(max_elements=100_000, error_rate=0.005)

    async def _fetch_one(domain: str) -> list[CDXSearchResult]:
        nonlocal last_request
        async with semaphore:
            elapsed = time.monotonic() - last_request
            if elapsed < rate_limit_s:
                await asyncio.sleep(rate_limit_s - elapsed)
            last_request = time.monotonic()
            results = await cdx_deep_search(domain, session, match_type=match_type)
            unique = [r for r in results if r.original not in seen_urls]
            for r in unique:
                seen_urls.add(r.original)
            return unique

    gathered = await parallel_ok(*[_fetch_one(d) for d in domains], label="wayback_cdx:315")
    for res in gathered:
        if isinstance(res, list):
            all_results.extend(res)
    return all_results


class WaybackCDXDeepSearch:
    """
    High-level CDX deep search with session management,
    rate limiting, and CanonicalFinding output.

    Integrates with existing WaybackCDX in archive_discovery.py
    as an extension layer — adds domain/subdomain discovery
    that WaybackCDX.get_snapshots() doesn't cover.
    """

    __slots__ = ("_session", "_session_provider", "_stats")

    def __init__(self, session_provider: Callable[[], Awaitable[httpx.AsyncClient]] | None = None) -> None:
        self._session_provider = session_provider
        self._session: httpx.AsyncClient | None = None
        self._stats: dict[str, int] = {"domains_searched": 0, "total_results": 0, "errors": 0}

    async def _ensure_session(self) -> httpx.AsyncClient:
        if self._session_provider is not None:
            return await self._session_provider()
        if self._session is None or self._session.is_closed:
            self._session = await session_pool.httpx()
        return self._session

    async def close(self) -> None:
        if self._session is not None and (not self._session.is_closed):
            await self._session.aclose()
            self._session = None

    async def search(
        self,
        domains_or_urls: list[str],
        *,
        match_type: str = "domain",
        from_date: str | None = None,
        to_date: str | None = None,
        limit_per_domain: int = 200,
        concurrency: int = 3,
    ) -> CDXDeepSearchResult:
        """
        Search multiple domains/URLs via CDX deep search.

        Args:
            domains_or_urls: List of domains or full URLs
            match_type:      CDX match type (default: domain)
            from_date:       Optional start date YYYYMMDD
            to_date:         Optional end date YYYYMMDD
            limit_per_domain: Max results per domain
            concurrency:     Max concurrent CDX requests (Semaphore)

        Returns:
            CDXDeepSearchResult with all findings + telemetry.
        """
        start = time.monotonic()
        session = await self._ensure_session()
        semaphore = asyncio.Semaphore(concurrency)
        last_request = 0.0

        async def _fetch_one(domain: str) -> list[CDXSearchResult]:
            nonlocal last_request
            async with semaphore:
                elapsed = time.monotonic() - last_request
                if elapsed < RATE_LIMIT_S:
                    await asyncio.sleep(RATE_LIMIT_S - elapsed)
                last_request = time.monotonic()
                return await cdx_deep_search(
                    domain, session, match_type=match_type, from_date=from_date, to_date=to_date, limit=limit_per_domain
                )

        gathered = await parallel_ok(*[_fetch_one(d) for d in domains_or_urls], label="wayback_cdx:410")
        all_results: list[CDXSearchResult] = []
        for res in gathered:
            if isinstance(res, list):
                all_results.extend(res)
        self._stats["domains_searched"] += len(domains_or_urls)
        self._stats["total_results"] += len(all_results)
        elapsed = time.monotonic() - start
        return CDXDeepSearchResult(
            query=",".join(domains_or_urls[:5]),
            match_type=match_type,
            total_rows=len(all_results),
            results=all_results[:MAX_CDX_RESULTS],
            duration_s=elapsed,
        )

    async def search_full(
        self,
        domains_or_urls: list[str],
        *,
        match_type: str = "domain",
        from_date: str | None = None,
        to_date: str | None = None,
        max_per_domain: int = MAX_CDX_RESULTS_FULL,
        concurrency: int = 2,
        deduplicate: bool = True,
    ) -> CDXDeepSearchResult:
        """
        P8-003: CDX deep search with FULL pagination.

        Fetches ALL archived URLs for each domain by paginating through
        all CDX API result pages (using resumeKey cursor). Unlike search()
        which is limited to MAX_CDX_RESULTS (500), this returns up to
        max_per_domain (default 5000) per domain.

        WARNING: This can take significant time for domains with large
        archives (archive.org itself has 20+ years). Rate limiting
        between pages is enforced.

        Args:
            domains_or_urls: List of domains or full URLs
            match_type:      CDX match type (default: domain)
            from_date:       Optional start date YYYYMMDD
            to_date:         Optional end date YYYYMMDD
            max_per_domain:  Max results per domain (default: 5000)
            concurrency:     Max concurrent domain searches (Semaphore)
            deduplicate:     Deduplicate results by original URL (default: True)

        Returns:
            CDXDeepSearchResult with paginated findings + telemetry.
        """
        start = time.monotonic()
        session = await self._ensure_session()
        semaphore = asyncio.Semaphore(concurrency)
        last_request = 0.0

        async def _fetch_one(domain: str) -> list[CDXSearchResult]:
            nonlocal last_request
            async with semaphore:
                elapsed = time.monotonic() - last_request
                if elapsed < RATE_LIMIT_S:
                    await asyncio.sleep(RATE_LIMIT_S - elapsed)
                last_request = time.monotonic()
                return await cdx_deep_search_full(
                    domain,
                    session,
                    match_type=match_type,
                    from_date=from_date,
                    to_date=to_date,
                    max_total=max_per_domain,
                )

        gathered = await parallel_ok(*[_fetch_one(d) for d in domains_or_urls], label="wayback_cdx:search_full")

        # Collect results with optional deduplication
        all_results: list[CDXSearchResult] = []
        # M-2026-FIX: bounded RBF for URL dedup.
        seen_urls: RotatingBloomFilter = RotatingBloomFilter(max_elements=100_000, error_rate=0.005)
        for res in gathered:
            if isinstance(res, list):
                if deduplicate:
                    for r in res:
                        if r.original not in seen_urls:
                            seen_urls.add(r.original)
                            all_results.append(r)
                else:
                    all_results.extend(res)

        self._stats["domains_searched"] += len(domains_or_urls)
        self._stats["total_results"] += len(all_results)
        elapsed = time.monotonic() - start
        return CDXDeepSearchResult(
            query=",".join(domains_or_urls[:5]),
            match_type=match_type,
            total_rows=len(all_results),
            results=all_results,
            duration_s=elapsed,
        )

    async def search_batch(
        self, domains: list[str], *, match_type: str = "domain", concurrency: int = 3
    ) -> list[CDXSearchResult]:
        """Batch search across domains with concurrency + rate limiting."""
        session = await self._ensure_session()
        results = await cdx_deep_search_batch(domains, session, match_type=match_type, concurrency=concurrency)
        self._stats["domains_searched"] += len(domains)
        self._stats["total_results"] += len(results)
        return results

    def get_stats(self) -> dict:
        return self._stats.copy()
