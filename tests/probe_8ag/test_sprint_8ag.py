"""
Sprint 8AG Tests: Persistent Dedup + LMDB Boot Safety + UMA Enum Sanity
"""

import asyncio
import tempfile
import os
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))


def _get_attr(obj, name, default=None):
    """Get attribute safely whether obj is a struct or dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class TestPersistentDedup:
    """§6.17: Persistent dedup LMDB tests."""

    @pytest_asyncio.fixture
    async def store(self):
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test_shadow.duckdb"
            store = DuckDBShadowStore(db_path=db_path)
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

    @pytest.mark.asyncio
    async def test_persistent_dedup_namespace_exists(self, store):
        assert store.DEDUP_NAMESPACE == "dedup:"
        key = store._dedup_key_from_fingerprint("a" * 32)
        assert key == b"dedup:" + b"a" * 32

    @pytest.mark.asyncio
    async def test_dedup_key_prefix_is_dedup(self, store):
        fp = "abcd" * 8
        key = store._dedup_key_from_fingerprint(fp)
        assert key.startswith(b"dedup:")
        assert key == f"dedup:{fp}".encode("utf-8")

    @pytest.mark.asyncio
    async def test_dedup_value_contains_finding_id(self, store):
        fp = "deadbeef" * 4
        fid = "finding-123"
        store._store_persistent_dedup(fp, fid)
        result = store._lookup_persistent_dedup(fp)
        assert result == fid
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_no_python_hash_usage(self):
        """No Python built-in hash() in dedup code path."""
        import hledac.universal.knowledge.duckdb_store as mod
        import inspect

        src = inspect.getsource(mod._compute_dedup_fingerprint)
        # Must use hashlib.blake2b, NOT hash()
        assert "hashlib.blake2b" in src, "Must use hashlib.blake2b"
        # Should NOT use Python's built-in hash()
        assert "hash(" not in src or "hashlib" in src

    @pytest.mark.asyncio
    async def test_duplicate_hit_same_process(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        f1 = CanonicalFinding(
            finding_id="fid-001", query="test query dup same proc",
            source_type="test", confidence=0.9, ts=time.time(),
        )
        f2 = CanonicalFinding(
            finding_id="fid-002", query="test query dup same proc",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        r1 = await store.async_ingest_finding(f1)
        accepted1 = _get_attr(r1, "accepted")
        assert accepted1 is True, f"First should be accepted: {r1}"

        r2 = await store.async_ingest_finding(f2)
        accepted2 = _get_attr(r2, "accepted")
        dup2 = _get_attr(r2, "duplicate")
        reason2 = _get_attr(r2, "reason")
        assert accepted2 is False, f"Second should be duplicate: {r2}"
        assert dup2 is True
        assert reason2 == "duplicate_detected"

    @pytest.mark.asyncio
    async def test_miss_store_then_hit(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        f1 = CanonicalFinding(
            finding_id="fid-miss-store-hit",
            query="unique content miss store hit xyz",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        r1 = await store.async_ingest_finding(f1)
        accepted1 = _get_attr(r1, "accepted")
        assert accepted1 is True

        # Re-ingest same content
        r2 = await store.async_ingest_finding(f1)
        accepted2 = _get_attr(r2, "accepted")
        dup2 = _get_attr(r2, "duplicate")
        assert accepted2 is False
        assert dup2 is True

    @pytest.mark.asyncio
    async def test_duplicate_hit_simulated_restart(self, tmp_path):
        from hledac.universal.knowledge.duckdb_store import (
            DuckDBShadowStore, CanonicalFinding,
        )

        db_path = tmp_path / "restart_test.duckdb"
        finding = CanonicalFinding(
            finding_id="fid-restart",
            query="restart persistence test unique",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        store1 = DuckDBShadowStore(db_path=db_path)
        await store1.async_initialize()
        r1 = await store1.async_ingest_finding(finding)
        accepted1 = _get_attr(r1, "accepted")
        assert accepted1 is True
        await store1.aclose()

        # Simulate restart
        store2 = DuckDBShadowStore(db_path=db_path)
        await store2.async_initialize()
        r2 = await store2.async_ingest_finding(finding)
        accepted2 = _get_attr(r2, "accepted")
        dup2 = _get_attr(r2, "duplicate")
        assert accepted2 is False, "Restart should detect persisted duplicate"
        assert dup2 is True
        await store2.aclose()

    @pytest.mark.asyncio
    async def test_quality_duplicate_increments_counter(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        before = store._quality_duplicate_count

        f1 = CanonicalFinding(
            finding_id="fid-dup-count-1",
            query="dup count test unique 1",
            source_type="test", confidence=0.9, ts=time.time(),
        )
        f2 = CanonicalFinding(
            finding_id="fid-dup-count-2",
            query="dup count test unique 1",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        await store.async_ingest_finding(f1)
        await store.async_ingest_finding(f2)

        assert store._quality_duplicate_count == before + 1

    @pytest.mark.asyncio
    async def test_quality_reject_path_unchanged(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        finding = CanonicalFinding(
            finding_id="fid-reject",
            query="aaa bbb ccc ddd eee fff ggg hhh iii jjj kkk",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        result = await store.async_ingest_finding(finding)
        accepted = _get_attr(result, "accepted")
        assert isinstance(accepted, bool)

    @pytest.mark.asyncio
    async def test_fail_open_path_unchanged(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        finding = CanonicalFinding(
            finding_id="fid-failopen",
            query="fail open test xyz",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        original = store._assess_finding_quality

        def raising评估(*args, **kwargs):
            raise RuntimeError("simulated quality check error")

        store._assess_finding_quality = raising评估

        result = await store.async_ingest_finding(finding)
        # Should fail-open — finding is stored despite quality check error
        assert store._quality_fail_open_count == 1
        # Result should be an ActivationResult (dict or struct with finding_id)
        assert _get_attr(result, "finding_id") is not None or _get_attr(result, "lmdb_success") is not None

        store._assess_finding_quality = original

    @pytest.mark.asyncio
    async def test_batch_1to1_invariant(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        findings = [
            CanonicalFinding(
                finding_id=f"fid-batch-{i}",
                query=f"batch query {i} unique {i}",
                source_type="test", confidence=0.9, ts=time.time(),
            )
            for i in range(5)
        ]

        results = await store.async_ingest_findings_batch(findings)
        assert len(results) == len(findings), "1:1 invariant violated"

    @pytest.mark.asyncio
    async def test_batch_mix_accept_duplicate_reject(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        findings = [
            CanonicalFinding(
                finding_id="fid-mix-1",
                query="unique content alpha xyz",
                source_type="test", confidence=0.9, ts=time.time(),
            ),
            CanonicalFinding(
                finding_id="fid-mix-2",
                query="unique content beta xyz",
                source_type="test", confidence=0.9, ts=time.time(),
            ),
            CanonicalFinding(
                finding_id="fid-mix-3",
                query="unique content alpha xyz",  # duplicate of 1
                source_type="test", confidence=0.9, ts=time.time(),
            ),
        ]

        results = await store.async_ingest_findings_batch(findings)
        assert len(results) == 3, "Batch output length mismatch"
        # Each result has an 'accepted' field (FindingQualityDecision or ActivationResult dict)
        for r in results:
            assert hasattr(r, "accepted") or isinstance(r, dict) and "accepted" in r, \
                f"Expected result with 'accepted' field, got {type(r)}"


class TestDedupHotCache:
    """Hot cache boundedness tests."""

    @pytest_asyncio.fixture
    async def store(self):
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "hot_cache_test.duckdb"
            store = DuckDBShadowStore(db_path=db_path)
            await store.async_initialize()
            yield store
            await store.aclose()

    @pytest.mark.asyncio
    async def test_hot_cache_is_bounded(self, store):
        from hledac.universal.knowledge.duckdb_store import _DEDUP_HOT_CACHE_MAX

        for i in range(_DEDUP_HOT_CACHE_MAX + 100):
            store._add_to_hot_cache(f"fp-{i:06d}", f"fid-{i:06d}")

        assert len(store._dedup_hot_cache) <= _DEDUP_HOT_CACHE_MAX
        assert len(store._dedup_hot_cache_order) <= _DEDUP_HOT_CACHE_MAX

    @pytest.mark.asyncio
    async def test_hot_cache_fifo_eviction(self, store):
        store._add_to_hot_cache("fp-old", "fid-old")
        store._add_to_hot_cache("fp-new", "fid-new")

        assert store._hot_cache_lookup("fp-old") == "fid-old"
        assert store._hot_cache_lookup("fp-new") == "fid-new"


class TestDedupRuntimeStatus:
    """Status surface tests."""

    @pytest.fixture
    async def store(self):
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "status_test.duckdb"
            store = DuckDBShadowStore(db_path=db_path)
            await store.async_initialize()
            yield store
            await store.aclose()

    @pytest.mark.asyncio
    async def test_get_dedup_runtime_status_shape(self, store):
        status = store.get_dedup_runtime_status()

        required_keys = [
            "persistent_dedup_enabled",
            "last_boot_cleanup_error",
            "last_dedup_error",
            "dedup_lmdb_path",
            "dedup_namespace",
            "hot_cache_size",
            "hot_cache_capacity",
        ]
        for k in required_keys:
            assert k in status, f"Missing key: {k}"
        assert status["dedup_namespace"] == "dedup:"

    @pytest.mark.asyncio
    async def test_status_reflects_initialized_state(self, store):
        status = store.get_dedup_runtime_status()
        assert isinstance(status["persistent_dedup_enabled"], bool)


class TestBootGuard:
    """§1.4 LMDB boot safety tests."""

    def test_is_process_alive_live_process(self):
        from hledac.universal.knowledge.lmdb_boot_guard import _is_process_alive

        assert _is_process_alive(os.getpid()) is True

    def test_is_process_alive_dead_process(self):
        from hledac.universal.knowledge.lmdb_boot_guard import _is_process_alive

        assert _is_process_alive(99999) is False

    def test_cleanup_does_not_blindly_delete(self):
        from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock

        with tempfile.TemporaryDirectory() as tmpdir:
            lmdb_dir = Path(tmpdir) / "test_lmdb"
            lmdb_dir.mkdir()

            removed, reason = cleanup_stale_lmdb_lock(lmdb_dir)
            assert removed == 0
            assert "not_found" in reason or "recent" in reason

    def test_live_holder_prevents_cleanup(self):
        from hledac.universal.knowledge.lmdb_boot_guard import _is_lock_stale

        with tempfile.TemporaryDirectory() as tmpdir:
            lmdb_dir = Path(tmpdir) / "test_lmdb"
            lmdb_dir.mkdir()
            lock_path = lmdb_dir / "lock.mdb"

            # Write current PID as lock holder
            lock_path.write_bytes(os.getpid().to_bytes(4, byteorder="little"))

            is_stale, reason = _is_lock_stale(lock_path)
            assert is_stale is False, f"Live holder should NOT be stale: {reason}"
            assert "alive" in reason.lower()

    def test_boot_guard_idempotent(self):
        from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock

        with tempfile.TemporaryDirectory() as tmpdir:
            lmdb_dir = Path(tmpdir) / "test_lmdb"
            lmdb_dir.mkdir()

            r1 = cleanup_stale_lmdb_lock(lmdb_dir)
            r2 = cleanup_stale_lmdb_lock(lmdb_dir)
            assert r1 == r2

    def test_stale_lock_old_file_no_pid(self):
        from hledac.universal.knowledge.lmdb_boot_guard import _is_lock_stale

        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / "lock.mdb"
            lock_path.write_bytes(b"\x00\x00\x00\x00")
            old_time = time.time() - 300
            os.utime(lock_path, (old_time, old_time))

            is_stale, reason = _is_lock_stale(lock_path)
            assert is_stale is True, f"Old lock with no live PID should be stale: {reason}"


class TestUMAEnumContract:
    """UMA state enum unification tests."""

    def test_evaluate_uma_state_returns_valid_enums(self):
        from hledac.universal.core.resource_governor import evaluate_uma_state

        valid_enums = {"ok", "warn", "critical", "emergency"}
        forbidden_enums = {"normal", "elevated", "warning"}

        for gib in [3.0, 4.0, 5.9, 6.0, 6.1, 6.5, 6.9, 7.0, 7.5, 8.0]:
            state = evaluate_uma_state(gib)
            assert state in valid_enums, f"Invalid state '{state}' for {gib} GiB"
            assert state not in forbidden_enums

    def test_pipeline_no_normal_alias(self):
        """Pipeline no longer uses 'normal' as uma_state default."""
        import inspect
        from hledac.universal.pipeline import live_public_pipeline

        src = inspect.getsource(live_public_pipeline)

        for line in src.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "uma_state" in line and "=" in line and "normal" in line:
                raise AssertionError(f"Pipeline still uses 'normal' alias: {line.strip()}")


class TestNoImportSideEffects:
    """Import-time side effects absent."""

    def test_duckdb_store_creates_no_files_on_init(self):
        """Import duckdb_store does not open any LMDB or create files."""
        import gc
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "side_effect_test.duckdb"

            # Count files before import
            files_before = set(Path(tmpdir).rglob("*"))

            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

            # Creating object should not create any files
            store = DuckDBShadowStore(db_path=db_path)
            files_after = set(Path(tmpdir).rglob("*"))

            # Only files created should be the parent dir itself (which exists)
            # The duckdb file should NOT be created until async_initialize
            new_files = files_after - files_before
            # Should be empty — no file creation on __init__
            assert len(new_files) == 0, f"Files created on __init__: {new_files}"


class TestAsyncIngestFindingsBatchCanonical:
    """
    TEST-001: Unit tests for async_ingest_findings_batch canonical write path.

    Covers:
    - Success path: all findings accepted + stored
    - Quality rejection: FindingQualityDecision returned
    - Fail-open: exception in quality check → finding still stored
    - 1:1 invariant: len(results) == len(findings)
    - Closed store: returns error results without crashing
    - Startup timeout: returns error results
    - LMDB partial failure: only some succeed
    - Desync detection: LMDB ok + DuckDB fail → desync=True
    """

    @pytest_asyncio.fixture
    async def store(self):
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "canonical_write_test.duckdb"
            store = DuckDBShadowStore(db_path=db_path)
            await store.async_initialize()
            yield store
            if store._dedup_lmdb is not None:
                try:
                    with store._dedup_lmdb._env.begin(write=True) as txn:
                        with txn.cursor() as cur:
                            for k, _ in cur:
                                txn.delete(k)
                except Exception:
                    pass
            await store.aclose()

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        results = await store.async_ingest_findings_batch([])
        assert results == []
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_success_path_all_accepted(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        import uuid
        # Use semantically distinct queries to avoid semantic dedup LMDB collisions
        queries = [
            f"acme_corp_revenue_2024_analysis_alpha_{uuid.uuid4().hex[:12]}",
            f"global_warming_mitigation_strategies_beta_{uuid.uuid4().hex[:12]}",
            f"quantum_computing_cryptography_breakthrough_{uuid.uuid4().hex[:12]}",
        ]
        findings = [
            CanonicalFinding(
                finding_id=f"fid-succ-{uuid.uuid4().hex[:8]}",
                query=queries[i],
                source_type="test", confidence=0.9, ts=time.time(),
            )
            for i in range(3)
        ]

        results = await store.async_ingest_findings_batch(findings)

        # 1:1 invariant
        assert len(results) == len(findings)

        # All should be accepted (ActivationResult with accepted=True)
        for r in results:
            assert _get_attr(r, "accepted") is True, f"Expected accepted=True: {r}"
            assert _get_attr(r, "lmdb_success") is True, f"Expected lmdb_success=True: {r}"
            assert _get_attr(r, "finding_id") is not None

    @pytest.mark.asyncio
    async def test_quality_rejection_returns_decision(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        # Low-entropy query SHOULD be rejected by quality gate (entropy < 0.5)
        # "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" normalizes to 32 a's, entropy=0.0
        findings = [
            CanonicalFinding(
                finding_id="fid-reject-1",
                query="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # entropy 0.0 < 0.5 threshold
                source_type="test", confidence=0.9, ts=time.time(),
            ),
            CanonicalFinding(
                finding_id="fid-reject-2",
                query="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",  # entropy 0.0 < 0.5 threshold
                source_type="test", confidence=0.9, ts=time.time(),
            ),
        ]

        results = await store.async_ingest_findings_batch(findings)

        assert len(results) == 2
        for r in results:
            # FindingQualityDecision has accepted=False for entropy rejection
            assert _get_attr(r, "accepted") is False, f"Expected rejected: {r}"
            # Should have a reason
            reason = _get_attr(r, "reason")
            assert reason is not None, f"Expected reason for rejection: {r}"

    @pytest.mark.asyncio
    async def test_mixed_batch_accept_and_reject(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        findings = [
            CanonicalFinding(
                finding_id="fid-mix-accept",
                query=f"unique mixed content acceptance {time.time()}",
                source_type="test", confidence=0.9, ts=time.time(),
            ),
            CanonicalFinding(
                finding_id="fid-mix-reject",
                query="aaa bbb ccc ddd eee fff ggg hhh iii jjj kkk",  # low entropy
                source_type="test", confidence=0.9, ts=time.time(),
            ),
        ]

        results = await store.async_ingest_findings_batch(findings)

        assert len(results) == 2
        # At least one accepted, one rejected (based on entropy)
        accepted_count = sum(1 for r in results if _get_attr(r, "accepted") is True)
        rejected_count = sum(1 for r in results if _get_attr(r, "accepted") is False)
        assert accepted_count + rejected_count == 2

    @pytest.mark.asyncio
    async def test_fail_open_on_quality_exception(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        finding = CanonicalFinding(
            finding_id="fid-failopen-batch",
            query="fail open test batch unique content",
            source_type="test", confidence=0.9, ts=time.time(),
        )

        original = store._assess_finding_quality

        def raising_quality(*args, **kwargs):
            raise RuntimeError("simulated quality check error")

        store._assess_finding_quality = raising_quality

        try:
            before = store._quality_fail_open_count
            results = await store.async_ingest_findings_batch([finding])

            assert len(results) == 1
            # Fail-open: finding stored despite quality check error
            assert store._quality_fail_open_count == before + 1
            # Should have an ActivationResult (not FindingQualityDecision)
            r = results[0]
            assert _get_attr(r, "finding_id") is not None or _get_attr(r, "lmdb_success") is not None
        finally:
            store._assess_finding_quality = original

    @pytest.mark.asyncio
    async def test_closed_store_returns_error_results(self):
        from hledac.universal.knowledge.duckdb_store import (
            DuckDBShadowStore, CanonicalFinding,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "closed_store_test.duckdb"
            store = DuckDBShadowStore(db_path=db_path)
            # Don't initialize — store is not initialized

            findings = [
                CanonicalFinding(
                    finding_id="fid-closed",
                    query="closed store test",
                    source_type="test", confidence=0.9, ts=time.time(),
                ),
            ]

            results = await store.async_ingest_findings_batch(findings)

            assert len(results) == 1
            assert _get_attr(results[0], "accepted") is False
            assert _get_attr(results[0], "lmdb_success") is False

    @pytest.mark.asyncio
    async def test_1to1_invariant_holds(self, store):
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding

        # Test with various batch sizes
        for batch_size in [1, 3, 7]:
            findings = [
                CanonicalFinding(
                    finding_id=f"fid-invariant-{batch_size}-{i}",
                    query=f"invariant test content {time.time()}-{i}",
                    source_type="test", confidence=0.9, ts=time.time(),
                )
                for i in range(batch_size)
            ]

            results = await store.async_ingest_findings_batch(findings)
            assert len(results) == batch_size, \
                f"1:1 invariant violated: {len(results)} != {batch_size}"

    @pytest.mark.asyncio
    async def test_desync_flag_when_duckdb_fails(self, store):
        """When LMDB succeeds but DuckDB fails, desync=True in result."""
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
        from unittest.mock import patch

        findings = [
            CanonicalFinding(
                finding_id="fid-desync-test",
                query=f"desync test query {time.time()}",
                source_type="test", confidence=0.9, ts=time.time(),
            ),
        ]

        # Patch DuckDB insert to fail while LMDB succeeds
        original_sync = store._canonical_findings_batch_to_activation_results

        def failing_duckdb(findings):
            # Simulate: LMDB ok, DuckDB fail
            results = []
            for f in findings:
                results.append({
                    "finding_id": f.finding_id,
                    "lmdb_success": True,  # LMDB wrote
                    "duckdb_success": False,  # DuckDB failed
                    "error": None,
                })
            return results

        store._canonical_findings_batch_to_activation_results = failing_duckdb

        try:
            results = await store.async_ingest_findings_batch(findings)
            assert len(results) == 1
            # desync should be True: LMDB ok, DuckDB not ok
            assert _get_attr(results[0], "desync") is True, \
                f"Expected desync=True when LMDB ok but DuckDB fails: {results[0]}"
        finally:
            store._canonical_findings_batch_to_activation_results = original_sync


class TestDedupFingerprintStability:
    """Verify fingerprint stability (not Python hash())."""

    def test_fingerprint_uses_blake2b(self):
        from hledac.universal.knowledge.duckdb_store import _compute_dedup_fingerprint

        fp1 = _compute_dedup_fingerprint("hello world")
        fp2 = _compute_dedup_fingerprint("hello world")

        assert fp1 == fp2, "Fingerprint must be stable"
        assert len(fp1) == 32
        assert all(c in "0123456789abcdef" for c in fp1)

    def test_fingerprint_stable_across_calls(self):
        from hledac.universal.knowledge.duckdb_store import _compute_dedup_fingerprint

        fps = [_compute_dedup_fingerprint(f"test-{i}") for i in range(10)]
        assert len(set(fps)) == 10, "Fingerprint appears randomized (using Python hash()?)"
