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

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Constants — bounded, M1 8GB friendly
# ─────────────────────────────────────────────────────────────────────

#: Default num_partitions (matches existing F264D default).
DEFAULT_NUM_PARTITIONS: int = 64
#: Hard floor for num_partitions. Lower than 8 → no meaningful IVF.
MIN_NUM_PARTITIONS: int = 8
#: Hard ceiling for num_partitions on M1 8GB (256 MB IVF centroids at 256d).
MAX_NUM_PARTITIONS: int = 256

#: Default num_sub_vectors (matches F264D default).
DEFAULT_NUM_SUB_VECTORS: int = 12
#: Hard floor for sub_vectors (4 bytes per codebook × 4 = 16B per vector).
MIN_NUM_SUB_VECTORS: int = 4
#: Hard ceiling for sub_vectors (256d / 4 = 64, 384d / 4 = 96).
MAX_NUM_SUB_VECTORS: int = 64

#: M1 max_iterations override (default LanceDB is 50, M1 saves ~60% time).
M1_MAX_ITERATIONS: int = 20

#: Opt-in env flag for the auto-tuner (independent of F264D's QUANTIZE gate).
AUTO_TUNE_ENV: str = "HLEDAC_LANCEDB_AUTO_TUNE"

#: Insert-count threshold since last tune before re-evaluation fires.
INSERT_THRESHOLD_ENV: str = "HLEDAC_LANCEDB_AUTO_TUNE_THRESHOLD"
DEFAULT_INSERT_THRESHOLD: int = 5_000

#: Cooldown in seconds between consecutive auto-tune attempts.
COOLDOWN_SECONDS_ENV: str = "HLEDAC_LANCEDB_AUTO_TUNE_COOLDOWN_S"
DEFAULT_COOLDOWN_SECONDS: float = 3600.0  # 1 hour

#: Sample size for recall@K measurement (bounded).
DEFAULT_RECALL_SAMPLES: int = 50
MAX_RECALL_SAMPLES: int = 100
#: Top-K for recall measurement.
RECALL_TOP_K: int = 10

#: If recall below this → grow partitions by 50%.
RECALL_TOO_LOW: float = 0.85
#: If recall above this AND search fast → shrink partitions by 25%.
RECALL_EXCELLENT: float = 0.97
#: Search above this ms (with excellent recall) → safe to shrink.
SEARCH_MS_EXCESSIVE: float = 50.0

#: M1 RSS guard — skip tuning above 5.5 GiB to protect sprint memory budget.
M1_RSS_GUARD_BYTES: int = int(5.5 * 1024**3)

#: Hard cap on rows scanned for brute-force baseline (sampling for large tables).
MAX_BRUTE_FORCE_ROWS: int = 10_000


# ─────────────────────────────────────────────────────────────────────
# DTOs — frozen, immutable
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TuneResult:
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

    def changed(self) -> bool:
        """True iff the partition count was actually modified."""
        return self.triggered and self.success and self.new_partitions != self.old_partitions


@dataclass(frozen=True)
class TuneState:
    """Persistent state — JSON-serialized to ``state_path`` for cross-session."""

    last_tune_at: float = 0.0
    last_num_partitions: int = DEFAULT_NUM_PARTITIONS
    last_recall: float = 0.0
    inserts_since_tune: int = 0
    tune_count: int = 0


# ─────────────────────────────────────────────────────────────────────
# Table protocol — minimal contract both consumers satisfy
# ─────────────────────────────────────────────────────────────────────


class _QueryBuilder(Protocol):
    """Structural type for the LanceDB query builder returned by ``Table.search()``.

    Supports method chaining: ``table.search(q).metric("cosine").limit(K).to_list()``.
    """

    def metric(self, name: str) -> _QueryBuilder: ...
    def limit(self, n: int) -> _QueryBuilder: ...
    def to_list(self) -> list[dict[str, Any]]: ...
    def to_pandas(self) -> Any: ...


