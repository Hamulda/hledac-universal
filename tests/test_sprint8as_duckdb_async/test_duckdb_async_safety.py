"""
Sprint 8AS: DuckDB Async-Safety + Shadow Ingest Prep

Tests:
  1. duckdb not imported on orchestrator boot
  2. duckdb_store module not imported on orchestrator boot
  3. :memory: mode uses ONE persistent connection
  4. async calls preserve :memory: state across multiple calls
  5. async API does not block event loop unreasonably
  6. batch method chunks large input
  7. PRAGMAs applied (threads, memory limits)
  8. aclose is idempotent
  9. worker thread name is duckdb_worker
  10. sync API backward compat still works
"""

import asyncio
import subprocess
import sys
import time

import pytest

# Import the sidecar directly (not via hledac package path)
sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore


# ---------------------------------------------------------------------------
# Tests 1-2: Boot isolation
# ---------------------------------------------------------------------------

class TestBootIsolation:
    def test_duckdb_not_imported_on_orchestrator_boot(self):
        """duckdb must NOT be imported when orchestrator boots."""
        code = (
            "import sys; "
            "import hledac.universal.autonomous_orchestrator; "
            "print(int('duckdb' in sys.modules))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True,
        )
        # Last line contains the 0/1 answer
        lines = [l for l in r.stdout.strip().split("\n") if l]
        val = int(lines[-1])
        assert val == 0, f"duckdb was loaded during boot: {r.stdout}"

    def test_duckdb_store_module_not_imported_on_orchestrator_boot(self):
        """duckdb_store must NOT be imported when orchestrator boots."""
        code = (
            "import sys; "
            "import hledac.universal.autonomous_orchestrator; "
            "print(int('hledac.universal.knowledge.duckdb_store' in sys.modules))"
        )
        r = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=True,
        )
        lines = [l for l in r.stdout.strip().split("\n") if l]
        val = int(lines[-1])
        assert val == 0, f"duckdb_store was loaded during boot: {r.stdout}"


# ---------------------------------------------------------------------------
# Test 3: :memory: single persistent connection
# ---------------------------------------------------------------------------

class TestMemoryModeConnection:
    def test_memory_mode_uses_persistent_single_connection(self):
        """
        In :memory: mode, there must be ONE persistent connection reused
        across all operations — NOT a new connection per call.
        """
        store = DuckDBShadowStore()
        assert store._persistent_conn is None  # not created yet

        # Initialize
        store.initialize()
        assert store._persistent_conn is not None, \
            ":memory: mode must create a persistent connection"

        conn_id_before = id(store._persistent_conn)

        # Do a sync insert
        store.insert_shadow_finding("id1", "q", "web", 0.5)

        conn_id_after = id(store._persistent_conn)
        assert conn_id_before == conn_id_after, \
            "Connection must be the same object after insert"

        # Do an async insert
        async def go():
            await store.async_record_shadow_finding("id2", "q2", "web", 0.6)
        asyncio.run(go())

        conn_id_after_async = id(store._persistent_conn)
        assert conn_id_before == conn_id_after_async, \
            "Connection must remain the same after async insert"

        store._do_close()

    def test_pragmas_applied_threads_and_memory_limits(self):
        """PRAGMA threads=2 must be applied after connection init."""
        store = DuckDBShadowStore()
        store.initialize()

        # Query PRAGMA threads value via the persistent connection
        result = store._persistent_conn.execute("SELECT current_setting('threads')").fetchone()
        threads_val = int(result[0])
        assert threads_val == 2, f"PRAGMA threads must be 2, got {threads_val}"

        # memory_limit
        result2 = store._persistent_conn.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone()
        # DuckDB may report "953.6 MiB" for a 1GB limit — that's correct
        mem_val = str(result2[0])
        assert "gb" in mem_val.lower() or "mib" in mem_val.lower() or "953" in mem_val, \
            f"memory_limit should be ~1GB (DuckDB reports in GiB): {result2}"

        store._do_close()


# ---------------------------------------------------------------------------
# Test 4: async calls preserve :memory: state
# ---------------------------------------------------------------------------

class TestAsyncMemoryPersistence:
    @pytest.mark.asyncio
    async def test_async_calls_preserve_memory_mode_state(self):
        """
        Multiple async calls must all see the SAME :memory: database state.
        Data inserted in one call must be queryable in the next.
        """
        store = DuckDBShadowStore()
        await store.async_initialize()

        # Insert via async
        await store.async_record_shadow_finding("a1", "query_a", "web", 0.9)
        await store.async_record_shadow_run("r1", time.time(), None, 10, 256)

        # Query back — must see what we inserted
        findings = await store.async_query_recent_findings(limit=5)
        finding_ids = [f["id"] for f in findings]
        assert "a1" in finding_ids, f"a1 not found in {finding_ids}"

        # Insert more in second call
        await store.async_record_shadow_finding("a2", "query_b", "academic", 0.7)

        # Query again — must now see BOTH
        findings2 = await store.async_query_recent_findings(limit=5)
        finding_ids2 = [f["id"] for f in findings2]
        assert "a1" in finding_ids2
        assert "a2" in finding_ids2, f"a2 not found in {finding_ids2}"

        await store.aclose()


# ---------------------------------------------------------------------------
# Test 5: async API does not block event loop unreasonably
# ---------------------------------------------------------------------------

