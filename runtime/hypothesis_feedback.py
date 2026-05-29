"""
Sprint F203G: Hypothesis Feedback Loop & Dead-End Pruning

Provides feedback on pivot outcomes so PivotPlanner can penalize
low-yield pivot types and generate fewer dead-end branches.

Bounds:
- MAX_FEEDBACK_RECORDS=10000 — hard cap on stored feedback records
- MAX_PRUNED_TYPES=20 — max pivot types that can be penalized
- No hard ban: repeated zero-yield >= 3 triggers penalty but not hard block
- Feedback is advisory: planner scores are adjusted, not hard-blocked

Schema (persisted in duckdb_store):
  hypothesis_feedback(
    id TEXT PRIMARY KEY,
    target_id TEXT,
    pivot_type TEXT,
    ioc_type TEXT,
    produced_count INTEGER,
    accepted_count INTEGER,
    signal_value DOUBLE,
    ts DOUBLE
  )

Integration:
- duckdb_store: async_record_hypothesis_feedback(), async_get_hypothesis_feedback()
- sprint_scheduler: record_pivot_outcome() writes feedback via duckdb_store
- pivot_planner: receives HypothesisFeedbackSummary and penalizes low-yield types
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

__all__ = [
    "HypothesisFeedbackRecord",
    "HypothesisFeedbackSummary",
    "HypothesisFeedbackAdapter",
    "MAX_FEEDBACK_RECORDS",
    "MAX_PRUNED_TYPES",
]

logger = logging.getLogger(__name__)

# Bounds
MAX_FEEDBACK_RECORDS: int = 10000
MAX_PRUNED_TYPES: int = 20

# Zero-yield penalty threshold: penalize after >= 3 consecutive zero-yield
_ZERO_YIELD_PENALTY_THRESHOLD: int = 3

# Penalty factor applied to expected_value for low-yield types
_PENALTY_FACTOR: float = 0.5


@dataclass(frozen=True, slots=True)
class HypothesisFeedbackRecord:
    """
    A single feedback record for one pivot outcome.

    Fields:
        id: Unique record identifier (UUID)
        target_id: Target this pivot was generated for
        pivot_type: domain/identity/leak/archive/graph
        ioc_type: The IOC type the pivot operated on
        produced_count: How many findings this pivot produced
        accepted_count: How many findings were accepted (stored)
        signal_value: reward signal [0.0, 1.0] — fps-derived
        ts: Unix timestamp of this record
    """
    id: str
    target_id: str
    pivot_type: str
    ioc_type: str
    produced_count: int
    accepted_count: int
    signal_value: float
    ts: float


@dataclass(frozen=True, slots=True)
class HypothesisFeedbackSummary:
    """
    Aggregated feedback summary per (target_id, pivot_type, ioc_type).

    Fields:
        pivot_type: The pivot type
        ioc_type: The IOC type
        total_records: How many records aggregated
        total_produced: Sum of produced_count across records
        total_accepted: Sum of accepted_count across records
        avg_signal: Average signal_value [0.0, 1.0]
        consecutive_zero_yield: How many consecutive records had 0 produced
        penalty_multiplier: Score multiplier [0.0, 1.0] — 1.0 = no penalty
    """
    pivot_type: str
    ioc_type: str
    total_records: int
    total_produced: int
    total_accepted: int
    avg_signal: float
    consecutive_zero_yield: int
    penalty_multiplier: float


class HypothesisFeedbackAdapter:
    """
    F203G: Converts raw HypothesisFeedbackRecords into scoring multipliers
    that PivotPlanner uses to penalize low-yield pivot types.

    Usage:
        adapter = HypothesisFeedbackAdapter(duckdb_store, target_id)
        summary = await adapter.get_summary()  # fetches from duckdb
        # Pass summary to PivotPlanner.plan_pivots(..., feedback_summary=summary)
    """

    def __init__(
        self,
        duckdb_store: object | None = None,
        target_id: str = "default",
    ) -> None:
        """
        Initialize adapter.

        Args:
            duckdb_store: DuckDBShadowStore instance for persistence.
                         If None, adapter operates in in-memory only mode.
            target_id: Target identifier for scoping feedback records.
        """
        self._store = duckdb_store
        self._target_id = target_id
        self._cache: dict[tuple[str, str], HypothesisFeedbackSummary] | None = None
        self._cache_ts: float = 0.0
        self._cache_ttl: float = 300.0  # 5-minute cache

    async def async_record(
        self,
        pivot_type: str,
        ioc_type: str,
        produced_count: int,
        accepted_count: int,
        signal_value: float,
    ) -> bool:
        """
        Record a single pivot outcome to DuckDB.

        Args:
            pivot_type: domain/identity/leak/archive/graph
            ioc_type: The IOC type operated on
            produced_count: Number of findings produced
            accepted_count: Number of findings accepted
            signal_value: reward signal [0.0, 1.0]

        Returns:
            True if recorded successfully, False otherwise.
        """
        import uuid
        if self._store is None:
            return False

        try:
            record = HypothesisFeedbackRecord(
                id=str(uuid.uuid4()),
                target_id=self._target_id,
                pivot_type=pivot_type,
                ioc_type=ioc_type,
                produced_count=produced_count,
                accepted_count=accepted_count,
                signal_value=signal_value,
                ts=time.time(),
            )
            await self._store.async_record_hypothesis_feedback(record)
            self._cache = None  # Invalidate cache
            return True
        except Exception as e:
            logger.debug(f"[F203G] async_record_hypothesis_feedback failed: {e}")
            return False

    async def async_get_summary(
        self,
        pivot_types: list[str] | None = None,
    ) -> dict[tuple[str, str], HypothesisFeedbackSummary]:
        """
        Fetch aggregated feedback summary from DuckDB.

        Args:
            pivot_types: Optional list of pivot types to filter.

        Returns:
            Dict mapping (pivot_type, ioc_type) → HypothesisFeedbackSummary.
            Empty dict if store unavailable or query fails.
        """
        # Return cached if still valid
        now = time.time()
        if self._cache is not None and (now - self._cache_ts) < self._cache_ttl:
            return self._get_filtered_summary(self._cache, pivot_types)

        if self._store is None:
            return {}

        try:
            raw_records: list[HypothesisFeedbackRecord] = (
                await self._store.async_get_hypothesis_feedback(
                    target_id=self._target_id,
                    limit=MAX_FEEDBACK_RECORDS,
                )
            )
            summary = self._aggregate(raw_records)
            self._cache = summary
            self._cache_ts = now
            return self._get_filtered_summary(summary, pivot_types)
        except Exception as e:
            logger.debug(f"[F203G] async_get_hypothesis_feedback failed: {e}")
            return {}

    def _aggregate(
        self,
        records: list[HypothesisFeedbackRecord],
    ) -> dict[tuple[str, str], HypothesisFeedbackSummary]:
        """Aggregate raw records into per-(pivot_type, ioc_type) summaries."""
        buckets: dict[tuple[str, str], list[HypothesisFeedbackRecord]] = {}
        for rec in records:
            key = (rec.pivot_type, rec.ioc_type)
            buckets.setdefault(key, []).append(rec)

        result: dict[tuple[str, str], HypothesisFeedbackSummary] = {}
        for (pivot_type, ioc_type), recs in buckets.items():
            total_produced = sum(r.produced_count for r in recs)
            total_accepted = sum(r.accepted_count for r in recs)
            avg_signal = sum(r.signal_value for r in recs) / len(recs)

            # Count consecutive zero-yield from most recent records
            consecutive_zero = 0
            for r in reversed(recs):
                if r.produced_count == 0:
                    consecutive_zero += 1
                else:
                    break

            # Compute penalty multiplier
            penalty = self._compute_penalty(
                avg_signal=avg_signal,
                consecutive_zero_yield=consecutive_zero,
                _total_records=len(recs),
            )

            result[(pivot_type, ioc_type)] = HypothesisFeedbackSummary(
                pivot_type=pivot_type,
                ioc_type=ioc_type,
                total_records=len(recs),
                total_produced=total_produced,
                total_accepted=total_accepted,
                avg_signal=avg_signal,
                consecutive_zero_yield=consecutive_zero,
                penalty_multiplier=penalty,
            )

        return result

    def _compute_penalty(
        self,
        avg_signal: float,
        consecutive_zero_yield: int,
        _total_records: int,
    ) -> float:
        """
        Compute penalty multiplier [0.0, 1.0] for this pivot type.

        Rules:
        - avg_signal >= 0.3 → no penalty (multiplier=1.0)
        - consecutive_zero_yield >= 3 → apply penalty
        - Penalty scales with consecutive zero-yield count
        - Never returns 0.0 (minimum 0.1)
        """
        if avg_signal >= 0.3:
            return 1.0

        # Penalize based on consecutive zero-yield
        if consecutive_zero_yield >= _ZERO_YIELD_PENALTY_THRESHOLD:
            # Scale penalty: 3 zeros → 0.5, 4 zeros → 0.4, etc.
            penalty = max(0.1, _PENALTY_FACTOR - (consecutive_zero_yield - _ZERO_YIELD_PENALTY_THRESHOLD) * 0.1)
            return penalty

        # Low signal but no consecutive zeros: mild penalty
        if avg_signal < 0.1:
            return 0.7

        return 1.0

    def _get_filtered_summary(
        self,
        summary: dict[tuple[str, str], HypothesisFeedbackSummary],
        pivot_types: list[str] | None,
    ) -> dict[tuple[str, str], HypothesisFeedbackSummary]:
        """Filter summary by pivot_types if provided."""
        if pivot_types is None:
            return summary
        return {
            k: v for k, v in summary.items()
            if k[0] in pivot_types
        }

    def get_penalty_multiplier(
        self,
        pivot_type: str,
        ioc_type: str,
        summaries: dict[tuple[str, str], HypothesisFeedbackSummary],
    ) -> float:
        """
        Get penalty multiplier for a specific pivot_type + ioc_type.

        Returns 1.0 (no penalty) if no feedback exists for this combination.
        """
        key = (pivot_type, ioc_type)
        if key not in summaries:
            return 1.0
        return summaries[key].penalty_multiplier
