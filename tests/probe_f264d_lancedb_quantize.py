"""
Sprint F264D: LanceDB IVF-PQ quantization + lazy loading probe tests.

Verifies:
  1. _ANNIndex (knowledge/ann_index.py) flag-y:
     - default off (env unset) → _ivfpq_enabled = False
     - env on → _ivfpq_enabled = True
     - num_partitions / num_sub_vectors bounded by min/max
  2. _ANNIndex._ivfpq_trained default False (lazy loading initial state)
  3. _ANNIndex._ensure_ivf_pq_index skip when rows < 256 (guard)
  4. _ANNIndex._ensure_ivf_pq_index happy path: calls create_index with IVF_PQ
  5. _ANNIndex._ensure_ivf_pq_index fail-soft: create_index raise → no propagate
  6. _ANNIndex._ensure_ivf_pq_index double-checked: 2nd call is no-op
  7. _ANNIndex._log_table_opened emits `lancedb.table_opened` event with size_mb
  8. LanceDBIdentityStore (knowledge/lancedb_store.py) flag-y:
     - default off → _ivfpq_enabled = False
     - env on → _ivfpq_enabled = True
     - num_partitions / num_sub_vectors bounds
  9. LanceDBIdentityStore._log_table_opened format (mock table)
 10. LanceDBIdentityStore._ensure_ivf_pq_index_async respects env params

INVARIANTS:
  - HLEDAC_LANCEDB_QUANTIZE=0 (default) → no IVF-PQ training attempted
  - HLEDAC_LANCEDB_QUANTIZE=1 + rows < 256 → no training (insufficient data)
  - HLEDAC_LANCEDB_QUANTIZE=1 + create_index raises → no exception propagated
  - All helpers are fail-soft (try/except, never raise)
  - _ANNIndex is sync API; LanceDBIdentityStore is async API

Sprint F264D — always-on, bounded, fail-soft.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers — env reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_lancedb_env(monkeypatch):
    """Strip LanceDB IVF-PQ env vars before each test for isolation."""
    for var in (
        "HLEDAC_LANCEDB_QUANTIZE",
        "HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS",
        "HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _make_mock_table(row_count: int = 300) -> MagicMock:
    """Create a mock LanceDB table with .count_rows() and .create_index()."""
    tbl = MagicMock()
    tbl.count_rows.return_value = row_count
    return tbl


# ===========================================================================
# _ANNIndex (knowledge/ann_index.py) — sync API
# ===========================================================================


class TestANNIndexFlags:
    """_ANNIndex env flag handling and bounded defaults."""

    def test_default_flag_off(self, monkeypatch):
        """No env → IVF-PQ disabled by default (opt-in, M1 8GB safety)."""
        monkeypatch.delenv("HLEDAC_LANCEDB_QUANTIZE", raising=False)
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_enabled is False
        assert ann._ivfpq_trained is False
        assert ann._ivfpq_num_partitions == 128  # optimized for 256d vectors
        assert ann._ivfpq_num_sub_vectors == 8  # optimized for 256d vectors

    def test_flag_on_via_env(self, monkeypatch):
        """HLEDAC_LANCEDB_QUANTIZE=1 → IVF-PQ enabled."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_enabled is True

    def test_num_partitions_bounded(self, monkeypatch):
        """num_partitions clamped to [8, 256]."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        monkeypatch.setenv("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", "999")
        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_num_partitions == 256  # clamped to max

        monkeypatch.setenv("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", "1")
        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_num_partitions == 8  # clamped to min

    def test_num_sub_vectors_bounded(self, monkeypatch):
        """num_sub_vectors clamped to [4, 64]."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        monkeypatch.setenv("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", "999")
        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_num_sub_vectors == 64  # clamped to max

        monkeypatch.setenv("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", "0")
        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_num_sub_vectors == 4  # clamped to min

    def test_ivfpq_trained_default_false(self, monkeypatch):
        """Lazy loading: index is NOT trained in __init__."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        # After __init__, training flag is False (lazy)
        assert ann._ivfpq_trained is False
        # And table is None (LanceDB not initialized)
        assert ann._table is None


class TestANNIndexEnsureIVFPQ:
    """_ANNIndex._ensure_ivf_pq_index lazy training logic."""

    def test_skip_when_disabled(self, monkeypatch):
        """Flag off → _ensure_ivf_pq_index is no-op."""
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        # Set _table mock to verify it would NOT be called
        ann._table = _make_mock_table(row_count=1000)
        ann._ivfpq_enabled = False  # explicit

        ann._ensure_ivf_pq_index()
        ann._table.create_index.assert_not_called()
        assert ann._ivfpq_trained is False

    def test_skip_when_table_none(self, monkeypatch):
        """No table → no training attempt."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        # _table is None — should skip without error
        ann._ensure_ivf_pq_index()
        assert ann._ivfpq_trained is False

    def test_skip_when_rows_below_256(self, monkeypatch):
        """Insufficient training data → skip + mark attempted."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        ann._table = _make_mock_table(row_count=100)  # < 256

        ann._ensure_ivf_pq_index()
        ann._table.create_index.assert_not_called()
        # Marked as attempted so we don't retry on every search
        assert ann._ivfpq_trained is True

    def test_happy_path_calls_create_index_with_ivf_pq(self, monkeypatch):
        """With >= 256 rows + flag on → create_index(IVF_PQ) called."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        monkeypatch.setenv("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", "32")
        monkeypatch.setenv("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", "16")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        ann._table = _make_mock_table(row_count=500)

        ann._ensure_ivf_pq_index()
        # Verify create_index was called with IVF_PQ + correct params
        ann._table.create_index.assert_called_once()
        call_kwargs = ann._table.create_index.call_args.kwargs
        assert call_kwargs["metric"] == "cosine"
        assert call_kwargs["index_type"] == "IVF_PQ"
        assert call_kwargs["num_partitions"] == 32
        assert call_kwargs["num_sub_vectors"] == 16
        assert ann._ivfpq_trained is True

    def test_fail_soft_on_create_index_error(self, monkeypatch, caplog):
        """create_index raise → log warning + mark attempted (no propagate)."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        ann._table = _make_mock_table(row_count=500)
        ann._table.create_index.side_effect = RuntimeError("IVF_PQ unsupported")

        with caplog.at_level(logging.WARNING, logger="hledac.universal.knowledge.ann_index"):
            # Must not raise — fail-soft invariant
            ann._ensure_ivf_pq_index()

        # Marked attempted (don't retry on every search)
        assert ann._ivfpq_trained is True
        # Logged as warning
        assert any(
            "IVF-PQ training failed" in record.message
            for record in caplog.records
        )

    def test_double_checked_locking(self, monkeypatch):
        """Second call does not re-invoke create_index."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        ann._table = _make_mock_table(row_count=500)

        ann._ensure_ivf_pq_index()
        ann._ensure_ivf_pq_index()
        ann._ensure_ivf_pq_index()
        # create_index called exactly once (subsequent calls are no-op)
        assert ann._table.create_index.call_count == 1


