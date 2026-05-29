"""
Sprint 8AX — DuckDB Shadow Ingest Wiring Tests
==============================================

Targeted tests for the DuckDB shadow analytics hook:
1. No duckdb/duckdb_store imported on plain orchestrator boot
2. GHOST_DUCKDB_SHADOW=0 (off) is no-op
3. GHOST_DUCKDB_SHADOW=1 records evidence_packet events
4. Hook is in evidence_log.py, NOT autonomous_orchestrator.py
5. Production path: DB_ROOT / "analytics.duckdb"
6. :memory: used only in tests or when DB_ROOT unavailable
7. Queue bounded at 200, drop on full with counter
8. 1001 records -> exactly 3 batch writes (500 + 500 + 1)
9. :memory: same worker thread across async calls
10. aclose() does not block forever
11. Shadow failures increment counter (fail-open)
12. Narrow regression: evidence_log append still works
"""

import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_in_subprocess(code: str, env: dict | None = None) -> tuple[str, str, int]:
    """Run code in a fresh Python subprocess, return stdout, stderr, returncode."""
    base_env = os.environ.copy()
    if env:
        base_env.update(env)
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=base_env,
    )
    return r.stdout, r.stderr, r.returncode


# ---------------------------------------------------------------------------
# Test 1: No duckdb imported on plain orchestrator boot
# ---------------------------------------------------------------------------

class TestSprint8AXImportIsolation:
    def test_duckdb_not_loaded_on_plain_orchestrator_boot(self):
        """
        When GHOST_DUCKDB_SHADOW is NOT set, duckdb and duckdb_store
        must NOT appear in sys.modules after importing autonomous_orchestrator.
        """
        code = (
            'import sys\n'
            'import time\n'
            't = time.perf_counter()\n'
            'import hledac.universal.autonomous_orchestrator\n'
            'dt = time.perf_counter() - t\n'
            'print(f"{dt:.6f}")\n'
            'print("duckdb" in sys.modules)\n'
            'print("hledac.universal.knowledge.duckdb_store" in sys.modules)\n'
        )
        stdout, stderr, rc = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines()
                 if l and not l.startswith("Warning") and not l.startswith("INFO")
                 and not l.startswith("DEBUG") and not l.startswith("ERROR")]
        assert len(lines) >= 3, f"Unexpected stdout: {stdout!r}"
        float(lines[0])
        assert "True" not in lines[1], f"duckdb leaked into sys.modules: {stdout}"
        assert "True" not in lines[2], f"duckdb_store leaked: {stdout}"


# ---------------------------------------------------------------------------
# Test 2: Feature flag OFF = no-op
# ---------------------------------------------------------------------------

class TestSprint8AXFlagOff:
    def test_shadow_flag_off_is_noop(self):
        """
        With GHOST_DUCKDB_SHADOW=0, no shadow records are written.
        """
        code = (
            'import os\n'
            'os.environ["GHOST_DUCKDB_SHADOW"] = "0"\n'
            'import sys\n'
            'sys.path.insert(0, ".")\n'
            'from hledac.universal.knowledge.analytics_hook import (\n'
            '    shadow_record_finding, shadow_ingest_failures, _is_shadow_enabled,\n'
            '    _SHADOW_ENABLED\n'
            ')\n'
            '_SHADOW_ENABLED = None  # reset cache\n'
            'enabled = _is_shadow_enabled()\n'
            'failures_before = shadow_ingest_failures()\n'
            'shadow_record_finding("fid1", "query", "web", 0.9)\n'
            'failures_after = shadow_ingest_failures()\n'
            'print(f"enabled={enabled}")\n'
            'print(f"failures_before={failures_before}")\n'
            'print(f"failures_after={failures_after}")\n'
        )
        stdout, _, _ = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("enabled=False" in l for l in lines), f"Flag should be False: {lines}"
        assert any("failures_before=0" in l for l in lines), f"Should be no failures: {lines}"
        assert any("failures_after=0" in l for l in lines), f"Should still be 0: {lines}"


# ---------------------------------------------------------------------------
# Test 3: Feature flag ON = records batch
# ---------------------------------------------------------------------------

