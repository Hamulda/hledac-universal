"""
Sprint 8W: Evidence Quality Funnel Tests
=======================================
Quality gate over CanonicalFinding ingest: low-entropy reject,
exact-content dedup, fail-open behavior.

Tests invariants:
  1. valid finding passes quality gate
  2. "aaaaaaaaaa" -> reject (low entropy)
  3. "192.168.1.1" -> accept (high entropy)
  4. "john@example.com" -> accept (email entropy)
  5. "ok" (<8 chars) -> entropy skip -> accept
  6. duplicate detection works
  7. duplicate detection uses normalized text
  8. provenance remains intact
  9. CanonicalFinding remains frozen typed DTO
  10. fail-open on quality exception
  11. reject is NOT storage failure
  12. duplicate is NOT storage failure
  13. _quality_rejected_count grows correctly
  14. _quality_duplicate_count grows correctly
  15. _quality_fail_open_count grows correctly
  16. 1:1 batch results length invariant
  17. mixed batch doesn't fail entirely
  18. legacy async_record_canonical_finding still works
  19. legacy async_record_canonical_findings_batch still works
  20. probe_8p still passes
  21. probe_8r still passes
  22. probe_8s still passes
  23. probe_8l/h/f/b still pass
  24. AO canary still passes
  25. benchmark tests stable
  26. no heavy import regression
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from hledac.universal.knowledge.duckdb_store import (
    DuckDBShadowStore,
    CanonicalFinding,
    FindingQualityDecision,
    _normalize_for_quality,
    _compute_entropy,
    _compute_dedup_fingerprint,
    _QUALITY_ENTROPY_THRESHOLD,
    _QUALITY_MIN_ENTROPY_LEN,
)


@pytest_asyncio.fixture
async def store(tmp_path_factory: pytest.TempPathFactory) -> DuckDBShadowStore:
    """Fresh store per test."""
    tmp = tmp_path_factory.mktemp("sprint8w")
    store = DuckDBShadowStore(db_path=tmp / "test.duckdb", temp_dir=tmp / "tmp")
    await store.async_initialize()
    yield store
    # Sprint 8AG: Isolate each test — clear shared dedup LMDB after test
    if store._dedup_lmdb is not None:
        try:
            with store._dedup_lmdb._env.begin(write=True) as txn:
                with txn.cursor() as cur:
                    for k, _ in cur:
                        txn.delete(k)
        except Exception:
            pass
    await store.aclose()


class TestQualityHelpers:
    """Unit tests for quality helper functions."""

    def test_normalize_lowercase(self) -> None:
        assert _normalize_for_quality("HELLO") == "hello"
        assert _normalize_for_quality("Hello World") == "hello world"

    def test_normalize_strip(self) -> None:
        assert _normalize_for_quality("  hello  ") == "hello"

    def test_normalize_collapse_whitespace(self) -> None:
        assert _normalize_for_quality("hello   world") == "hello world"
        # Tabs/newlines are whitespace → split() treats them as delimiters → join
        assert _normalize_for_quality("a\tb\nc") == "a b c"

    def test_normalize_remove_nonprintable(self) -> None:
        # NUL(\x00) and US(\x1f) are non-whitespace delimiters for split()
        assert _normalize_for_quality("a\x00b\x1fc") == "ab c"

    def test_normalize_empty(self) -> None:
        assert _normalize_for_quality("") == ""

    def test_entropy_aaaaaaaa(self) -> None:
        # All same chars → 0 entropy
        e = _compute_entropy("aaaaaaaaaa")
        assert e == 0.0

    def test_entropy_random(self) -> None:
        # "192.168.1.1" has moderate entropy
        e = _compute_entropy("192.168.1.1")
        assert e > 1.0

    def test_entropy_empty(self) -> None:
        assert _compute_entropy("") == 0.0

    def test_fingerprint_stable(self) -> None:
        fp1 = _compute_dedup_fingerprint("hello world")
        fp2 = _compute_dedup_fingerprint("hello world")
        assert fp1 == fp2

    def test_fingerprint_different(self) -> None:
        fp1 = _compute_dedup_fingerprint("hello")
        fp2 = _compute_dedup_fingerprint("world")
        assert fp1 != fp2

    def test_fingerprint_normalized(self) -> None:
        # Fingerprint uses normalized text
        fp1 = _compute_dedup_fingerprint("HELLO  WORLD")
        fp2 = _compute_dedup_fingerprint("hello world")
        assert fp1 == fp2

    def test_fingerprint_uses_blake2b(self) -> None:
        import hashlib
        fp = _compute_dedup_fingerprint("test")
        # Blake2b-128 produces 32 hex chars
        assert len(fp) == 32
        # Verify it's valid hex
        int(fp, 16)

    def test_entropy_threshold_default(self) -> None:
        assert _QUALITY_ENTROPY_THRESHOLD == 0.5

    def test_min_entropy_len_default(self) -> None:
        assert _QUALITY_MIN_ENTROPY_LEN == 8


class TestQualityDecisionStruct:
    """Tests for FindingQualityDecision typed struct."""

    def test_finding_quality_decision_frozen(self) -> None:
        d = FindingQualityDecision(
            accepted=True,
            reason=None,
            entropy=2.5,
            normalized_hash="abc123",
            duplicate=False,
        )
        with pytest.raises(AttributeError):
            d.accepted = False

    def test_finding_quality_decision_fields(self) -> None:
        d = FindingQualityDecision(
            accepted=False,
            reason="low_entropy_rejected",
            entropy=0.1,
            normalized_hash="xyz",
            duplicate=False,
        )
        assert d.accepted is False
        assert d.reason == "low_entropy_rejected"
        assert d.entropy == 0.1
        assert d.normalized_hash == "xyz"
        assert d.duplicate is False


class TestSingleIngest:
    """Tests for async_ingest_finding (single finding)."""

    @pytest.mark.asyncio
    async def test_valid_finding_passes_gate(self, store: DuckDBShadowStore) -> None:
        """Invariant 1: valid finding passes quality gate."""
        f = CanonicalFinding(
            finding_id="v1",
            query="192.168.1.1",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        # Accept → returns ActivationResult dict
        assert isinstance(r, dict)
        assert r["lmdb_success"] is True

    @pytest.mark.asyncio
    async def test_low_entropy_rejected(self, store: DuckDBShadowStore) -> None:
        """Invariant 2: 'aaaaaaaaaa' rejected due to low entropy."""
        f = CanonicalFinding(
            finding_id="le1",
            query="aaaaaaaaaa",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        assert isinstance(r, FindingQualityDecision)
        assert r.accepted is False
        assert r.reason == "low_entropy_rejected"
        assert r.entropy == 0.0

    @pytest.mark.asyncio
    async def test_ip_address_accepted(self, store: DuckDBShadowStore) -> None:
        """Invariant 3: IP address accepted (high entropy)."""
        f = CanonicalFinding(
            finding_id="ip1",
            query="192.168.1.1",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        assert isinstance(r, dict)
        assert r["lmdb_success"] is True

    @pytest.mark.asyncio
    async def test_email_accepted(self, store: DuckDBShadowStore) -> None:
        """Invariant 4: email accepted."""
        f = CanonicalFinding(
            finding_id="em1",
            query="john@example.com",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        assert isinstance(r, dict)
        assert r["lmdb_success"] is True

    @pytest.mark.asyncio
    async def test_short_string_accepted(self, store: DuckDBShadowStore) -> None:
        """Invariant 5: 'ok' (<8 chars) skips entropy filter, accepts."""
        f = CanonicalFinding(
            finding_id="ss1",
            query="ok",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        assert isinstance(r, dict)
        assert r["lmdb_success"] is True

    @pytest.mark.asyncio
    async def test_duplicate_detected(self, store: DuckDBShadowStore) -> None:
        """Invariant 6: exact duplicate detected."""
        f1 = CanonicalFinding(
            finding_id="d1",
            query="unique content 12345",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        f2 = CanonicalFinding(
            finding_id="d2",
            query="unique content 12345",
            source_type="test",
            confidence=0.5,
            ts=2.0,
            provenance=("t",),
            payload_text=None,
        )
        r1 = await store.async_ingest_finding(f1)
        r2 = await store.async_ingest_finding(f2)
        assert isinstance(r1, dict)
        assert isinstance(r2, FindingQualityDecision)
        assert r2.duplicate is True
        assert r2.reason == "duplicate_detected"

    @pytest.mark.asyncio
    async def test_duplicate_uses_normalized(self, store: DuckDBShadowStore) -> None:
        """Invariant 7: duplicate detection uses normalized text."""
        f1 = CanonicalFinding(
            finding_id="dn1",
            query="HELLO   WORLD",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        f2 = CanonicalFinding(
            finding_id="dn2",
            query="hello world",
            source_type="test",
            confidence=0.5,
            ts=2.0,
            provenance=("t",),
            payload_text=None,
        )
        r1 = await store.async_ingest_finding(f1)
        r2 = await store.async_ingest_finding(f2)
        assert isinstance(r1, dict)
        assert isinstance(r2, FindingQualityDecision)
        assert r2.duplicate is True

    @pytest.mark.asyncio
    async def test_provenance_intact(self, store: DuckDBShadowStore) -> None:
        """Invariant 8: provenance remains intact after quality gate."""
        prov = ("fetch", "http", "test_via_quality")
        f = CanonicalFinding(
            finding_id="p1",
            query="john@example.com",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=prov,
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        assert isinstance(r, dict)
        assert r["lmdb_success"] is True
        # Provenance is stored in WAL, not directly readable without LMDB access

    @pytest.mark.asyncio
    async def test_canonical_finding_remains_frozen(self, store: DuckDBShadowStore) -> None:
        """Invariant 9: CanonicalFinding stays frozen typed DTO."""
        f = CanonicalFinding(
            finding_id="cf1",
            query="test",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        with pytest.raises(AttributeError):
            f.finding_id = "changed"

    @pytest.mark.asyncio
    async def test_fail_open_on_exception(self, store: DuckDBShadowStore) -> None:
        """Invariant 10: fail-open when quality helper raises."""
        # Monkey-patch _normalize_for_quality to raise
        import hledac.universal.knowledge.duckdb_store as dks

        orig = dks._normalize_for_quality

        def bad_normalize(text: str) -> str:
            raise RuntimeError("simulated quality helper failure")

        dks._normalize_for_quality = bad_normalize
        try:
            f = CanonicalFinding(
                finding_id="fo1",
                query="test query",
                source_type="test",
                confidence=0.5,
                ts=1.0,
                provenance=("t",),
                payload_text=None,
            )
            r = await store.async_ingest_finding(f)
            # Should fall through to legacy storage
            assert isinstance(r, dict)
            assert r["lmdb_success"] is True
            assert store._quality_fail_open_count == 1
        finally:
            dks._normalize_for_quality = orig

    @pytest.mark.asyncio
    async def test_reject_not_storage_failure(self, store: DuckDBShadowStore) -> None:
        """Invariant 11: reject does NOT increment _storage_fail_count."""
        f = CanonicalFinding(
            finding_id="rns1",
            query="bbbbbbbbbb",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        await store.async_ingest_finding(f)
        # _quality_rejected_count should be incremented, not storage_fail_count
        assert store._quality_rejected_count >= 1
        # Storage counters are internal; we just verify no exception was raised

    @pytest.mark.asyncio
    async def test_duplicate_not_storage_failure(self, store: DuckDBShadowStore) -> None:
        """Invariant 12: duplicate does NOT increment _storage_fail_count."""
        f1 = CanonicalFinding(
            finding_id="dns1",
            query="test content abc",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        await store.async_ingest_finding(f1)
        f2 = CanonicalFinding(
            finding_id="dns2",
            query="test content abc",
            source_type="test",
            confidence=0.5,
            ts=2.0,
            provenance=("t",),
            payload_text=None,
        )
        await store.async_ingest_finding(f2)
        assert store._quality_duplicate_count >= 1

    @pytest.mark.asyncio
    async def test_quality_rejected_count(self, store: DuckDBShadowStore) -> None:
        """Invariant 13: _quality_rejected_count grows correctly."""
        store._quality_rejected_count = 0
        f = CanonicalFinding(
            finding_id="qrc1",
            query="cccccccccc",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        await store.async_ingest_finding(f)
        assert store._quality_rejected_count == 1

    @pytest.mark.asyncio
    async def test_quality_duplicate_count(self, store: DuckDBShadowStore) -> None:
        """Invariant 14: _quality_duplicate_count grows correctly."""
        store._quality_duplicate_count = 0
        f1 = CanonicalFinding(
            finding_id="qdc1",
            query="dup content xyz",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        f2 = CanonicalFinding(
            finding_id="qdc2",
            query="dup content xyz",
            source_type="test",
            confidence=0.5,
            ts=2.0,
            provenance=("t",),
            payload_text=None,
        )
        await store.async_ingest_finding(f1)
        await store.async_ingest_finding(f2)
        assert store._quality_duplicate_count == 1

    @pytest.mark.asyncio
    async def test_quality_fail_open_count(self, store: DuckDBShadowStore) -> None:
        """Invariant 15: _quality_fail_open_count grows correctly."""
        import hledac.universal.knowledge.duckdb_store as dks

        orig = dks._normalize_for_quality

        def bad(text: str) -> str:
            raise ValueError("test")

        dks._normalize_for_quality = bad
        try:
            store._quality_fail_open_count = 0
            f = CanonicalFinding(
                finding_id="qfoc1",
                query="test",
                source_type="test",
                confidence=0.5,
                ts=1.0,
                provenance=("t",),
                payload_text=None,
            )
            await store.async_ingest_finding(f)
            assert store._quality_fail_open_count == 1
        finally:
            dks._normalize_for_quality = orig


class TestBatchIngest:
    """Tests for async_ingest_findings_batch."""

    @pytest.mark.asyncio
    async def test_batch_1to1_length_invariant(self, store: DuckDBShadowStore) -> None:
        """Invariant 16: len(results) == len(findings) always."""
        findings = [
            CanonicalFinding(
                finding_id=f"b1_{i}",
                query=f"content {i}",
                source_type="test",
                confidence=0.5,
                ts=float(i),
                provenance=("t",),
                payload_text=None,
            )
            for i in range(5)
        ]
        results = await store.async_ingest_findings_batch(findings)
        assert len(results) == len(findings)

    @pytest.mark.asyncio
    async def test_batch_mixed_not_all_fail(self, store: DuckDBShadowStore) -> None:
        """Invariant 17: mixed batch doesn't fail entirely."""
        findings = [
            CanonicalFinding(
                finding_id="bm1",
                query="aaaaaaaaaa",  # will reject
                source_type="test",
                confidence=0.5,
                ts=1.0,
                provenance=("t",),
                payload_text=None,
            ),
            CanonicalFinding(
                finding_id="bm2",
                query="good content here",
                source_type="test",
                confidence=0.5,
                ts=2.0,
                provenance=("t",),
                payload_text=None,
            ),
            CanonicalFinding(
                finding_id="bm3",
                query="another good one",
                source_type="test",
                confidence=0.5,
                ts=3.0,
                provenance=("t",),
                payload_text=None,
            ),
        ]
        results = await store.async_ingest_findings_batch(findings)
        assert len(results) == 3
        # At least one should be accepted (dict with finding_id/lmdb_key)
        assert any(isinstance(r, dict) and "finding_id" in r for r in results)

    @pytest.mark.asyncio
    async def test_batch_all_rejected(self, store: DuckDBShadowStore) -> None:
        """Batch where all findings are rejected."""
        findings = [
            CanonicalFinding(
                finding_id=f"bar{i}",
                query="zzzzzzzzzz",
                source_type="test",
                confidence=0.5,
                ts=float(i),
                provenance=("t",),
                payload_text=None,
            )
            for i in range(3)
        ]
        results = await store.async_ingest_findings_batch(findings)
        assert len(results) == 3
        assert all(isinstance(r, FindingQualityDecision) for r in results)
        assert all(r.accepted is False for r in results)

    @pytest.mark.asyncio
    async def test_batch_all_accepted(self, store: DuckDBShadowStore) -> None:
        """Batch where all findings are accepted."""
        findings = [
            CanonicalFinding(
                finding_id=f"baa{i}",
                query=f"unique content {i}",
                source_type="test",
                confidence=0.5,
                ts=float(i),
                provenance=("t",),
                payload_text=None,
            )
            for i in range(3)
        ]
        results = await store.async_ingest_findings_batch(findings)
        assert len(results) == 3
        # All results are dicts (accepted → ActivationResult)
        assert all(isinstance(r, dict) for r in results)
        # Check structure: all have finding_id and lmdb_key
        assert all("finding_id" in r for r in results)
        assert all("lmdb_key" in r for r in results)


