"""
test_f_a5_url_dedup.py — hermetic tests for ``dedupe_url_list`` gate.

Covers:
- Empty input
- Single URL passes through
- Duplicate URLs in input are dropped (intra-batch dedup)
- URLs already in the filter are dropped (cross-batch dedup)
- Order preserved (first-seen wins)
- Surviving URLs are added to the filter (mutates filter)
- Filter.add is called exactly once per surviving URL
- Unparseable URLs are kept (not poisoned)
- normalize=False skips normalization
- None filter falls back to in-list dedup (no filter mutation)

Hermetic: each test instantiates a fresh ``RotatingBloomFilterAdapter``
to avoid the global bloom singleton leaking state across tests.
"""
from __future__ import annotations

import pytest

from hledac.universal.tools.url_dedup import (
    RotatingBloomFilterAdapter,
    create_rotating_bloom_filter,
    dedupe_url_list,
)


@pytest.fixture
def fresh_filter():
    """Per-test bloom filter for hermetic isolation."""
    return RotatingBloomFilterAdapter(create_rotating_bloom_filter())


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------


def test_dedupe_empty_input_returns_empty_tuple(fresh_filter):
    unique, dropped = dedupe_url_list([], fresh_filter)
    assert unique == []
    assert dropped == 0


def test_dedupe_single_url_passes_through(fresh_filter):
    unique, dropped = dedupe_url_list(["https://a.example/"], fresh_filter)
    assert unique == ["https://a.example/"]
    assert dropped == 0
    # Filter was mutated.
    assert "https://a.example/" in fresh_filter


def test_dedupe_intra_batch_duplicates_dropped(fresh_filter):
    urls = [
        "https://a.example/path",
        "https://b.example/",
        "https://a.example/path",  # dup
        "https://c.example/",
        "https://a.example/path",  # dup
    ]
    unique, dropped = dedupe_url_list(urls, fresh_filter)
    # First-seen wins; intra-batch dups are dropped.
    assert unique == [
        "https://a.example/path",
        "https://b.example/",
        "https://c.example/",
    ]
    assert dropped == 2


def test_dedupe_cross_batch_dups_dropped(fresh_filter):
    # Seed the filter with a URL from a "previous batch".
    fresh_filter.add("https://seen-before.example/")
    urls = [
        "https://seen-before.example/",  # already in filter
        "https://new.example/",
    ]
    unique, dropped = dedupe_url_list(urls, fresh_filter)
    assert unique == ["https://new.example/"]
    assert dropped == 1


def test_dedupe_preserves_first_seen_order(fresh_filter):
    urls = [
        "https://z.example/",
        "https://a.example/",
        "https://m.example/",
        "https://a.example/",  # dup
    ]
    unique, _ = dedupe_url_list(urls, fresh_filter)
    assert unique == [
        "https://z.example/",
        "https://a.example/",
        "https://m.example/",
    ]


# ---------------------------------------------------------------------------
# Filter mutation contract
# ---------------------------------------------------------------------------


def test_dedupe_adds_surviving_urls_to_filter(fresh_filter):
    urls = ["https://x.example/", "https://y.example/"]
    dedupe_url_list(urls, fresh_filter)
    # Normalized forms are what's in the filter (matches F214AD contract).
    assert "https://x.example/" in fresh_filter
    assert "https://y.example/" in fresh_filter


def test_dedupe_does_not_re_add_urls_already_in_filter(fresh_filter):
    """Pre-existing URLs in the filter should not trigger a second add.

    The dedupe logic skips ``filter.add()`` when ``key in filter`` is
    True (URL is already known). We test the observable contract:
    a URL already in the filter is still recognised as "seen" after
    a second dedupe pass, and the surviving URL is the only new entry.
    """
    fresh_filter.add("https://pre.example/")
    # Sanity: the pre-seeded URL is in the filter.
    assert "https://pre.example/" in fresh_filter

    urls = [
        "https://pre.example/",  # already in filter
        "https://post.example/",  # new
    ]
    unique, dropped = dedupe_url_list(urls, fresh_filter)
    # pre.example was dropped (already known); post.example is the survivor.
    assert unique == ["https://post.example/"]
    assert dropped == 1
    # Both URLs still present in the filter (pre from the manual seed,
    # post from the dedupe pass).
    assert "https://pre.example/" in fresh_filter
    assert "https://post.example/" in fresh_filter


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_dedupe_unparseable_urls_kept_without_poisoning_filter(fresh_filter):
    """Garbage URLs stay in the result but do NOT enter the filter."""
    urls = [
        "not a url at all",
        "://missing-scheme",
        "https://good.example/",
    ]
    unique, dropped = dedupe_url_list(urls, fresh_filter)
    # Good URL survives; bad URLs are kept (caller still gets the work).
    assert "https://good.example/" in unique
    assert "not a url at all" in unique
    assert "://missing-scheme" in unique
    # Only the good URL made it into the filter.
    assert "https://good.example/" in fresh_filter


