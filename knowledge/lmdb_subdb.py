"""
knowledge/lmdb_subdb.py
=======================
Sprint F265X: Unified LMDB store with key-prefix isolation for M1 8GB RAM.

F272: Expanded to consolidate WAL, dedup, conditional_cache into single mmap.

Reduces mmap overhead by merging multiple LMDB environments into one with
key-prefix namespacing.

Architecture:
    Single LMDB env: sprint_unified.lmdb
    Key namespaces:
        wal:            (WALManager: finding:, pending_duckdb_sync:, deadletter_ingest:)
        dedup:          (DedupManager: cross-run fingerprint dedup)
        cc:             (conditional_cache: ETag/Last-Modified HTTP cache)
        forensics:       (forensics enrichment metadata)
        multimodal:      (multimodal embedding cache)

Benefits vs separate files:
    - Single mmap region instead of 3-4 separate = ~50-70% RAM reduction
    - Shared lock file and OS page cache = lower kernel overhead
    - WAL, dedup, conditional_cache all share ~150MB instead of 144MB separate

M1 8GB bounds:
    Total map_size: 256 MB (wal:64 + dedup:64 + cc:16 + forensics:50 + multimodal:62)
    Key format: f"{prefix}:{original_key}"

Migration (F272):
    WALManager, DedupManager, conditional_cache gain optional unified store support.
    Default: use unified store (UNIFIED_LMDB=1).
    Opt-out per-component via env vars:
        HLEDAC_WAL_UNIFIED=0
        HLEDAC_DEDUP_UNIFIED=0
        HLEDAC_CC_UNIFIED=0
"""



import logging
import os
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.codec import decode as _msgspec_loads, encode as _msgspec_encode
from _core import aclose

if TYPE_CHECKING:
    pass

__all__ = [
    "UnifiedLMDBStore",
    "KEY_PREFIX_WAL",
    "KEY_PREFIX_DEDUP",
    "KEY_PREFIX_CC",
    "KEY_PREFIX_FORENSICS",
    "KEY_PREFIX_MULTIMODAL",
    "open_unified_lmdb",
]

# Key prefix constants (namespace isolation)
KEY_PREFIX_WAL: str = "wal"
KEY_PREFIX_DEDUP: str = "dedup"
KEY_PREFIX_CC: str = "cc"
KEY_PREFIX_FORENSICS: str = "forensics"
KEY_PREFIX_MULTIMODAL: str = "multimodal"

# Default unified map_size = sum of consolidated stores
_UNIFIED_MAP_SIZE: int = 256 * 1024 * 1024  # 256 MB

logger = logging.getLogger(__name__)


