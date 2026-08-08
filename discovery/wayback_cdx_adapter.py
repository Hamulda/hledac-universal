"""
Wayback CDX — Internet Archive CDX API fallback.

Sprint F206AM: Providerless Discovery Mesh Phase 1
Sprint F206AS: Transport Alignment — uses shared httpx session + circuit breaker.

Rules:
- HTTP API only
- bounded top-k
- dedup URLs
- no body fetch
- passive only
- fail-soft
"""

import asyncio
from typing import Any
import time

from hledac.universal.discovery.base import DiscoveryBatchResult, DiscoveryHit
from hledac.universal.transport.circuit_breaker import get_breaker

# ---------------------------------------------------------------------------
# Wayback CDX API
# ---------------------------------------------------------------------------

_WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Error type mapping for circuit breaker taxonomy
_ERROR_TYPE_MAP: dict[str, tuple[str, str]] = {
    'circuit_breaker_open:': ('circuit_breaker_open', 'wayback_cdx_fetch_error'),
    'timeout': ('timeout', 'wayback_cdx_timeout'),
    'client_error': ('network_error', 'wayback_cdx_network_error'),
}

def _build_error_result(
    error_type: str,
    error: str,
    elapsed: float,
    status_code: int = 0,
) -> DiscoveryBatchResult:
    """Build standardized error result from error type and message."""
    # Map known error patterns
    for prefix, (err_type, err_msg) in _ERROR_TYPE_MAP.items():
        if error.startswith(prefix):
            return DiscoveryBatchResult(
                hits=(),
                error_type=err_type,
                elapsed_s=elapsed,
                provider_name='wayback_cdx',
                provider_chain=('wayback_cdx',),
                source_family='archive',
                error=f'{err_msg}:{error}',
            )

    # Default: generic network error
    return DiscoveryBatchResult(
        hits=(),
        error_type='network_error',
        elapsed_s=elapsed,
        provider_name='wayback_cdx',
        provider_chain=('wayback_cdx',),
        source_family='archive',
        error=f'wayback_cdx_fetch_error:{error}',
    )

def _build_http_error_result(
    status_code: int,
    elapsed: float,
) -> DiscoveryBatchResult:
    """Build result for HTTP error status codes."""
    error_map = {
        403: ('http_403', 'wayback_cdx_forbidden'),
        429: ('http_429', 'wayback_cdx_rate_limited'),
    }
    err_type, err_msg = error_map.get(status_code, ('http_error', 'wayback_cdx_http_error'))

    return DiscoveryBatchResult(
        hits=(),
        error_type=err_type,
        elapsed_s=elapsed,
        provider_name='wayback_cdx',
        provider_chain=('wayback_cdx',),
        source_family='archive',
        error=err_msg,
    )

def _build_success_result(data: Any, elapsed: float) -> DiscoveryBatchResult:
    """Build success result from CDX data."""
    if not data or len(data) < 2:
        return DiscoveryBatchResult(hits=(), elapsed_s=elapsed)

    hits = []
    for row in data[1:]:  # Skip header row
        if len(row) >= 4:
            hits.append({
                'url': row[0],
                'timestamp': row[1],
                'original': row[2],
                'mime': row[3],
            })
        if len(hits) >= 20:
            break

    return DiscoveryBatchResult(
        hits=tuple(hits),
        elapsed_s=elapsed,
        provider_name='wayback_cdx',
        provider_chain=('wayback_cdx',),
        source_family='archive',
    )

async def _fetch_cdx_data(
    session: Any,
    params: dict[str, Any],
    timeout_s: float,
) -> tuple[Any, int | None, str | None]:
    """Fetch CDX data, return (data, status, error)."""
    try:
        async with asyncio.timeout(timeout_s):
            response = await session.get(
                _WAYBACK_CDX_URL,
                params=params,
                headers={'User-Agent': 'Hledac/1.0 (research bot)'},
            )
            status = response.status_code
            data = response.json() if status == 200 else None
            err = None
            return data, status, err
    except asyncio.TimeoutError:
        return None, 0, 'timeout'
    except Exception as e:
        return None, 0, str(e)

async def _parse_cdx_rows(rows: list, max_results: int, query: str, now_ts: float) -> list[DiscoveryHit]:
    """Parse CDX rows into DiscoveryHit objects."""
    hits_list: list[DiscoveryHit] = []
    seen_urls: set[str] = set()

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

    return hits_list

async def async_search_wayback_cdx(
    query: str,
    max_results: int = 10,
    timeout_s: float = 5.0,
) -> DiscoveryBatchResult:
    """
    Wayback CDX API — historical snapshots matching query.
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

    # Get session pool
    try:
        from hledac.universal.transport.session_pool import session_pool
    except Exception as exc:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=(),
            error_type='import_error',
            elapsed_s=elapsed,
            error=f'session_pool_unavailable:{exc}',
        )

    params = {
        'url': query,
        'output': 'json',
        'limit': max_results,
        'fl': 'url,timestamp,original,mimetype,statuscode',
        'filter': 'statuscode:200',
        'from': '1996',
        'to': '2026',
    }

    try:
        session = await session_pool.httpx()
        data, status, err = await _fetch_cdx_data(session, params, timeout_s)
        elapsed = time.monotonic() - start

        # Record circuit breaker
        from urllib.parse import urlparse as _urlparse
        breaker = get_breaker(_urlparse(_WAYBACK_CDX_URL).netloc)
        if err:
            breaker.record_failure(failure_kind='wayback_cdx')
            return _build_error_result('network_error', err, elapsed)
        else:
            breaker.record_success()

        # HTTP error codes
        if status == 403:
            return _build_http_error_result(403, elapsed)
        if status == 429:
            return _build_http_error_result(429, elapsed)
        if status != 200:
            return _build_http_error_result(status, elapsed)

        # Parse and return success
        if not data or not isinstance(data, list):
            return DiscoveryBatchResult(
                hits=(),
                error_type='provider_empty',
                elapsed_s=elapsed,
                provider_name='wayback_cdx',
                provider_chain=('wayback_cdx',),
                source_family='archive',
            )

        # Skip header row if present
        rows = data[1:] if data and data[0] == ['url', 'timestamp', 'original', 'mimetype', 'statuscode'] else data
        hits_list = await _parse_cdx_rows(rows, max_results, query, time.time())

        return DiscoveryBatchResult(
            hits=tuple(hits_list),
            provider_name='wayback_cdx',
            provider_chain=('wayback_cdx',),
            source_family='archive',
            elapsed_s=elapsed,
            error_type='none' if hits_list else 'provider_empty',
        )

    except asyncio.CancelledError:
        raise  # Re-raise CancelledError — do not swallow
    except Exception as e:
        elapsed = time.monotonic() - start
        return _build_error_result('network_error', str(e), elapsed)
