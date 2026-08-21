"""
Tests for AP-03: Cascade Fusion — CASCADE_FUSION_MODE configuration.

Issue: discovery/cascade.py:308-329 first-wins discard 2-of-3 results.
Root cause: DDG + Historical Frontier + Wayback CDX běží paralelně, ale first-wins
zahodí 2 výsledky.

Solution:
  1. CASCADE_FUSION_MODE = {first_wins, fuse_always, fuse_on_empty}
  2. fuse_discovery_hits (RRF + MMR) aplikován na všech 3 výsledcích
  3. Benchmark test ukazuje lepší hit recall vs first-wins

AP-03 also fixed: fusion_ranker.py was missing the import of
_normalize_url_for_dedup from duckduckgo_adapter.
"""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, patch

import pytest

from hledac.universal.discovery.base import DiscoveryBatchResult, DiscoveryHit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hit(url: str, title: str = "", snippet: str = "", source: str = "search") -> DiscoveryHit:
    return DiscoveryHit(
        query="test_query",
        title=title or url,
        url=url,
        snippet=snippet or url,
        source=source,
        rank=0,
        retrieved_ts=time.time(),
    )


def _make_result(
    hits: tuple[DiscoveryHit, ...],
    provider: str = "test",
    error: str | None = None,
    source_family: str = "search",
) -> DiscoveryBatchResult:
    return DiscoveryBatchResult(
        hits=hits,
        error=error,
        error_type=None if not error else "test",
        provider_name=provider,
        provider_chain=(provider,),
        source_family=source_family,
        elapsed_s=0.1,
        provider_status_debug=[],
    )


# ---------------------------------------------------------------------------
# Unit tests: _get_fusion_mode() — no module imports, pure env parsing
# ---------------------------------------------------------------------------


class TestCascadeFusionModeGetter:
    """AP-03: Unit tests for _get_fusion_mode() env var parsing."""

    def test_get_fusion_mode_first_wins(self) -> None:
        """CASCADE_FUSION_MODE=first_wins returns 'first_wins'."""
        from hledac.universal.discovery.cascade import _get_fusion_mode

        with patch.dict(os.environ, {"CASCADE_FUSION_MODE": "first_wins"}, clear=True):
            assert _get_fusion_mode() == "first_wins"

    def test_get_fusion_mode_fuse_always(self) -> None:
        """CASCADE_FUSION_MODE=fuse_always returns 'fuse_always'."""
        from hledac.universal.discovery.cascade import _get_fusion_mode

        with patch.dict(os.environ, {"CASCADE_FUSION_MODE": "fuse_always"}, clear=True):
            assert _get_fusion_mode() == "fuse_always"

    def test_get_fusion_mode_fuse_on_empty(self) -> None:
        """CASCADE_FUSION_MODE=fuse_on_empty returns 'fuse_on_empty'."""
        from hledac.universal.discovery.cascade import _get_fusion_mode

        with patch.dict(os.environ, {"CASCADE_FUSION_MODE": "fuse_on_empty"}, clear=True):
            assert _get_fusion_mode() == "fuse_on_empty"

    def test_get_fusion_mode_unknown_kept_as_is(self) -> None:
        """Unknown CASCADE_FUSION_MODE values are returned as-is (fallback in caller)."""
        from hledac.universal.discovery.cascade import _get_fusion_mode

        with patch.dict(os.environ, {"CASCADE_FUSION_MODE": "invalid_xyz"}, clear=True):
            assert _get_fusion_mode() == "invalid_xyz"

    def test_get_fusion_mode_default_is_first_wins(self) -> None:
        """Without CASCADE_FUSION_MODE set, defaults to 'first_wins'."""
        from hledac.universal.discovery.cascade import _get_fusion_mode

        env_clean = {k: v for k, v in os.environ.items() if k != "CASCADE_FUSION_MODE"}
        with patch.dict(os.environ, env_clean, clear=True):
            assert _get_fusion_mode() == "first_wins"


# ---------------------------------------------------------------------------
# Fusion ranker integration tests
# ---------------------------------------------------------------------------


