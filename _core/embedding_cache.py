from __future__ import annotations
import hashlib
import mmap
import os


import threading
from hledac.universal.utils.lru_cache import LRUCache
from pathlib import Path
from typing import TYPE_CHECKING, Any
import numpy as np
from _core._util import aclose
if TYPE_CHECKING:
    from numpy.typing import NDArray

class EmbeddingCacheError(Exception):
    """Base exception."""

class CacheCorruptError(EmbeddingCacheError):
    """L2 memmap read failed — file truncated or checksum mismatch."""
_L2_CACHE_DIR = Path.home() / '.hledac' / 'embedding_cache'
_L2_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_MAX_L2_BYTES = 2 * 1024 * 1024 * 1024
_L2_ENTRY_OVERHEAD = 24

def _item_size(dim: int, itemsize: int) -> int:
    """Bytes per embedding vector including 16-byte key digest."""
    return 16 + int(dim) * itemsize

def _max_items(dim: int, itemsize: int, max_bytes: int) -> int:
    """Max entries that fit within max_bytes."""
    return max(0, (max_bytes - _L2_ENTRY_OVERHEAD) // _item_size(dim, itemsize))
_DARWIN_ALIGN = 4096

class _L2Store:
    """Memory-mapped L2 cache.

    Format (no header magic):
      [entry_0, entry_1, ..., entry_N]
    Each entry = 16-byte key_digest || vector_bytes, aligned to 4KB.
    O(1) offset lookup via _offset_map dict (avoids linear scan).

    Thread-safe via threading.RLock.
    """
    __slots__ = tuple(('_dim', '_entry_bytes', '_fp', '_free_offsets', '_itemsize', '_lock', '_max_items', '_mmap', '_offset_map', '_path', '_vec_bytes'))

    def __init__(self, path: Path, dim: int, itemsize: int, max_bytes: int=_MAX_L2_BYTES) -> None:
        self._path = path
        self._dim = dim
        self._itemsize = itemsize
        self._vec_bytes = dim * itemsize
        self._entry_bytes = max(_DARWIN_ALIGN, 16 + self._vec_bytes)
        self._entry_bytes = (self._entry_bytes + _DARWIN_ALIGN - 1) // _DARWIN_ALIGN * _DARWIN_ALIGN
        self._max_items = _max_items(dim, itemsize, max_bytes)
        self._lock = threading.RLock()
        self._free_offsets: list[int] = []
        self._offset_map: dict[bytes, int] = {}
        self._mmap: mmap.mmap | None = None
        self._fp: Any = None
        try:
            if path.exists():
                self._mmap, self._fp = self._open_mmap(path)
            else:
                self._fp = open(path, 'w+b')
                try:
                    self._resize(self._max_items * self._entry_bytes)
                    self._mmap = mmap.mmap(self._fp.fileno(), 0)
                except Exception:
                    self._fp.close()
                    self._fp = None
                    raise
        except BaseException:
            if self._mmap is not None:
                try:
                    self._mmap.close()
                except Exception:  # noqa: BLE001
                    pass
                self._mmap = None
            if self._fp is not None:
                try:
                    self._fp.close()
                except Exception:  # noqa: BLE001
                    pass
                self._fp = None
            raise

    def _open_mmap(self, path: Path) -> tuple[mmap.mmap, Any]:
        """Return both mmap and fp to keep them paired in instance state."""
        try:
            fp = open(path, 'r+b')
            return (mmap.mmap(fp.fileno(), 0), fp)
        except OSError:
            path.unlink(missing_ok=True)
            fp = open(path, 'w+b')
            try:
                self._resize(self._max_items * self._entry_bytes)
                return (mmap.mmap(fp.fileno(), 0), fp)
            except Exception:
                fp.close()
                raise

    def _resize(self, size: int) -> None:
        """Truncate file to size bytes."""
        if self._fp is not None:
            os.ftruncate(self._fp.fileno(), size)
            self._fp.flush()

    def get(self, key_digest: bytes) -> NDArray[np.float16] | None:
        """Read entry by 16-byte key digest. Returns None on miss."""
        mm = self._mmap
        if mm is None:
            return None
        with self._lock:
            offset = self._offset_map.get(key_digest, -1)
            if offset < 0:
                return None
            data = mm[offset + 16:offset + 16 + self._vec_bytes]
            vec = np.frombuffer(data, dtype=np.float16)
            vec.flags.writeable = False
            try:
                self._free_offsets.remove(offset)
            except ValueError:  # noqa: BLE001
                pass
            return vec

    def set(self, key_digest: bytes, vec: NDArray[np.float16]) -> bool:
        """Store embedding. Returns True on success, False on eviction failure."""
        mm = self._mmap
        if mm is None:
            return False
        with self._lock:
            existing = self._offset_map.get(key_digest, -1)
            if existing >= 0:
                offset = existing
            elif self._free_offsets:
                offset = self._free_offsets.pop(0)
            else:
                return False
            mm[offset:offset + 16] = key_digest
            mm[offset + 16:offset + 16 + self._vec_bytes] = vec.tobytes()
            self._offset_map[key_digest] = offset
            return True

    def evict_oldest(self) -> bytes | None:
        """Evict oldest entry, return its key_digest."""
        mm = self._mmap
        if mm is None:
            return None
        with self._lock:
            for offset in self._offset_map.values():
                if offset not in self._free_offsets:
                    key_digest = bytes(mm[offset:offset + 16])
                    self._free_offsets.append(offset)
                    del self._offset_map[key_digest]
                    return key_digest
            return None

    def close(self) -> None:
        with self._lock:
            if self._mmap is not None:
                self._mmap.close()
                self._mmap = None
            if self._fp is not None:
                self._fp.close()
                self._fp = None

    def __len__(self) -> int:
        """Approximate count — items minus free slots."""
        mm = self._mmap
        if mm is None:
            return 0
        with self._lock:
            return len(self._offset_map)

class EmbeddingCache:
    """Two-layer embedding cache: L1 (OrderedDict[float16]) + L2 (np.memmap).

    Reduces Python-side embedding cache from ~15 MB (float32x5000x768d)
    to ~8 MB (float16x5000x384d) with disk overflow for M1 8GB safety.

    ## M1 8GB budget
    - L1: max 512 MB, items truncated to 384d float16
    - L2: max 2 GB, full 768d float16 overflow
    - Total: ~8 MB for 5000x384d + optional 2 GB L2
    """
    __slots__ = ('_l1', '_l1_size', '_l2', '_l2_path', '_dim', '_itemsize', '_max_l1_items', '_l1_max_mb', '_l2_max_bytes', '_hits', '_misses', '_l2_hits', '_l2_evictions')

    def __init__(self, capacity: int=100000, dim: int=384, dtype: type[np.floating[Any]]=np.float16, l1_max_mb: float=512.0, l2_max_gb: float=2.0) -> None:
        self._l1_size: int = 0
        self._dim = dim
        self._itemsize = np.dtype(dtype).itemsize
        max_l1_items = int(l1_max_mb * 1024 * 1024 / (dim * self._itemsize))
        self._max_l1_items = min(capacity, max_l1_items)
        self._l1: LRUCache[str, NDArray[np.float16]] = LRUCache(max_size=self._max_l1_items)
        self._l1_max_mb = l1_max_mb
        self._l2_max_bytes = int(l2_max_gb * 1024 * 1024 * 1024)
        self._l2: _L2Store | None = None
        self._l2_path = _L2_CACHE_DIR / f'embed_{dim}d_{self._itemsize}b.bin'
        self._hits = 0
        self._misses = 0
        self._l2_hits = 0
        self._l2_evictions = 0

    def get(self, key: str) -> NDArray[np.float16] | None:
        """LRU lookup: L1 -> L2 -> None."""
        if key in self._l1:
            self._l1.move_to_end(key)
            self._hits += 1
            return self._l1[key]
        if self._l2 is not None:
            digest = self._key_digest(key)
            vec = self._l2.get(digest)
            if vec is not None:
                self._l2_hits += 1
                self._promote(vec)
                return vec
        self._misses += 1
        return None

    def set(self, key: str, vec: NDArray[np.float16]) -> None:
        """Store embedding, evict L1/L2 if over capacity."""
        vec16 = vec.astype(np.float16)
        vec_bytes = vec16.nbytes
        if key in self._l1:
            old_bytes = self._l1[key].nbytes
            self._l1_size -= old_bytes
            self._l1.move_to_end(key)
        else:
            while self._l1_size + vec_bytes > int(self._l1_max_mb * 1024 * 1024) or len(self._l1) >= self._max_l1_items:
                self._evict_l1()
        self._l1[key] = vec16
        self._l1_size += vec16.nbytes

    def clear(self) -> None:
        """Clear both layers."""
        self._l1.clear()
        self._l1_size = 0
        if self._l2 is not None:
            self._l2.close()
            self._l2 = None
            self._l2_path.unlink(missing_ok=True)

    @property
    def stats(self) -> dict[str, int | float]:
        """Return hit/miss statistics."""
        total = self._hits + self._misses
        return {'hits': self._hits, 'misses': self._misses, 'l1_hit_rate': self._hits / total if total else 0.0, 'l2_hits': self._l2_hits, 'l2_evictions': self._l2_evictions, 'l1_size_mb': self._l1_size / 1024 / 1024, 'l1_items': len(self._l1), 'l2_items': len(self._l2) if self._l2 else 0}

    def _key_digest(self, key: str) -> bytes:
        """SHA256 digest of key, first 16 bytes."""
        return hashlib.sha256(key.encode()).digest()[:16]

    def _promote(self, vec: NDArray[np.float16]) -> None:
        """Promote L2 hit to L1 if room."""
        vec_bytes = vec.nbytes
        while self._l1_size + vec_bytes > int(self._l1_max_mb * 1024 * 1024) or len(self._l1) >= self._max_l1_items:
            if not self._l1:
                break
            self._evict_l1()
        key = f'_l2promo_{id(vec)}'
        self._l1[key] = vec.copy()
        self._l1_size += vec.nbytes

    def _evict_l1(self) -> None:
        """Evict oldest L1 entry to L2."""
        if not self._l1:
            return
        oldest_key, oldest_vec = self._l1.pop_lru()
        self._l1_size -= oldest_vec.nbytes
        if self._l2 is None:
            try:
                self._l2 = _L2Store(self._l2_path, self._dim, self._itemsize, max_bytes=self._l2_max_bytes)
            except OSError:
                self._l2 = None
                return
        digest = self._key_digest(oldest_key)
        if not self._l2.set(digest, oldest_vec):
            evicted = self._l2.evict_oldest()
            if evicted is None:
                return
            self._l2_evictions += 1
            self._l2.set(digest, oldest_vec)

    def __enter__(self) -> 'EmbeddingCache':
        return self

    def __exit__(self, *_: object) -> None:
        if self._l2 is not None:
            self._l2.close()
            self._l2 = None

    def __contains__(self, key: str) -> bool:
        """True if key is in L1 or L2."""
        if key in self._l1:
            return True
        if self._l2 is not None:
            digest = self._key_digest(key)
            return self._l2.get(digest) is not None
        return False

    def __len__(self) -> int:
        """Approximate total items (L1 + L2)."""
        l2_count = len(self._l2) if self._l2 else 0
        return len(self._l1) + l2_count

    def clear_l1_only(self) -> None:
        """Clear L1 only, preserve L2."""
        self._l1.clear()
        self._l1_size = 0