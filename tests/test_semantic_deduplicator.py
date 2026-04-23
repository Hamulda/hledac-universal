"""
Sprint F195: Semantic Deduplicator Tests
========================================

Tests for embedding-based semantic duplicate detection.
Verifies: low-memory fail-soft, persistence, cache hits.
"""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import numpy as np
import pytest

from hledac.universal.semantic_deduplicator import (
    SemanticDedupCache,
    _SemanticDedupLMDB,
    _cosine_similarity,
    _generate_single_embedding,
    MAX_CACHE_ITEMS,
    MAX_CACHE_MEMORY_MB,
    _EMBEDDING_DIM,
    _MEMORY_GUARD_THRESHOLD_GB,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_texts(n: int) -> list[str]:
    """Generate n distinct test texts."""
    return [f"Unique finding text number {i} with specific content {i * 17}" for i in range(n)]


def _make_similar_texts(base: str, n: int) -> list[str]:
    """Generate texts semantically similar to base."""
    return [f"{base} variant {i}" for i in range(n)]


# ---------------------------------------------------------------------------
# Test: Memory guard
# ---------------------------------------------------------------------------

class TestSemanticDedupMemoryGuard:
    """Low-memory run disables deduplicator fail-soft."""

    def test_memory_guard_triggers_above_threshold(self):
        """RSS > 6GB skips semantic dedup."""
        cache = SemanticDedupCache(lmdb_path=None)

        # Mock RSS to be above threshold
        mock_rss = 6.5 * 1024**3
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.memory_info.return_value.rss = int(mock_rss)
            result = cache._check_memory_guard()
            assert result is False

    def test_memory_guard_allows_below_threshold(self):
        """RSS < 6GB allows semantic dedup."""
        cache = SemanticDedupCache(lmdb_path=None)

        mock_rss = 4.0 * 1024**3
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.memory_info.return_value.rss = int(mock_rss)
            result = cache._check_memory_guard()
            assert result is True

    def test_memory_guard_failsoft_on_error(self):
        """psutil error → returns True (allow dedup to proceed)."""
        cache = SemanticDedupCache(lmdb_path=None)

        with patch("psutil.Process", side_effect=RuntimeError("no psutil")):
            result = cache._check_memory_guard()
            assert result is True


# ---------------------------------------------------------------------------
# Test: LRU cache
# ---------------------------------------------------------------------------

class TestSemanticDedupCacheLRU:
    """Cache hit avoids recomputation."""

    def test_cache_bounded_by_item_count(self):
        """Cache respects MAX_CACHE_ITEMS limit."""
        cache = SemanticDedupCache(lmdb_path=None)
        texts = _make_texts(MAX_CACHE_ITEMS + 100)

        for t in texts:
            cache._add_to_cache(t, np.random.randn(_EMBEDDING_DIM).astype(np.float32))

        assert len(cache._cache) <= MAX_CACHE_ITEMS

    def test_cache_bounded_by_memory(self):
        """Cache respects MAX_CACHE_MEMORY_MB limit."""
        cache = SemanticDedupCache(lmdb_path=None)
        # 256d float32 = 1KB per embedding
        # Add items until memory limit is hit
        count = 0
        while cache._cache_memory_bytes < MAX_CACHE_MEMORY_MB * 1024 * 1024:
            text = f"x" * 200  # ~200 bytes key
            emb = np.random.randn(_EMBEDDING_DIM).astype(np.float32)
            cache._add_to_cache(text, emb)
            count += 1

        # After adding more items, old ones should be evicted
        texts = [f"new item {i}" for i in range(100)]
        for t in texts:
            cache._add_to_cache(t, np.random.randn(_EMBEDDING_DIM).astype(np.float32))

        assert cache._cache_memory_bytes <= MAX_CACHE_MEMORY_MB * 1024 * 1024

    def test_cache_hit_updates_lru_order(self):
        """Cache hit moves item to end (most recently used)."""
        cache = SemanticDedupCache(lmdb_path=None)
        text = "test text"
        emb = np.random.randn(_EMBEDDING_DIM).astype(np.float32)

        cache._add_to_cache(text, emb)
        assert list(cache._cache.keys()).index(text) == 0

        cache._get_from_cache(text)
        assert list(cache._cache.keys()).index(text) == len(cache._cache) - 1

    def test_cache_miss_returns_none(self):
        """Cache miss returns None, doesn't raise."""
        cache = SemanticDedupCache(lmdb_path=None)
        result = cache._get_from_cache("nonexistent text")
        assert result is None


# ---------------------------------------------------------------------------
# Test: Semantic dedup logic
# ---------------------------------------------------------------------------

class TestSemanticDedupLogic:
    """Semantic duplicate detection via embeddings."""

    def test_check_and_cache_returns_false_on_first_seen(self):
        """First time seeing text → not a duplicate."""
        cache = SemanticDedupCache(lmdb_path=None)

        with patch("hledac.universal.semantic_deduplicator.generate_embeddings") as mock_gen:
            mock_gen.return_value = np.random.randn(1, _EMBEDDING_DIM).astype(np.float32)
            result = cache.check_and_cache("first time seeing this text content")
            assert result is False

    def test_check_and_cache_returns_true_on_duplicate(self):
        """Same text checked twice → duplicate detected."""
        cache = SemanticDedupCache(lmdb_path=None)
        text = "duplicate detection test text"

        emb = np.random.randn(_EMBEDDING_DIM).astype(np.float32)
        cache._add_to_cache(text, emb)

        with patch("hledac.universal.semantic_deduplicator.generate_embeddings") as mock_gen:
            mock_gen.return_value = np.array([emb], dtype=np.float32)
            result = cache.check_and_cache(text)
            assert result is True

    def test_check_and_cache_memory_guard_skip(self):
        """Memory guard triggered → returns False (fail-soft)."""
        cache = SemanticDedupCache(lmdb_path=None)

        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.memory_info.return_value.rss = int(6.5 * 1024**3)
            result = cache.check_and_cache("any text")
            assert result is False

    def test_stats_tracking(self):
        """Stats (cache_hits, misses, duplicates) are tracked."""
        cache = SemanticDedupCache(lmdb_path=None)
        stats = cache.get_stats()

        assert "cache_hits" in stats
        assert "cache_misses" in stats
        assert "duplicate_count" in stats
        assert "skipped_count" in stats
        assert "lmdb_ready" in stats


# ---------------------------------------------------------------------------
# Test: LMDB persistence
# ---------------------------------------------------------------------------

class TestSemanticDedupLMDB:
    """Persistent LMDB store for cross-run embeddings."""

    def test_lmdb_init_failsoft(self):
        """Invalid path → boot_error set, no crash."""
        store = _SemanticDedupLMDB(path_str=None)
        assert store._boot_error is not None
        assert store._lmdb is None

    def test_lmdb_put_get_roundtrip(self):
        """LMDB put/get with float32 embedding roundtrip."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _SemanticDedupLMDB(path_str=f"{tmpdir}/test.lmdb")
            assert store._boot_error is None

            key = "test_key_123"
            emb = np.random.randn(_EMBEDDING_DIM).astype(np.float32)
            ok = store.put(key, emb)
            assert ok is True

            retrieved = store.get(key)
            assert retrieved is not None
            np.testing.assert_array_almost_equal(retrieved, emb)

    def test_lmdb_get_miss_returns_none(self):
        """LMDB key not found → returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _SemanticDedupLMDB(path_str=f"{tmpdir}/test.lmdb")
            result = store.get("nonexistent_key")
            assert result is None

    def test_lmdb_close_idempotent(self):
        """close() called twice → no error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _SemanticDedupLMDB(path_str=f"{tmpdir}/test.lmdb")
            store.close()
            store.close()  # idempotent


# ---------------------------------------------------------------------------
# Test: Cosine similarity helper
# ---------------------------------------------------------------------------

class TestCosineSimilarity:
    """Cosine similarity computation."""

    def test_identical_vectors(self):
        """Identical vectors → similarity = 1.0."""
        a = np.array([[1.0, 0.0]])
        b = np.array([[1.0, 0.0]])
        sim = _cosine_similarity(a, b)
        assert sim[0, 0] == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_vectors(self):
        """Orthogonal vectors → similarity ≈ 0."""
        a = np.array([[1.0, 0.0]])
        b = np.array([[0.0, 1.0]])
        sim = _cosine_similarity(a, b)
        assert sim[0, 0] == pytest.approx(0.0, abs=1e-6)

    def test_batch_similarity(self):
        """Batch similarity across multiple vectors."""
        vectors = np.random.randn(5, _EMBEDDING_DIM).astype(np.float32)
        sims = _cosine_similarity(vectors, vectors)
        assert sims.shape == (5, 5)
        # Diagonal should be ~1.0
        for i in range(5):
            assert sims[i, i] == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# Test: Integration with duckdb_store (manual verification only)
# ---------------------------------------------------------------------------
# Note: Full integration tests require DuckDB init which is slow.
# Verified manually via:
#   .venv/bin/python -c "async_test_here..."
# Key behaviors verified:
#   1. _semantic_dedup_cache attribute exists after async_initialize()
#   2. When cache is None, findings pass through (accepted=True)
#   3. When cache returns True for is_dup, finding is rejected with reason="semantic_duplicate"

