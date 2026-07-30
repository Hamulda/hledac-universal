"""DuckDB WAL Manager — F360: WAL lifecycle consolidation.

Manages WAL + LMDB-backed storage within DuckDBShadowStore:
  - WAL truth records (finding:{id})
  - Pending-sync recovery markers
  - Deadletter markers

ARCHITECTURE:
    DuckDBWALManager wraps WALManager (knowledge/wal.py).
    F272: unified_store parameter enables shared LMDB map (sprint_unified.lmdb)
    replacing separate shadow_wal.lmdb / dedup.lmdb paths.

    DedupManager (_dedup_manager) is NOT wrapped — it is a separate
    component with 44 references in DuckDBShadowStore and will be
    addressed in F360 Phase 2 (DuckDBCanonical extraction).

    Composed into DuckDBCanonical.

STORAGE TRINITY (CLAUDE.md):
    Layer    | Tech    | Purpose
    ---------|---------|-------------------------------
    LMDB     | Key-val | Entity/claim metadata (WAL, dedup, query_cache)

M1 8GB constraints:
    - LMDB map_size bounded per instance
    - WAL compaction runs on shutdown / checkpoint
    - Deadletter markers limited to MAX_DEADLETTER_ENTRIES
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

_logger = logging.getLogger(__name__)

# Deadletter cap — prevents unbounded LMDB growth
MAX_DEADLETTER_ENTRIES = 10_000
# Pending-sync marker eviction batch size
_EVICTION_BATCH = 100


class DuckDBWALManager:
    """
    F360: WAL + LMDB lifecycle for DuckDBShadowStore.

    Wraps WALManager (knowledge/wal.py) for WAL truth records.
    Manages pending-sync recovery markers and deadletter markers.

    F272 consolidation target:
        shadow_wal.lmdb  → sprint_unified.lmdb (WAL truth)
        dedup.lmdb       → sprint_unified.lmdb (dedup bloom)
        query_cache.lmdb → sprint_unified.lmdb (query cache)
        (sub-database namespace isolation within single LMDB env)

    Currently: separate LMDB paths (F272 incomplete).
    """

    __slots__ = (
        "_wal_manager",       # WALManager instance
        "_wal_root",          # Path to WAL directory
        "_dedup_lmdb_path",   # Path to dedup LMDB
        "_query_cache_lmdb",  # _DuckDBQueryCache instance
        "_unified_lmdb",      # F272: UnifiedLMDBStore or None
        "_unified_store",     # F272: unified_store passed to WALManager
    )

    def __init__(
        self,
        wal_root: Path,
        dedup_lmdb_path: Path | None = None,
        query_cache_lmdb: Any | None = None,
        unified_store: Any | None = None,
    ) -> None:
        """
        Args:
            wal_root: Directory for WAL LMDB files.
            dedup_lmdb_path: Path to dedup LMDB (future: merged into unified).
            query_cache_lmdb: _DuckDBQueryCache instance (future: merged).
            unified_store: F272: UnifiedLMDBStore for shared LMDB map.
        """
        from hledac.universal.knowledge.wal import WALManager

        self._wal_root = wal_root
        self._dedup_lmdb_path = dedup_lmdb_path
        self._query_cache_lmdb = query_cache_lmdb
        self._unified_lmdb = unified_store
        self._unified_store = unified_store
        self._wal_manager = WALManager(
            wal_path=str(wal_root / "shadow_wal.lmdb"),
            unified_store=unified_store,
        )

    # ── WAL Manager delegation ──────────────────────────────────────────────

    @property
    def wal_manager(self) -> Any:  # WALManager
        """Return WALManager instance for WAL operations."""
        return self._wal_manager

    def wal_write_finding(
        self,
        finding_id: str,
        query: str = "",
        source_type: str = "",
        confidence: float = 0.0,
    ) -> bool:
        """Write finding WAL truth record.

        Accepts both forms used in duckdb_store.py:
          - wal_write_finding(finding)     — finding object (auto-detected)
          - wal_write_finding(finding_id=..., query=..., source_type=..., confidence=...)

        Delegates to WALManager.wal_write_finding(finding_id, query, source_type, confidence).
        """
        # Auto-detect: if first positional arg is a string, treat as WALManager signature
        if isinstance(finding_id, str):
            # WALManager-style keyword or positional args
            return self._wal_manager.wal_write_finding(
                finding_id=str(finding_id),
                query=str(query),
                source_type=str(source_type),
                confidence=float(confidence),
            )
        # Finding-object form: finding_id is actually the finding object
        finding_obj = finding_id
        finding_id_str = getattr(finding_obj, "finding_id", None) or getattr(finding_obj, "id", None)
        if not finding_id_str:
            return False
        return self._wal_manager.wal_write_finding(
            finding_id=str(finding_id_str),
            query=str(getattr(finding_obj, "query", "")),
            source_type=str(getattr(finding_obj, "source_type", "")),
            confidence=float(getattr(finding_obj, "confidence", 0.0)),
        )

    def wal_get_finding(self, finding_id: str) -> bytes | None:
        """Get finding WAL truth record by ID."""
        return self._wal_manager.wal_get_finding(finding_id)

    def wal_put_many(self, items: list[tuple[str, bytes]]) -> int:
        """Bulk WAL write (finding:{id}, value)."""
        return self._wal_manager.wal_put_many(items)

    def wal_delete(self, finding_id: str) -> None:
        """Delete finding WAL record."""
        self._wal_manager.wal_delete(finding_id)

    def wal_put(self, finding: Any) -> bool:
        """Write finding via WAL (single-item, legacy path)."""
        return self._wal_manager.wal_put(finding)

    def initialize(self) -> None:
        """Initialize WAL manager."""
        self._wal_manager.initialize()

    def close(self) -> None:
        """Close WAL manager synchronously."""
        self._wal_manager.close()

    # ── Pending-sync markers ─────────────────────────────────────────────────

    def wal_write_pending_sync_marker(self, finding_id: str) -> None:
        """Mark finding as pending DuckDB sync."""
        self._wal_manager.wal_write_pending_sync_marker(finding_id)

    def wal_get_pending_marker(self, finding_id: str) -> bytes | None:
        """Get pending-sync marker for finding."""
        return self._wal_manager.wal_get_pending_marker(finding_id)

    def wal_scan_pending_sync_markers(self) -> list[str]:
        """Return all finding IDs with pending sync markers."""
        return self._wal_manager.wal_scan_pending_sync_markers()

    def wal_clear_pending_sync_marker(self, finding_id: str) -> None:
        """Clear pending-sync marker after successful DuckDB write."""
        self._wal_manager.wal_clear_pending_sync_marker(finding_id)

    # ── FLOW-03: Checkpoint protocol ────────────────────────────────────────

    def wal_write_prewrite(self, finding_id: str) -> bool:
        """FLOW-03: Write prewrite marker before DuckDB write."""
        return self._wal_manager.wal_write_prewrite(finding_id)

    def wal_write_checkpoint(self, finding_id: str) -> bool:
        """FLOW-03: Write checkpoint marker after DuckDB write succeeds."""
        return self._wal_manager.wal_write_checkpoint(finding_id)

    def wal_clear_prewrite(self, finding_id: str) -> bool:
        """FLOW-03: Delete prewrite marker after checkpoint is written."""
        return self._wal_manager.wal_clear_prewrite(finding_id)

    def wal_has_checkpoint(self, finding_id: str) -> bool:
        """FLOW-03: Check if checkpoint exists for finding."""
        return self._wal_manager.wal_has_checkpoint(finding_id)

    def wal_scan_prewrites_without_checkpoint(self) -> list[dict[str, Any]]:
        """FLOW-03: Scan for prewrites needing recovery."""
        return self._wal_manager.wal_scan_prewrites_without_checkpoint()

    # ── Deadletter markers ───────────────────────────────────────────────────

    def wal_write_deadletter_marker(self, finding_id: str, reason: str) -> None:
        """Mark finding as deadletter (failed after max retries)."""
        self._wal_manager.wal_write_deadletter_marker(finding_id, reason)

    def wal_delete_deadletter_marker(self, finding_id: str) -> None:
        """Delete deadletter marker (after recovery)."""
        self._wal_manager.wal_delete_deadletter_marker(finding_id)

    def deadletter_marker_count(self) -> int:
        """Return count of deadletter markers."""
        return self._wal_manager.deadletter_marker_count()

    # ── WAL maintenance ───────────────────────────────────────────────────────

    def compact(self) -> None:
        """Compact WAL LMDB to reclaim space."""
        self._wal_manager.compact()

    async def aclose(self) -> None:
        """Async close WAL manager."""
        await self._wal_manager.aclose()

    # ── Pending marker eviction ──────────────────────────────────────────────

    def _evict_oldest_pending_markers(self) -> int:
        """Evict oldest pending-sync markers if over limit."""
        return self._wal_manager._evict_oldest_pending_markers()

    # ── WAL replay ──────────────────────────────────────────────────────────

    async def async_replay_all_pending_duckdb_sync(self) -> int:
        """Replay all pending-sync markers to DuckDB. Returns count replayed."""
        return await self._wal_manager.async_replay_all_pending_duckdb_sync()

    async def async_replay_single_pending_marker(self, finding_id: str) -> bool:
        """Replay single pending marker. Returns True if successful."""
        return await self._wal_manager.async_replay_single_pending_marker(finding_id)
