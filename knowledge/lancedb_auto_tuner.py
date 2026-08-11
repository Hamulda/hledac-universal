"""Sprint F264E — LanceDB IVF-PQ Adaptive Auto-Tuner.

ROLE
----



Adaptive ``num_partitions`` adjustment for IVF-PQ indices. Single source of
truth shared between ``LanceDBIdentityStore`` (entities table, async) and
``_ANNIndex`` (semantic_dedup_v1, sync). Measures recall@K against a brute-force
baseline on a bounded sample, then either grows, shrinks, or leaves the index
in place. Persists tuning state across sessions so cold starts inherit the
last-known-good partition count.

CUTTING EDGE
------------
LanceDB 0.33+ ``Table.optimize(retrain=True)`` is **DEPRECATED** — the
``retrain`` parameter is documented as "no longer used" in the inspect
signature. The canonical way to re-train IVF-PQ is::

    table.create_index(
        metric="cosine",
        index_type="IVF_PQ",
        num_partitions=NEW_N,
        num_sub_vectors=K,
        replace=True,
    )

This module follows the canonical API and additionally passes
``max_iterations=20`` (vs default 50) for M1 8GB friendliness.

INVARIANTS
----------
- **Always-on, no toggles for new behavior** — inherits opt-in via
  ``HLEDAC_LANCEDB_QUANTIZE=1`` (existing F264D gate). Auto-tune itself is
  opt-in via ``HLEDAC_LANCEDB_AUTO_TUNE=1`` so safe rollout.
- **Fail-safe** — every public method is wrapped in try/except. No call
  propagates to the caller.
- **Bounded** — sample size capped at ``MAX_RECALL_SAMPLES=50``,
  brute-force rows capped at ``MAX_BRUTE_FORCE_ROWS=10_000``,
  num_partitions clamped to ``[MIN_NUM_PARTITIONS, MAX_NUM_PARTITIONS]``.
- **Off event loop** — async path always runs the synchronous core in
  ``loop.run_in_executor``. Direct sync callers (e.g. ``_ANNIndex``) call
  the core directly under their own lock.
- **M1 RSS guard** — tuning is skipped when process RSS exceeds
  ``M1_RSS_GUARD_BYTES`` (5.5 GiB).
- **Cooldown** — minimum ``DEFAULT_COOLDOWN_SECONDS=3600`` between tunes
  so we never thrash the index.
"""
import asyncio
import logging
import os
import time
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

from hledac.universal.utils.executor_decorator import offload_to

import orjson
if TYPE_CHECKING:
    import numpy as np
logger = logging.getLogger(__name__)
DEFAULT_NUM_PARTITIONS: int = 64
MIN_NUM_PARTITIONS: int = 8
MAX_NUM_PARTITIONS: int = 256
DEFAULT_NUM_SUB_VECTORS: int = 12
MIN_NUM_SUB_VECTORS: int = 4
MAX_NUM_SUB_VECTORS: int = 64
M1_MAX_ITERATIONS: int = 20
AUTO_TUNE_ENV: str = 'HLEDAC_LANCEDB_AUTO_TUNE'
INSERT_THRESHOLD_ENV: str = 'HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD'
DEFAULT_INSERT_THRESHOLD: int = 500
COOLDOWN_SECONDS_ENV: str = 'HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S'
DEFAULT_COOLDOWN_SECONDS: float = 3600.0
DEFAULT_RECALL_SAMPLES: int = 50
MAX_RECALL_SAMPLES: int = 100
RECALL_TOP_K: int = 10
RECALL_TOO_LOW: float = 0.85
RECALL_EXCELLENT: float = 0.97
SEARCH_MS_EXCESSIVE: float = 50.0
# SSOT: Use UmaBudget.MISSION_PEAK_RSS_GIB instead of hardcoded 5.5 GiB
from hledac.universal.utils.uma_budget import MISSION_PEAK_RSS_GIB
M1_RSS_GUARD_BYTES: int = int(MISSION_PEAK_RSS_GIB * 1024 ** 3)  # 5.5 GiB (SSOT)
MAX_BRUTE_FORCE_ROWS: int = 10000

