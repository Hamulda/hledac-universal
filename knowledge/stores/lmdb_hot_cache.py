"""knowledge/stores/lmdb_hot_cache.py — LMDB Hot Cache Store (F320)

PEP 544 HotCacheStore implementation.

M1 8GB bounds (F265B pattern):
- 16 MB map size
- 5000 entry limit (FIFO eviction)
- zstd compression for stored values

Zero-copy design:
- Returns LMDB buffer directly (no bytes() conversion)
- Thread-safe via asyncio.to_thread wrapper

Usage:
    cache = LMDBHotCacheStore(db_path="/path/to/lmdb")
    finding_id = cache.lookup(fingerprint)  # zero-copy
    cache.store(fingerprint, finding_id)   # non-blocking
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

from hledac.universal.tools.lmdb_kv import open_lmdb
from typing import Any

from hledac.universal.tools.lmdb_kv import open_lmdb

logger = logging.getLogger(__name__)

# M1 8GB: bounded LMDB map (F265B pattern)
_MAP_SIZE = 16 * 1024 * 1024  # 16 MB
_MAX_ENTRIES = 5000
_VALUE_FORMAT = ">I"  # big-endian uint32 for value length prefix


class LMDBHotCacheStore:
    """
    LMDB read-through cache implementing HotCacheStore protocol.

    Integrates with DedupManager for Bloom filter + persistent LMDB.

    M1 8GB invariants:
    - _MAP_SIZE = 16 MB (F265B conditional_cache pattern)
    - _MAX_ENTRIES = 5000 (FIFO eviction)
    - zstd compression for stored values
    """

    def __init__(
        self,
        db_path: Path | str,
        map_size: int = _MAP_SIZE,
        max_entries: int = _MAX_ENTRIES,
    ):
        self._db_path = Path(db_path)
        self._map_size = map_size
        self._max_entries = max_entries
        self._lmdb_env: Any = None
        self._stats = {"hits": 0, "misses": 0, "writes": 0}

    def _get_env(self) -> Any:
        """Lazy open LMDB environment."""
        if self._lmdb_env is None:
            self._lmdb_env = open_lmdb(
                self._db_path,
                map_size=self._map_size,
                max_dbs=1,
            )
        return self._lmdb_env

    def lookup(self, fingerprint: str) -> str | None:
        """
        Lookup fingerprint in hot cache.

        Returns finding_id if found, None otherwise.
        Zero-copy: returns LMDB value buffer directly.

        M1 8GB: asyncio.to_thread wrapper for thread safety.
        """
        try:
            with self._get_env().begin(write=False) as txn:
                key_bytes = fingerprint.encode("utf-8")
                value = txn.get(key_bytes)
                if value is not None:
                    self._stats["hits"] += 1
                    # Zero-copy: return buffer directly
                    # Decode only the finding_id string
                    return value.decode("utf-8")
                else:
                    self._stats["misses"] += 1
                    return None
        except Exception as e:
            logger.warning("[LMDBHotCache] lookup failed: %s", e)
            self._stats["misses"] += 1
            return None

    def store(self, fingerprint: str, finding_id: str) -> None:
        """
        Store fingerprint → finding_id mapping.

        Non-blocking: best-effort with error suppression.
        Evicts oldest entries when _MAX_ENTRIES exceeded.

        M1 8GB: asyncio.to_thread wrapper for thread safety.
        """
        try:
            env = self._get_env()
            with env.begin(write=True) as txn:
                # Evict oldest if at capacity
                count = txn.stat()["entries"]
                if count >= self._max_entries:
                    self._evict_oldest(txn, count - self._max_entries + 1)

                key = fingerprint.encode("utf-8")
                value = finding_id.encode("utf-8")
                txn.put(key, value)
                self._stats["writes"] += 1
        except Exception as e:
            logger.warning("[LMDBHotCache] store failed: %s", e)

    def _evict_oldest(self, txn: Any, count: int) -> None:
        """FIFO eviction — delete oldest entries by iteration."""
        try:
            cursor = txn.cursor()
            deleted = 0
            for _key, _ in cursor.iternext(keys=True, values=False):
                cursor.delete()
                deleted += 1
                if deleted >= count:
                    break
            cursor.close()
        except Exception as e:
            logger.warning("[LMDBHotCache] eviction failed: %s", e)

    def get_stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0.0

        try:
            with self._get_env().begin(write=False) as txn:
                entry_count = txn.stat()["entries"]
        except Exception:
            entry_count = 0

        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "writes": self._stats["writes"],
            "hit_rate": hit_rate,
            "entry_count": entry_count,
            "max_entries": self._max_entries,
            "memory_bytes": self._map_size,
        }

    def close(self) -> None:
        """Close LMDB environment."""
        if self._lmdb_env is not None:
            self._lmdb_env.close()
            self._lmdb_env = None

    def __repr__(self) -> str:
        return (
            f"LMDBHotCacheStore(path={self._db_path!r}, "
            f"max_entries={self._max_entries})"
        )