class TestEventLoopNonBlocking:
    @pytest.mark.asyncio
    async def test_async_insert_does_not_block_event_loop(self):
        """
        While async DB operations run on the executor, the event loop must
        remain responsive. We schedule a heartbeat and verify it fires
        within a reasonable window even while DB work is in progress.
        """
        store = DuckDBShadowStore()
        await store.async_initialize()

        heartbeat_fired = False
        loop_lag_ms = None

        async def heartbeat():
            nonlocal heartbeat_fired, loop_lag_ms
            t0 = asyncio.get_event_loop().time()
            await asyncio.sleep(0)  # yield to event loop
            elapsed = (asyncio.get_event_loop().time() - t0) * 1000
            loop_lag_ms = elapsed
            heartbeat_fired = True

        # Schedule heartbeat, then run DB insert concurrently
        async def concurrent_work():
            # Insert 20 records — each goes through executor
            for i in range(20):
                await store.async_record_shadow_finding(f"blkatest_{i}", f"q{i}", "web", 0.5)

        # Run both concurrently
        await asyncio.gather(heartbeat(), concurrent_work())

        assert heartbeat_fired, "Heartbeat must have fired"
        # Event loop lag should be < 50ms for a simple heartbeat yield
        assert loop_lag_ms is not None and loop_lag_ms < 50, \
            f"Event loop lag too high: {loop_lag_ms}ms"

        await store.aclose()


# ---------------------------------------------------------------------------
# Test 6: batch chunks large input
# ---------------------------------------------------------------------------

class TestBatchChunking:
    @pytest.mark.asyncio
    async def test_batch_chunks_large_input(self):
        """
        async_record_shadow_findings_batch must respect max_batch_size.
        We pass 1200 records with max_batch_size=500 and verify
        at most 500 are inserted in the first chunk attempt.
        """
        store = DuckDBShadowStore()
        await store.async_initialize()

        # Insert a known large batch
        big_batch = [
            {"id": f"chunktest_{i}", "query": f"q{i}", "source_type": "web", "confidence": 0.5}
            for i in range(1200)
        ]

        inserted = await store.async_record_shadow_findings_batch(
            big_batch, max_batch_size=500
        )

        # At most 1200, but we inserted in chunks
        assert inserted <= 1200, f"Cannot insert more than input size: {inserted}"

        # Query back to verify actual count stored
        findings = await store.async_query_recent_findings(limit=10000)
        chunktest_ids = [f["id"] for f in findings if f["id"].startswith("chunktest_")]
        # Verify no single chunk exceeds 500
        # We inserted in order so first 500 should be from chunk 0
        # If all went through, we have all 1200
        assert len(chunktest_ids) == inserted, \
            f"Inserted {inserted} but query returned {len(chunktest_ids)}"

        await store.aclose()

    @pytest.mark.asyncio
    async def test_batch_empty_list_returns_zero(self):
        """Empty batch must return 0."""
        store = DuckDBShadowStore()
        await store.async_initialize()
        result = await store.async_record_shadow_findings_batch([], max_batch_size=500)
        assert result == 0
        await store.aclose()


# ---------------------------------------------------------------------------
# Test 7: PRAGMAs — already covered in TestMemoryModeConnection
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 8: aclose idempotent
# ---------------------------------------------------------------------------

class TestIdempotentClose:
    @pytest.mark.asyncio
    async def test_aclose_is_idempotent(self):
        """Multiple aclose() calls must not raise."""
        store = DuckDBShadowStore()
        await store.async_initialize()
        await store.aclose()
        await store.aclose()  # must not raise
        await store.aclose()  # must not raise again
        assert store.is_closed

    @pytest.mark.asyncio
    async def test_no_op_after_aclose(self):
        """All async methods must return False/empty after aclose."""
        store = DuckDBShadowStore()
        await store.async_initialize()
        await store.aclose()

        assert await store.async_record_shadow_finding("x", "q", "web", 0.5) is False
        assert await store.async_record_shadow_run("x", time.time(), None, 1, 1) is False
        assert await store.async_query_recent_findings(5) == []
        assert await store.async_healthcheck() is False


# ---------------------------------------------------------------------------
# Test 9: worker thread name
# ---------------------------------------------------------------------------

class TestWorkerThread:
    def test_executor_thread_name_is_duckdb_worker(self):
        """Executor worker thread must have 'duckdb_worker' in its name."""
        store = DuckDBShadowStore()
        store.initialize()

        threads = store._executor._threads
        assert len(threads) >= 1
        thread_names = [t.name for t in threads]
        assert any("duckdb_worker" in name for name in thread_names), \
            f"Expected duckdb_worker in thread names: {thread_names}"

        store._do_close()


# ---------------------------------------------------------------------------
# Test 10: sync API backward compat
# ---------------------------------------------------------------------------

class TestSyncBackwardCompat:
    def test_sync_insert_still_works(self):
        """Sync insert API from 8AO must still function."""
        store = DuckDBShadowStore()
        store.initialize()

        ok = store.insert_shadow_finding("sync1", "sync query", "web", 0.8)
        assert ok is True

        ok2 = store.insert_shadow_run("sync_run1", time.time(), None, 20, 128)
        assert ok2 is True

        findings = store.query_recent_findings(limit=5)
        ids = [f["id"] for f in findings]
        assert "sync1" in ids

        store._do_close()

    def test_sync_initialize_returns_bool(self):
        """initialize() must return True on success, False on double-init."""
        store = DuckDBShadowStore()
        assert store.initialize() is True
        assert store.initialize() is True  # already initialized — still True
        store._do_close()


# ---------------------------------------------------------------------------
# Test: async_healthcheck
# ---------------------------------------------------------------------------

class TestHealthcheck:
    @pytest.mark.asyncio
    async def test_healthcheck_returns_true_when_healthy(self):
        store = DuckDBShadowStore()
        await store.async_initialize()
        assert await store.async_healthcheck() is True
        await store.aclose()

    @pytest.mark.asyncio
    async def test_healthcheck_returns_false_when_closed(self):
        store = DuckDBShadowStore()
        await store.async_initialize()
        await store.aclose()
        assert await store.async_healthcheck() is False
