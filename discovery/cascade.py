"""
Providerless Discovery Cascade.

Sprint F206AM: Providerless Discovery Mesh Phase 1
Sprint F206AP: Fusion Ranker — RRF + MMR + Source-Family Diversity

Fallback order (legacy sequential mode):
1. DuckDuckGo (primary, via duckduckgo_adapter)
2. Historical Frontier (DuckDB shadow_findings)
3. Wayback CDX (Internet Archive)
4. Wayback Sitemap (Internet Archive Sitemaps — Sprint P2-2)

Fusion mode (when HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1):
  - Runs all 3+ providers concurrently
  - Fuses results via fusion_ranker.fuse_discovery_hits
  - Enforces RRF ranking, diversity caps, dedup

Env gate: HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1 (default enabled, F350M-R D-9)
"""

import asyncio
import os
import time

from hledac.universal.discovery.base import DiscoveryBatchResult
from hledac.universal.utils.asyncx import parallel_ok


def _is_providerless_enabled() -> bool:
    """Check if providerless discovery is enabled via env var (call-time check).

    D-9: Default changed from 0 to 1 — fusion parallel mode is now always-on.
    OSINT collectors (DDG + Historical + Wayback) run concurrently via TaskGroup
    with RRF+MMR fusion ranker. Sequential fallback preserved for explicit
    HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=0.

    OPTIMIZATION #2 NOTE: fuse_always improves recall but adds ~100-200ms latency
    from parallel execution. For latency-critical use cases, set CASCADE_FUSION_MODE=fuse_on_empty.
    Monitor production metrics: if p95 latency > 500ms, consider reducing to fuse_on_empty.
    """
    return os.environ.get("HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "1").strip().lower() in ("1", "true", "yes", "on")


def is_providerless_enabled() -> bool:
    """Public alias."""
    return _is_providerless_enabled()


_CASCADE_FUSION_MODE_VALUES = ("first_wins", "fuse_always", "fuse_on_empty")


def _get_fusion_mode() -> str:
    """Return current CASCADE_FUSION_MODE (always-on, call-time check).

    D-9: Default changed from first_wins to fuse_always — all 3 concurrent
    providers (DDG + HF + WB) are always fused via RRF+MMR ranker regardless
    of whether primary returns hits. Best recall at minimal latency cost.
    """
    return os.environ.get("CASCADE_FUSION_MODE", "fuse_always").strip().lower()


async def _search_all_providers(query: str, max_results: int, timeout_s: float) -> list[DiscoveryBatchResult]:
    """
    Run all three discovery providers concurrently.

    Returns list of DiscoveryBatchResult (one per provider), with empty hits
    for any provider that errored or timed out.
    """
    ddg_task = _run_ddg(query, max_results, timeout_s)
    hf_task = _run_historical_frontier(query, max_results, timeout_s)
    wb_task = _run_wayback_cdx(query, max_results, timeout_s)
    results = await parallel_ok(ddg_task, hf_task, wb_task, label="cascade:67")

    def coerce(result, name, default_chain, default_family):
        if isinstance(result, asyncio.TimeoutError):
            return DiscoveryBatchResult(
                hits=(),
                error=f"{name}_timeout",
                error_type="timeout",
                provider_name=name,
                provider_chain=default_chain,
                source_family=default_family,
                provider_status_debug=[
                    {"provider": name, "state": "production", "selected": False, "reason": "cascade_timeout"}
                ],
            )
        if isinstance(result, BaseException):
            return DiscoveryBatchResult(
                hits=(),
                error=f"{name}_error",
                error_type="provider_exception",
                provider_name=name,
                provider_chain=default_chain,
                source_family=default_family,
                provider_status_debug=[
                    {"provider": name, "state": "production", "selected": False, "reason": "cascade_exception"}
                ],
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
            provider_status_debug=[
                {"provider": "duckduckgo", "state": "production", "selected": False, "reason": "ddg_timeout"}
            ],
        )


async def _run_historical_frontier(query: str, max_results: int, timeout_s: float) -> DiscoveryBatchResult:
    """Run Historical Frontier with its configured timeout."""
    from hledac.universal.discovery.historical_frontier import async_search_historical_frontier

    timeout = min(timeout_s, 2.0)
    try:
        async with asyncio.timeout(timeout):
            return await async_search_historical_frontier(query, max_results=max_results, timeout_s=timeout)
    except TimeoutError:
        return DiscoveryBatchResult(
            hits=(),
            error="historical_frontier_timeout",
            error_type="timeout",
            provider_name="historical_frontier",
            provider_chain=("historical_frontier",),
            source_family="historical",
            elapsed_s=timeout,
            provider_status_debug=[
                {"provider": "historical_frontier", "state": "production", "selected": False, "reason": "hf_timeout"}
            ],
        )


async def _run_wayback_cdx(query: str, max_results: int, timeout_s: float) -> DiscoveryBatchResult:
    """Run Wayback CDX with its configured timeout."""
    from hledac.universal.discovery.wayback_cdx_adapter import async_search_wayback_cdx

    timeout = min(timeout_s, 5.0)
    try:
        async with asyncio.timeout(timeout):
            return await async_search_wayback_cdx(query, max_results=max_results, timeout_s=timeout)
    except TimeoutError:
        return DiscoveryBatchResult(
            hits=(),
            error="wayback_cdx_timeout",
            error_type="timeout",
            provider_name="wayback_cdx",
            provider_chain=("wayback_cdx",),
            source_family="archive",
            elapsed_s=timeout,
            provider_status_debug=[
                {"provider": "wayback_cdx", "state": "production", "selected": False, "reason": "wb_timeout"}
            ],
        )


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


async def _async_search_sequential(query: str, max_results: int = 10, timeout_s: float = 30.0) -> DiscoveryBatchResult:
    """
    Concurrent limited-fallback cascade: DDG + Historical Frontier + Wayback CDX
    run in parallel, results merged in priority order.

    Uses priority allocation: DDG gets the most time (20s), HF 5s, WB 5s,
    all running concurrently so the first provider to return hits wins
    without waiting for sequential timeouts.

    Used when HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=0 (default).
    """
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
    from hledac.universal.discovery.historical_frontier import async_search_historical_frontier
    from hledac.universal.discovery.wayback_cdx_adapter import async_search_wayback_cdx

    start = time.monotonic()
    async with asyncio.TaskGroup() as _tg:
        _ddg_task = _tg.create_task(
            async_search_public_web(query, max_results=max_results, timeout_s=min(timeout_s, 20.0)), name="cascade:ddg"
        )
        _hf_task = _tg.create_task(
            async_search_historical_frontier(query, max_results=max_results, timeout_s=5.0), name="cascade:hf"
        )
        _wb_task = _tg.create_task(
            async_search_wayback_cdx(query, max_results=max_results, timeout_s=5.0), name="cascade:wb"
        )
    ddg_raw: DiscoveryBatchResult | BaseException = _ddg_task.result()
    hf_raw: DiscoveryBatchResult | BaseException = _hf_task.result()
    wb_raw: DiscoveryBatchResult | BaseException = _wb_task.result()
    elapsed = time.monotonic() - start
    results: list[DiscoveryBatchResult | BaseException] = [ddg_raw, hf_raw, wb_raw]

    def _coerce(
        result: DiscoveryBatchResult | BaseException, name: str, chain: tuple[str, ...], family: str
    ) -> DiscoveryBatchResult:
        if isinstance(result, asyncio.TimeoutError):
            return DiscoveryBatchResult(
                hits=(),
                error=f"{name}_timeout",
                error_type="timeout",
                provider_name=name,
                provider_chain=chain,
                source_family=family,
                elapsed_s=elapsed,
                provider_status_debug=[
                    {"provider": name, "state": "production", "selected": False, "reason": f"{name}_timeout"}
                ],
            )
        if isinstance(result, BaseException):
            return DiscoveryBatchResult(
                hits=(),
                error=f"{name}_error",
                error_type="provider_exception",
                provider_name=name,
                provider_chain=chain,
                source_family=family,
                elapsed_s=elapsed,
                provider_status_debug=[
                    {"provider": name, "state": "production", "selected": False, "reason": f"{name}_exception"}
                ],
            )
        return result

    ddg_r = _coerce(results[0], "duckduckgo", ("duckduckgo",), "search")
    hf_r = _coerce(results[1], "historical_frontier", ("historical_frontier",), "historical")
    wb_r = _coerce(results[2], "wayback_cdx", ("wayback_cdx",), "archive")
    fusion_mode = _get_fusion_mode()
    if fusion_mode == "fuse_always" or (fusion_mode == "fuse_on_empty" and (not ddg_r.hits or ddg_r.error)):
        from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

        provider_results = [ddg_r, hf_r, wb_r]
        fused = fuse_discovery_hits(provider_results, max_results=max_results)
        if fused.hits:
            return DiscoveryBatchResult(
                hits=fused.hits,
                error=fused.error,
                fallback_triggered=None,
                provider_name="fusion",
                provider_chain=fused.provider_chain,
                source_family=fused.source_family,
                elapsed_s=elapsed,
                error_type=None,
                provider_status_debug=getattr(fused, "provider_status_debug", None),
            )
    if ddg_r.hits and (not ddg_r.error):
        return DiscoveryBatchResult(
            hits=ddg_r.hits,
            error=ddg_r.error,
            fallback_triggered=None,
            provider_name="duckduckgo",
            provider_chain=("duckduckgo",),
            source_family="search",
            elapsed_s=elapsed,
            error_type=None,
        )
    if hf_r.hits:
        return DiscoveryBatchResult(
            hits=hf_r.hits,
            error=hf_r.error,
            fallback_triggered="primary_backend_failed_fallback_succeeded",
            provider_name="historical_frontier",
            provider_chain=("duckduckgo", "historical_frontier"),
            source_family="historical",
            elapsed_s=elapsed,
            error_type=hf_r.error_type or "none",
        )
    if wb_r.hits:
        return DiscoveryBatchResult(
            hits=wb_r.hits,
            error=wb_r.error,
            fallback_triggered="primary_backend_failed_fallback_succeeded",
            provider_name="wayback_cdx",
            provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
            source_family="archive",
            elapsed_s=elapsed,
            error_type=wb_r.error_type or "none",
        )
    remaining = max(1.0, timeout_s - elapsed)
    if remaining >= 5.0:
        dht_result = await _run_dht(query, max_results, remaining)
        if dht_result.hits:
            return dht_result
    return DiscoveryBatchResult(
        hits=(),
        error=ddg_r.error or "all_providers_returned_empty",
        fallback_triggered="primary_backend_failed_fallback_failed",
        provider_name=None,
        provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
        source_family=None,
        elapsed_s=elapsed,
        error_type=ddg_r.error_type or "unknown_backend_error",
    )


async def async_search_providerless(query: str, max_results: int = 10, timeout_s: float = 30.0) -> DiscoveryBatchResult:
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
        provider_status_debug=getattr(fused, "provider_status_debug", None),
    )
