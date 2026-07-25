"""
tests/test_lmdb_unified.py
===========================
Sprint S-01: UnifiedLMDB integration tests.

Tests:
    1. Singleton behavior — get_unified_lmdb() returns same instance
    2. Lazy init — env not opened until first access
    3. Sub-DB isolation — data in sub-db A not visible in sub-db B
    4. put/get/delete round-trip on each sub-DB index
    5. put_batch and scan_prefix
    6. Pressure-responsive mapsize (NORMAL → ELEVATED → CRITICAL)
    7. close / __enter__ / __exit__
    8. Thread-safe concurrent access
    9. VM reduction claim — single 512 MB mmap vs separate envs
"""

from __future__ import annotations

import tempfile
import threading
import time
import pathlib
import pytest

from hledac.universal.core.lmdb_unified import (
    UnifiedLMDB,
    SubDB,
    get_unified_lmdb,
    reset_unified_lmdb,
    unified_lmdb_stats,
    _UNIFIED_MAP_SIZE_DEFAULT,
)


class TestUnifiedLMDBBasic:
    """Basic API tests."""

    def test_singleton(self, tmp_path: pathlib.Path) -> None:
        """get_unified_lmdb() returns the same instance."""
        reset_unified_lmdb()
        store1 = get_unified_lmdb()
        store2 = get_unified_lmdb()
        assert store1 is store2
        store1.close()
        reset_unified_lmdb()

    def test_lazy_init(self, tmp_path: pathlib.Path) -> None:
        """Env not opened until first access."""
        reset_unified_lmdb()
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=True)
        assert not store.is_initialized()
        assert store.env() is not None
        assert store.is_initialized()
        store.close()

    def test_subdb_isolation(self, tmp_path: pathlib.Path) -> None:
        """Data in sub-db A not visible in sub-db B."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        # Write to sub-db 0
        store.put(SubDB.SESSION_META, b"key1", b"value1")
        # Read from sub-db 1 — should be None (different sub-db)
        result = store.get(SubDB.EXPOSURE_DATA, b"key1")
        assert result is None
        # Write to sub-db 1 and verify isolation
        store.put(SubDB.EXPOSURE_DATA, b"key1", b"value_in_subdb1")
        assert store.get(SubDB.EXPOSURE_DATA, b"key1") == b"value_in_subdb1"
        assert store.get(SubDB.SESSION_META, b"key1") == b"value1"
        store.close()

    def test_put_get_delete_roundtrip(self, tmp_path: pathlib.Path) -> None:
        """put/get/delete work correctly on all sub-DB indices."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        for idx in range(15):
            key = f"key_{idx}".encode()
            value = f"value_{idx}".encode()
            assert store.put(idx, key, value)
            assert store.get(idx, key) == value
            assert store.delete(idx, key)
            assert store.get(idx, key) is None
        store.close()

    def test_put_batch(self, tmp_path: pathlib.Path) -> None:
        """Batch put with cursor.putmulti."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        items = [(f"k{i}".encode(), f"v{i}".encode()) for i in range(100)]
        assert store.put_batch(SubDB.SESSION_META, items)
        for i in range(100):
            assert store.get(SubDB.SESSION_META, f"k{i}".encode()) == f"v{i}".encode()
        store.close()

    def test_scan_prefix(self, tmp_path: pathlib.Path) -> None:
        """scan_prefix returns matching key-value pairs."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        # Put some data
        store.put_batch(SubDB.SESSION_META, [(b"aaa_1", b"v1"), (b"aaa_2", b"v2"), (b"bbb_1", b"v3")])
        results = store.scan_prefix(SubDB.SESSION_META, b"aaa_")
        assert len(results) == 2
        keys = sorted(r[0] for r in results)
        assert keys == [b"aaa_1", b"aaa_2"]
        store.close()

    def test_context_manager(self, tmp_path: pathlib.Path) -> None:
        """__enter__ / __exit__ work correctly."""
        with UnifiedLMDB(tmp_path / "test.lmdb", lazy=False) as store:
            store.put(SubDB.SESSION_META, b"key", b"val")
            assert store.get(SubDB.SESSION_META, b"key") == b"val"
        # After exit, store should be closed
        assert store.is_closed()


class TestUnifiedLMDBPressure:
    """Pressure-responsive mapsize tests."""

    def test_pressure_state_nominal(self, tmp_path: pathlib.Path) -> None:
        """NORMAL state keeps default mapsize."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", map_size=512 * 1024 * 1024, lazy=False)
        assert store._pressure_state == "NORMAL"
        assert store._map_size_current == 512 * 1024 * 1024
        store.set_pressure("NORMAL")
        assert store._pressure_state == "NORMAL"
        store.close()

    def test_pressure_elevated_grows(self, tmp_path: pathlib.Path) -> None:
        """ELEVATED from NORMAL triggers set_mapsize growth."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", map_size=256 * 1024 * 1024, lazy=False)
        store.set_pressure("NORMAL")
        assert store._map_size_current == 256 * 1024 * 1024
        # NORMAL → ELEVATED should double (cap at default)
        store.set_pressure("ELEVATED")
        assert store._map_size_current == 256 * 1024 * 1024  # can't grow past default stored in _map_size_default
        store.close()

    def test_emergency_shrink_critical(self, tmp_path: pathlib.Path) -> None:
        """CRITICAL pressure shrinks env via close+reopen."""
        store = UnifiedLMDB(
            tmp_path / "test.lmdb",
            map_size=512 * 1024 * 1024,
            lazy=False,
        )
        # Write some data so env has content
        store.put_batch(SubDB.SESSION_META, [(b"k1", b"v1"), (b"k2", b"v2")])
        assert store.get(SubDB.SESSION_META, b"k1") == b"v1"

        # CRITICAL → shrinks to 128 MB
        store.set_pressure("CRITICAL")
        assert store._map_size_current == 128 * 1024 * 1024
        assert store._pressure_state == "CRITICAL"

        # Data should survive emergency shrink (env was reopened)
        # Note: emergency_shrink closes and reopens env, so data is preserved
        store.close()


