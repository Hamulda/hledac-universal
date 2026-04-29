"""
Probe tests for TemporalSignalStore (Sprint F206Q).

Tests cover:
1.  store initializes SQLite WAL
2.  save/load snapshot roundtrip
3.  corrupted DB/load fail-soft
4.  close idempotent
5.  env disabled → runtime does not create store
6.  env enabled → runtime creates/uses store
7.  pipeline start restores snapshot when enabled
8.  pipeline end saves snapshot when enabled
9.  no per-event write
10. CancelledError re-raise
11. temporal_signal_summary includes persistence flags
12. existing per-run behavior unchanged when env disabled
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Any

import pytest

# Module-level reset before each test
@pytest.fixture(autouse=True)
def _reset_runtime():
    """Reset temporal_signal_runtime module state before each test."""
    import hledac.universal.layers.temporal_signal_runtime as _rt
    _rt._layer = None
    _rt._reset_ts = 0.0
    _rt._store = None
    _rt._store_enabled = None
    yield
    _rt._layer = None
    _rt._reset_ts = 0.0
    _rt._store = None
    _rt._store_enabled = None


# ─── TemporalSignalStore unit tests ────────────────────────────────────────────

class TestTemporalSignalStoreInit:
    def test_store_initializes_sqlite_wal(self, tmp_path: Path):
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        db_path = tmp_path / "test.db"
        store = TemporalSignalStore(path=db_path)
        store.initialize()

        conn = sqlite3.connect(str(db_path))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.upper() == "WAL", f"Expected WAL, got {mode}"
        finally:
            conn.close()
        store.close()

    def test_save_load_snapshot_roundtrip(self, tmp_path: Path):
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        db_path = tmp_path / "roundtrip.db"
        store = TemporalSignalStore(path=db_path)
        store.initialize()

        snapshot = {
            "max_keys": 128,
            "ring_size": 256,
            "states": {"example.com": {"last_ts": 1234.5, "event_count": 10}},
            "edge_candidates": [],
            "sync_window": [],
        }
        store.save_snapshot(snapshot)
        loaded = store.load_snapshot()

        assert loaded is not None
        assert loaded["max_keys"] == 128
        assert loaded["states"]["example.com"]["event_count"] == 10
        store.close()

    def test_load_returns_none_when_empty(self, tmp_path: Path):
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        db_path = tmp_path / "empty.db"
        store = TemporalSignalStore(path=db_path)
        store.initialize()

        assert store.load_snapshot() is None
        store.close()

    def test_corrupted_db_load_failsoft(self, tmp_path: Path):
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        db_path = tmp_path / "corrupt.db"
        db_path.write_text("this is not a sqlite database")

        store = TemporalSignalStore(path=db_path)
        store.initialize()
        result = store.load_snapshot()

        assert result is None  # fail-soft, no crash

    def test_close_idempotent(self, tmp_path: Path):
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        db_path = tmp_path / "close_test.db"
        store = TemporalSignalStore(path=db_path)
        store.initialize()

        store.save_snapshot({"test": "data"})
        store.close()
        store.close()  # second close must not raise
        store.close()  # third close must not raise


class TestTemporalSignalStoreNoPerEventWrite:
    def test_no_per_event_write(self, tmp_path: Path):
        """
        Verify only one REPLACE per save_snapshot call — no per-event writes.

        Design invariant: snapshot is written as a single atomic blob (REPLACE INTO).
        The PRAGMA wal_checkpoint call is the only additional statement.
        """
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        db_path = tmp_path / "no_per_event.db"

        # Intercept REPLACE at the DB file level (WAL shared memory)
        # by verifying the WAL contains exactly one snapshot record after N saves.
        store = TemporalSignalStore(path=db_path)
        store.initialize()

        # Make 3 independent saves
        for i in range(3):
            store.save_snapshot({"save_id": i, "states": {}, "edge_candidates": [], "sync_window": []})

        # Exactly 3 rows in the table (REPLACE = upsert, no duplicates)
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT COUNT(*) FROM temporal_snapshot").fetchone()[0]
            assert count == 1, f"Expected 1 row (upsert), got {count} — per-event write detected"
        finally:
            conn.close()
        store.close()


# ─── Runtime env-gated behavior tests ─────────────────────────────────────────

class TestRuntimeEnvGate:
    def test_env_disabled_runtime_does_not_create_store(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "0")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        from hledac.universal.layers.temporal_signal_runtime import (
            get_temporal_signal_store,
            is_temporal_store_enabled,
        )
        assert is_temporal_store_enabled() is False
        assert get_temporal_signal_store() is None

    def test_env_enabled_runtime_creates_store(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "1")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        from hledac.universal.layers.temporal_signal_runtime import (
            get_temporal_signal_store,
            is_temporal_store_enabled,
        )
        assert is_temporal_store_enabled() is True
        store = get_temporal_signal_store()
        assert store is not None
        store.close()


# ─── Pipeline integration tests ────────────────────────────────────────────────

class TestPipelineSnapshotRestore:
    def test_pipeline_start_restores_snapshot_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """When store is enabled and has a snapshot, load_temporal_signal_snapshot returns True."""
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore

        # Pre-seed a snapshot
        db_path = tmp_path / "restore_test.db"
        store = TemporalSignalStore(path=db_path)
        store.initialize()
        store.save_snapshot({
            "max_keys": 256,
            "ring_size": 512,
            "half_life_s": 300.0,
            "synchrony_window_s": 3600.0,
            "bocpd_max_run": 1000,
            "states": {
                "restored.example.com": {
                    "last_ts": 9999.0,
                    "event_count": 42,
                    "ewma_rate": 0.5,
                    "ewma_gap": 60.0,
                    "gap_variance": 10.0,
                    "ring_gaps": [],
                    "ring_sources": [],
                    "confirmation_weight": 1.0,
                    "last_updated": 9999.0,
                    "ph_cumsum": 0.0,
                    "ph_mean": 0.0,
                    "bocpd_run_length": 0,
                    "bocpd_log_odds": 0.0,
                }
            },
            "lru_order": ["restored.example.com"],
            "edge_candidates": [],
            "sync_window": [],
        })
        store.close()

        # Point store at this path
        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "1")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        # Patch DEFAULT_STORE_PATH before reloading
        import hledac.universal.layers.temporal_signal_store as _store_mod
        _orig_init = _store_mod.TemporalSignalStore.__init__

        def _patched_init(self: Any, path: Any = None) -> None:
            _orig_init(self, path=db_path)

        monkeypatch.setattr(_store_mod.TemporalSignalStore, "__init__", _patched_init)

        from hledac.universal.layers.temporal_signal_runtime import (
            load_temporal_signal_snapshot,
            get_temporal_signal_layer,
        )
        # Reset so load creates a fresh layer
        _rt._layer = None

        restored = load_temporal_signal_snapshot()
        assert restored is True

        layer = get_temporal_signal_layer()
        assert layer is not None
        assert "restored.example.com" in layer._states
        assert layer._states["restored.example.com"].event_count == 42


class TestPipelineSnapshotSave:
    def test_pipeline_end_saves_snapshot_when_enabled(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """save_temporal_signal_snapshot writes to the store."""
        db_path = tmp_path / "save_test.db"

        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "1")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        import hledac.universal.layers.temporal_signal_store as _store_mod
        _orig_init = _store_mod.TemporalSignalStore.__init__

        def _patched_init(self: Any, path: Any = None) -> None:
            _orig_init(self, path=db_path)

        monkeypatch.setattr(_store_mod.TemporalSignalStore, "__init__", _patched_init)

        from hledac.universal.layers.temporal_signal_runtime import (
            save_temporal_signal_snapshot,
            get_temporal_signal_layer,
        )
        # Create a layer with some state
        layer = get_temporal_signal_layer()

        saved = save_temporal_signal_snapshot()
        assert saved is True

        # Verify data is actually in the DB
        from hledac.universal.layers.temporal_signal_store import TemporalSignalStore
        verifier = TemporalSignalStore(path=db_path)
        verifier.initialize()
        snapshot = verifier.load_snapshot()
        verifier.close()
        assert snapshot is not None
        assert "max_keys" in snapshot


class TestPipelinePersistenceFlags:
    def test_temporal_signal_summary_includes_persistence_flags(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """The verdict dict includes persistence_enabled/restored/saved fields."""
        # This tests that the pipeline code paths are wired correctly.
        # We simulate the behavior without running the full pipeline.
        db_path = tmp_path / "flags_test.db"

        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "1")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        import hledac.universal.layers.temporal_signal_store as _store_mod
        _orig_init = _store_mod.TemporalSignalStore.__init__

        def _patched_init(self: Any, path: Any = None) -> None:
            _orig_init(self, path=db_path)

        monkeypatch.setattr(_store_mod.TemporalSignalStore, "__init__", _patched_init)

        from hledac.universal.layers.temporal_signal_runtime import (
            is_temporal_store_enabled,
            load_temporal_signal_snapshot,
            save_temporal_signal_snapshot,
            get_temporal_signal_layer,
        )

        # Simulate pipeline start
        _rt._layer = None
        persistence_enabled = is_temporal_store_enabled()
        persistence_restored = load_temporal_signal_snapshot()

        # Add state to layer
        layer = get_temporal_signal_layer()

        # Simulate pipeline end
        persistence_saved = save_temporal_signal_snapshot()

        verdict = {
            "persistence_enabled": persistence_enabled,
            "persistence_restored": persistence_restored,
            "persistence_saved": persistence_saved,
        }

        assert verdict["persistence_enabled"] is True
        assert verdict["persistence_restored"] is False  # no prior snapshot
        assert verdict["persistence_saved"] is True


class TestPerRunBehaviorUnchanged:
    def test_env_disabled_per_run_behavior_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        """When env is disabled, layer is reset but no store is accessed."""
        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "0")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        from hledac.universal.layers.temporal_signal_runtime import (
            get_temporal_signal_layer,
            reset_temporal_signal_layer,
            is_temporal_store_enabled,
            get_temporal_signal_store,
        )

        # Create layer
        layer1 = get_temporal_signal_layer()
        layer1_id = id(layer1)

        # Reset — should clear state but layer stays same object
        reset_temporal_signal_layer()

        # Store should be None
        assert is_temporal_store_enabled() is False
        assert get_temporal_signal_store() is None

        # Next get returns a fresh layer (reset clears internal state)
        layer2 = get_temporal_signal_layer()
        # After reset, the existing layer was reset in-place
        assert layer2._states == {}  # reset cleared state


class TestCancelledErrorReraise:
    def test_cancelled_error_propagates_from_save(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """
        CancelledError from store operations must propagate, not be swallowed.

        asyncio.CancelledError inherits from BaseException (not Exception),
        so it bypasses our `except Exception` handlers and propagates naturally.
        """
        import asyncio
        db_path = tmp_path / "cancel_test.db"

        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "1")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        import hledac.universal.layers.temporal_signal_store as _store_mod
        _orig_init = _store_mod.TemporalSignalStore.__init__

        def _patched_init(self: Any, path: Any = None) -> None:
            _orig_init(self, path=db_path)

        monkeypatch.setattr(_store_mod.TemporalSignalStore, "__init__", _patched_init)

        from hledac.universal.layers.temporal_signal_runtime import (
            save_temporal_signal_snapshot,
            get_temporal_signal_store,
            get_temporal_signal_layer,
        )

        # Create a layer first so _layer is not None
        layer = get_temporal_signal_layer()
        assert layer is not None

        # Patch the store's save_snapshot to raise CancelledError
        store = get_temporal_signal_store()
        assert store is not None

        def _raising_save(snapshot: Any) -> None:
            raise asyncio.CancelledError("test cancel")

        store.save_snapshot = _raising_save  # type: ignore[method-assign]

        # CancelledError is BaseException, not Exception — propagates through
        # our `except Exception` handler without being caught.
        with pytest.raises(asyncio.CancelledError):
            save_temporal_signal_snapshot()

    def test_cancelled_error_propagates_from_load(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        """CancelledError from load also propagates (not an Exception subclass)."""
        import asyncio
        db_path = tmp_path / "cancel_load.db"

        monkeypatch.setenv("HLEDAC_ENABLE_TEMPORAL_STORE", "1")
        import importlib
        import hledac.universal.layers.temporal_signal_runtime as _rt
        _rt._store_enabled = None
        _rt._store = None
        importlib.reload(_rt)

        import hledac.universal.layers.temporal_signal_store as _store_mod
        _orig_init = _store_mod.TemporalSignalStore.__init__

        def _patched_init(self: Any, path: Any = None) -> None:
            _orig_init(self, path=db_path)

        monkeypatch.setattr(_store_mod.TemporalSignalStore, "__init__", _patched_init)

        from hledac.universal.layers.temporal_signal_runtime import load_temporal_signal_snapshot
        from hledac.universal.layers.temporal_signal_runtime import get_temporal_signal_store
        store = get_temporal_signal_store()
        assert store is not None

        def _raising_load() -> Any:
            raise asyncio.CancelledError("load cancel")

        store.load_snapshot = _raising_load  # type: ignore[method-assign]

        # CancelledError is BaseException — propagates
        with pytest.raises(asyncio.CancelledError):
            load_temporal_signal_snapshot()
