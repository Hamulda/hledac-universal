"""
Sprint F197B: Semantic Dedup Write Seam Tests
==========================================

Tests verify that semantic deduplication is integrated correctly at the
canonical write seam in DuckDBShadowStore._assess_finding_quality().

Hook ordering: hash/url dedup BEFORE semantic dedup, semantic dedup BEFORE store.
Fail-open: low-memory, LMDB boot failure, or embedder error must NOT reject findings.

Invariant table:
  invariant_1 | semantic dedup called AFTER hot cache + LMDB hash dedup
  invariant_2 | semantic dedup called BEFORE _store_persistent_dedup
  invariant_3 | _store_persistent_dedup NOT called if semantic dedup rejects
  invariant_4 | finding accepted on embedder/LMDB/memory failure (fail-open)
  invariant_5 | _semantic_dedup_cache=None is handled gracefully (skip dedup)
  invariant_6 | semantic dedup cache initialized with memory guard (RSS > 6GB skips)
  invariant_7 | URL-first path short-circuits before semantic dedup (URL is identity)
  invariant_8 | semantic dedup rejects are counted in _quality_duplicate_count
"""
from __future__ import annotations

import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from hledac.universal.knowledge.duckdb_store import (
    DuckDBShadowStore,
    CanonicalFinding,
    FindingQualityDecision,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _normalize_for_quality(text: str) -> str:
    """Mirror of the module-level _normalize_for_quality for fingerprint calc."""
    import string
    stripped = text.strip()
    normalized = " ".join(stripped.split())
    whitespace_chars = set(string.whitespace)
    cleaned = "".join(
        ch for ch in normalized if ord(ch) >= 32 or ch in whitespace_chars
    )
    return cleaned


def _compute_fingerprint(text: str) -> str:
    """Compute the BLAKE2b-128 fingerprint of normalized text."""
    normalized = _normalize_for_quality(text)
    return hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()


def _make_finding(
    finding_id: str = "test-id-001",
    query: str = "test query for semantic dedup",
    payload_text: str | None = "test payload text content here",
    provenance: str = "test:source",
    source_type: str = "test_source",
    confidence: float = 0.8,
    ts: float | None = None,
) -> CanonicalFinding:
    """Create a CanonicalFinding for testing."""
    return CanonicalFinding(
        finding_id=finding_id,
        query=query,
        payload_text=payload_text,
        provenance=(provenance,),
        source_type=source_type,
        confidence=confidence,
        ts=ts or time.time(),
    )


# ------------------------------------------------------------------
# Test: Hook order — hash dedup before semantic dedup
# ------------------------------------------------------------------

class TestHookOrder:
    """invariant_1: hash/url dedup checked before semantic dedup."""

    def test_hot_cache_hit_skips_semantic_dedup(self):
        """
        When hot cache returns duplicate, semantic dedup is NEVER called.
        Verifies: hot cache check order is before semantic dedup.
        """
        store = DuckDBShadowStore()
        # Compute actual fingerprint for the test payload
        fp = _compute_fingerprint("test payload text content here")
        store._dedup_hot_cache[fp] = "prior-finding-id"

        finding = _make_finding(finding_id="new-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=False)

        decision = store._assess_finding_quality(finding)

        # Hot cache hit → rejected immediately, no semantic dedup call
        assert decision.accepted is False
        assert decision.reason == "duplicate_detected"
        store._semantic_dedup_cache.check_and_cache.assert_not_called()

    def test_lmdb_dedup_hit_skips_semantic_dedup(self):
        """
        When persistent LMDB returns duplicate, semantic dedup is NEVER called.
        Verifies: LMDB dedup check order is before semantic dedup.
        """
        store = DuckDBShadowStore()
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=False)

        with patch.object(store, "_lookup_persistent_dedup", return_value="prior-id"):
            finding = _make_finding(finding_id="new-finding")
            decision = store._assess_finding_quality(finding)

        # LMDB hit → rejected, no semantic dedup call
        assert decision.accepted is False
        store._semantic_dedup_cache.check_and_cache.assert_not_called()


# ------------------------------------------------------------------
# Test: Semantic dedup before store
# ------------------------------------------------------------------

class TestStoreDeferred:
    """invariant_2+3: LMDB store happens AFTER semantic dedup pass."""

    def test_store_not_called_if_semantic_dedup_rejects(self):
        """
        When semantic dedup detects duplicate, LMDB store is NOT called.
        Verifies: storage deferred until after semantic dedup decision.
        """
        store = DuckDBShadowStore()
        finding = _make_finding(finding_id="dup-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=True)

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    decision = store._assess_finding_quality(finding)

        # Semantic duplicate → rejected, no store call
        assert decision.accepted is False
        assert decision.reason == "semantic_duplicate"
        mock_store.assert_not_called()
        mock_add.assert_not_called()

    def test_store_called_only_after_semantic_dedup_passes(self):
        """
        When semantic dedup passes, LMDB store IS called.
        Verifies: storage happens after semantic dedup confirms non-duplicate.
        """
        store = DuckDBShadowStore()
        finding = _make_finding(finding_id="new-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=False)

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    decision = store._assess_finding_quality(finding)

        # Non-duplicate → store called
        assert decision.accepted is True
        assert decision.reason is None
        mock_store.assert_called_once()
        mock_add.assert_called_once()


# ------------------------------------------------------------------
# Test: Fail-open on embedder/LMDB/memory failure
# ------------------------------------------------------------------

class TestFailOpen:
    """invariant_4: finding accepted despite embedder/LMDB/memory failure."""

    def test_embedder_error_fails_open(self):
        """
        Embedder exception → finding accepted (not rejected).
        Verifies: any embedder error returns duplicate=False and proceeds.
        """
        store = DuckDBShadowStore()
        finding = _make_finding(finding_id="test-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(
            side_effect=RuntimeError("embedder crashed")
        )

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    decision = store._assess_finding_quality(finding)

        # Embedder error → fail-open, finding accepted, store called
        assert decision.accepted is True
        mock_store.assert_called_once()
        mock_add.assert_called_once()

    def test_lmdb_persistence_error_fails_open(self):
        """
        LMDB persistence error in semantic dedup → finding accepted.
        Verifies: LMDB error in check_and_cache doesn't block the finding.
        """
        store = DuckDBShadowStore()
        finding = _make_finding(finding_id="test-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(
            side_effect=IOError("LMDB write failed")
        )

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    decision = store._assess_finding_quality(finding)

        # LMDB error → fail-open, finding accepted
        assert decision.accepted is True
        mock_store.assert_called_once()
        mock_add.assert_called_once()

    def test_cache_none_skips_semantic_dedup(self):
        """
        _semantic_dedup_cache=None (memory pressure) → finding accepted.
        Verifies: None cache means skip, not reject.
        """
        store = DuckDBShadowStore()
        finding = _make_finding(finding_id="test-finding")
        store._semantic_dedup_cache = None  # memory pressure case

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    decision = store._assess_finding_quality(finding)

        # None cache → skip semantic dedup, accept immediately
        assert decision.accepted is True
        mock_store.assert_called_once()
        mock_add.assert_called_once()


# ------------------------------------------------------------------
# Test: Counter tracking for semantic duplicates
# ------------------------------------------------------------------

class TestCounters:
    """invariant_8: _quality_duplicate_count incremented on semantic dedup reject."""

    def test_quality_duplicate_count_incremented_on_semantic_reject(self):
        """
        Semantic duplicate rejection → _quality_duplicate_count incremented.
        """
        store = DuckDBShadowStore()
        initial_count = store._quality_duplicate_count
        finding = _make_finding(finding_id="dup-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=True)

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                decision = store._assess_finding_quality(finding)

        assert decision.accepted is False
        assert store._quality_duplicate_count == initial_count + 1

    def test_quality_duplicate_count_not_incremented_on_semantic_pass(self):
        """
        Semantic dedup pass → _quality_duplicate_count NOT incremented.
        """
        store = DuckDBShadowStore()
        initial_count = store._quality_duplicate_count
        finding = _make_finding(finding_id="new-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=False)

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                decision = store._assess_finding_quality(finding)

        assert decision.accepted is True
        assert store._quality_duplicate_count == initial_count


# ------------------------------------------------------------------
# Test: Semantic dedup cache memory guard initialization
# ------------------------------------------------------------------

class TestSemanticDedupInit:
    """invariant_6: _init_semantic_dedup_cache respects RSS memory guard."""

    def test_init_skipped_above_6gb_rss(self):
        """
        RSS > 6GB → _semantic_dedup_cache set to None.
        Verifies: memory guard in _init_semantic_dedup_cache.
        """
        store = DuckDBShadowStore()
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.memory_info.return_value.rss = int(6.5 * 1024**3)
            store._init_semantic_dedup_cache()

        assert store._semantic_dedup_cache is None
        assert "memory pressure" in store._semantic_dedup_boot_error

    def test_init_skipped_on_lmdb_boot_failure(self):
        """
        LMDB boot failure → cache None, boot_error stored.
        Verifies: fail-soft init stores error without crashing.
        """
        store = DuckDBShadowStore()
        with patch("psutil.Process") as mock_proc:
            mock_proc.return_value.memory_info.return_value.rss = int(4.0 * 1024**3)
            with patch(
                "hledac.universal.semantic_deduplicator.SemanticDedupCache",
                side_effect=RuntimeError("LMDB boot failed"),
            ):
                store._init_semantic_dedup_cache()


        assert store._semantic_dedup_cache is None
        assert store._semantic_dedup_boot_error == "LMDB boot failed"


# ------------------------------------------------------------------
# Test: FindingQualityDecision structure
# ------------------------------------------------------------------

class TestFindingQualityDecision:
    """Decision has correct fields for all rejection reasons."""

    def test_semantic_duplicate_decision_fields(self):
        """reason=semantic_duplicate → duplicate=True in decision."""
        store = DuckDBShadowStore()
        finding = _make_finding(finding_id="test-finding")
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=True)

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                decision = store._assess_finding_quality(finding)

        assert decision.accepted is False
        assert decision.reason == "semantic_duplicate"
        assert decision.duplicate is True
        assert decision.normalized_hash is not None


# ------------------------------------------------------------------
# Test: URL-first path bypasses semantic dedup
# ------------------------------------------------------------------

class TestURLFirstPath:
    """invariant_7: URL-first findings short-circuit before semantic dedup check."""

    def test_url_fingerprint_short_circuits(self):
        """
        URL-based findings skip semantic dedup (URL is identity).
        Note: existing URL-first behavior — semantic dedup is still checked
        for the normal (non-URL) path only.
        """
        store = DuckDBShadowStore()
        finding = _make_finding(
            finding_id="url-finding",
            provenance="http://example.com/page",
        )
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(
            side_effect=RuntimeError("should not be called")
        )

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    decision = store._assess_finding_quality(finding)

        # URL-first → accepted immediately, no semantic dedup
        assert decision.accepted is True
        store._semantic_dedup_cache.check_and_cache.assert_not_called()
        mock_store.assert_called_once()


# ------------------------------------------------------------------
# Test: Batch ingest also respects hook order
# ------------------------------------------------------------------

class TestBatchIngestHookOrder:
    """Batch ingest path also defers store until after semantic dedup pass."""

    @pytest.mark.asyncio
    async def test_batch_all_passing_accepted(self):
        """Batch where all items pass semantic dedup → all stored."""
        store = DuckDBShadowStore()
        findings = [_make_finding(finding_id=f"batch-{i}") for i in range(3)]
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(return_value=False)

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup"), \
                     patch.object(store, "_add_to_hot_cache"):
                    decisions = [store._assess_finding_quality(f) for f in findings]

        # All pass semantic dedup → all accepted
        for d in decisions:
            assert d.accepted is True

    @pytest.mark.asyncio
    async def test_batch_first_item_semantic_dup_rejected(self):
        """First item semantic duplicate → rejected, not stored."""
        store = DuckDBShadowStore()
        findings = [_make_finding(finding_id=f"batch-{i}") for i in range(3)]
        store._semantic_dedup_cache = MagicMock()
        store._semantic_dedup_cache.check_and_cache = MagicMock(
            side_effect=[True, False, False]
        )

        with patch.object(store, "_hot_cache_lookup", return_value=None):
            with patch.object(store, "_lookup_persistent_dedup", return_value=None):
                with patch.object(store, "_store_persistent_dedup") as mock_store, \
                     patch.object(store, "_add_to_hot_cache") as mock_add:
                    d0 = store._assess_finding_quality(findings[0])
                    d1 = store._assess_finding_quality(findings[1])
                    d2 = store._assess_finding_quality(findings[2])

        # First is rejected
        assert d0.accepted is False
        assert d0.reason == "semantic_duplicate"
        # Store NOT called for first (rejected)
        assert mock_store.call_count == 2  # only d1, d2 stored