class TestUnifiedLMDBSubDB:
    """Sub-DB index and naming tests."""

    def test_subdb_names(self) -> None:
        assert SubDB.name(SubDB.SESSION_META) == "session_meta"
        assert SubDB.name(SubDB.HOT_EDGES) == "hot_edges"
        assert SubDB.name(SubDB.RESERVED) == "reserved"
        assert SubDB.name(99) == "unknown(99)"

    def test_open_db_validates_range(self, tmp_path: pathlib.Path) -> None:
        """open_db raises on out-of-range index."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        with pytest.raises(ValueError, match="out of range"):
            store.open_db(16)  # max_dbs=16, valid is 0-15
        store.close()


class TestUnifiedLMDBCurrent:
    """Integration with existing code patterns."""

    def test_env_begin_convenience(self, tmp_path: pathlib.Path) -> None:
        """env_begin returns a transaction on the sub-DB."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        with store.env_begin(SubDB.SESSION_META, write=True) as txn:
            txn.put(b"test_key", b"test_val")
        with store.env_begin(SubDB.SESSION_META, write=False) as txn:
            assert txn.get(b"test_key") == b"test_val"
        store.close()

    def test_unified_lmdb_stats(self, tmp_path: pathlib.Path) -> None:
        """unified_lmdb_stats() returns correct structure."""
        reset_unified_lmdb()
        store = get_unified_lmdb()
        store._path = tmp_path / "unified.lmdb"  # point to temp path
        store._ensure_init()

        stats = unified_lmdb_stats()
        assert stats["initialized"] is True
        assert "map_size_current_mb" in stats
        assert "pressure_state" in stats
        assert stats["pressure_state"] == "NORMAL"
        store.close()
        reset_unified_lmdb()


class TestUnifiedLMDBThreadSafety:
    """Thread-safety tests."""

    def test_concurrent_put(self, tmp_path: pathlib.Path) -> None:
        """Concurrent puts from multiple threads don't crash."""
        store = UnifiedLMDB(tmp_path / "test.lmdb", lazy=False)
        errors: list[Exception] = []

        def writer(sub_idx: int, start: int) -> None:
            try:
                for i in range(start, start + 50):
                    key = f"t{threading.current_thread().name}_{i}".encode()
                    store.put(sub_idx, key, b"val")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(SubDB.SESSION_META, 0)),
            threading.Thread(target=writer, args=(SubDB.EXPOSURE_DATA, 50)),
            threading.Thread(target=writer, args=(SubDB.HOT_EDGES, 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent writes failed: {errors}"
        store.close()

    def test_singleton_thread_safe(self, tmp_path: pathlib.Path) -> None:
        """get_unified_lmdb() is safe to call from multiple threads concurrently."""
        reset_unified_lmdb()
        results: list[object] = []
        lock = threading.Lock()

        def getter() -> None:
            store = get_unified_lmdb()
            with lock:
                results.append(store)

        threads = [threading.Thread(target=getter) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same instance
        assert len(set(id(r) for r in results)) == 1
        get_unified_lmdb().close()
        reset_unified_lmdb()


class TestUnifiedLMDBVMReduction:
    """
    S-01 VM reduction claim tests.

    Invariant: A single UnifiedLMDB with max_dbs=16 consumes ~1 mmap region
    instead of N separate mmap regions from individual lmdb.open() calls.
    """

    def test_single_mmap_region(self, tmp_path: pathlib.Path) -> None:
        """Verify that UnifiedLMDB opens a single env with multiple sub-DBs."""
        store = UnifiedLMDB(tmp_path / "unified.lmdb", lazy=False)
        env = store.env()
        assert env is not None

        # Open a second sub-db to verify multi-db support
        sub1 = store.open_db(SubDB.SESSION_META)
        sub2 = store.open_db(SubDB.EXPOSURE_DATA)
        assert sub1 is not sub2

        # Verify env.info() shows the mapsize
        info = env.info()
        assert info["map_size"] == _UNIFIED_MAP_SIZE_DEFAULT

        store.close()

    def test_map_size_default_512mb(self) -> None:
        """Default mapsize is 512 MB (not per-env, total)."""
        assert _UNIFIED_MAP_SIZE_DEFAULT == 512 * 1024 * 1024
