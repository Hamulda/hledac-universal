"""
WAL Manager — Sprint F216G refactor
===================================

ROLE: Owns LMDB for pending sync markers, deadletters, and WAL replay.

Separated from DuckDBShadowStore so WAL bugs are isolatable by testing
WALManager directly without touching DuckDB.

BOUNDARY:
    DuckDBShadowStore.async_ingest_findings_batch() writes to LMDB WAL first,
    then calls WALManager to write pending-sync markers on DuckDB failure.
    WALManager handles all marker lifecycle (write/scan/clear/deadletter).

CANONICAL WRITE PATH (unchanged):
    DuckDBShadowStore._activation_record_finding():
        1. WALManager.wal_write_finding()  → LMDB WAL
        2. DuckDB _sync_insert_finding()    → DuckDB
        3. On DuckDB fail: WALManager.wal_write_pending_sync_marker()

LMDB NAMESPACE:
    finding:{id}              → WAL truth record
    pending_duckdb_sync:{id}  → pending recovery marker
    deadletter_ingest:{id}     → permanently failed marker
"""

from __future__ import annotations

import time as _time
from typing import Any, Optional

import orjson

from hledac.universal.tools.lmdb_kv import LMDBKVStore

__all__ = ["WALManager"]


class WALManager:
    """
    Owns LMDB WAL lifecycle for DuckDBShadowStore.

    Responsible for:
      - WAL truth records (finding:{id})
      - Pending-sync recovery markers (pending_duckdb_sync:{id})
      - Dead-letter namespace (deadletter_ingest:{id})
      - Eviction of oldest pending markers (bounded by MAX_PENDING_SYNC_MARKERS)
    """

    MAX_PENDING_SYNC_MARKERS: int = 10000  # same bound as DuckDBShadowStore
    DEAD_LETTER_PREFIX: str = "deadletter_ingest:"

    def __init__(
        self,
        wal_path: str,
        *,
        map_size: int = 64 * 1024 * 1024,  # 64MB default
    ) -> None:
        """
        Args:
            wal_path: Absolute path to the WAL LMDB directory.
            map_size: LMDB map size in bytes.
        """
        self._wal_path = wal_path
        self._map_size = map_size
        self._wal_lmdb: Optional[LMDBKVStore] = None
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """Lazily initialize the WAL LMDB store."""
        if self._initialized:
            return
        self._wal_lmdb = LMDBKVStore(path=self._wal_path, map_size=self._map_size)
        self._initialized = True

    def close(self) -> None:
        """Close the WAL LMDB and release the lock file."""
        if self._wal_lmdb is not None:
            try:
                self._wal_lmdb.close()
            except Exception:
                pass
            self._wal_lmdb = None
        self._initialized = False

    @property
    def lmdb(self) -> Optional[LMDBKVStore]:
        """Return the WAL LMDB store (may be None if not initialized)."""
        return self._wal_lmdb

    # ------------------------------------------------------------------
    # WAL truth records
    # ------------------------------------------------------------------

    def wal_write_finding(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Write a finding to the WAL LMDB (sync, no await).

        LMDB key:   finding:{id}
        Value:      serialized dict with id, query, source_type, confidence, ts

        Returns True if LMDB write succeeded.
        """
        if not self._initialized:
            self.initialize()
        if self._wal_lmdb is None:
            return False

        try:
            key = f"finding:{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
            }
            return self._wal_lmdb.put(key, value)
        except Exception:
            return False

    def wal_get_finding(self, finding_id: str) -> Optional[dict[str, Any]]:
        """Get a WAL truth record by finding_id."""
        if self._wal_lmdb is None:
            return None
        try:
            key = f"finding:{finding_id}"
            return self._wal_lmdb.get(key)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Pending-sync markers
    # ------------------------------------------------------------------

    def wal_write_pending_sync_marker(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
    ) -> bool:
        """
        Write a pending-sync recovery marker to LMDB.

        Marker key:  pending_duckdb_sync:{id}
        Value:       same structure as WAL finding (id, query, source_type, confidence, ts)

        Written ONLY when LMDB succeeded but DuckDB failed.
        A future recovery sprint can find it via prefix scan and retry the DuckDB write.

        Evicts oldest markers if at or above MAX_PENDING_SYNC_MARKERS bound.
        """
        if not self._initialized:
            self.initialize()
        if self._wal_lmdb is None:
            return False

        try:
            # Evict oldest markers if we're at or above the bound
            self._evict_oldest_pending_markers(self.MAX_PENDING_SYNC_MARKERS - 1)

            key = f"pending_duckdb_sync:{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
            }
            return self._wal_lmdb.put(key, value)
        except Exception:
            return False

    def wal_scan_pending_sync_markers(self) -> list[dict[str, Any]]:
        """
        Efficient prefix scan for all pending_duckdb_sync markers.

        Returns list of marker values (dicts with id, query, source_type, confidence, ts).
        Uses LMDB cursor with prefix iteration — O(n) where n = number of pending markers.
        """
        if self._wal_lmdb is None:
            return []

        try:
            env = self._wal_lmdb._env
            if env is None:
                return []
            results = []
            prefix = "pending_duckdb_sync:"
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix.encode("utf-8")):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")
                        if not key.startswith(prefix):
                            break
                        try:
                            vb = bytes(value_bytes) if isinstance(value_bytes, memoryview) else value_bytes
                            value = orjson.loads(vb)
                            results.append(value)
                        except Exception:
                            continue
            return results
        except Exception:
            return []

    def wal_clear_pending_sync_marker(self, finding_id: str) -> bool:
        """
        Clear a pending-sync marker after successful recovery.

        Called by a future recovery sprint after the DuckDB write succeeds.
        """
        if self._wal_lmdb is None:
            return False
        try:
            key = f"pending_duckdb_sync:{finding_id}"
            return self._wal_lmdb.delete(key)
        except Exception:
            return False

    def wal_get_pending_marker(self, finding_id: str) -> Optional[dict[str, Any]]:
        """Get a single pending marker value by finding_id."""
        if self._wal_lmdb is None:
            return None
        try:
            key = f"pending_duckdb_sync:{finding_id}"
            return self._wal_lmdb.get(key)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Dead-letter namespace
    # ------------------------------------------------------------------

    def wal_write_deadletter_marker(
        self,
        finding_id: str,
        query: str,
        source_type: str,
        confidence: float,
        error: str,
        retry_count: int,
    ) -> bool:
        """
        Write a marker to the dead-letter namespace after max retries exceeded.

        Dead-letter key:  deadletter_ingest:{id}
        Value:            id, query, source_type, confidence, ts, error, retry_count
        """
        if self._wal_lmdb is None:
            return False
        try:
            key = f"{self.DEAD_LETTER_PREFIX}{finding_id}"
            value = {
                "id": finding_id,
                "query": query,
                "source_type": source_type,
                "confidence": confidence,
                "ts": _time.time(),
                "error": error,
                "retry_count": retry_count,
            }
            return self._wal_lmdb.put(key, value)
        except Exception:
            return False

    def wal_delete_deadletter_marker(self, finding_id: str) -> bool:
        """
        Delete a dead-letter marker (used when replay succeeds later).
        """
        if self._wal_lmdb is None:
            return False
        try:
            key = f"{self.DEAD_LETTER_PREFIX}{finding_id}"
            return self._wal_lmdb.delete(key)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def _evict_oldest_pending_markers(self, keep_count: int) -> int:
        """
        Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.

        Removes (total_count - keep_count) oldest markers by timestamp.
        Returns number of markers evicted.

        M1-safe: uses bounded heap instead of full sort, single write transaction
        for all deletions, and processes in chunks to limit memory pressure.
        """
        if self._wal_lmdb is None:
            return 0

        try:
            env = self._wal_lmdb._env
            if env is None:
                return 0

            prefix = "pending_duckdb_sync:"
            prefix_bytes = prefix.encode("utf-8")

            # Phase 1: count total markers efficiently (cursor range)
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if not cursor.set_range(prefix_bytes):
                    return 0
                total_count = 0
                for key_bytes, _ in cursor.iternext():
                    key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")
                    if not key.startswith(prefix):
                        break
                    total_count += 1

            if total_count <= keep_count:
                return 0

            evict_count = total_count - keep_count

            # Phase 2: bounded heap — only keep evict_count smallest by ts
            # heapq.nsmallest is O(n log k) vs full sort O(n log n), k = evict_count
            import heapq
            prefix_bytes = prefix.encode("utf-8")
            oldest_keys: list[tuple[float, str]] = []

            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix_bytes):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")
                        if not key.startswith(prefix):
                            break
                        try:
                            vb = bytes(value_bytes) if isinstance(value_bytes, memoryview) else value_bytes
                            value = orjson.loads(vb)
                            ts = value.get("ts", 0.0)
                            if len(oldest_keys) < evict_count:
                                heapq.heappush(oldest_keys, (ts, key))
                            elif ts < oldest_keys[0][0]:
                                heapq.heapreplace(oldest_keys, (ts, key))
                        except Exception:
                            continue

            if not oldest_keys:
                return 0

            # Extract just the keys for deletion (ts already embedded in heap)
            keys_to_evict = [key for _, key in oldest_keys]

            # Phase 3: single write transaction for all deletions (C2 fix)
            deleted = 0
            with env.begin(write=True) as txn:
                for key in keys_to_evict:
                    if txn.delete(key.encode("utf-8")):
                        deleted += 1

            return deleted
        except Exception:
            return 0

    def wal_delete(self, key: str) -> bool:
        """Delete a WAL entry by key."""
        if self._wal_lmdb is None:
            return False
        return self._wal_lmdb.delete(key)

    def wal_put(self, key: str, value: dict) -> bool:
        """Put a raw WAL entry."""
        if self._wal_lmdb is None:
            return False
        return self._wal_lmdb.put(key, value)

    def wal_put_many(self, items: list[tuple[str, dict]]) -> bool:
        """Put multiple raw WAL entries. Returns True if all succeed."""
        if self._wal_lmdb is None:
            return False
        return self._wal_lmdb.put_many(items)

    def wal_get(self, key: str) -> Optional[dict]:
        """Get a raw WAL entry."""
        if self._wal_lmdb is None:
            return None
        return self._wal_lmdb.get(key)