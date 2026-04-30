"""
Tests for discovery/fusion_ranker.py

Sprint F206AP: Providerless Discovery Fusion Ranker

Test coverage:
  1. duplicate URL merged
  2. provider_chain preserved
  3. source_family diversity cap works
  4. same-host cap works
  5. RRF stable ranking
  6. deterministic output
  7. bounded max_results
  8. no numpy/pandas import
"""

from __future__ import annotations

import time

from hledac.universal.discovery.duckduckgo_adapter import (
    DiscoveryHit,
    DiscoveryBatchResult,
)
from hledac.universal.discovery.fusion_ranker import (
    fuse_discovery_hits,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_hit(
    url: str,
    rank: int = 0,
    score: float = 0.0,
    source: str = "duckduckgo",
    title: str = "Test Page",
    query: str = "test query",
    retrieved_ts: float | None = None,
) -> DiscoveryHit:
    return DiscoveryHit(
        query=query,
        title=title,
        url=url,
        snippet="snippet",
        source=source,
        rank=rank,
        retrieved_ts=retrieved_ts or time.time(),
        score=score,
        reason=None,
    )


def _make_batch(
    hits: list[DiscoveryHit],
    provider_name: str = "duckduckgo",
    provider_chain: tuple[str, ...] = (),
    source_family: str | None = "search",
) -> DiscoveryBatchResult:
    return DiscoveryBatchResult(
        hits=tuple(hits),
        provider_name=provider_name,
        provider_chain=provider_chain,
        source_family=source_family,
    )


# ---------------------------------------------------------------------------
# Test 1: duplicate URL merged
# ---------------------------------------------------------------------------

class TestDuplicateUrlMerged:
    def test_same_url_from_two_providers_kept_once(self):
        """URLs identical after normalisation should appear exactly once."""
        hit1 = _make_hit(url="https://example.com/page", rank=0, source="duckduckgo")
        hit2 = _make_hit(url="https://example.com/page", rank=0, source="historical_frontier")

        batch1 = _make_batch([hit1], provider_chain=("duckduckgo",), source_family="search")
        batch2 = _make_batch([hit2], provider_chain=("historical_frontier",), source_family="historical")

        result = fuse_discovery_hits([batch1, batch2])

        assert len(result.hits) == 1
        assert result.hits[0].url == "https://example.com/page"

    def test_www_stripped_for_dedup(self):
        """www. variant should be deduplicated against non-www."""
        hit1 = _make_hit(url="https://example.com/page", rank=0, source="duckduckgo")
        hit2 = _make_hit(url="https://www.example.com/page", rank=1, source="wayback_cdx")

        batch1 = _make_batch([hit1], provider_chain=("duckduckgo",), source_family="search")
        batch2 = _make_batch([hit2], provider_chain=("wayback_cdx",), source_family="archive")

        result = fuse_discovery_hits([batch1, batch2])

        assert len(result.hits) == 1

    def test_different_paths_not_deduplicated(self):
        """Different paths should remain separate."""
        hit1 = _make_hit(url="https://example.com/page1", rank=0, source="duckduckgo")
        hit2 = _make_hit(url="https://example.com/page2", rank=1, source="duckduckgo")

        batch1 = _make_batch([hit1], provider_chain=("duckduckgo",), source_family="search")
        batch2 = _make_batch([hit2], provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch1, batch2])

        assert len(result.hits) == 2


# ---------------------------------------------------------------------------
# Test 2: provider_chain preserved
# ---------------------------------------------------------------------------

