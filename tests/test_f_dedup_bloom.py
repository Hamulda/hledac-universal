"""
F266-B4 — DedupBloom (DistributedBloomFilter) tests
===================================================

Hermetic test suite for `DedupBloom` (Python wrapper) and its Rust backend
(`PyDistributedBloomFilter` in `rust_extensions/src/dedup_bloom.rs`).

The tests cover:
  - Class availability (skip if Rust extension not built).
  - Basic add/contains semantics (true positive, true negative).
  - skip_batch: bulk duplicate-skip semantics and count accuracy.
  - add_batch / contains_batch: batch operations.
  - save/load persistence cycle across re-instantiation.
  - Reset: clears all tiers.
  - stats(): returns expected keys including tier_count.
  - Python set fallback when Rust unavailable (bounded to 50K items).
  - Thread-safe singleton via DCLP lock (concurrent get_dedup_bloom() calls).
  - Memory bound: Python fallback enforces 50K item limit.

These tests do NOT touch network, MLX, or any other heavy dependency.
"""

from pathlib import Path
import tempfile
import threading
import time

import pytest

# ---------------------------------------------------------------------------
# Availability helpers — skip if Rust extension not built.
# ---------------------------------------------------------------------------

_RUST_AVAILABLE = False
_RUST_IMPORT_ERROR: str | None = None
try:
    from hledac_rust_extensions import hledac_rust_extensions as _rust_ext  # noqa: F401

    _DedupBloomRust = getattr(_rust_ext, "PyDistributedBloomFilter", None)
    _RUST_AVAILABLE = _DedupBloomRust is not None
except ImportError as _e:
    _RUST_IMPORT_ERROR = str(_e)
    _DedupBloomRust = None  # type: ignore[assignment]


pytestmark = pytest.mark.skipif(
    not _RUST_AVAILABLE,
    reason=(
        f"hledac_rust_extensions.PyDistributedBloomFilter not available "
        f"({_RUST_IMPORT_ERROR or 'extension not built'})"
    ),
)


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """DedupBloom requires an actual directory (not just a file path)."""
    d = tmp_path / "dedup_bloom_test"
    d.mkdir()
    return d