class _TableLike(Protocol):
    """Structural type for the LanceDB table interface used by the tuner.

    Both ``lancedb.table.Table`` and the test-mock objects satisfy this via
    duck-typing. Only the methods the tuner actually calls are listed.
    """

    def count_rows(self) -> int: ...

    def search(self, query: Any, *, vector_column_name: str = ...) -> _QueryBuilder: ...

    def create_index(
        self,
        *,
        metric: str = ...,
        index_type: str = ...,
        num_partitions: int = ...,
        num_sub_vectors: int = ...,
        replace: bool = ...,
        max_iterations: int = ...,
        **kwargs: Any,
    ) -> Any: ...

    def to_polars(self) -> Any: ...


# ─────────────────────────────────────────────────────────────────────
# IVFPQAutoTuner
# ─────────────────────────────────────────────────────────────────────


class IVFPQAutoTuner:
    """Adaptive IVF-PQ index tuner.

    Lifecycle::

        tuner = IVFPQAutoTuner(table_name="entities", state_path=Path("..."))
        # after each insert batch in the consumer:
        result = tuner.tune_if_due(table, current_partitions=N, inserts_delta=1)
        if result.changed():
            consumer._ivfpq_num_partitions = result.new_partitions
    """

    def __init__(
        self,
        *,
        table_name: str,
        state_path: Path | None = None,
        num_sub_vectors: int = DEFAULT_NUM_SUB_VECTORS,
        vector_column: str = "vector",
        key_column: str = "id",
        insert_threshold: int | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        self._table_name: str = table_name
        self._state_path: Path | None = state_path
        self._num_sub_vectors: int = max(
            MIN_NUM_SUB_VECTORS,
            min(MAX_NUM_SUB_VECTORS, int(num_sub_vectors)),
        )
        self._vector_column: str = vector_column
        self._key_column: str = key_column
        self._insert_threshold: int = (
            int(insert_threshold)
            if insert_threshold is not None
            else int(os.environ.get(INSERT_THRESHOLD_ENV, str(DEFAULT_INSERT_THRESHOLD)))
        )
        self._cooldown_seconds: float = (
            float(cooldown_seconds)
            if cooldown_seconds is not None
            else float(os.environ.get(COOLDOWN_SECONDS_ENV, str(DEFAULT_COOLDOWN_SECONDS)))
        )
        # In-process state cache — synchronized via state file on disk.
        self._state: TuneState = self._load_state()
        # Lock guarding state file writes (single-writer per tuner instance).
        self._state_lock: asyncio.Lock | None = None  # lazy for async safety

    # ── Properties ────────────────────────────────────────────────

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
        return os.environ.get(AUTO_TUNE_ENV, "0") == "1"

    @property
    def state(self) -> TuneState:
        """Current persistent state (read-only snapshot)."""
        return self._state

    @property
    def num_sub_vectors(self) -> int:
        """Configured sub-vector count (immutable for tuner lifetime)."""
        return self._num_sub_vectors

    # ── State persistence (JSON, fail-soft) ───────────────────────

    def _load_state(self) -> TuneState:
        """Load state from JSON. Returns TuneState() on any error (fail-soft)."""
        if self._state_path is None:
            return TuneState()
        try:
            if not self._state_path.exists():
                return TuneState()
            with self._state_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return TuneState(
                last_tune_at=float(data.get("last_tune_at", 0.0)),
                last_num_partitions=int(
                    data.get("last_num_partitions", DEFAULT_NUM_PARTITIONS)
                ),
                last_recall=float(data.get("last_recall", 0.0)),
                inserts_since_tune=int(data.get("inserts_since_tune", 0)),
                tune_count=int(data.get("tune_count", 0)),
            )
        except Exception as e:  # noqa: BLE001 — fail-soft by contract
            logger.debug(f"[LANCEDB-AUTOTUNE] state load failed (defaults): {e}")
            return TuneState()

    def _save_state(self, state: TuneState) -> None:
        """Persist state atomically. Fail-soft — never raises."""
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(asdict(state), f, indent=2, sort_keys=True)
            os.replace(tmp, self._state_path)  # atomic on POSIX
        except Exception as e:  # noqa: BLE001 — fail-soft by contract
            logger.debug(f"[LANCEDB-AUTOTUNE] state save failed: {e}")

    # ── Cooldown / threshold gate ─────────────────────────────────

    def should_tune(
        self,
        state: TuneState,
        inserts_since_tune: int,
        now: float | None = None,
    ) -> bool:
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
            return True  # first-ever tune
        ts_now = now if now is not None else time.time()
        return (ts_now - state.last_tune_at) >= self._cooldown_seconds

    # ── Recall measurement (bounded, sample-based) ────────────────

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
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[LANCEDB-AUTOTUNE] to_polars failed: {e}")
            return _np.empty((0, 0), dtype=_np.float32), []

        try:
            if self._vector_column not in df.columns:
                return _np.empty((0, 0), dtype=_np.float32), []
            total = len(df)
            if total == 0:
                return _np.empty((0, 0), dtype=_np.float32), []

            # Bounded brute-force sample
            if total > MAX_BRUTE_FORCE_ROWS:
                df = df.sample(n=MAX_BRUTE_FORCE_ROWS, seed=42)
            vectors = _np.array(df[self._vector_column].to_list(), dtype=_np.float32)
            if vectors.ndim != 2 or vectors.shape[0] == 0:
                return _np.empty((0, 0), dtype=_np.float32), []

            # L2 normalize for cosine
            norms = _np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8
            vectors_norm = (vectors / norms).astype(_np.float32)

            if self._key_column in df.columns:
                keys = [str(k) for k in df[self._key_column].to_list()]
            else:
                keys = [str(i) for i in range(vectors_norm.shape[0])]
            return vectors_norm, keys
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[LANCEDB-AUTOTUNE] vector extraction failed: {e}")
            return _np.empty((0, 0), dtype=_np.float32), []

    def measure_recall(
        self,
        table: _TableLike,
        *,
        sample_size: int = DEFAULT_RECALL_SAMPLES,
        k: int = RECALL_TOP_K,
    ) -> tuple[float, float]:
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
            return 0.0, 0.0

        k_eff = min(k, n - 1)
        if k_eff < 1:
            return 1.0, 0.0  # trivially perfect when K=0

        rng = _np.random.default_rng(seed=42)
        actual_sample = min(actual_sample, n)
        sample_idx = rng.choice(n, size=actual_sample, replace=False)
        queries = vectors_norm[sample_idx]

        # Brute-force similarity matrix: (sample, total) — bounded by 50 × 10k
        try:
            sims = queries @ vectors_norm.T  # type: ignore[operator]
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[LANCEDB-AUTOTUNE] sims matmul failed: {e}")
            return 0.0, 0.0

        recall_sum = 0.0
        search_times_ms: list[float] = []
        counted = 0
        for s_idx, q_idx in enumerate(sample_idx):
            # Brute top-(K+1) excluding self
            sims_row = sims[s_idx].copy()
            sims_row[int(q_idx)] = -2.0  # exclude self
            top_kp1 = _np.argpartition(-sims_row, k_eff)[:k_eff]
            brute_keys: set[str] = set()
            for bi in top_kp1:
                if int(bi) != int(q_idx):
                    brute_keys.add(keys[int(bi)])
                if len(brute_keys) >= k_eff:
                    break
            if not brute_keys:
                continue

            # ANN top-K via LanceDB
            q_list = queries[s_idx].tolist()
            t0 = time.perf_counter()
            try:
                ann_rows = (
                    table.search(q_list, vector_column_name=self._vector_column)
                    .metric("cosine")
                    .limit(k_eff)
                    .to_list()
                )
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[LANCEDB-AUTOTUNE] ANN search failed: {e}")
                ann_rows = []
            dt_ms = (time.perf_counter() - t0) * 1000.0
            search_times_ms.append(dt_ms)

            ann_keys: set[str] = set()
            for r in ann_rows:
                if self._key_column in r:
                    ann_keys.add(str(r[self._key_column]))
                elif "id" in r:
                    ann_keys.add(str(r["id"]))
                elif "finding_key" in r:
                    ann_keys.add(str(r["finding_key"]))
                if len(ann_keys) >= k_eff:
                    break

            recall_sum += len(ann_keys & brute_keys) / max(1, len(brute_keys))
            counted += 1

        if counted == 0:
            return 0.0, 0.0
        avg_recall = recall_sum / counted
        avg_ms = sum(search_times_ms) / len(search_times_ms) if search_times_ms else 0.0
        return float(avg_recall), float(avg_ms)

    # ── Partition adjustment heuristic ────────────────────────────

    def compute_optimal_partitions(
        self,
        *,
        current: int,
        recall: float,
        avg_search_ms: float,
        row_count: int,
    ) -> int:
        """Decide next ``num_partitions`` based on observed recall and search latency.

        Heuristic (cutting-edge PID-style, bounded):

        - **recall < RECALL_TOO_LOW (0.85)** → grow by 50% (clamp upper).
          IVF-PQ with too few partitions is hitting quantization error.
        - **recall ≥ RECALL_EXCELLENT (0.97) AND avg_search_ms > 50** → shrink
          by 25% (clamp lower). Index is over-partitioned for current data.
        - otherwise → no change. Index is well-tuned.

        Heuristic floor: never grow above 1 partition per ~64 rows (4× of
        default rule-of-thumb). For 50k rows → max 800 partitions. We
        additionally clamp to ``MAX_NUM_PARTITIONS=256`` to keep M1 RSS bounded.
        """
        current = max(MIN_NUM_PARTITIONS, min(MAX_NUM_PARTITIONS, int(current)))
        if row_count <= 0:
            return current

        if recall < RECALL_TOO_LOW:
            new = int(current * 1.5)
        elif recall >= RECALL_EXCELLENT and avg_search_ms > SEARCH_MS_EXCESSIVE:
            new = int(current * 0.75)
        else:
            return current

        # Heuristic ceiling from row count: never grow past ~1 partition per 16
        # rows, but never shrink below current. Soft upper bound — data
        # cardinality sets the practical upper limit; small datasets can't
        # meaningfully use many partitions.
        ceiling = min(MAX_NUM_PARTITIONS, max(current, (row_count // 16) + 1))
        new = max(MIN_NUM_PARTITIONS, min(ceiling, new))
        return new

    # ── Retrain (canonical LanceDB API) ───────────────────────────

    def retrain(
        self,
        table: _TableLike,
        *,
        new_num_partitions: int,
        num_sub_vectors: int | None = None,
    ) -> bool:
        """Re-train IVF-PQ with new ``num_partitions``.

        Uses the canonical ``Table.create_index(..., replace=True)`` API
        (LanceDB 0.4+). ``Table.optimize(retrain=True)`` is DEPRECATED and
        does NOT re-train IVF-PQ centroids — it only compacts files. This
        method is the only correct way to re-train with new params.

        Returns True on success, False on any error (fail-soft).
        """
        n_part = max(
            MIN_NUM_PARTITIONS,
            min(MAX_NUM_PARTITIONS, int(new_num_partitions)),
        )
        n_sub = max(
            MIN_NUM_SUB_VECTORS,
            min(
                MAX_NUM_SUB_VECTORS,
                int(num_sub_vectors) if num_sub_vectors is not None else self._num_sub_vectors,
            ),
        )
        try:
            table.create_index(
                metric="cosine",
                index_type="IVF_PQ",
                num_partitions=n_part,
                num_sub_vectors=n_sub,
                replace=True,
                max_iterations=M1_MAX_ITERATIONS,
            )
            logger.info(
                f"[LANCEDB-AUTOTUNE] retrained table={self._table_name} "
                f"num_partitions={n_part} num_sub_vectors={n_sub} "
                f"max_iterations={M1_MAX_ITERATIONS}"
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[LANCEDB-AUTOTUNE] retrain failed table={self._table_name} "
                f"num_partitions={n_part}: {e}"
            )
            return False

    # ── M1 RSS guard ──────────────────────────────────────────────

    def _rss_under_guard(self) -> bool:
        """True iff process RSS is below M1 8GB safety threshold.

        Fail-soft: if psutil is missing or measurement fails, returns True
        (allow tuning) — the existing per-table row guards still bound work.
        """
        try:
            import psutil

            rss = psutil.Process().memory_info().rss
            return rss < M1_RSS_GUARD_BYTES
        except Exception:  # noqa: BLE001
            return True

    # ── Main entry: synchronous ───────────────────────────────────

    def tune_if_due(
        self,
        table: _TableLike,
        *,
        current_num_partitions: int,
        inserts_delta: int = 1,
    ) -> TuneResult:
        """Decide-and-execute a tune cycle (synchronous core).

        Steps:
          1. Update inserts_since_tune counter (in-memory only).
          2. If not enabled OR cooldown not satisfied → return early
             with ``triggered=False``.
          3. Else: measure recall, compute optimal partitions, retrain if
             changed, persist new state.

        Always returns a ``TuneResult``. Never raises. The caller should
        apply ``result.new_partitions`` to its own state if ``result.changed()``.
        """
        ts0 = time.perf_counter()
        if not self.enabled or table is None:
            return TuneResult(
                success=False,
                triggered=False,
                old_partitions=current_num_partitions,
                new_partitions=current_num_partitions,
                recall=0.0,
                avg_search_ms=0.0,
                rows=0,
                elapsed_ms=0.0,
            )

        new_inserts = self._state.inserts_since_tune + max(0, int(inserts_delta))
        if not self.should_tune(self._state, new_inserts):
            # Persist updated counter even when not tuning.
            self._state = TuneState(
                last_tune_at=self._state.last_tune_at,
                last_num_partitions=self._state.last_num_partitions,
                last_recall=self._state.last_recall,
                inserts_since_tune=new_inserts,
                tune_count=self._state.tune_count,
            )
            self._save_state(self._state)
            return TuneResult(
                success=False,
                triggered=False,
                old_partitions=current_num_partitions,
                new_partitions=current_num_partitions,
                recall=self._state.last_recall,
                avg_search_ms=0.0,
                rows=0,
                elapsed_ms=(time.perf_counter() - ts0) * 1000.0,
            )

        if not self._rss_under_guard():
            logger.debug(
                f"[LANCEDB-AUTOTUNE] skipped: RSS guard table={self._table_name}"
            )
            return TuneResult(
                success=False,
                triggered=False,
                old_partitions=current_num_partitions,
                new_partitions=current_num_partitions,
                recall=0.0,
                avg_search_ms=0.0,
                rows=0,
                elapsed_ms=(time.perf_counter() - ts0) * 1000.0,
                error="rss_guard",
            )

        # Measure row count (table.count_rows is fast)
        try:
            row_count = int(table.count_rows())
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[LANCEDB-AUTOTUNE] count_rows failed: {e}")
            row_count = 0

        if row_count < 256:
            return TuneResult(
                success=False,
                triggered=False,
                old_partitions=current_num_partitions,
                new_partitions=current_num_partitions,
                recall=0.0,
                avg_search_ms=0.0,
                rows=row_count,
                elapsed_ms=(time.perf_counter() - ts0) * 1000.0,
                error="insufficient_rows",
            )

        # Measure recall
        recall, avg_ms = self.measure_recall(table)
        new_partitions = self.compute_optimal_partitions(
            current=current_num_partitions,
            recall=recall,
            avg_search_ms=avg_ms,
            row_count=row_count,
        )

        success = True
        err: str | None = None
        if new_partitions != current_num_partitions:
            success = self.retrain(
                table,
                new_num_partitions=new_partitions,
                num_sub_vectors=self._num_sub_vectors,
            )
            if not success:
                err = "retrain_failed"
        else:
            # Even when partitions unchanged, log the measurement.
            logger.info(
                f"[LANCEDB-AUTOTUNE] measured table={self._table_name} "
                f"recall@K={recall:.3f} avg_ms={avg_ms:.2f} "
                f"partitions={current_num_partitions} rows={row_count} (no change)"
            )

        # Persist new state — even on retrain failure, record the measurement.
        new_state = TuneState(
            last_tune_at=time.time(),
            last_num_partitions=new_partitions if success else self._state.last_num_partitions,
            last_recall=recall,
            inserts_since_tune=0,
            tune_count=self._state.tune_count + 1,
        )
        self._state = new_state
        self._save_state(new_state)

        return TuneResult(
            success=success,
            triggered=True,
            old_partitions=current_num_partitions,
            new_partitions=new_partitions if success else current_num_partitions,
            recall=recall,
            avg_search_ms=avg_ms,
            rows=row_count,
            elapsed_ms=(time.perf_counter() - ts0) * 1000.0,
            error=err,
        )

    # ── Main entry: async wrapper (off-thread) ────────────────────

    async def tune_if_due_async(
        self,
        table: _TableLike,
        *,
        current_num_partitions: int,
        inserts_delta: int = 1,
    ) -> TuneResult:
        """Async-safe wrapper — runs the synchronous ``tune_if_due`` in executor.

        Use this from async code paths (e.g. ``LanceDBIdentityStore.add_entity``).
        Off-loads the blocking ``to_polars``, ``search``, ``create_index`` calls
        to the default executor so the event loop stays responsive.
        """
        if not self.enabled:
            return TuneResult(
                success=False,
                triggered=False,
                old_partitions=current_num_partitions,
                new_partitions=current_num_partitions,
                recall=0.0,
                avg_search_ms=0.0,
                rows=0,
            )
        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.tune_if_due(
                    table,
                    current_num_partitions=current_num_partitions,
                    inserts_delta=inserts_delta,
                ),
            )
            return result
        except RuntimeError:
            # No event loop — fall back to direct sync call.
            return self.tune_if_due(
                table,
                current_num_partitions=current_num_partitions,
                inserts_delta=inserts_delta,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[LANCEDB-AUTOTUNE] async wrapper failed: {e}")
            return TuneResult(
                success=False,
                triggered=False,
                old_partitions=current_num_partitions,
                new_partitions=current_num_partitions,
                recall=0.0,
                avg_search_ms=0.0,
                rows=0,
                error=str(e),
            )


# ─────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────


def make_default_tuner(
    *,
    table_name: str,
    state_dir: Path | None,
    num_sub_vectors: int = DEFAULT_NUM_SUB_VECTORS,
    vector_column: str = "vector",
    key_column: str = "id",
) -> IVFPQAutoTuner:
    """Construct an ``IVFPQAutoTuner`` with default settings.

    State path is ``<state_dir>/lancedb_autotune_<table_name>.json``. Pass
    ``state_dir=None`` to disable persistence (in-memory state only).
    """
    if state_dir is None:
        state_path: Path | None = None
    else:
        state_path = state_dir / f"lancedb_autotune_{table_name}.json"
    return IVFPQAutoTuner(
        table_name=table_name,
        state_path=state_path,
        num_sub_vectors=num_sub_vectors,
        vector_column=vector_column,
        key_column=key_column,
    )


__all__ = [
    "DEFAULT_NUM_PARTITIONS",
    "MIN_NUM_PARTITIONS",
    "MAX_NUM_PARTITIONS",
    "DEFAULT_NUM_SUB_VECTORS",
    "MIN_NUM_SUB_VECTORS",
    "MAX_NUM_SUB_VECTORS",
    "M1_MAX_ITERATIONS",
    "AUTO_TUNE_ENV",
    "INSERT_THRESHOLD_ENV",
    "DEFAULT_INSERT_THRESHOLD",
    "COOLDOWN_SECONDS_ENV",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_RECALL_SAMPLES",
    "MAX_RECALL_SAMPLES",
    "RECALL_TOP_K",
    "RECALL_TOO_LOW",
    "RECALL_EXCELLENT",
    "SEARCH_MS_EXCESSIVE",
    "M1_RSS_GUARD_BYTES",
    "MAX_BRUTE_FORCE_ROWS",
    "TuneResult",
    "TuneState",
    "IVFPQAutoTuner",
    "make_default_tuner",
]