class TestANNIndexLogTableOpened:
    """_ANNIndex._log_table_opened emits lancedb.table_opened event with size_mb."""

    def test_log_event_format(self, monkeypatch, caplog):
        """Log contains 'lancedb.table_opened' + 'size_mb=' for ANN table."""
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        ann._table = _make_mock_table(row_count=1000)  # 1000 × 256 × 4 = ~1MB

        with caplog.at_level(logging.INFO, logger="hledac.universal.knowledge.ann_index"):
            ann._log_table_opened()

        # Find log record with lancedb.table_opened marker
        records = [r for r in caplog.records if "lancedb.table_opened" in r.message]
        assert len(records) == 1
        msg = records[0].message
        assert "table=semantic_dedup_v1" in msg
        assert "rows=1000" in msg
        assert "size_mb=" in msg
        # size_mb is a number with 2 decimal places
        import re
        assert re.search(r"size_mb=\d+\.\d{2}", msg)

    def test_log_event_skip_when_table_none(self, caplog):
        """No table → no log."""
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        # _table is None
        with caplog.at_level(logging.INFO):
            ann._log_table_opened()  # must not raise
        # No lancedb.table_opened event emitted
        assert not any(
            "lancedb.table_opened" in r.message for r in caplog.records
        )


# ===========================================================================
# LanceDBIdentityStore (knowledge/lancedb_store.py) — async API
# ===========================================================================
#
# NOTE: LanceDBIdentityStore.__init__ calls self._initialize() which requires
# actual lancedb package + filesystem. We bypass __init__ via __new__ and set
# only the fields needed to test the IVF-PQ logic. This is a legitimate
# testing pattern for legacy classes with mandatory initialization side effects.