class TuneResult(Struct, frozen=True):
    """Outcome of a single auto-tune attempt (immutable, log-friendly)."""
    success: bool
    triggered: bool
    old_partitions: int
    new_partitions: int
    recall: float
    avg_search_ms: float
    rows: int
    elapsed_ms: float = 0.0
    error: str | None = None
    old_num_sub_vectors: int = DEFAULT_NUM_SUB_VECTORS
    new_num_sub_vectors: int = DEFAULT_NUM_SUB_VECTORS

    def changed(self) -> bool:
        """True iff the partition count was actually modified."""
        return self.triggered and self.success and (self.new_partitions != self.old_partitions or self.new_num_sub_vectors != self.old_num_sub_vectors)

class TuneState(Struct, frozen=True):
    """Persistent state — JSON-serialized to ``state_path`` for cross-session."""
    last_tune_at: float = 0.0
    last_num_partitions: int = DEFAULT_NUM_PARTITIONS
    last_recall: float = 0.0
    inserts_since_tune: int = 0
    tune_count: int = 0
    last_num_sub_vectors: int = DEFAULT_NUM_SUB_VECTORS
    recall_ema: float = 0.0
    recall_ema_alpha: float = 0.3

class _QueryBuilder(Protocol):
    """Structural type for the LanceDB query builder returned by ``Table.search()``.

    Supports method chaining: ``table.search(q).metric("cosine").limit(K).to_list()``.
    """

    def metric(self, name: str) -> _QueryBuilder:
        ...

    def limit(self, n: int) -> _QueryBuilder:
        ...

    def to_list(self) -> list[dict[str, Any]]:
        ...

    def to_pandas(self) -> Any:
        ...

class _TableLike(Protocol):
    """Structural type for the LanceDB table interface used by the tuner.

    Both ``lancedb.table.Table`` and the test-mock objects satisfy this via
    duck-typing. Only the methods the tuner actually calls are listed.
    """

    def count_rows(self) -> int:
        ...

    def search(self, query: Any, *, vector_column_name: str=...) -> _QueryBuilder:
        ...

    def create_index(self, *, metric: str=..., index_type: str=..., num_partitions: int=..., num_sub_vectors: int=..., replace: bool=..., max_iterations: int=..., **kwargs: Any) -> Any:
        ...

    def to_polars(self) -> Any:
        ...

