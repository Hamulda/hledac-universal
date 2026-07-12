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

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
    DiscoveryHit,
)

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

    # Lazy import httpx
    try:
        import httpx
    except ImportError:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error_type="import_error",
            elapsed_s=elapsed,
            error="httpx_not_available",
        )

    # Build search params — TV News collection
    # Query syntax: collection:tvnews AND (search terms)
    # collection:tv is wrong; tvnews is the correct Archive.org TV News collection
    params = {
        "q": f"collection:tvnews AND ({query})",
        "output": "json",
        "rows": str(max_results),
        "fl": "identifier,title,date,description,station,show_name,subject",
        # NOTE: sort=date desc + utc_offset returns 0 results for tvnews collection
        # Results are already returned in relevance order by Archive.org
    }

    timeout = httpx.Timeout(timeout_s)

    try:
        async with asyncio.timeout(timeout_s):
            async with httpx.AsyncClient(timeout=timeout) as session:
                headers = {
                    "User-Agent": "Hledac/1.0 (research bot; OSINT orchestrator)",
                    "Accept": "application/json",
                }
                async with session.get(
                    _SEARCH_URL,
                    params=params,
                    headers=headers,
                ) as response:
                    elapsed = time.monotonic() - start
                    status = response.status

                    if status == 403:
                        return DiscoveryBatchResult(
                            hits=(),
                            error_type="http_403",
                            elapsed_s=elapsed,
                            provider_name=_SOURCE_NAME,
                            provider_chain=(_SOURCE_NAME,),
                            source_family="archive",
                            error="tvnews_forbidden",
                        )

                    if status == 429:
                        return DiscoveryBatchResult(
                            hits=(),
                            error_type="http_429",
                            elapsed_s=elapsed,
                            provider_name=_SOURCE_NAME,
                            provider_chain=(_SOURCE_NAME,),
                            source_family="archive",
                            error="tvnews_rate_limited",
                        )

                    if status >= 500:
                        return DiscoveryBatchResult(
                            hits=(),
                            error_type="http_5xx",
                            elapsed_s=elapsed,
                            provider_name=_SOURCE_NAME,
                            provider_chain=(_SOURCE_NAME,),
                            source_family="archive",
                            error=f"tvnews_server_error_{status}",
                        )

                    if status != 200:
                        return DiscoveryBatchResult(
                            hits=(),
                            error_type="server_error",
                            elapsed_s=elapsed,
                            provider_name=_SOURCE_NAME,
                            provider_chain=(_SOURCE_NAME,),
                            source_family="archive",
                            error=f"tvnews_http_{status}",
                        )

                    # Parse JSON response
                    try:
                        data = await response.json()
                    except Exception as e:
                        return DiscoveryBatchResult(
                            hits=(),
                            error_type="parse_error",
                            elapsed_s=elapsed,
                            provider_name=_SOURCE_NAME,
                            provider_chain=(_SOURCE_NAME,),
                            source_family="archive",
                            error=f"tvnews_parse_error:{e}",
                        )

    except TimeoutError:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error_type="timeout",
            elapsed_s=elapsed,
            provider_name=_SOURCE_NAME,
            provider_chain=(_SOURCE_NAME,),
            source_family="archive",
            error="tvnews_timeout",
        )
    except asyncio.CancelledError:
        raise  # Re-raise CancelledError — do not swallow
    except Exception:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error_type="provider_exception",
            elapsed_s=elapsed,
            provider_name=_SOURCE_NAME,
            provider_chain=(_SOURCE_NAME,),
            source_family="archive",
            error="tvnews_error",
        )

    elapsed = time.monotonic() - start

    # Validate response structure
    if not isinstance(data, dict):
        return DiscoveryBatchResult(
            hits=(),
            error_type="provider_empty",
            elapsed_s=elapsed,
            provider_name=_SOURCE_NAME,
            provider_chain=(_SOURCE_NAME,),
            source_family="archive",
        )

    # Extract docs from response
    docs = data.get("response", {}).get("docs", [])
    if not docs:
        return DiscoveryBatchResult(
            hits=(),
            error_type="provider_empty",
            elapsed_s=elapsed,
            provider_name=_SOURCE_NAME,
            provider_chain=(_SOURCE_NAME,),
            source_family="archive",
        )

    # Process results
    seen_ids: set[str] = set()
    hits_list: list[DiscoveryHit] = []
    now_ts = time.time()

    for doc in docs:
        if len(hits_list) >= max_results:
            break

        identifier = doc.get("identifier", "")
        if not identifier or identifier in seen_ids:
            continue

        title = doc.get("title", "")
        date = doc.get("date", "")
        description = doc.get("description", "")
        # description can be a list or string
        if isinstance(description, list):
            description = " ".join(str(d) for d in description[:3])
        station = doc.get("station", "")
        show_name = doc.get("show_name", "")
        subject = doc.get("subject", "")
        if isinstance(subject, list):
            subject = "; ".join(str(s) for s in subject[:5])

        # Build snippet with extra context
        extra_snippet = ""
        if subject:
            extra_snippet = f" Topics: {subject[:80]}"

        full_description = f"{description}{extra_snippet}" if description else extra_snippet

        hit = _make_hit(
            query=query,
            identifier=identifier,
            title=title,
            date=date,
            description=full_description,
            station=station,
            show_name=show_name,
            now_ts=now_ts,
            rank=len(hits_list),
        )
        hits_list.append(hit)
        seen_ids.add(identifier)

    return DiscoveryBatchResult(
        hits=tuple(hits_list),
        provider_name=_SOURCE_NAME,
        provider_chain=(_SOURCE_NAME,),
        source_family="archive",
        elapsed_s=elapsed,
        error_type="none" if hits_list else "provider_empty",
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
