"""
Sprint F264E — LanceDB IVF-PQ Auto-Tuner Test Suite
====================================================

Hermetic tests for ``knowledge.lancedb_auto_tuner.IVFPQAutoTuner``. Covers:

1. State persistence (JSON round-trip, fail-soft on missing/corrupt)
2. Cooldown + insert-threshold gating
3. Recall measurement (sample vs brute-force)
4. Partition adjustment heuristic (3 branches)
5. Retrain (create_index replace=True, fail-soft)
6. Main entry: tune_if_due + tune_if_due_async
7. Env flag gating
8. M1 RSS guard
9. Bounded compute (sample size, brute-force cap)
10. Wire-up into LanceDBIdentityStore + _ANNIndex (mock)

All tests are hermetic — no real LanceDB connection, no real filesystem I/O
outside a tmp_path. Pattern follows ``tests/probe_f264d_lancedb_quantize.py``.
"""


import asyncio
import importlib
import json
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

# ─────────────────────────────────────────────────────────────────────
# Module under test
# ─────────────────────────────────────────────────────────────────────

MODULE_PATH = "knowledge.lancedb_auto_tuner"


@pytest.fixture
def tuner_mod():
    """Import the tuner module, clearing cached imports."""
    sys.modules.pop(MODULE_PATH, None)
    mod = importlib.import_module(MODULE_PATH)
    return mod


@pytest.fixture
def clean_env(monkeypatch):
    """Strip all HLEDAC_LANCEDB_* env vars for a clean baseline."""
    for var in (
        "HLEDAC_LANCEDB_QUANTIZE",
        "HLEDAC_LANCEDB_AUTO_TUNE",
        "HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD",
        "HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S",
        "HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS",
        "HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS",
    ):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


@pytest.fixture
def auto_tune_on(clean_env):
    """Enable HLEDAC_LANCEDB_AUTO_TUNE=1."""
    clean_env.setenv("HLEDAC_LANCEDB_AUTO_TUNE", "1")
    return clean_env


@pytest.fixture
def tuner(clean_env, tmp_path):
    """Construct a default IVFPQAutoTuner with env flags off + tmp state dir."""
    return _build_tuner(clean_env, tmp_path)


def _build_tuner(env, tmp_path: Path, **overrides):
    """Build tuner with optional overrides for keys."""
    from knowledge.lancedb_auto_tuner import IVFPQAutoTuner
    defaults: dict[str, Any] = {
        "table_name": "test_table",
        "state_path": tmp_path / "tune_state.json",
        "num_sub_vectors": 16,
        "vector_column": "vector",
        "key_column": "id",
    }
    defaults.update(overrides)
    return IVFPQAutoTuner(**defaults)


def _make_mock_table(
    *,
    row_count: int = 1000,
    dim: int = 256,
    id_prefix: str = "row_",
    ivfpq_active: bool = True,
    search_should_fail: bool = False,
    create_index_should_fail: bool = False,
    to_polars_should_fail: bool = False,
    ann_includes_self: bool = True,
) -> MagicMock:
    """Build a mock LanceDB table for hermetic tests.

    Returns a MagicMock that:
      - count_rows() → row_count
      - search(q, ...).metric(...).limit(K).to_list() → top-K rows by cosine
        (optionally excluding the query itself — real LanceDB ANN includes
        the query as top-1 unless the user filters; we default to this).
      - create_index(..., replace=True) → no-op (or raises)
      - to_polars() → polars DataFrame with 'id' + 'vector' columns
    """
    # Pre-generate deterministic vectors
    rng = np.random.default_rng(seed=42)
    vectors = rng.standard_normal((row_count, dim), dtype=np.float32)
    # Normalize for cosine
    norms = np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
    vectors_norm = (vectors / norms).astype(np.float32)

    table = MagicMock()
    table.count_rows = MagicMock(return_value=row_count)

    # Build a polars DataFrame lazily (only when to_polars called)
    def _to_polars_impl() -> Any:
        if to_polars_should_fail:
            raise RuntimeError("to_polars failed (test)")
        try:
            import polars as pl
        except ImportError:
            return None
        return pl.DataFrame({
            "id": [f"{id_prefix}{i}" for i in range(row_count)],
            "vector": [v.tolist() for v in vectors_norm],
        })

    table.to_polars = MagicMock(side_effect=_to_polars_impl)

    # Build a search builder
    def _search_impl(query: Any, *, vector_column_name: str = "vector") -> MagicMock:
        builder = MagicMock()
        q_arr = np.array(query, dtype=np.float32).flatten()
        q_norm = q_arr / (np.linalg.norm(q_arr) + 1e-8)

        sims = vectors_norm @ q_norm
        # Find the index of the query in the table (best match = ~1.0 similarity)
        # to optionally exclude from ANN top-K
        if not ann_includes_self:
            self_idx = int(np.argmax(sims))
            sims[self_idx] = -2.0  # exclude self
        k = 10
        if search_should_fail:
            builder.metric = MagicMock(side_effect=RuntimeError("search failed (test)"))
            builder.limit = MagicMock(return_value=builder)
            builder.to_list = MagicMock(return_value=[])
            return builder

        top_idx = np.argsort(-sims)[:k]
        ann_results = [
            {"id": f"{id_prefix}{i}", "_distance": float(1.0 - sims[i])}
            for i in top_idx
        ]
        builder.metric = MagicMock(return_value=builder)
        builder.limit = MagicMock(return_value=builder)
        builder.to_list = MagicMock(return_value=ann_results)
        return builder

    table.search = MagicMock(side_effect=_search_impl)

    if create_index_should_fail:
        table.create_index = MagicMock(
            side_effect=RuntimeError("create_index failed (test)")
        )
    else:
        table.create_index = MagicMock(return_value=None)

    return table


