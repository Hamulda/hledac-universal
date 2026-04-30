"""
Providerless Discovery Cascade.

Sprint F206AM: Providerless Discovery Mesh Phase 1

Fallback order:
1. DuckDuckGo (primary, via duckduckgo_adapter)
2. Historical Frontier (DuckDB shadow_findings)
3. Wayback CDX (Internet Archive)

Env gate: HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1 (default disabled)
"""

from __future__ import annotations

import asyncio
import os
import time

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryBatchResult,
)


# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------

_PROVIDERLESS_ENABLED = os.environ.get(
    "HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "0"
).strip().lower() in ("1", "true", "yes", "on")


def is_providerless_enabled() -> bool:
    """Check if providerless discovery is enabled via env var."""
    return _PROVIDERLESS_ENABLED


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


async def async_search_providerless(
    query: str,
    max_results: int = 10,
    timeout_s: float = 30.0,
) -> DiscoveryBatchResult:
    """
    Providerless discovery cascade: DDG → Historical Frontier → Wayback CDX.

    Enabled only when HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1.
    Otherwise falls back to standard DDG immediately.

    Args:
        query:       Search query string.
        max_results: Max hits to return (default 10).
        timeout_s:   Total timeout for all layers (default 30s).

    Returns:
        DiscoveryBatchResult with hits and provider_chain metadata.
    """
    if not _PROVIDERLESS_ENABLED:
        # Gate disabled — use standard DDG
        from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
        return await async_search_public_web(query, max_results=max_results, timeout_s=timeout_s)

    start = time.monotonic()

    # Layer 1: DuckDuckGo primary
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web

    try:
        async with asyncio.timeout(min(timeout_s, 20.0)):
            result = await async_search_public_web(query, max_results=max_results, timeout_s=timeout_s)
    except asyncio.TimeoutError:
        result = DiscoveryBatchResult(
            hits=(),
            error="timeout",
            error_type="timeout",
            elapsed_s=min(timeout_s, 20.0),
        )

    elapsed = time.monotonic() - start

    # If DDG returned hits, return them with provider chain
    if result.hits and not result.error:
        elapsed = time.monotonic() - start
        return DiscoveryBatchResult(
            hits=result.hits,
            error=result.error,
            fallback_triggered=None,
            provider_name="duckduckgo",
            provider_chain=("duckduckgo",),
            source_family="search",
            elapsed_s=elapsed,
            error_type=None,
        )

    # Layer 2: Historical Frontier
    from hledac.universal.discovery.historical_frontier import (
        async_search_historical_frontier,
    )

    remaining_timeout = max(1.0, timeout_s - elapsed)
    try:
        async with asyncio.timeout(min(remaining_timeout, 2.0)):
            hf_result = await async_search_historical_frontier(
                query, max_results=max_results, timeout_s=2.0
            )
    except asyncio.TimeoutError:
        hf_result = DiscoveryBatchResult(
            hits=(),
            error="historical_frontier_timeout",
            error_type="timeout",
            elapsed_s=remaining_timeout,
        )

    elapsed = time.monotonic() - start

    if hf_result.hits:
        return DiscoveryBatchResult(
            hits=hf_result.hits,
            error=result.error,
            fallback_triggered="primary_backend_failed_fallback_succeeded",
            provider_name="historical_frontier",
            provider_chain=("duckduckgo", "historical_frontier"),
            source_family="historical",
            elapsed_s=elapsed,
            error_type=hf_result.error_type or "none",
        )

    # Layer 3: Wayback CDX
    from hledac.universal.discovery.wayback_cdx_adapter import (
        async_search_wayback_cdx,
    )

    remaining_timeout = max(1.0, timeout_s - elapsed)
    try:
        async with asyncio.timeout(min(remaining_timeout, 5.0)):
            wb_result = await async_search_wayback_cdx(
                query, max_results=max_results, timeout_s=5.0
            )
    except asyncio.TimeoutError:
        wb_result = DiscoveryBatchResult(
            hits=(),
            error="wayback_cdx_timeout",
            error_type="timeout",
            elapsed_s=remaining_timeout,
        )

    elapsed = time.monotonic() - start

    if wb_result.hits:
        return DiscoveryBatchResult(
            hits=wb_result.hits,
            error=result.error,
            fallback_triggered="primary_backend_failed_fallback_succeeded",
            provider_name="wayback_cdx",
            provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
            source_family="archive",
            elapsed_s=elapsed,
            error_type=wb_result.error_type or "none",
        )

    # All layers failed
    return DiscoveryBatchResult(
        hits=(),
        error=result.error,
        fallback_triggered="primary_backend_failed_fallback_failed",
        provider_name=None,
        provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
        source_family=None,
        elapsed_s=elapsed,
        error_type=result.error_type or "unknown_backend_error",
    )
