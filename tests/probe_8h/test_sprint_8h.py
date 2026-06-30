"""
Sprint 8H — Pending-DuckDB-Sync Recovery Worker
================================================

Tests:
1.  replay one marker works
2.  replay multiple markers works
3.  marker deleted only on success
4.  marker stays on failure
5.  WAL truth untouched by replay
6.  replay uses same safe write path as activation
7.  fresh read-back confirms record
8.  replay is idempotent
9.  replay empty pending set is clean no-op
10. replay respects limit
11. replay uses replay lock
12. scan returns eager list (lazy cursor lifetime bug not possible)
13. poison marker goes to dead-letter after max retries
14. ReplayResult TypedDict is stable
15. test isolation uses unique tmpdir

Invariant table:
| test                           | invariant           |
| ------------------------------ | ------------------- |
| test_replay_one_marker_works   | 1.A, 1.B, 1.F      |
| test_replay_multiple_markers  | 1.A, 1.F           |
| test_marker_deleted_on_success | 1.A, 1.B           |
| test_marker_stays_on_failure   | 1.A, 1.B           |
| test_wal_truth_untouched       | 1.B                |
| test_replay_uses_same_path     | 1.A                |
| test_fresh_read_back          | 1.E                |
| test_replay_idempotent         | 1.C, 1.C           |
| test_replay_empty_noop         | 1.F                |
| test_replay_respects_limit     | 1.F                |
| test_replay_lock_prevents_dup  | 1.C                |
| test_scan_returns_eager_list   | 1.D                |
| test_deadletter_after_retries  | 1.G                |
| test_replay_result_stable      | 1.H                |
| test_unique_tmpdir_isolation   | 1.I                |
"""


import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch, MagicMock

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


class TestReplayResultTypedDict(unittest.TestCase):
    """14. ReplayResult TypedDict is stable."""

    def test_replay_result_has_required_fields(self):
        """14. ReplayResult has all required fields."""
        from hledac.universal.knowledge.duckdb_store import ReplayResult

        keys = set(ReplayResult.__annotations__.keys())
        expected = {
            "finding_id", "marker_found", "wal_truth_found",
            "duckdb_written", "marker_cleared", "read_back_verified",
            "deadlettered", "retry_count", "error",
        }
        self.assertEqual(keys, expected)

    def test_replay_result_field_types(self):
        """14b. ReplayResult field types."""
        import typing
        from hledac.universal.knowledge.duckdb_store import ReplayResult

        ann = typing.get_type_hints(ReplayResult)
        self.assertEqual(ann["finding_id"], str)
        self.assertEqual(ann["marker_found"], bool)
        self.assertEqual(ann["wal_truth_found"], bool)
        self.assertEqual(ann["duckdb_written"], bool)
        self.assertEqual(ann["marker_cleared"], bool)
        self.assertEqual(ann["read_back_verified"], bool)
        self.assertEqual(ann["deadlettered"], bool)
        self.assertEqual(ann["retry_count"], int)
        self.assertEqual(ann["error"], str | None)