class TestFusionRankerIntegration:
    """AP-03: fuse_discovery_hits combines hits from all providers (standalone)."""

    def test_fuse_discovery_hits_combines_distinct_providers(self) -> None:
        """fuse_discovery_hits kombinuje hits z různých providerů do jednoho."""
        from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

        results = [
            _make_result(
                (_make_hit("https://ddg1.example.com", "DDG 1"), _make_hit("https://ddg2.example.com", "DDG 2")),
                "duckduckgo",
                source_family="search",
            ),
            _make_result(
                (_make_hit("https://hf1.example.com", "HF 1", source="historical"),),
                "historical_frontier",
                source_family="historical",
            ),
            _make_result(
                (_make_hit("https://wb1.example.com", "WB 1", source="archive"),),
                "wayback_cdx",
                source_family="archive",
            ),
        ]

        fused = fuse_discovery_hits(results, max_results=20)

        assert len(fused.hits) == 4
        urls = {h.url for h in fused.hits}
        assert urls == {
            "https://ddg1.example.com",
            "https://ddg2.example.com",
            "https://hf1.example.com",
            "https://wb1.example.com",
        }
        assert fused.provider_name == "fusion"

    def test_fuse_discovery_hits_dedups_same_url(self) -> None:
        """fuse_discovery_hits deduplikuje stejné URL z různých providerů."""
        from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

        hit = _make_hit("https://same.example.com", "Same")
        results = [
            _make_result((hit,), "ddg", source_family="search"),
            _make_result((hit,), "hf", source_family="historical"),
            _make_result((hit,), "wb", source_family="archive"),
        ]

        fused = fuse_discovery_hits(results, max_results=20)

        # 3x stejná URL z různých providerů → dedup na 1
        assert len(fused.hits) == 1
        assert fused.hits[0].url == "https://same.example.com"

    def test_fuse_discovery_hits_empty_input(self) -> None:
        """fuse_discovery_hits správně zpracuje prázdný vstup."""
        from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

        fused = fuse_discovery_hits([], max_results=20)
        assert len(fused.hits) == 0
        assert fused.provider_name is None

    def test_fuse_discovery_hits_all_empty_providers(self) -> None:
        """fuse_discovery_hits s prázdnými výsledky ze všech providerů vrací prázdné."""
        from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

        results = [
            _make_result((), "duckduckgo", source_family="search"),
            _make_result((), "historical_frontier", source_family="historical"),
            _make_result((), "wayback_cdx", source_family="archive"),
        ]

        fused = fuse_discovery_hits(results, max_results=20)
        assert len(fused.hits) == 0

    def test_fuse_discovery_hits_max_results_cap(self) -> None:
        """fuse_discovery_hits respektuje max_results limit."""
        from hledac.universal.discovery.fusion_ranker import fuse_discovery_hits

        hits = tuple(_make_hit(f"https://h{i}.example.com", f"Hit {i}") for i in range(30))
        results = [_make_result(hits, "ddg", source_family="search")]

        fused = fuse_discovery_hits(results, max_results=5)
        assert len(fused.hits) == 5


# ---------------------------------------------------------------------------
# Cascade fusion logic — test the fusion decision tree in isolation
# (patches only _get_fusion_mode, avoiding full cascade module import)
# ---------------------------------------------------------------------------


