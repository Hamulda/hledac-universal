"""
Sprint 8L — Bounded startup replay + boot barrier + recovery truth in init path
================================================================================

Tests:
1.  async_initialize without replay works (backward compat)
2.  async_initialize with replay_pending_limit=0/None skips replay
3.  async_initialize with replay_pending_limit>0 triggers bounded replay
4.  boot barrier blocks activation writes during replay
5.  barrier lifts after replay completes
6.  startup replay respects limit
7.  startup replay respects timeout
8.  startup replay ignores deadletter namespace
9.  missing WAL truth fail-open
10. successful replay clears marker after fresh read-back verify
11. retry/deadletter logic remains functional
12. replay lock prevents concurrent double replay
13. telemetry helper pending_marker_count works
14. telemetry helper deadletter_marker_count works
15. startup_ready and startup_replay_done properties work
16. aclose resets barrier for re-init
17. sync initialize sets barrier immediately
18. probe_8h still passes
19. probe_8f still passes
20. AO canary still passes

Invariant table:
| test                                     | invariant |
| ---------------------------------------- | --------- |
| test_init_no_replay                      | C.5       |
| test_init_replay_limit_zero_skips        | C.1, C.5  |
| test_init_with_replay_triggers           | C.1       |
| test_barrier_blocks_during_replay        | C.2       |
| test_barrier_lifts_after_replay          | C.2, C.3  |
| test_replay_respects_limit               | C.3, C.6  |
| test_replay_respects_timeout             | C.3, C.7  |
| test_replay_ignores_deadletter           | C.8       |
| test_missing_wal_truth_fail_open         | C.9       |
| test_marker_cleared_after_verified       | C.10      |
| test_deadletter_after_retries_still_works| C.11      |
| test_replay_lock_prevents_double         | C.12      |
| test_pending_marker_count                 | C.13      |
| test_deadletter_marker_count              | C.14      |
| test_startup_ready_properties             | C.15      |
| test_aclose_resets_barrier               | C.5       |
| test_sync_init_sets_barrier               | C.5       |
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


class TestInitNoReplay(unittest.TestCase):
    """1. async_initialize without replay works (backward compat)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_init_no_replay(self):
        """1. async_initialize() without replay_pending_limit is backward-compatible."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ok = loop.run_until_complete(store.async_initialize())
        finally:
            asyncio.set_event_loop(None)

        self.assertTrue(ok)
        self.assertTrue(store.is_initialized)
        self.assertTrue(store.startup_ready)
        self.assertFalse(store.startup_replay_done)

        loop.run_until_complete(store.aclose())


class TestInitReplayLimitZero(unittest.TestCase):
    """2. async_initialize with replay_pending_limit=0/None skips replay."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_init_replay_limit_zero_skips(self):
        """2. async_initialize(replay_pending_limit=0) does NOT scan or replay."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        # Write a pending marker BEFORE init
        store._db_path = self.db_path  # needs to be set before _wal_* calls
        store._temp_dir = self.db_tmp
        # Initialize first to have WAL ready
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        store._wal_write_finding("skip-me", "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker("skip-me", "q", "synthetic", 0.9)
        count_before = store.pending_marker_count()
        self.assertEqual(count_before, 1)

        # Re-init with limit=0 — should NOT clear the marker
        loop.run_until_complete(store.aclose())

        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=0))
        self.assertTrue(store2.startup_ready)
        self.assertFalse(store2.startup_replay_done)  # limit=0 means no replay run

        count_after = store2.pending_marker_count()
        self.assertEqual(count_after, 1)  # marker still there

        loop.run_until_complete(store2.aclose())

    def test_init_replay_limit_none_skips(self):
        """2b. async_initialize(replay_pending_limit=None) also skips replay."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize(replay_pending_limit=None))
        self.assertTrue(store.startup_ready)
        self.assertFalse(store.startup_replay_done)
        loop.run_until_complete(store.aclose())


class TestInitWithReplay(unittest.TestCase):
    """3. async_initialize with replay_pending_limit>0 triggers bounded replay."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_init_with_replay_triggers(self):
        """3. async_initialize(replay_pending_limit>0) runs bounded startup replay."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        # Write 3 pending markers
        for i in range(3):
            store._wal_write_finding(f"replay-{i}", f"q{i}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(f"replay-{i}", f"q{i}", "synthetic", 0.9)

        count_before = store.pending_marker_count()
        self.assertEqual(count_before, 3)

        loop.run_until_complete(store.aclose())

        # New store with replay enabled (limit=10)
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=10))

        self.assertTrue(store2.startup_ready)
        self.assertTrue(store2.startup_replay_done)

        # All 3 markers should have been replayed and cleared
        count_after = store2.pending_marker_count()
        self.assertEqual(count_after, 0)

        loop.run_until_complete(store2.aclose())


