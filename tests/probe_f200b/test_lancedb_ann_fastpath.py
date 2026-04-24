"""
Sprint F200B — LanceDB ANN Fast Path Probe Tests
================================================

Tests verify the ANN fast path for semantic dedup:
1. _ANNIndex init (lazy, fail-soft, memory guard)
2. ann_search() returns correct structure
3. upsert() stores and retrieves embeddings
4. check_ann_duplicate() detects cross-run duplicates
5. Fail-open on init/query failure
6. Memory guard skips init above 6GB RSS

Invariant table:
  inv_1 | _ANNIndex.init() returns False and sets _boot_error on failure
  inv_2 | ann_search() returns [] when _boot_error is set
  inv_3 | upsert() stores embedding and count_rows increments
  inv_4 | check_ann_duplicate() returns False when ANN unavailable
  inv_5 | check_ann_duplicate() returns True on high-similarity match (>=0.90)
  inv_6 | memory guard skips init above _MEMORY_GUARD_GB threshold
  inv_7 | reset_ann_index() closes and nullifies the singleton
  inv_8 | semantic_deduplicator.check_and_cache() calls check_ann_duplicate()

Fast-path contract: ANN search adds cosine-similarity check over cross-run
LMDB persistence. All methods fail-open: errors return False, never raise.
"""

from __future__ import annotations

import hashlib
import time
from unittest.mock import MagicMock, patch
import pytest

import numpy as np


# ------------------------------------------------------------------\
# Helpers
# ------------------------------------------------------------------

def _make_256d_embedding(seed: int = 0) -> np.ndarray:
    """Create a deterministic 256d float32 embedding."""
    np.random.seed(seed)
    emb = np.random.randn(_EMBEDDING_DIM).astype(np.float32)
    norm = np.linalg.norm(emb) + 1e-8
    return emb / norm


_EMBEDDING_DIM = 256


def _text_hash(text: str) -> str:
    """SHA256 of text for ANN verification."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _finding_key(text: str) -> str:
    """BLAKE2b-32 key for text."""
    return hashlib.blake2b(text.encode("utf-8"), digest_size=32).hexdigest()


# ------------------------------------------------------------------\
# Test: _ANNIndex init and memory guard
# ------------------------------------------------------------------

class TestANNIndexInit:
    """inv_1: init returns False and sets _boot_error on failure."""

    def test_init_sets_boot_error_on_missing_lancedb(self):
        """
        lancedb import failure → _boot_error set, init returns False.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        with patch.dict("sys.modules", {"lancedb": None}):
            ann = _ANNIndex("/tmp/test_ann_missing_lancedb")
            result = ann.init()

        assert result is False
        assert ann._boot_error is not None

    def test_init_sets_boot_error_on_connection_failure(self):
        """
        DB connection failure → _boot_error set, init returns False.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/nonexistent/deep/path/that/cant/be/created")
        result = ann.init()

        assert result is False
        assert ann._boot_error is not None

    def test_init_idempotent_when_already_initialized(self):
        """
        Second init() call returns previous result (no re-init).
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_init_idempotent")
        r1 = ann.init()
        r2 = ann.init()
        # Both should return same result
        assert r1 == r2

    def test_init_skipped_when_memory_above_threshold(self):
        """
        RSS > 6GB → init returns False with 'memory pressure' boot_error.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_mem_guard")
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.memory_info.return_value.rss = int(7.0 * 1024**3)
            result = ann.init()

        assert result is False
        assert "memory" in str(ann._boot_error).lower()


# ------------------------------------------------------------------\
# Test: ann_search structure
# ------------------------------------------------------------------

class TestANNSearch:
    """inv_2: ann_search() returns [] when _boot_error set."""

    def test_search_returns_empty_when_boot_error(self):
        """
        With boot_error set, ann_search() returns [] (fail-open).
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_search_boot_error")
        ann._boot_error = "simulated failure"
        emb = _make_256d_embedding(42)

        results = ann.ann_search(emb, top_k=5)

        assert results == []

    def test_search_returns_list_of_dicts(self):
        """
        ann_search() returns list[dict] with keys: finding_key, text_hash, score.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_search_structure")
        ann._initialized = True  # bypass init
        ann._table = MagicMock()
        mock_results = [
            {"finding_key": "abc123", "text_hash": "xyz789", "_distance": 0.05},
            {"finding_key": "def456", "text_hash": "uvw456", "_distance": 0.10},
        ]
        mock_table = MagicMock()
        mock_table.search.return_value.metric.return_value.limit.return_value.to_list.return_value = mock_results
        ann._table = mock_table

        results = ann.ann_search(_make_256d_embedding(1), top_k=5)

        assert isinstance(results, list)
        for r in results:
            assert "finding_key" in r
            assert "text_hash" in r
            assert "score" in r
        # Scores computed from _distance
        assert results[0]["score"] == pytest.approx(0.95, abs=0.01)
        assert results[1]["score"] == pytest.approx(0.90, abs=0.01)

    def test_search_filters_below_min_score(self):
        """
        Results with score < 0.90 are filtered out.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_filter_score")
        ann._initialized = True
        mock_results = [
            {"finding_key": "lowscore", "text_hash": "hash1", "_distance": 0.30},  # score=0.70
        ]
        mock_table = MagicMock()
        mock_table.search.return_value.metric.return_value.limit.return_value.to_list.return_value = mock_results
        ann._table = mock_table

        results = ann.ann_search(_make_256d_embedding(1), top_k=5)

        # Score 0.70 < 0.90 threshold → filtered out
        assert len(results) == 0