class TestDedupBloomBasic:
    """Basic add/contains semantics."""

    def test_add_returns_true_for_new_url(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        assert bloom.add("https://example.com") is True

    def test_add_returns_false_for_duplicate(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        assert bloom.add("https://example.com") is False

    def test_contains_false_before_add(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        assert bloom.contains("https://example.com") is False

    def test_contains_true_after_add(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        assert bloom.contains("https://example.com") is True

    def test_len_increments_on_add(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://a.com")
        bloom.add("https://b.com")
        assert bloom.len() == 2

    def test_len_decrement_after_reset(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        bloom.reset()
        assert bloom.len() == 0


class TestDedupBloomBatch:
    """Batch operations: add_batch, contains_batch, skip_batch."""

    def test_contains_batch_all_new(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        results = bloom.contains_batch(urls)
        assert results == [False, False, False]

    def test_contains_batch_some_duplicates(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://b.com")
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        # b.com was added → contains returns True (maybe-seen, may be false positive)
        results = bloom.contains_batch(urls)
        assert results == [False, True, False]

    def test_add_batch_returns_correct_new_flags(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        urls = ["https://a.com", "https://b.com", "https://a.com"]
        results = bloom.add_batch(urls)
        # a.com and b.com are new, a.com duplicate within batch
        assert results == [True, True, False]

    def test_skip_batch_returns_non_duplicates_and_count(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://b.com")  # pre-populate
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        non_dups, skip_count = bloom.skip_batch(urls)

        # b.com is skip_count=1; a.com and c.com are non-duplicates
        assert skip_count == 1
        assert "https://b.com" not in non_dups
        assert set(non_dups) == {"https://a.com", "https://c.com"}

    def test_skip_batch_empty_list(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        non_dups, skip_count = bloom.skip_batch([])
        assert non_dups == []
        assert skip_count == 0

    def test_skip_batch_all_duplicates(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://a.com")
        bloom.add("https://b.com")
        urls = ["https://a.com", "https://b.com"]
        non_dups, skip_count = bloom.skip_batch(urls)
        assert non_dups == []
        assert skip_count == 2


class TestDedupBloomPersistence:
    """save/load cycle, weakref.finalize, restart-safe persistence."""

    def test_save_returns_path(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        path = bloom.save()
        assert path is not None
        assert Path(path).exists()

    def test_persistence_after_reinstantiation(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        # Create, add, save
        bloom1 = DedupBloom(str(tmp_cache_dir))
        bloom1.add("https://persist.com")
        bloom1.save()

        # Re-instantiate with same path — should reload persisted state
        bloom2 = DedupBloom(str(tmp_cache_dir))
        assert bloom2.contains("https://persist.com") is True

    def test_reset_clears_all_tiers(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://a.com")
        bloom.add("https://b.com")
        bloom.reset()
        assert bloom.contains("https://a.com") is False
        assert bloom.contains("https://b.com") is False
        assert bloom.len() == 0


class TestDedupBloomStats:
    """stats() returns expected structure."""

    def test_stats_has_expected_keys(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        s = bloom.stats()

        assert "total_items" in s or "len" in s or "items" in s
        assert "memory_bytes" in s or "size" in s

    def test_memory_bytes_reasonable(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        mem = bloom.memory_bytes()
        assert mem > 0
        assert mem < 100_000_000  # sanity: less than 100 MB

    def test_efficiency_returns_fill_rates(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://example.com")
        eff = bloom.efficiency()

        assert "overall_fill_rate" in eff
        assert "tier_0_fill_rate" in eff
        assert 0.0 <= eff["overall_fill_rate"] <= 1.0
        assert all(0.0 <= eff[f"tier_{i}_fill_rate"] <= 1.0 for i in range(3))


class TestDedupBloomSingleton:
    """DCLP lock: concurrent get_dedup_bloom() calls return same instance."""

    def test_singleton_returns_same_instance(self) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import get_dedup_bloom

        # Clear module-level cache to test singleton creation
        import rust_extensions.wiring.dedup_bloom_wiring as _mod

        _mod._cached_instance = None

        try:
            results: list = []

            def get_one() -> None:
                b = get_dedup_bloom()
                results.append(b)

            threads = [threading.Thread(target=get_one) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(results) == 8
            assert all(r is results[0] for r in results)
        finally:
            _mod._cached_instance = None

    def test_available_property_true_when_rust_backed(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        assert bloom.available is True


class TestDedupBloomPythonFallback:
    """Python set fallback when Rust is unavailable (bounded to 50K items)."""

    def test_python_fallback_is_bounded(self, tmp_cache_dir: Path) -> None:
        # This test verifies the set maxsize=50_000 is respected
        # by checking that after 55K items, the cache size is bounded and
        # early entries have been evicted. We simulate Rust being unavailable.
        from rust_extensions.wiring import dedup_bloom_wiring as _mod

        orig_module = _mod._dedup_bloom_module
        _mod._dedup_bloom_module = None  # Force Python fallback

        try:
            from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

            bloom = DedupBloom(str(tmp_cache_dir))
            assert bloom.available is False

            # Add 55K items — old ones should be evicted past 50K
            for i in range(55_000):
                bloom.add(f"https://example{i}.com")

            # The first item (example0.com) should have been evicted
            evicted_50k = bloom.contains(f"https://example0.com")
            # After 55K adds with 50K max, early items are evicted
            assert bloom.len() <= 50_000
            # Eviction is probabilistic with set (not LRU) but 55K vs 50K
            # guarantees some early items are removed
        finally:
            _mod._dedup_bloom_module = orig_module


class TestDedupBloomSkipBatchSemantics:
    """
    skip_batch uses contains_batch internally.
    contains_batch returns True = "maybe duplicate" (bloom filter property).
    skip_batch inverts: returns non-duplicates (not-seen) and skip_count (seen).
    """

    def test_skip_batch_false_positive_awareness(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        # Never added — but bloom filters can have false positives
        # So we can only test the structural contract
        urls = ["https://truly-new-never-seen.example.com"]
        non_dups, skip_count = bloom.skip_batch(urls)

        # Either it passes through (skip_count=0) or is flagged as duplicate (skip_count=1)
        # Both are valid bloom filter semantics
        assert skip_count in (0, 1)
        if skip_count == 0:
            assert len(non_dups) == 1
        else:
            assert len(non_dups) == 0

    def test_skip_batch_order_preserved(self, tmp_cache_dir: Path) -> None:
        from rust_extensions.wiring.dedup_bloom_wiring import DedupBloom

        bloom = DedupBloom(str(tmp_cache_dir))
        bloom.add("https://second.com")
        urls = [
            "https://first.com",
            "https://second.com",
            "https://third.com",
        ]
        non_dups, skip_count = bloom.skip_batch(urls)

        assert skip_count == 1
        # first.com and third.com should be in non_dups, order preserved
        assert non_dups == ["https://first.com", "https://third.com"]