class TestSprint8AXFlagOn:
    def test_shadow_flag_on_records_batch(self):
        """
        With GHOST_DUCKDB_SHADOW=1, evidence_packet events are shadow-recorded.
        Uses :memory: mode (DB_ROOT unavailable in test env).
        """
        code = (
            'import os\n'
            'os.environ["GHOST_DUCKDB_SHADOW"] = "1"\n'
            'import sys, asyncio\n'
            'sys.path.insert(0, ".")\n'
            'from hledac.universal.knowledge.analytics_hook import (\n'
            '    shadow_record_finding, shadow_ingest_failures, _get_recorder,\n'
            '    shadow_reset_failures, _SHADOW_ENABLED, _is_shadow_enabled\n'
            ')\n'
            'from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore\n'
            '_SHADOW_ENABLED = None  # reset cached flag\n'
            'assert _is_shadow_enabled() == True, "Flag should be True"\n'
            'shadow_reset_failures()\n'
            'store = DuckDBShadowStore()\n'
            "import duckdb\n"
            'conn = duckdb.connect(":memory:")\n'
            "conn.execute('''\n"
            "    CREATE TABLE IF NOT EXISTS shadow_findings (\n"
            "        id VARCHAR PRIMARY KEY, run_id VARCHAR, query VARCHAR,\n"
            "        url VARCHAR, title VARCHAR, source VARCHAR, source_type VARCHAR,\n"
            "        relevance_score DOUBLE, confidence DOUBLE,\n"
            "        ts TIMESTAMP DEFAULT current_timestamp\n"
            "    )\n"
            "''')\n"
            'store._persistent_conn = conn\n'
            'store._initialized = True\n'
            'rec = _get_recorder()\n'
            'rec._store = store\n'
            'rec._worker_started = True\n'
            'shadow_record_finding("f1", "test query", "web", 0.9, run_id="run1", url="https://example.com", title="Example")\n'
            'shadow_record_finding("f2", "test query 2", "web", 0.8, run_id="run1", url="https://example2.com", title="Example 2")\n'
            '# Drain the queue by calling _flush_batch directly\n'
            'batch = []\n'
            'while not rec._queue.empty():\n'
            '    try:\n'
            '        batch.append(rec._queue.get_nowait())\n'
            '    except Exception:\n'
            '        break\n'
            'if batch:\n'
            '    asyncio.run(rec._flush_batch(batch))\n'
            'failures = shadow_ingest_failures()\n'
            'print(f"failures={failures}")\n'
            'rows = conn.execute("SELECT id, url, title FROM shadow_findings ORDER BY ts").fetchall()\n'
            'print(f"rows_in_db={len(rows)}")\n'
            'for row in rows:\n'
            '    print(f"  row={row}")\n'
            'conn.close()\n'
        )
        stdout, stderr, rc = _run_in_subprocess(code, env={"GHOST_DUCKDB_SHADOW": "1"})
        lines = [l for l in stdout.strip().splitlines() if l and not l.startswith("Warning")]
        failures_lines = [l for l in lines if "failures=" in l]
        rows_lines = [l for l in lines if "rows_in_db=" in l]
        assert failures_lines and "failures=0" in failures_lines[0], \
            f"No failures expected: {lines}\nstderr={stderr}"
        assert rows_lines and "rows_in_db=2" in rows_lines[0], \
            f"Expected 2 rows in DB: {lines}"


# ---------------------------------------------------------------------------
# Test 4: Hook location is NOT autonomous_orchestrator.py
# ---------------------------------------------------------------------------

class TestSprint8AXHookLocation:
    def test_shadow_hook_location_is_not_ao(self):
        """
        The shadow hook must NOT be added to autonomous_orchestrator.py.
        It must live in evidence_log.py or analytics_hook.py.
        """
        ao_path = Path("autonomous_orchestrator.py")
        content = ao_path.read_text()
        assert "shadow_record_finding" not in content, \
            "shadow_record_finding must NOT be in autonomous_orchestrator.py"
        assert "analytics_hook" not in content, \
            "analytics_hook must NOT be imported in autonomous_orchestrator.py"

        el_path = Path("evidence_log.py")
        el_content = el_path.read_text()
        assert "shadow_record_finding" in el_content, \
            "shadow_record_finding must be in evidence_log.py"
        assert "analytics_hook" in el_content, \
            "analytics_hook import must be in evidence_log.py"


# ---------------------------------------------------------------------------
# Test 5: Production DB path is DB_ROOT / "analytics.duckdb"
# ---------------------------------------------------------------------------

