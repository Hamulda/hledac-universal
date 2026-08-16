"""
Embedding Cache — np.memmap int8/float16 two-layer LRU for M1 8GB UMA.

Architecture:



- Layer 1: LRU in-memory dict (fastest)
- Layer 2: np.memmap file for persistence (int8 + scale or float16)
- Fallback: MLXEmbeddingManager.encode()

ISSUE #022 INT8 QUANTIZATION:
- Per-axis int8 quantization with absmax scaling: 4× smaller than float32
- int8: 256×1B + 4B scale = 260B per entry vs float16: 512B
- M1 8GB: ~512MB max = ~340k→~2M entries (4× capacity increase)
- Backward compatible with float16 entries (auto-detected via VERSION in header)

Key invariants:
- Always-on, no feature flags
- Fail-safe: if memmap fails, fall back to pure encode
- Bounded: MAX_ENTRIES (soft cap) + MAX_BYTES (hard cap ~512MB)
- asyncio.Lock for thread-safety in async context
"""
from __future__ import annotations

from ._shared import get_cache_lock_async as _get_cache_lock_async

try:
    import orjson

    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False

import asyncio
import gc
import hashlib
import logging

try:
    import xxhash
    XXHASH_AVAILABLE = True
except ImportError:
    XXHASH_AVAILABLE = False
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import msgspec
import numpy as np

from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str
from _core._util import aclose

try:
    from hledac.universal.utils.msgspec_json import decode as _msgspec_decode, encode as _msgspec_encode
except ImportError:
    _msgspec_decode = cast(Any, None)
    _msgspec_encode = cast(Any, None)

logger = logging.getLogger(__name__)

_EMBED_CACHE_DIR = Path.home() / ".hledac" / "cache" / "embeddings"
_MAX_ENTRIES = 100000
_MAX_BYTES = 512 * 1024 * 1024
_HEADER_SIZE = 4096
_ENTRY_OVERHEAD = 128

# ISSUE #022: Version 2 = int8+scale format
_VERSION_INT8 = 2
_VERSION_FLOAT16 = 1
_CACHE_VERSION = _VERSION_INT8


# ---------------------------------------------------------------------------
# Int8 quantization helpers (ISSUE #022)
# ---------------------------------------------------------------------------