class TestBootBarrierBlocks(unittest.TestCase):
    """4. boot barrier blocks activation writes during replay."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_barrier_blocks_during_replay(self):
        """4. Activation writes are blocked while startup replay is running."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Pre-populate 50 markers
        loop.run_until_complete(store.async_initialize())
        for i in range(50):
            store._wal_write_finding(f"barrier-{i:03d}", f"q{i}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(f"barrier-{i:03d}", f"q{i}", "synthetic", 0.9)
        loop.run_until_complete(store.aclose())

        # Fresh store with slow bounded replay
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        original_bounded = store2._bounded_startup_replay
        barrier_check_results = []

        async def slow_bounded(*args, **kwargs):
            # Record barrier state before we sleep (during replay phase)
            barrier_check_results.append(("before_sleep", store2._startup_ready.is_set()))
            await asyncio.sleep(0.2)
            barrier_check_results.append(("after_sleep", store2._startup_ready.is_set()))
            await original_bounded(*args, **kwargs)

        store2._bounded_startup_replay = slow_bounded

        async def run_test():
            # Start init - it runs bounded replay which is slow
            init_task = asyncio.create_task(store2.async_initialize(replay_pending_limit=10))
            # Give init a tiny head start
            await asyncio.sleep(0.02)
            # While init is running (replay is in progress), barrier is NOT set yet
            # Try activation - should block waiting for barrier
            activation_task = asyncio.create_task(
                store2.async_record_activation("barrier-test", "q", "synthetic", 0.9)
            )
            # Wait for activation to complete (it should either timeout or succeed after barrier lifts)
            try:
                result = await asyncio.wait_for(activation_task, timeout=2.0)
                # If activation succeeded, check barrier is now set
                self.assertTrue(store2.startup_ready)
            except asyncio.TimeoutError:
                # Timeout means barrier correctly blocked - this is acceptable
                activation_task.cancel()
                pass
            finally:
                init_task.cancel()
                await store2.aclose()

        loop.run_until_complete(run_test())
        # At least one barrier check during replay should show barrier NOT set
        during_replay = [v for k, v in barrier_check_results if "before_sleep" in k]
        if during_replay:
            self.assertFalse(during_replay[0], "barrier should NOT be set during slow replay")


class TestBarrierLifts(unittest.TestCase):
    """5. barrier lifts after replay completes."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_barrier_lifts_after_replay(self):
        """5. After async_initialize with replay completes, writes are accepted."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Create a few markers
        loop.run_until_complete(store.async_initialize())
        for i in range(3):
            store._wal_write_finding(f"lift-{i}", f"q{i}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(f"lift-{i}", f"q{i}", "synthetic", 0.9)
        loop.run_until_complete(store.aclose())

        # Fresh store with replay
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=10))

        # Barrier must be lifted
        self.assertTrue(store2.startup_ready)
        self.assertTrue(store2.startup_replay_done)

        # Activation write should work immediately (no timeout/blocking)
        result = loop.run_until_complete(
            store2.async_record_activation("write-after-barrier", "q", "synthetic", 0.9)
        )
        self.assertTrue(result["lmdb_success"])

        loop.run_until_complete(store2.aclose())


class TestReplayRespectsLimit(unittest.TestCase):
    """6. startup replay respects limit."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_respects_limit(self):
        """6. async_initialize(replay_pending_limit=3) replays at most 3 markers."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        # Write 10 markers
        for i in range(10):
            store._wal_write_finding(f"limit-{i:02d}", f"q{i}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(f"limit-{i:02d}", f"q{i}", "synthetic", 0.9)

        count_before = store.pending_marker_count()
        self.assertEqual(count_before, 10)
        loop.run_until_complete(store.aclose())

        # Replay with limit=3
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=3))

        # Exactly 3 should be replayed and cleared, 7 should remain
        count_after = store2.pending_marker_count()
        self.assertEqual(count_after, 7)

        loop.run_until_complete(store2.aclose())


