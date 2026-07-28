"""
PersistentKVCache — Sprint KV Cache Persistence.

Persistent KV cache napříč sprinty pomocí LMDB metadata index +
safetensors na disku. Eliminuje repeated prefill náklady při
restartování procesu.

ARCHITECTURA:
┌─────────────────────────────────────────────────────────┐
│  PersistentKVCache (singleton, process-wide)            │
│  ├── _lmdb_env: LMDB for metadata (hash → CacheEntry)│
│  ├── _cache_dir: Path to safetensors files            │
│  └── _lru_order: LRUCache[str, float] for LRU         │
│                                                         │
│  CacheEntry (LMDB value, msgpack):                     │
│  ├── prompt_hash: str (xxhash)                        │
│  ├── safetensors_path: str                            │
│  ├── size_bytes: int                                  │
│  ├── created_at: float                                │
│  └── last_accessed: float                             │
└─────────────────────────────────────────────────────────┘

DISK STRUCTURE:
~/.hledac/cache/mlx_kv_cache/
├── meta.lmdb/           # LMDB metadata index
└── cache/               # Safetensors files
    ├── <hash>.safetensors
    └── ...

M1 8GB BOUNDS:
- max_size_gb: 1.0 GiB default (hard cap na disku)
- max_entries: 256 (LRU eviction trigger)
- map_size: 16 MB pro LMDB metadata

Author: Sprint KV-PERSIST
"""



import asyncio
import msgspec
import hashlib
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hledac.universal.utils.lru_cache import LRUCache  # noqa: I001

import msgspec  # noqa: E402 (lazy, ok at module level for msgpack encode/decode)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# P4-3: async LMDB via Rust backend with py.allow_threads() GIL release
# Falls back to asyncio.to_thread() when Rust backend unavailable
_lmdb_async: Any | None = None


def _get_lmdb_async() -> Any:
    """Lazy-load async LMDB module."""
    global _lmdb_async
    if _lmdb_async is None:
        try:
            from hledac.universal.core.lmdb_async import (
                lmdb_async_delete,
                lmdb_async_put,
                lmdb_async_scan_prefix,
            )

            _lmdb_async = {
                "put": lmdb_async_put,
                "delete": lmdb_async_delete,
                "scan_prefix": lmdb_async_scan_prefix,
            }
        except ImportError:
            _lmdb_async = {}
    return _lmdb_async

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_CACHE_DIR = Path.home() / ".hledac" / "cache" / "mlx_kv_cache"
_CACHE_SUBDIR = "cache"
_META_LMDB = "meta.lmdb"
_LMDB_MAP_SIZE = 16 * 1024 * 1024  # 16 MB — 5000 entries × ~3KB each
_MAX_SIZE_GB = 1.0  # 1 GiB hard cap on disk usage
_MAX_ENTRIES = 256  # LRU eviction trigger
_ENTRY_TTL_S = 7 * 24 * 3600  # 7 days TTL


# ─────────────────────────────────────────────────────────────────────────────
# CacheEntry dataclass
# ─────────────────────────────────────────────────────────────────────────────

class CacheEntry(msgspec.Struct, gc=False):
    """Metadata entry for one cached KV cache. F350M-R: gc=False for M1 8GB."""

    prompt_hash: str
    safetensors_path: str
    size_bytes: int
    created_at: float
    last_accessed: float
    token_count: int = 0

    def encode(self) -> bytes:
        enc = msgspec.msgpack.Encoder()
        return enc.encode(self)

    @classmethod
    def decode(cls, data: bytes) -> CacheEntry:
        dec = msgspec.msgpack.Decoder(cls)
        return dec.decode(data)


# ─────────────────────────────────────────────────────────────────────────────
# PersistentKVCache
# ─────────────────────────────────────────────────────────────────────────────

