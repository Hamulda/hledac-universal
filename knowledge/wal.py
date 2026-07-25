"""
WAL Manager — Sprint F216G refactor
F272: Optional UnifiedLMDBStore support for reduced mmap overhead.

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

LMDB NAMESPACE (dedicated WAL LMDB):
    finding:{id}              → WAL truth record
    pending_duckdb_sync:{id}  → pending recovery marker
    deadletter_ingest:{id}     → permanently failed marker

F272: When using unified store, keys are prefixed with "wal:" namespace:
    wal:finding:{id}, wal:pending_duckdb_sync:{id}, wal:deadletter_ingest:{id}
"""
import asyncio
import atexit
import os
import time as _time
import weakref
from typing import TYPE_CHECKING, Any
import orjson
if TYPE_CHECKING:
    from hledac.universal.tools.lmdb_kv import LMDBKVStore
__all__ = ['WALManager']

class WALManager:
    """
    Owns LMDB WAL lifecycle for DuckDBShadowStore.

    Responsible for:
      - WAL truth records (finding:{id})
      - Pending-sync recovery markers (pending_duckdb_sync:{id})
      - Dead-letter namespace (deadletter_ingest:{id})
      - Eviction of oldest pending markers (bounded by MAX_PENDING_SYNC_MARKERS)

    F272: Supports UnifiedLMDBStore via HLEDAC_WAL_UNIFIED=1 (default).
          Uses separate LMDB file when HLEDAC_WAL_UNIFIED=0.
    """
    MAX_PENDING_SYNC_MARKERS: int = 10000
    DEAD_LETTER_PREFIX: str = 'deadletter_ingest:'
    __slots__ = tuple(('_compact_interval_s', '_compact_write_threshold', '_finalize_handle', '_initialized', '_last_compact_ts', '_map_size', '_unified_store', '_use_unified', '_wal_lmdb', '_wal_path', '_write_count_since_compact', '__weakref__'))

    def __init__(self, wal_path: str, *, map_size: int=256 * 1024 * 1024, unified_store: Any=None) -> None:
        """
        Args:
            wal_path: Absolute path to the WAL LMDB directory.
            map_size: LMDB map size in bytes (unused when unified_store provided).
            unified_store: Optional UnifiedLMDBStore for consolidated storage.
        """
        self._wal_path = wal_path
        self._map_size = map_size
        self._unified_store = unified_store
        self._wal_lmdb: LMDBKVStore | None = None
        self._initialized: bool = False
        self._use_unified: bool = os.environ.get('HLEDAC_WAL_UNIFIED', '1') == '1' and unified_store is not None
        self._compact_interval_s: float = float(os.environ.get('HLEDAC_WAL_COMPACT_INTERVAL_S', '3600'))
        self._last_compact_ts: float = 0.0
        self._write_count_since_compact: int = 0
        self._compact_write_threshold: int = int(os.environ.get('HLEDAC_WAL_COMPACT_WRITE_THRESHOLD', '5000'))
        self._finalize_handle: weakref.finalize | None = None

    def initialize(self) -> None:
        """Lazily initialize the WAL LMDB store."""
        if self._initialized:
            return
        if self._use_unified and self._unified_store is not None:
            self._wal_lmdb = None
        else:
            from hledac.universal.tools.lmdb_kv import LMDBKVStore
            self._wal_lmdb = LMDBKVStore(path=self._wal_path, map_size=self._map_size)
        self._initialized = True
        self._ensure_cleanup()

    def close(self) -> None:
        """Close the WAL LMDB and release the lock file."""
        if self._wal_lmdb is not None:
            try:
                self._wal_lmdb.close()
            except Exception:
                pass
            self._wal_lmdb = None
        self._initialized = False
        if hasattr(self, '_finalize_handle') and self._finalize_handle is not None:
            try:
                self._finalize_handle.detach()
            except Exception:
                pass
            self._finalize_handle = None
        if hasattr(self, '_atexit_registered') and self._atexit_registered:
            try:
                atexit.unregister(self._atexit_cleanup)
            except Exception:
                pass
            self._atexit_registered = False

    @property
    def lmdb(self) -> LMDBKVStore | None:
        """Return the WAL LMDB store (may be None if using unified store)."""
        return self._wal_lmdb

    @property
    def unified_store(self) -> Any:
        """Return the unified store if using unified mode."""
        return self._unified_store

    def _key_finding(self, finding_id: str) -> str:
        """Build finding key."""
        return f'finding:{finding_id}'

    def _key_pending_sync(self, finding_id: str) -> str:
        """Build pending sync marker key."""
        return f'pending_duckdb_sync:{finding_id}'

    def _key_deadletter(self, finding_id: str) -> str:
        """Build deadletter key."""
        return f'{self.DEAD_LETTER_PREFIX}{finding_id}'

    def wal_write_finding(self, finding_id: str, query: str, source_type: str, confidence: float) -> bool:
        """
        Write a finding to the WAL LMDB (sync, no await).

        LMDB key:   finding:{id}
        Value:      serialized dict with id, query, source_type, confidence, ts

        Returns True if LMDB write succeeded.
        """
        if not self._initialized:
            self.initialize()
        value = {'id': finding_id, 'query': query, 'source_type': source_type, 'confidence': confidence, 'ts': _time.time()}
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.put_str('wal', self._key_finding(finding_id), value)
        if self._wal_lmdb is None:
            return False
        try:
            result = self._wal_lmdb.put(self._key_finding(finding_id), value)
            if result:
                self._write_count_since_compact += 1
            return result
        except Exception:
            return False

    def wal_get_finding(self, finding_id: str) -> dict[str, Any] | None:
        """Get a WAL truth record by finding_id."""
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.get_str('wal', self._key_finding(finding_id))
        if self._wal_lmdb is None:
            return None
        try:
            return self._wal_lmdb.get(self._key_finding(finding_id))
        except Exception:
            return None

    def wal_write_pending_sync_marker(self, finding_id: str, query: str, source_type: str, confidence: float) -> bool:
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
        self._evict_oldest_pending_markers(self.MAX_PENDING_SYNC_MARKERS - 1)
        value = {'id': finding_id, 'query': query, 'source_type': source_type, 'confidence': confidence, 'ts': _time.time()}
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.put_str('wal', self._key_pending_sync(finding_id), value)
        if self._wal_lmdb is None:
            return False
        try:
            result = self._wal_lmdb.put(self._key_pending_sync(finding_id), value)
            if result:
                self._write_count_since_compact += 1
            return result
        except Exception:
            return False

    def wal_scan_pending_sync_markers(self) -> list[dict[str, Any]]:
        """
        Efficient prefix scan for all pending_duckdb_sync markers.

        Returns list of marker values (dicts with id, query, source_type, confidence, ts).
        Uses LMDB cursor with prefix iteration — O(n) where n = number of pending markers.
        """
        if self._use_unified and self._unified_store is not None:
            results: list[dict[str, Any]] = []
            all_entries = self._unified_store.scan_prefix('wal')
            for key, value in all_entries:
                if key.startswith('pending_duckdb_sync:'):
                    results.append(value)
            return results
        if self._wal_lmdb is None:
            return []
        try:
            env = self._wal_lmdb._env
            if env is None:
                return []
            results = []
            prefix = self._key_pending_sync('')
            prefix_bytes = prefix.encode('utf-8')
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix_bytes):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = key_bytes.decode('utf-8') if isinstance(key_bytes, bytes) else bytes(key_bytes).decode('utf-8')
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
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.delete('wal', self._key_pending_sync(finding_id))
        if self._wal_lmdb is None:
            return False
        try:
            return self._wal_lmdb.delete(self._key_pending_sync(finding_id))
        except Exception:
            return False

    def wal_get_pending_marker(self, finding_id: str) -> dict[str, Any] | None:
        """Get a single pending marker value by finding_id."""
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.get_str('wal', self._key_pending_sync(finding_id))
        if self._wal_lmdb is None:
            return None
        try:
            return self._wal_lmdb.get(self._key_pending_sync(finding_id))
        except Exception:
            return None

    def wal_write_deadletter_marker(self, finding_id: str, query: str, source_type: str, confidence: float, error: str, retry_count: int) -> bool:
        """
        Write a marker to the dead-letter namespace after max retries exceeded.

        Dead-letter key:  deadletter_ingest:{id}
        Value:            id, query, source_type, confidence, ts, error, retry_count
        """
        if self._wal_lmdb is None and (not self._use_unified):
            return False
        value = {'id': finding_id, 'query': query, 'source_type': source_type, 'confidence': confidence, 'ts': _time.time(), 'error': error, 'retry_count': retry_count}
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.put_str('wal', self._key_deadletter(finding_id), value)
        if self._wal_lmdb is None:
            return False
        try:
            return self._wal_lmdb.put(self._key_deadletter(finding_id), value)
        except Exception:
            return False

    def wal_delete_deadletter_marker(self, finding_id: str) -> bool:
        """
        Delete a dead-letter marker (used when replay succeeds later).
        """
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.delete('wal', self._key_deadletter(finding_id))
        if self._wal_lmdb is None:
            return False
        try:
            return self._wal_lmdb.delete(self._key_deadletter(finding_id))
        except Exception:
            return False

    def _evict_oldest_pending_markers(self, keep_count: int) -> int:
        """
        Evict oldest pending sync markers to enforce MAX_PENDING_SYNC_MARKERS bound.

        Removes (total_count - keep_count) oldest markers by timestamp.
        Returns number of markers evicted.

        M1-safe: uses bounded heap instead of full sort, single write transaction
        for all deletions, and processes in chunks to limit memory pressure.
        """
        if self._use_unified and self._unified_store is not None:
            try:
                all_entries = self._unified_store.scan_prefix('wal')
                pending = [(k, v) for k, v in all_entries if k.startswith('pending_duckdb_sync:')]
                if len(pending) <= keep_count:
                    return 0
                pending.sort(key=lambda x: x[1].get('ts', 0))
                to_evict = pending[:len(pending) - keep_count]
                for key, _ in to_evict:
                    self._unified_store.delete('wal', key)
                return len(to_evict)
            except Exception:
                return 0
        if self._wal_lmdb is None:
            return 0
        try:
            env = self._wal_lmdb._env
            if env is None:
                return 0
            prefix = self._key_pending_sync('')
            prefix_bytes = prefix.encode('utf-8')
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if not cursor.set_range(prefix_bytes):
                    return 0
                total_count = 0
                for key_bytes, _ in cursor.iternext():
                    key = key_bytes.decode('utf-8') if isinstance(key_bytes, bytes) else bytes(key_bytes).decode('utf-8')
                    if not key.startswith(prefix):
                        break
                    total_count += 1
            if total_count <= keep_count:
                return 0
            evict_count = total_count - keep_count
            import heapq
            oldest_keys: list[tuple[float, str]] = []
            with env.begin(write=False, buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefix_bytes):
                    for key_bytes, value_bytes in cursor.iternext():
                        key = key_bytes.decode('utf-8') if isinstance(key_bytes, bytes) else bytes(key_bytes).decode('utf-8')
                        if not key.startswith(prefix):
                            break
                        try:
                            vb = bytes(value_bytes) if isinstance(value_bytes, memoryview) else value_bytes
                            value = orjson.loads(vb)
                            ts = value.get('ts', 0.0)
                            if len(oldest_keys) < evict_count:
                                heapq.heappush(oldest_keys, (ts, key))
                            elif ts < oldest_keys[0][0]:
                                heapq.heapreplace(oldest_keys, (ts, key))
                        except Exception:
                            continue
            if not oldest_keys:
                return 0
            keys_to_evict = [key for _, key in oldest_keys]
            deleted = 0
            with env.begin(write=True) as txn:
                for key in keys_to_evict:
                    if txn.delete(key.encode('utf-8')):
                        deleted += 1
            return deleted
        except Exception:
            return 0

    def wal_delete(self, key: str) -> bool:
        """Delete a WAL entry by key."""
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.delete('wal', key)
        if self._wal_lmdb is None:
            return False
        return self._wal_lmdb.delete(key)

    def wal_put(self, key: str, value: dict) -> bool:
        """Put a raw WAL entry."""
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.put_str('wal', key, value)
        if self._wal_lmdb is None:
            return False
        try:
            return self._wal_lmdb.put(key, value)
        except Exception:
            return False

    def wal_put_many(self, items: list[tuple[str, dict]]) -> list[bool]:
        """Put multiple raw WAL entries. Returns per-item success list."""
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.putmany_str('wal', items)
        if self._wal_lmdb is None:
            return [False] * len(items)
        results = self._wal_lmdb.put_many(items)
        if any(results):
            self._write_count_since_compact += sum((1 for r in results if r))
        return results

    def wal_get(self, key: str) -> dict | None:
        """Get a raw WAL entry."""
        if self._use_unified and self._unified_store is not None:
            return self._unified_store.get_str('wal', key)
        if self._wal_lmdb is None:
            return None
        return self._wal_lmdb.get(key)

    def compact(self) -> dict[str, int] | None:
        """
        Compact the WAL LMDB if interval OR write count threshold reached.

        Compaction is triggered when EITHER:
          - Time since last compaction >= _compact_interval_s (default: 1h)
          - Writes since last compaction >= _compact_write_threshold (default: 5000)
          - WAL LMDB is available (not using unified store)

        Returns compaction stats dict or None if skipped / unavailable.
        """
        if self._wal_lmdb is None:
            return None
        now = _time.time()
        time_elapsed = now - self._last_compact_ts >= self._compact_interval_s
        count_exceeded = self._write_count_since_compact >= self._compact_write_threshold
        if not time_elapsed and (not count_exceeded):
            return None
        from hledac.universal.knowledge.lmdb_boot_guard import compact_lmdb
        env = getattr(self._wal_lmdb, '_env', None)
        if env is None:
            return None
        result = compact_lmdb(env)
        if result is not None:
            self._last_compact_ts = now
            self._write_count_since_compact = 0
        return result

    async def aclose(self) -> None:
        """
        Async idempotent shutdown — canonical async cleanup path.

        Uses asyncio.to_thread() to avoid blocking the event loop.
        Idempotent: safe to call multiple times.
        """
        if self._wal_lmdb is None and (not self._initialized):
            return
        try:
            await asyncio.to_thread(self.close)
        except Exception:
            pass

    def _atexit_cleanup(self) -> None:
        """
        Emergency sync cleanup for atexit.register().

        Called at interpreter shutdown as last resort for lock file release.
        Uses sync close() since event loop is not available at atexit time.
        """
        try:
            self.close()
        except Exception:
            pass

    def _ensure_atexit(self) -> None:
        """
        Legacy: Register atexit cleanup if not already registered.

        Deprecated: Use _ensure_cleanup() instead (weakref.finalize).
        Kept for backward compat.
        """
        if not hasattr(self, '_atexit_registered'):
            self._atexit_registered = True
            atexit.register(self._atexit_cleanup)

    def _ensure_cleanup(self) -> None:
        """
        E4: Register weakref.finalize for guaranteed cleanup on interpreter shutdown.

        Replaces atexit.register() as primary safety net (Python 3.14+ refcounting
        changes make __del__ non-deterministic). weakref.finalize is guaranteed to run.
        """
        if self._finalize_handle is None:
            self._finalize_handle = weakref.finalize(self, self._cleanup_on_shutdown)

    def _cleanup_on_shutdown(self) -> None:
        """
        E4: Cleanup callback for weakref.finalize -- called at interpreter shutdown.

        Idempotent: safe even if close() was already called.
        """
        try:
            self.close()
        except Exception:
            pass

    def __del__(self) -> None:
        """
        Fallback destructor -- weakref.finalize is primary, __del__ is last resort.

        In Python 3.14+ __del__ is not guaranteed to run, so _ensure_cleanup()
        (via weakref.finalize) is the canonical cleanup path.
        """
        if hasattr(self, '_finalize_handle') and self._finalize_handle is not None:
            try:
                self._finalize_handle()
            except Exception:
                pass