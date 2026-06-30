"""
Sprint 8B — Async Activation Wrapper + Gate Truth Cleanup
============================================================

Tests:
1. AO canary existence / run
2. probe_8a run
3. probe_7i run
4. async_record_activation() returns typed ActivationResult
5. async_record_activation_batch() returns list[ActivationResult]
6. LMDB first semantics (WAL written before DuckDB)
7. DuckDB second semantics
8. partial failure: LMDB OK / DuckDB FAIL → desync=True
9. desync flag is correctly set
10. lmdb_key == f"finding:{id}"
11. fresh read-back after write completion
12. N=1 activation
13. N=10 activation
14. rag_engine wiring deferred (confirmed)
"""


import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestActivationResultContract(unittest.TestCase):
    """Test that ActivationResult TypedDict is properly defined."""

    def test_activation_result_importable(self):
        """0. ActivationResult is importable from duckdb_store."""
        from hledac.universal.knowledge.duckdb_store import ActivationResult
        # TypedDict is a class
        self.assertTrue(hasattr(ActivationResult, "__annotations__"))
        keys = set(ActivationResult.__annotations__.keys())
        expected = {"finding_id", "lmdb_success", "duckdb_success", "lmdb_key", "desync", "error"}
        self.assertEqual(keys, expected)

    def test_activation_result_fields_typed(self):
        """0b. ActivationResult fields have correct types (using get_type_hints)."""
        import typing
        from hledac.universal.knowledge.duckdb_store import ActivationResult
        ann = typing.get_type_hints(ActivationResult)
        self.assertEqual(ann["finding_id"], str)
        self.assertEqual(ann["lmdb_success"], bool)
        self.assertEqual(ann["duckdb_success"], bool | None)
        self.assertEqual(ann["lmdb_key"], str)
        self.assertEqual(ann["desync"], bool)
        self.assertEqual(ann["error"], str | None)


class TestAsyncRecordActivationSignature(unittest.TestCase):
    """Test that async_record_activation has correct signature."""

    def test_method_exists(self):
        """1. async_record_activation exists on DuckDBShadowStore."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        store = DuckDBShadowStore()
        self.assertTrue(hasattr(store, "async_record_activation"))
        self.assertTrue(callable(store.async_record_activation))

    def test_batch_method_exists(self):
        """2. async_record_activation_batch exists on DuckDBShadowStore."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        store = DuckDBShadowStore()
        self.assertTrue(hasattr(store, "async_record_activation_batch"))
        self.assertTrue(callable(store.async_record_activation_batch))


class TestAsyncRecordActivationN1(unittest.TestCase):
    """Test async_record_activation with N=1 finding."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.lmdb_path = Path(self.tmpdir) / "wal.lmdb"
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_typed_activation_result(self):
        """3. async_record_activation returns ActivationResult (not raw dict)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore, ActivationResult

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available in this environment")

        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation(
                "fid-001", "test query", "synthetic", 0.95
            )
        )

        # Must be an ActivationResult, not a plain dict
        self.assertIsInstance(result, dict)
        self.assertIn("finding_id", result)
        self.assertIn("lmdb_success", result)
        self.assertIn("duckdb_success", result)
        self.assertIn("lmdb_key", result)
        self.assertIn("desync", result)
        self.assertIn("error", result)

        # Check field values
        self.assertEqual(result["finding_id"], "fid-001")
        self.assertEqual(result["lmdb_key"], "finding:fid-001")
        self.assertIsInstance(result["lmdb_success"], bool)
        self.assertIsInstance(result["desync"], bool)

        asyncio.get_event_loop().run_until_complete(store.aclose())

    def test_lmdb_key_format(self):
        """4. lmdb_key == f'finding:{id}'."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("my-finding-xyz", "q", "web", 0.8)
        )

        self.assertEqual(result["lmdb_key"], "finding:my-finding-xyz")

        asyncio.get_event_loop().run_until_complete(store.aclose())

    def test_store_closed_returns_error_result(self):
        """5. store.closed returns error result with correct fields."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        store._closed = True  # simulate already-closed store

        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("fid", "q", "s", 0.5)
        )

        self.assertEqual(result["finding_id"], "fid")
        self.assertFalse(result["lmdb_success"])
        self.assertIsNone(result["duckdb_success"])
        self.assertFalse(result["desync"])  # closed store → error, not desync (no LMDB write happened)
        self.assertIsNotNone(result["error"])