class PersistentKVCache:
    """
    Persistent KV cache s LMDB metadata index a safetensors storage.

    Features:
    - LMDB-backed metadata index (fast lookups, crash-safe)
    - Safetensors storage (efficient, mlx-native)
    - LRU eviction s bounded disk usage
    - Async save/load (non-blocking disk I/O)
    - Cross-sprint shared cache (singleton)
    - Fail-safe: graceful degradation on any error
    """

    __slots__ = (
        "_lmdb_env",
        "_lmdb_db",
        "_cache_dir",
        "_cache_subdir",
        "_lru_order",
        "_total_bytes",
        "_max_size_bytes",
        "_max_entries",
        "_initialized",
        "_lock",
        "_xxhash",
    )

    _instance: PersistentKVCache | None = None

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_size_gb: float = _MAX_SIZE_GB,
        max_entries: int = _MAX_ENTRIES,
    ) -> None:
        self._lmdb_env: Any = None
        self._lmdb_db: Any = None
        self._cache_dir = (cache_dir or _DEFAULT_CACHE_DIR).resolve()
        self._cache_subdir = self._cache_dir / _CACHE_SUBDIR
        self._lru_order: LRUCache[str, float] = LRUCache()
        self._total_bytes: int = 0
        self._max_size_bytes: int = int(max_size_gb * 1024 * 1024 * 1024)
        self._max_entries: int = max_entries
        self._initialized: bool = False
        self._lock: asyncio.Lock | None = None
        self._xxhash: Any = None

    # ─────────────────────────────────────────────────────────────────────────
    # Singleton
    # ─────────────────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls, **kwargs: Any) -> PersistentKVCache:
        """Get or create the singleton PersistentKVCache instance."""
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        if cls._instance is not None:
            cls._instance._close()
            cls._instance = None

    # ─────────────────────────────────────────────────────────────────────────
    # Initialization
    # ─────────────────────────────────────────────────────────────────────────

    def _init_lmdb(self) -> None:
        """Initialize LMDB metadata index."""
        import lmdb  # type: ignore

        try:
            from hledac.universal.knowledge.lmdb_boot_guard import cleanup_stale_lmdb_lock

            meta_path = self._cache_dir / _META_LMDB
            cleanup_stale_lmdb_lock(meta_path)

            meta_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_subdir.mkdir(parents=True, exist_ok=True)

            self._lmdb_env = lmdb.open(
                str(meta_path),
                map_size=_LMDB_MAP_SIZE,
                subdir=True,
                readonly=False,
                create=True,
                max_dbs=1,
            )
            self._lmdb_db = self._lmdb_env.open_db(b"pkv")
            self._initialized = True
            logger.info(
                "[PKV] LMDB metadata index initialized at %s",
                meta_path,
            )
        except Exception as e:
            logger.warning("[PKV] LMDB init failed: %s, running without metadata index", e)
            self._initialized = False

    async def async_init(self) -> None:
        """Async initialization — call once at startup."""
        if self._initialized:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._initialized:
                return
            # Run LMDB init in thread to avoid blocking event loop
            await asyncio.to_thread(self._init_lmdb)
            # Load existing LRU order from LMDB
            await asyncio.to_thread(self._load_lru_order)

    def _close(self) -> None:
        """Close LMDB environment."""
        if self._lmdb_env is not None:
            try:
                self._lmdb_env.close()
            except Exception:  # noqa: BLE001
                pass
            self._lmdb_env = None
            self._lmdb_db = None
            self._initialized = False

    # ─────────────────────────────────────────────────────────────────────────
    # xxhash lazy import
    # ─────────────────────────────────────────────────────────────────────────

    def _get_xxhash(self) -> Any:
        """Lazy xxhash import."""
        if self._xxhash is None:
            try:
                import xxhash
                self._xxhash = xxhash
            except ImportError:
                self._xxhash = False
        return self._xxhash

    def _hash_prompt(self, prompt: str) -> str:
        """Generate 16-char hash of prompt for cache key."""
        xxh = self._get_xxhash()
        if xxh:
            return xxh.xxh64(prompt).hexdigest()[:16]
        return hashlib.sha256(prompt.encode()).hexdigest()[:16]

    # ─────────────────────────────────────────────────────────────────────────
    # LRU order management
    # ─────────────────────────────────────────────────────────────────────────

    def _load_lru_order(self) -> None:
        """Load LRU order from LMDB at startup."""
        if not self._initialized or self._lmdb_env is None:
            return
        try:
            items: list[tuple[str, float]] = []
            with self._lmdb_env.begin() as txn:
                cursor = txn.cursor(self._lmdb_db)
                for key, value in cursor:
                    try:
                        entry = CacheEntry.decode(value)
                        items.append((key.decode(), entry.last_accessed))
                        self._total_bytes += entry.size_bytes
                    except Exception:
                        continue
            # Sort by last_accessed (oldest first = LRU order)
            items.sort(key=lambda x: x[1])
            # Rebuild LRUCache internal structures in sorted order
            self._lru_order._data.clear()
            self._lru_order._order.clear()
            for key, ts in items:
                self._lru_order._data[key] = ts
                self._lru_order._order.append(key)
            logger.debug(
                "[PKV] Loaded %d entries, total=%.1fMB",
                len(self._lru_order),
                self._total_bytes / 1024 / 1024,
            )
        except Exception as e:
            logger.debug("[PKV] Failed to load LRU order: %s", e)

    def _update_lru(self, key: str) -> None:
        """Update LRU order on access."""
        if key in self._lru_order:
            self._lru_order.move_to_end(key)
        self._lru_order[key] = time.time()

    async def _evict_lru(self) -> int:
        """Evict oldest LRU entries until within bounds. Returns count evicted."""
        evicted = 0
        while self._total_bytes > self._max_size_bytes or len(self._lru_order) > self._max_entries:
            if not self._lru_order:
                break
            oldest_key, _ = self._lru_order.popitem(last=False)
            evicted += await self._evict_entry(oldest_key)
        return evicted

    async def _evict_entry(self, key: str) -> int:
        """Evict a single entry by key. Returns bytes freed."""
        if not self._initialized or self._lmdb_env is None:
            return 0
        freed = 0
        key_bytes = key.encode()
        try:
            # P4-3: Read entry to get size_bytes (needed for _total_bytes accounting)
            def _read_for_size() -> tuple[int, Path | None]:
                with self._lmdb_env.begin() as txn:
                    value = txn.get(key_bytes, db=self._lmdb_db)
                    if value:
                        entry = CacheEntry.decode(value)
                        return entry.size_bytes, Path(entry.safetensors_path)
                return 0, None

            freed, st_path = await asyncio.to_thread(_read_for_size)

            # Delete safetensors file (sync, fast)
            if st_path and st_path.exists():
                st_path.unlink(missing_ok=True)

            # P4-3: Delete from LMDB using Rust backend with py.allow_threads() GIL release
            async_lmdb = _get_lmdb_async()
            if async_lmdb:
                await async_lmdb["delete"](self._lmdb_env, key_bytes)
            else:
                def _delete_lmdb() -> None:
                    with self._lmdb_env.begin(write=True) as txn:
                        txn.delete(key_bytes, db=self._lmdb_db)

                await asyncio.to_thread(_delete_lmdb)

            if key in self._lru_order:
                self._lru_order.pop(key)
            self._total_bytes = max(0, self._total_bytes - freed)
        except Exception as e:
            logger.debug("[PKV] Evict failed for %s: %s", key, e)
        return freed

    # ─────────────────────────────────────────────────────────────────────────
    # Public API: save / load / check
    # ─────────────────────────────────────────────────────────────────────────

    async def save(
        self,
        prompt: str,
        kv_cache: Any,
        token_count: int = 0,
    ) -> bool:
        """
        Save KV cache to persistent storage.

        Args:
            prompt: The prompt that generated this cache (for hashing)
            kv_cache: MLX KV cache object from mlx_lm
            token_count: Number of tokens in the cache

        Returns:
            True if saved successfully, False otherwise
        """
        if not self._initialized:
            await self.async_init()

        key = self._hash_prompt(prompt)
        safetensors_path = self._cache_subdir / f"{key}.safetensors"

        try:
            import mlx.core as mx
            from mlx_lm.models.cache import save_prompt_cache

            mx.eval(kv_cache)

            def _do_save() -> bool:
                try:
                    save_prompt_cache(str(safetensors_path), kv_cache)
                    return True
                except Exception as e:
                    logger.debug("[PKV] save_prompt_cache failed: %s", e)
                    return False

            success = await asyncio.to_thread(_do_save)
            if not success:
                return False

            size_bytes = safetensors_path.stat().st_size

            # Evict if needed BEFORE adding new entry
            await self._evict_lru()

            entry = CacheEntry(
                prompt_hash=key,
                safetensors_path=str(safetensors_path),
                size_bytes=size_bytes,
                created_at=time.time(),
                last_accessed=time.time(),
                token_count=token_count,
            )

            if self._lmdb_env is not None:
                # P4-3: Rust backend with py.allow_threads() GIL release
                # Falls back to asyncio.to_thread() when Rust unavailable
                async_lmdb = _get_lmdb_async()
                key_bytes = key.encode()
                value_bytes = entry.encode()
                if async_lmdb:
                    await async_lmdb["put"](self._lmdb_env, key_bytes, value_bytes)
                else:
                    def _write_lmdb() -> None:
                        with self._lmdb_env.begin(write=True) as txn:
                            txn.put(key_bytes, value_bytes, db=self._lmdb_db)

                    await asyncio.to_thread(_write_lmdb)

            self._lru_order[key] = entry.last_accessed
            self._total_bytes += size_bytes

            logger.debug(
                "[PKV] Saved cache %s (%.1fKB, total=%.1fMB)",
                key,
                size_bytes / 1024,
                self._total_bytes / 1024 / 1024,
            )
            return True

        except Exception as e:
            logger.debug("[PKV] save failed: %s", e)
            if safetensors_path.exists():
                safetensors_path.unlink(missing_ok=True)
            return False

    async def load(self, prompt: str) -> tuple[Any, int] | tuple[None, None]:
        """
        Load KV cache from persistent storage.

        Args:
            prompt: The prompt to look up

        Returns:
            (kv_cache, token_count) if found, (None, None) if not found or error
        """
        if not self._initialized:
            await self.async_init()

        key = self._hash_prompt(prompt)

        entry: CacheEntry | None = None
        if self._lmdb_env is not None:
            try:
                def _read_lmdb() -> CacheEntry | None:
                    with self._lmdb_env.begin() as txn:
                        value = txn.get(key.encode(), db=self._lmdb_db)
                        if value:
                            return CacheEntry.decode(value)
                        return None

                entry = await asyncio.to_thread(_read_lmdb)
            except Exception as e:
                logger.debug("[PKV] LMDB lookup failed: %s", e)

        if entry is None:
            return None, None

        if time.time() - entry.created_at > _ENTRY_TTL_S:
            await self._evict_entry(key)
            return None, None

        st_path = Path(entry.safetensors_path)
        if not st_path.exists():
            await self._evict_entry(key)
            return None, None

        try:
            from mlx_lm.models.cache import load_prompt_cache

            def _do_load() -> tuple[Any, int]:
                cache, metadata = load_prompt_cache(
                    str(st_path),
                    return_metadata=True,
                )
                tok_count = 0
                if isinstance(metadata, dict):
                    tok_count = metadata.get("token_count", 0)
                return cache, tok_count

            kv_cache, token_count = await asyncio.to_thread(_do_load)

            self._update_lru(key)

            if self._lmdb_env is not None:
                entry.last_accessed = time.time()
                # P4-3: Rust backend with py.allow_threads() GIL release
                async_lmdb = _get_lmdb_async()
                key_bytes = key.encode()
                value_bytes = entry.encode()
                if async_lmdb:
                    await async_lmdb["put"](self._lmdb_env, key_bytes, value_bytes)
                else:
                    def _update_lmdb() -> None:
                        with self._lmdb_env.begin(write=True) as txn:
                            txn.put(key_bytes, value_bytes, db=self._lmdb_db)

                    await asyncio.to_thread(_update_lmdb)

            logger.debug("[PKV] Loaded cache %s (%.1fKB)", key, entry.size_bytes / 1024)
            return kv_cache, token_count

        except Exception as e:
            logger.debug("[PKV] load failed: %s", e)
            return None, None

    def has(self, prompt: str) -> bool:
        """Check if cache entry exists (synchronous, for hot path)."""
        if not self._initialized:
            return False
        key = self._hash_prompt(prompt)
        return key in self._lru_order

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            "entries": len(self._lru_order),
            "total_bytes": self._total_bytes,
            "total_mb": round(self._total_bytes / 1024 / 1024, 2),
            "max_bytes": self._max_size_bytes,
            "max_mb": round(self._max_size_bytes / 1024 / 1024, 2),
            "utilization": round(self._total_bytes / self._max_size_bytes * 100, 1)
            if self._max_size_bytes > 0
            else 0.0,
            "initialized": self._initialized,
        }

    async def clear(self) -> None:
        """Clear all cache entries."""
        try:
            keys = list(self._lru_order.keys())
            for key in keys:
                await self._evict_entry(key)
            self._lru_order.clear()
            self._total_bytes = 0
            logger.info("[PKV] Cache cleared")
        except Exception as e:
            logger.debug("[PKV] clear failed: %s", e)

    def close(self) -> None:
        """Close the cache manager."""
        self._close()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level convenience
# ─────────────────────────────────────────────────────────────────────────────

_pkv_instance: PersistentKVCache | None = None


def get_persistent_kv_cache() -> PersistentKVCache:
    """Get the singleton PersistentKVCache instance."""
    return PersistentKVCache.get_instance()


async def async_init_persistent_kv_cache() -> None:
    """Initialize the global PersistentKVCache (call at startup)."""
    global _pkv_instance
    _pkv_instance = get_persistent_kv_cache()
    await _pkv_instance.async_init()
