"""
LMDB Zero-Copy KV Store
========================



Zero-copy key-value storage using LMDB with msgspec (orjson fallback).
Optimized for M1 MacBook with 8GB RAM constraints.

Features:
- Zero-copy reads via buffers=True
- msgspec.json for fast JSON serialization (10-20x stdlib json, 2-3x orjson)
- Bounded storage with max size
- Async LMDB support via aiolmdb (if available)

Sprint F264: Migrated to ``utils.msgspec_json`` facade.
"""
import asyncio
import logging
import weakref
from pathlib import Path
from hledac.universal.utils.msgspec_json import decode, encode
from core import aclose
try:
    import lmdb
    LMDB_AVAILABLE = True
except ImportError:
    LMDB_AVAILABLE = False
try:
    from hledac.universal.paths import SPRINT_LMDB_ROOT, open_lmdb
    _PATH_ROOT = SPRINT_LMDB_ROOT
    _USE_CANONICAL = True
except ImportError:
    _PATH_ROOT = None
    _USE_CANONICAL = False
    open_lmdb = None
try:
    import aiolmdb
    AIOLMDB_AVAILABLE = True
except ImportError:
    AIOLMDB_AVAILABLE = False
logger = logging.getLogger(__name__)
DEFAULT_MAP_SIZE = 256 * 1024 * 1024
MAX_KEYS = 10000
LMDB_WRITE_BATCH_SIZE = 2500

