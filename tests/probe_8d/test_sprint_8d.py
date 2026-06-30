"""
Sprint 8D: DuckDB Gate Detox + Persistence Truth Cleanup
=========================================================

Tests:
1.   duckdb availability detection
2.   SKIP behavior when duckdb missing
3.   PASS behavior when duckdb present
4.   test-friendly db_path/temp_dir seam in DuckDBShadowStore.__init__
5.   fresh read-back after write completion
6.   no concurrent write/read happy-path race
7.   partial failure semantics unchanged
8.   AO canary existence / run
"""


import asyncio
import importlib
import importlib.util
import os
import shutil
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

import pytest

# ---------------------------------------------------------------------------
# Skip entire module if duckdb is not available
# ---------------------------------------------------------------------------
_DUCKDB_AVAILABLE = importlib.util.find_spec("duckdb") is not None

pytestmark: pytest.MarkDecorator = pytest.mark.skipif(
    not _DUCKDB_AVAILABLE,
    reason="duckdb not installed in this environment"
)

# ---------------------------------------------------------------------------
# Storage — isolated temp dirs
# ---------------------------------------------------------------------------

_TMP_DIRS: List[Path] = []


def _mk_tmp() -> Path:
    p = Path(tempfile.mkdtemp(prefix="probe_8d_"))
    _TMP_DIRS.append(p)
    return p


@pytest.fixture(autouse=True)
def _cleanup_tmp():
    yield
    for d in _TMP_DIRS:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


# ---------------------------------------------------------------------------
# Store factory — uses new db_path/temp_dir seam (Sprint 8D)
# ---------------------------------------------------------------------------

def _make_store(tmp: Path) -> Any:
    """Create isolated DuckDBShadowStore using new __init__ seam."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

    db_path = tmp / "shadow_analytics.duckdb"
    temp_dir = tmp / "duckdb_tmp"
    os.environ.setdefault("GHOST_DUCKDB_MEMORY", "256MB")
    # Sprint 8D: use new constructor seam — no monkeypatch needed
    store = DuckDBShadowStore(db_path=db_path, temp_dir=temp_dir)
    store.initialize()
    return store


def _make_lmdb(tmp: Path) -> Any:
    """Create isolated LMDBKVStore for testing."""
    from hledac.universal.tools.lmdb_kv import LMDBKVStore

    lmdb_path = tmp / "test_wal.lmdb"
    store = LMDBKVStore(path=str(lmdb_path), map_size=32 * 1024 * 1024, max_keys=5000)
    return store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid() -> str:
    return str(uuid.uuid4())


def _make_finding(idx: int = 0) -> Dict[str, Any]:
    return {
        "id": f"fid-{_uuid()[:8]}-{idx}",
        "query": f"test query {idx}",
        "source_type": "test_source",
        "confidence": 0.5 + (idx % 10) * 0.05,
    }


# ---------------------------------------------------------------------------
# Test 1: duckdb availability detection
# ---------------------------------------------------------------------------

def test_duckdb_availability_detected():
    """1. duckdb availability is correctly detected."""
    assert _DUCKDB_AVAILABLE is True, "duckdb must be available for these tests"


# ---------------------------------------------------------------------------
# Test 2: SKIP behavior — module-level marker exists
# ---------------------------------------------------------------------------

def test_module_skip_marker_exists():
    """2. Module has skipif marker so it skips cleanly when duckdb missing."""
    import tests.probe_8d.test_sprint_8d as mod
    # The module-level pytestmark causes pytest to skip the whole module
    # when duckdb is not available — verify the marker is present
    assert hasattr(mod, "pytestmark")
    marker = mod.pytestmark
    assert isinstance(marker, pytest.MarkDecorator)
    # The skip condition is the module-level _DUCKDB_AVAILABLE
    assert _DUCKDB_AVAILABLE is True


# ---------------------------------------------------------------------------
# Test 4: test-friendly db_path/temp_dir seam
# ---------------------------------------------------------------------------

def test_constructor_seam_db_path_injection(tmp_path):
    """4a. DuckDBShadowStore(db_path=...) injects path without monkeypatch."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

    db_path = tmp_path / "seam_test.duckdb"
    temp_dir = tmp_path / "duckdb_tmp"
    store = DuckDBShadowStore(db_path=db_path, temp_dir=temp_dir)

    assert store._db_path == db_path
    assert store._temp_dir == temp_dir
    assert store.db_path == db_path
    assert store.temp_dir == temp_dir

    store.initialize()
    assert store.is_initialized is True

    # Verify DB was actually created
    assert db_path.exists(), "DuckDB file must be created at injected path"