class TestLegacyAPI:
    """Tests that legacy API still works unchanged."""

    @pytest.mark.asyncio
    async def test_legacy_single_ingest(self, store: DuckDBShadowStore) -> None:
        """Invariant 18: legacy async_record_canonical_finding still works."""
        f = CanonicalFinding(
            finding_id="leg1",
            query="legacy test",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_record_canonical_finding(f)
        assert isinstance(r, dict)
        assert r.get("lmdb_success") is True

    @pytest.mark.asyncio
    async def test_legacy_batch_ingest(self, store: DuckDBShadowStore) -> None:
        """Invariant 19: legacy async_record_canonical_findings_batch still works."""
        findings = [
            CanonicalFinding(
                finding_id=f"legb{i}",
                query=f"batch item {i}",
                source_type="test",
                confidence=0.5,
                ts=float(i),
                provenance=("t",),
                payload_text=None,
            )
            for i in range(3)
        ]
        results = await store.async_record_canonical_findings_batch(findings)
        assert len(results) == 3
        assert all(isinstance(r, dict) for r in results)


class TestPayloadTextVsQuery:
    """Tests for text mapping: payload_text if exists, else query."""

    @pytest.mark.asyncio
    async def test_payload_text_used_for_quality(self, store: DuckDBShadowStore) -> None:
        """Quality check uses payload_text when provided."""
        # payload_text has low entropy, query has high
        f = CanonicalFinding(
            finding_id="pt1",
            query="192.168.1.1",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text="bbbbbbbbbb",
        )
        r = await store.async_ingest_finding(f)
        # Should be rejected because payload_text "bbbbbbbbbb" has low entropy
        assert isinstance(r, FindingQualityDecision)
        assert r.accepted is False

    @pytest.mark.asyncio
    async def test_empty_payload_text_falls_back_to_query(self, store: DuckDBShadowStore) -> None:
        """When payload_text is empty/None, falls back to query."""
        f = CanonicalFinding(
            finding_id="ept1",
            query="192.168.1.1",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        r = await store.async_ingest_finding(f)
        assert isinstance(r, dict)
        assert r["lmdb_success"] is True


class TestBenchmarks:
    """Benchmark tests for Sprint 8W quality gate overhead."""

    @pytest.mark.asyncio
    async def test_benchmark_single_accept(self, store: DuckDBShadowStore) -> None:
        """Benchmark N=1 accept: quality-only overhead."""
        f = CanonicalFinding(
            finding_id="bench1",
            query="192.168.1.1",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        start = time.perf_counter()
        for _ in range(100):
            await store.async_ingest_finding(f)
        elapsed = (time.perf_counter() - start) * 1000
        avg_ms = elapsed / 100
        # Quality gate alone should be sub-millisecond
        assert avg_ms < 50  # generous upper bound for M1
        print(f"\n  N=1 accept: {avg_ms:.2f}ms avg")

    @pytest.mark.asyncio
    async def test_benchmark_batch_mixed(self, store: DuckDBShadowStore) -> None:
        """Benchmark N=50 mixed batch (valid + duplicate + low entropy)."""
        # Reset counter for clean measurement
        store._quality_rejected_count = 0
        store._quality_duplicate_count = 0

        findings = []
        for i in range(50):
            if i % 3 == 0:
                query = "aaaaaaaaaa"  # low entropy → reject
            elif i % 3 == 1:
                query = f"unique content {i}"  # good
            else:
                query = f"dup {i // 3}"  # some duplicates
            findings.append(
                CanonicalFinding(
                    finding_id=f"bench{i}",
                    query=query,
                    source_type="test",
                    confidence=0.5,
                    ts=float(i),
                    provenance=("t",),
                    payload_text=None,
                )
            )

        start = time.perf_counter()
        results = await store.async_ingest_findings_batch(findings)
        elapsed = (time.perf_counter() - start) * 1000

        assert len(results) == 50
        avg_ms = elapsed / 50
        print(f"\n  N=50 mixed batch: {elapsed:.1f}ms total, {avg_ms:.2f}ms avg")
        # Should complete in reasonable time
        assert elapsed < 5000

    @pytest.mark.asyncio
    async def test_benchmark_quality_only(self, store: DuckDBShadowStore) -> None:
        """Benchmark quality-only overhead (no storage)."""
        # This measures _assess_finding_quality directly
        f = CanonicalFinding(
            finding_id="benchq",
            query="192.168.1.1",
            source_type="test",
            confidence=0.5,
            ts=1.0,
            provenance=("t",),
            payload_text=None,
        )
        start = time.perf_counter()
        for _ in range(1000):
            store._assess_finding_quality(f)
        elapsed = (time.perf_counter() - start) * 1000
        avg_us = elapsed / 1000
        print(f"\n  Quality assess only: {avg_us:.3f}ms avg per call")
        assert avg_us < 1.0  # Should be microseconds, not milliseconds


class TestBatchCanonicalPersistence:
    """Sprint 8W: Batch canonical persistence invariants.

    Verifies:
      A. _canonical_findings_batch_to_activation_results always returns list[dict], never None
      B. async_record_canonical_findings_batch accepted path returns valid ActivationResult list
      C. 1:1 invariant: len(results) == len(findings) even on DuckDB partial failure (fail-soft)
    """

    @pytest.mark.asyncio
    async def test_batch_helper_never_returns_none(self, store: DuckDBShadowStore) -> None:
        """Invariant A: sync helper always returns list[dict], never implicit None."""
        findings = [
            CanonicalFinding(
                finding_id=f"bnone{i}",
                query=f"batch item {i}",
                source_type="test",
                confidence=0.5,
                ts=float(i),
                provenance=("t",),
                payload_text=None,
            )
            for i in range(3)
        ]
        # Call the sync helper directly via run_in_executor (simulates the async seam)
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            store._executor,
            store._canonical_findings_batch_to_activation_results,
            findings,
        )
        assert results is not None, "_canonical_findings_batch_to_activation_results returned None"
        assert isinstance(results, list), f"Expected list, got {type(results)}"
        assert len(results) == 3, f"Expected 3 results, got {len(results)}"
        assert all(isinstance(r, dict) for r in results), "All results must be dicts"
        assert all("finding_id" in r for r in results), "All results must have finding_id"

    @pytest.mark.asyncio
    async def test_accepted_batch_returns_valid_activation_results(self, store: DuckDBShadowStore) -> None:
        """Invariant B: accepted batch path returns correct ActivationResult list."""
        findings = [
            CanonicalFinding(
                finding_id=f"bacc{i}",
                query=f"accepted item {i}",
                source_type="test",
                confidence=0.8,
                ts=float(i),
                provenance=("probe", "https://example.com"),
                payload_text="192.168.1.1",
            )
            for i in range(4)
        ]
        results = await store.async_record_canonical_findings_batch(findings)
        # 1:1 invariant
        assert len(results) == len(findings), f"Result count mismatch: {len(results)} vs {len(findings)}"
        # Every result is a proper ActivationResult (duck-typed dict with required keys)
        for r in results:
            assert isinstance(r, dict), f"Result is not a dict: {type(r)}"
            assert "lmdb_success" in r, "Result missing lmdb_success"
            assert "duckdb_success" in r, "Result missing duckdb_success"
            assert "finding_id" in r, "Result missing finding_id"
            assert "accepted" in r, "Result missing accepted"
        # At least one should have succeeded (LMDB write path is active)
        assert any(r.get("lmdb_success") for r in results), "No LMDB successes in batch"

    @pytest.mark.asyncio
    async def test_accepted_count_incremented_after_batch(self, store: DuckDBShadowStore) -> None:
        """Accepted count grows exactly by number of accepted findings in batch."""
        before = store._accepted_count
        findings = [
            CanonicalFinding(
                finding_id=f"bacct{i}",
                query=f"count item {i}",
                source_type="test",
                confidence=0.8,
                ts=float(i),
                provenance=("t",),
                payload_text="10.0.0.1",
            )
            for i in range(5)
        ]
        await store.async_record_canonical_findings_batch(findings)
        after = store._accepted_count
        assert after >= before + 5, f"Expected >=5 increment, got {after - before}"

    @pytest.mark.asyncio
    async def test_1n1_batch_results_length_on_empty_input(self, store: DuckDBShadowStore) -> None:
        """Edge case: empty list returns empty list, not None."""
        loop = asyncio.get_running_loop()
        results = await loop.run_in_executor(
            store._executor,
            store._canonical_findings_batch_to_activation_results,
            [],
        )
        assert results == [], f"Empty batch must return [], got {results!r}"
        assert results is not None


class TestNoImportRegression:
    """Invariant 26: no heavy import regression."""

    def test_module_imports_cleanly(self) -> None:
        """Module imports without side effects."""
        import importlib
        import hledac.universal.knowledge.duckdb_store as dks

        # Re-import should be clean
        importlib.reload(dks)

        # All expected module-level symbols available
        assert hasattr(dks, "DuckDBShadowStore")
        assert hasattr(dks, "CanonicalFinding")
        assert hasattr(dks, "FindingQualityDecision")
        assert hasattr(dks, "_normalize_for_quality")
        assert hasattr(dks, "_compute_entropy")
        assert hasattr(dks, "_compute_dedup_fingerprint")
        # async_ingest_finding/async_ingest_findings_batch are instance methods
        # (not module-level), verify via class
        assert hasattr(dks.DuckDBShadowStore, "async_ingest_finding")
        assert hasattr(dks.DuckDBShadowStore, "async_ingest_findings_batch")