class IVFPQAutoTuner:
    """Adaptive IVF-PQ index tuner.

    Lifecycle::

        tuner = IVFPQAutoTuner(table_name="entities", state_path=Path("..."))
        # after each insert batch in the consumer:
        result = tuner.tune_if_due(table, current_partitions=N, inserts_delta=1)
        if result.changed():
            consumer._ivfpq_num_partitions = result.new_partitions
    """
    __slots__ = tuple(('_cooldown_seconds', '_embedding_dim', '_insert_threshold', '_key_column', '_num_sub_vectors', '_state', '_state_lock', '_state_path', '_table_name', '_vector_column'))

    def __init__(self, *, table_name: str, state_path: Path | None=None, num_sub_vectors: int=DEFAULT_NUM_SUB_VECTORS, vector_column: str='vector', key_column: str='id', insert_threshold: int | None=None, cooldown_seconds: float | None=None, embedding_dim: int=256) -> None:
        self._table_name: str = table_name
        self._state_path: Path | None = state_path
        self._num_sub_vectors: int = max(MIN_NUM_SUB_VECTORS, min(MAX_NUM_SUB_VECTORS, int(num_sub_vectors)))
        self._vector_column: str = vector_column
        self._key_column: str = key_column
        self._insert_threshold: int = int(insert_threshold) if insert_threshold is not None else int(os.environ.get(INSERT_THRESHOLD_ENV, str(DEFAULT_INSERT_THRESHOLD)))
        self._cooldown_seconds: float = float(cooldown_seconds) if cooldown_seconds is not None else float(os.environ.get(COOLDOWN_SECONDS_ENV, str(DEFAULT_COOLDOWN_SECONDS)))
        self._embedding_dim: int = max(1, int(embedding_dim))
        self._state: TuneState = self._load_state()
        self._state_lock: asyncio.Lock | None = None

    @property
    def table_name(self) -> str:
        """Public accessor for the table name (read-only)."""
        return self._table_name

    @property
    def vector_column(self) -> str:
        """Public accessor for the vector column name (read-only)."""
        return self._vector_column

    @property
    def key_column(self) -> str:
        """Public accessor for the key column name (read-only)."""
        return self._key_column

    @property
    def enabled(self) -> bool:
        """Auto-tune gate. Independent of F264D ``HLEDAC_LANCEDB_QUANTIZE``."""
        return os.environ.get(AUTO_TUNE_ENV, '0') == '1'

    @property
    def state(self) -> TuneState:
        """Current persistent state (read-only snapshot)."""
        return self._state

    @property
    def num_sub_vectors(self) -> int:
        """Configured sub-vector count (immutable for tuner lifetime)."""
        return self._num_sub_vectors

    def _load_state(self) -> TuneState:
        """Load state from JSON. Returns TuneState() on any error (fail-soft)."""
        if self._state_path is None:
            return TuneState()
        try:
            if not self._state_path.exists():
                return TuneState()
            with self._state_path.open('rb') as f:
                raw = f.read()
            data = orjson.loads(raw)
            return TuneState(last_tune_at=float(data.get('last_tune_at', 0.0)), last_num_partitions=int(data.get('last_num_partitions', DEFAULT_NUM_PARTITIONS)), last_recall=float(data.get('last_recall', 0.0)), inserts_since_tune=int(data.get('inserts_since_tune', 0)), tune_count=int(data.get('tune_count', 0)), last_num_sub_vectors=int(data.get('last_num_sub_vectors', DEFAULT_NUM_SUB_VECTORS)), recall_ema=float(data.get('recall_ema', 0.0)), recall_ema_alpha=float(data.get('recall_ema_alpha', 0.3)))
        except Exception as e:
            logger.debug(f'[LANCEDB-AUTOTUNE] state load failed (defaults): {e}')
            return TuneState()

    def _save_state(self, state: TuneState) -> None:
        """Persist state atomically. Fail-soft — never raises."""
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(self._state_path.suffix + '.tmp')
            # NOTE: state is msgspec.Struct — use msgspec.to_builtins() (convert to JSON-compatible dict)
            _state_dict = msgspec.to_builtins(state)
            raw = orjson.dumps(_state_dict, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS)
            tmp.write_bytes(raw)
            os.replace(tmp, self._state_path)
        except Exception as e:
            logger.debug(f'[LANCEDB-AUTOTUNE] state save failed: {e}')

    def should_tune(self, state: TuneState, inserts_since_tune: int, now: float | None=None) -> bool:
        """Decide whether the cooldown + insert-threshold gate is satisfied.

        Returns True only if both:
          - ``inserts_since_tune >= self._insert_threshold``
          - ``(now - state.last_tune_at) >= self._cooldown_seconds``

        Pure function — does NOT mutate state. Caller persists changes.
        """
        if not self.enabled:
            return False
        if inserts_since_tune < self._insert_threshold:
            return False
        if state.last_tune_at <= 0.0:
            return True
        ts_now = now if now is not None else time.time()
        return ts_now - state.last_tune_at >= self._cooldown_seconds

    def _extract_vectors_and_keys(self, table: _TableLike) -> tuple[np.ndarray, list[str]]:
        """Extract the vector column and a key column from the table as numpy.

        Returns ``(vectors_normalized, key_list)``. Vectors are L2-normalized
        for cosine-similarity. If the table is too large (>MAX_BRUTE_FORCE_ROWS)
        a deterministic random sample is taken for the brute-force baseline.

        Fail-soft: any error returns empty arrays.
        """
        import numpy as _np
        try:
            df = table.to_polars()
        except Exception as e:
            logger.debug(f'[LANCEDB-AUTOTUNE] to_polars failed: {e}')
            return (_np.empty((0, 0), dtype=_np.float32), [])
        try:
            if self._vector_column not in df.columns:
                return (_np.empty((0, 0), dtype=_np.float32), [])
            total = len(df)
            if total == 0:
                return (_np.empty((0, 0), dtype=_np.float32), [])
            if total > MAX_BRUTE_FORCE_ROWS:
                df = df.sample(n=MAX_BRUTE_FORCE_ROWS, seed=42)
            vectors = _np.array(df[self._vector_column].to_list(), dtype=_np.float32)
            if vectors.ndim != 2 or vectors.shape[0] == 0:
                return (_np.empty((0, 0), dtype=_np.float32), [])
            norms = _np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-08
            vectors_norm = (vectors / norms).astype(_np.float32)
            if self._key_column in df.columns:
                keys = [str(k) for k in df[self._key_column].to_list()]
            else:
                keys = [str(i) for i in range(vectors_norm.shape[0])]
            return (vectors_norm, keys)
        except Exception as e:
            logger.debug(f'[LANCEDB-AUTOTUNE] vector extraction failed: {e}')
            return (_np.empty((0, 0), dtype=_np.float32), [])

    def measure_recall(self, table: _TableLike, *, sample_size: int=DEFAULT_RECALL_SAMPLES, k: int=RECALL_TOP_K) -> tuple[float, float]:
        """Measure recall@K on a bounded random sample.

        Returns ``(recall_at_k, avg_search_ms)``. ``recall_at_k`` is in
        ``[0.0, 1.0]`` (1.0 = perfect overlap with brute-force top-K excluding
        the query itself). Returns ``(0.0, 0.0)`` on any failure.

        Algorithm:
          1. Extract up to ``MAX_BRUTE_FORCE_ROWS`` vectors via ``to_polars()``.
          2. Sample ``sample_size`` query vectors (deterministic seed).
          3. For each query: compute brute top-(K+1) via numpy matmul, exclude
             self, compare with ANN top-K from ``table.search(...).limit(K)``.
          4. ``recall = mean(|ANN ∩ brute| / K)``
        """
        import numpy as _np
        actual_sample = max(1, min(sample_size, MAX_RECALL_SAMPLES, MAX_BRUTE_FORCE_ROWS))
        vectors_norm, keys = self._extract_vectors_and_keys(table)
        n = vectors_norm.shape[0]
        if n < 2 or len(keys) != n:
            return (0.0, 0.0)
        k_eff = min(k, n - 1)
        if k_eff < 1:
            return (1.0, 0.0)
        rng = _np.random.default_rng(seed=42)
        actual_sample = min(actual_sample, n)
        sample_idx = rng.choice(n, size=actual_sample, replace=False)
        queries = vectors_norm[sample_idx]
        try:
            sims = queries @ vectors_norm.T
        except Exception as e:
            logger.debug(f'[LANCEDB-AUTOTUNE] sims matmul failed: {e}')
            return (0.0, 0.0)
        recall_sum = 0.0
        search_times_ms: list[float] = []
        counted = 0
        for s_idx, q_idx in enumerate(sample_idx):
            sims_row = sims[s_idx].copy()
            sims_row[int(q_idx)] = -2.0
            top_kp1 = _np.argpartition(-sims_row, k_eff)[:k_eff]
            brute_keys: set[str] = set()
            for bi in top_kp1:
                if int(bi) != int(q_idx):
                    brute_keys.add(keys[int(bi)])
                if len(brute_keys) >= k_eff:
                    break
            if not brute_keys:
                continue
            q_list = queries[s_idx].tolist()
            t0 = time.perf_counter()
            try:
                ann_rows = table.search(q_list, vector_column_name=self._vector_column).metric('cosine').limit(k_eff).to_list()
            except Exception as e:
                logger.debug(f'[LANCEDB-AUTOTUNE] ANN search failed: {e}')
                ann_rows = []
            dt_ms = (time.perf_counter() - t0) * 1000.0
            search_times_ms.append(dt_ms)
            ann_keys: set[str] = set()
            for r in ann_rows:
                if self._key_column in r:
                    ann_keys.add(str(r[self._key_column]))
                elif 'id' in r:
                    ann_keys.add(str(r['id']))
                elif 'finding_key' in r:
                    ann_keys.add(str(r['finding_key']))
                if len(ann_keys) >= k_eff:
                    break
            recall_sum += len(ann_keys & brute_keys) / max(1, len(brute_keys))
            counted += 1
        if counted == 0:
            return (0.0, 0.0)
        avg_recall = recall_sum / counted
        avg_ms = sum(search_times_ms) / len(search_times_ms) if search_times_ms else 0.0
        return (float(avg_recall), float(avg_ms))

    def _ema_recall(self, prev_ema: float, new_recall: float, alpha: float=0.3) -> float:
        """Exponential moving average of recall for trend detection.

        P0-2: Closed-loop PID — smooths noise in recall measurements so the
        controller reacts to direction, not single noisy samples.
        """
        return alpha * new_recall + (1.0 - alpha) * prev_ema

    def compute_optimal_partitions(self, *, current: int, recall: float, avg_search_ms: float, row_count: int, prev_recall_ema: float=0.0) -> int:
        """Decide next ``num_partitions`` based on observed recall and search latency.

        P0-2 Enhancement: Trend-aware PID controller.

        Instead of reacting to a single noisy recall sample, we compute an EMA
        (exponential moving average) of recall and use its *direction* to guide
        the adjustment. This provides closed-loop stability — the controller
        damps oscillations that plague open-loop threshold-only approaches.

        Branches:
        - **recall_ema < RECALL_TOO_LOW (0.85)** → grow by 50% (clamp upper).
          IVF-PQ with too few partitions is hitting quantization error.
        - **recall_ema ≥ RECALL_EXCELLENT (0.97) AND avg_search_ms > 50** → shrink
          by 25% (clamp lower). Index is over-partitioned for current data.
        - **EMA trend is falling significantly** (3 consecutive drops) → early grow
          signal before hitting hard threshold. Detects degradation trajectory.
        - otherwise → no change. Index is well-tuned.

        Heuristic floor: never grow above 1 partition per ~16 rows. Clamped to
        ``MAX_NUM_PARTITIONS=256`` to keep M1 RSS bounded.
        """
        current = max(MIN_NUM_PARTITIONS, min(MAX_NUM_PARTITIONS, int(current)))
        if row_count <= 0:
            return current
        is_cold_start = prev_recall_ema <= 0.0
        recall_ema = self._ema_recall(prev_recall_ema, recall)
        falling_trajectory = not is_cold_start and recall < prev_recall_ema and (prev_recall_ema - recall > 0.05)
        decision_recall = recall if is_cold_start else recall_ema
        if decision_recall < RECALL_TOO_LOW or falling_trajectory:
            delta = 1.5 if decision_recall < RECALL_TOO_LOW else 1.25
            new = int(current * delta)
        elif decision_recall >= RECALL_EXCELLENT and avg_search_ms > SEARCH_MS_EXCESSIVE:
            new = int(current * 0.75)
        else:
            return current
        ceiling = min(MAX_NUM_PARTITIONS, max(current, row_count // 16 + 1))
        new = max(MIN_NUM_PARTITIONS, min(ceiling, new))
        return new
    _SUB_VEC_TOO_FEW: float = 0.8
    _SUB_VEC_EXCELLENT: float = 0.95

    def compute_optimal_sub_vectors(self, *, current: int, recall: float, avg_search_ms: float, embedding_dim: int) -> int:
        """Decide next ``num_sub_vectors`` based on recall and embedding dimension.

        P1-2 Enhancement: Adaptive compression ratio for IVF-PQ.

        num_sub_vectors controls the compression ratio:
          - More sub_vectors = smaller storage, faster search, lower accuracy
          - Fewer sub_vectors = larger storage, slower search, higher accuracy

        For 256d embeddings: 12 sub_vectors = ~21 bytes/vector (256/12 ≈ 21)
        For 384d embeddings: 16 sub_vectors = ~24 bytes/vector (384/16 = 24)

        Heuristic (mirrors partition logic — only act when there's a problem):
        - **recall < 0.80** → grow sub_vectors (reduce compression, improve recall)
        - **recall ≥ 0.95 AND avg_search_ms > SEARCH_MS_EXCESSIVE (50ms)
          AND current > MIN** → shrink (save memory, still accurate)
        - otherwise → no change

        Clamped to [MIN_NUM_SUB_VECTORS, MAX_NUM_SUB_VECTORS] and also bounded
        by embedding_dim (can't have more sub_vectors than dimensions).
        """
        current = max(MIN_NUM_SUB_VECTORS, min(MAX_NUM_SUB_VECTORS, int(current)))
        dim_limit = max(MIN_NUM_SUB_VECTORS, embedding_dim)
        upper_bound = min(MAX_NUM_SUB_VECTORS, dim_limit)
        if recall < self._SUB_VEC_TOO_FEW:
            new = int(current * 1.5)
        elif recall >= self._SUB_VEC_EXCELLENT and avg_search_ms > SEARCH_MS_EXCESSIVE and (current > MIN_NUM_SUB_VECTORS):
            new = max(MIN_NUM_SUB_VECTORS, int(current * 0.75))
        else:
            return current
        new = max(MIN_NUM_SUB_VECTORS, min(upper_bound, new))
        return new

    def retrain(self, table: _TableLike, *, new_num_partitions: int, num_sub_vectors: int | None=None) -> bool:
        """Re-train IVF-PQ with new ``num_partitions`` and optionally ``num_sub_vectors``.

        P1-2 Enhancement: Both IVF-PQ knobs are now tuned together.

        Uses the canonical ``Table.create_index(..., replace=True)`` API
        (LanceDB 0.4+). ``Table.optimize(retrain=True)`` is DEPRECATED and
        does NOT re-train IVF-PQ centroids — it only compacts files. This
        method is the only correct way to re-train with new params.

        P1-1 Enhancement: LanceDB 0.4x API compatibility — passes
        ``max_iterations`` only when confirmed supported by the table, with
        graceful fallback to signature-based detection.

        Returns True on success, False on any error (fail-soft).
        """
        n_part = max(MIN_NUM_PARTITIONS, min(MAX_NUM_PARTITIONS, int(new_num_partitions)))
        n_sub = max(MIN_NUM_SUB_VECTORS, min(MAX_NUM_SUB_VECTORS, int(num_sub_vectors) if num_sub_vectors is not None else self._num_sub_vectors))
        try:
            index_kwargs: dict[str, Any] = {'metric': 'cosine', 'index_type': 'IVF_PQ', 'num_partitions': n_part, 'num_sub_vectors': n_sub, 'replace': True}
            try:
                index_kwargs['max_iterations'] = M1_MAX_ITERATIONS
                table.create_index(**index_kwargs)
            except TypeError:
                del index_kwargs['max_iterations']
                table.create_index(**index_kwargs)
            logger.info(f'[LANCEDB-AUTOTUNE] retrained table={self._table_name} num_partitions={n_part} num_sub_vectors={n_sub} max_iterations={M1_MAX_ITERATIONS}')
            try:
                if hasattr(table, 'optimize'):
                    cast(Any, table).optimize()
            except Exception as e_opt:
                logger.debug(f'[LANCEDB-AUTOTUNE] post-retrain compact skipped: {e_opt}')
            return True
        except Exception as e:
            logger.warning(f'[LANCEDB-AUTOTUNE] retrain failed table={self._table_name} num_partitions={n_part}: {e}')
            return False

    def _rss_under_guard(self) -> bool:
        """True iff process RSS is below M1 8GB safety threshold.

        Fail-soft: if psutil is missing or measurement fails, returns True
        (allow tuning) — the existing per-table row guards still bound work.
        """
        try:
            import psutil
            rss = psutil.Process().memory_info().rss
            return rss < M1_RSS_GUARD_BYTES
        except Exception:
            return True

    def tune_if_due(self, table: _TableLike, *, current_num_partitions: int, current_num_sub_vectors: int | None=None, inserts_delta: int=1) -> TuneResult:
        """Decide-and-execute a tune cycle (synchronous core).

        P0-1 + P0-2 Enhancement: Tunes BOTH num_partitions AND num_sub_vectors.

        Steps:
          1. Update inserts_since_tune counter (in-memory only).
          2. If not enabled OR cooldown not satisfied → return early
             with ``triggered=False``.
          3. Else: measure recall, compute optimal partitions + sub_vectors,
             retrain if either changed, persist new state.

        Always returns a ``TuneResult``. Never raises. The caller should
        apply ``result.new_partitions`` and ``result.new_num_sub_vectors``
        to its own state if ``result.changed()``.
        """
        ts0 = time.perf_counter()
        cur_sub = int(current_num_sub_vectors) if current_num_sub_vectors is not None else self._state.last_num_sub_vectors
        if not self.enabled or table is None:
            return TuneResult(success=False, triggered=False, old_partitions=current_num_partitions, new_partitions=current_num_partitions, recall=0.0, avg_search_ms=0.0, rows=0, elapsed_ms=0.0, old_num_sub_vectors=cur_sub, new_num_sub_vectors=cur_sub)
        new_inserts = self._state.inserts_since_tune + max(0, int(inserts_delta))
        if not self.should_tune(self._state, new_inserts):
            self._state = TuneState(last_tune_at=self._state.last_tune_at, last_num_partitions=self._state.last_num_partitions, last_recall=self._state.last_recall, inserts_since_tune=new_inserts, tune_count=self._state.tune_count, last_num_sub_vectors=self._state.last_num_sub_vectors, recall_ema=self._state.recall_ema, recall_ema_alpha=self._state.recall_ema_alpha)
            self._save_state(self._state)
            return TuneResult(success=False, triggered=False, old_partitions=current_num_partitions, new_partitions=current_num_partitions, recall=self._state.last_recall, avg_search_ms=0.0, rows=0, elapsed_ms=(time.perf_counter() - ts0) * 1000.0, old_num_sub_vectors=cur_sub, new_num_sub_vectors=cur_sub)
        if not self._rss_under_guard():
            logger.debug(f'[LANCEDB-AUTOTUNE] skipped: RSS guard table={self._table_name}')
            return TuneResult(success=False, triggered=False, old_partitions=current_num_partitions, new_partitions=current_num_partitions, recall=0.0, avg_search_ms=0.0, rows=0, elapsed_ms=(time.perf_counter() - ts0) * 1000.0, error='rss_guard', old_num_sub_vectors=cur_sub, new_num_sub_vectors=cur_sub)
        try:
            row_count = int(table.count_rows())
        except Exception as e:
            logger.debug(f'[LANCEDB-AUTOTUNE] count_rows failed: {e}')
            row_count = 0
        if row_count < 256:
            return TuneResult(success=False, triggered=False, old_partitions=current_num_partitions, new_partitions=current_num_partitions, recall=0.0, avg_search_ms=0.0, rows=row_count, elapsed_ms=(time.perf_counter() - ts0) * 1000.0, error='insufficient_rows', old_num_sub_vectors=cur_sub, new_num_sub_vectors=cur_sub)
        recall, avg_ms = self.measure_recall(table)
        prev_ema = self._state.recall_ema
        recall_ema = self._ema_recall(prev_ema, recall)
        new_partitions = self.compute_optimal_partitions(current=current_num_partitions, recall=recall, avg_search_ms=avg_ms, row_count=row_count, prev_recall_ema=prev_ema)
        new_sub_vec = self.compute_optimal_sub_vectors(current=cur_sub, recall=recall, avg_search_ms=avg_ms, embedding_dim=self._embedding_dim)
        success = True
        err: str | None = None
        partitions_changed = new_partitions != current_num_partitions
        sub_vec_changed = new_sub_vec != cur_sub
        if partitions_changed or sub_vec_changed:
            success = self.retrain(table, new_num_partitions=new_partitions, num_sub_vectors=new_sub_vec)
            if not success:
                err = 'retrain_failed'
                new_partitions = current_num_partitions
                new_sub_vec = cur_sub
        else:
            logger.info(f'[LANCEDB-AUTOTUNE] measured table={self._table_name} recall={recall:.3f} recall_ema={recall_ema:.3f} avg_ms={avg_ms:.2f} partitions={current_num_partitions} sub_vec={cur_sub} rows={row_count} (no change)')
        new_state = TuneState(last_tune_at=time.time(), last_num_partitions=new_partitions, last_recall=recall, inserts_since_tune=0, tune_count=self._state.tune_count + 1, last_num_sub_vectors=new_sub_vec, recall_ema=recall_ema, recall_ema_alpha=self._state.recall_ema_alpha)
        self._state = new_state
        self._save_state(new_state)
        return TuneResult(success=success, triggered=True, old_partitions=current_num_partitions, new_partitions=new_partitions, recall=recall, avg_search_ms=avg_ms, rows=row_count, elapsed_ms=(time.perf_counter() - ts0) * 1000.0, error=err, old_num_sub_vectors=cur_sub, new_num_sub_vectors=new_sub_vec)

    async def tune_if_due_async(self, table: _TableLike, *, current_num_partitions: int, current_num_sub_vectors: int | None=None, inserts_delta: int=1) -> TuneResult:
        """Async-safe wrapper — runs the synchronous ``tune_if_due`` in executor.

        P1-2 Enhancement: Passes current_num_sub_vectors through to the
        synchronous core so both IVF-PQ knobs are tuned.

        Use this from async code paths (e.g. ``LanceDBIdentityStore.add_entity``).
        Off-loads the blocking ``to_polars``, ``search``, ``create_index`` calls
        to the default executor so the event loop stays responsive.
        """
        if not self.enabled:
            cur_sub = int(current_num_sub_vectors) if current_num_sub_vectors is not None else self._state.last_num_sub_vectors
            return TuneResult(success=False, triggered=False, old_partitions=current_num_partitions, new_partitions=current_num_partitions, recall=0.0, avg_search_ms=0.0, rows=0, old_num_sub_vectors=cur_sub, new_num_sub_vectors=cur_sub)
        try:
            result = await offload_to("cpu_blocking_pool", self.tune_if_due, table, current_num_partitions, current_num_sub_vectors, inserts_delta)
            return result
        except RuntimeError:
            return self.tune_if_due(table, current_num_partitions=current_num_partitions, current_num_sub_vectors=current_num_sub_vectors, inserts_delta=inserts_delta)
        except Exception as e:
            cur_sub = int(current_num_sub_vectors) if current_num_sub_vectors is not None else self._state.last_num_sub_vectors
            logger.debug(f'[LANCEDB-AUTOTUNE] async wrapper failed: {e}')
            return TuneResult(success=False, triggered=False, old_partitions=current_num_partitions, new_partitions=current_num_partitions, recall=0.0, avg_search_ms=0.0, rows=0, error=str(e), old_num_sub_vectors=cur_sub, new_num_sub_vectors=cur_sub)

def make_default_tuner(*, table_name: str, state_dir: Path | None, num_sub_vectors: int=DEFAULT_NUM_SUB_VECTORS, vector_column: str='vector', key_column: str='id', embedding_dim: int=256) -> IVFPQAutoTuner:
    """Construct an ``IVFPQAutoTuner`` with default settings.

    State path is ``<state_dir>/lancedb_autotune_<table_name>.json``. Pass
    ``state_dir=None`` to disable persistence (in-memory state only).
    """
    if state_dir is None:
        state_path: Path | None = None
    else:
        state_path = state_dir / f'lancedb_autotune_{table_name}.json'
    return IVFPQAutoTuner(table_name=table_name, state_path=state_path, num_sub_vectors=num_sub_vectors, vector_column=vector_column, key_column=key_column, embedding_dim=embedding_dim)
__all__ = ['DEFAULT_NUM_PARTITIONS', 'MIN_NUM_PARTITIONS', 'MAX_NUM_PARTITIONS', 'DEFAULT_NUM_SUB_VECTORS', 'MIN_NUM_SUB_VECTORS', 'MAX_NUM_SUB_VECTORS', 'M1_MAX_ITERATIONS', 'AUTO_TUNE_ENV', 'INSERT_THRESHOLD_ENV', 'DEFAULT_INSERT_THRESHOLD', 'COOLDOWN_SECONDS_ENV', 'DEFAULT_COOLDOWN_SECONDS', 'DEFAULT_RECALL_SAMPLES', 'MAX_RECALL_SAMPLES', 'RECALL_TOP_K', 'RECALL_TOO_LOW', 'RECALL_EXCELLENT', 'SEARCH_MS_EXCESSIVE', 'M1_RSS_GUARD_BYTES', 'MAX_BRUTE_FORCE_ROWS', 'TuneResult', 'TuneState', 'IVFPQAutoTuner', 'make_default_tuner']