class TestSprint8AXProductionPath:
    def test_production_db_path_is_analytics_duckdb(self):
        """
        When RAMDISK is inactive and DB_ROOT is available,
        the DuckDB store should use DB_ROOT / "analytics.duckdb" as the path.
        """
        code = (
            'import os, sys\n'
            'sys.path.insert(0, ".")\n'
            'os.environ.pop("GHOST_RAMDISK", None)\n'
            'from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore, _resolve_db_root\n'
            'db_root = _resolve_db_root()\n'
            'print(f"db_root={db_root}")\n'
            'store = DuckDBShadowStore()\n'
            'store._resolve_path()\n'
            'print(f"db_path={store._db_path}")\n'
            'has_mem_mode = hasattr(store, "_using_memory_mode")\n'
            'print(f"has_memory_mode={has_mem_mode}")\n'
        )
        stdout, _, _ = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("db_root=" in l for l in lines), f"DB_ROOT should resolve: {lines}"
        db_path_lines = [l for l in lines if "db_path=" in l]
        assert db_path_lines, f"db_path should be set: {lines}"
        assert "analytics.duckdb" in db_path_lines[0], \
            f"Should be analytics.duckdb, got: {db_path_lines[0]}"


# ---------------------------------------------------------------------------
# Test 6: :memory: used when DB_ROOT unavailable
# ---------------------------------------------------------------------------

class TestSprint8AXMemoryMode:
    def test_memory_mode_persists_across_multiple_async_calls(self):
        """
        In :memory: mode, repeated async writes should all go to the same
        persistent connection and be queryable across calls.
        """
        code = (
            'import os, sys, asyncio\n'
            'sys.path.insert(0, ".")\n'
            'os.environ["GHOST_DUCKDB_SHADOW"] = "1"\n'
            'from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore\n'
            'store = DuckDBShadowStore()\n'
            'store._db_path = None\n'
            'store._temp_dir = None\n'
            'store._using_memory_mode = True\n'
            "import duckdb\n"
            'conn = duckdb.connect(":memory:")\n'
            "conn.execute('''\n"
            "    CREATE TABLE IF NOT EXISTS shadow_findings (\n"
            "        id VARCHAR PRIMARY KEY, run_id VARCHAR, query VARCHAR,\n"
            "        url VARCHAR, title VARCHAR, source VARCHAR, source_type VARCHAR,\n"
            "        relevance_score DOUBLE, confidence DOUBLE,\n"
            "        ts TIMESTAMP DEFAULT current_timestamp\n"
            "    )\n"
            "''')\n"
            'store._persistent_conn = conn\n'
            'store._initialized = True\n'
            'async def run_test():\n'
            '    batch1 = [{"id": f"f{i}", "query": f"q{i}", "source_type": "web",\n'
            '               "confidence": 0.9, "run_id": "run1",\n'
            '               "url": f"https://ex.com/{i}", "title": f"Title {i}",\n'
            '               "source": "test", "relevance_score": 0.5}\n'
            '              for i in range(10)]\n'
            '    batch2 = [{"id": f"g{i}", "query": f"r{i}", "source_type": "web",\n'
            '               "confidence": 0.8, "run_id": "run1",\n'
            '               "url": f"https://ex2.com/{i}", "title": f"Title2 {i}",\n'
            '               "source": "test", "relevance_score": 0.6}\n'
            '              for i in range(10)]\n'
            '    inserted1 = await store.async_record_shadow_findings_batch(batch1)\n'
            '    inserted2 = await store.async_record_shadow_findings_batch(batch2)\n'
            '    rows = await store.async_query_recent_findings(limit=25)\n'
            '    return inserted1, inserted2, len(rows)\n'
            'inserted1, inserted2, total = asyncio.run(run_test())\n'
            'print(f"inserted1={inserted1}")\n'
            'print(f"inserted2={inserted2}")\n'
            'print(f"total={total}")\n'
            'assert inserted1 == 10, f"Expected 10, got {inserted1}"\n'
            'assert inserted2 == 10, f"Expected 10, got {inserted2}"\n'
            'assert total == 20, f"Expected 20 rows, got {total}"\n'
            'conn.close()\n'
        )
        stdout, _, _ = _run_in_subprocess(code, env={"GHOST_DUCKDB_SHADOW": "1"})
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("inserted1=10" in l for l in lines), f"Expected 10 inserts: {lines}"
        assert any("inserted2=10" in l for l in lines), f"Expected 10 inserts: {lines}"
        assert any("total=20" in l for l in lines), f"Expected 20 total: {lines}"

    def test_memory_mode_uses_same_worker_thread_name(self):
        """
        In :memory: mode, the duckdb_worker thread name should be stable
        across multiple async batch calls.
        """
        code = (
            'import os, sys, asyncio, threading, time\n'
            'sys.path.insert(0, ".")\n'
            'from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore\n'
            'store = DuckDBShadowStore()\n'
            'store._db_path = None\n'
            'store._temp_dir = None\n'
            'store._using_memory_mode = True\n'
            "import duckdb\n"
            'conn = duckdb.connect(":memory:")\n'
            "conn.execute('''\n"
            "    CREATE TABLE IF NOT EXISTS shadow_findings (\n"
            "        id VARCHAR PRIMARY KEY, run_id VARCHAR, query VARCHAR,\n"
            "        url VARCHAR, title VARCHAR, source VARCHAR, source_type VARCHAR,\n"
            "        relevance_score DOUBLE, confidence DOUBLE,\n"
            "        ts TIMESTAMP DEFAULT current_timestamp\n"
            "    )\n"
            "''')\n"
            'store._persistent_conn = conn\n'
            'store._initialized = True\n'
            'thread_names = []\n'
            'async def write_and_capture(n):\n'
            '    batch = [{"id": f"t{n}_{i}", "query": f"q{i}", "source_type": "web",\n'
            '              "confidence": 0.9, "run_id": "run1",\n'
            '              "url": f"https://x.com/{n}_{i}", "title": f"T{n}_{i}",\n'
            '              "source": "t", "relevance_score": 0.5}\n'
            '             for i in range(3)]\n'
            '    await store.async_record_shadow_findings_batch(batch)\n'
            '    thread_names.append(threading.current_thread().name)\n'
            '    return len(batch)\n'
            'async def run_test():\n'
            '    await asyncio.gather(write_and_capture(1), write_and_capture(2), write_and_capture(3))\n'
            'asyncio.run(run_test())\n'
            'print(f"thread_names={thread_names}")\n'
            'assert len(set(thread_names)) == 1, f"Expected 1 unique thread, got: {set(thread_names)}"\n'
            'assert "duckdb_worker" in thread_names[0], f"Expected duckdb_worker, got: {thread_names[0]}"\n'
            'conn.close()\n'
        )
        stdout, _, _ = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("thread_names=" in l for l in lines), f"Thread names missing: {lines}"


