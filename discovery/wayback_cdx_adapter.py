"""
Wayback CDX — Internet Archive CDX API fallback.

Sprint F206AM: Providerless Discovery Mesh Phase 1
Sprint F206AS: Transport Alignment — uses shared aiohttp session + circuit breaker.

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

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
    DiscoveryHit,
)
from hledac.universal.network.session_runtime import async_get_aiohttp_session
from hledac.universal.transport.circuit_breaker import checked_aiohttp_get

# ---------------------------------------------------------------------------
# Wayback CDX API
# ---------------------------------------------------------------------------

_WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"


async def async_search_wayback_cdx(
    query: str,
    max_results: int = 10,
    timeout_s: float = 5.0,
) -> DiscoveryBatchResult:
    """
    Wayback CDX API — historical snapshots matching query.

    Args:
        query:       Search query string.
        max_results: Max hits to return (default 10, hard cap 20).
        timeout_s:   HTTP timeout in seconds (default 5.0).

    Returns:
        DiscoveryBatchResult with archive.org snapshot URLs.

    Fail-soft: returns empty hits on any error.
    """
    # Bounds
    try:
        max_results = max(1, min(int(max_results), 20))
    except (TypeError, ValueError):
        max_results = 10
    query = query.strip() if query else ""
    if not query:
        return DiscoveryBatchResult(hits=(), error="empty_query")

    start = time.monotonic()

    try:
        import aiohttp
    except ImportError:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
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

    session = await async_get_aiohttp_session()
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    try:
        async with asyncio.timeout(timeout_s):
            resp, err = await checked_aiohttp_get(
                session,
                _WAYBACK_CDX_URL,
                params=params,
                headers={"User-Agent": "Hledac/1.0 (research bot)"},
                timeout=timeout,
                failure_kind="wayback_cdx",
            )
            if err:
                elapsed = time.monotonic() - start
                # Map circuit_breaker error strings to taxonomy
                if err.startswith("circuit_breaker_open:"):
                    return DiscoveryBatchResult(
                        hits=(),
                        error_type="circuit_breaker_open",
                        elapsed_s=elapsed,
                        provider_name="wayback_cdx",
                        provider_chain=("wayback_cdx",),
                        source_family="archive",
                        error=err,
                    )
                if err == "timeout":
                    return DiscoveryBatchResult(
                        hits=(),
                        error_type="timeout",
                        elapsed_s=elapsed,
                        provider_name="wayback_cdx",
                        provider_chain=("wayback_cdx",),
                        source_family="archive",
                        error="wayback_cdx_timeout",
                    )
                if err == "client_error":
                    return DiscoveryBatchResult(
                        hits=(),
                        error_type="network_error",
                        elapsed_s=elapsed,
                        provider_name="wayback_cdx",
                        provider_chain=("wayback_cdx",),
                        source_family="archive",
                        error="wayback_cdx_network_error",
                    )
                # Any other err
                return DiscoveryBatchResult(
                    hits=(),
                    error_type="network_error",
                    elapsed_s=elapsed,
                    provider_name="wayback_cdx",
                    provider_chain=("wayback_cdx",),
                    source_family="archive",
                    error=f"wayback_cdx_fetch_error:{err}",
                )

            # checked_aiohttp_get returns resp on any status (including 4xx/5xx)
            status = resp.status
            if status == 403:
                elapsed = time.monotonic() - start
                return DiscoveryBatchResult(
                    hits=(),
                    error_type="http_403",
                    elapsed_s=elapsed,
                    provider_name="wayback_cdx",
                    provider_chain=("wayback_cdx",),
                    source_family="archive",
                    error="wayback_cdx_forbidden",
                )
            if status == 429:
                elapsed = time.monotonic() - start
                return DiscoveryBatchResult(
                    hits=(),
                    error_type="http_429",
                    elapsed_s=elapsed,
                    provider_name="wayback_cdx",
                    provider_chain=("wayback_cdx",),
                    source_family="archive",
                    error="wayback_cdx_rate_limited",
                )
            if status >= 500:
                elapsed = time.monotonic() - start
                return DiscoveryBatchResult(
                    hits=(),
                    error_type="http_5xx",
                    elapsed_s=elapsed,
                    provider_name="wayback_cdx",
                    provider_chain=("wayback_cdx",),
                    source_family="archive",
                    error=f"wayback_cdx_server_error_{status}",
                )
            if status != 200:
                elapsed = time.monotonic() - start
                return DiscoveryBatchResult(
                    hits=(),
                    error_type="server_error",
                    elapsed_s=elapsed,
                    provider_name="wayback_cdx",
                    provider_chain=("wayback_cdx",),
                    source_family="archive",
                    error=f"wayback_cdx_http_{status}",
                )

            data = await resp.json()

    except TimeoutError:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error_type="timeout",
            elapsed_s=elapsed,
            provider_name="wayback_cdx",
            provider_chain=("wayback_cdx",),
            source_family="archive",
            error="wayback_cdx_timeout",
        )
    except asyncio.CancelledError:
        raise  # Re-raise CancelledError — do not swallow
    except Exception:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
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
        return DiscoveryBatchResult(
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
    hits_list: list[DiscoveryHit] = []
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
            DiscoveryHit(
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

    return DiscoveryBatchResult(
        hits=tuple(hits_list),
        provider_name="wayback_cdx",
        provider_chain=("wayback_cdx",),
        source_family="archive",
        elapsed_s=elapsed,
        error_type="none" if hits_list else "provider_empty",
    )
