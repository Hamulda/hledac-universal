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
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional

import aiohttp

try:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
except ImportError:
    CanonicalFinding = None

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_CDX_RESULTS: int = 500
RATE_LIMIT_S: float = 2.0
TIMEOUT_PER_REQUEST: float = 60.0

# CDX API endpoint
CDX_API = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE_URL = "https://web.archive.org"


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class CDXSearchResult:
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
        if self.replay_url and not self.timestamp:
            self.replay_url = ""
        elif self.timestamp and self.original:
            # Build replay URL: https://web.archive.org/web/{timestamp}/{original}
            safe_url = self.original[:500]  # safety cap
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

    def to_canonical_finding(
        self, query: str, _sprint_id: str = ""
    ) -> Optional["CanonicalFinding"]:
        if CanonicalFinding is None:
            return None
        try:
            payload = self._build_payload()
            return CanonicalFinding(
                finding_id=f"cdx-{self.digest[:16] if self.digest else self.timestamp[:12]}",
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


@dataclass
class CDXDeepSearchResult:
    """Result of a cdx_deep_search() call."""
    query: str
    match_type: str
    total_rows: int = 0
    results: List[CDXSearchResult] = field(default_factory=list)
    error: Optional[str] = None
    timeout: bool = False
    duration_s: float = 0.0
    rate_limited: bool = False

    def to_findings(self, query: str, sprint_id: str) -> list:
        if self.error:
            return []
        return [r.to_canonical_finding(query, sprint_id) for r in self.results if r.to_canonical_finding(query, sprint_id)]


# ── Core function ─────────────────────────────────────────────────────────────


async def cdx_deep_search(
    domain: str,
    session: aiohttp.ClientSession,
    *,
    match_type: str = "domain",    # exact | prefix | host | domain
    from_date: Optional[str] = None,  # YYYYMMDD
    to_date: Optional[str] = None,    # YYYYMMDD
    limit: int = MAX_CDX_RESULTS,
) -> List[CDXSearchResult]:
    """
    CDX fulltext discovery — finds archived URLs for a domain.

    Unlike simple snapshot lookups, this discovers:
      - Subdomains (*.example.com)
      - Historical paths no longer on live web
      - Content deleted from live site
      - Old endpoints and API routes

    Args:
        domain:      Domain to search (e.g. "example.com")
        session:     aiohttp.ClientSession
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
    # Build URL param based on match_type
    if match_type == "domain":
        url_param = f"*.{domain}"
    elif match_type == "host":
        url_param = domain
    elif match_type == "prefix":
        url_param = f"http://{domain}/*"
    else:
        url_param = domain  # exact

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
        async with session.get(
            CDX_API,
            params=params,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_PER_REQUEST),
        ) as resp:
            if resp.status == 429:
                logger.warning(f"CDX rate limited for {domain}")
                return []
            if resp.status != 200:
                logger.debug(f"CDX {domain} → HTTP {resp.status}")
                return []

            raw: list[list[str]] = await resp.json(content_type=None)
            return _parse_cdx_response(raw)

    except asyncio.TimeoutError:
        logger.debug(f"CDX deep search timeout for {domain}")
        return []
    except Exception as e:
        logger.debug(f"CDX deep search error for {domain}: {e}")
        return []


def _parse_cdx_response(raw: list[list[str]]) -> List[CDXSearchResult]:
    """Parse CDX JSON response into CDXSearchResult list."""
    if not raw or len(raw) < 2:
        return []

    headers = raw[0]
    results: List[CDXSearchResult] = []

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


# ── Async batch wrapper ───────────────────────────────────────────────────────


async def cdx_deep_search_batch(
    domains: List[str],
    session: aiohttp.ClientSession,
    *,
    match_type: str = "domain",
    concurrency: int = 3,
    rate_limit_s: float = RATE_LIMIT_S,
) -> List[CDXSearchResult]:
    """
    Batch CDX deep search across multiple domains with rate limiting.

    Args:
        domains:      List of domain strings
        session:      aiohttp.ClientSession
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
    all_results: List[CDXSearchResult] = []
    seen_urls: set[str] = set()  # deduplicate

    async def _fetch_one(domain: str) -> List[CDXSearchResult]:
        nonlocal last_request
        async with semaphore:
            elapsed = time.monotonic() - last_request
            if elapsed < rate_limit_s:
                await asyncio.sleep(rate_limit_s - elapsed)
            last_request = time.monotonic()

            results = await cdx_deep_search(domain, session, match_type=match_type)
            # Deduplicate
            unique = [r for r in results if r.original not in seen_urls]
            for r in unique:
                seen_urls.add(r.original)
            return unique

    gathered = await asyncio.gather(
        *[_fetch_one(d) for d in domains],
        return_exceptions=True,
    )

    for res in gathered:
        if isinstance(res, list):
            all_results.extend(res)

    return all_results


# ── WaybackCDX deep search extension ─────────────────────────────────────────


class WaybackCDXDeepSearch:
    """
    High-level CDX deep search with session management,
    rate limiting, and CanonicalFinding output.

    Integrates with existing WaybackCDX in archive_discovery.py
    as an extension layer — adds domain/subdomain discovery
    that WaybackCDX.get_snapshots() doesn't cover.
    """

    def __init__(
        self,
        session_provider: Optional[Callable[[], Awaitable[aiohttp.ClientSession]]] = None,
    ) -> None:
        self._session_provider = session_provider
        self._session: Optional[aiohttp.ClientSession] = None
        self._stats: dict[str, int] = {
            "domains_searched": 0,
            "total_results": 0,
            "errors": 0,
        }

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session_provider is not None:
            return await self._session_provider()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def search(
        self,
        domains_or_urls: List[str],
        *,
        match_type: str = "domain",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
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

        async def _fetch_one(domain: str) -> List[CDXSearchResult]:
            nonlocal last_request
            async with semaphore:
                elapsed = time.monotonic() - last_request
                if elapsed < RATE_LIMIT_S:
                    await asyncio.sleep(RATE_LIMIT_S - elapsed)
                last_request = time.monotonic()
                return await cdx_deep_search(
                    domain,
                    session,
                    match_type=match_type,
                    from_date=from_date,
                    to_date=to_date,
                    limit=limit_per_domain,
                )

        gathered = await asyncio.gather(
            *[_fetch_one(d) for d in domains_or_urls],
            return_exceptions=True,
        )

        all_results: List[CDXSearchResult] = []
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

    async def search_batch(
        self,
        domains: List[str],
        *,
        match_type: str = "domain",
        concurrency: int = 3,
    ) -> List[CDXSearchResult]:
        """Batch search across domains with concurrency + rate limiting."""
        session = await self._ensure_session()
        results = await cdx_deep_search_batch(
            domains,
            session,
            match_type=match_type,
            concurrency=concurrency,
        )
        self._stats["domains_searched"] += len(domains)
        self._stats["total_results"] += len(results)
        return results

    def get_stats(self) -> dict:
        return self._stats.copy()