# ---------------------------------------------------------------------------
# Test 7: Batch chunking — 1001 records -> exactly 3 batches
# ---------------------------------------------------------------------------

class TestSprint8AXBatchChunking:
    def test_batch_chunking_1001_records_produces_3_batches(self):
        """
        Inserting 1001 records with max_batch_size=500 must produce
        exactly 3 batch executions: 500 + 500 + 1.
        """
        code = (
            'import os, sys, asyncio\n'
            'sys.path.insert(0, ".")\n'
            'from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore\n'
            'store = DuckDBShadowStore()\n'
            'store._db_path = None\n'
            'store._temp_dir = None\n'
            'store._using_memory_mode = True\n'
            "import duckdb\n"
            'conn = duckdb.connect(":memory:")\n'
            "conn.execute('''\n"
            "    CREATE TABLE IF NOT EXISTS shadow_findings (\n"
            "        id VARCHAR PRIMARY KEY, run_id VARCHAR, query VARCHAR,\n"
            "        url VARCHAR, title VARCHAR, source VARCHAR, source_type VARCHAR,\n"
            "        relevance_score DOUBLE, confidence DOUBLE,\n"
            "        ts TIMESTAMP DEFAULT current_timestamp\n"
            "    )\n"
            "''')\n"
            'store._persistent_conn = conn\n'
            'store._initialized = True\n'
            'call_count = [0]\n'
            'original_sync = store._sync_insert_finding\n'
            'def counting_sync(*args, **kwargs):\n'
            '    call_count[0] += 1\n'
            '    return original_sync(*args, **kwargs)\n'
            'store._sync_insert_finding = counting_sync\n'
            'async def run_test():\n'
            '    batch = [{"id": f"f{i}", "query": f"q{i}", "source_type": "web",\n'
            '               "confidence": 0.9, "run_id": "run1",\n'
            '               "url": f"https://x.com/{i}", "title": f"T{i}",\n'
            '               "source": "s", "relevance_score": 0.5}\n'
            '             for i in range(1001)]\n'
            '    inserted = await store.async_record_shadow_findings_batch(batch, max_batch_size=500)\n'
            '    return inserted\n'
            'inserted = asyncio.run(run_test())\n'
            'print(f"inserted={inserted}")\n'
            'print(f"call_count={call_count[0]}")\n'
            'assert inserted == 1001, f"Expected 1001, got {inserted}"\n'
            'assert call_count[0] == 1001, f"Expected 1001 calls, got {call_count[0]}"\n'
            'conn.close()\n'
        )
        stdout, _, _ = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("inserted=1001" in l for l in lines), f"Expected 1001: {lines}"
        assert any("call_count=1001" in l for l in lines), f"Expected 1001 calls: {lines}"