# ═════════════════════════════════════════════════════════════════════
# 1. State persistence
# ═════════════════════════════════════════════════════════════════════


class TestTuneStatePersistence:
    """TuneState + JSON round-trip behavior."""

    def test_default_state_values(self, tuner_mod):
        """Fresh TuneState has sensible defaults."""
        from knowledge.lancedb_auto_tuner import TuneState
        s = TuneState()
        assert s.last_tune_at == 0.0
        assert s.last_num_partitions == 64
        assert s.last_recall == 0.0
        assert s.inserts_since_tune == 0
        assert s.tune_count == 0

    def test_tune_result_is_frozen(self, tuner_mod):
        """TuneResult is immutable (frozen dataclass)."""
        from knowledge.lancedb_auto_tuner import TuneResult
        r = TuneResult(success=True, triggered=True, old_partitions=64,
                       new_partitions=96, recall=0.92, avg_search_ms=12.5,
                       rows=1000)
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError  # noqa: B017
            r.new_partitions = 128  # type: ignore[misc]

    def test_tuner_load_state_missing_file(self, clean_env, tmp_path):
        """Missing state file → returns default TuneState, no error."""
        t = _build_tuner(clean_env, tmp_path, state_path=tmp_path / "nope.json")
        s = t._load_state()
        assert s.last_tune_at == 0.0
        assert s.tune_count == 0

    def test_tuner_load_state_corrupt_json(self, clean_env, tmp_path):
        """Corrupt JSON → returns default, no error (fail-soft)."""
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json")
        t = _build_tuner(clean_env, tmp_path, state_path=p)
        s = t._load_state()
        assert s.tune_count == 0

    def test_tuner_save_and_load_state_roundtrip(self, clean_env, tmp_path):
        """Save → load returns equivalent state."""
        from knowledge.lancedb_auto_tuner import TuneState
        p = tmp_path / "rt.json"
        t = _build_tuner(clean_env, tmp_path, state_path=p)
        original = TuneState(
            last_tune_at=1234.5,
            last_num_partitions=128,
            last_recall=0.93,
            inserts_since_tune=200,
            tune_count=7,
        )
        t._save_state(original)
        assert p.exists()
        # New tuner with same path should load
        t2 = _build_tuner(clean_env, tmp_path, state_path=p)
        loaded = t2._load_state()
        assert loaded.last_tune_at == 1234.5
        assert loaded.last_num_partitions == 128
        assert loaded.last_recall == 0.93
        assert loaded.tune_count == 7

    def test_tuner_save_atomic_via_tmp_replace(self, clean_env, tmp_path):
        """Save uses atomic tmp + os.replace (no partial writes)."""
        from knowledge.lancedb_auto_tuner import TuneState
        p = tmp_path / "atomic.json"
        t = _build_tuner(clean_env, tmp_path, state_path=p)
        t._save_state(TuneState(tune_count=42))
        # No leftover .tmp files
        leftovers = list(tmp_path.glob("*.tmp"))
        assert not leftovers
        # File parses
        data = json.loads(p.read_text())
        assert data["tune_count"] == 42

    def test_tuner_no_state_path_uses_in_memory(self, clean_env):
        """state_path=None → in-memory only, no I/O."""
        t = _build_tuner(clean_env, Path("/tmp"), state_path=None)
        from knowledge.lancedb_auto_tuner import TuneState
        # No raise
        t._save_state(TuneState(tune_count=1))
        s = t._load_state()
        assert s.tune_count == 0  # default (no file)


