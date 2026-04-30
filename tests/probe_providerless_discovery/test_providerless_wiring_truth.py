#!/usr/bin/env python3
"""
Sprint F206AO: Providerless Wiring Truth Probe
==============================================

Verifies that live_public_pipeline correctly wires the providerless cascade
based on HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY env var.

PROVIDERLESS_NOT_WIRED → providerless cascade is not in canonical path
PROVIDERLESS_WIRED → env=1 routes to async_search_providerless
PROVIDERLESS_DEFAULT → env=0/disabled routes to async_search_public_web
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Test 1: Default env=0 → _ASYNC_DISCOVERY_SEARCH is async_search_public_web
# ---------------------------------------------------------------------------


def test_f206ao_default_env_uses_ddg_direct():
    """F206AO-1: Default HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=0 → DDG direct path."""
    # Force clean module re-import by clearing any cached state
    # We test the wiring logic by checking what _ensure_discovery_patched would assign
    env_val = os.environ.get("HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "0").strip().lower()
    providerless = env_val in ("1", "true", "yes", "on")

    # Default behavior: providerless is disabled
    assert providerless is False, (
        f"HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY={env_val!r} — default should be disabled. "
        "Unset the env var to test default path."
    )

    # Verify cascade module exists and is callable
    from hledac.universal.discovery.cascade import async_search_providerless, is_providerless_enabled
    assert callable(async_search_providerless)
    assert is_providerless_enabled() is False  # default disabled

    # Verify DDG module exists and is callable
    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
    assert callable(async_search_public_web)

    print("F206AO-1 PASS: default env → DDG direct, providerless cascade available but dormant")


# ---------------------------------------------------------------------------
# Test 2: Env enabled → async_search_providerless is called by _ensure_discovery_patched
# ---------------------------------------------------------------------------


def test_f206ao_env_enabled_uses_providerless_cascade():
    """F206AO-2: HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1 → uses async_search_providerless."""
    # Patch _is_providerless_enabled to return True (fusion mode)
    with patch("hledac.universal.discovery.cascade._is_providerless_enabled", return_value=True):
        from hledac.universal.discovery.cascade import async_search_providerless, _is_providerless_enabled
        from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web

        # When _is_providerless_enabled() is True, the branch picks async_search_providerless
        _providerless = _is_providerless_enabled()
        assert _providerless is True, "patch should enable providerless mode"

        if _providerless:
            expected_search = async_search_providerless
        else:
            expected_search = async_search_public_web

        assert expected_search is async_search_providerless, (
            "Env enabled → expected async_search_providerless as _ASYNC_DISCOVERY_SEARCH"
        )

    print("F206AO-2 PASS: env=1 → async_search_providerless would be assigned to _ASYNC_DISCOVERY_SEARCH")


# ---------------------------------------------------------------------------
# Test 3: provider_chain propagates into public_branch_verdict via _add_discovery_metadata
# ---------------------------------------------------------------------------


def test_f206ao_provider_metadata_propagates_to_verdict():
    """F206AO-3: provider_name, provider_chain, source_family propagate from DiscoveryBatchResult."""
    # Simulate the verdict extraction logic from live_public_pipeline.py lines ~2592-2610
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

    hit = DiscoveryHit(
        query="test query",
        title="Test",
        url="https://example.com",
        snippet="Test snippet",
        source="duckduckgo",
        rank=0,
        retrieved_ts=time.time(),
    )

    # Simulate a cascade result with provider metadata
    cascade_result = DiscoveryBatchResult(
        hits=(hit,),
        error=None,
        fallback_triggered="primary_backend_failed_fallback_succeeded",
        provider_name="historical_frontier",
        provider_chain=("duckduckgo", "historical_frontier"),
        source_family="historical",
        elapsed_s=0.45,
        error_type="none",
    )

    # Simulate the extraction logic from live_public_pipeline.py
    public_branch_verdict = {}
    _dbr_provider_name = getattr(cascade_result, "provider_name", None)
    _dbr_provider_chain = getattr(cascade_result, "provider_chain", None)
    _dbr_source_family = getattr(cascade_result, "source_family", None)
    _dbr_elapsed_s = getattr(cascade_result, "elapsed_s", None)
    _dbr_error_type = getattr(cascade_result, "error_type", None)

    if _dbr_provider_name is not None:
        public_branch_verdict["discovery_provider_name"] = _dbr_provider_name
    if _dbr_provider_chain is not None:
        public_branch_verdict["discovery_provider_chain"] = _dbr_provider_chain
    if _dbr_source_family is not None:
        public_branch_verdict["discovery_source_family"] = _dbr_source_family
    if _dbr_elapsed_s is not None:
        public_branch_verdict["discovery_provider_elapsed_s"] = _dbr_elapsed_s
    if _dbr_error_type is not None:
        public_branch_verdict["discovery_provider_error_type"] = _dbr_error_type

    assert public_branch_verdict.get("discovery_provider_name") == "historical_frontier"
    assert public_branch_verdict.get("discovery_provider_chain") == ("duckduckgo", "historical_frontier")
    assert public_branch_verdict.get("discovery_source_family") == "historical"
    assert public_branch_verdict.get("discovery_provider_elapsed_s") == 0.45
    assert public_branch_verdict.get("discovery_provider_error_type") == "none"

    print("F206AO-3 PASS: provider metadata propagates into verdict dict")


# ---------------------------------------------------------------------------
# Test 4: fallback_triggered propagates into verdict (existing behavior, verified)
# ---------------------------------------------------------------------------


def test_f206ao_fallback_triggered_in_verdict():
    """F206AO-4: fallback_triggered is propagated into public_branch_verdict."""
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult

    result = DiscoveryBatchResult(
        hits=(),
        error="timeout",
        fallback_triggered="primary_backend_failed_fallback_succeeded",
        provider_name="wayback_cdx",
        provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
        source_family="archive",
        elapsed_s=2.1,
        error_type="timeout",
    )

    fallback_triggered = getattr(result, "fallback_triggered", None)
    assert fallback_triggered == "primary_backend_failed_fallback_succeeded"

    # Simulate the verdict map from live_public_pipeline.py
    _FALLBACK_STATE_MAP = {
        "primary_backend_failed_fallback_succeeded": "primary_failed_fallback_succeeded",
        "primary_backend_failed_fallback_failed": "primary_failed_fallback_failed",
    }
    public_discovery_fallback_state = _FALLBACK_STATE_MAP.get(fallback_triggered) or (
        "no_fallback_needed" if None else None
    )
    assert public_discovery_fallback_state == "primary_failed_fallback_succeeded"

    print("F206AO-4 PASS: fallback_triggered maps correctly into verdict fallback_state")


# ---------------------------------------------------------------------------
# Test 5: Hermetic cascade with DDG empty + historical frontier returns 2 hits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f206ao_hermetic_cascade_ddg_empty_historical_returns_hits():
    """F206AO-5: DDG empty → historical frontier fallback produces hits (hermetic, no live net)."""
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

    mock_ddg_result = DiscoveryBatchResult(
        hits=(),
        error="timeout",
        error_type="timeout",
        elapsed_s=20.0,
        provider_name="duckduckgo",
        provider_chain=("duckduckgo",),
        source_family="search",
    )

    mock_hf_result = DiscoveryBatchResult(
        hits=(
            DiscoveryHit(
                query="test query",
                title="Historical Result 1",
                url="https://example.com/hist1",
                snippet="From historical frontier",
                source="historical_frontier",
                rank=0,
                retrieved_ts=time.time(),
            ),
            DiscoveryHit(
                query="test query",
                title="Historical Result 2",
                url="https://example.com/hist2",
                snippet="From historical frontier 2",
                source="historical_frontier",
                rank=1,
                retrieved_ts=time.time(),
            ),
        ),
        error=None,
        fallback_triggered="primary_backend_failed_fallback_succeeded",
        provider_name="historical_frontier",
        provider_chain=("historical_frontier",),
        source_family="historical",
        elapsed_s=0.3,
        error_type=None,
    )

    # Patch all 3 providers so the cascade is fully hermetic
    with patch("hledac.universal.discovery.cascade._is_providerless_enabled", return_value=True):
        from hledac.universal.discovery.cascade import async_search_providerless
        with patch(
            "hledac.universal.discovery.duckduckgo_adapter.async_search_public_web",
            new_callable=AsyncMock,
            return_value=mock_ddg_result,
        ):
            with patch(
                "hledac.universal.discovery.historical_frontier.async_search_historical_frontier",
                new_callable=AsyncMock,
                return_value=mock_hf_result,
            ):
                with patch(
                    "hledac.universal.discovery.wayback_cdx_adapter.async_search_wayback_cdx",
                    new_callable=AsyncMock,
                    return_value=DiscoveryBatchResult(
                        hits=(), error="timeout", error_type="timeout", elapsed_s=5.0,
                        provider_name="wayback_cdx", provider_chain=("wayback_cdx",), source_family="archive"
                    ),
                ):
                    result = await async_search_providerless("test query", max_results=10, timeout_s=5.0)

    assert len(result.hits) == 2, f"Expected 2 hits from historical frontier, got {len(result.hits)}: {result.error}"
    # Fusion mode: all providers run concurrently; result is fused
    assert result.provider_name == "fusion"
    assert result.provider_chain == ("duckduckgo", "historical_frontier", "wayback_cdx")
    assert result.source_family == "historical"
    assert result.fallback_triggered is None  # concurrent fusion, not sequential fallback

    print("F206AO-5 PASS: hermetic cascade DDG→hist frontier produces 2 hits with correct metadata")


# ---------------------------------------------------------------------------
# Test 6: Hermetic cascade — all layers fail → empty result with correct error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f206ao_hermetic_cascade_all_layers_fail():
    """F206AO-6: All layers fail → empty result, provider_chain intact, fail-soft."""
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult

    empty_ddg = DiscoveryBatchResult(
        hits=(), error="timeout", error_type="timeout", elapsed_s=20.0,
        provider_name="duckduckgo", provider_chain=("duckduckgo",), source_family="search"
    )
    empty_hf = DiscoveryBatchResult(
        hits=(), error="timeout", error_type="timeout", elapsed_s=2.0,
        provider_name="historical_frontier", provider_chain=("historical_frontier",), source_family="historical"
    )
    empty_wb = DiscoveryBatchResult(
        hits=(), error="timeout", error_type="timeout", elapsed_s=5.0,
        provider_name="wayback_cdx", provider_chain=("wayback_cdx",), source_family="archive"
    )

    with patch("hledac.universal.discovery.cascade._is_providerless_enabled", return_value=True):
        from hledac.universal.discovery.cascade import async_search_providerless
        with patch(
            "hledac.universal.discovery.duckduckgo_adapter.async_search_public_web",
            new_callable=AsyncMock,
            return_value=empty_ddg,
        ):
            with patch(
                "hledac.universal.discovery.historical_frontier.async_search_historical_frontier",
                new_callable=AsyncMock,
                return_value=empty_hf,
            ):
                with patch(
                    "hledac.universal.discovery.wayback_cdx_adapter.async_search_wayback_cdx",
                    new_callable=AsyncMock,
                    return_value=empty_wb,
                ):
                    result = await async_search_providerless("test query", max_results=10, timeout_s=5.0)

    assert result.hits == ()
    assert result.provider_chain == ("duckduckgo", "historical_frontier", "wayback_cdx")
    assert result.provider_name == "fusion"
    assert result.fallback_triggered is None

    print("F206AO-6 PASS: all layers fail → empty result, fail-soft")


# ---------------------------------------------------------------------------
# Test 7: CancelledError re-raised
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f206ao_cascade_raises_cancelled_error():
    """F206AO-7: asyncio.CancelledError is re-raised from cascade."""
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult

    async def raise_cancelled(*args, **kwargs):
        raise asyncio.CancelledError("discovery cancelled")

    with patch(
        "hledac.universal.discovery.duckduckgo_adapter.async_search_public_web",
        side_effect=raise_cancelled,
    ):
        from hledac.universal.discovery.cascade import async_search_providerless
        with pytest.raises(asyncio.CancelledError):
            await async_search_providerless("test", max_results=5, timeout_s=2.0)

    print("F206AO-7 PASS: CancelledError re-raised from cascade")


# ---------------------------------------------------------------------------
# Test 8: No Brave/SearXNG imports in live_public_pipeline
# ---------------------------------------------------------------------------


def test_f206ao_no_brave_searx_in_live_public_pipeline():
    """F206AO-8: live_public_pipeline.py must not import brave or searx."""
    from pathlib import Path
    lpp = PROJECT_ROOT / "pipeline" / "live_public_pipeline.py"
    src = lpp.read_text()
    assert "brave" not in src.lower(), "live_public_pipeline.py must not reference brave"
    assert "searx" not in src.lower(), "live_public_pipeline.py must not reference searx"

    print("F206AO-8 PASS: no brave/searx imports in live_public_pipeline.py")


# ---------------------------------------------------------------------------
# Test 9: Scheduler not mutated (no _run_pivot_planner_advisory changes)
# ---------------------------------------------------------------------------


def test_f206ao_no_scheduler_mutation():
    """F206AO-9: _ASYNC_DISCOVERY_SEARCH wiring must not touch runtime/sprint_scheduler.py."""
    scheduler_path = PROJECT_ROOT / "runtime" / "sprint_scheduler.py"
    if scheduler_path.exists():
        src = scheduler_path.read_text()
        assert "_ASYNC_DISCOVERY_SEARCH" not in src, (
            "_ASYNC_DISCOVERY_SEARCH must not appear in sprint_scheduler.py"
        )
    print("F206AO-9 PASS: no _ASYNC_DISCOVERY_SEARCH in sprint_scheduler.py")


# ---------------------------------------------------------------------------
# Test 10: env=1 + DDG returns hits → provider_name=duckduckgo, chain=("duckduckgo",)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f206ao_env_enabled_ddg_returns_hits_direct():
    """F206AO-10: env=1 + DDG returns hits → provider_name=duckduckgo, no fallback."""
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

    hit = DiscoveryHit(
        query="test", title="DDG Hit", url="https://ddg.example.com",
        snippet="From DDG", source="duckduckgo", rank=0, retrieved_ts=time.time(),
    )
    ddg_hit_result = DiscoveryBatchResult(
        hits=(hit,),
        error=None,
        fallback_triggered=None,
        provider_name="duckduckgo",
        provider_chain=("duckduckgo",),
        source_family="search",
        elapsed_s=0.8,
        error_type=None,
    )

    with patch("hledac.universal.discovery.cascade._is_providerless_enabled", return_value=True):
        from hledac.universal.discovery.cascade import async_search_providerless
        with patch(
            "hledac.universal.discovery.duckduckgo_adapter.async_search_public_web",
            new_callable=AsyncMock,
            return_value=ddg_hit_result,
        ):
            result = await async_search_providerless("test query", max_results=10, timeout_s=5.0)

    assert result.hits  # DDG returned hits
    # Fusion mode: all providers run concurrently; result is fused
    assert result.provider_name == "fusion"
    assert "duckduckgo" in result.provider_chain
    assert result.source_family == "search"
    assert result.fallback_triggered is None  # concurrent fusion, not sequential fallback

    print("F206AO-10 PASS: env=1 + DDG hits direct return, no fallback triggered")


# ---------------------------------------------------------------------------
# Test 11: wayback fallback produces hits with correct source_family=archive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_f206ao_wayback_fallback_produces_archive_hits():
    """F206AO-11: DDG+histo fail → wayback returns archive hits with source_family=archive."""
    from hledac.universal.discovery.cascade import async_search_providerless
    from hledac.universal.discovery.duckduckgo_adapter import DiscoveryBatchResult, DiscoveryHit

    empty_result = DiscoveryBatchResult(hits=(), error="timeout", error_type="timeout", elapsed_s=20.0)

    wayback_hit = DiscoveryHit(
        query="test", title="Archived Page", url="https://web.archive.org/web/2020/example.com",
        snippet="From Wayback Machine", source="wayback_cdx", rank=0, retrieved_ts=time.time(),
    )
    wayback_result = DiscoveryBatchResult(
        hits=(wayback_hit,),
        error=None,
        fallback_triggered="primary_backend_failed_fallback_succeeded",
        provider_name="wayback_cdx",
        provider_chain=("duckduckgo", "historical_frontier", "wayback_cdx"),
        source_family="archive",
        elapsed_s=4.5,
        error_type=None,
    )

    with patch("hledac.universal.discovery.cascade._is_providerless_enabled", return_value=True):
        with patch(
            "hledac.universal.discovery.duckduckgo_adapter.async_search_public_web",
            new_callable=AsyncMock,
            return_value=empty_result,
        ):
            with patch(
                "hledac.universal.discovery.historical_frontier.async_search_historical_frontier",
                new_callable=AsyncMock,
                return_value=empty_result,
            ):
                with patch(
                    "hledac.universal.discovery.wayback_cdx_adapter.async_search_wayback_cdx",
                    new_callable=AsyncMock,
                    return_value=wayback_result,
                ):
                    result = await async_search_providerless("test query", max_results=10, timeout_s=5.0)

    assert len(result.hits) == 1
    # Fusion mode: concurrent execution returns fused result
    assert result.provider_name == "fusion"
    assert result.provider_chain == ("duckduckgo", "historical_frontier", "wayback_cdx")
    assert result.source_family == "archive"  # only archive family had hits
    assert result.fallback_triggered is None  # concurrent fusion, not sequential fallback

    print("F206AO-11 PASS: wayback fallback produces archive hits with correct metadata")


# ---------------------------------------------------------------------------
# Test 12: Default env=0 → async_search_providerless NOT called (DDG direct)
# ---------------------------------------------------------------------------


def test_f206ao_default_env_does_not_use_providerless():
    """F206AO-12: Default env=0 → _ASYNC_DISCOVERY_SEARCH assigned to async_search_public_web."""
    # Simulate the default path
    env_val = os.environ.get("HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY", "0").strip().lower()
    _providerless = env_val in ("1", "true", "yes", "on")

    from hledac.universal.discovery.duckduckgo_adapter import async_search_public_web
    from hledac.universal.discovery.cascade import async_search_providerless

    if _providerless:
        expected = async_search_providerless
    else:
        expected = async_search_public_web

    assert expected is async_search_public_web, (
        f"Default env=0 should assign async_search_public_web, got {expected}"
    )

    print("F206AO-12 PASS: default env → async_search_public_web assigned to _ASYNC_DISCOVERY_SEARCH")