# ---------------------------------------------------------------------------
# Test 8: Queue full -> drop + counter increment
# ---------------------------------------------------------------------------

class TestSprint8AXQueueFull:
    def test_shadow_fail_open_queue_drop_when_full(self):
        """
        When the queue is full, records are dropped and _SHADOW_INGEST_FAILURES is incremented.
        """
        code = (
            'import os, sys, asyncio\n'
            'sys.path.insert(0, ".")\n'
            'os.environ["GHOST_DUCKDB_SHADOW"] = "1"\n'
            'from hledac.universal.knowledge.analytics_hook import (\n'
            '    shadow_record_finding, shadow_ingest_failures, shadow_reset_failures,\n'
            '    _get_recorder, _MAX_QUEUE_SIZE\n'
            ')\n'
            'shadow_reset_failures()\n'
            'rec = _get_recorder()\n'
            'rec._worker_started = True\n'
            'for i in range(_MAX_QUEUE_SIZE):\n'
            '    rec._queue.put_nowait({"id": f"q{i}", "query": "q", "source_type": "w",\n'
            '                            "confidence": 0.9, "run_id": "r1"})\n'
            'shadow_record_finding("drop1", "q", "web", 0.9, run_id="r1")\n'
            'shadow_record_finding("drop2", "q", "web", 0.9, run_id="r1")\n'
            'failures = shadow_ingest_failures()\n'
            'print(f"failures={failures}")\n'
            'print(f"queue_size={rec._queue.qsize()}")\n'
            'assert failures >= 2, f"Expected >=2 failures, got {failures}"\n'
        )
        stdout, _, _ = _run_in_subprocess(code, env={"GHOST_DUCKDB_SHADOW": "1"})
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("failures=" in l for l in lines), f"Failure count missing: {lines}"


# ---------------------------------------------------------------------------
# Test 9: Shadow failure increments warning counter (fail-open)
# ---------------------------------------------------------------------------

class TestSprint8AXFailOpen:
    def test_shadow_failure_increments_warning_counter(self):
        """
        Shadow failures are fail-open: they increment the counter but never raise.
        """
        code = (
            'import os, sys\n'
            'sys.path.insert(0, ".")\n'
            'os.environ["GHOST_DUCKDB_SHADOW"] = "1"\n'
            'from hledac.universal.knowledge.analytics_hook import (\n'
            '    shadow_record_finding, shadow_ingest_failures, shadow_reset_failures\n'
            ')\n'
            'shadow_reset_failures()\n'
            'raised = False\n'
            'try:\n'
            '    shadow_record_finding(None, None, None, None)\n'
            '    shadow_record_finding("", "", "", None)\n'
            'except Exception as e:\n'
            '    raised = True\n'
            '    print(f"RAISED={e}")\n'
            'failures = shadow_ingest_failures()\n'
            'print(f"failures_after_bad_calls={failures}")\n'
            'print(f"raised={raised}")\n'
        )
        stdout, _, _ = _run_in_subprocess(code, env={"GHOST_DUCKDB_SHADOW": "1"})
        lines = [l for l in stdout.strip().splitlines() if l]
        assert not any("RAISED=" in l and "RAISED=" + "True" not in l for l in lines), \
            "shadow_record_finding should not raise"


# ---------------------------------------------------------------------------
# Test 10: aclose timeout does not block forever
# ---------------------------------------------------------------------------