# ------------------------------------------------------------------\
# Test: upsert
# ------------------------------------------------------------------

class TestANNUpsert:
    """inv_3: upsert() stores embedding, count_rows increments."""

    def test_upsert_returns_false_when_boot_error(self):
        """
        With boot_error, upsert() returns False (fail-open).
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_upsert_failopen")
        ann._boot_error = "simulated"
        result = ann.upsert("key", _make_256d_embedding(1), "hash")

        assert result is False

    def test_upsert_returns_true_on_success(self):
        """
        upsert() returns True when table.add() succeeds.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex("/tmp/test_ann_upsert_success")
        ann._initialized = True
        mock_table = MagicMock()
        ann._table = mock_table

        result = ann.upsert(
            _finding_key("test text"),
            _make_256d_embedding(99),
            _text_hash("test text"),
        )

        assert result is True
        mock_table.add.assert_called_once()


# ------------------------------------------------------------------\
# Test: check_ann_duplicate facade
# ------------------------------------------------------------------

class TestCheckAnnDuplicate:
    """inv_4+5: check_ann_duplicate() fail-open + detect duplicates."""

    def test_returns_false_when_ann_unavailable(self):
        """
        ANN unavailable → returns False (fail-open).
        """
        from hledac.universal.knowledge.ann_index import check_ann_duplicate, _ANNIndex, _ann_index

        # Force ANN unavailable
        old_index = _ann_index
        try:
            # Set global to None + create mock ANN with boot_error
            import hledac.universal.knowledge.ann_index as ann_mod
            ann_mod._ann_index = None

            mock_ann = MagicMock()
            mock_ann._boot_error = "forced unavailability"
            ann_mod._ann_index = mock_ann

            result = check_ann_duplicate(
                _make_256d_embedding(1),
                _text_hash("test"),
                _finding_key("test"),
            )

            assert result is False
        finally:
            ann_mod._ann_index = old_index

    def test_returns_false_on_no_match(self):
        """
        No similar embedding in ANN → returns False, upserts the embedding.
        """
        from hledac.universal.knowledge.ann_index import (
            check_ann_duplicate,
            _ANNIndex,
        )
        import hledac.universal.knowledge.ann_index as ann_mod

        mock_ann = MagicMock()
        mock_ann._boot_error = None
        mock_ann.ann_search.return_value = []  # no match
        mock_ann.upsert.return_value = True

        old_index = ann_mod._ann_index
        try:
            ann_mod._ann_index = mock_ann

            result = check_ann_duplicate(
                _make_256d_embedding(1),
                _text_hash("unique text"),
                _finding_key("unique text"),
            )

            assert result is False
            mock_ann.upsert.assert_called_once()
        finally:
            ann_mod._ann_index = old_index

    def test_returns_true_on_high_similarity_match(self):
        """
        ANN search score >= 0.90 with matching text_hash → returns True.
        """
        from hledac.universal.knowledge.ann_index import (
            check_ann_duplicate,
            _ANNIndex,
        )
        import hledac.universal.knowledge.ann_index as ann_mod

        text = "ransomware attack detected"
        key = _finding_key(text)
        hash_val = _text_hash(text)

        mock_ann = MagicMock()
        mock_ann._boot_error = None
        mock_ann.ann_search.return_value = [
            {"finding_key": key, "text_hash": hash_val, "score": 0.95}
        ]

        old_index = ann_mod._ann_index
        try:
            ann_mod._ann_index = mock_ann

            result = check_ann_duplicate(
                _make_256d_embedding(42),
                hash_val,
                key,
            )

            assert result is True
        finally:
            ann_mod._ann_index = old_index

    def test_text_hash_mismatch_prevents_false_positive(self):
        """
        Score >= 0.90 but text_hash differs → returns False (no duplicate).
        """
        from hledac.universal.knowledge.ann_index import (
            check_ann_duplicate,
            _ANNIndex,
        )
        import hledac.universal.knowledge.ann_index as ann_mod

        mock_ann = MagicMock()
        mock_ann._boot_error = None
        mock_ann.ann_search.return_value = [
            {"finding_key": "somekey", "text_hash": "different_hash", "score": 0.95}
        ]
        mock_ann.upsert.return_value = True

        old_index = ann_mod._ann_index
        try:
            ann_mod._ann_index = mock_ann

            result = check_ann_duplicate(
                _make_256d_embedding(99),
                _text_hash("original text"),
                _finding_key("original text"),
            )

            # Hash mismatch → no duplicate detected, but upsert still called
            assert result is False
            mock_ann.upsert.assert_called_once()
        finally:
            ann_mod._ann_index = old_index


