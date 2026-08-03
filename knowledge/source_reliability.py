"""
SourceReliabilityTracker — META-008 cross-sprint source reliability scoring.

Tracks source_contradiction_count / source_total_claims ratio per source
across sprints. Sources with ratio > AUTO_RETRACT_RATIO are flagged for
auto-retraction at SYNTHESIS phase.

ARCHITECTURE:
  - In-memory primary path: O(1) updates, O(k) queries (k = tracked sources)
  - Optional DuckDB persistence: follows domain_reputation.py pattern
  - Fail-soft: all errors return neutral data, never raise

AUTO-RETRACTION THRESHOLD (M1 8GB safe):
  - AUTO_RETRACT_RATIO = 0.3  — auto-retract when >30% of claims are contradictory
  - AUTO_RETRACT_MIN_CLAIMS = 3  — minimum claims before ratio is meaningful
  - MAX_TRACKED_SOURCES = 256  — LRU eviction, ~2KB memory footprint

FEATURE FLAG:
  HLEDAC_ENABLE_SOURCE_RELIABILITY=1 (default ON, opt-out via 0)

USAGE:
  from hledac.universal.knowledge.source_reliability import (
      get_source_reliability_tracker, SourceReliabilityTracker
  )

  tracker = get_source_reliability_tracker()
  tracker.record_claim("source_a", contradictory=False)
  tracker.record_claim("source_a", contradictory=True)

  if tracker.should_auto_retract("source_a"):
      await ioc_graph.retract_source("source_a")

META-008: This closes the gap between contradiction detection (META-007)
and JTMS source retraction — when a source is a systematic dissenter,
it is automatically retracted rather than left active.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Feature flag & bounds (M1 8GB safe)
# ---------------------------------------------------------------------------

_SOURCE_RELIABILITY_ENABLED: bool = (
    os.environ.get("HLEDAC_ENABLE_SOURCE_RELIABILITY", "1").lower()
    in ("1", "true", "yes", "on")
)

AUTO_RETRACT_RATIO: float = 0.3
AUTO_RETRACT_MIN_CLAIMS: int = 3
MAX_TRACKED_SOURCES: int = 256
MAX_CLAIMS_PER_SOURCE: int = 1000  # cap to prevent overflow
DUCKDB_PERSISTENCE_ENABLED: bool = _SOURCE_RELIABILITY_ENABLED

# DuckDB table name (if persistence enabled)
_SOURCE_RELIABILITY_TABLE = "source_reliability"

# CREATE TABLE SQL (idempotent)
_SOURCE_RELIABILITY_DDL = f"""
CREATE TABLE IF NOT EXISTS {_SOURCE_RELIABILITY_TABLE} (
    source_id        TEXT PRIMARY KEY,
    total_claims     INTEGER NOT NULL DEFAULT 0,
    contradiction_count INTEGER NOT NULL DEFAULT 0,
    ratio            REAL NOT NULL DEFAULT 0.0,
    last_updated     DOUBLE NOT NULL,
    auto_retracted   BOOLEAN NOT NULL DEFAULT FALSE,
    auto_retracted_at DOUBLE,
    sprint_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_source_reliability_ratio
    ON {_SOURCE_RELIABILITY_TABLE}(ratio DESC);
CREATE INDEX IF NOT EXISTS idx_source_reliability_updated
    ON {_SOURCE_RELIABILITY_TABLE}(last_updated DESC);
"""


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class SourceReliability:
    """Immutable snapshot of a source's reliability across sprints."""
    source_id: str
    total_claims: int = 0
    contradiction_count: int = 0
    ratio: float = 0.0
    last_updated: float = 0.0
    auto_retracted: bool = False
    auto_retracted_at: float = 0.0

    @property
    def is_reliable(self) -> bool:
        """True if source is below auto-retract threshold."""
        if self.total_claims < AUTO_RETRACT_MIN_CLAIMS:
            return True  # not enough data to judge
        return self.ratio <= AUTO_RETRACT_RATIO

    @property
    def should_auto_retract(self) -> bool:
        """True if source meets auto-retraction criteria."""
        return not self.is_reliable and not self.auto_retracted


@dataclass(slots=True)
class SourceStats:
    """Mutable per-source statistics (in-memory hot path)."""
    total_claims: int = 0
    contradiction_count: int = 0
    last_updated: float = 0.0
    auto_retracted: bool = False
    auto_retracted_at: float = 0.0


# ---------------------------------------------------------------------------
# SourceReliabilityTracker
# ---------------------------------------------------------------------------

class SourceReliabilityTracker:
    """Tracks source contradiction ratios across sprints.

    Primary path: in-memory dict with LRU eviction (M1 8GB safe).
    Optional DuckDB persistence for cross-sprint durability.

    Thread safety: asyncio.Lock for all mutations.
    Fail-soft: all errors return neutral data.
    """

    __slots__ = (
        "_enabled",
        "_lock",
        "_sources",
        "_store",
        "_duckdb_initialized",
        "_stats",
        "_last_lru_eviction",
    )

    def __init__(
        self,
        store: DuckDBShadowStore | None = None,
    ) -> None:
        """Initialize SourceReliabilityTracker.

        Args:
            store: Optional DuckDBShadowStore for cross-sprint persistence.
                   None = in-memory only (per-sprint).
        """
        self._enabled: bool = _SOURCE_RELIABILITY_ENABLED
        self._lock: asyncio.Lock = asyncio.Lock()
        self._sources: dict[str, SourceStats] = {}
        self._store: DuckDBShadowStore | None = store
        self._duckdb_initialized: bool = False
        self._stats: dict[str, int] = {
            "claims_recorded": 0,
            "contradictions_recorded": 0,
            "auto_retractions": 0,
            "lru_evictions": 0,
            "db_writes": 0,
            "db_reads": 0,
        }
        self._last_lru_eviction: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def record_claim(
        self,
        source_id: str,
        *,
        contradictory: bool = False,
        sprint_id: str = "",
    ) -> None:
        """Record a claim from a source (with optional contradiction flag).

        Args:
            source_id: Source identifier (e.g., 'source_a', 'ct_logs_v1').
            contradictory: True if this claim was flagged as contradiction.
            sprint_id: Optional sprint ID for audit trail.
        """
        if not self._enabled or not source_id:
            return

        try:
            async with self._lock:
                self._stats["claims_recorded"] += 1
                if contradictory:
                    self._stats["contradictions_recorded"] += 1

                # Get or create stats
                stats = self._sources.get(source_id)
                if stats is None:
                    # LRU eviction if at capacity
                    if len(self._sources) >= MAX_TRACKED_SOURCES:
                        self._evict_lru()
                    stats = SourceStats()
                    self._sources[source_id] = stats

                # Cap claims to prevent overflow
                if stats.total_claims < MAX_CLAIMS_PER_SOURCE:
                    stats.total_claims += 1
                    if contradictory:
                        stats.contradiction_count += 1
                else:
                    # Slow decay: halve counts to keep recent data weight higher
                    stats.total_claims = stats.total_claims // 2 + 1
                    stats.contradiction_count = stats.contradiction_count // 2 + (
                        1 if contradictory else 0
                    )

                stats.last_updated = _time.time()

        except Exception as e:
            logger.debug(
                "[SourceReliability] record_claim(%s) failed (fail-soft): %s",
                source_id, e,
            )

    async def record_batch(
        self,
        claims: list[tuple[str, bool]],
        *,
        sprint_id: str = "",
    ) -> None:
        """Record multiple claims atomically.

        Args:
            claims: List of (source_id, is_contradictory) tuples.
            sprint_id: Optional sprint ID for audit trail.
        """
        if not self._enabled or not claims:
            return

        try:
            async with self._lock:
                for source_id, contradictory in claims:
                    self._stats["claims_recorded"] += 1
                    if contradictory:
                        self._stats["contradictions_recorded"] += 1

                    stats = self._sources.get(source_id)
                    if stats is None:
                        if len(self._sources) >= MAX_TRACKED_SOURCES:
                            self._evict_lru()
                        stats = SourceStats()
                        self._sources[source_id] = stats

                    if stats.total_claims < MAX_CLAIMS_PER_SOURCE:
                        stats.total_claims += 1
                        if contradictory:
                            stats.contradiction_count += 1
                    else:
                        stats.total_claims = stats.total_claims // 2 + 1
                        stats.contradiction_count = (
                            stats.contradiction_count // 2 + (1 if contradictory else 0)
                        )
                    stats.last_updated = _time.time()

        except Exception as e:
            logger.debug(
                "[SourceReliability] record_batch failed (fail-soft): %s", e,
            )

    def should_auto_retract(self, source_id: str) -> bool:
        """Check if a source meets auto-retraction criteria (synchronous).

        Returns True if:
        - Source has ≥ AUTO_RETRACT_MIN_CLAIMS claims
        - contradiction_count / total_claims > AUTO_RETRACT_RATIO
        - Source has NOT already been auto-retracted
        """
        if not self._enabled or not source_id:
            return False

        stats = self._sources.get(source_id)
        if stats is None:
            return False
        if stats.auto_retracted:
            return False
        if stats.total_claims < AUTO_RETRACT_MIN_CLAIMS:
            return False

        ratio = stats.contradiction_count / stats.total_claims
        return ratio > AUTO_RETRACT_RATIO

    def get_reliability(self, source_id: str) -> SourceReliability:
        """Get reliability snapshot for a source (synchronous)."""
        if not self._enabled or not source_id:
            return SourceReliability(source_id=source_id)

        stats = self._sources.get(source_id)
        if stats is None:
            return SourceReliability(source_id=source_id)

        ratio = (
            stats.contradiction_count / stats.total_claims
            if stats.total_claims > 0
            else 0.0
        )
        return SourceReliability(
            source_id=source_id,
            total_claims=stats.total_claims,
            contradiction_count=stats.contradiction_count,
            ratio=round(ratio, 4),
            last_updated=stats.last_updated,
            auto_retracted=stats.auto_retracted,
            auto_retracted_at=stats.auto_retracted_at,
        )

    def get_unreliable_sources(self) -> list[SourceReliability]:
        """Get all sources that meet auto-retract criteria (synchronous)."""
        results: list[SourceReliability] = []
        for source_id, stats in self._sources.items():
            if stats.auto_retracted:
                continue
            if stats.total_claims < AUTO_RETRACT_MIN_CLAIMS:
                continue
            ratio = stats.contradiction_count / stats.total_claims
            if ratio > AUTO_RETRACT_RATIO:
                results.append(SourceReliability(
                    source_id=source_id,
                    total_claims=stats.total_claims,
                    contradiction_count=stats.contradiction_count,
                    ratio=round(ratio, 4),
                    last_updated=stats.last_updated,
                ))
        results.sort(key=lambda r: r.ratio, reverse=True)
        return results

    async def mark_auto_retracted(
        self,
        source_id: str,
        sprint_id: str = "",
    ) -> bool:
        """Mark a source as auto-retracted (prevents repeated retraction)."""
        if not self._enabled or not source_id:
            return False

        try:
            async with self._lock:
                stats = self._sources.get(source_id)
                if stats is None:
                    stats = SourceStats()
                    self._sources[source_id] = stats
                stats.auto_retracted = True
                stats.auto_retracted_at = _time.time()
                self._stats["auto_retractions"] += 1
                return True
        except Exception as e:
            logger.debug(
                "[SourceReliability] mark_auto_retracted(%s) failed: %s",
                source_id, e,
            )
            return False

    async def record_decisions(
        self,
        decisions: list[Any],  # list[RetractionDecision]
    ) -> None:
        """Record verdict decisions from ConsistencyVerifier for cross-sprint tracking.

        Args:
            decisions: List of RetractionDecision from check_batch().
                       Only 'tri_source_voting' decisions count as strong evidence
                       of systematic unreliability.
        """
        if not self._enabled or not decisions:
            return

        try:
            async with self._lock:
                for decision in decisions:
                    source_id = getattr(decision, "source_id", None) or ""
                    if not source_id:
                        continue

                    stats = self._sources.get(source_id)
                    if stats is None:
                        if len(self._sources) >= MAX_TRACKED_SOURCES:
                            self._evict_lru()
                        stats = SourceStats()
                        self._sources[source_id] = stats

                    # Record verdict (counts as contradictory claim for ratio)
                    # Cap to prevent overflow
                    if stats.total_claims < MAX_CLAIMS_PER_SOURCE:
                        stats.total_claims += 1
                        stats.contradiction_count += 1
                    stats.last_updated = _time.time()

                    # If the verdict is tri_source_voting, it is strong evidence —
                    # record a second count to bias toward retraction
                    reason = getattr(decision, "reason", "")
                    if reason == "tri_source_voting" and stats.total_claims < MAX_CLAIMS_PER_SOURCE:
                        stats.contradiction_count += 1
                        stats.total_claims += 1

        except Exception as e:
            logger.debug(
                "[SourceReliability] record_decisions failed (fail-soft): %s", e,
            )

    def get_stats(self) -> dict[str, int]:
        """Return telemetry counter snapshot."""
        return {
            **self._stats,
            "tracked_sources": len(self._sources),
            "unreliable_sources": len(self.get_unreliable_sources()),
        }

    def reset(self) -> None:
        """Reset all in-memory state (for testing)."""
        self._sources.clear()
        self._stats = {
            "claims_recorded": 0,
            "contradictions_recorded": 0,
            "auto_retractions": 0,
            "lru_evictions": 0,
            "db_writes": 0,
            "db_reads": 0,
        }

    # ------------------------------------------------------------------
    # DuckDB persistence (optional, fail-soft)
    # ------------------------------------------------------------------

    async def _ensure_duckdb_table(self) -> bool:
        """Create source_reliability table in DuckDB if not present."""
        if self._duckdb_initialized or self._store is None:
            return self._duckdb_initialized

        try:
            # Use the store's internal connection to execute DDL
            if hasattr(self._store, "_execute_sql"):
                await asyncio.to_thread(
                    self._store._execute_sql, _SOURCE_RELIABILITY_DDL
                )
                self._duckdb_initialized = True
                return True
        except Exception as e:
            logger.debug(
                "[SourceReliability] DuckDB table init failed (fail-soft): %s", e,
            )
        return False

    async def sync_to_duckdb(
        self,
        source_ids: list[str] | None = None,
        sprint_id: str = "",
    ) -> int:
        """Persist in-memory stats to DuckDB (best-effort).

        Args:
            source_ids: Specific sources to sync, or None for all tracked.
            sprint_id: Sprint ID for audit trail.

        Returns:
            Number of rows written.
        """
        if not self._enabled or self._store is None:
            return 0

        try:
            if not await self._ensure_duckdb_table():
                return 0

            targets = source_ids if source_ids is not None else list(self._sources.keys())
            if not targets:
                return 0

            written = 0
            async with self._lock:
                for source_id in targets:
                    stats = self._sources.get(source_id)
                    if stats is None:
                        continue
                    ratio = (
                        stats.contradiction_count / stats.total_claims
                        if stats.total_claims > 0
                        else 0.0
                    )
                    try:
                        if hasattr(self._store, "_execute_sql"):
                            # DuckDB uses conn.execute(sql, [params]) — list of positional args
                            await asyncio.to_thread(
                                self._store._execute_sql,
                                f"""INSERT OR REPLACE INTO {_SOURCE_RELIABILITY_TABLE}
                                (source_id, total_claims, contradiction_count, ratio,
                                 last_updated, auto_retracted, auto_retracted_at, sprint_id)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                [
                                    source_id,
                                    stats.total_claims,
                                    stats.contradiction_count,
                                    ratio,
                                    stats.last_updated,
                                    stats.auto_retracted,
                                    stats.auto_retracted_at if stats.auto_retracted else None,
                                    sprint_id or None,
                                ],
                            )
                            written += 1
                    except Exception as e:
                        logger.debug(
                            "[SourceReliability] DuckDB write for %s failed: %s",
                            source_id, e,
                        )

            self._stats["db_writes"] += written
            return written

        except Exception as e:
            logger.debug(
                "[SourceReliability] sync_to_duckdb failed (fail-soft): %s", e,
            )
            return 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_lru(self) -> None:
        """Evict the least recently updated source (FIFO by last_updated)."""
        if not self._sources:
            return
        oldest = min(self._sources, key=lambda k: self._sources[k].last_updated)
        del self._sources[oldest]
        self._stats["lru_evictions"] += 1
        self._last_lru_eviction = _time.time()
        logger.debug("[SourceReliability] LRU evicted: %s", oldest)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_SOURCE_TRACKER: SourceReliabilityTracker | None = None
_TRACKER_LOCK = asyncio.Lock()


def get_source_reliability_tracker(
    store: DuckDBShadowStore | None = None,
) -> SourceReliabilityTracker:
    """Get or create the global SourceReliabilityTracker singleton.

    Args:
        store: Optional DuckDBShadowStore for persistence.
               Only used on first call; ignored on subsequent calls.

    Returns:
        SourceReliabilityTracker singleton.
    """
    global _SOURCE_TRACKER
    if _SOURCE_TRACKER is None:
        _SOURCE_TRACKER = SourceReliabilityTracker(store=store)
        logger.debug("[SourceReliability] Global tracker initialized")
    return _SOURCE_TRACKER


def reset_source_reliability_tracker() -> None:
    """Reset the global tracker (for testing only)."""
    global _SOURCE_TRACKER
    if _SOURCE_TRACKER is not None:
        _SOURCE_TRACKER.reset()
    _SOURCE_TRACKER = None


__all__ = [
    "SourceReliabilityTracker",
    "SourceReliability",
    "SourceStats",
    "get_source_reliability_tracker",
    "reset_source_reliability_tracker",
    "AUTO_RETRACT_RATIO",
    "AUTO_RETRACT_MIN_CLAIMS",
    "MAX_TRACKED_SOURCES",
]
