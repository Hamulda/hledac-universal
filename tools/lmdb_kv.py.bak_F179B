"""
LMDB Zero-Copy KV Store
========================

Zero-copy key-value storage using LMDB with orjson.
Optimized for M1 MacBook with 8GB RAM constraints.

Features:
- Zero-copy reads via buffers=True
- orjson for fast JSON serialization
- Bounded storage with max size
- Async LMDB support via aiolmdb (if available)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

import orjson

try:
    import lmdb
    LMDB_AVAILABLE = True
except ImportError:
    LMDB_AVAILABLE = False

# Sprint 3D: Use paths helpers for sprint ephemeral store
try:
    from hledac.universal.paths import SPRINT_LMDB_ROOT, open_lmdb
    _PATH_ROOT = SPRINT_LMDB_ROOT
    _USE_CANONICAL = True
except ImportError:
    _PATH_ROOT = None
    _USE_CANONICAL = False
    open_lmdb = None

# Async LMDB support
try:
    import aiolmdb
    AIOLMDB_AVAILABLE = True
except ImportError:
    AIOLMDB_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default bounds
DEFAULT_MAP_SIZE = 64 * 1024 * 1024  # 64MB
MAX_KEYS = 10000
LMDB_WRITE_BATCH_SIZE = 500  # Hard cap for batched writes


class LMDBKVStore:
    """
    Zero-copy LMDB key-value store.

    Uses buffers=True for zero-copy reads and orjson for fast serialization.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        map_size: int = DEFAULT_MAP_SIZE,
        max_keys: int = MAX_KEYS,
    ):
        """
        Initialize LMDB KV store.

        Args:
            path: Directory path for LMDB database. If None and canonical paths
                  are available, uses SPRINT_LMDB_ROOT / "kvstore.lmdb".
            map_size: Maximum database size in bytes
            max_keys: Maximum number of keys (for bounded storage)
        """
        if not LMDB_AVAILABLE:
            raise ImportError("lmdb package not available")

        # Sprint 3D: canonical path resolution
        if path is None:
            if _USE_CANONICAL and _PATH_ROOT is not None:
                self._path = _PATH_ROOT / "kvstore.lmdb"
            else:
                self._path = Path.home() / ".hledac_kvstore.lmdb"
        else:
            self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._map_size = map_size
        self._max_keys = max_keys

        # Sprint 3D: use open_lmdb() for env-driven discipline + lock recovery
        if _USE_CANONICAL and open_lmdb is not None:
            self._env = open_lmdb(
                self._path,
                map_size=map_size,
                max_dbs=1,
                writemap=False,
                metasync=True,
            )
        else:
            # Fallback: direct lmdb.open (backward compat, no lock recovery)
            self._env = lmdb.open(
                str(self._path),
                map_size=map_size,
                max_dbs=1,
                writemap=False,
                metasync=True,
            )
        logger.info(f"LMDB KV store initialized at {self._path}")

    def get(self, key: str) -> Optional[dict]:
        """
        Zero-copy get operation.

        Args:
            key: Key to retrieve

        Returns:
            Dict value if found, None otherwise
        """
        try:
            # Zero-copy: buffers=True returns memoryview without copying
            with self._env.begin(write=False, buffers=True) as txn:
                value = txn.get(key.encode("utf-8"))
                if value is None:
                    return None
                # orjson.loads accepts bytes/memoryview directly - no decode() needed
                return orjson.loads(value)
        except Exception as e:
            logger.error(f"LMDB get failed for key {key}: {e}")
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
                # Check key count limit
                if txn.stat()["entries"] >= self._max_keys:
                    logger.warning(f"Max keys ({self._max_keys}) reached")
                    return False

                serialized = orjson.dumps(value)
                txn.put(key.encode("utf-8"), serialized)
            return True
        except Exception as e:
            logger.error(f"LMDB put failed for key {key}: {e}")
            return False

    def put_many(self, items: list[tuple[str, dict]]) -> bool:
        """
        Batch write multiple key-value pairs with batching.

        Args:
            items: List of (key, value) tuples

        Returns:
            True if all successful, False otherwise
        """
        if not items:
            return True

        try:
            # Batch items
            for i in range(0, len(items), LMDB_WRITE_BATCH_SIZE):
                batch = items[i:i + LMDB_WRITE_BATCH_SIZE]
                try:
                    with self._env.begin(write=True) as txn:
                        # Check key count limit
                        current_entries = txn.stat()["entries"]
                        if current_entries + len(batch) > self._max_keys:
                            logger.warning(f"Max keys ({self._max_keys}) would be exceeded")
                            return False

                        for key, value in batch:
                            serialized = orjson.dumps(value)
                            txn.put(key.encode("utf-8"), serialized)
                except Exception as batch_err:
                    logger.warning(f"Batch write failed, falling back to individual writes: {batch_err}")
                    # Fallback: write individually
                    for key, value in batch:
                        try:
                            with self._env.begin(write=True) as txn:
                                serialized = orjson.dumps(value)
                                txn.put(key.encode("utf-8"), serialized)
                        except Exception as single_err:
                            logger.error(f"Individual write failed for {key}: {single_err}")
            return True
        except Exception as e:
            logger.error(f"LMDB put_many failed: {e}")
            return False

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
                return txn.delete(key.encode("utf-8"))
        except Exception as e:
            logger.error(f"LMDB delete failed for key {key}: {e}")
            return False

    def sync_hint(self) -> None:
        """
        Hint to sync data after bulk operations.

        This is a no-op in LMDB (it's always consistent),
        but included for API compatibility.
        """
        try:
            self._env.sync(False)
        except Exception:
            pass

    def close(self) -> None:
        """Close the database."""
        if hasattr(self, "_env") and self._env:
            self._env.close()
            logger.info("LMDB KV store closed")

    def __enter__(self) -> "LMDBKVStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class AsyncLMDBKVStore:
    """
    Async LMDB KV store with aiolmdb support.
    Falls back to ThreadPoolExecutor if aiolmdb is not available.
    """

    def __init__(self, path: str | Path, map_size: int = DEFAULT_MAP_SIZE):
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
                logger.info(f"AsyncLMDBKVStore opened (aiolmdb) at {self.path}")
                return
            except Exception as e:
                logger.warning(f"aiolmdb not available, using ThreadPoolExecutor: {e}")
                self._use_async = False

        # Fallback to ThreadPoolExecutor
        if LMDB_AVAILABLE:
            self._env = lmdb.open(str(self.path), map_size=self.map_size)
            logger.info(f"AsyncLMDBKVStore opened (ThreadPoolExecutor) at {self.path}")
        else:
            raise ImportError("Neither aiolmdb nor lmdb available")

    async def get(self, key: str) -> Optional[dict]:
        """Async get operation."""
        key_bytes = key.encode()

        if self._use_async and self._env:
            try:
                val = await self._env.get(key_bytes)
                if val is None:
                    return None
                return orjson.loads(val)
            except Exception as e:
                logger.error(f"AsyncLMDB get failed: {e}")
                return None
        else:
            # Fallback: use ThreadPoolExecutor
            loop = asyncio.get_running_loop()
            try:
                def _get():
                    with self._env.begin(buffers=True) as txn:
                        return txn.get(key_bytes)

                val = await loop.run_in_executor(None, _get)
                if val is None:
                    return None
                return orjson.loads(val)
            except Exception as e:
                logger.error(f"AsyncLMDB get (executor) failed: {e}")
                return None

    async def put(self, key: str, value: dict) -> bool:
        """Async put operation."""
        key_bytes = key.encode()
        data = orjson.dumps(value)

        if self._use_async and self._env:
            try:
                await self._env.put(key_bytes, data)
                return True
            except Exception as e:
                logger.error(f"AsyncLMDB put failed: {e}")
                return False
        else:
            # Fallback: use ThreadPoolExecutor
            loop = asyncio.get_running_loop()
            try:
                def _put():
                    with self._env.begin(write=True) as txn:
                        txn.put(key_bytes, data)

                await loop.run_in_executor(None, _put)
                return True
            except Exception as e:
                logger.error(f"AsyncLMDB put (executor) failed: {e}")
                return False

    async def close(self):
        """Close the async LMDB store."""
        if self._env:
            if self._use_async:
                try:
                    await self._env.close()
                except Exception:
                    pass
            else:
                try:
                    self._env.close()
                except Exception:
                    pass
            self._env = None
            logger.info("AsyncLMDBKVStore closed")