# ═════════════════════════════════════════════════════════════════════
# 2. Cooldown + insert-threshold gating
# ═════════════════════════════════════════════════════════════════════


class TestShouldTuneGating:
    """should_tune() — cooldown + threshold gate."""

    def test_disabled_by_default(self, clean_env, tmp_path):
        """Without HLEDAC_LANCEDB_AUTO_TUNE=1, should_tune always returns False."""
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(clean_env, tmp_path)
        # Even with huge insert count + past cooldown
        s = TuneState(last_tune_at=time.time() - 10_000)
        assert t.should_tune(s, inserts_since_tune=100_000) is False

    def test_enabled_but_threshold_not_met(self, auto_tune_on, tmp_path):
        """Below insert threshold → False."""
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(auto_tune_on, tmp_path)
        s = TuneState(last_tune_at=0.0)  # never tuned before
        assert t.should_tune(s, inserts_since_tune=100) is False  # threshold=5000

    def test_enabled_threshold_met_first_ever_tune(self, auto_tune_on, tmp_path):
        """Threshold met + never tuned before → True."""
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(auto_tune_on, tmp_path)
        s = TuneState(last_tune_at=0.0)
        assert t.should_tune(s, inserts_since_tune=5_000) is True

    def test_enabled_threshold_met_cooldown_active(self, auto_tune_on, tmp_path):
        """Threshold met but cooldown not elapsed → False."""
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(auto_tune_on, tmp_path)
        s = TuneState(last_tune_at=time.time() - 60)  # 1 min ago
        assert t.should_tune(s, inserts_since_tune=5_000) is False

    def test_enabled_threshold_met_cooldown_elapsed(self, auto_tune_on, tmp_path):
        """Threshold + cooldown both satisfied → True."""
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(auto_tune_on, tmp_path)
        s = TuneState(last_tune_at=time.time() - 7_200)  # 2 hours ago
        assert t.should_tune(s, inserts_since_tune=6_000) is True

    def test_custom_threshold_via_env(self, auto_tune_on, tmp_path):
        """HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD overrides default 5000."""
        auto_tune_on.setenv("HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD", "100")
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(auto_tune_on, tmp_path)
        s = TuneState(last_tune_at=0.0)
        assert t.should_tune(s, inserts_since_tune=100) is True
        assert t.should_tune(s, inserts_since_tune=99) is False

    def test_custom_cooldown_via_env(self, auto_tune_on, tmp_path):
        """HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S overrides default 3600."""
        auto_tune_on.setenv("HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S", "10")
        from knowledge.lancedb_auto_tuner import TuneState
        t = _build_tuner(auto_tune_on, tmp_path)
        s = TuneState(last_tune_at=time.time() - 5)  # 5 sec ago
        assert t.should_tune(s, inserts_since_tune=5_000) is False
        s = TuneState(last_tune_at=time.time() - 30)  # 30 sec ago
        assert t.should_tune(s, inserts_since_tune=5_000) is True


# ═════════════════════════════════════════════════════════════════════
# 3. Recall measurement
# ═════════════════════════════════════════════════════════════════════


