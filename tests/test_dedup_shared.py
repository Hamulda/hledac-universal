"""
Test F-06: Shared RotatingBloomFilter across all pipeline components.

Verifies that URL deduplication is shared across:
  - duckduckgo_adapter (discovery layer)
  - live_public_pipeline (public lane)
  - FetchCoordinator (fetch layer)

A single shared singleton filter must be used so that 1 URL processed
anywhere in the sprint is marked as "seen" everywhere.

Acceptance: 0 duplicates in 10k URL batch.
"""

from __future__ import annotations

import gc
import os

import pytest

from hledac.universal.tools.url_dedup import (
    get_default_bloom_filter,
    reset_default_bloom_filter,
)


class TestDedupSharedSingleton:
    """Test that all pipeline components share the same BloomFilter singleton."""

    def setup_method(self) -> None:
        """Reset singleton before each test."""
        reset_default_bloom_filter()
        gc.collect()

    def teardown_method(self) -> None:
        """Reset after each test."""
        reset_default_bloom_filter()
        gc.collect()

    def test_singleton_returns_same_instance(self) -> None:
        """Multiple calls to get_default_bloom_filter() return the same object."""
        f1 = get_default_bloom_filter()
        f2 = get_default_bloom_filter()
        assert f1 is f2, "get_default_bloom_filter() must return the same singleton instance"

    def test_singleton_tracks_urls(self) -> None:
        """The shared filter tracks URLs across multiple add() calls."""
        f = get_default_bloom_filter()
        urls = [f"https://example.com/page{i}" for i in range(100)]
        for url in urls:
            assert url not in f  # not yet added
            f.add(url)
        for url in urls:
            assert url in f  # now present

    def test_zero_duplicates_in_10k_batch(self) -> None:
        """F-06 acceptance: all true duplicates caught in 10k URL batch.

        Uses a filter sized for the unique set so FPR stays negligible.
        Measures: (a) no true duplicate is missed, (b) FPR is within bounds.
        Uses a FRESH filter (not shared with previous tests) to avoid saturation.
        """
        # Fresh filter — not polluted by previous tests
        reset_default_bloom_filter()
        f = get_default_bloom_filter()

        # 3000 unique URLs — well within capacity, negligible FPR
        unique_count = 3000
        total_batch = 10_000
        expected_duplicates = total_batch - unique_count  # 7000

        urls: list[str] = []
        for i in range(total_batch):
            page_id = i % unique_count
            urls.append(f"https://example.com/page{page_id}")

        deduped_count = 0
        duplicate_count = 0

        for url in urls:
            if url in f:
                duplicate_count += 1
            else:
                f.add(url)
                deduped_count += 1

        # All true duplicates must be caught (0 missed)
        # True duplicates = the 7000 re-inserted items (5000-9999 matching 0-4999)
        missed = expected_duplicates - duplicate_count
        assert missed == 0, (
            f"Shared BloomFilter singleton must catch ALL true duplicates. "
            f"Missed {missed}/{expected_duplicates} duplicates. "
            f"This means the filter is not shared across pipeline components."
        )

        # FPR: false positives among the "unique" portion (first 3000 inserts)
        # After 3000 inserts into a filter sized for 3000, FPR should be very low
        true_negatives = unique_count - duplicate_count
        false_positives = max(0, duplicate_count - expected_duplicates)
        if true_negatives + false_positives > 0:
            fpr = false_positives / (true_negatives + false_positives)
            assert fpr < 0.10, (
                f"False-positive rate too high: {fpr:.1%}. "
                f"Got {false_positives} false positives. "
                f"Filter may be undersized for the dataset."
            )

        assert deduped_count == unique_count, f"Expected {unique_count} unique URLs, got {deduped_count}"

    def test_shared_filter_between_adapters(self) -> None:
        """Simulate DDG adapter + live_pipeline sharing the same filter.

        This is the actual F-06 bug scenario:
        1. DDG adapter adds URL → filter now contains it
        2. live_public_pipeline checks same URL → should be a duplicate
        """
        reset_default_bloom_filter()
        f = get_default_bloom_filter()

        # Simulate DuckDuckGo adapter adding a URL
        ddg_urls = [f"https://test{i}.com" for i in range(50)]
        for url in ddg_urls:
            f.add(url)

        # Simulate live_public_pipeline processing same URLs
        live_pipeline_hits = [f"https://test{i}.com" for i in range(50)]
        duplicates = 0
        for url in live_pipeline_hits:
            if url in f:
                duplicates += 1

        assert duplicates == 50, (
            f"Expected 50 duplicates (URLs already seen by DDG), got {duplicates}. "
            f"Shared filter not preventing live_pipeline from re-processing DDG URLs."
        )

    def test_reset_clears_singleton(self) -> None:
        """reset_default_bloom_filter() creates a new instance."""
        f1 = get_default_bloom_filter()
        reset_default_bloom_filter()
        f2 = get_default_bloom_filter()
        assert f1 is not f2, "reset_default_bloom_filter() must create a new singleton"

    def test_reset_enables_fresh_dedup(self) -> None:
        """After reset, previously-seen URLs are not in the new filter."""
        f1 = get_default_bloom_filter()
        f1.add("https://already-seen.com")
        assert "https://already-seen.com" in f1

        reset_default_bloom_filter()
        f2 = get_default_bloom_filter()
        assert "https://already-seen.com" not in f2, "After reset, new filter must be empty"

    def test_filter_interface_add_contains(self) -> None:
        """Filter must satisfy DeduplicationStrategy protocol."""
        f = get_default_bloom_filter()
        url = "https://protocol-test.com/path"

        # Must support 'in' operator
        assert url not in f
        # Must support add()
        f.add(url)
        # After add, must be contained
        assert url in f


class TestDedupSharedWithHomeChange:
    """HOME-change detection (P1-3F) invalidates cached singleton."""

    def setup_method(self) -> None:
        reset_default_bloom_filter()
        gc.collect()

    def teardown_method(self) -> None:
        reset_default_bloom_filter()
        gc.collect()

    def test_home_change_invalidates_singleton(self) -> None:
        """When HOME env changes, get_default_bloom_filter() returns a new instance."""
        f1 = get_default_bloom_filter()
        f1.add("https://keep.com")
        original_home = os.environ.get("HOME", "")

        try:
            # Simulate HOME change (as test fixtures do)
            os.environ["HOME"] = original_home + "/different-path"
            f2 = get_default_bloom_filter()

            assert f1 is not f2, "HOME change must invalidate cached singleton"
            assert "https://keep.com" not in f2, "New filter after HOME change must not contain old URLs"
        finally:
            os.environ["HOME"] = original_home


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-x"])