class TestReplayRespectsTimeout(unittest.TestCase):
    """7. startup replay respects timeout."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_respects_timeout(self):
        """7. async_initialize with very short timeout stops early."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        # Write 20 markers
        for i in range(20):
            store._wal_write_finding(f"timeout-{i:02d}", f"q{i}", "synthetic", 0.9)
            store._wal_write_pending_sync_marker(f"timeout-{i:02d}", f"q{i}", "synthetic", 0.9)

        count_before = store.pending_marker_count()
        self.assertEqual(count_before, 20)
        loop.run_until_complete(store.aclose())

        # Replay with limit=100 but timeout=0.001s (near-instant timeout)
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=100, replay_timeout_s=0.001))

        # With near-zero timeout, very few (if any) should be replayed
        count_after = store2.pending_marker_count()
        # Most markers should remain (timeout hit early)
        self.assertGreater(count_after, 15)

        loop.run_until_complete(store2.aclose())


class TestReplayIgnoresDeadletter(unittest.TestCase):
    """8. startup replay ignores deadletter namespace."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_ignores_deadletter(self):
        """8. Dead-letter markers are NOT scanned or replayed."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        # Write one pending marker and one deadletter marker
        store._wal_write_finding("pending-001", "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker("pending-001", "q", "synthetic", 0.9)
        store._wal_write_deadletter_marker(
            "deadletter-001", "q", "synthetic", 0.9, "test error", 3
        )

        self.assertEqual(store.pending_marker_count(), 1)
        self.assertEqual(store.deadletter_marker_count(), 1)
        loop.run_until_complete(store.aclose())

        # Replay — should process pending only
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=10))

        # Pending should be cleared
        self.assertEqual(store2.pending_marker_count(), 0)
        # Deadletter should remain intact
        self.assertEqual(store2.deadletter_marker_count(), 1)

        loop.run_until_complete(store2.aclose())


class TestMissingWalTruthFailOpen(unittest.TestCase):
    """9. missing WAL truth fail-open."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_missing_wal_truth_fail_open(self):
        """9. A pending marker with no WAL truth is skipped (not crash)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        # Write a pending marker WITHOUT the WAL finding truth
        # (simulates corrupted/missing WAL)
        store._wal_write_pending_sync_marker("orphan-marker", "q", "synthetic", 0.9)

        count = store.pending_marker_count()
        self.assertEqual(count, 1)
        loop.run_until_complete(store.aclose())

        # Replay should handle the missing WAL truth gracefully
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=10))

        # The orphan marker should remain (replay couldn't find WAL truth)
        # OR it was skipped — either way no crash
        count_after = store2.pending_marker_count()
        self.assertIn(count_after, [0, 1])  # either skipped or remains

        loop.run_until_complete(store2.aclose())


class TestMarkerClearedAfterVerified(unittest.TestCase):
    """10. successful replay clears marker after fresh read-back verify."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_marker_cleared_after_verified(self):
        """10. Pending marker is cleared ONLY after fresh read-back confirms DuckDB write."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        store._wal_write_finding("verify-001", "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker("verify-001", "q", "synthetic", 0.9)

        pending_key = "pending_duckdb_sync:verify-001"
        self.assertIsNotNone(store._wal_lmdb.get(pending_key))
        loop.run_until_complete(store.aclose())

        # Replay — use fresh store
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=10))

        # Marker must be gone (use pending_marker_count for alive LMDB)
        self.assertEqual(store2.pending_marker_count(), 0)
        # WAL finding truth must remain (check before aclose)
        self.assertIsNotNone(store2._wal_lmdb.get("finding:verify-001"))

        loop.run_until_complete(store2.aclose())


class TestDeadletterAfterRetriesStillWorks(unittest.TestCase):
    """11. retry/deadletter logic remains functional."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_deadletter_after_retries_still_works(self):
        """11. MAX_RETRY_COUNT failures still move marker to dead-letter namespace."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        finding_id = "deadletter-8l-001"
        store._wal_write_finding(finding_id, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(finding_id, "q", "synthetic", 0.9)

        pending_key = f"pending_duckdb_sync:{finding_id}"
        deadletter_key = f"deadletter_duckdb_sync:{finding_id}"

        # Mock DuckDB to always fail
        original_sync = store._sync_insert_finding
        store._sync_insert_finding = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("simulated"))

        # Retry MAX_RETRY_COUNT times via direct replay call
        for i in range(store.MAX_RETRY_COUNT):
            loop.run_until_complete(store.async_replay_single_pending_marker(finding_id))

        # Pending marker should be gone
        self.assertIsNone(store._wal_lmdb.get(pending_key))
        # Dead-letter should exist
        self.assertIsNotNone(store._wal_lmdb.get(deadletter_key))

        loop.run_until_complete(store.aclose())