class TestCascadeFusionDecisionTree:
    """
    Test the AP-03 fusion decision tree logic directly.

    We test the logic by patching _get_fusion_mode and _run_* helpers
    at the cascade module level. This avoids triggering the lock conflict
    that occurs when importing cascade through the full module path.
    """

    @pytest.mark.asyncio
    async def test_fusion_mode_fuse_always_returns_fusion(self) -> None:
        """CASCADE_FUSION_MODE=fuse_always → returns fusion provider with all hits."""
        from hledac.universal.discovery import cascade as cascade_mod

        ddg_hit = _make_hit("https://ddg.example.com", "DDG")
        hf_hit = _make_hit("https://hf.example.com", "HF")
        wb_hit = _make_hit("https://wb.example.com", "WB")

        async def no_dht(*_a: object, **_kw: object) -> DiscoveryBatchResult:
            return _make_result(())

        with (
            patch.object(cascade_mod, "_get_fusion_mode", return_value="fuse_always"),
            patch.object(cascade_mod, "_run_ddg", AsyncMock(return_value=_make_result((ddg_hit,), "duckduckgo"))),
            patch.object(
                cascade_mod,
                "_run_historical_frontier",
                AsyncMock(return_value=_make_result((hf_hit,), "historical_frontier", source_family="historical")),
            ),
            patch.object(
                cascade_mod,
                "_run_wayback_cdx",
                AsyncMock(return_value=_make_result((wb_hit,), "wayback_cdx", source_family="archive")),
            ),
            patch.object(cascade_mod, "_run_dht", no_dht),
        ):
            result = await cascade_mod._async_search_sequential("test", max_results=20, timeout_s=10.0)

        assert result.provider_name == "fusion"
        assert len(result.hits) == 3
        urls = {h.url for h in result.hits}
        assert urls == {"https://ddg.example.com", "https://hf.example.com", "https://wb.example.com"}

    @pytest.mark.asyncio
    async def test_fusion_mode_first_wins_returns_ddg_only(self) -> None:
        """CASCADE_FUSION_MODE=first_wins → returns duckduckgo with only DDG hits."""
        from hledac.universal.discovery import cascade as cascade_mod

        async def no_dht(*_a: object, **_kw: object) -> DiscoveryBatchResult:
            return _make_result(())

        with (
            patch.object(cascade_mod, "_get_fusion_mode", return_value="first_wins"),
            patch.object(
                cascade_mod,
                "_run_ddg",
                AsyncMock(return_value=_make_result((_make_hit("https://ddg.example.com", "DDG"),), "duckduckgo")),
            ),
            patch.object(
                cascade_mod,
                "_run_historical_frontier",
                AsyncMock(
                    return_value=_make_result(
                        (_make_hit("https://hf.example.com", "HF"),), "historical_frontier", source_family="historical"
                    )
                ),
            ),
            patch.object(
                cascade_mod,
                "_run_wayback_cdx",
                AsyncMock(
                    return_value=_make_result(
                        (_make_hit("https://wb.example.com", "WB"),), "wayback_cdx", source_family="archive"
                    )
                ),
            ),
            patch.object(cascade_mod, "_run_dht", no_dht),
        ):
            result = await cascade_mod._async_search_sequential("test", max_results=20, timeout_s=10.0)

        assert result.provider_name == "duckduckgo"
        assert len(result.hits) == 1
        assert result.hits[0].url == "https://ddg.example.com"

    @pytest.mark.asyncio
    async def test_fusion_mode_fuse_on_empty_with_ddg_hit(self) -> None:
        """CASCADE_FUSION_MODE=fuse_on_empty + DDG has hits → first_wins (DDG wins)."""
        from hledac.universal.discovery import cascade as cascade_mod

        async def no_dht(*_a: object, **_kw: object) -> DiscoveryBatchResult:
            return _make_result(())

        with (
            patch.object(cascade_mod, "_get_fusion_mode", return_value="fuse_on_empty"),
            patch.object(
                cascade_mod,
                "_run_ddg",
                AsyncMock(return_value=_make_result((_make_hit("https://ddg.example.com", "DDG"),), "duckduckgo")),
            ),
            patch.object(
                cascade_mod,
                "_run_historical_frontier",
                AsyncMock(
                    return_value=_make_result(
                        (_make_hit("https://hf.example.com", "HF"),), "historical_frontier", source_family="historical"
                    )
                ),
            ),
            patch.object(
                cascade_mod,
                "_run_wayback_cdx",
                AsyncMock(
                    return_value=_make_result(
                        (_make_hit("https://wb.example.com", "WB"),), "wayback_cdx", source_family="archive"
                    )
                ),
            ),
            patch.object(cascade_mod, "_run_dht", no_dht),
        ):
            result = await cascade_mod._async_search_sequential("test", max_results=20, timeout_s=10.0)

        # fuse_on_empty + DDG nonempty → first_wins → DDG wins
        assert result.provider_name == "duckduckgo"
        assert len(result.hits) == 1

    @pytest.mark.asyncio
    async def test_fusion_mode_fuse_on_empty_with_ddg_empty(self) -> None:
        """CASCADE_FUSION_MODE=fuse_on_empty + DDG empty → fuse (all 3 empty → last fallback)."""
        from hledac.universal.discovery import cascade as cascade_mod

        async def dht_hit(*_a: object, **_kw: object) -> DiscoveryBatchResult:
            return _make_result(
                (_make_hit("https://dht.example.com", "DHT", source="dht_discovery"),),
                "dht",
                source_family="dht_discovery",
            )

        empty = _make_result((), "duckduckgo", error="ddg_empty")
        hf_hit = _make_hit("https://hf.example.com", "HF")
        with (
            patch.object(cascade_mod, "_get_fusion_mode", return_value="fuse_on_empty"),
            patch.object(cascade_mod, "_run_ddg", AsyncMock(return_value=empty)),
            patch.object(
                cascade_mod,
                "_run_historical_frontier",
                AsyncMock(return_value=_make_result((hf_hit,), "historical_frontier", source_family="historical")),
            ),
            patch.object(cascade_mod, "_run_wayback_cdx", AsyncMock(return_value=_make_result((), "wayback_cdx"))),
            patch.object(cascade_mod, "_run_dht", dht_hit),
        ):
            result = await cascade_mod._async_search_sequential("test", max_results=20, timeout_s=10.0)

        # DDG empty + HF nonempty → first_wins fallback → HF wins
        assert result.provider_name == "historical_frontier"
        assert len(result.hits) == 1
        assert result.hits[0].url == "https://hf.example.com"