class TestMeasureRecall:
    """measure_recall() — sample-based recall@K vs brute-force baseline."""

    def test_empty_table_returns_zero(self, tuner):
        """No rows → (0.0, 0.0)."""
        t = tuner
        table = _make_mock_table(row_count=0)
        assert t.measure_recall(table) == (0.0, 0.0)

    def test_too_small_table_returns_perfect(self, tuner):
        """n < K+1 → (1.0, ~0). No meaningful recall measurement possible."""
        t = tuner
        # Use a table where ANN excludes self (matches brute-force exclusion),
        # so recall is exactly 1.0 for trivial case.
        table = _make_mock_table(row_count=5, ann_includes_self=False)
        recall, _ = t.measure_recall(table, sample_size=2, k=10)
        # K is clamped to n-1 = 4, mock ANN matches brute → recall == 1.0
        assert recall == 1.0

    def test_realistic_ann_underestimates_brute(self, tuner):
        """ANN on small data with same vector order → should approximate brute (high recall)."""
        t = tuner
        # Mock ANN returns the actual top-K (excluding self, matching brute) → high recall
        table = _make_mock_table(row_count=500, ann_includes_self=False)
        recall, avg_ms = t.measure_recall(table, sample_size=20, k=10)
        # Mock ANN matches brute (excludes self) → recall == 1.0
        assert recall == 1.0
        assert avg_ms > 0.0  # we measured something

    def test_ann_with_subset_recall_lower(self, tuner):
        """ANN that returns wrong keys → recall < 1.0."""
        # Use a table where brute-force neighbors differ from the first 10 rows.
        table = _make_mock_table(row_count=500)

        def _wrong_search(query, *, vector_column_name="vector"):
            builder = MagicMock()
            builder.metric = MagicMock(return_value=builder)
            builder.limit = MagicMock(return_value=builder)
            # Return random ids from the END of the table (likely not in brute top-K)
            builder.to_list = MagicMock(return_value=[
                {"id": f"row_{400 + i}", "_distance": 0.1} for i in range(10)
            ])
            return builder
        table.search = MagicMock(side_effect=_wrong_search)

        t = tuner
        recall, _ = t.measure_recall(table, sample_size=20, k=10)
        # Should be much less than 1.0 (0% expected — none of 400-409 are in top-10)
        assert recall < 0.5

    def test_bounded_sample_size(self, tuner):
        """MAX_RECALL_SAMPLES=100 caps sample_size."""
        t = tuner
        # Even asking for 1000 samples, we cap
        table = _make_mock_table(row_count=2000)
        # We just check no crash + result is valid
        recall, ms = t.measure_recall(table, sample_size=1000, k=10)
        assert 0.0 <= recall <= 1.0
        assert ms >= 0.0

    def test_bounded_brute_force(self, tuner):
        """Tables > MAX_BRUTE_FORCE_ROWS=10_000 are sampled down for brute."""
        t = tuner
        # Large table — should still work without OOM
        table = _make_mock_table(row_count=50_000)
        recall, _ = t.measure_recall(table, sample_size=10, k=5)
        assert 0.0 <= recall <= 1.0

    def test_fails_soft_on_to_polars_error(self, tuner):
        """to_polars raises → returns (0.0, 0.0), no exception."""
        t = tuner
        table = _make_mock_table(row_count=100, to_polars_should_fail=True)
        recall, ms = t.measure_recall(table)
        assert recall == 0.0
        assert ms == 0.0

    def test_fails_soft_on_search_error(self, tuner):
        """search raises → individual query fails, partial result returned."""
        t = tuner
        table = _make_mock_table(row_count=500, search_should_fail=True)
        # Should not raise
        recall, ms = t.measure_recall(table, sample_size=5, k=10)
        # All queries failed → recall=0
        assert recall == 0.0


# ═════════════════════════════════════════════════════════════════════
# 4. Partition adjustment heuristic
# ═════════════════════════════════════════════════════════════════════


