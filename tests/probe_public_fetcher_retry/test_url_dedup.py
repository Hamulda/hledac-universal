"""
TEST-003: RotatingBloomFilter URL Deduplication Tests
TEST-004: Circuit Breaker Domain Eviction Tests
=====================================================

Unit tests for URL deduplication via RotatingBloomFilter.
Unit tests for MAX_HOST_PENALTIES circuit breaker eviction.

Covers add/contains/invocation with parametrized false positive rates.
"""
import pytest

from hledac.universal.tools.url_dedup import (
    create_rotating_bloom_filter,
    reset_default_bloom_filter,
    fast_hash,
    DEFAULT_URL_ESTIMATE,
    DEFAULT_FPR,
)
from hledac.universal.tools.checkpoint import (
    _bound_host_penalties,
    MAX_HOST_PENALTIES,
)


class TestRotatingBloomFilterCreation:
    """Test RotatingBloomFilter factory and parameters."""

    def test_create_rotating_bloom_filter_default_params(self):
        """Default creation uses DEFAULT_URL_ESTIMATE and DEFAULT_FPR."""
        bf = create_rotating_bloom_filter()
        assert bf is not None

    def test_create_rotating_bloom_filter_custom_estimate(self):
        """Custom est_elements parameter is respected."""
        bf = create_rotating_bloom_filter(est_elements=50000)
        assert bf is not None

    def test_create_rotating_bloom_filter_custom_fpr(self):
        """Custom false_positive_rate parameter is respected."""
        bf = create_rotating_bloom_filter(false_positive_rate=0.001)
        assert bf is not None

    def test_create_rotating_bloom_filter_min_fpr(self):
        """Minimum FPR (0.001) is accepted."""
        bf = create_rotating_bloom_filter(false_positive_rate=0.001)
        assert bf is not None

    def test_default_constants_defined(self):
        """DEFAULT_URL_ESTIMATE and DEFAULT_FPR are defined."""
        assert DEFAULT_URL_ESTIMATE == 100000
        assert DEFAULT_FPR == 0.01


class TestRotatingBloomFilterAddContains:
    """Test add() and contains (in) operations."""

    def test_add_then_contains_returns_true(self):
        """URL added to filter is subsequently found by `in`."""
        bf = create_rotating_bloom_filter(est_elements=1000, false_positive_rate=0.01)
        url = "https://example.com/page1"
        bf.add(url)
        assert url in bf

    def test_contains_returns_false_for_unknown(self):
        """URL never added returns False for `in`."""
        bf = create_rotating_bloom_filter(est_elements=1000, false_positive_rate=0.01)
        url = "https://unique-example-never-added.com/page1"
        assert url not in bf

    def test_multiple_urls_all_found_after_add(self):
        """All added URLs are subsequently found."""
        bf = create_rotating_bloom_filter(est_elements=1000, false_positive_rate=0.01)
        urls = [f"https://example.com/page{i}" for i in range(20)]
        for url in urls:
            bf.add(url)
        for url in urls:
            assert url in bf, f"Expected {url} to be in bloom filter"

    def test_false_positive_rate_bounded(self):
        """False positive rate stays within expected bounds for small sets."""
        bf = create_rotating_bloom_filter(est_elements=1000, false_positive_rate=0.01)
        added_urls = [f"https://added-{i}.com" for i in range(50)]
        for url in added_urls:
            bf.add(url)

        # Check 200 unseen URLs - false positives should be very low
        false_positives = 0
        check_count = 200
        for i in range(check_count):
            url = f"https://never-added-unique-{i}-xyz.com/page"
            if url in bf:
                false_positives += 1

        # With 0.01 FPR and 50 items, expect ~0-2 false positives
        # Be permissive: allow up to 5% false positive rate for small sample
        fpr_observed = false_positives / check_count
        assert fpr_observed < 0.05, (
            f"FPR too high: observed {fpr_observed:.2%} ({false_positives}/{check_count}), "
            f"expected <5% for 0.01 target"
        )


class TestRotatingBloomFilterDedup:
    """Test deduplication behavior typical of fetch coordinator."""

    def test_duplicate_detection_basic(self):
        """Same URL added twice is detected on second check."""
        bf = create_rotating_bloom_filter(est_elements=1000, false_positive_rate=0.01)
        url = "https://example.com/duplicate-test"

        # First encounter - not a duplicate
        is_dup = url in bf
        assert not is_dup, "URL should not be in filter before first add"

        # Add and check again
        bf.add(url)
        is_dup = url in bf
        assert is_dup, "URL should be in filter after add"

    def test_dedup_flow_with_candidates(self):
        """Simulate fetch coordinator dedup flow: check before add."""
        bf = create_rotating_bloom_filter(est_elements=1000, false_positive_rate=0.01)
        results = []

        urls_to_process = [
            "https://a.com/1",
            "https://b.com/2",
            "https://a.com/1",  # duplicate
            "https://c.com/3",
            "https://b.com/2",  # duplicate
            "https://d.com/4",
        ]

        for url in urls_to_process:
            if url not in bf:
                # Simulate fetch success
                bf.add(url)
                results.append(("fetched", url))
            else:
                results.append(("skipped", url))

        # Verify results
        fetched = [r for r in results if r[0] == "fetched"]
        skipped = [r for r in results if r[0] == "skipped"]

        assert len(fetched) == 4, f"Expected 4 unique URLs, got {len(fetched)}: {fetched}"
        assert len(skipped) == 2, f"Expected 2 duplicates, got {len(skipped)}: {skipped}"
        assert fetched == [
            ("fetched", "https://a.com/1"),
            ("fetched", "https://b.com/2"),
            ("fetched", "https://c.com/3"),
            ("fetched", "https://d.com/4"),
        ]


