"""
Wayback CDX — Internet Archive CDX API fallback.

Sprint F206AM: Providerless Discovery Mesh Phase 1

Rules:
- HTTP API only
- bounded top-k
- dedup URLs
- no body fetch
- passive only
- fail-soft
"""

from __future__ import annotations

import asyncio
import time
import msgspec


# ---------------------------------------------------------------------------
# DTO — mirrors duckduckgo_adapter DTOs
# ---------------------------------------------------------------------------


class _DiscoveryHit(msgspec.Struct, frozen=True, gc=False):
    """Local DTO — mirrors duckduckgo_adapter.DiscoveryHit."""

    query: str
    title: str
    url: str
    snippet: str
    source: str
    rank: int
    retrieved_ts: float
    score: float = 0.0
    reason: str | None = None


class _DiscoveryBatchResult(msgspec.Struct, frozen=True, gc=False):
    """Local DTO — mirrors duckduckgo_adapter.DiscoveryBatchResult."""

    hits: tuple[_DiscoveryHit, ...]
    error: str | None = None
    fallback_triggered: str | None = None
    provider_name: str | None = None
    provider_chain: tuple[str, ...] = ()
    source_family: str | None = None
    elapsed_s: float | None = None
    error_type: str | None = None


# ---------------------------------------------------------------------------
# Wayback CDX API
# ---------------------------------------------------------------------------

_WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"


async def async_search_wayback_cdx(
    query: str,
    max_results: int = 10,
    timeout_s: float = 5.0,
) -> _DiscoveryBatchResult:
    """
    Wayback CDX API — historical snapshots matching query.

    Args:
        query:       Search query string.
        max_results: Max hits to return (default 10, hard cap 20).
        timeout_s:   HTTP timeout in seconds (default 5.0).

    Returns:
        _DiscoveryBatchResult with archive.org snapshot URLs.

    Fail-soft: returns empty hits on any error.
    """
    # Bounds
    try:
        max_results = max(1, min(int(max_results), 20))
    except (TypeError, ValueError):
        max_results = 10
    query = query.strip() if query else ""
    if not query:
        return _DiscoveryBatchResult(hits=(), error="empty_query")

    start = time.monotonic()

    try:
        import aiohttp
    except ImportError:
        elapsed = time.monotonic() - start
        return _DiscoveryBatchResult(
            hits=(),
            error_type="import_error",
            elapsed_s=elapsed,
            error="aiohttp_not_available",
        )

    params = {
        "url": query,
        "output": "json",
        "limit": max_results,
        "fl": "url,timestamp,original,mimetype,statuscode",
        "filter": "statuscode:200",
        "from": "1996",
        "to": "2026",
    }

    try:
        async with asyncio.timeout(timeout_s):
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    _WAYBACK_CDX_URL,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                    headers={"User-Agent": "Hledac/1.0 (research bot)"},
                ) as resp:
                    if resp.status != 200:
                        elapsed = time.monotonic() - start
                        return _DiscoveryBatchResult(
                            hits=(),
                            error_type="server_error",
                            elapsed_s=elapsed,
                            provider_name="wayback_cdx",
                            provider_chain=("wayback_cdx",),
                            source_family="archive",
                        )
                    data = await resp.json()
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return _DiscoveryBatchResult(
            hits=(),
            error_type="timeout",
            elapsed_s=elapsed,
            provider_name="wayback_cdx",
            provider_chain=("wayback_cdx",),
            source_family="archive",
            error="wayback_cdx_timeout",
        )
    except Exception:
        elapsed = time.monotonic() - start
        return _DiscoveryBatchResult(
            hits=(),
            error_type="provider_exception",
            elapsed_s=elapsed,
            provider_name="wayback_cdx",
            provider_chain=("wayback_cdx",),
            source_family="archive",
            error="wayback_cdx_error",
        )

    elapsed = time.monotonic() - start

    if not data or not isinstance(data, list):
        return _DiscoveryBatchResult(
            hits=(),
            error_type="provider_empty",
            elapsed_s=elapsed,
            provider_name="wayback_cdx",
            provider_chain=("wayback_cdx",),
            source_family="archive",
        )

    # Skip header row if present
    rows = data[1:] if data and data[0] == ["url", "timestamp", "original", "mimetype", "statuscode"] else data

    seen_urls: set[str] = set()
    hits_list: list[_DiscoveryHit] = []
    now_ts = time.time()

    for row in rows:
        if len(row) < 3:
            continue
        url_entry = row[0]
        timestamp = row[1]
        original_url = row[2] if len(row) > 2 else url_entry
        mimetype = row[3] if len(row) > 3 else ""

        # Skip non-HTML
        if mimetype and mimetype not in ("text/html", "application/xhtml+xml", ""):
            continue

        if not original_url or original_url in seen_urls:
            continue

        # Build Wayback Machine URL for this snapshot
        wayback_url = f"https://web.archive.org/web/{timestamp}/{original_url}"

        hits_list.append(
            _DiscoveryHit(
                query=query,
                title=f"Wayback: {original_url[:80]}",
                url=wayback_url,
                snippet=f"Snapshot from {timestamp[:8]}. Original: {original_url[:100]}",
                source="wayback_cdx",
                rank=len(hits_list),
                retrieved_ts=now_ts,
                score=0.5,
                reason="archive_snapshot",
            )
        )
        seen_urls.add(original_url)
        if len(hits_list) >= max_results:
            break

    return _DiscoveryBatchResult(
        hits=tuple(hits_list),
        provider_name="wayback_cdx",
        provider_chain=("wayback_cdx",),
        source_family="archive",
        elapsed_s=elapsed,
        error_type="none" if hits_list else "provider_empty",
    )