# ---------------------------------------------------------------------------
# Recall benchmark: fusion vs first_wins
# ---------------------------------------------------------------------------


class TestCascadeFusionRecall:
    """AP-03: Benchmark — fusion má výrazně lepší hit recall než first_wins."""

    @pytest.mark.asyncio
    async def test_fusion_recall_superset_of_first_wins(self) -> None:
        """
        Benchmark scenario: DDG má 3 hits, HF má 2 unikátní, WB má 2 unikátní.
        first_wins → 3 hits (DDG only).
        fuse_always → 7 hits (all unique, deduped).
        Fusion recall > first_wins recall, fusion superset of first_wins.
        """
        from hledac.universal.discovery import cascade as cascade_mod

        ddg_hits = tuple(_make_hit(f"https://ddg{i}.example.com", f"DDG {i}") for i in range(3))
        hf_hits = tuple(_make_hit(f"https://hf{i}.example.com", f"HF {i}", source="historical") for i in range(2))
        wb_hits = tuple(_make_hit(f"https://wb{i}.example.com", f"WB {i}", source="archive") for i in range(2))

        ddg_r = _make_result(ddg_hits, "duckduckgo")
        hf_r = _make_result(hf_hits, "historical_frontier", source_family="historical")
        wb_r = _make_result(wb_hits, "wayback_cdx", source_family="archive")

        async def no_dht(*_a: object, **_kw: object) -> DiscoveryBatchResult:
            return _make_result(())

        async def run_seq(mode: str) -> DiscoveryBatchResult:
            with (
                patch.object(cascade_mod, "_get_fusion_mode", lambda: mode),
                patch.object(cascade_mod, "_run_ddg", AsyncMock(return_value=ddg_r)),
                patch.object(cascade_mod, "_run_historical_frontier", AsyncMock(return_value=hf_r)),
                patch.object(cascade_mod, "_run_wayback_cdx", AsyncMock(return_value=wb_r)),
                patch.object(cascade_mod, "_run_dht", no_dht),
            ):
                return await cascade_mod._async_search_sequential("test", max_results=20, timeout_s=10.0)

        first_result = await run_seq("first_wins")
        fuse_result = await run_seq("fuse_always")

        first_urls = {h.url for h in first_result.hits}
        fuse_urls = {h.url for h in fuse_result.hits}

        # first_wins: pouze DDG (3 hits)
        assert len(first_result.hits) == 3, f"expected 3, got {len(first_result.hits)}"
        assert first_result.provider_name == "duckduckgo"

        # fuse_always: všech 7 unikátních
        assert len(fuse_result.hits) == 7, f"expected 7, got {len(fuse_result.hits)}"
        assert fuse_result.provider_name == "fusion"

        # Fusion je nadmnožina first_wins
        assert first_urls.issubset(fuse_urls), "fusion must superset first_wins"
        assert len(fuse_urls) > len(first_urls), "fusion must return more hits"

    @pytest.mark.asyncio
    async def test_dht_last_resort_still_works(self) -> None:
        """DHT last-resort funguje i po AP-03 změnách — když všechny 3 selžou."""
        from hledac.universal.discovery import cascade as cascade_mod

        empty = _make_result((), "empty", error="all_empty")

        async def dht_hit(*_a: object, **_kw: object) -> DiscoveryBatchResult:
            hit = _make_hit("https://dht.example.com/p2p", "DHT P2P", source="dht_discovery")
            return _make_result((hit,), "dht", source_family="dht_discovery")

        with (
            patch.object(cascade_mod, "_get_fusion_mode", lambda: "first_wins"),
            patch.object(cascade_mod, "_run_ddg", AsyncMock(return_value=empty)),
            patch.object(cascade_mod, "_run_historical_frontier", AsyncMock(return_value=empty)),
            patch.object(cascade_mod, "_run_wayback_cdx", AsyncMock(return_value=empty)),
            patch.object(cascade_mod, "_run_dht", dht_hit),
        ):
            result = await cascade_mod._async_search_sequential("test", max_results=20, timeout_s=10.0)

        assert result.provider_name == "dht"
        assert len(result.hits) == 1
        assert result.hits[0].url == "https://dht.example.com/p2p"
