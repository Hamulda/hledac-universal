"""
Sprint F265-L1: Hot Edges L1 Rust Write Buffer Tests

Tests for the optional L1 Rust write buffer (HotEdgeCounterRust) in front of LMDB.
Covers: availability gating, threshold-based flush, aggregation correctness,
fallback when Rust unavailable, and stats reporting.

Edit ONLY: tests/test_hot_edges_cache/test_hot_edges_l1_buffer.py
"""
from __future__ import annotations

from typing import Any, cast

import pytest

# Import the module under test — lazy imports mean Rust extension may not be loaded yet.
from hledac.universal.knowledge import hot_edges_cache as _hec

# Snapshot availability flag before any test touches the module.
_L1_AVAILABLE = _hec._L1_AVAILABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _l1():
    """Return the L1 buffer (always non-None when _L1_AVAILABLE is True)."""
    return _hec._EDGE_COUNTER_L1  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_lmdb():
    """Clear LMDB state before and after every test."""
    _hec.clear_all()
    yield
    _hec.clear_all()


@pytest.fixture
def _flush_threshold_env(monkeypatch):
    """Re-create L1 buffer with a given flush threshold."""
    def _set(threshold: int):
        monkeypatch.setenv("HLEDAC_HOT_EDGES_L1_FLUSH", str(threshold))
        # Re-create L1 buffer — requires the Rust class to be available.
        # Use cast(Any) to satisfy type-checker which sees _HotEdgeCounterRust as type | None.
        _hec._EDGE_COUNTER_L1 = cast(Any, _hec._HotEdgeCounterRust)(
            flush_threshold=threshold
        )
    return _set


# ---------------------------------------------------------------------------
# Test 1: L1 buffer active when Rust available
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _L1_AVAILABLE, reason="hledac_rust_extensions required")
def test_l1_buffer_active_when_rust_available():
    """
    When hledac_rust_extensions is importable, _L1_AVAILABLE must be True.
    """
    assert _hec._L1_AVAILABLE is True
    assert _hec._EDGE_COUNTER_L1 is not None


# ---------------------------------------------------------------------------
# Test 2: Below threshold — no LMDB write
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _L1_AVAILABLE, reason="hledac_rust_extensions required")
def test_record_edge_below_threshold_no_lmdb_write(monkeypatch, _flush_threshold_env):
    """
    With flush_threshold=100, a single record_edge(1, 2) call must NOT
    trigger _record_edge_lmdb (L1 absorbs the write).
    """
    _flush_threshold_env(100)

    recorded: list[tuple[int, int]] = []
    original = _hec._record_edge_lmdb

    def _track_lmdb(src_id: int, dst_id: int) -> bool:
        recorded.append((src_id, dst_id))
        return original(src_id, dst_id)

    monkeypatch.setattr(_hec, "_record_edge_lmdb", _track_lmdb, raising=False)

    result = _hec.record_edge(1, 2)

    assert result is True
    assert recorded == [], "_record_edge_lmdb must NOT be called below flush threshold"
    assert _l1().pending_count() == 1  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Test 3: Flush triggered at threshold
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _L1_AVAILABLE, reason="hledac_rust_extensions required")
def test_flush_triggered_at_threshold(monkeypatch, _flush_threshold_env):
    """
    With flush_threshold=3, calling record_edge three times must call
    _flush_l1_to_lmdb exactly once on the third bump.

    After flush, L1 pending count must be 0.
    """
    _flush_threshold_env(3)

    flush_calls: list = []
    original_flush = _hec._flush_l1_to_lmdb

    def _track_flush() -> bool:
        flush_calls.append(True)
        return original_flush()

    monkeypatch.setattr(_hec, "_flush_l1_to_lmdb", _track_flush, raising=False)

    for i in range(3):
        result = _hec.record_edge(i, i + 1)
        assert result is True

    assert len(flush_calls) == 1, f"Expected 1 flush call, got {len(flush_calls)}"
    assert _l1().pending_count() == 0  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Test 4: Flush aggregates same src/dst
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _L1_AVAILABLE, reason="hledac_rust_extensions required")
def test_flush_aggregates_same_src(_flush_threshold_env):
    """
    With flush_threshold=1, calling record_edge(src=1, dst=2) twice must
    aggregate the counts: after flush, get_hot_neighbors(1) returns [(2, 2)]
    (dst=2 with count=2), NOT two separate entries.
    """
    _flush_threshold_env(1)

    _hec.record_edge(1, 2)
    assert _l1().pending_count() == 1  # type: ignore[union-attr]
    _hec.record_edge(1, 2)
    # Second call triggered flush (threshold=1), pending resets to 0.
    assert _l1().pending_count() == 0  # type: ignore[union-attr]

    neighbors = _hec.get_hot_neighbors(1)
    assert neighbors == [(2, 2)], f"Expected [(2, 2)], got {neighbors}"


# ---------------------------------------------------------------------------
# Test 5: L1 fallback when Rust unavailable
# ---------------------------------------------------------------------------

def test_l1_fallback_when_rust_unavailable(monkeypatch):
    """
    When _L1_AVAILABLE is forced to False, record_edge() must fall back
    to _record_edge_lmdb and succeed without error.
    """
    monkeypatch.setattr(_hec, "_L1_AVAILABLE", False, raising=False)
    monkeypatch.setattr(_hec, "_EDGE_COUNTER_L1", None, raising=False)

    lmdb_calls: list[tuple[int, int]] = []
    original = _hec._record_edge_lmdb

    def _track_lmdb(src_id: int, dst_id: int) -> bool:
        lmdb_calls.append((src_id, dst_id))
        return original(src_id, dst_id)

    monkeypatch.setattr(_hec, "_record_edge_lmdb", _track_lmdb, raising=False)

    result = _hec.record_edge(10, 20)

    assert result is True
    assert lmdb_calls == [(10, 20)], "LMDB path must be called when L1 unavailable"
    assert _hec.get_hot_neighbors(10) == [(20, 1)]


# ---------------------------------------------------------------------------
# Test 6: stats includes l1_pending
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _L1_AVAILABLE, reason="hledac_rust_extensions required")
def test_stats_includes_l1_pending(_flush_threshold_env):
    """
    After record_edge(1, 2) with flush_threshold=100 (no flush possible),
    stats()["l1_pending"] must be 1.
    """
    _flush_threshold_env(100)

    _hec.record_edge(1, 2)

    s = _hec.stats()
    assert s["l1_pending"] > 0, f"Expected l1_pending > 0, got {s['l1_pending']}"
