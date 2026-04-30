"""
Historical Frontier — DuckDB-backed read-only discovery.

Sprint F206AM: Providerless Discovery Mesh Phase 1

Rules:
- read-only DuckDB
- no schema migration
- top-k bounded
- no heavy imports
- fail-soft
- returns DiscoveryHit objects
"""

from __future__ import annotations

import asyncio
import time

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryHit,
    DiscoveryBatchResult,
)

# DuckDB store interface for historical query
_HISTORICAL_STORE_PATH = "~/.hledac/hledac.duckdb"


# ---------------------------------------------------------------------------
# Historical Frontier
# ---------------------------------------------------------------------------

_PROVENANCE_SOURCE_RE = __import__("re").compile(
    r'"source"\s*:\s*"([^"]+)"'
)


def _extract_source_from_provenance(provenance_json: str | None) -> str:
    """Extract source field from provenance JSON, default to 'historical_frontier'."""
    if not provenance_json:
        return "historical_frontier"
    m = _PROVENANCE_SOURCE_RE.search(provenance_json)
    return m.group(1) if m else "historical_frontier"


async def async_search_historical_frontier(
    query: str,
    max_results: int = 10,
    timeout_s: float = 2.0,
) -> DiscoveryBatchResult:
    """
    Read-only DuckDB historical URL discovery.

    Queries shadow_findings for prior art: previous queries and their
    results (url, title, snippet) that match the current query tokens.

    Args:
        query:        Search query string.
        max_results:  Max hits to return (default 10, hard cap 20).
        timeout_s:    Query timeout in seconds (default 2.0).

    Returns:
        _DiscoveryBatchResult with hits from DuckDB shadow_findings.

    Fail-soft: returns empty hits on any error (import, SQL, timing).
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
        import duckdb

        db_path = _HISTORICAL_STORE_PATH.replace("~", str(__import__("pathlib").Path.home()))
        conn = duckdb.connect(db_path, read_only=True)

        try:
            # Tokenize query — match tokens against stored query + title + url
            tokens = {t.lower().strip(".,;:!?()[]{}-_") for t in query.split() if len(t) > 1}
            if not tokens:
                return _DiscoveryBatchResult(hits=(), error="empty_query")

            # Build LIKE pattern from most significant token (first substantial word)
            primary = next((t for t in query.split() if len(t) > 2), query.split()[0] if query.split() else "")
            pattern = f"%{primary}%"

            async def _query() -> list:
                return conn.execute(
                    f"""
                    SELECT query, title, url, snippet, provenance_json
                    FROM shadow_findings
                    WHERE (
                        query ILIKE ? OR
                        title ILIKE ? OR
                        url ILIKE ?
                    )
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    [pattern, pattern, pattern, max_results * 3],
                ).fetchall()

            async with asyncio.timeout(timeout_s):
                rows = await _query()
        finally:
            conn.close()
    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        return _DiscoveryBatchResult(
            hits=(),
            error_type="timeout",
            elapsed_s=elapsed,
            error="historical_frontier_timeout",
        )
    except Exception:
        elapsed = time.monotonic() - start
        return _DiscoveryBatchResult(
            hits=(),
            error_type="provider_exception",
            elapsed_s=elapsed,
            error="historical_frontier_error",
        )

    if not rows:
        elapsed = time.monotonic() - start
        return _DiscoveryBatchResult(
            hits=(),
            error_type="provider_empty",
            elapsed_s=elapsed,
            provider_name="historical_frontier",
            provider_chain=("historical_frontier",),
            source_family="historical",
        )

    # Build hits — score by token overlap
    seen_urls: set[str] = set()
    hits_list: list[_DiscoveryHit] = []
    now_ts = time.time()

    for row in rows:
        row_query, title, url, snippet, provenance = row
        if not url or url in seen_urls:
            continue
        # Score by token overlap
        score = 0.0
        reason = None
        if row_query:
            row_lower = row_query.lower()
            overlap = tokens & {t for t in row_lower.split()}
            if overlap:
                score = min(0.8, len(overlap) * 0.15)
                reason = "query_match"
        if title:
            title_lower = title.lower()
            overlap = tokens & {t for t in title_lower.split()}
            if overlap:
                score = max(score, min(0.6, len(overlap) * 0.1))
                reason = reason or "title_match"
        if not score:
            score = 0.3
            reason = reason or "url_match"

        hits_list.append(
            _DiscoveryHit(
                query=query,
                title=title or "",
                url=url,
                snippet=snippet or "",
                source=_extract_source_from_provenance(provenance),
                rank=len(hits_list),
                retrieved_ts=now_ts,
                score=score,
                reason=reason,
            )
        )
        seen_urls.add(url)
        if len(hits_list) >= max_results:
            break

    elapsed = time.monotonic() - start
    return _DiscoveryBatchResult(
        hits=tuple(hits_list),
        provider_name="historical_frontier",
        provider_chain=("historical_frontier",),
        source_family="historical",
        elapsed_s=elapsed,
        error_type="none" if hits_list else "provider_empty",
    )