class TestAsyncRecordActivationBatch(unittest.TestCase):
    """Test async_record_activation_batch with N=10 findings."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_returns_list_of_activation_result(self):
        """6. batch returns list[ActivationResult]."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        findings = [
            {
                "id": f"batch-{i:03d}",
                "query": f"batch query {i}",
                "source_type": "synthetic",
                "confidence": 0.5 + i * 0.05,
            }
            for i in range(10)
        ]

        results = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation_batch(findings)
        )

        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 10)

        # Each must be a proper ActivationResult
        for r in results:
            self.assertIn("finding_id", r)
            self.assertIn("lmdb_success", r)
            self.assertIn("duckdb_success", r)
            self.assertIn("lmdb_key", r)
            self.assertIn("desync", r)
            self.assertIn("error", r)

        # lmdb_key format check
        self.assertEqual(results[0]["lmdb_key"], "finding:batch-000")
        self.assertEqual(results[9]["lmdb_key"], "finding:batch-009")

        # finding_ids match input
        ids = [r["finding_id"] for r in results]
        expected_ids = [f"batch-{i:03d}" for i in range(10)]
        self.assertEqual(ids, expected_ids)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestDesyncSemantics(unittest.TestCase):
    """Test WAL-first partial failure: LMDB OK / DuckDB FAIL → desync=True."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_desync_flag_when_lmdb_ok_duckdb_fail(self):
        """7. desync=True when LMDB succeeds but DuckDB fails."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Mock DuckDB to fail after LMDB succeeds
        original_sync = store._sync_insert_finding

        def failing_insert(*args, **kwargs):
            # LMDB part already happened in _activation_record_finding
            # We make DuckDB fail by raising
            raise RuntimeError("simulated DuckDB failure")

        store._sync_insert_finding = failing_insert

        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("desync-001", "q", "synthetic", 0.9)
        )

        self.assertTrue(result["lmdb_success"],
            "LMDB should succeed (we didn't mock it)")
        self.assertFalse(result["duckdb_success"])
        self.assertTrue(result["desync"],
            "desync must be True when LMDB OK but DuckDB FAIL")
        self.assertEqual(result["finding_id"], "desync-001")
        self.assertIsNone(result["error"])  # no exception propagated

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestFreshReadBackSafety(unittest.TestCase):
    """Test that fresh read-back comes AFTER write completion, not concurrent."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_read_back_after_write_completion(self):
        """8. read-back uses fresh connection and runs after write completes."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Write
        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("rb-001", "readback test", "synthetic", 0.88)
        )
        self.assertTrue(result["lmdb_success"])

        # Read-back with fresh connection (async_query_recent_findings uses executor)
        rows = asyncio.get_event_loop().run_until_complete(
            store.async_query_recent_findings(limit=5)
        )

        ids = {r["id"] for r in rows}
        self.assertIn("rb-001", ids)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestRagEngineWiringDeferred(unittest.TestCase):
    """Confirm rag_engine consumer wiring is deferred."""

    def test_summarize_cluster_returns_str_not_structured(self):
        """9. rag_engine._summarize_cluster returns str, no structured result for wiring."""
        import inspect
        from hledac.universal.knowledge.rag_engine import RAGEngine

        sig = inspect.signature(RAGEngine._summarize_cluster)
        # With `from __future__ import annotations`, return_annotation is the string 'str'
        self.assertEqual(sig.return_annotation, "str",
            "_summarize_cluster must return str for deferred wiring decision")

    def test_rag_engine_has_no_async_record_activation_call(self):
        """10. rag_engine does NOT call async_record_activation (not wired)."""
        from hledac.universal.knowledge import rag_engine
        import inspect

        source = inspect.getsource(rag_engine)
        self.assertNotIn("async_record_activation", source,
            "rag_engine should NOT be wired to async_record_activation (deferred)")
        self.assertNotIn("async_record_activation_batch", source)


class TestLMDBFirstSemantics(unittest.TestCase):
    """Verify LMDB is written FIRST in WAL-first order."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_lmdb_written_before_duckdb(self):
        """11. LMDB WAL write order is enforced (LMDB first)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        call_order: List[str] = []

        original_wal = store._wal_write_finding
        def tracking_wal(*args, **kwargs):
            call_order.append("lmdb")
            return original_wal(*args, **kwargs)
        store._wal_write_finding = tracking_wal

        original_sync = store._sync_insert_finding
        def tracking_sync(*args, **kwargs):
            call_order.append("duckdb")
            return original_sync(*args, **kwargs)
        store._sync_insert_finding = tracking_sync

        asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("order-001", "q", "s", 0.7)
        )

        self.assertEqual(call_order, ["lmdb", "duckdb"],
            "LMDB must be called before DuckDB (WAL-first)")

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestProbe8AGate(unittest.TestCase):
    """Run probe_8a as a gate check."""

    def test_probe_8a_passes_or_skip(self):
        """12. probe_8a passes or skips if duckdb unavailable."""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest("duckdb not installed — probe_8a env-specific skip")

        import subprocess
        result = subprocess.run(
            ["pytest", "tests/probe_8a/", "-q", "--tb=no"],
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0,
            f"probe_8a failed:\n{result.stdout}\n{result.stderr}")


class TestProbe7iGate(unittest.TestCase):
    """Run probe_7i as a gate check."""

    def test_probe_7i_passes(self):
        """13. probe_7i exists and passes."""
        import subprocess
        result = subprocess.run(
            ["pytest", "tests/probe_7i/", "-q", "--tb=no"],
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0,
            f"probe_7i failed:\n{result.stdout}\n{result.stderr}")


class TestAOCanaryGate(unittest.TestCase):
    """Run AO canary as a gate check."""

    def test_ao_canary_passes(self):
        """14. AO canary exists and passes."""
        import subprocess
        result = subprocess.run(
            ["pytest", "tests/test_ao_canary.py", "-q", "--tb=no"],
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0,
            f"AO canary failed:\n{result.stdout}\n{result.stderr}")


class TestProbe7fGate(unittest.TestCase):
    """probe_7f gate: skip if duckdb not available, else run."""

    def test_probe_7f_duckdb_guard(self):
        """15. probe_7f is skipped if duckdb not installed (env-specific)."""
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest("duckdb not installed — probe_7f env-specific skip")

        import subprocess
        result = subprocess.run(
            ["pytest", "tests/probe_7f/", "-q", "--tb=no"],
            cwd="/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal",
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0,
            f"probe_7f failed:\n{result.stdout}\n{result.stderr}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