def _quantize_int8(emb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Per-axis int8 quantization with absmax scaling.

    Maps float32 ∈ [-absmax, +absmax] → int8 ∈ [-127, 127].
    Scale factor = absmax / 127.0 stored alongside (4B float32).

    Memory: 256 × 1B + 4B = 260B vs float16: 512B (≈2× smaller).

    Args:
        emb: float32/float64 embedding vector, shape (dim,) or (1, dim)

    Returns:
        (int8_vector, scale): quantized vector (int8) and scale factor (float32)
    """
    emb_f = emb.astype(np.float32).flatten()
    absmax = np.abs(emb_f).max()
    if absmax < 1e-8:
        scale = np.array([1.0], dtype=np.float32)
    else:
        scale = np.array([absmax / 127.0], dtype=np.float32)

    int8_vec = np.clip(
        np.round(emb_f / scale[0]), -127.0, 127.0
    ).astype(np.int8)
    return int8_vec, scale


def _dequantize_int8(int8_vec: np.ndarray, scale: np.ndarray) -> np.ndarray:
    """
    Dequantize int8 vector back to float32.

    Args:
        int8_vec: quantized vector, shape (dim,)
        scale: scale factor from _quantize_int8, shape (1,)

    Returns:
        float32 dequantized vector, shape (dim,)
    """
    return (int8_vec.astype(np.float32) * scale[0]).astype(np.float32)


# ---------------------------------------------------------------------------
# Cache stats
# ---------------------------------------------------------------------------


class CacheStats(msgspec.Struct, gc=False):
    hits: int = 0
    misses: int = 0
    l1_hits: int = 0
    l2_hits: int = 0
    evictions: int = 0
    memmap_errors: int = 0
    encode_errors: int = 0
    int8_stores: int = 0  # ISSUE #022: count of int8 stores
    int8_reads: int = 0  # ISSUE #022: count of int8 reads
    float16_fallback_reads: int = 0  # ISSUE #022: legacy float16 reads

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
            "int8_stores": self.int8_stores,
            "int8_reads": self.int8_reads,
            "float16_fallback_reads": self.float16_fallback_reads,
            "hit_rate": round(self.hit_rate, 4),
        }


# ISSUE #022: CacheEntry now mutable (no frozen=True) to allow mtime updates
class CacheEntry(msgspec.Struct, gc=False):
    offset: int
    length: int  # embedding dimension (256)
    mtime: float
    text_hash: str
    scale: np.ndarray | None = None  # ISSUE #022: scale factor for int8 (None=float16)


# ---------------------------------------------------------------------------
# EmbeddingCache
# ---------------------------------------------------------------------------


class EmbeddingCache:
    """
    Two-layer embedding cache with np.memmap backing.

    Layer 1: in-memory dict[hash -> CacheEntry] with LRU eviction
    Layer 2: np.memmap file for persistence

    ISSUE #022: Supports BOTH int8+scale (VERSION=2, default) and
    float16 (VERSION=1, legacy) entries. Auto-detected on read via
    the 'version' field in the memmap header.

    Thread-safe for async use via asyncio.Lock.

    M1 8GB bounds:
    - int8: 260B/entry → ~2M entries max in 512MB
    - float16: 512B/entry → ~1M entries max in 512MB
    """

    VERSION = _CACHE_VERSION  # ISSUE #022: = 2 (int8+scale)

    # bytes per entry: VERSION=2 (int8+scale) vs VERSION=1 (float16)
    _FLOAT16_BYTES_PER_ENTRY = 512  # float16 vec (always 512 regardless of dim)

    def _int8_bytes_per_entry(self) -> int:
        """Dynamic int8 bytes per entry: dim bytes + 4B scale."""
        return self.dim + 4

    def _float16_bytes_per_entry(self) -> int:
        """Dynamic float16 bytes per entry."""
        return self.dim * 2  # 2 bytes per float16 element

    __slots__ = tuple(
        (
            "_file_size",
            "_hash_index",  # E-5 FIX: text_hash → slot_idx for real L2
            "_free_list",  # E-10 FIX: in-memory free_list — O(1) alloc/evict
            "_l1",
            "_l1_lock",
            "_memmap_path",
            "_meta_path",
            "_mmap",
            "_mmap_init_lock",  # E-4 FIX: threading.Lock for sync init
            "_mmap_lock",
            "_pending_meta_save",  # E-10 FIX: coalesced meta flush
            "_version",
            "cache_dir",
            "dim",
            "max_bytes",
            "max_entries",
            "stats",
    )
    )

    def __init__(
        self,
        dim: int = 256,
        max_entries: int = _MAX_ENTRIES,
        max_bytes: int = _MAX_BYTES,
        cache_dir: Path | None = None,
    ):
        self.dim = dim
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.cache_dir = cache_dir or _EMBED_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memmap_path = self.cache_dir / f"embeddings_d{dim}.dat"
        self._meta_path = self.cache_dir / f"embeddings_d{dim}.meta.json"
        self._l1: dict[str, CacheEntry] = {}
        self._hash_index: dict[str, int] = {}  # E-5: text_hash → slot_idx
        # E-10 FIX: in-memory free_list — O(1) alloc/evict, no per-op I/O
        self._free_list: list[int] = []
        self._pending_meta_save: bool = False  # E-10: coalesced flush
        # ISSUE-2984: lazy lock — NEVER asyncio.Lock() at __init__ (macOS crash vector)
        self._l1_lock: asyncio.Lock | None = None
        self._mmap: np.memmap | None = None
        self._mmap_lock: asyncio.Lock | None = None
        self.stats = CacheStats()
        self._file_size = 0
        self._version: int = _CACHE_VERSION
        # E-4 FIX: asyncio.Runner() removed — raises RuntimeError when called from
        # within an existing event loop (e.g. async module init). Replaced by
        # sync _init_memmap_sync() using threading.Lock (no event loop required).
        self._mmap_init_lock = threading.Lock()
        self._init_memmap_sync()

    # ISSUE-2984: lazy lock helpers — NEVER instantiate asyncio.Lock() in __init__
    async def _get_l1_lock(self) -> asyncio.Lock:
        """Lazily create _l1_lock inside an event loop."""
        if self._l1_lock is None:
            self._l1_lock = asyncio.Lock()
        return self._l1_lock

    async def _get_mmap_lock(self) -> asyncio.Lock:
        """Lazily create _mmap_lock inside an event loop."""
        if self._mmap_lock is None:
            self._mmap_lock = asyncio.Lock()
        return self._mmap_lock

    # E-4 FIX: sync version for __init__ — threading.Lock (no event loop)
    def _init_memmap_sync(self) -> None:
        """Synchronous memmap init (thread-safe via _mmap_init_lock)."""
        with self._mmap_init_lock:
            try:
                if self._memmap_path.exists():
                    self._load_meta_sync()
                    file_size = self._memmap_path.stat().st_size
                    read_size = min(_HEADER_SIZE, file_size)
                    header_arr = np.memmap(
                        str(self._memmap_path),
                        dtype=np.uint8,
                        mode="r",
                        shape=(read_size,),
    )
                    raw_header = bytes(header_arr)
                    try:
                        decoded = msgspec.json.decode(raw_header)
                        self._version = decoded.get("version", _VERSION_FLOAT16)
                    except Exception:
                        self._version = _VERSION_FLOAT16

                    if self._version >= _VERSION_INT8:
                        self._mmap = np.memmap(
                            str(self._memmap_path),
                            dtype=np.int8,
                            mode="r+",
                            offset=_HEADER_SIZE,
                            shape=(self._max_shape()[0], self._int8_bytes_per_entry()),
    )
                    else:
                        self._mmap = np.memmap(
                            str(self._memmap_path),
                            dtype=np.float16,
                            mode="r+",
                            offset=_HEADER_SIZE,
                            shape=(self._max_shape()[0], self.dim),
    )
                    self._file_size = self._memmap_path.stat().st_size
                    logger.info(
                        f"[EmbedCache] Opened memmap v{self._version}: "
                        f"{self._file_size / 1024 / 1024:.1f} MB"
    )
                else:
                    self._create_memmap_sync()
                    logger.info(f"[EmbedCache] Created new memmap v{self._version}")
            except Exception as e:
                logger.warning(
                    f"[EmbedCache] memmap init failed (fallback to encode-only): {e}"
    )
                self.stats.memmap_errors += 1
                self._mmap = None

    def _load_meta_sync(self) -> None:
        """Sync version of _load_meta for __init__ — also rebuilds _hash_index + _free_list."""
        try:
            if self._meta_path.exists():
                content = self._meta_path.read_text()
                meta = _msgspec_decode(content) if _msgspec_decode else msgspec.json.decode(content)
                max_entries = meta.get("max_entries", self._max_shape()[0])
                self._version = meta.get("version", _VERSION_INT8)
                free_list = set(meta.get("free_list", []))
                all_slots = set(range(max_entries))
                used_slots = all_slots - free_list
                entry_bytes = self._bytes_per_entry()
                has_scale = self._version >= _VERSION_INT8
                slot_scales = meta.get("slot_scales", {})
                # E-5: rebuild hash_index from meta
                self._hash_index: dict[str, int] = {}
                for slot_idx in used_slots:
                    offset = slot_idx * entry_bytes
                    # BUG FIX: restore actual scale from slot_scales, not default [1.0]
                    scale_val = slot_scales.get(str(slot_idx)) if has_scale else None
                    scale = np.array([scale_val], dtype=np.float32) if scale_val is not None else None
                    th = meta.get("slot_hashes", {}).get(str(slot_idx), f"_slot_{slot_idx}")
                    self._hash_index[th] = slot_idx
                    self._l1[f"_slot_{slot_idx}"] = CacheEntry(
                        offset=offset,
                        length=self.dim,
                        mtime=meta.get("slot_mtimes", {}).get(str(slot_idx), 0),
                        text_hash=th,
                        scale=scale,
    )
                # E-10 FIX: populate in-memory free_list from meta — O(1) alloc after this
                self._free_list = sorted(free_list)
                logger.debug(
                    f"[EmbedCache] meta loaded: {len(used_slots)} slots, "
                    f"hash_index size={len(self._hash_index)}, free_list size={len(self._free_list)}"
    )
        except Exception as e:
            logger.debug(f"[EmbedCache] meta load failed (recreating): {e}")

    def _save_meta_sync(self) -> None:
        """Sync version of _save_meta for use in _create_memmap_sync."""
        try:
            slot_mtimes = {}
            slot_hashes = {}
            slot_scales = {}
            for key, entry in self._l1.items():
                if key.startswith("_slot_"):
                    slot_idx = int(key.split("_slot_")[1])
                    slot_mtimes[str(slot_idx)] = entry.mtime
                    slot_hashes[str(slot_idx)] = entry.text_hash
                    if entry.scale is not None and self._version >= _VERSION_INT8:
                        slot_scales[str(slot_idx)] = float(entry.scale[0])
                    # None scale for int8 = corruption signal; omit from meta so load
                    # defaults to [1.0] fallback (harmless identity dequantization)
            meta = {
                "version": self._version,
                "dim": self.dim,
                "max_entries": self._max_shape()[0],
                "free_list": self._free_list,  # E-10: use in-memory free_list
                "used": len(self._l1),
                "slot_mtimes": slot_mtimes,
                "slot_hashes": slot_hashes,  # E-5: persist hash_index
                "slot_scales": slot_scales,  # BUG FIX: persist per-slot scales for int8
                "format": "int8_scale" if self._version >= _VERSION_INT8 else "float16",
            }
            if _msgspec_encode:
                data = _msgspec_encode(meta).decode()
                mode = "w"
            else:
                data = orjson.dumps(meta).decode() if ORJSON_AVAILABLE else str(meta)
                mode = "w"
            with open(self._meta_path, mode) as f:
                f.write(data)
            self._pending_meta_save = False
        except Exception as e:
            logger.debug(f"[EmbedCache] meta save failed (non-fatal): {e}")

    def _create_memmap_sync(self) -> None:
        """Sync version of _create_memmap for __init__."""
        max_entries, entry_bytes = self._max_shape()
        total_size = _HEADER_SIZE + max_entries * entry_bytes
        try:
            with open(self._memmap_path, "wb") as f:
                header = {
                    "version": self.VERSION,
                    "dim": self.dim,
                    "max_entries": max_entries,
                    "free_list": list(range(max_entries)),
                    "used": 0,
                    "format": "int8_scale",
                }
                if ORJSON_AVAILABLE:
                    _header_str = _msgspec_dumps_str(header)
                    header_bytes: bytes = _header_str.encode()
                else:
                    header_bytes = str(header).encode()
                padding = _HEADER_SIZE - len(header_bytes)
                if padding > 0:
                    header_bytes = header_bytes + b"\x00" * padding
                f.write(header_bytes)
                f.seek(total_size - 1)
                f.write(b"\x00")

            self._mmap = np.memmap(
                str(self._memmap_path),
                dtype=np.int8,
                mode="r+",
                offset=_HEADER_SIZE,
                shape=(max_entries, entry_bytes),
    )
            self._file_size = total_size
            self._version = _VERSION_INT8
            # E-10 FIX: init free_list for new cache, set pending to flush meta
            self._free_list = []
            self._pending_meta_save = True
            self._save_meta_sync()
        except Exception as e:
            logger.warning(f"[EmbedCache] memmap create failed: {e}")
            self.stats.memmap_errors += 1
            raise

    def _bytes_per_entry(self) -> int:
        """ISSUE #022: Return bytes per entry based on version."""
        return self._int8_bytes_per_entry() if self._version >= _VERSION_INT8 else self._float16_bytes_per_entry()

    async def _init_memmap(self) -> None:
        async with await self._get_mmap_lock():
            try:
                if self._memmap_path.exists():
                    await self._load_meta()
                    # ISSUE #022 fix: read full _HEADER_SIZE, pad if needed for msgspec
                    file_size = self._memmap_path.stat().st_size
                    read_size = min(_HEADER_SIZE, file_size)
                    header_arr = np.memmap(
                        str(self._memmap_path),
                        dtype=np.uint8,
                        mode="r",
                        shape=(read_size,),
    )
                    raw_header = bytes(header_arr)
                    # msgspec.json.decode needs full JSON — raw_header is already padded with \x00
                    try:
                        decoded = msgspec.json.decode(raw_header)
                        self._version = decoded.get("version", _VERSION_FLOAT16)
                    except Exception:
                        self._version = _VERSION_FLOAT16

                    entry_bytes = self._bytes_per_entry()
                    if self._version >= _VERSION_INT8:
                        self._mmap = np.memmap(
                            str(self._memmap_path),
                            dtype=np.int8,
                            mode="r+",
                            offset=_HEADER_SIZE,
                            shape=(self._max_shape()[0], self._int8_bytes_per_entry()),
    )
                    else:
                        self._mmap = np.memmap(
                            str(self._memmap_path),
                            dtype=np.float16,
                            mode="r+",
                            offset=_HEADER_SIZE,
                            shape=(self._max_shape()[0], self.dim),
    )
                    self._file_size = self._memmap_path.stat().st_size
                    logger.info(
                        f"[EmbedCache] Opened memmap v{self._version}: "
                        f"{self._file_size / 1024 / 1024:.1f} MB, "
                        f"bytes_per_entry={entry_bytes}"
    )
                else:
                    await self._create_memmap()
                    logger.info(f"[EmbedCache] Created new memmap v{self._version}")
            except Exception as e:
                logger.warning(
                    f"[EmbedCache] memmap init failed (fallback to encode-only): {e}"
    )
                self.stats.memmap_errors += 1
                self._mmap = None

    def _max_shape(self) -> tuple[int, int]:
        entry_bytes = self._int8_bytes_per_entry()  # always use int8 for max_shape
        max_entries = min(self.max_entries, self.max_bytes // entry_bytes)
        return (max(max_entries, 1000), self._int8_bytes_per_entry())

    async def _create_memmap(self) -> None:
        max_entries, entry_bytes = self._max_shape()
        total_size = _HEADER_SIZE + max_entries * entry_bytes
        try:
            with open(self._memmap_path, "wb") as f:
                header = {
                    "version": self.VERSION,
                    "dim": self.dim,
                    "max_entries": max_entries,
                    "free_list": list(range(max_entries)),
                    "used": 0,
                    "format": "int8_scale",  # ISSUE #022: explicit format marker
                }
                # ISSUE #022 fix: always produce bytes, handle str from msgspec
                if ORJSON_AVAILABLE:
                    _header_str = _msgspec_dumps_str(header)
                    header_bytes: bytes = _header_str.encode()
                else:
                    header_bytes = str(header).encode()
                # Pad with null bytes to _HEADER_SIZE
                padding = _HEADER_SIZE - len(header_bytes)
                if padding > 0:
                    header_bytes = header_bytes + b"\x00" * padding
                f.write(header_bytes)
                f.seek(total_size - 1)
                f.write(b"\x00")

            self._mmap = np.memmap(
                str(self._memmap_path),
                dtype=np.int8,
                mode="r+",
                offset=_HEADER_SIZE,
                shape=(max_entries, entry_bytes),
    )
            self._file_size = total_size
            self._version = _VERSION_INT8
            # E-10 FIX: init free_list for new cache, set pending to flush meta
            self._free_list = []
            self._pending_meta_save = True
            await self._save_meta()
        except Exception as e:
            logger.warning(f"[EmbedCache] memmap create failed: {e}")
            self.stats.memmap_errors += 1
            raise

    async def _load_meta(self) -> None:
        try:
            if self._meta_path.exists():
                content = await asyncio.to_thread(self._meta_path.read_text)
                meta = _msgspec_decode(content) if _msgspec_decode else msgspec.json.decode(content)
                max_entries = meta.get("max_entries", self._max_shape()[0])
                self._version = meta.get("version", _VERSION_INT8)
                free_list = set(meta.get("free_list", []))
                all_slots = set(range(max_entries))
                used_slots = all_slots - free_list
                entry_bytes = self._bytes_per_entry()
                has_scale = self._version >= _VERSION_INT8
                slot_scales = meta.get("slot_scales", {})
                # E-5: rebuild hash_index from meta
                self._hash_index: dict[str, int] = {}
                for slot_idx in used_slots:
                    offset = slot_idx * entry_bytes
                    # BUG FIX: restore actual scale from slot_scales, not default [1.0]
                    scale_val = slot_scales.get(str(slot_idx)) if has_scale else None
                    scale = np.array([scale_val], dtype=np.float32) if scale_val is not None else None
                    th = meta.get("slot_hashes", {}).get(str(slot_idx), f"_slot_{slot_idx}")
                    self._hash_index[th] = slot_idx
                    self._l1[f"_slot_{slot_idx}"] = CacheEntry(
                        offset=offset,
                        length=self.dim,
                        mtime=meta.get("slot_mtimes", {}).get(str(slot_idx), 0),
                        text_hash=th,
                        scale=scale,
    )
                # E-10 FIX: populate in-memory free_list from meta
                self._free_list = sorted(free_list)
        except Exception as e:
            logger.debug(f"[EmbedCache] meta load failed (recreating): {e}")

    async def _save_meta(self) -> None:
        """E-10 FIX: coalesced meta flush — only write if pending_meta_save is set."""
        if not self._pending_meta_save:
            return
        try:
            slot_mtimes = {}
            slot_hashes = {}
            slot_scales = {}
            for key, entry in self._l1.items():
                if key.startswith("_slot_"):
                    slot_idx = int(key.split("_slot_")[1])
                    slot_mtimes[str(slot_idx)] = entry.mtime
                    slot_hashes[str(slot_idx)] = entry.text_hash
                    if entry.scale is not None and self._version >= _VERSION_INT8:
                        slot_scales[str(slot_idx)] = float(entry.scale[0])
                    # None scale for int8 = corruption signal; omit from meta so load
                    # defaults to [1.0] fallback (harmless identity dequantization)
            meta = {
                "version": self._version,
                "dim": self.dim,
                "max_entries": self._max_shape()[0],
                "free_list": self._free_list,  # E-10: use in-memory free_list
                "used": len(self._l1),
                "slot_mtimes": slot_mtimes,
                "slot_hashes": slot_hashes,  # E-5: persist hash_index
                "slot_scales": slot_scales,  # BUG FIX: persist per-slot scales for int8
                "format": "int8_scale" if self._version >= _VERSION_INT8 else "float16",
            }
            def _write_sync():
                if _msgspec_encode:
                    data: str | bytes = _msgspec_encode(meta).decode()
                    mode = "w"
                else:
                    data = orjson.dumps(meta)
                    mode = "wb"
                with open(self._meta_path, mode) as f:
                    f.write(data)
            await asyncio.to_thread(_write_sync)
            self._pending_meta_save = False
        except Exception as e:
            logger.debug(f"[EmbedCache] meta save failed (non-fatal): {e}")

    def _hash_text(self, text: str) -> str:
        """
        E-36 FIX: Use xxhash.xxh3_64_hexdigest for non-cryptographic hashing.
        xxhash is ~10x faster than SHA-256 for cache key hashing (no security needed here).
        Falls back to SHA-256 if xxhash is unavailable.
        """
        if XXHASH_AVAILABLE:
            return xxhash.xxh3_64_hexdigest(text)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def get_or_encode(
        self, text: str, encode_fn: Any = None
    ) -> np.ndarray | None:
        text_hash = self._hash_text(text)
        async with await self._get_l1_lock():
            entry = self._l1.get(text_hash)
        if entry is not None:
            self.stats.hits += 1
            self.stats.l1_hits += 1
            entry.mtime = asyncio.get_running_loop().time()
            return await self._read_memmap(entry)
        l2_entry = await self._l2_lookup(text_hash)
        if l2_entry is not None:
            self.stats.hits += 1
            self.stats.l2_hits += 1
            async with await self._get_l1_lock():
                self._l1[text_hash] = l2_entry
            l2_entry.mtime = asyncio.get_running_loop().time()
            return await self._read_memmap(l2_entry)
        self.stats.misses += 1
        try:
            if encode_fn is not None:
                embedding = await encode_fn(text)
            else:
                embedding = await self._default_encode(text)
            if embedding is None:
                return None
            await self.set(text, embedding)
            return embedding
        except Exception as e:
            logger.warning(f"[EmbedCache] encode failed: {e}")
            self.stats.encode_errors += 1
            return None

    async def _default_encode(self, text: str) -> np.ndarray:
        from hledac.universal._core.embeddings.manager import get_mlx_embedder

        mgr = get_mlx_embedder()
        result = mgr.encode(text, normalize=True, truncate_dim=self.dim)
        return result

    async def _l2_lookup(self, text_hash: str) -> CacheEntry | None:
        # E-5 FIX: real L2 lookup using _hash_index, not just L1 re-query
        async with await self._get_l1_lock():
            slot_idx = self._hash_index.get(text_hash)
            if slot_idx is None:
                return None
            return self._l1.get(f"_slot_{slot_idx}")

    async def _read_memmap(self, entry: CacheEntry) -> np.ndarray | None:
        if self._mmap is None:
            return None
        try:
            entry_bytes = self._bytes_per_entry()
            slot_idx = entry.offset // entry_bytes
            if self._version >= _VERSION_INT8:
                # ISSUE #022: int8 + scale format
                self.stats.int8_reads += 1
                raw = self._mmap[slot_idx]
                int8_vec = raw[: self.dim].astype(np.int8)
                # scale is bytes 256-259: little-endian float32
                scale_bytes = raw[self.dim : self.dim + 4].tobytes()
                scale = np.frombuffer(scale_bytes, dtype=np.float32)
                if scale[0] < 1e-8:
                    scale[0] = 1.0
                return _dequantize_int8(int8_vec, scale)
            else:
                # Legacy float16 format
                self.stats.float16_fallback_reads += 1
                vec = self._mmap[slot_idx].copy()
                return vec.astype(np.float32)
        except Exception as e:
            logger.debug(f"[EmbedCache] memmap read failed: {e}")
            self.stats.memmap_errors += 1
            return None

    async def set(self, text: str, embedding: np.ndarray) -> bool:
        text_hash = self._hash_text(text)
        if embedding.shape[0] != self.dim:
            logger.warning(
                f"[EmbedCache] dim mismatch: {embedding.shape[0]} != {self.dim}"
    )
            return False
        try:
            slot_idx = await self._allocate_slot()
            if slot_idx is None:
                await self._evict_lru()
                slot_idx = await self._allocate_slot()
                if slot_idx is None:
                    logger.debug("[EmbedCache] cache full after eviction")
                    return False

            if self._mmap is not None:
                entry_bytes = self._bytes_per_entry()
                slot_offset = slot_idx * entry_bytes
                if self._version >= _VERSION_INT8:
                    # ISSUE #022: int8 + scale quantization
                    self.stats.int8_stores += 1
                    int8_vec, scale = _quantize_int8(embedding)
                    # Write int8 vector (256B)
                    self._mmap[slot_idx, : self.dim] = int8_vec
                    # Write scale factor (4B) at offset 256 — store as raw int8 bytes (float32 layout)
                    scale_bytes = scale.tobytes()
                    self._mmap[slot_idx, self.dim : self.dim + 4] = np.frombuffer(
                        scale_bytes, dtype=np.int8
    )
                    entry = CacheEntry(
                        offset=slot_offset,
                        length=self.dim,
                        mtime=asyncio.get_running_loop().time(),
                        text_hash=text_hash,
                        scale=scale,
    )
                else:
                    # Legacy float16
                    vec_f16 = embedding.astype(np.float16)
                    self._mmap[slot_idx] = vec_f16
                    entry = CacheEntry(
                        offset=slot_offset,
                        length=self.dim,
                        mtime=asyncio.get_running_loop().time(),
                        text_hash=text_hash,
                        scale=None,
    )

                if hasattr(self._mmap, "flush"):
                    self._mmap.flush()

                async with await self._get_l1_lock():
                    if len(self._l1) >= self.max_entries:
                        await self._evict_lru()
                    self._l1[text_hash] = entry
                    self._hash_index[text_hash] = slot_idx  # E-5: L2 index

                # E-10 FIX: removed periodic _save_meta every 100 inserts.
                # Meta is now coalesced — only written on close() or explicit flush.
                return True
            return False
        except Exception as e:
            logger.warning(f"[EmbedCache] set failed: {e}")
            self.stats.memmap_errors += 1
            return False

    async def _allocate_slot(self) -> int | None:
        """E-10 FIX: O(1) in-memory free_list pop — no per-op meta I/O."""
        try:
            if self._free_list:
                slot = self._free_list.pop()
                self._pending_meta_save = True
                return slot
            # Fallback: no free slots — evict will repopulate
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _evict_lru(self) -> None:
        """E-10 FIX: O(1) in-memory free_list append — no per-evict meta I/O."""
        async with await self._get_l1_lock():
            if not self._l1:
                return
            lru_key = min(self._l1.keys(), key=lambda k: self._l1[k].mtime)
            entry = self._l1.pop(lru_key)
            # E-5: also remove from L2 hash_index
            self._hash_index.pop(entry.text_hash, None)
            try:
                entry_bytes = self._bytes_per_entry()
                slot_idx = entry.offset // entry_bytes
                # E-10 FIX: update in-memory free_list, set pending flag
                self._free_list.append(slot_idx)
                self._pending_meta_save = True
            except Exception as e:
                logger.debug(f"[EmbedCache] slot free failed: {e}")
            self.stats.evictions += 1

    async def clear(self) -> None:
        async with await self._get_l1_lock():
            self._l1.clear()
            self._hash_index.clear()
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
        self.stats = CacheStats()
        logger.info("[EmbedCache] cleared")

    def clear_sync(self) -> None:
        """Synchronous clear for use by GlobalCacheRegistry (Issue #16).

        Clears L1 and resets mmap without awaiting locks.
        """
        self._l1.clear()
        self._hash_index.clear()
        if self._mmap is not None:
            self._mmap.flush()  # E-37 twin: flush before del — same data-loss guard as close()
            del self._mmap
            self._mmap = None
        try:
            if self._memmap_path.exists():
                self._memmap_path.unlink()
            if self._meta_path.exists():
                self._meta_path.unlink()
        except Exception as e:
            logger.debug(f"[EmbedCache] clear_sync failed: {e}")
        self.stats = CacheStats()
        logger.debug("[EmbedCache] clear_sync complete")

    def get_stats(self) -> dict[str, Any]:
        return self.stats.to_dict()

    async def close(self) -> None:
        await self._save_meta()
        if self._mmap is not None:
            self._mmap.flush()  # E-37: flush before del — data loss guard between writes and close
            del self._mmap
            self._mmap = None


_cache: EmbeddingCache | None = None


async def get_embedding_cache(dim: int = 256) -> EmbeddingCache:
    global _cache
    async with await _get_cache_lock_async():
        if _cache is None:
            _cache = EmbeddingCache(dim=dim)
            # Issue #16: Register with GlobalCacheRegistry for winddown clear_all
            try:
                from hledac.universal._core.global_cache_registry import register_cache
                register_cache(
                    "embeddings",
                    get_size=lambda c=_cache: len(getattr(c, '_l1', {})),
                    clear=_cache.clear_sync,
                    description="MLX embedding two-layer LRU cache",
    )
            except Exception:  # noqa: BLE001
                pass  # Non-fatal — registry is optional
        return _cache


def get_embedding_cache_stats() -> dict[str, Any]:
    global _cache
    if _cache is None:
        return {}
    return _cache.get_stats()
