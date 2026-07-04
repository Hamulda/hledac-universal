"""
Embedding Cache with np.memmap float16 backing for M1 8GB UMA.

Architecture:
- Layer 1: LRU in-memory dict (fastest, bounded by item count)
- Layer 2: np.memmap float16 file (persists across restarts, OS-managed)
- Fallback: MLXEmbeddingManager.encode()

Key invariants:
- Always-on, no feature flags
- Fail-safe: if memmap fails, fall back to pure encode
- Bounded: MAX_ENTRIES (soft cap) + MAX_BYTES (hard cap ~512MB)
- Thread-safe via asyncio.Lock (not threading.Lock for async context)
- M1 8GB: ~512MB max cache = ~340k embeddings @ 256d float16

Usage:
    cache = EmbeddingCache(dim=256)
    embedding = await cache.get_or_encode("text to embed")
    await cache.set("text", embedding)
"""
from __future__ import annotations


import asyncio
import hashlib
import json
import logging
import math
import shutil
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from asyncio import Lock as _AsyncioLock  # Python 3.11+

logger = logging.getLogger(__name__)

# === Constants (M1 8GB UMA tuned) ===
_EMBED_CACHE_DIR = Path.home() / ".hledac" / "cache" / "embeddings"
_MAX_ENTRIES = 100_000  # Soft cap: ~100k entries
_MAX_BYTES = 512 * 1024 * 1024  # 512 MB hard cap
_HEADER_SIZE = 4096  # 4 KB header for metadata
_ENTRY_OVERHEAD = 128  # bytes per LRU dict entry (estimate)


@dataclass
class CacheStats:
    """Runtime cache statistics."""

    hits: int = 0
    misses: int = 0
    l1_hits: int = 0  # in-memory dict hits
    l2_hits: int = 0  # memmap hits
    evictions: int = 0
    memmap_errors: int = 0
    encode_errors: int = 0

    @property
    def total_requests(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.hits / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "l1_hits": self.l1_hits,
            "l2_hits": self.l2_hits,
            "evictions": self.evictions,
            "memmap_errors": self.memmap_errors,
            "encode_errors": self.encode_errors,
            "hit_rate": round(self.hit_rate, 4),
        }


@dataclass
class CacheEntry:
    """Single cache entry with LRU tracking."""

    offset: int  # byte offset in memmap file
    length: int  # number of floats (dim)
    mtime: float  # for LRU eviction
    text_hash: str  # sha256 of original text


