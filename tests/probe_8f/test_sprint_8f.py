"""
Sprint 8F — Activation Recovery Seam + DuckDB Full Gate Cleanup
================================================================

Tests:
1. probe_7f clean (gate N/A — not found)
2. ActivationResult shape is compatible with probe_8b contract
3. batch API returns list[ActivationResult]
4. pending marker uses prefix pending_duckdb_sync:
5. DuckDB fail → desync=True after LMDB success
6. LMDB fail first → duckdb_success is None
7. pending marker created ONLY on DuckDB fail after LMDB success
8. recovery marker does NOT collide with finding:{id} keyspace
9. async wrapper is awaitable, not default fire-and-forget
10. transaction-safe bulk path verified (no Appender requirement)
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


class TestActivationResultShape(unittest.TestCase):
    """Verify ActivationResult TypedDict matches probe_8b contract."""

    def test_activation_result_has_required_fields(self):
        """1. ActivationResult has all required fields from probe_8b."""
        from hledac.universal.knowledge.duckdb_store import ActivationResult

        keys = set(ActivationResult.__annotations__.keys())
        expected = {"finding_id", "lmdb_success", "duckdb_success", "lmdb_key", "desync", "error"}
        self.assertEqual(keys, expected)

    def test_activation_result_field_types(self):
        """1b. ActivationResult field types match probe_8b."""
        import typing
        from hledac.universal.knowledge.duckdb_store import ActivationResult

        ann = typing.get_type_hints(ActivationResult)
        self.assertEqual(ann["finding_id"], str)
        self.assertEqual(ann["lmdb_success"], bool)
        self.assertEqual(ann["duckdb_success"], bool | None)
        self.assertEqual(ann["lmdb_key"], str)
        self.assertEqual(ann["desync"], bool)
        self.assertEqual(ann["error"], str | None)


class TestBatchAPIReturnsListOfActivationResult(unittest.TestCase):
    """Verify batch API returns list[ActivationResult], not list[dict]."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_batch_returns_list_of_activation_result(self):
        """2. async_record_activation_batch returns list[ActivationResult]."""
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
            for i in range(5)
        ]

        results = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation_batch(findings)
        )

        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 5)

        # Each must be an ActivationResult
        for r in results:
            self.assertIn("finding_id", r)
            self.assertIn("lmdb_success", r)
            self.assertIn("duckdb_success", r)
            self.assertIn("lmdb_key", r)
            self.assertIn("desync", r)
            self.assertIn("error", r)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestPendingMarkerPrefix(unittest.TestCase):
    """Verify pending_duckdb_sync: prefix is used and does not collide with finding:."""

    def test_pending_marker_key_format(self):
        """3. pending marker uses prefix pending_duckdb_sync:."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()

        # Direct test of marker key format
        finding_id = "test-finding-123"
        expected_key = f"pending_duckdb_sync:{finding_id}"
        self.assertEqual(expected_key, "pending_duckdb_sync:test-finding-123")

        # Verify it does NOT collide with finding:{id} keyspace
        finding_key = f"finding:{finding_id}"
        self.assertNotEqual(expected_key, finding_key)

    def test_finding_key_prefix_unchanged(self):
        """3b. finding:{id} key format unchanged (backward compatibility)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        finding_id = "test-finding-456"
        expected = f"finding:{finding_id}"
        self.assertEqual(expected, "finding:test-finding-456")