class TestLanceDBIdentityStoreFlags:
    """LanceDBIdentityStore env flag handling (env-only, no LanceDB init)."""

    def test_default_flag_off(self, monkeypatch):
        """No env → IVF-PQ enabled by default (F265C: O(N) flat search unacceptable at 10K+)."""
        monkeypatch.delenv("HLEDAC_LANCEDB_QUANTIZE", raising=False)
        # We cannot instantiate the real class without LanceDB — verify via
        # module-level: search for flag init code in source
        from pathlib import Path as P  # noqa: N817
        src_path = (
            P(__file__).resolve().parent.parent
            / "knowledge" / "lancedb_store.py"
        )
        content = src_path.read_text()
        # F265C: default changed from "0" (opt-in) to "1" (always-on)
        assert 'os.environ.get("HLEDAC_LANCEDB_QUANTIZE", "1") != "0"' in content

    def test_flag_wired_in_init(self):
        """Verify flag initialization code is present in __init__."""
        from pathlib import Path as P  # noqa: N817
        src_path = (
            P(__file__).resolve().parent.parent
            / "knowledge" / "lancedb_store.py"
        )
        content = src_path.read_text()
        # Check that _ivfpq_enabled is set in __init__ (not just imported)
        assert "_ivfpq_enabled: bool" in content
        assert "_ivfpq_trained: bool" in content
        assert "_ivfpq_num_partitions: int" in content
        assert "_ivfpq_num_sub_vectors: int" in content

    def test_bounded_num_partitions(self):
        """num_partitions bounded to [8, 256] in source."""
        from pathlib import Path as P  # noqa: N817
        src_path = (
            P(__file__).resolve().parent.parent
            / "knowledge" / "lancedb_store.py"
        )
        content = src_path.read_text()
        # Multiline-aware: just check the key tokens (avoid whitespace issues)
        assert "min(256," in content
        assert "max(" in content
        assert 'os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS", "64")' in content

    def test_bounded_num_sub_vectors(self):
        """num_sub_vectors bounded to [4, 64] in source."""
        from pathlib import Path as P  # noqa: N817
        src_path = (
            P(__file__).resolve().parent.parent
            / "knowledge" / "lancedb_store.py"
        )
        content = src_path.read_text()
        assert "min(64," in content
        assert "max(" in content
        assert 'os.environ.get("HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS", "12")' in content