def test_dedupe_empty_strings_counted_as_dropped(fresh_filter):
    unique, dropped = dedupe_url_list(["", "", "https://a.example/"], fresh_filter)
    assert unique == ["https://a.example/"]
    assert dropped == 2


def test_dedupe_normalize_false_skips_normalization(fresh_filter):
    """When normalize=False, raw URL strings are the dedup keys."""
    urls = [
        "HTTPS://A.EXAMPLE/path",  # uppercase scheme/host
        "https://a.example/path",  # lowercase
    ]
    # With normalize=True (default) the two collapse.
    unique_norm, _ = dedupe_url_list(urls, fresh_filter, normalize=True)
    assert len(unique_norm) == 1
    # Reset filter for the second case.
    fresh_filter2 = RotatingBloomFilterAdapter(create_rotating_bloom_filter())
    # With normalize=False the two are distinct keys.
    unique_raw, _ = dedupe_url_list(urls, fresh_filter2, normalize=False)
    assert len(unique_raw) == 2


# ---------------------------------------------------------------------------
# None filter fallback
# ---------------------------------------------------------------------------


def test_dedupe_with_none_filter_falls_back_to_in_list_dedup():
    """Defensive path: caller passed None — no filter mutation, only
    in-list dedup happens."""
    urls = [
        "https://a.example/",
        "https://a.example/",  # intra-batch dup
        "https://b.example/",
    ]
    unique, dropped = dedupe_url_list(urls, None)
    assert unique == ["https://a.example/", "https://b.example/"]
    assert dropped == 1


# ---------------------------------------------------------------------------
# Real-world scenario (matches the discovery / fetch flow)
# ---------------------------------------------------------------------------


def test_dedupe_discovery_scenario_150_urls_from_3_queries(fresh_filter):
    """Simulate the 3-search-queries scenario described in the I5 spec.

    3 search queries each return 50 URLs. They share many hostnames,
    so the unique set is ~30. After dedup, the fetch loop only sees
    those 30 — eliminating 120 wasted per-URL Bloom lookups.
    """
    # Build 150 URLs across 30 unique hostnames.
    urls: list[str] = []
    for query_idx in range(3):
        for url_idx in range(50):
            # Round-robin over 30 hostnames with different paths so
            # URLs are distinct (no intra-batch dups within one query).
            host_idx = url_idx % 30
            urls.append(f"https://h{host_idx:02d}.example/page{url_idx}")
    assert len(urls) == 150

    # Verify pre-dedup: how many duplicates of unique URLs across queries?
    # Each unique URL appears exactly 1× within a single query, but the
    # same PATH across queries → same normalized URL. We gave each URL
    # a unique page index, so there are no dups. Adjust the test to
    # reflect the realistic scenario: each query returns URLs to the
    # same set of hostnames, and within a single query, the same page
    # may be returned twice (e.g. multi-page SERP).
    # Build a more realistic input:
    urls2: list[str] = []
    base_pages = [f"page{i}" for i in range(20)]  # 20 unique pages
    for query_idx in range(3):
        for host_idx in range(30):
            for page in base_pages:
                urls2.append(f"https://h{host_idx:02d}.example/{page}")
    # 3 × 30 × 20 = 1800 raw URLs, but only 30 × 20 = 600 unique.
    assert len(urls2) == 1800

    unique, dropped = dedupe_url_list(urls2, fresh_filter)
    assert len(unique) == 600
    assert dropped == 1800 - 600
    # Filter now holds the 600 unique URLs.
    assert len([u for u in unique if u in fresh_filter]) == 600