# ------------------------------------------------------------------\
# Test: reset_ann_index
# ------------------------------------------------------------------

class TestResetAnnIndex:
    """inv_7: reset_ann_index() closes and nullifies singleton."""

    def test_reset_closes_and_nullifies(self):
        """
        reset_ann_index() calls close() on existing index and sets _ann_index=None.
        """
        from hledac.universal.knowledge.ann_index import reset_ann_index, _ANNIndex
        import hledac.universal.knowledge.ann_index as ann_mod

        mock_ann = MagicMock()
        old_index = ann_mod._ann_index
        ann_mod._ann_index = mock_ann

        try:
            reset_ann_index()
            mock_ann.close.assert_called_once()
            assert ann_mod._ann_index is None
        finally:
            ann_mod._ann_index = old_index


# ------------------------------------------------------------------\
# Test: semantic_deduplicator integration
# ------------------------------------------------------------------

class TestSemanticDedupIntegration:
    """inv_8: check_and_cache() calls check_ann_duplicate() in step 5."""

    def test_check_and_cache_calls_check_ann_duplicate(self):
        """
        check_and_cache() calls check_ann_duplicate() after LRU check.
        Verifies ANN fast path is integrated in the dedup flow.
        """
        from hledac.universal.semantic_deduplicator import SemanticDedupCache

        cache = SemanticDedupCache(lmdb_path=None)

        with patch(
            "hledac.universal.knowledge.ann_index.check_ann_duplicate",
            return_value=True,
        ) as mock_ann:
            with patch(
                "hledac.universal.semantic_deduplicator._generate_single_embedding",
                return_value=_make_256d_embedding(1),
            ):
                result = cache.check_and_cache("test text for ann check")

        # ANN returned duplicate=True → check_and_cache returns True
        assert result is True
        mock_ann.assert_called_once()

    def test_check_and_cache_continues_to_lmdb_on_ann_miss(self):
        """
        check_ann_duplicate returns False → check_and_cache continues to LMDB store.
        """
        from hledac.universal.semantic_deduplicator import SemanticDedupCache

        cache = SemanticDedupCache(lmdb_path=None)

        with patch(
            "hledac.universal.knowledge.ann_index.check_ann_duplicate",
            return_value=False,
        ) as mock_ann:
            with patch(
                "hledac.universal.semantic_deduplicator._generate_single_embedding",
                return_value=_make_256d_embedding(1),
            ):
                result = cache.check_and_cache("test text for ann miss")

        # ANN returned False → proceeds (no duplicate)
        assert result is False

    def test_check_and_cache_fails_open_on_ann_exception(self):
        """
        check_ann_duplicate raises → check_and_cache catches and returns False.
        """
        from hledac.universal.semantic_deduplicator import SemanticDedupCache

        cache = SemanticDedupCache(lmdb_path=None)

        with patch(
            "hledac.universal.knowledge.ann_index.check_ann_duplicate",
            side_effect=RuntimeError("ANN crashed"),
        ):
            with patch(
                "hledac.universal.semantic_deduplicator._generate_single_embedding",
                return_value=_make_256d_embedding(1),
            ):
                result = cache.check_and_cache("test text for ann exception")

        # Exception in ANN → fail-open, returns False (not rejected)
        assert result is False