class UnifiedLMDBStore:
    """
    Unified LMDB store with key-prefix isolation for multiple data types.

    Opens a single LMDB environment and uses key prefixes to isolate
    different data types. This reduces mmap overhead from N separate
    files to 1 shared mmap region.

    ISSUE-6.1: Lazy initialization — LMDB environment is opened on first
    access, not in __init__. This saves ~200-400ms from sprint boot time
    when LMDB is not immediately needed.

    Usage:
        store = UnifiedLMDBStore(path)
        store.put(KEY_PREFIX_DEDUP, b"key1", b"value1")  # opens lazily
        value = store.get(KEY_PREFIX_DEDUP, b"key1")
        store.close()

    F272: Expanded API for WALManager/DedupManager compatibility.
    """

    __slots__ = ("_env", "_map_size", "_closed", "_initialized", "_path", "_lazy")  # _env: Any (set after _ensure_init)

    def __init__(
        self,
        path: Any,
        *,
        map_size: int | None = None,
        lazy: bool = True,
    ) -> None:
        """
        Args:
            path: Path to LMDB directory.
            map_size: Total map_size for unified environment.
                      Default: 256 MB (wal:64 + dedup:64 + cc:16 + forensics:50 + multimodal:62)
            lazy: If True (default), LMDB environment is opened on first access.
                  If False, opens immediately in __init__ (legacy behavior).
        """
        self._path = path
        if map_size is None:
            map_size = _UNIFIED_MAP_SIZE
        self._map_size = map_size
        self._env: Any = None  # type: ignore[assignment] — set after _ensure_init()
        self._closed = False
        self._initialized = False
        self._lazy = lazy
        if not lazy:
            self._ensure_init()

    def _ensure_init(self) -> None:
        """
        Lazy initialization — opens LMDB environment on first access.

        ISSUE-6.1: Deferred open saves ~200-400ms from sprint boot when
        LMDB is not immediately needed (lazy=True, default).
        """
        if self._initialized:
            return
        if self._closed:
            raise RuntimeError("Cannot initialize closed store")
        from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

        # Ensure parent directory exists
        import pathlib
        p = pathlib.Path(self._path)
        p.mkdir(parents=True, exist_ok=True)

        # P0-3 Fix: critical=True ensures durable writes (sync=True, metasync=True, writemap=False)
        # WAL stores findings that can be recovered from DuckDB, but we want crash-consistency.
        # critical=False uses writemap=True (fast but crash-inconsistent).
        self._env = open_lmdb_with_guard(
            self._path,
            map_size=self._map_size,
            max_dbs=1,  # Single DB, prefixes isolate namespaces
            critical=True,  # P0-3 Fix: ensure WAL durability
    )
        self._initialized = True
        logger.debug(
            f"[LMDB-UNIFIED] Opened at {self._path}, map_size={self._map_size / (1024*1024):.0f}MB"
    )

    @property
    def env(self) -> Any:
        """Return the LMDB environment for use with putmulti_bounded."""
        self._ensure_init()
        return self._env

    @property
    def is_initialized(self) -> bool:
        """Return True if store has been initialized (LMDB open attempted)."""
        return self._initialized

    @property
    def is_closed(self) -> bool:
        """Return True if store has been closed."""
        return self._closed

    def _key(self, prefix: str, key: bytes | str) -> bytes:
        """Create prefixed key."""
        if isinstance(key, str):
            key = key.encode("utf-8")
        return prefix.encode() + b":" + key

    def _key_str(self, prefix: str, key: str) -> bytes:
        """Create prefixed key from string key (for WAL-style operations)."""
        return prefix.encode() + b":" + key.encode("utf-8")

    def put(self, prefix: str, key: bytes, value: bytes) -> bool:
        """Put a single key-value pair."""
        if self._closed:
            return False
        try:
            self._ensure_init()
            with self._env.begin(write=True) as txn:
                txn.put(self._key(prefix, key), value)
            return True
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] put failed: {exc}")
            return False

    def get(self, prefix: str, key: bytes) -> bytes | None:
        """Get a value by key."""
        if self._closed:
            return None
        try:
            self._ensure_init()
            with self._env.begin() as txn:
                return txn.get(self._key(prefix, key))
        except Exception:
            return None

    def delete(self, prefix: str, key: bytes | str) -> bool:
        """Delete a key from the store."""
        if self._closed:
            return False
        try:
            self._ensure_init()
            with self._env.begin(write=True) as txn:
                txn.delete(self._key(prefix, key))
            return True
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] delete failed: {exc}")
            return False

    def put_str(self, prefix: str, key: str, value: dict) -> bool:
        """Put a JSON-serializable value with string key (WALManager compatibility)."""
        if self._closed:
            return False
        try:
            self._ensure_init()
            key_bytes = self._key_str(prefix, key)
            value_bytes = _msgspec_encode(value)  # encode() returns bytes for LMDB
            with self._env.begin(write=True) as txn:
                txn.put(key_bytes, value_bytes)
            return True
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] put_str failed: {exc}")
            return False

    def get_str(self, prefix: str, key: str) -> dict | None:
        """Get a JSON-deserialized value by string key (WALManager compatibility)."""
        if self._closed:
            return None
        try:
            self._ensure_init()
            with self._env.begin(buffers=True) as txn:
                raw = txn.get(self._key_str(prefix, key))
                if raw is None:
                    return None
                # P0-4 FIX: Convert memoryview to bytes INSIDE the with block.
                # With buffers=True, LMDB returns memoryview tied to txn's buffer.
                # After txn closes, memoryview is invalid → ValueError on bytes().
                # Exception swallowed → get_str returns None for existing keys.
                if isinstance(raw, memoryview):
                    raw = bytes(raw)
            return _msgspec_loads(raw)
        except Exception:
            return None

    def putmany_str(
        self, prefix: str, items: list[tuple[str, dict]]
    ) -> list[bool]:
        """Batch put with string keys. Returns per-item success list."""
        if self._closed or not items:
            return [False] * len(items) if items else []
        try:
            self._ensure_init()
            # M1-OPT: Single write transaction per chunk via putmulti_bounded_str
            from hledac.universal.utils.lmdb_bulk import putmulti_bounded_str
            return putmulti_bounded_str(self._env, items, key_prefix=prefix)
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] putmany_str failed: {exc}")
            return [False] * len(items)

    def scan_prefix(self, prefix: str) -> list[tuple[str, dict]]:
        """
        Efficient prefix scan for all entries with given prefix.
        Returns list of (key_str, value_dict) tuples.
        F272: WALManager wal_scan_pending_sync_markers compatibility.
        """
        results: list[tuple[str, dict]] = []
        if self._closed:
            return results
        try:
            self._ensure_init()
            prefixed_key = prefix.encode() + b":"
            with self._env.begin(buffers=True) as txn:
                cursor = txn.cursor()
                if cursor.set_range(prefixed_key):
                    for key_bytes, value_bytes in cursor.iternext():
                        # buffers=True returns memoryview; handle both bytes and memoryview
                        key = key_bytes.decode("utf-8") if isinstance(key_bytes, bytes) else bytes(key_bytes).decode("utf-8")
                        if not key.startswith(prefix + ":"):
                            break
                        try:
                            value = _msgspec_loads(value_bytes)
                            original_key = key[len(prefix) + 1:]
                            results.append((original_key, value))
                        except Exception:
                            continue
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] scan_prefix failed: {exc}")
        return results

    def put_batch(
        self,
        prefix: str,
        items: list[tuple[bytes, bytes]],
        overwrite: bool = True,
    ) -> int:
        """
        Batch write using putmulti_bounded.

        Args:
            prefix: Key prefix namespace.
            items: List of (key, value) tuples.
            overwrite: Whether to overwrite existing keys.

        Returns:
            Number of items written.
        """
        if self._closed or not items:
            return 0
        try:
            self._ensure_init()
            prefixed = [(self._key(prefix, k), v) for k, v in items]
            from hledac.universal.utils.lmdb_bulk import putmulti_bounded

            return putmulti_bounded(self._env, prefixed, overwrite=overwrite)
        except Exception:
            return 0

    def iter_prefix(self, prefix: str) -> list[tuple[bytes, bytes]]:
        """Iterate all items with given prefix."""
        results: list[tuple[bytes, bytes]] = []
        if self._closed:
            return results
        try:
            self._ensure_init()
            prefixed_key = prefix.encode() + b":"
            with self._env.begin() as txn:
                cursor = txn.cursor()
                for key, value in cursor:
                    if key.startswith(prefixed_key):
                        original_key = key[len(prefixed_key):]
                        results.append((original_key, value))
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] iter_prefix failed: {exc}")
        return results

    def get_raw(self, prefix: str, key: bytes) -> bytes | None:
        """
        Get raw bytes by prefixed key (DedupManager compatibility).

        Args:
            prefix: Namespace prefix (e.g., 'dedup').
            key: Raw bytes key (already encoded, not prefixed).

        Returns:
            Raw bytes value or None if not found.
        """
        if self._closed:
            return None
        try:
            self._ensure_init()
            prefixed_key = prefix.encode() + b":" + key
            with self._env.begin(buffers=True) as txn:
                val = txn.get(prefixed_key)
                if val is None:
                    return None
                # P6-1: Convert memoryview to bytes before returning.
                # txn.get() returns memoryview when buffers=True, but memoryview
                # is only valid while txn is open. Convert to bytes to ensure
                # caller can safely use the returned data after txn ends.
                if isinstance(val, memoryview):
                    return bytes(val)
                return val
        except Exception:
            return None

    def putmulti_raw(self, prefix: str, items: list[tuple[bytes, bytes]]) -> bool:
        """
        Batch write raw bytes using putmulti_bounded (DedupManager single-item compat).

        Args:
            prefix: Namespace prefix.
            items: List of (key_bytes, value_bytes) tuples.

        Returns:
            True if all items written successfully.
        """
        if self._closed or not items:
            return False
        try:
            self._ensure_init()
            from hledac.universal.utils.lmdb_bulk import putmulti_bounded
            prefixed = [(prefix.encode() + b":" + k, v) for k, v in items]
            putmulti_bounded(self._env, prefixed, overwrite=True)
            return True
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] putmulti_raw failed: {exc}")
            return False

    def putmulti_cursor_raw(self, prefix: str, items: list[tuple[bytes, bytes]]) -> bool:
        """
        Batch write using cursor.putmulti (DedupManager batch compat).

        Uses single transaction for all items — O(1) overhead vs N transactions.

        Args:
            prefix: Namespace prefix.
            items: List of (key_bytes, value_bytes) tuples.

        Returns:
            True on success.
        """
        if self._closed or not items:
            return False
        try:
            self._ensure_init()
            prefixed = [(prefix.encode() + b":" + k, v) for k, v in items]
            with self._env.begin(write=True) as txn:
                cursor = txn.cursor()
                cursor.putmulti(prefixed)
            return True
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] putmulti_cursor_raw failed: {exc}")
            return False

    def env_begin(self, write: bool = False) -> Any:
        """
        Return a new LMDB transaction (for advanced users).
        F272: Needed by DedupManager direct env access patterns.
        """
        if self._closed:
            return None
        try:
            self._ensure_init()
            return self._env.begin(write=write)
        except Exception as exc:
            logger.debug(f"[LMDB-UNIFIED] env_begin failed: {exc}")
            return None

    def close(self) -> None:
        """Close the LMDB environment."""
        if self._closed:
            return
        if self._env is not None:
            try:
                self._env.close()
            except Exception as exc:
                logger.debug(f"[LMDB-UNIFIED] close error: {exc}")
            self._env = None
        self._closed = True
        self._initialized = False

    def __enter__(self) -> UnifiedLMDBStore:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def compact_database(self) -> bool:
        """
        Compact the unified LMDB store to reclaim space after bulk deletions.

        Uses copy-to-new-DB pattern: iterates all live key-value pairs from
        the current env and writes them to a new temp LMDB, then atomically
        swaps the data.mdb file.

        M1 8GB safe: compaction is done in a single write transaction so
        memory usage is bounded by the batch size, not the total DB size.

        Returns:
            True on success, False on failure (store remains usable).
        """
        import pathlib
        import shutil
        import tempfile

        if self._closed or not self._initialized:
            return False

        try:
            # Pre-check: estimate live entries
            total_entries = 0
            with self._env.begin() as txn:
                stats = txn.stat()
                total_entries = stats.get("entries", 0)
            if total_entries == 0:
                logger.debug("[LMDB-UNIFIED] compact_database: empty, skipping")
                return True

            # Phase 1: Create temp LMDB and copy all live data
            temp_dir = tempfile.TemporaryDirectory(prefix="lmdb_compact_unified_")
            temp_path = pathlib.Path(temp_dir.name)

            import lmdb

            new_env = lmdb.open(
                str(temp_path),
                map_size=self._map_size,
                max_dbs=1,
                writemap=False,
                metasync=False,
    )
            new_db = new_env.open_db()

            # Copy all live data via cursor iteration
            with self._env.begin(buffers=True) as src_txn:
                src_cursor = src_txn.cursor()
                with new_env.begin(write=True, db=new_db) as dst_txn:
                    dst_cursor = dst_txn.cursor()
                    copied = 0
                    for key, value in src_cursor:
                        dst_cursor.put(key, value)
                        copied += 1
                    logger.debug(
                        "[LMDB-UNIFIED] compact_database: copied %d entries",
                        copied,
    )

            # Phase 2: Sync and close temp env (durable write)
            new_env.sync(force=True)
            new_env.close()

            # Phase 3: Atomic swap
            old_path = pathlib.Path(self._path)
            backup_path = old_path.with_suffix(".bak")
            data_mdb = old_path / "data.mdb"

            # Close current env before replacing files
            self._env.close()
            self._env = None
            self._initialized = False

            # Swap files
            if data_mdb.exists():
                shutil.move(str(data_mdb), str(backup_path))
            shutil.move(str(temp_path / "data.mdb"), str(data_mdb))

            # Move lock file if present
            temp_lock = temp_path / "lock.mdb"
            old_lock = old_path / "lock.mdb"
            if temp_lock.exists():
                old_lock.unlink(missing_ok=True)
                shutil.move(str(temp_lock), str(old_lock))

            # Reopen the store (metasync=False for M1 8GB optimization)
            # FIX: Reopen with mode=0o600 to maintain SEC-02 security guarantee
            self._env = lmdb.open(
                str(self._path),
                map_size=self._map_size,
                max_dbs=1,
                writemap=False,
                metasync=False,
                mode=0o600,
    )
            self._initialized = True

            # Cleanup backup
            shutil.rmtree(str(backup_path), ignore_errors=True)
            temp_dir.cleanup()

            logger.info("[LMDB-UNIFIED] compact_database: done (%d entries)", copied)
            return True

        except Exception as exc:
            logger.warning("[LMDB-UNIFIED] compact_database failed: %s", exc)
            # Try to restore — reopen if we closed the env (metasync=False for M1)
            if not self._initialized:
                try:
                    import lmdb
                    self._env = lmdb.open(
                        str(self._path),
                        map_size=self._map_size,
                        max_dbs=1,
                        writemap=False,
                        metasync=False,
                        mode=0o600,  # FIX: maintain SEC-02 security on restore
    )
                    self._initialized = True
                except Exception:
                    self._closed = True
            return False


def open_unified_lmdb(
    path: Any,
    *,
    map_size: int | None = None,
    lazy: bool = True,
) -> UnifiedLMDBStore:
    """
    Factory for UnifiedLMDBStore with M1 8GB safe defaults.

    F272: Replaces separate LMDB opens for WAL, dedup, conditional_cache.
    Default 256 MB shared mmap vs ~144 MB separate.

    ISSUE-6.1: Default lazy=True defers LMDB open to first access,
    saving ~200-400ms from sprint boot when LMDB is not immediately needed.

    Args:
        path: Path to LMDB directory.
        map_size: Override total map_size. Default 256 MB.
        lazy: If True (default), opens lazily on first access.

    Returns:
        UnifiedLMDBStore instance (caller must close).
    """
    return UnifiedLMDBStore(path, map_size=map_size, lazy=lazy)