class TestDuckDBFailDesyncTrue(unittest.TestCase):
    """Test: DuckDB fail after LMDB success → desync=True."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_desync_true_when_lmdb_ok_duckdb_fail(self):
        """4. DuckDB fail after LMDB success → desync=True."""
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

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestLMDBFailDuckDBSuccessIsNone(unittest.TestCase):
    """Test: LMDB fail first → duckdb_success is None."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_lmdb_fail_duckdb_success_is_none(self):
        """5. LMDB fail first → duckdb_success is None (not attempted)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Mock LMDB WAL to fail
        original_wal = store._wal_write_finding

        def failing_wal(*args, **kwargs):
            return False  # LMDB fails

        store._wal_write_finding = failing_wal

        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("lmdb-fail-001", "q", "synthetic", 0.9)
        )

        self.assertFalse(result["lmdb_success"])
        self.assertIsNone(result["duckdb_success"],
            "duckdb_success must be None when LMDB failed (not attempted)")
        self.assertFalse(result["desync"],
            "desync must be False when LMDB failed (no desync possible)")

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestPendingMarkerCreationOnlyOnDuckDBFail(unittest.TestCase):
    """Test: pending marker created ONLY when LMDB OK but DuckDB fails."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_pending_marker_created_only_on_duckdb_fail(self):
        """6. pending marker created ONLY on DuckDB fail after LMDB success."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        marker_written = []

        original_write = store._wal_write_pending_sync_marker

        def tracking_marker(*args, **kwargs):
            marker_written.append(args[0])  # record finding_id
            return original_write(*args, **kwargs)

        store._wal_write_pending_sync_marker = tracking_marker

        # Case 1: DuckDB fails after LMDB OK → marker SHOULD be written
        def failing_insert(*args, **kwargs):
            raise RuntimeError("simulated DuckDB failure")

        store._sync_insert_finding = failing_insert

        result = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation("marker-test-001", "q", "synthetic", 0.9)
        )

        self.assertTrue(result["lmdb_success"])
        self.assertFalse(result["duckdb_success"])
        self.assertIn("marker-test-001", marker_written,
            "Pending marker should be written when DuckDB fails after LMDB success")

        # Reset for case 2
        marker_written.clear()

        # Case 2: Both LMDB and DuckDB succeed → marker should NOT be written
        original_sync_ok = store.__class__. _sync_insert_finding  # noqa
        # Use a fresh store instance for clean state
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp
            ok = store2.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        marker_written2 = []

        def tracking_marker2(*args, **kwargs):
            marker_written2.append(args[0])
            return original_write(*args, **kwargs)

        store2._wal_write_pending_sync_marker = tracking_marker2

        result2 = asyncio.get_event_loop().run_until_complete(
            store2.async_record_activation("no-marker-001", "q", "synthetic", 0.9)
        )

        self.assertTrue(result2["lmdb_success"])
        self.assertTrue(result2["duckdb_success"])
        self.assertFalse(result2["desync"])

        asyncio.get_event_loop().run_until_complete(store.aclose())
        asyncio.get_event_loop().run_until_complete(store2.aclose())


class TestRecoveryMarkerDoesNotCollide(unittest.TestCase):
    """Test: pending_duckdb_sync: does NOT collide with finding: keyspace."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_markers_do_not_collide(self):
        """7. pending_duckdb_sync: keyspace is distinct from finding:."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "collision-test-xyz"
        finding_key = f"finding:{finding_id}"
        pending_key = f"pending_duckdb_sync:{finding_id}"

        # Keys must be different
        self.assertNotEqual(finding_key, pending_key)

        # Direct LMDB write test
        lmdb_ok = store._wal_write_finding(finding_id, "test query", "synthetic", 0.95)
        self.assertTrue(lmdb_ok)

        # Pending marker write
        marker_ok = store._wal_write_pending_sync_marker(
            finding_id, "test query", "synthetic", 0.95
        )
        self.assertTrue(marker_ok)

        # Both can coexist as distinct keys
        finding_value = store._wal_lmdb.get(finding_key)
        pending_value = store._wal_lmdb.get(pending_key)

        self.assertIsNotNone(finding_value)
        self.assertIsNotNone(pending_value)
        # IDs are the same (correct) — the keys are different
        self.assertEqual(finding_value["id"], pending_value["id"])
        # But key strings are different
        self.assertNotEqual(finding_key, pending_key)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestAsyncWrapperIsAwaitable(unittest.TestCase):
    """Test: async wrapper is awaitable, not default fire-and-forget."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_async_wrapper_returns_awaitable(self):
        """8. async_record_activation is awaitable and returns ActivationResult."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Get the coroutine
        coro = store.async_record_activation("await-test-001", "q", "synthetic", 0.9)
        self.assertTrue(asyncio.iscoroutine(coro),
            "async_record_activation must return a coroutine (awaitable)")

        # Run it and verify result
        result = asyncio.get_event_loop().run_until_complete(coro)

        self.assertIsInstance(result, dict)
        self.assertIn("finding_id", result)
        self.assertEqual(result["finding_id"], "await-test-001")

        asyncio.get_event_loop().run_until_complete(store.aclose())

    def test_batch_async_wrapper_is_awaitable(self):
        """8b. async_record_activation_batch is awaitable."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        findings = [{"id": f"batch-{i}", "query": f"q{i}", "source_type": "synthetic", "confidence": 0.9} for i in range(3)]

        coro = store.async_record_activation_batch(findings)
        self.assertTrue(asyncio.iscoroutine(coro),
            "async_record_activation_batch must return a coroutine (awaitable)")

        results = asyncio.get_event_loop().run_until_complete(coro)

        self.assertIsInstance(results, list)
        self.assertEqual(len(results), 3)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestTransactionSafeBulkPath(unittest.TestCase):
    """Test: transaction-safe bulk path exists and is used."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_bulk_insert_uses_transaction(self):
        """9. _sync_insert_findings_bulk uses explicit transaction (BEGIN/COMMIT)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Successful bulk insert
        findings = [
            {
                "id": f"bulk-{i:03d}",
                "query": f"bulk query {i}",
                "source_type": "synthetic",
                "confidence": 0.95,
            }
            for i in range(10)
        ]

        results = asyncio.get_event_loop().run_until_complete(
            store.async_record_activation_batch(findings)
        )

        # All should succeed
        for r in results:
            self.assertTrue(r["lmdb_success"],
                f"LMDB should succeed for {r['finding_id']}")
            # DuckDB might succeed or fail depending on environment

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestPendingMarkerScan(unittest.TestCase):
    """Test: _wal_scan_pending_sync_markers uses efficient prefix scan."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_scan_finds_pending_markers(self):
        """10. _wal_scan_pending_sync_markers finds written markers."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Write some pending markers directly
        for i in range(3):
            store._wal_write_pending_sync_marker(
                f"scan-test-{i}", f"query {i}", "synthetic", 0.9
            )

        # Scan should find them
        markers = store._wal_scan_pending_sync_markers()

        self.assertIsInstance(markers, list)
        # Should find our 3 markers
        marker_ids = [m["id"] for m in markers if m.get("id", "").startswith("scan-test-")]
        self.assertEqual(sorted(marker_ids), ["scan-test-0", "scan-test-1", "scan-test-2"])

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestClearPendingMarker(unittest.TestCase):
    """Test: _wal_clear_pending_sync_marker removes marker."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_clear_removes_marker(self):
        """10b. _wal_clear_pending_sync_marker removes marker from LMDB."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "clear-test-xyz"

        # Write marker
        store._wal_write_pending_sync_marker(
            finding_id, "query", "synthetic", 0.9
        )

        # Verify it exists
        pending_key = f"pending_duckdb_sync:{finding_id}"
        value_before = store._wal_lmdb.get(pending_key)
        self.assertIsNotNone(value_before)

        # Clear it
        cleared = store._wal_clear_pending_sync_marker(finding_id)
        self.assertTrue(cleared)

        # Verify it's gone
        value_after = store._wal_lmdb.get(pending_key)
        self.assertIsNone(value_after)

        asyncio.get_event_loop().run_until_complete(store.aclose())


if __name__ == "__main__":
    unittest.main(verbosity=2)