class LMDBKVStore:
    """
    Zero-copy LMDB key-value store.

    Uses buffers=True for zero-copy reads and orjson for fast serialization.
    """
    __slots__ = tuple(('_env', '_finalizer', '_map_size', '_max_keys', '_path', '_critical'))

    def __init__(self, path: str | Path | None=None, map_size: int=DEFAULT_MAP_SIZE, max_keys: int=MAX_KEYS, critical: bool=False):
        """
        Initialize LMDB KV store.

        Args:
            path: Directory path for LMDB database. If None and canonical paths
                  are available, uses SPRINT_LMDB_ROOT / "kvstore.lmdb".
            map_size: Maximum database size in bytes
            max_keys: Maximum number of keys (for bounded storage)
            critical: If True, use synchronous writes for durability.
                      WAL stores should use critical=True to avoid crash-consistency issues
                      when HLEDAC_WAL_UNIFIED=0 opt-out path is used.
        """
        if not LMDB_AVAILABLE:
            raise ImportError('lmdb package not available')
        if path is None:
            if _USE_CANONICAL and _PATH_ROOT is not None:
                self._path = _PATH_ROOT / 'kvstore.lmdb'
            else:
                from hledac.universal.paths import DB_ROOT
                self._path = DB_ROOT / 'kvstore.lmdb'
        else:
            self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._map_size = map_size
        self._max_keys = max_keys
        self._critical = critical
        
        # F1 FIX: Use critical parameter for sync behavior
        # critical=True → sync=True, metasync=True, writemap=False (safe, durable)
        # critical=False → sync=False, metasync=False, writemap=True (fast, crash-risk)
        if critical:
            sync, metasync, writemap = True, True, False
        else:
            sync, metasync, writemap = False, False, True
            
        if _USE_CANONICAL and open_lmdb is not None:
            self._env = open_lmdb(self._path, map_size=map_size, max_dbs=1, 
                                   writemap=writemap, metasync=metasync, readahead=False)
        else:
            self._env = lmdb.open(str(self._path), map_size=map_size, max_dbs=1,
                                   writemap=writemap, metasync=metasync, readahead=False,
                                   sync=sync)
        logger.info(f'LMDB KV store initialized at {self._path} (critical={critical})')
        # F264: Use weakref.finalize for deterministic LMDB cleanup
        self._finalizer = weakref.finalize(self, self._cleanup)

    def _cleanup(self) -> None:
        """Called by weakref.finalize when LMDBKVStore is garbage collected."""
        try:
            if hasattr(self, '_env') and self._env:
                self._env.close()
                logger.info('LMDB KV store closed via finalizer')
        except Exception:  # noqa: BLE001
            pass  # Never raise in cleanup

    def get(self, key: str) -> dict | None:
        """
        Zero-copy get operation.

        Args:
            key: Key to retrieve

        Returns:
            Dict value if found, None otherwise
        """
        try:
            with self._env.begin(write=False, buffers=True) as txn:
                value = txn.get(key.encode('utf-8'))
                if value is None:
                    return None
                return decode(value)
        except Exception as e:
            logger.error(f'LMDB get failed for key {key}: {e}')
            return None

    def put(self, key: str, value: dict) -> bool:
        """
        Store a key-value pair.

        Args:
            key: Key to store
            value: Dict value to store

        Returns:
            True if successful, False otherwise
        """
        try:
            with self._env.begin(write=True) as txn:
                if txn.stat()['entries'] >= self._max_keys:
                    logger.warning(f'Max keys ({self._max_keys}) reached')
                    return False
                serialized = encode(value)
                txn.put(key.encode('utf-8'), serialized)
            return True
        except Exception as e:
            logger.error(f'LMDB put failed for key {key}: {e}')
            return False

    def put_many(self, items: list[tuple[str, dict]]) -> list[bool]:
        """
        Batch write multiple key-value pairs with batching.

        GHOST_INVARIANTS: LMDB bulk write always via cursor.putmulti() —
        never per-item env.begin(write=True) in loop.

        Args:
            items: List of (key, value) tuples

        Returns:
            list[bool]: Per-item success status. Never raises.
        """
        if not items:
            return []
        results: list[bool] = [False] * len(items)

        def _encode_pair(key: str, value: dict) -> tuple[bytes, bytes]:
            return (key.encode('utf-8'), encode(value))
        try:
            for i in range(0, len(items), LMDB_WRITE_BATCH_SIZE):
                batch = items[i:i + LMDB_WRITE_BATCH_SIZE]
                batch_indices = list(range(i, min(i + LMDB_WRITE_BATCH_SIZE, len(items))))
                try:
                    with self._env.begin(write=True) as txn:
                        current_entries = txn.stat()['entries']
                        if current_entries + len(batch) > self._max_keys:
                            logger.warning(f'Max keys ({self._max_keys}) would be exceeded')
                            for bi in batch_indices:
                                results[bi] = False
                            continue
                        encoded: list[tuple[bytes, bytes]] = [_encode_pair(key, value) for key, value in batch]
                        cursor = txn.cursor()
                        cursor.putmulti(encoded)
                        for bi in batch_indices:
                            results[bi] = True
                except Exception as batch_err:
                    logger.warning(f'putmulti batch failed, falling back to single-txn: {batch_err}')
                    try:
                        with self._env.begin(write=True) as txn:
                            encoded_batch = [(key.encode('utf-8'), encode(value)) for key, value in batch]
                            cursor = txn.cursor()
                            cursor.putmulti(encoded_batch)
                            for bi in batch_indices:
                                results[bi] = True
                    except Exception as fallback_err:
                        logger.error(f'Fallback transaction failed: {fallback_err}')
                        for bi in batch_indices:
                            results[bi] = False
            return results
        except Exception as e:
            logger.error(f'LMDB put_many failed: {e}')
            return [False] * len(items)

    def delete(self, key: str) -> bool:
        """
        Delete a key.

        Args:
            key: Key to delete

        Returns:
            True if key existed, False otherwise
        """
        try:
            with self._env.begin(write=True) as txn:
                return txn.delete(key.encode('utf-8'))
        except Exception as e:
            logger.error(f'LMDB delete failed for key {key}: {e}')
            return False

    def sync_hint(self) -> None:
        """
        Hint to sync data after bulk operations.

        This is a no-op in LMDB (it's always consistent),
        but included for API compatibility.
        """
        try:
            self._env.sync(False)
        except Exception:  # noqa: BLE001
            pass

    def compact(self) -> dict[str, int] | None:
        """
        Compact the LMDB environment in-place.

        Reclaims pages from deleted records and rebalances B-tree.
        Safe to call concurrently with readers (copy-on-write).

        Returns:
            dict with pages_reclaimed, pages_free, leaf_entries,
            branch_pages or None if unavailable.
        """
        try:
            from hledac.universal.knowledge.lmdb_boot_guard import compact_lmdb
            return compact_lmdb(self._env)
        except Exception:
            return None

    def close(self) -> None:
        """Close the database."""
        # F264: Detach finalizer when explicitly closed
        if hasattr(self, '_finalizer'):
            self._finalizer.detach()
        if hasattr(self, '_env') and self._env:
            self._env.close()
            logger.info('LMDB KV store closed')

    def __enter__(self) -> LMDBKVStore:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