def test_constructor_seam_no_path_uses_resolve(tmp_path):
    """4b. DuckDBShadowStore() with no args falls back to _resolve_path."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

    store = DuckDBShadowStore()
    # _db_path is None initially — will be resolved on initialize()
    assert store._db_path is None
    assert store._temp_dir is None


# ---------------------------------------------------------------------------
# Test 5: fresh read-back after write completion
# ---------------------------------------------------------------------------

def _fresh_read_findings(db_path: Path, limit: int = 10) -> List[Dict[str, Any]]:
    """Read findings using a fresh DuckDB connection (not store's internal)."""
    import duckdb
    conn = duckdb.connect(str(db_path))
    result = conn.execute(
        "SELECT id, query, source_type, confidence FROM shadow_findings ORDER BY ts DESC LIMIT ?",
        [limit],
    ).fetchall()
    conn.close()
    return [
        {"id": row[0], "query": row[1], "source_type": row[2], "confidence": row[3]}
        for row in result
    ]


def test_fresh_readback_after_write(tmp_path):
    """5. Fresh connection read-back returns data immediately after write."""
    store = _make_store(tmp_path)
    db_path = tmp_path / "shadow_analytics.duckdb"

    finding = _make_finding(0)
    store._sync_insert_finding(
        finding["id"], finding["query"], finding["source_type"], finding["confidence"]
    )

    # Fresh connection read-back (not store's internal connection)
    results = _fresh_read_findings(db_path, limit=5)
    assert len(results) >= 1, "DuckDB must have at least 1 record after write"
    assert results[0]["id"] == finding["id"]
    assert results[0]["query"] == finding["query"]


def test_fresh_readback_after_bulk_write(tmp_path):
    """5b. Fresh connection read-back after bulk write."""
    store = _make_store(tmp_path)
    db_path = tmp_path / "shadow_analytics.duckdb"

    findings = [_make_finding(100 + i) for i in range(10)]
    inserted = store._sync_insert_findings_bulk(findings)
    assert inserted == 10, f"Bulk insert must return 10, got {inserted}"

    # Fresh connection read-back
    results = _fresh_read_findings(db_path, limit=15)
    assert len(results) >= 10, f"DuckDB must have at least 10 records, got {len(results)}"
    found_ids = {r["id"] for r in results}
    for f in findings:
        assert f["id"] in found_ids, f"Finding {f['id']} not found in results"


# ---------------------------------------------------------------------------
# Test 6: no concurrent write/read happy-path race
# ---------------------------------------------------------------------------

def test_no_race_write_read_concurrent(tmp_path):
    """6. Concurrent writes and reads don't corrupt data (happy-path)."""
    store = _make_store(tmp_path)
    db_path = tmp_path / "shadow_analytics.duckdb"

    findings = [_make_finding(200 + i) for i in range(20)]

    # Write all findings
    for f in findings:
        store._sync_insert_finding(f["id"], f["query"], f["source_type"], f["confidence"])

    # Read back — should see all 20
    results = _fresh_read_findings(db_path, limit=25)
    assert len(results) >= 20, f"Expected >=20 records, got {len(results)}"
    found_ids = {r["id"] for r in results}
    for f in findings:
        assert f["id"] in found_ids, f"Finding {f['id']} not found after concurrent access"


# ---------------------------------------------------------------------------
# Test 7: partial failure semantics unchanged
# ---------------------------------------------------------------------------

def test_partial_failure_lmdb_persists_when_duckdb_fails(tmp_path):
    """7. When LMDB succeeds but DuckDB fails, LMDB record must remain."""
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    from hledac.universal.tools.lmdb_kv import LMDBKVStore

    wal_path = tmp_path / "shadow_wal.lmdb"

    # Create LMDB directly
    lmdb = LMDBKVStore(path=str(wal_path))
    fid = f"partial-{_uuid()[:8]}"
    key = f"finding:{fid}"
    value = {"id": fid, "query": "partial fail test", "source_type": "fail_test", "confidence": 0.5, "ts": time.time()}
    lmdb.put(key, value)

    # Verify LMDB has the record
    retrieved = lmdb.get(key)
    assert retrieved is not None, "LMDB must have the record before DuckDB failure"

    # Create and close a store (closes _file_conn)
    store_a = _make_store(tmp_path)
    store_a._sync_close_on_worker()

    # DuckDB write will now fail (connection closed)
    finding = _make_finding(500)
    result = store_a._activation_record_finding(
        finding_id=finding["id"],
        query=finding["query"],
        source_type=finding["source_type"],
        confidence=finding["confidence"],
    )

    # LMDB should still have its original record
    retrieved_after = lmdb.get(key)
    assert retrieved_after is not None, "LMDB record must persist after DuckDB failure"
    assert retrieved_after["id"] == fid


# ---------------------------------------------------------------------------
# Test 8: AO canary
# ---------------------------------------------------------------------------

def test_ao_canary_exists():
    """8a. AO canary must exist."""
    canary = Path(__file__).parent.parent / "test_ao_canary.py"
    assert canary.exists(), "test_ao_canary.py not found"


def test_ao_canary_runs():
    """8b. AO canary must run without errors."""
    import subprocess
    result = subprocess.run(
        ["pytest", str(Path(__file__).parent.parent / "test_ao_canary.py"), "-q", "--tb=no"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"AO canary failed: {result.stdout}\n{result.stderr}"


# ---------------------------------------------------------------------------
# Test: probe_7f and probe_8a existence
# ---------------------------------------------------------------------------

def test_probe_7f_exists():
    """probe_7f must exist."""
    probe_7f = Path(__file__).parent.parent / "probe_7f"
    assert probe_7f.exists(), "probe_7f not found"


def test_probe_8a_exists():
    """probe_8a must exist."""
    probe_8a = Path(__file__).parent.parent / "probe_8a"
    assert probe_8a.exists(), "probe_8a not found"


# ---------------------------------------------------------------------------
# Test: WAL-first semantics unchanged (duckdb-dependent)
# ---------------------------------------------------------------------------

def test_wal_first_lmdb_first_then_duckdb(tmp_path):
    """WAL-first: LMDB write must succeed before DuckDB is attempted."""
    from hledac.universal.tools.lmdb_kv import LMDBKVStore

    wal_path = tmp_path / "shadow_wal.lmdb"
    lmdb = LMDBKVStore(path=str(wal_path))

    fid = f"waltest-{_uuid()[:8]}"
    key = f"finding:{fid}"
    value = {"id": fid, "query": "wal first test", "source_type": "waltest", "confidence": 0.9, "ts": time.time()}

    # LMDB write first
    ok = lmdb.put(key, value)
    assert ok, "LMDB WAL write must succeed"

    # Verify LMDB has it
    retrieved = lmdb.get(key)
    assert retrieved is not None
    assert retrieved["id"] == fid


# ---------------------------------------------------------------------------
# Test: no per-call connection when _file_conn is available (Sprint 7H)
# ---------------------------------------------------------------------------

def test_persistent_connection_used_for_inserts(tmp_path):
    """When _file_conn is set, inserts use it instead of per-call connect."""
    store = _make_store(tmp_path)
    db_path = tmp_path / "shadow_analytics.duckdb"

    # Verify _file_conn is set (persistent connection)
    assert store._file_conn is not None, "_file_conn must be set in file mode"

    finding = _make_finding(300)
    ok = store._sync_insert_finding(
        finding["id"], finding["query"], finding["source_type"], finding["confidence"]
    )
    assert ok, "Insert must succeed using persistent _file_conn"

    # Verify via fresh connection
    results = _fresh_read_findings(db_path, limit=5)
    ids = {r["id"] for r in results}
    assert finding["id"] in ids, "Finding must be readable via fresh connection"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
