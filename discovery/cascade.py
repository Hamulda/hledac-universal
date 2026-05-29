"""
Providerless Discovery Cascade.

Sprint F206AM: Providerless Discovery Mesh Phase 1
Sprint F206AP: Fusion Ranker — RRF + MMR + Source-Family Diversity

Fallback order (legacy sequential mode):
1. DuckDuckGo (primary, via duckduckgo_adapter)
2. Historical Frontier (DuckDB shadow_findings)
3. Wayback CDX (Internet Archive)

Fusion mode (when HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1):
  - Runs all 3 providers concurrently
  - Fuses results via fusion_ranker.fuse_discovery_hits
  - Enforces RRF ranking, diversity caps, dedup

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
# Env gate — re-checked on every call (not cached at import time)
# ---------------------------------------------------------------------------


def _is_providerless_enabled() -> bool:
    """Check if providerless discovery is enabled via env var (call-time check)."""
    return os.environ.get(
        "HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "0"
    ).strip().lower() in ("1", "true", "yes", "on")


def is_providerless_enabled() -> bool:
    """Public alias."""
    return _is_providerless_enabled()


# ---------------------------------------------------------------------------
# Cascade — fused concurrent mode
# ---------------------------------------------------------------------------


async def _search_all_providers(
    query: str,
    max_results: int,
    timeout_s: float,
) -> list[DiscoveryBatchResult]:
    """
    Run all three discovery providers concurrently.

    Returns list of DiscoveryBatchResult (one per provider), with empty hits
    for any provider that errored or timed out.
    """
    ddg_task = _run_ddg(query, max_results, timeout_s)
    hf_task = _run_historical_frontier(query, max_results, timeout_s)
    wb_task = _run_wayback_cdx(query, max_results, timeout_s)

    results = await asyncio.gather(ddg_task, hf_task, wb_task, return_exceptions=True)

    def coerce(result, name, default_chain, default_family):
        if isinstance(result, asyncio.TimeoutError):
            return DiscoveryBatchResult(
                hits=(),
                error=f"{name}_timeout",
                error_type="timeout",
                provider_name=name,
                provider_chain=default_chain,
                source_family=default_family,
            )
        if isinstance(result, Exception):
            return DiscoveryBatchResult(
                hits=(),
                error=f"{name}_error",
                error_type="provider_exception",
                provider_name=name,
                provider_chain=default_chain,
                source_family=default_family,
            )
        return result

    ddg_result = coerce(results[0], "duckduckgo", ("duckduckgo",), "search")
    hf_result = coerce(results[1], "historical_frontier", ("historical_frontier",), "historical")
    wb_result = coerce(results[2], "wayback_cdx", ("wayback_cdx",), "archive")

    return [ddg_result, hf_result, wb_result]


async def _run_ddg(query: str, max_results: int, timeout_s: float) -> DiscoveryBatchResult:
    """Run DuckDuckGo with its configured timeout."""
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
    timeout = min(timeout_s, 20.0)
    try:
        async with asyncio.timeout(timeout):
            return await async_search_public_web(query, max_results=max_results, timeout_s=timeout)
    except TimeoutError:
        return DiscoveryBatchResult(
            hits=(),
            error="timeout",
            error_type="timeout",
            provider_name="duckduckgo",
            provider_chain=("duckduckgo",),
            source_family="search",
            elapsed_s=timeout,
        )


async def _run_historical_frontier(query: str, max_results: int, timeout_s: float) -> DiscoveryBatchResult:
    """Run Historical Frontier with its configured timeout."""
    from hledac.universal.discovery.historical_frontier import async_search_historical_frontier
    timeout = min(timeout_s, 2.0)
    try:
        async with asyncio.timeout(timeout):
            return await async_search_historical_frontier(
                query, max_results=max_results, timeout_s=timeout
            )
    except TimeoutError:
        return DiscoveryBatchResult(
            hits=(),
            error="historical_frontier_timeout",
            error_type="timeout",
            provider_name="historical_frontier",
            provider_chain=("historical_frontier",),
            source_family="historical",
            elapsed_s=timeout,
        )


async def _run_wayback_cdx(query: str, max_results: int, timeout_s: float) -> DiscoveryBatchResult:
    """Run Wayback CDX with its configured timeout."""
    from hledac.universal.discovery.wayback_cdx_adapter import async_search_wayback_cdx
    timeout = min(timeout_s, 5.0)
    try:
        async with asyncio.timeout(timeout):
            return await async_search_wayback_cdx(
                query, max_results=max_results, timeout_s=timeout
            )
    except TimeoutError:
        return DiscoveryBatchResult(
            hits=(),
            error="wayback_cdx_timeout",
            error_type="timeout",
            provider_name="wayback_cdx",
            provider_chain=("wayback_cdx",),
            source_family="archive",
            elapsed_s=timeout,
        )


# ---------------------------------------------------------------------------
# DHT Discovery — Sprint F214Q / F229
# Tier-3 experimental: last-resort in sequential cascade
# ---------------------------------------------------------------------------

_DHT_SEQUENTIAL_TIMEOUT_S = 30.0


async def _run_dht(query: str, max_results: int, timeout_s: float) -> DiscoveryBatchResult:
    """Run DHT discovery as last-resort in sequential cascade.

    Gated by HLEDAC_ENABLE_DHT=1. Returns empty result if disabled or on error.
    Max 30s per DHT call to avoid blocking the cascade.
    """
    dht_timeout = min(timeout_s, _DHT_SEQUENTIAL_TIMEOUT_S)
    try:
        async with asyncio.timeout(dht_timeout):
            from .dht_adapter import async_search_dht

            return await async_search_dht(query, max_results=max_results, timeout_s=dht_timeout)
    except TimeoutError:
        return DiscoveryBatchResult(
            hits=(),
            error="dht_timeout",
            error_type="timeout",
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=dht_timeout,
        )
    except Exception as e:
        return DiscoveryBatchResult(
            hits=(),
            error=str(e),
            error_type="exception",
            provider_name="dht",
            provider_chain=("dht",),
            source_family="dht_discovery",
            elapsed_s=0.0,
        )


# ---------------------------------------------------------------------------
# Cascade — sequential fallback mode (when providerless is disabled)
# ---------------------------------------------------------------------------


async def _async_search_sequential(
    query: str,
    max_results: int = 10,
    timeout_s: float = 30.0,
) -> DiscoveryBatchResult:
    """
    Sequential first-hit-wins cascade: DDG → Historical Frontier → Wayback CDX.

    Used when HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=0 (default).
    """
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
    from hledac.universal.discovery.historical_frontier import async_search_historical_frontier
    from hledac.universal.discovery.wayback_cdx_adapter import async_search_wayback_cdx

    start = time.monotonic()

    try:
        async with asyncio.timeout(min(timeout_s, 20.0)):
            result = await async_search_public_web(query, max_results=max_results, timeout_s=timeout_s)
    except TimeoutError:
        result = DiscoveryBatchResult(
            hits=(),
            error="timeout",
            error_type="timeout",
            elapsed_s=min(timeout_s, 20.0),
        )

    elapsed = time.monotonic() - start

    if result.hits and not result.error:
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

    remaining_timeout = max(1.0, timeout_s - elapsed)
    try:
        async with asyncio.timeout(min(remaining_timeout, 2.0)):
            hf_result = await async_search_historical_frontier(
                query, max_results=max_results, timeout_s=2.0
            )
    except TimeoutError:
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
            error=hf_result.error,
            fallback_triggered="primary_backend_failed_fallback_succeeded",
            provider_name="historical_frontier",
            provider_chain=("duckduckgo", "historical_frontier"),
            source_family="historical",
            elapsed_s=elapsed,
            error_type=hf_result.error_type or "none",
        )

    remaining_timeout = max(1.0, timeout_s - elapsed)
    try:
        async with asyncio.timeout(min(remaining_timeout, 5.0)):
            wb_result = await async_search_wayback_cdx(
                query, max_results=max_results, timeout_s=5.0
            )
    except TimeoutError:
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
            error=wb_result.error,
            fallback_triggered="primary_backend_failed_fallback_succeeded",
            provider_name="wayback_cdx",
            provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
            source_family="archive",
            elapsed_s=elapsed,
            error_type=wb_result.error_type or "none",
        )

    # DHT last-resort — Sprint F214Q / F229
    remaining = max(1.0, timeout_s - (time.monotonic() - start))
    if remaining >= 5.0:
        dht_result = await _run_dht(query, max_results, remaining)
        if dht_result.hits:
            return dht_result

    return DiscoveryBatchResult(
        hits=(),
        error=result.error,
        fallback_triggered="primary_backend_failed_fallback_failed",
        provider_name=None,
        provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
        source_family=None,
        elapsed_s=time.monotonic() - start,
        error_type=result.error_type or "unknown_backend_error",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def async_search_providerless(
    query: str,
    max_results: int = 10,
    timeout_s: float = 30.0,
) -> DiscoveryBatchResult:
    """
    Providerless discovery cascade.

    When HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1:
        Runs all 3 providers concurrently and fuses results via RRF+MMR ranker.
    When HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=0 (default):
        Falls back to standard DDG via duckduckgo_adapter.

    Args:
        query:       Search query string.
        max_results: Max hits to return (default 10).
        timeout_s:   Total timeout for all layers (default 30s).

    Returns:
        DiscoveryBatchResult with hits and provider_chain metadata.
    """
    if not _is_providerless_enabled():
        return await _async_search_sequential(query, max_results=max_results, timeout_s=timeout_s)

    # Fusion mode: run all providers concurrently
    from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

    start = time.monotonic()
    results = await _search_all_providers(query, max_results, timeout_s)
    fused = fuse_discovery_hits(results, max_results=max_results)
    elapsed = time.monotonic() - start

    return DiscoveryBatchResult(
        hits=fused.hits,
        error=fused.error,
        fallback_triggered=None,
        provider_name="fusion",
        provider_chain=fused.provider_chain,
        source_family=fused.source_family,
        elapsed_s=elapsed,
        error_type=None,
    )