# ------------------------------------------------------------------\
# Test: memory guard constants
# ------------------------------------------------------------------

class TestMemoryGuard:
    """inv_6: memory guard threshold is 6GB."""

    def test_memory_guard_constant_is_6gb(self):
        """
        _MEMORY_GUARD_GB constant = 6.0 (from ann_index.py).
        """
        from hledac.universal.knowledge.ann_index import _MEMORY_GUARD_GB

        assert _MEMORY_GUARD_GB == 6.0


# ------------------------------------------------------------------\
# Test: dimension contract
# ------------------------------------------------------------------

class TestDimensionContract:
    """ANN index uses 256d float32 (matches embedding_pipeline._EMBEDDING_DIM)."""

    def test_ann_embedding_dim_matches_pipeline(self):
        """
        _EMBEDDING_DIM in ann_index.py == embedding_pipeline._EMBEDDING_DIM (256).
        """
        from hledac.universal.knowledge.ann_index import _EMBEDDING_DIM as ANN_DIM
        from hledac.universal.embedding_pipeline import get_embedding_dimension

        assert ANN_DIM == get_embedding_dimension()
        assert ANN_DIM == 256


# ------------------------------------------------------------------\
# Benchmark contract: sub-10ms ANN path
# ------------------------------------------------------------------

class TestBenchmarkContract:
    """
    Benchmark contract: ANN fast path target <10ms on representative path.

    Note: Mocked ANN is CPU-bound fast; real-world target is <10ms for
    ANN search (top-5 cosine) on M1. Tests mock at the table layer
    to isolate ANN path from embedder latency.
    """

    def test_ann_search_latency_under_10ms_mock(self):
        """
        Mocked ann_search() completes in <10ms for top-5 query.

        Uses simple mock that returns instantly to verify the path is
        bounded and no unnecessary work happens in the search path.
        Real end-to-end (with embedder) will be higher due to MLX encode.
        """
        from hledac.universal.knowledge.ann_index import _ANNIndex
        import time

        ann = _ANNIndex("/tmp/test_ann_benchmark")
        ann._initialized = True
        mock_table = MagicMock()
        mock_table.search.return_value.metric.return_value.limit.return_value.to_list.return_value = [
            {"finding_key": "k1", "text_hash": "h1", "_distance": 0.05},
        ]
        ann._table = mock_table

        emb = _make_256d_embedding(1)

        start = time.perf_counter()
        for _ in range(100):
            ann.ann_search(emb, top_k=5)
        elapsed = (time.perf_counter() - start) / 100 * 1000  # ms per call

        assert elapsed < 10.0, f"ANN search {elapsed:.2f}ms > 10ms target (mock path)"

    def test_check_ann_duplicate_returns_in_under_1ms_mock(self):
        """
        check_ann_duplicate() mock returns in <1ms (ANN path only, no embed).

        Representative path for cross-run duplicate detection:
        embedding already computed, ANN search is the fast path check.
        """
        from hledac.universal.knowledge.ann_index import (
            check_ann_duplicate,
        )
        import hledac.universal.knowledge.ann_index as ann_mod
        import time

        mock_ann = MagicMock()
        mock_ann._boot_error = None
        mock_ann.ann_search.return_value = []  # no duplicate found
        mock_ann.upsert.return_value = True

        old_index = ann_mod._ann_index
        ann_mod._ann_index = mock_ann
        try:
            start = time.perf_counter()
            for _ in range(1000):
                check_ann_duplicate(
                    _make_256d_embedding(1),
                    _text_hash("benchmark text"),
                    _finding_key("benchmark text"),
                )
            elapsed = (time.perf_counter() - start) / 1000 * 1000  # ms per call

            assert elapsed < 1.0, f"check_ann_duplicate {elapsed:.4f}ms > 1ms target (mock)"
        finally:
            ann_mod._ann_index = old_index