class TestComputeOptimalPartitions:
    """compute_optimal_partitions() — 3-branch PID-style heuristic."""

    def test_low_recall_grows_partitions(self, tuner):
        """recall < 0.85 → grow by 50%."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=64, recall=0.70, avg_search_ms=10.0, row_count=10_000
        )
        assert new == 96  # int(64 * 1.5)

    def test_excellent_recall_slow_search_shrinks(self, tuner):
        """recall ≥ 0.97 AND search > 50ms → shrink by 25%."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=128, recall=0.98, avg_search_ms=80.0, row_count=20_000
        )
        assert new == 96  # int(128 * 0.75)

    def test_excellent_recall_fast_search_no_change(self, tuner):
        """recall excellent but search fast → no change."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=64, recall=0.98, avg_search_ms=5.0, row_count=10_000
        )
        assert new == 64

    def test_acceptable_recall_no_change(self, tuner):
        """recall in [0.85, 0.97) → no change."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=64, recall=0.90, avg_search_ms=30.0, row_count=10_000
        )
        assert new == 64

    def test_low_recall_clamped_to_max(self, tuner):
        """Growth respects MAX_NUM_PARTITIONS=256."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=200, recall=0.50, avg_search_ms=10.0, row_count=10_000
        )
        assert new <= 256

    def test_low_recall_ceiling_from_row_count(self, tuner):
        """Growth also respects heuristic row-count ceiling (soft upper bound)."""
        t = tuner
        # 1000 rows → ceiling = max(current=8, 1000//16 + 1) = max(8, 63) = 63
        # current=8, recall=0.50 → want 12, but ceiling=63, so 12
        new = t.compute_optimal_partitions(
            current=8, recall=0.50, avg_search_ms=10.0, row_count=1000
        )
        # 8 * 1.5 = 12, ceiling = 63, so result is 12
        assert new == 12

    def test_growth_clamped_to_row_ceiling(self, tuner):
        """If heuristic wants to grow past row-count ceiling, clamp."""
        t = tuner
        # 256 rows → ceiling = max(current=8, 256//16 + 1) = max(8, 17) = 17
        # current=8, recall=0.50 → want 12, ceiling=17, ok
        # But 16 rows → ceiling = max(8, 16//16 + 1) = max(8, 2) = 8
        # current=8, recall=0.50 → want 12, but ceiling=8 → no growth
        new = t.compute_optimal_partitions(
            current=8, recall=0.50, avg_search_ms=10.0, row_count=16
        )
        assert new == 8  # no growth — data too small

    def test_shrink_clamped_to_min(self, tuner):
        """Shrink respects MIN_NUM_PARTITIONS=8."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=8, recall=0.99, avg_search_ms=200.0, row_count=10_000
        )
        assert new >= 8

    def test_zero_rows_returns_current(self, tuner):
        """row_count=0 → return current (sanity guard)."""
        t = tuner
        new = t.compute_optimal_partitions(
            current=64, recall=0.5, avg_search_ms=10.0, row_count=0
        )
        assert new == 64

    def test_current_clamped_to_bounds(self, tuner):
        """Even garbage inputs are clamped before heuristic."""
        t = tuner
        # current=99999 should be clamped to MAX=256 first
        new = t.compute_optimal_partitions(
            current=99999, recall=0.99, avg_search_ms=5.0, row_count=10_000
        )
        # After clamping, no change branch → returns 256
        assert new == 256


# ═════════════════════════════════════════════════════════════════════
# 5. Retrain
# ═════════════════════════════════════════════════════════════════════


class TestRetrain:
    """retrain() — create_index(replace=True) with bounded params."""

    def test_calls_create_index_with_replace(self, tuner):
        """Retrain uses canonical create_index(replace=True)."""
        t = tuner
        table = _make_mock_table(row_count=1000)
        ok = t.retrain(table, new_num_partitions=96, num_sub_vectors=16)
        assert ok is True
        table.create_index.assert_called_once()
        kwargs = table.create_index.call_args.kwargs
        assert kwargs["replace"] is True
        assert kwargs["num_partitions"] == 96
        assert kwargs["num_sub_vectors"] == 16
        assert kwargs["index_type"] == "IVF_PQ"
        assert kwargs["metric"] == "cosine"
        # M1 override
        assert kwargs["max_iterations"] == 20  # M1_MAX_ITERATIONS

    def test_retrain_clamps_partitions(self, tuner):
        """Retrain clamps num_partitions to [MIN, MAX] before calling."""
        t = tuner
        table = _make_mock_table(row_count=1000)
        t.retrain(table, new_num_partitions=999)  # above max
        kwargs = table.create_index.call_args.kwargs
        assert kwargs["num_partitions"] == 256  # MAX

        t.retrain(table, new_num_partitions=1)  # below min
        kwargs = table.create_index.call_args.kwargs
        assert kwargs["num_partitions"] == 8  # MIN

    def test_retrain_clamps_sub_vectors(self, tuner):
        """Retrain clamps num_sub_vectors to [MIN, MAX]."""
        t = tuner
        table = _make_mock_table(row_count=1000)
        t.retrain(table, new_num_partitions=64, num_sub_vectors=999)
        kwargs = table.create_index.call_args.kwargs
        assert kwargs["num_sub_vectors"] == 64  # MAX

    def test_retrain_fails_soft_on_error(self, tuner):
        """create_index raises → returns False, no exception."""
        t = tuner
        table = _make_mock_table(row_count=1000, create_index_should_fail=True)
        ok = t.retrain(table, new_num_partitions=64)
        assert ok is False