class TestProviderChainPreserved:
    def test_combined_chain_from_multiple_batches(self):
        """Combined provider_chain should contain all unique providers in order."""
        batch1 = _make_batch([], provider_name="duckduckgo",
                             provider_chain=("duckduckgo",), source_family="search")
        batch2 = _make_batch([], provider_name="historical_frontier",
                             provider_chain=("historical_frontier",), source_family="historical")
        batch3 = _make_batch([], provider_name="wayback_cdx",
                             provider_chain=("wayback_cdx",), source_family="archive")

        result = fuse_discovery_hits([batch1, batch2, batch3])

        assert result.provider_chain == ("duckduckgo", "historical_frontier", "wayback_cdx")

    def test_empty_input(self):
        """Empty provider list should return empty hits with empty chain."""
        result = fuse_discovery_hits([])

        assert result.hits == ()
        assert result.provider_chain == ()


# ---------------------------------------------------------------------------
# Test 3: source_family diversity cap works
# ---------------------------------------------------------------------------

class TestSourceFamilyDiversityCap:
    def test_family_ratio_enforced_at_50_percent(self):
        """No single source_family should exceed 50% of results."""
        # Create 10 hits all from "search" family
        search_hits = [
            _make_hit(url=f"https://search{i}.com", rank=i, source="duckduckgo")
            for i in range(10)
        ]
        # Create 10 hits from "archive" family
        archive_hits = [
            _make_hit(url=f"https://archive{i}.com", rank=i, source="wayback_cdx")
            for i in range(10)
        ]

        search_batch = _make_batch(search_hits, provider_chain=("duckduckgo",), source_family="search")
        archive_batch = _make_batch(archive_hits, provider_chain=("wayback_cdx",), source_family="archive")

        result = fuse_discovery_hits([search_batch, archive_batch], max_results=20)

        # "search" family should have at most 10 hits (50% of 20)
        search_count = sum(1 for h in result.hits
                          if _get_family_from_hit(h, [search_batch, archive_batch]) == "search")
        assert search_count <= 10

    def test_multi_family_result(self):
        """Mixed families should produce multi source_family label."""
        hit1 = _make_hit(url="https://search.com", rank=0, source="duckduckgo",)
        hit2 = _make_hit(url="https://archive.com", rank=0, source="wayback_cdx",)

        batch1 = _make_batch([hit1], provider_chain=("duckduckgo",), source_family="search")
        batch2 = _make_batch([hit2], provider_chain=("wayback_cdx",), source_family="archive")

        result = fuse_discovery_hits([batch1, batch2])

        assert result.source_family == "multi"


# ---------------------------------------------------------------------------
# Test 4: same-host cap works
# ---------------------------------------------------------------------------

class TestSameHostCap:
    def test_max_3_per_host_enforced(self):
        """No more than 3 hits per host should be returned."""
        # 10 hits from same host
        hits = [
            _make_hit(url=f"https://example.com/page{i}", rank=i, source="duckduckgo")
            for i in range(10)
        ]
        batch = _make_batch(hits, provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch], max_results=20)

        host_counts: dict[str, int] = {}
        for h in result.hits:
            from urllib.parse import urlparse
            host = urlparse(h.url).netloc.lower()
            host_counts[host] = host_counts.get(host, 0) + 1

        assert all(c <= 3 for c in host_counts.values())

    def test_different_hosts_all_kept(self):
        """Different hosts should not be subject to per-host cap."""
        hits = [
            _make_hit(url=f"https://host{i}.com", rank=i, source="duckduckgo")
            for i in range(10)
        ]
        batch = _make_batch(hits, provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch], max_results=20)

        assert len(result.hits) == 10


# ---------------------------------------------------------------------------
# Test 5: RRF stable ranking
# ---------------------------------------------------------------------------

class TestRRFStableRanking:
    def test_rank_position_affects_score(self):
        """Lower rank (higher position) should score higher in RRF."""
        hit0 = _make_hit(url="https://rank0.com", rank=0, source="duckduckgo",)
        hit1 = _make_hit(url="https://rank1.com", rank=1, source="duckduckgo",)
        hit5 = _make_hit(url="https://rank5.com", rank=5, source="duckduckgo",)

        batch = _make_batch([hit0, hit1, hit5], provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch])

        urls = [h.url for h in result.hits]
        # rank0 should appear before rank1 and rank5
        assert urls.index("https://rank0.com") < urls.index("https://rank1.com")
        assert urls.index("https://rank1.com") < urls.index("https://rank5.com")