class AsyncLMDBKVStore:
    """
    Async LMDB KV store with aiolmdb support.
    Falls back to ThreadPoolExecutor if aiolmdb is not available.
    """
    __slots__ = tuple(('_env', '_use_async', 'map_size', 'path'))

    def __init__(self, path: str | Path, map_size: int=DEFAULT_MAP_SIZE):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.map_size = map_size
        self._env = None
        self._use_async = AIOLMDB_AVAILABLE and LMDB_AVAILABLE

    async def open(self):
        """Open the async LMDB store."""
        if self._use_async:
            try:
                self._env = await aiolmdb.open(str(self.path), map_size=self.map_size)
                logger.info(f'AsyncLMDBKVStore opened (aiolmdb) at {self.path}')
                return
            except Exception as e:
                logger.warning(f'aiolmdb not available, using ThreadPoolExecutor: {e}')
                self._use_async = False
        if LMDB_AVAILABLE:
            self._env = lmdb.open(str(self.path), map_size=self.map_size, readahead=False, writemap=False, sync=False)
            logger.info(f'AsyncLMDBKVStore opened (ThreadPoolExecutor) at {self.path}')
        else:
            raise ImportError('Neither aiolmdb nor lmdb available')

    async def get(self, key: str) -> dict | None:
        """Async get operation."""
        key_bytes = key.encode()
        if self._use_async and self._env:
            try:
                val = await self._env.get(key_bytes)
                if val is None:
                    return None
                return decode(val)
            except Exception as e:
                logger.error(f'AsyncLMDB get failed: {e}')
                return None
        else:
            try:

                def _get():
                    with self._env.begin(buffers=True) as txn:
                        raw = txn.get(key_bytes)
                        if raw is None:
                            return None
                        # P0-4 FIX: Convert memoryview to bytes INSIDE the with block.
                        # With buffers=True, LMDB returns memoryview tied to txn's buffer.
                        # After txn closes, memoryview is invalid → ValueError on decode().
                        if isinstance(raw, memoryview):
                            return bytes(raw)
                        return raw
                val = await asyncio.to_thread(_get)
                if val is None:
                    return None
                return decode(val)
            except Exception as e:
                logger.error(f'AsyncLMDB get (executor) failed: {e}')
                return None

    async def put(self, key: str, value: dict) -> bool:
        """Async put operation."""
        key_bytes = key.encode()
        data = encode(value)
        if self._use_async and self._env:
            try:
                await self._env.put(key_bytes, data)
                return True
            except Exception as e:
                logger.error(f'AsyncLMDB put failed: {e}')
                return False
        else:
            try:

                def _put():
                    with self._env.begin(write=True) as txn:
                        txn.put(key_bytes, data)
                await asyncio.to_thread(_put)
                return True
            except Exception as e:
                logger.error(f'AsyncLMDB put (executor) failed: {e}')
                return False

    async def close(self):
        """Close the async LMDB store."""
        if self._env:
            if self._use_async:
                try:
                    self._env.close()
                except Exception:  # noqa: BLE001
                    pass
            else:
                try:
                    self._env.close()
                except Exception:  # noqa: BLE001
                    pass
            self._env = None
            logger.info('AsyncLMDBKVStore closed')