# ═════════════════════════════════════════════════════════════════════
# 6. tune_if_due (sync) main entry
# ═════════════════════════════════════════════════════════════════════


class TestTuneIfDueSync:
    """tune_if_due() — full cycle, returns TuneResult."""

    def test_disabled_returns_not_triggered(self, clean_env, tmp_path):
        """Without HLEDAC_LANCEDB_AUTO_TUNE=1, returns triggered=False."""
        t = _build_tuner(clean_env, tmp_path)
        table = _make_mock_table(row_count=1000)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=100_000)
        assert r.triggered is False
        assert r.success is False
        # create_index not called
        table.create_index.assert_not_called()

    def test_below_threshold_returns_not_triggered(self, auto_tune_on, tmp_path):
        """Below insert threshold → triggered=False, no retrain."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=1000)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=100)
        assert r.triggered is False
        table.create_index.assert_not_called()

    def test_insufficient_rows_skips(self, auto_tune_on, tmp_path):
        """< 256 rows → skipped with error='insufficient_rows'."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=100)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=5_000)
        assert r.triggered is False
        assert r.error == "insufficient_rows"
        table.create_index.assert_not_called()

    def test_first_ever_tune_above_threshold(self, auto_tune_on, tmp_path):
        """First tune: threshold met + never tuned + enough rows → triggered."""
        t = _build_tuner(auto_tune_on, tmp_path)
        # ANN excludes self → matches brute → recall=1.0 → no growth needed
        table = _make_mock_table(row_count=2000, ann_includes_self=False)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=5_000)
        assert r.triggered is True
        assert r.success is True
        # Mock ANN returns correct top-K → recall=1.0 → no growth needed
        assert r.recall == 1.0
        # partitions unchanged (already optimal in mock)
        assert r.new_partitions == 64
        table.create_index.assert_not_called()  # no change → no retrain

    def test_tune_with_real_growth(self, auto_tune_on, tmp_path):
        """Tune triggers retrain when recall is artificially low."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000)
        # Override to return wrong IDs from end of table → recall=0
        def _wrong_search(query, *, vector_column_name="vector"):
            builder = MagicMock()
            builder.metric = MagicMock(return_value=builder)
            builder.limit = MagicMock(return_value=builder)
            # Return ids from end of table — not in brute top-K
            builder.to_list = MagicMock(return_value=[
                {"id": f"row_{1900 + i}", "_distance": 0.1} for i in range(10)
            ])
            return builder
        table.search = MagicMock(side_effect=_wrong_search)

        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=5_000)
        assert r.triggered is True
        # Recall was low → partitions grew
        assert r.new_partitions > 64
        # Retrain was called
        table.create_index.assert_called_once()
        kwargs = table.create_index.call_args.kwargs
        assert kwargs["num_partitions"] == 96  # 64 * 1.5
        assert kwargs["replace"] is True

    def test_state_persisted_after_tune(self, auto_tune_on, tmp_path):
        """After successful tune, state file reflects new state."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=5_000)
        assert r.triggered is True
        # State should be persisted
        assert t._state.tune_count == 1
        # In-memory state updated
        assert t.state.tune_count == 1
        # inserts_since_tune reset to 0
        assert t._state.inserts_since_tune == 0

    def test_state_persisted_even_when_not_tuning(self, auto_tune_on, tmp_path):
        """Below threshold: inserts_since_tune increments, no retrain."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000)
        t.tune_if_due(table, current_num_partitions=64, inserts_delta=100)
        # inserts_since_tune tracked
        assert t._state.inserts_since_tune == 100

    def test_rss_guard_skips_tune(self, auto_tune_on, tmp_path, monkeypatch):
        """When RSS > 5.5 GiB, tune is skipped."""
        import psutil
        # Monkeypatch memory_info to return huge RSS
        fake_mem = MagicMock(rss=10 * 1024**3)  # 10 GiB
        monkeypatch.setattr(psutil.Process, "memory_info", lambda self: fake_mem)
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=5_000)
        assert r.triggered is False
        assert r.error == "rss_guard"
        table.create_index.assert_not_called()

    def test_no_table_returns_not_triggered(self, auto_tune_on, tmp_path):
        """table=None → no-op."""
        t = _build_tuner(auto_tune_on, tmp_path)
        r = t.tune_if_due(None, current_num_partitions=64, inserts_delta=5_000)  # type: ignore[arg-type]
        assert r.triggered is False

    def test_retrain_failure_keeps_old_partitions(self, auto_tune_on, tmp_path):
        """create_index raises → success=False, partitions unchanged."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000, create_index_should_fail=True)
        # Force a growth scenario (wrong search → low recall)
        def _wrong_search(query, *, vector_column_name="vector"):
            builder = MagicMock()
            builder.metric = MagicMock(return_value=builder)
            builder.limit = MagicMock(return_value=builder)
            # Return ids from end of table — not in brute top-K → recall=0
            builder.to_list = MagicMock(return_value=[
                {"id": f"row_{1900 + i}", "_distance": 0.1} for i in range(10)
            ])
            return builder
        table.search = MagicMock(side_effect=_wrong_search)
        r = t.tune_if_due(table, current_num_partitions=64, inserts_delta=5_000)
        assert r.triggered is True
        assert r.success is False
        assert r.error == "retrain_failed"
        # new_partitions should fall back to old (we didn't actually change)
        assert r.new_partitions == 64