class EmbeddingCache:
    """
    Two-layer embedding cache with np.memmap float16 backing.

    Layer 1: in-memory dict[hash -> CacheEntry] with LRU eviction
    Layer 2: np.memmap float16 file for persistence

    Thread-safe for async use via asyncio.Lock.
    """

    VERSION = 1  # memmap file format version

    def __init__(
        self,
        dim: int = 256,
        max_entries: int = _MAX_ENTRIES,
        max_bytes: int = _MAX_BYTES,
        cache_dir: Path | None = None,
    ):
        """
        Initialize embedding cache.

        Args:
            dim: Embedding dimension (256 for MRL, 768 for full)
            max_entries: Soft cap for LRU dict entries
            max_bytes: Hard cap for memmap file size
            cache_dir: Override cache directory
        """
        self.dim = dim
        self.max_entries = max_entries
        self.max_bytes = max_bytes

        # Resolve cache directory
        if cache_dir is not None:
            self.cache_dir = cache_dir
        else:
            self.cache_dir = _EMBED_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # memmap file path
        self._memmap_path = self.cache_dir / f"embeddings_d{dim}.dat"
        self._meta_path = self.cache_dir / f"embeddings_d{dim}.meta.json"

        # Layer 1: LRU in-memory dict
        # key: sha256(text), value: CacheEntry
        self._l1: dict[str, CacheEntry] = {}
        self._l1_lock = asyncio.Lock()

        # Layer 2: memmap backing (lazy init)
        self._mmap: np.memmap | None = None
        self._mmap_lock = asyncio.Lock()

        # Stats
        self.stats = CacheStats()

        # Track cache file size for eviction
        self._file_size = 0

        # Initialize memmap on construction (sync __init__, no running loop yet)
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        try:
            _loop.run_until_complete(self._init_memmap())
        finally:
            _loop.close()

    async def _init_memmap(self) -> None:
        """Initialize or open existing memmap file."""
        async with self._mmap_lock:
            try:
                if self._memmap_path.exists():
                    # Open existing: verify header, load LRU metadata
                    await self._load_meta()
                    # Memory-map existing file
                    self._mmap = np.memmap(
                        str(self._memmap_path),
                        dtype=np.float16,
                        mode="r+",
                        offset=_HEADER_SIZE,
                        shape=(self._max_shape()[0], self.dim),
                    )
                    self._file_size = self._memmap_path.stat().st_size
                    logger.info(
                        f"[EmbedCache] Opened existing memmap: {self._file_size / 1024 / 1024:.1f} MB"
                    )
                else:
                    # Create new memmap file
                    await self._create_memmap()
                    logger.info(f"[EmbedCache] Created new memmap: {self._memmap_path}")
            except Exception as e:
                logger.warning(f"[EmbedCache] memmap init failed (fallback to encode-only): {e}")
                self.stats.memmap_errors += 1
                self._mmap = None

    def _max_shape(self) -> tuple[int, int]:
        """Calculate max entries that fit in max_bytes."""
        # Each entry: dim floats * 2 bytes (float16) + entry overhead
        bytes_per_entry = self.dim * 2 + _ENTRY_OVERHEAD
        max_entries = min(self.max_entries, self.max_bytes // bytes_per_entry)
        return (max(max_entries, 1000), self.dim)  # at least 1000 entries

    async def _create_memmap(self) -> None:
        """Create new memmap file with header."""
        max_entries, dim = self._max_shape()
        total_size = _HEADER_SIZE + max_entries * dim * 2  # float16 = 2 bytes

        try:
            # Create sparse file (fast on APFS)
            with open(self._memmap_path, "wb") as f:
                # Write header
                header = {
                    "version": self.VERSION,
                    "dim": dim,
                    "max_entries": max_entries,
                    "free_list": list(range(max_entries)),  # free slot offsets
                    "used": 0,
                }
                header_bytes = json.dumps(header).encode()
                header_bytes = header_bytes.ljust(_HEADER_SIZE, b"\x00")
                f.write(header_bytes)

                # Write zeroed data area
                f.seek(total_size - 1)
                f.write(b"\x00")

            # Memory-map
            self._mmap = np.memmap(
                str(self._memmap_path),
                dtype=np.float16,
                mode="r+",
                offset=_HEADER_SIZE,
                shape=(max_entries, dim),
            )
            self._file_size = total_size

            # Write initial meta
            await self._save_meta()

        except Exception as e:
            logger.warning(f"[EmbedCache] memmap create failed: {e}")
            self.stats.memmap_errors += 1
            raise

    async def _load_meta(self) -> None:
        """Load metadata and LRU state from disk."""
        try:
            if self._meta_path.exists():
                with open(self._meta_path) as f:
                    meta = json.load(f)

                # Rebuild L1 from free_list
                max_entries = meta.get("max_entries", self._max_shape()[0])
                free_list = set(meta.get("free_list", []))

                # All non-free slots are used
                all_slots = set(range(max_entries))
                used_slots = all_slots - free_list

                for slot_idx in used_slots:
                    offset = slot_idx * self.dim * 2  # byte offset
                    self._l1[f"_slot_{slot_idx}"] = CacheEntry(
                        offset=offset,
                        length=self.dim,
                        mtime=meta.get("slot_mtimes", {}).get(str(slot_idx), 0),
                        text_hash=f"_slot_{slot_idx}",
                    )
        except Exception as e:
            logger.debug(f"[EmbedCache] meta load failed (recreating): {e}")

    async def _save_meta(self) -> None:
        """Save metadata and LRU state to disk."""
        try:
            free_slots = []
            slot_mtimes = {}

            for key, entry in self._l1.items():
                if key.startswith("_slot_"):
                    slot_idx = int(key.split("_slot_")[1])
                    free_slots.append(slot_idx)
                    slot_mtimes[str(slot_idx)] = entry.mtime

            meta = {
                "version": self.VERSION,
                "dim": self.dim,
                "max_entries": self._max_shape()[0],
                "free_list": free_slots,
                "used": len(self._l1),
                "slot_mtimes": slot_mtimes,
            }

            with open(self._meta_path, "w") as f:
                json.dump(meta, f)
        except Exception as e:
            logger.debug(f"[EmbedCache] meta save failed (non-fatal): {e}")

    def _hash_text(self, text: str) -> str:
        """SHA256 hash of text for cache key."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get_or_encode(
        self,
        text: str,
        encode_fn: Any = None,
    ) -> np.ndarray | None:
        """
        Get embedding from cache or encode via MLX.

        Args:
            text: Text to embed
            encode_fn: Async callable that returns np.ndarray(dim). If None, uses MLX.

        Returns:
            Embedding vector (float16 np.ndarray) or None on failure
        """
        text_hash = self._hash_text(text)

        # Try L1 first
        async with self._l1_lock:
            entry = self._l1.get(text_hash)

        if entry is not None:
            self.stats.hits += 1
            self.stats.l1_hits += 1
            entry.mtime = asyncio.get_running_loop().time()
            return await self._read_memmap(entry)

        # Try L2 (memmap directly indexed by hash)
        l2_entry = await self._l2_lookup(text_hash)
        if l2_entry is not None:
            self.stats.hits += 1
            self.stats.l2_hits += 1
            # Promote to L1
            async with self._l1_lock:
                self._l1[text_hash] = l2_entry
            l2_entry.mtime = asyncio.get_running_loop().time()
            return await self._read_memmap(l2_entry)

        # Cache miss: encode
        self.stats.misses += 1

        try:
            if encode_fn is not None:
                embedding = await encode_fn(text)
            else:
                # Default: use MLXEmbeddingManager
                embedding = await self._default_encode(text)

            if embedding is None:
                return None

            # Store in cache
            await self.set(text, embedding)
            return embedding

        except Exception as e:
            logger.warning(f"[EmbedCache] encode failed: {e}")
            self.stats.encode_errors += 1
            return None

    async def _default_encode(self, text: str) -> np.ndarray:
        """Default encoding via MLXEmbeddingManager."""
        # Import here to avoid circular deps and lazy load
        from hledac.universal.core.mlx_embeddings import get_mlx_embedder

        mgr = get_mlx_embedder()
        # encode() is sync but may call _load_model() which is thread-safe
        result = mgr.encode(text, normalize=True, truncate_dim=self.dim)
        return result

    async def _l2_lookup(self, text_hash: str) -> CacheEntry | None:
        """Look up entry in memmap by text hash (linear scan of L1 is L2)."""
        # L2 is a full scan of L1 entries by text_hash
        # Since L1 maps hash -> entry, we already have L2 in L1
        # This is for entries that are in memmap but not in L1
        async with self._l1_lock:
            return self._l1.get(text_hash)

    async def _read_memmap(self, entry: CacheEntry) -> np.ndarray | None:
        """Read embedding from memmap file."""
        if self._mmap is None:
            return None

        try:
            slot_idx = entry.offset // (self.dim * 2)
            vec = self._mmap[slot_idx].copy()
            return vec.astype(np.float32)  # Return float32 for compatibility
        except Exception as e:
            logger.debug(f"[EmbedCache] memmap read failed: {e}")
            self.stats.memmap_errors += 1
            return None

    async def set(self, text: str, embedding: np.ndarray) -> bool:
        """
        Store embedding in cache.

        Args:
            text: Original text
            embedding: Embedding vector (np.ndarray)

        Returns:
            True if stored, False on failure
        """
        text_hash = self._hash_text(text)
        vec = embedding.astype(np.float16)  # Store as float16

        if vec.shape[0] != self.dim:
            logger.warning(f"[EmbedCache] dim mismatch: {vec.shape[0]} != {self.dim}")
            return False

        try:
            # Find free slot
            slot_idx = await self._allocate_slot()
            if slot_idx is None:
                # Evict LRU and retry
                await self._evict_lru()
                slot_idx = await self._allocate_slot()
                if slot_idx is None:
                    logger.debug("[EmbedCache] cache full after eviction")
                    return False

            # Write to memmap
            if self._mmap is not None:
                self._mmap[slot_idx] = vec
                if hasattr(self._mmap, "flush"):
                    self._mmap.flush()

            # Update L1
            entry = CacheEntry(
                offset=slot_idx * self.dim * 2,
                length=self.dim,
                mtime=asyncio.get_running_loop().time(),
                text_hash=text_hash,
            )
            async with self._l1_lock:
                # Evict if at capacity
                if len(self._l1) >= self.max_entries:
                    await self._evict_lru()
                self._l1[text_hash] = entry

            # Save meta periodically (every 100 entries)
            if len(self._l1) % 100 == 0:
                await self._save_meta()

            return True

        except Exception as e:
            logger.warning(f"[EmbedCache] set failed: {e}")
            self.stats.memmap_errors += 1
            return False

    async def _allocate_slot(self) -> int | None:
        """Allocate a free slot in memmap. Returns slot index or None."""
        try:
            if self._meta_path.exists():
                with open(self._meta_path) as f:
                    meta = json.load(f)
                free_list = meta.get("free_list", [])
                if free_list:
                    slot = free_list.pop()
                    with open(self._meta_path, "w") as f:
                        json.dump(meta, f)
                    return slot
        except Exception:
            pass
        return None

    async def _evict_lru(self) -> None:
        """Evict least recently used entry from L1."""
        async with self._l1_lock:
            if not self._l1:
                return

            # Find LRU entry
            lru_key = min(self._l1.keys(), key=lambda k: self._l1[k].mtime)
            entry = self._l1.pop(lru_key)

            # Mark slot as free in meta
            try:
                if self._meta_path.exists():
                    with open(self._meta_path) as f:
                        meta = json.load(f)
                    slot_idx = entry.offset // (self.dim * 2)
                    meta.setdefault("free_list", []).append(slot_idx)
                    with open(self._meta_path, "w") as f:
                        json.dump(meta, f)
            except Exception as e:
                logger.debug(f"[EmbedCache] slot free failed: {e}")

            self.stats.evictions += 1

    async def clear(self) -> None:
        """Clear all cache layers."""
        async with self._l1_lock:
            self._l1.clear()

        if self._mmap is not None:
            del self._mmap
            self._mmap = None

        try:
            if self._memmap_path.exists():
                self._memmap_path.unlink()
            if self._meta_path.exists():
                self._meta_path.unlink()
        except Exception as e:
            logger.debug(f"[EmbedCache] clear failed: {e}")

        # Reset stats
        self.stats = CacheStats()
        logger.info("[EmbedCache] cleared")

    def get_stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return self.stats.to_dict()

    async def close(self) -> None:
        """Close cache and flush pending writes."""
        await self._save_meta()
        if self._mmap is not None:
            del self._mmap
            self._mmap = None


# === Global cache singleton ===
_cache: EmbeddingCache | None = None
_cache_lock = asyncio.Lock()


async def get_embedding_cache(dim: int = 256) -> EmbeddingCache:
    """Get or create global embedding cache singleton."""
    global _cache
    async with _cache_lock:
        if _cache is None:
            _cache = EmbeddingCache(dim=dim)
        return _cache


def get_embedding_cache_stats() -> dict[str, Any]:
    """Get stats from global cache."""
    global _cache
    if _cache is None:
        return {}
    return _cache.get_stats()