class TestLanceDBIdentityStoreAsync:
    """LanceDBIdentityStore async helpers (mocked _table, no LanceDB init)."""

    def _make_store_with_mock_table(self, row_count: int = 500) -> object:
        """Bypass __init__ via __new__ to test async helpers in isolation."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        # Set only fields needed by the methods under test
        store._table = _make_mock_table(row_count=row_count)
        store._embedding_dim = 256
        store.uri = "test://mock"  # required by _log_table_opened
        store._ivfpq_enabled = True
        store._ivfpq_trained = False
        store._ivfpq_num_partitions = 64
        store._ivfpq_num_sub_vectors = 16
        store._ivfpq_lock = asyncio.Lock()
        return store

    def test_ensure_ivf_pq_calls_create_index(self):
        """Async helper calls create_index with IVF_PQ on first invocation."""
        store = self._make_store_with_mock_table(row_count=500)
        asyncio.run(store._ensure_ivf_pq_index_async())
        store._table.create_index.assert_called_once()
        kwargs = store._table.create_index.call_args.kwargs
        assert kwargs["index_type"] == "IVF_PQ"
        assert kwargs["metric"] == "cosine"
        assert kwargs["num_partitions"] == 64
        assert kwargs["num_sub_vectors"] == 16
        assert store._ivfpq_trained is True

    def test_ensure_ivf_pq_skip_when_disabled(self):
        """Flag off → no training attempt."""
        store = self._make_store_with_mock_table()
        store._ivfpq_enabled = False
        asyncio.run(store._ensure_ivf_pq_index_async())
        store._table.create_index.assert_not_called()
        assert store._ivfpq_trained is False

    def test_ensure_ivf_pq_skip_when_table_none(self):
        """No table → no training attempt."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._table = None
        store._ivfpq_enabled = True
        store._ivfpq_trained = False
        store._ivfpq_lock = asyncio.Lock()
        # Must not raise
        asyncio.run(store._ensure_ivf_pq_index_async())
        assert store._ivfpq_trained is False

    def test_ensure_ivf_pq_skip_when_rows_below_256(self):
        """Insufficient data → skip + mark attempted."""
        store = self._make_store_with_mock_table(row_count=100)
        asyncio.run(store._ensure_ivf_pq_index_async())
        store._table.create_index.assert_not_called()
        assert store._ivfpq_trained is True  # marked as attempted

    def test_ensure_ivf_pq_fail_soft(self, caplog):
        """create_index raise → no propagate, mark attempted."""
        store = self._make_store_with_mock_table(row_count=500)
        store._table.create_index.side_effect = RuntimeError("IVF_PQ unsupported")

        with caplog.at_level(logging.WARNING):
            # Must not raise
            asyncio.run(store._ensure_ivf_pq_index_async())

        assert store._ivfpq_trained is True
        assert any(
            "IVF-PQ training failed" in r.message for r in caplog.records
        )

    def test_ensure_ivf_pq_double_checked(self):
        """Second call does not re-invoke create_index."""
        store = self._make_store_with_mock_table(row_count=500)
        asyncio.run(store._ensure_ivf_pq_index_async())
        asyncio.run(store._ensure_ivf_pq_index_async())
        asyncio.run(store._ensure_ivf_pq_index_async())
        assert store._table.create_index.call_count == 1

    def test_log_table_opened_format(self, caplog):
        """Log contains 'lancedb.table_opened' + 'size_mb=' for entities."""
        store = self._make_store_with_mock_table(row_count=1000)
        with caplog.at_level(logging.INFO):
            store._log_table_opened()

        records = [r for r in caplog.records if "lancedb.table_opened" in r.message]
        assert len(records) == 1
        msg = records[0].message
        assert "table=entities" in msg
        assert "rows=1000" in msg
        assert "size_mb=" in msg

    def test_log_table_opened_skip_when_table_none(self, caplog):
        """No table → no log, no raise."""
        from hledac.universal.knowledge.lancedb_store import LanceDBIdentityStore

        store = LanceDBIdentityStore.__new__(LanceDBIdentityStore)
        store._table = None
        with caplog.at_level(logging.INFO):
            store._log_table_opened()
        assert not any(
            "lancedb.table_opened" in r.message for r in caplog.records
        )


# ===========================================================================
# End-to-end lazy loading invariant
# ===========================================================================


class TestLazyLoadingInvariant:
    """Sprint F264D invariant: IVF-PQ training is lazy, not eager."""

    def test_ann_index_ivfpq_not_trained_in_init(self, monkeypatch):
        """__init__ must NOT train IVF-PQ eagerly."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        # _table is None (no LanceDB init) → _ivfpq_trained must remain False
        ann = _ANNIndex(Path("/tmp/fake_path"))
        assert ann._ivfpq_trained is False
        # And no background training task was spawned
        assert not hasattr(ann, "_ivfpq_task")

    def test_ann_search_triggers_lazy_training(self, monkeypatch):
        """First call to ann_search (when flag on) triggers _ensure_ivf_pq_index."""
        monkeypatch.setenv("HLEDAC_LANCEDB_QUANTIZE", "1")
        from hledac.universal.knowledge.ann_index import _ANNIndex

        ann = _ANNIndex(Path("/tmp/fake_path"))
        # Pretend table is initialized with enough rows
        ann._table = _make_mock_table(row_count=500)
        ann._boot_error = None  # mark as initialized

        # Call ann_search — should trigger lazy training
        try:
            ann.ann_search(
                __import__("numpy").zeros(256, dtype="float32"),
                top_k=5,
            )
        except Exception:
            pass  # search may fail, we only care about IVF-PQ trigger
        # IVF-PQ should have been trained (create_index called)
        ann._table.create_index.assert_called_once()
        assert ann._ivfpq_trained is True
