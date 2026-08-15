"""
Internet Archive TV News Adapter.

Sprint P2-1: Internet Archive TV News crawler pro OSINT orchestrátor.


API:
- Archive.org Advanced Search API pro TV News collection
- Endpoint: https://archive.org/advancedsearch.php
- Collection: tv (TV News Archive)

Rules:
- HTTP API only (httpx)
- bounded top-k (MAX_RESULTS=20)
- dedup URLs
- passive only (žádné ukládání obsahu)
- fail-soft na všech chybách
- M1-safe: žádné torch/sklearn, pouze stdlib + httpx
- env gate: HLEDAC_ENABLE_TV_NEWS=1
"""

import asyncio
import logging
import time
from typing import Any

from hledac.universal.discovery.base import DiscoveryBatchResult, DiscoveryHit
from hledac.universal.discovery.base import BaseDiscoveryMixin, DiscoveryResult
from _core import aclose

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_NAME: str = "tvnews"
_MAX_RESULTS: int = 20
_HARD_MAX_RESULTS: int = 50
_DEFAULT_TIMEOUT_S: float = 15.0
_BASE_URL: str = "https://archive.org"
_SEARCH_URL: str = f"{_BASE_URL}/advancedsearch.php"


# ---------------------------------------------------------------------------
# Discovery hit builder
# ---------------------------------------------------------------------------


def _make_hit(
    query: str,
    identifier: str,
    title: str,
    date: str,
    description: str,
    station: str,
    show_name: str,
    now_ts: float,
    rank: int,
) -> DiscoveryHit:
    """Build a DiscoveryHit from TV News metadata."""
    # Archive.org TV News URL pattern
    archive_url = f"{_BASE_URL}/details/{identifier}"

    snippet_parts = []
    if date:
        snippet_parts.append(f"Date: {date}")
    if station:
        snippet_parts.append(f"Station: {station}")
    if show_name:
        snippet_parts.append(f"Show: {show_name}")
    if description:
        snippet_parts.append(f"Desc: {description[:100]}")
    snippet = " | ".join(snippet_parts) if snippet_parts else title

    return DiscoveryHit(
        query=query,
        title=title[:200] if title else f"TV News: {identifier}",
        url=archive_url,
        snippet=snippet[:500],
        source=_SOURCE_NAME,
        rank=rank,
        retrieved_ts=now_ts,
        score=0.6,  # TV news generally high credibility
        reason="tvnews_archive",
    )


# ---------------------------------------------------------------------------
# Main search function
# ---------------------------------------------------------------------------


# Status code error mapping
_STATUS_ERRORS: dict[int | str, tuple[str, str]] = {
    403: ("http_403", "tvnews_forbidden"),
    429: ("http_429", "tvnews_rate_limited"),
}


async def async_search_tvnews(
    query: str,
    max_results: int = 10,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> DiscoveryBatchResult:
    """
    Search Internet Archive TV News collection for matching broadcasts.

    Args:
        query: Search query string (matched against title, description, station)
        max_results: Max hits to return (default 10, hard cap 50)
        timeout_s: HTTP timeout in seconds (default 15.0)

    Returns:
        DiscoveryBatchResult with TV news archive URLs and metadata.

    Fail-soft: returns empty hits on any error.
    """
    # Bounds
    try:
        max_results = max(1, min(int(max_results), _HARD_MAX_RESULTS))
    except (TypeError, ValueError):
        max_results = _MAX_RESULTS

    query = query.strip() if query else ""
    if not query:
        return DiscoveryBatchResult(hits=(), error="empty_query")

    start = time.monotonic()

    # F-01: Canonical session pool — reuse httpx client instead of per-call instantiation
    try:
        from hledac.universal.transport.session_pool import session_pool
    except Exception as exc:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error_type="import_error",
            elapsed_s=elapsed,
            error=f"session_pool_unavailable:{exc}",
        )

    # Build search params — TV News collection
    params = {
        "q": f"collection:tvnews AND ({query})",
        "output": "json",
        "rows": str(max_results),
        "fl": "identifier,title,date,description,station,show_name,subject",
    }

    data, elapsed = await _fetch_tvnews_data(session_pool, params, timeout_s, start)
    if data is None:
        return _make_error_result("provider_exception", elapsed, "tvnews_error")
    if isinstance(data, DiscoveryBatchResult):
        return data

    return _process_tvnews_response(data, query, max_results, elapsed)


async def _fetch_tvnews_data(session_pool, params: dict, timeout_s: float, start: float):
    """Fetch JSON data from TV News API with error handling."""
    try:
        async with asyncio.timeout(timeout_s):
            session = await session_pool.httpx()
            headers = {
                "User-Agent": "Hledac/1.0 (research bot; OSINT orchestrator)",
                "Accept": "application/json",
            }
            async with session.get(_SEARCH_URL, params=params, headers=headers) as response:
                elapsed = time.monotonic() - start
                return await _handle_tvnews_response(response, elapsed, start)
    except TimeoutError:
        return _make_error_result("timeout", time.monotonic() - start, "tvnews_timeout"), time.monotonic() - start
    except asyncio.CancelledError:
        raise
    except Exception:
        return _make_error_result("provider_exception", time.monotonic() - start, "tvnews_error"), time.monotonic() - start