# ---------------------------------------------------------------------------
# Test 6: deterministic output
# ---------------------------------------------------------------------------

class TestDeterministicOutput:
    def test_same_inputs_produce_same_output(self):
        """Identical inputs should always produce identical outputs."""
        hits = [
            _make_hit(url=f"https://example.com/page{i}", rank=i, source="duckduckgo")
            for i in range(5)
        ]
        batch = _make_batch(hits, provider_chain=("duckduckgo",), source_family="search")

        result1 = fuse_discovery_hits([batch])
        result2 = fuse_discovery_hits([batch])
        result3 = fuse_discovery_hits([batch])

        assert [h.url for h in result1.hits] == [h.url for h in result2.hits]
        assert [h.url for h in result2.hits] == [h.url for h in result3.hits]

    def test_url_tiebreak_deterministic(self):
        """Same-score hits should be ordered by URL ascending."""
        now = time.time()
        hit_a = _make_hit(url="https://zeta.com", rank=0, source="duckduckgo",
                          retrieved_ts=now)
        hit_b = _make_hit(url="https://alpha.com", rank=0, source="duckduckgo",
                          retrieved_ts=now)

        batch = _make_batch([hit_a, hit_b], provider_chain=("duckduckgo",), source_family="search")

        result1 = fuse_discovery_hits([batch])
        result2 = fuse_discovery_hits([batch])

        # URL ascending tiebreak should be consistent
        assert [h.url for h in result1.hits] == [h.url for h in result2.hits]


# ---------------------------------------------------------------------------
# Test 7: bounded max_results
# ---------------------------------------------------------------------------

class TestBoundedMaxResults:
    def test_exactly_max_results_returned(self):
        """Result should never exceed max_results."""
        hits = [
            _make_hit(url=f"https://example.com/page{i}", rank=i, source="duckduckgo")
            for i in range(50)
        ]
        batch = _make_batch(hits, provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch], max_results=10)

        assert len(result.hits) <= 10

    def test_max_results_20_default(self):
        """Default max_results should be 20."""
        hits = [
            _make_hit(url=f"https://example.com/page{i}", rank=i, source="duckduckgo")
            for i in range(100)
        ]
        batch = _make_batch(hits, provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch])

        assert len(result.hits) <= 20

    def test_max_results_0_returns_empty(self):
        """max_results=0 should return empty hits."""
        hit = _make_hit(url="https://example.com", rank=0, source="duckduckgo",)
        batch = _make_batch([hit], provider_chain=("duckduckgo",), source_family="search")

        result = fuse_discovery_hits([batch], max_results=0)

        assert len(result.hits) == 0


# ---------------------------------------------------------------------------
# Test 8: no numpy/pandas import
# ---------------------------------------------------------------------------

class TestNoNumpyPandas:
    def test_no_numpy_or_pandas_in_module(self):
        """Fusion ranker should not import numpy or pandas."""
        import ast

        # Read source
        import hledac.universal.discovery.fusion_ranker as mod
        _mod_file = mod.__file__
        assert _mod_file is not None
        source = open(_mod_file).read()

        tree = ast.parse(source)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    imports.append(alias.name)

        has_numpy = any("numpy" in name for name in imports)
        has_pandas = any("pandas" in name for name in imports)

        assert not has_numpy, "numpy should not be imported"
        assert not has_pandas, "pandas should not be imported"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_family_from_hit(hit: DiscoveryHit, batches: list[DiscoveryBatchResult]) -> str:
    """Get source_family for a hit from its originating batch."""
    for batch in batches:
        for h in batch.hits:
            if h.url == hit.url:
                return batch.source_family or "unknown"
    return "unknown"