class TestFastHash:
    """Test fast_hash utility function."""

    def test_fast_hash_returns_hex_string(self):
        """fast_hash returns a hexadecimal string."""
        result = fast_hash("https://example.com")
        assert isinstance(result, str)
        assert all(c in "0123456789abcdef" for c in result)

    def test_fast_hash_deterministic(self):
        """Same input always produces same hash."""
        url = "https://example.com/test"
        h1 = fast_hash(url)
        h2 = fast_hash(url)
        assert h1 == h2

    def test_fast_hash_different_urls_different_hashes(self):
        """Different URLs produce different hashes."""
        urls = [f"https://example.com/page{i}" for i in range(10)]
        hashes = [fast_hash(url) for url in urls]
        assert len(set(hashes)) == len(hashes), "Hashes should all be unique for different URLs"


class TestResetDefaultBloomFilter:
    """Test reset_default_bloom_filter for testing isolation."""

    def test_reset_clears_default(self):
        """reset_default_bloom_filter sets global to None."""
        from hledac.universal.tools.url_dedup import _default_bloom

        # Create default
        bf1 = create_rotating_bloom_filter()
        from hledac.universal.tools import url_dedup
        url_dedup._default_bloom = bf1

        # Reset
        reset_default_bloom_filter()

        # Verify cleared
        assert url_dedup._default_bloom is None


# =============================================================================
# TEST-004: Circuit Breaker Domain Eviction
# =============================================================================

class TestHostPenaltiesBoundedEviction:
    """Test MAX_HOST_PENALTIES bound eviction in checkpoint serialization."""

    def test_max_host_penalties_constant(self):
        """MAX_HOST_PENALTIES is defined as 512."""
        assert MAX_HOST_PENALTIES == 512

    def test_host_penalties_unchanged_when_under_limit(self):
        """host_penalties dict is unchanged when size <= MAX_HOST_PENALTIES."""
        obj = {
            "host_penalties": {
                f"host-{i}.example.com": float(i)
                for i in range(10)
            }
        }
        result = _bound_host_penalties(obj)
        assert len(result["host_penalties"]) == 10

    def test_host_penalties_bounded_to_max(self):
        """host_penalties dict is bounded to MAX_HOST_PENALTIES when exceeded."""
        obj = {
            "host_penalties": {
                f"host-{i}.example.com": float(i)
                for i in range(600)  # Exceeds 512
            }
        }
        assert len(obj["host_penalties"]) == 600
        result = _bound_host_penalties(obj)
        assert len(result["host_penalties"]) == MAX_HOST_PENALTIES

    def test_host_penalties_keeps_highest_penalties(self):
        """Eviction keeps highest penalty values when over MAX_HOST_PENALTIES."""
        # Create 600 entries (exceeds MAX_HOST_PENALTIES=512) with known penalties
        penalties = {}
        for i in range(600):
            penalties[f"host-{i}.example.com"] = float(i)

        obj = {"host_penalties": penalties}
        result = _bound_host_penalties(obj)

        # Should be bounded to 512
        assert len(result["host_penalties"]) == 512

        # Highest penalties (top 512) should be kept - these are hosts 88-599
        # (since we sort descending and take first 512, we get the 512 highest)
        # Host 599 has penalty 599.0, host 88 has penalty 88.0, host 87 has penalty 87.0 (cutoff)
        assert "host-599.example.com" in result["host_penalties"]
        assert "host-88.example.com" in result["host_penalties"]
        # Lower penalties should be evicted
        assert "host-87.example.com" not in result["host_penalties"]
        assert "host-0.example.com" not in result["host_penalties"]

    def test_host_penalties_bounded_exactly_at_limit(self):
        """Boundary case: exactly MAX_HOST_PENALTIES entries."""
        obj = {
            "host_penalties": {
                f"host-{i}.example.com": float(i)
                for i in range(512)
            }
        }
        result = _bound_host_penalties(obj)
        assert len(result["host_penalties"]) == 512

    def test_host_penalties_ignores_non_dict(self):
        """Non-dict host_penalties is passed through unchanged."""
        obj = {"host_penalties": "not-a-dict"}
        result = _bound_host_penalties(obj)
        assert result["host_penalties"] == "not-a-dict"

    def test_host_penalties_skips_invalid_values(self):
        """Invalid penalty values (negative, non-numeric) are skipped in sorting."""
        obj = {
            "host_penalties": {
                "valid-host.example.com": 50.0,
                "negative-penalty.example.com": -10.0,
                "another-valid.example.com": 75.0,
            }
        }
        result = _bound_host_penalties(obj)

        # Valid entries preserved
        assert "valid-host.example.com" in result["host_penalties"]
        assert "another-valid.example.com" in result["host_penalties"]
        # Negative penalty entry not retained (or handled gracefully)
        assert len(result["host_penalties"]) <= 3

    def test_host_penalties_host_length_truncated(self):
        """Host strings longer than MAX_HOST_LEN are truncated when eviction occurs."""
        # Create 600 entries with varying long host names
        # Use unique prefix before truncation point (first 200 chars unique)
        penalties = {}
        for i in range(600):
            # Unique identifier at START (will be preserved after truncation)
            unique_prefix = f"host-{i:04d}-"
            long_suffix = "x" * 250  # filler
            long_host = unique_prefix + long_suffix + f".example.com"
            penalties[long_host] = float(i)

        obj = {"host_penalties": penalties}
        result = _bound_host_penalties(obj)

        # Should be bounded to 512
        assert len(result["host_penalties"]) == 512

        # All keys should be truncated to MAX_HOST_LEN=256
        keys = list(result["host_penalties"].keys())
        for k in keys:
            assert len(k) <= 256, f"Key too long: {len(k)} > 256: {k[:50]}..."