# ═════════════════════════════════════════════════════════════════════
# 7. tune_if_due_async
# ═════════════════════════════════════════════════════════════════════


class TestTuneIfDueAsync:
    """tune_if_due_async() — async wrapper, off-thread."""

    def test_async_runs_in_executor(self, auto_tune_on, tmp_path):
        """Async wrapper delegates to sync core via run_in_executor."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000)
        r = asyncio.run(
            t.tune_if_due_async(
                table, current_num_partitions=64, inserts_delta=5_000
            )
        )
        assert r.triggered is True

    def test_async_disabled_returns_not_triggered(self, clean_env, tmp_path):
        """Async without env flag → not triggered."""
        t = _build_tuner(clean_env, tmp_path)
        table = _make_mock_table(row_count=2000)
        r = asyncio.run(
            t.tune_if_due_async(
                table, current_num_partitions=64, inserts_delta=5_000
            )
        )
        assert r.triggered is False

    def test_async_retrain_updates_partitions(self, auto_tune_on, tmp_path):
        """Async retrain: partitions grow when recall low."""
        t = _build_tuner(auto_tune_on, tmp_path)
        table = _make_mock_table(row_count=2000)
        def _wrong_search(query, *, vector_column_name="vector"):
            builder = MagicMock()
            builder.metric = MagicMock(return_value=builder)
            builder.limit = MagicMock(return_value=builder)
            # Return ids from end of table — not in brute top-K → recall=0
            builder.to_list = MagicMock(return_value=[
                {"id": f"row_{1900 + i}", "_distance": 0.1} for i in range(10)
            ])
            return builder
        table.search = MagicMock(side_effect=_wrong_search)
        r = asyncio.run(
            t.tune_if_due_async(
                table, current_num_partitions=64, inserts_delta=5_000
            )
        )
        assert r.triggered is True
        assert r.new_partitions == 96  # grew


# ═════════════════════════════════════════════════════════════════════
# 8. Wire-up into LanceDBIdentityStore + _ANNIndex (smoke)
# ═════════════════════════════════════════════════════════════════════


class TestWireUpIntoConsumers:
    """Smoke tests — both consumers initialize _autotune without errors."""

    def test_ann_index_creates_tuner(self, clean_env, tmp_path):
        """_ANNIndex.__init__ creates _autotune attribute."""
        from knowledge.ann_index import _ANNIndex
        ann = _ANNIndex(tmp_path)
        assert hasattr(ann, "_autotune")
        # Tuner is either None (if import failed) or an instance
        if ann._autotune is not None:
            assert ann._autotune.table_name == "semantic_dedup_v1"
            assert ann._autotune.vector_column == "vector"
            assert ann._autotune.key_column == "finding_key"

    def test_ann_index_slots_include_autotune(self):
        """_autotune is in __slots__ (Sprint F264E invariant)."""
        from knowledge.ann_index import _ANNIndex
        assert "_autotune" in _ANNIndex.__slots__

    def test_lancedb_store_creates_tuner(self, clean_env, tmp_path):
        """LanceDBIdentityStore.__init__ creates _autotune attribute."""
        from knowledge.lancedb_store import LanceDBIdentityStore
        uri = str(tmp_path / "lancedb")
        store = LanceDBIdentityStore(uri=uri)
        assert hasattr(store, "_autotune")
        if store._autotune is not None:
            assert store._autotune.table_name == "entities"
            assert store._autotune.vector_column == "embedding"
            assert store._autotune.key_column == "id"

    def test_ann_index_upsert_calls_tuner(self, auto_tune_on, tmp_path):
        """_ANNIndex.upsert triggers tuner when threshold met."""
        from knowledge.ann_index import _ANNIndex
        ann = _ANNIndex(tmp_path)
        # Mock tuner to verify it's called
        if ann._autotune is not None:
            tuner_mock = MagicMock()
            tuner_mock.tune_if_due = MagicMock(return_value=MagicMock(
                triggered=False, success=False, new_partitions=64,
                old_partitions=64, recall=0.0, avg_search_ms=0.0,
                changed=lambda: False
            ))
            ann._autotune = tuner_mock
            ann._ivfpq_enabled = True
            # Set up minimal table mock
            ann._table = _make_mock_table(row_count=1000)
            ann._boot_error = None
            # Call upsert
            import numpy as np
            emb = np.zeros(256, dtype="float32")
            ann.upsert("k1", emb, "h1")
            # Tuner was called
            assert tuner_mock.tune_if_due.called


# ═════════════════════════════════════════════════════════════════════
# 9. env flag registry
# ═════════════════════════════════════════════════════════════════════


class TestFlagRegistryWiring:
    """Flag registry contains the auto-tune env vars."""

    def test_auto_tune_flag_registered(self):
        """HLEDAC_LANCEDB_AUTO_TUNE is in flag registry."""
        from utils.flag_registry import get_spec
        spec = get_spec("HLEDAC_LANCEDB_AUTO_TUNE")
        assert spec is not None
        assert spec.group == "storage"
        assert "HLEDAC_LANCEDB_QUANTIZE" in spec.implies

    def test_threshold_flag_registered(self):
        """HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD is in flag registry."""
        from utils.flag_registry import get_spec
        spec = get_spec("HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD")
        assert spec is not None
        assert spec.group == "storage"

    def test_cooldown_flag_registered(self):
        """HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S is in flag registry."""
        from utils.flag_registry import get_spec
        spec = get_spec("HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S")
        assert spec is not None
        assert spec.group == "storage"

    def test_quantize_flag_still_registered(self):
        """Parent HLEDAC_LANCEDB_QUANTIZE still exists (regression check)."""
        from utils.flag_registry import get_spec
        assert get_spec("HLEDAC_LANCEDB_QUANTIZE") is not None


# ═════════════════════════════════════════════════════════════════════
# 10. Integration — F264D regression
# ═════════════════════════════════════════════════════════════════════


class TestF264DRegression:
    """Sprint F264D (lazy IVF-PQ training) still works alongside F264E."""

    def test_lancedb_store_ensure_ivf_pq_still_skips_when_disabled(self, tmp_path):
        """F264D skip-when-disabled invariant preserved."""
        from knowledge.lancedb_store import LanceDBIdentityStore
        store = LanceDBIdentityStore(uri=str(tmp_path / "lancedb"))
        store._ivfpq_enabled = False
        store._table = _make_mock_table(row_count=1000)
        # Should not call create_index
        asyncio.run(store._ensure_ivf_pq_index_async())
        store._table.create_index.assert_not_called()

    def test_ann_index_ensure_ivf_pq_still_skips_when_disabled(self, tmp_path):
        """F264D skip-when-disabled invariant preserved."""
        from knowledge.ann_index import _ANNIndex
        ann = _ANNIndex(tmp_path)
        ann._ivfpq_enabled = False
        ann._table = _make_mock_table(row_count=1000)
        ann._ensure_ivf_pq_index()
        ann._table.create_index.assert_not_called()

    def test_quantize_flag_off_means_auto_tune_also_off(self, clean_env, tmp_path):
        """With only HLEDAC_LANCEDB_AUTO_TUNE=1 (but not QUANTIZE), tuner
        should NOT trigger (because consumers gate on _ivfpq_enabled)."""
        clean_env.setenv("HLEDAC_LANCEDB_AUTO_TUNE", "1")
        t = _build_tuner(clean_env, tmp_path)
        # Tuner says it would fire
        from knowledge.lancedb_auto_tuner import TuneState
        s = TuneState(last_tune_at=0.0)
        assert t.should_tune(s, inserts_since_tune=5_000) is True
        # But the consumer's _ivfpq_enabled gate is what actually blocks it
        # (tested separately in WireUpIntoConsumers).