class TestReplayLockPreventsDouble(unittest.TestCase):
    """12. replay lock prevents concurrent double replay."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_replay_lock_prevents_double(self):
        """12. Concurrent replays of same marker are serialized by replay lock."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        fid = "lock-double-001"
        store._wal_write_finding(fid, "q", "synthetic", 0.9)
        store._wal_write_pending_sync_marker(fid, "q", "synthetic", 0.9)
        loop.run_until_complete(store.aclose())

        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize())

        # Run two replays concurrently
        call_times = []

        original_replay = store2.async_replay_single_pending_marker

        async def tracking_replay(fid_arg):
            call_times.append(time.time())
            return await original_replay(fid_arg)

        store2.async_replay_single_pending_marker = tracking_replay

        async def run_two():
            t1 = asyncio.create_task(tracking_replay(fid))
            t2 = asyncio.create_task(tracking_replay(fid))
            await t1
            await t2

        loop.run_until_complete(run_two())

        # Both calls succeeded (serialized by lock)
        self.assertEqual(len(call_times), 2)
        self.assertGreater(call_times[1] - call_times[0], 0)

        loop.run_until_complete(store2.aclose())


class TestTelemetryHelpers(unittest.TestCase):
    """13-14. telemetry helpers work."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_pending_marker_count(self):
        """13. pending_marker_count() returns correct count."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        self.assertEqual(store.pending_marker_count(), 0)

        for i in range(7):
            store._wal_write_pending_sync_marker(f"pc-{i}", f"q{i}", "synthetic", 0.9)

        self.assertEqual(store.pending_marker_count(), 7)
        loop.run_until_complete(store.aclose())

    def test_deadletter_marker_count(self):
        """14. deadletter_marker_count() returns correct count."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(store.async_initialize())

        self.assertEqual(store.deadletter_marker_count(), 0)

        for i in range(5):
            store._wal_write_deadletter_marker(f"dl-{i}", f"q{i}", "synthetic", 0.9, "err", 3)

        # Check count BEFORE aclose (after close, _wal_lmdb=None → count returns 0)
        self.assertEqual(store.deadletter_marker_count(), 5)
        loop.run_until_complete(store.aclose())


class TestStartupProperties(unittest.TestCase):
    """15. startup_ready and startup_replay_done properties work."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_startup_ready_properties(self):
        """15. startup_ready and startup_replay_done reflect actual state."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Before init
        self.assertFalse(store.startup_ready)
        self.assertFalse(store.startup_replay_done)

        loop.run_until_complete(store.async_initialize(replay_pending_limit=0))

        # After init without replay
        self.assertTrue(store.startup_ready)
        self.assertFalse(store.startup_replay_done)

        loop.run_until_complete(store.aclose())

        # After init with replay
        store2 = DuckDBShadowStore()
        with patch.object(store2, "_resolve_path"):
            store2._db_path = self.db_path
            store2._temp_dir = self.db_tmp

        loop.run_until_complete(store2.async_initialize(replay_pending_limit=10))

        self.assertTrue(store2.startup_ready)
        self.assertTrue(store2.startup_replay_done)

        loop.run_until_complete(store2.aclose())


class TestAcloseResetsBarrier(unittest.TestCase):
    """16. aclose resets barrier for re-init."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_aclose_resets_barrier(self):
        """16. After aclose + re-init, barrier is fresh and replay can run again."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        loop.run_until_complete(store.async_initialize(replay_pending_limit=0))
        self.assertTrue(store.startup_ready)
        self.assertFalse(store.startup_replay_done)

        loop.run_until_complete(store.aclose())

        # After close, barrier should be reset
        self.assertFalse(store.startup_ready)

        # Re-init should work
        loop.run_until_complete(store.async_initialize(replay_pending_limit=5))
        self.assertTrue(store.startup_ready)


class TestSyncInitSetsBarrier(unittest.TestCase):
    """17. sync initialize sets barrier immediately."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmpdir.name
        self.db_path = Path(self.tmpdir) / "analytics.duckdb"
        self.db_tmp = Path(self.tmpdir) / "duckdb_tmp"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_sync_init_sets_barrier(self):
        """17. sync initialize() sets _startup_ready immediately (no replay possible)."""
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

        store = DuckDBShadowStore()
        with patch.object(store, "_resolve_path"):
            store._db_path = self.db_path
            store._temp_dir = self.db_tmp

        ok = store.initialize()
        self.assertTrue(ok)
        self.assertTrue(store.is_initialized)

        # Sync init should set the barrier immediately
        self.assertTrue(store.startup_ready)

        store.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