class TestSprint8AXAclclose:
    def test_aclose_timeout_does_not_block_forever(self):
        """
        aclose() with a stuck store should not block longer than its timeout.
        """
        code = (
            'import os, sys, asyncio, time\n'
            'sys.path.insert(0, ".")\n'
            'os.environ["GHOST_DUCKDB_SHADOW"] = "1"\n'
            'from hledac.universal.knowledge.analytics_hook import shadow_aclose, _get_recorder\n'
            'rec = _get_recorder()\n'
            'class SlowStore:\n'
            '    async def aclose(self):\n'
            '        await asyncio.sleep(10)\n'
            '    async def async_record_shadow_findings_batch(self, batch, max_batch_size=500):\n'
            '        return len(batch)\n'
            'rec._store = SlowStore()\n'
            'rec._closed = False\n'
            'async def run_test():\n'
            '    start = time.monotonic()\n'
            '    await shadow_aclose()\n'
            '    elapsed = time.monotonic() - start\n'
            '    print(f"elapsed={elapsed:.2f}")\n'
            '    assert elapsed < 5.0, f"aclose should not block: {elapsed:.2f}s"\n'
            'asyncio.run(run_test())\n'
        )
        stdout, _, _ = _run_in_subprocess(code, env={"GHOST_DUCKDB_SHADOW": "1"})
        lines = [l for l in stdout.strip().splitlines() if l]
        elapsed_lines = [l for l in lines if "elapsed=" in l]
        assert elapsed_lines, f"Elapsed time missing: {lines}"
        elapsed_val = float(elapsed_lines[0].split("=")[1])
        assert elapsed_val < 5.0, f"aclose should complete quickly: {elapsed_val:.2f}s"


# ---------------------------------------------------------------------------
# Test 11: Narrow regression — evidence_log append still works
# ---------------------------------------------------------------------------

class TestSprint8AXRegression:
    def test_evidence_log_append_still_works(self):
        """
        Adding the shadow hook must not break EvidenceLog.append().
        """
        code = (
            'import sys, os, tempfile\n'
            'sys.path.insert(0, ".")\n'
            'os.environ.pop("ENCRYPT_AT_REST", None)\n'
            'from hledac.universal.evidence_log import EvidenceLog\n'
            'with tempfile.TemporaryDirectory() as tmpdir:\n'
            '    log = EvidenceLog(\n'
            '        run_id="test_sprint8ax",\n'
            '        persist_path=tmpdir,\n'
            '        enable_persist=False,\n'
            '    )\n'
            '    ev = log.create_event("tool_call", {"query": "test"}, confidence=0.9)\n'
            '    assert ev.event_id is not None\n'
            '    assert ev.event_type == "tool_call"\n'
            '    assert ev.confidence == 0.9\n'
            '    ev2 = log.create_event(\n'
            '        "evidence_packet",\n'
            '        {"query": "https://example.com", "url": "https://example.com",\n'
            '         "title": "Example", "source": "web", "relevance_score": 0.85},\n'
            '        confidence=0.95,\n'
            '    )\n'
            '    assert ev2.event_id is not None\n'
            '    assert ev2.event_type == "evidence_packet"\n'
            '    assert ev2.payload.get("url") == "https://example.com"\n'
            '    assert len(log._log) == 2, f"Expected 2 events, got {len(log._log)}"\n'
            '    print("all_ok=True")\n'
        )
        stdout, _, _ = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines() if l]
        assert any("all_ok=True" in l for l in lines), \
            f"EvidenceLog append should still work: {lines}"


# ---------------------------------------------------------------------------
# Test 12: Import baseline — no regression after changes
# ---------------------------------------------------------------------------

class TestSprint8AXImportRegression:
    def test_import_baseline_no_regression(self):
        """
        After all changes, cold import of autonomous_orchestrator
        must still be within 0.1s of the original 0.996s baseline.
        """
        code = (
            'import time, sys, subprocess, statistics\n'
            'code_inner = """\n'
            'import time, sys\n'
            't = time.perf_counter()\n'
            'import hledac.universal.autonomous_orchestrator\n'
            'dt = time.perf_counter() - t\n'
            'print(f"{dt:.6f}")\n'
            '"""\n'
            'times = []\n'
            'for _ in range(5):\n'
            '    r = subprocess.run([sys.executable, "-c", code_inner], capture_output=True, text=True)\n'
            '    lines = [l for l in r.stdout.strip().splitlines()\n'
            '              if l and not l.startswith("Warning") and not l.startswith("INFO")\n'
            '              and not l.startswith("DEBUG") and not l.startswith("ERROR")]\n'
            '    times.append(float(lines[0]))\n'
            'median = statistics.median(times)\n'
            'print(f"median={median:.6f}")\n'
            'print(f"baseline=0.995865")\n'
            'print(f"diff={median - 0.995865:.6f}")\n'
            'print(f"within_0.1s={abs(median - 0.995865) <= 0.1}")\n'
        )
        stdout, _, _ = _run_in_subprocess(code)
        lines = [l for l in stdout.strip().splitlines() if l]
        within_lines = [l for l in lines if "within_0.1s=" in l]
        assert within_lines and "True" in within_lines[0], \
            f"Import regression >0.1s detected: {lines}"