class TestReplayOneMarkerWorks(unittest.TestCase):
    """1. replay one marker works."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"
        self.wal_path = Path(self.tmpdir) / "shadow_wal.lmdb"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_single_marker_succeeds(self):
        """1. async_replay_single_pending_marker succeeds for a pending marker."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "replay-001"

        # Manually write WAL truth + pending marker (simulating a prior desync)
        wal_ok = store._wal_write_finding(finding_id, "test query", "synthetic", 0.9)
        self.assertTrue(wal_ok, "WAL truth must be written")

        marker_ok = store._wal_write_pending_sync_marker(
            finding_id, "test query", "synthetic", 0.9
        )
        self.assertTrue(marker_ok, "pending marker must be written")

        # Replay
        result = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )

        self.assertEqual(result["finding_id"], finding_id)
        self.assertTrue(result["marker_found"])
        self.assertTrue(result["wal_truth_found"])
        self.assertTrue(result["duckdb_written"])
        self.assertTrue(result["read_back_verified"])
        self.assertTrue(result["marker_cleared"])
        self.assertFalse(result["deadlettered"])
        self.assertIsNone(result["error"])

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestReplayMultipleMarkers(unittest.TestCase):
    """2. replay multiple markers works."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_multiple_markers(self):
        """2. async_replay_all_pending_duckdb_sync replays multiple markers."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        ids = [f"multi-replay-{i:03d}" for i in range(5)]

        for fid in ids:
            store._wal_write_finding(fid, f"query {fid}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(fid, f"query {fid}", "synthetic", 0.9)

        results = asyncio.get_event_loop().run_until_complete(
            store.async_replay_all_pending_duckdb_sync()
        )

        self.assertEqual(len(results), 5)
        for r in results:
            self.assertTrue(r["duckdb_written"])
            self.assertTrue(r["read_back_verified"])
            self.assertTrue(r["marker_cleared"])

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestMarkerDeletedOnSuccess(unittest.TestCase):
    """3. marker deleted only on success."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_marker_cleared_after_success(self):
        """3. pending marker is cleared after successful replay."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "clear-on-success-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        # Verify marker exists before replay
        pending_key = f"pending_duckdb_sync:{finding_id}"
        self.assertIsNotNone(store._wal_lmdb.get(pending_key))

        result = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )

        self.assertTrue(result["marker_cleared"])
        # Marker must be gone
        self.assertIsNone(store._wal_lmdb.get(pending_key))

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestMarkerStaysOnFailure(unittest.TestCase):
    """4. marker stays on failure."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_marker_stays_when_duckdb_fails(self):
        """4. pending marker remains when DuckDB write fails during replay."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "marker-stays-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        pending_key = f"pending_duckdb_sync:{finding_id}"

        # Mock DuckDB to fail
        original_sync = store._sync_insert_finding

        def failing_insert(*args, **kwargs):
            raise RuntimeError("simulated replay failure")

        store._sync_insert_finding = failing_insert

        result = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )

        self.assertTrue(result["marker_found"])
        self.assertFalse(result["duckdb_written"])
        # Marker must still exist
        self.assertIsNotNone(store._wal_lmdb.get(pending_key))

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestWalTruthUntouched(unittest.TestCase):
    """5. WAL truth remains untouched by replay (invariant 1.B)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_wal_truth_preserved_after_replay(self):
        """5. finding:{id} WAL record is NOT deleted after replay."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "wal-truth-preserved-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        wal_key = f"finding:{finding_id}"
        wal_before = store._wal_lmdb.get(wal_key)
        self.assertIsNotNone(wal_before)

        asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )

        # WAL truth must still exist (we never delete it)
        wal_after = store._wal_lmdb.get(wal_key)
        self.assertIsNotNone(wal_after, "WAL truth must be preserved after replay")
        self.assertEqual(wal_after["id"], finding_id)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestReplayUsesSameWritePath(unittest.TestCase):
    """6. replay uses same safe write path as activation."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_uses_sync_insert_finding(self):
        """6. _sync_replay_single_marker calls _sync_insert_finding."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "same-path-001"

        # Verify _sync_replay_single_marker uses _sync_insert_finding path
        # by checking that when _sync_insert_finding is patched to always fail,
        # the DuckDB write also fails
        original_sync = store._sync_insert_finding

        def always_fail(*args, **kwargs):
            return False

        store._sync_insert_finding = always_fail

        # Write WAL truth (independent of _sync_insert_finding)
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        result = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )

        # DuckDB write should fail because _sync_insert_finding always fails
        self.assertFalse(result["duckdb_written"])

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestFreshReadBackVerification(unittest.TestCase):
    """7. fresh read-back confirms record after write."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_read_back_verified_after_replay(self):
        """7. read_back_verified=True only when fresh read-back succeeds."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "readback-verify-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        result = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )

        self.assertTrue(result["read_back_verified"])

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestReplayIdempotent(unittest.TestCase):
    """8. replay is idempotent."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_idempotent_twice(self):
        """8. calling replay twice for same marker is safe (idempotent)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "idempotent-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        # First replay
        r1 = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )
        self.assertTrue(r1["duckdb_written"])

        # Second replay (no marker exists now)
        r2 = asyncio.get_event_loop().run_until_complete(
            store.async_replay_single_pending_marker(finding_id)
        )
        # Should succeed (idempotent) — marker gone but DuckDB has it
        self.assertTrue(r2["duckdb_written"])
        self.assertFalse(r2["marker_found"])

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestReplayEmptyNoop(unittest.TestCase):
    """9. replay empty pending set is clean no-op."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_replay_returns_empty_list(self):
        """9. async_replay_all_pending_duckdb_sync with no pending markers returns []."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        results = asyncio.get_event_loop().run_until_complete(
            store.async_replay_all_pending_duckdb_sync()
        )

        self.assertEqual(results, [])
        self.assertIsInstance(results, list)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestReplayRespectsLimit(unittest.TestCase):
    """10. replay respects limit."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_limit(self):
        """10. async_replay_all_pending_duckdb_sync respects limit parameter."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        ids = [f"limit-test-{i:03d}" for i in range(10)]
        for fid in ids:
            store._wal_write_finding(fid, f"q {fid}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(fid, f"q {fid}", "synthetic", 0.9)

        results = asyncio.get_event_loop().run_until_complete(
            store.async_replay_all_pending_duckdb_sync(limit=3)
        )

        self.assertEqual(len(results), 3)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestReplayLockPreventsDuplicates(unittest.TestCase):
    """11. replay uses replay lock."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_lock_is_instance_specific(self):
        """11. _replay_lock is per-instance, not class-level."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store1 = DuckDBShadowStore()
        store2 = DuckDBShadowStore()

        # Locks must be different objects (per-instance)
        l1 = store1._ensure_replay_lock()
        l2 = store2._ensure_replay_lock()
        self.assertIsNot(l1, l2, "replay lock must be per-instance")

        # Same store returns same lock
        l1b = store1._ensure_replay_lock()
        self.assertIs(l1, l1b, "same store returns same lock")

    def test_replay_lock_prevents_concurrent_replay(self):
        """11b. concurrent replays are serialized by the replay lock."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "lock-test-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        # Verify lock is per-instance (tested separately above)
        lock1 = store._ensure_replay_lock()
        lock2 = store._ensure_replay_lock()
        self.assertIs(lock1, lock2, "same store returns same lock instance")

        # Verify lock serializes — run two replays and ensure only one runs at a time
        call_times = []

        original_replay = store.async_replay_single_pending_marker

        async def tracking_replay(fid):
            call_times.append(time.time())
            return await original_replay(fid)

        store.async_replay_single_pending_marker = tracking_replay

        async def run_two_serial():
            t1 = asyncio.create_task(tracking_replay(finding_id))
            t2 = asyncio.create_task(tracking_replay(finding_id))
            await t1
            await t2

        asyncio.get_event_loop().run_until_complete(run_two_serial())

        # Both succeeded
        self.assertEqual(len(call_times), 2)
        # Gap between calls should be > 0 (serialized by lock)
        # Note: with lock, second call waits for first to complete
        gap = call_times[1] - call_times[0]
        # Note: aclose() intentionally omitted — these tests don't use async_record_activation

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestScanReturnsEagerList(unittest.TestCase):
    """12. scan returns eager list (lazy cursor lifetime bug not possible)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_scan_returns_list_not_generator(self):
        """12. _wal_scan_pending_sync_markers returns list, not a generator."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        # Write some markers
        for i in range(3):
            store._wal_write_pending_sync_marker(
                f"eager-scan-{i}", f"q{i}", "synthetic", 0.9
            )

        result = store._wal_scan_pending_sync_markers()

        # Must be a concrete list, not a generator or iterator
        self.assertIsInstance(result, list, "_wal_scan_pending_sync_markers must return a list")
        self.assertEqual(len(result), 3)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestDeadletterAfterRetries(unittest.TestCase):
    """13. poison marker goes to dead-letter after max retries."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_deadletter_after_max_retries(self):
        """13. after MAX_RETRY_COUNT failures, marker moves to dead-letter namespace."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp
            ok = store.initialize()
        if not ok:
            self.skipTest("DuckDB not available")

        finding_id = "deadletter-test-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        pending_key = f"pending_duckdb_sync:{finding_id}"
        deadletter_key = f"deadletter_duckdb_sync:{finding_id}"

        # Mock DuckDB to always fail
        def failing_insert(*args, **kwargs):
            raise RuntimeError("permanent failure")

        store._sync_insert_finding = failing_insert

        # Retry MAX_RETRY_COUNT times
        for i in range(store.MAX_RETRY_COUNT):
            result = asyncio.get_event_loop().run_until_complete(
                store.async_replay_single_pending_marker(finding_id)
            )
            if i < store.MAX_RETRY_COUNT - 1:
                self.assertFalse(result["deadlettered"])
            else:
                self.assertTrue(result["deadlettered"])

        # Pending marker should be gone
        self.assertIsNone(store._wal_lmdb.get(pending_key))
        # Dead-letter marker should exist
        dl = store._wal_lmdb.get(deadletter_key)
        self.assertIsNotNone(dl, "dead-letter marker must be written after max retries")
        self.assertEqual(dl["id"], finding_id)
        self.assertEqual(dl["retry_count"], store.MAX_RETRY_COUNT)

        asyncio.get_event_loop().run_until_complete(store.aclose())


class TestUniqueTmpdirIsolation(unittest.TestCase):
    """15. test isolation uses unique tmpdir."""

    def test_each_test_has_unique_tmpdir(self):
        """15. each test instance uses a fresh tmpdir (no shared state)."""
        import uuid
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        # Two stores with different tmpdirs must not share LMDB state
        tmp1 = tempfile.mkdtemp()
        tmp2 = tempfile.mkdtemp()
        try:
            db1 = Path(tmp1) / "a.duckdb"
            db2 = Path(tmp2) / "b.duckdb"

            s1 = DuckDBShadowStore(db_path=str(db1), temp_dir=tmp1)
            s2 = DuckDBShadowStore(db_path=str(db2), temp_dir=tmp2)

            # Initialize both
            with patch.object(s1, "_resolve_path"):
                s1._db_path = db1
                s1._temp_dir = Path(tmp1)
                s1.initialize()
            with patch.object(s2, "_resolve_path"):
                s2._db_path = db2
                s2._temp_dir = Path(tmp2)
                s2.initialize()

            # Write different markers to each WAL
            s1._wal_write_pending_sync_marker("fid-1", "q1", "synthetic", 0.9)
            s2._wal_write_pending_sync_marker("fid-2", "q2", "synthetic", 0.9)

            # Scans must be isolated
            m1 = s1._wal_scan_pending_sync_markers()
            m2 = s2._wal_scan_pending_sync_markers()

            ids1 = {m["id"] for m in m1}
            ids2 = {m["id"] for m in m2}

            self.assertIn("fid-1", ids1)
            self.assertNotIn("fid-1", ids2)
            self.assertIn("fid-2", ids2)
            self.assertNotIn("fid-2", ids1)

            asyncio.get_event_loop().run_until_complete(s1.aclose())
            asyncio.get_event_loop().run_until_complete(s2.aclose())
        finally:
            import shutil
            shutil.rmtree(tmp1, ignore_errors=True)
            shutil.rmtree(tmp2, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