async def _handle_tvnews_response(response, elapsed: float, start: float):
    """Handle TV News HTTP response and return parsed data."""
    status = response.status

    # Check status-specific errors
    if status in _STATUS_ERRORS:
        error_type, error_msg = _STATUS_ERRORS[status]
        return _make_error_result(error_type, elapsed, error_msg), elapsed
    if status >= 500:
        return _make_error_result("http_5xx", elapsed, f"tvnews_server_error_{status}"), elapsed
    if status != 200:
        return _make_error_result("server_error", elapsed, f"tvnews_http_{status}"), elapsed

    # Parse JSON response
    try:
        return await response.json(), elapsed
    except Exception as e:
        return _make_error_result("parse_error", elapsed, f"tvnews_parse_error:{e}"), elapsed


def _make_error_result(error_type: str, elapsed: float, error: str) -> DiscoveryBatchResult:
    """Create a DiscoveryBatchResult for error cases."""
    return DiscoveryBatchResult(
        hits=(),
        error_type=error_type,
        elapsed_s=elapsed,
        provider_name=_SOURCE_NAME,
        provider_chain=(_SOURCE_NAME,),
        source_family="archive",
        error=error,
    )


def _process_tvnews_response(data: dict, query: str, max_results: int, elapsed: float) -> DiscoveryBatchResult:
    """Process TV News API response and extract hits."""
    if not isinstance(data, dict):
        return DiscoveryBatchResult(
            hits=(), error_type="provider_empty", elapsed_s=elapsed,
            provider_name=_SOURCE_NAME, provider_chain=(_SOURCE_NAME,), source_family="archive",
        )

    docs = data.get("response", {}).get("docs", [])
    if not docs:
        return DiscoveryBatchResult(
            hits=(), error_type="provider_empty", elapsed_s=elapsed,
            provider_name=_SOURCE_NAME, provider_chain=(_SOURCE_NAME,), source_family="archive",
        )

    seen_ids: set[str] = set()
    hits_list: list[DiscoveryHit] = []
    now_ts = time.time()

    for doc in docs:
        if len(hits_list) >= max_results:
            break
        hit = _process_tvnews_doc(doc, query, now_ts, len(hits_list), seen_ids)
        if hit is not None:
            hits_list.append(hit)

    return DiscoveryBatchResult(
        hits=tuple(hits_list),
        provider_name=_SOURCE_NAME,
        provider_chain=(_SOURCE_NAME,),
        source_family="archive",
        elapsed_s=elapsed,
        error_type="none" if hits_list else "provider_empty",
    )


def _process_tvnews_doc(doc: dict, query: str, now_ts: float, rank: int, seen_ids: set[str]) -> DiscoveryHit | None:
    """Process a single TV News document into a DiscoveryHit."""
    identifier = doc.get("identifier", "")
    if not identifier or identifier in seen_ids:
        return None
    seen_ids.add(identifier)

    description = doc.get("description", "")
    if isinstance(description, list):
        description = " ".join(str(d) for d in description[:3])

    subject = doc.get("subject", "")
    if isinstance(subject, list):
        subject = "; ".join(str(s) for s in subject[:5])

    extra_snippet = f" Topics: {subject[:80]}" if subject else ""
    full_description = f"{description}{extra_snippet}" if description else extra_snippet

    return _make_hit(
        query=query,
        identifier=identifier,
        title=doc.get("title", ""),
        date=doc.get("date", ""),
        description=full_description,
        station=doc.get("station", ""),
        show_name=doc.get("show_name", ""),
        now_ts=now_ts,
        rank=rank,
    )


# ---------------------------------------------------------------------------
# Standalone query function for direct use
# ---------------------------------------------------------------------------


async def search_tvnews_for_query(
    query: str,
    max_results: int = 10,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> list[dict[str, Any]]:
    """
    Search TV News and return structured dicts for sidecar processing.

    Returns list of dicts with tvnews finding fields.
    """
    result = await async_search_tvnews(
        query=query,
        max_results=max_results,
        timeout_s=timeout_s,
    )

    findings = []
    for hit in result.hits:
        findings.append(
            {
                "source_type": "tvnews",
                "query": query,
                "ioc_type": "tv_broadcast",
                "ioc_value": hit.url,
                "title": hit.title,
                "url": hit.url,
                "snippet": hit.snippet,
                "confidence": hit.score,
                "retrieved_ts": hit.retrieved_ts,
                "reason": hit.reason,
            }
        )
    return findings

class TVNewsAdapter(BaseDiscoveryMixin):
    """
    Internet Archive TV News adapter using BaseDiscoveryMixin infrastructure.

    Wraps async_search_tvnews() as _do_discover().
    """

    name: str = "tvnews"
    source_type: str = "archive"

    @property
    def rate_limit_rpm(self) -> int:
        return 20  # Archive.org is respectful of rate limits

    @property
    def retry_attempts(self) -> int:
        return 3

    @property
    def retry_base_delay_s(self) -> float:
        return 2.0

    @property
    def timeout_s(self) -> float:
        return 15.0

    async def _do_discover(
        self, query: str, limit: int
    ):
        """Wrap async_search_tvnews() as an async iterator."""
        try:
            result = await async_search_tvnews(query, max_results=limit)
        except Exception:
            return

        for hit in result.hits:
            metadata: dict[str, str] = {}

            yield DiscoveryResult(
                query=hit.query,
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                source=hit.source,
                source_type=self.source_type,
                rank=hit.rank,
                retrieved_ts=hit.retrieved_ts,
                score=hit.score,
                reason=hit.reason,
                metadata=metadata,
            )
