"""
utils/mlx_memory/_slab.py — Metal Slab Pool (F330-MLX-DUP-007)

Bounded slab allocator pro Metal buffery na M1 8GB.


Používá velikostní třídy (powers of 2) pro minimalizaci fragmentace.

Architecture:
- 8 slab size classes: 64KB, 256KB, 1MB, 4MB, 16MB, 64MB, 128MB, 256MB
- LRU cache per class — max 2 slabs per class = 16 slabs total
- Thread-safe double-checked locking
- Fail-safe: vrací (None, None) při chybě

M1 8GB budget (pro slabs = ~0.5 GiB volitelné):
    macOS baseline:   ~2.5 GiB
    Orchestrátor:     ~1.0 GiB
    LLM (Hermes-3): ~2.0 GiB
    KV cache:         ~0.75 GiB
    Metal slabs:       ~0.5 GiB  (bounded, model má prioritu)

MODERN-43: Atomic allocation ledger integration
- On acquire_slab() allocation: mlx_alloc_bytes_add()
- On _evict_slab() release: mlx_alloc_bytes_sub()
- On slab hit: _cache_hit() (via Rust atomic facade)
- On slab miss: _cache_miss() (via Rust atomic facade)
"""
import gc
import logging
import threading
import time as _time
import uuid
from dataclasses import dataclass, field
import msgspec
from typing import Any
from operator import attrgetter, itemgetter
logger = logging.getLogger(__name__)

# MODERN-43: Try to load Rust atomic facade for allocation ledger and cache metrics
_RUST_ALLOC_AVAILABLE: bool = False
_mlx_alloc_bytes_add: Any = None
_mlx_alloc_bytes_sub: Any = None
_mlx_alloc_bytes_get: Any = None

try:
    from hledac_rust_extensions import mlx_alloc_bytes_add, mlx_alloc_bytes_sub, mlx_alloc_bytes_get
    _RUST_ALLOC_AVAILABLE = True
    _mlx_alloc_bytes_add = mlx_alloc_bytes_add
    _mlx_alloc_bytes_sub = mlx_alloc_bytes_sub
    _mlx_alloc_bytes_get = mlx_alloc_bytes_get
except ImportError:
    logger.debug("[MODERN-43] Rust allocation ledger unavailable in _slab.py")
_SLAB_CLASSES_BYTES: tuple[int, ...] = (64 * 1024, 256 * 1024, 1024 * 1024, 4 * 1024 * 1024, 16 * 1024 * 1024, 64 * 1024 * 1024, 128 * 1024 * 1024, 256 * 1024 * 1024)
_SLAB_CLASS_NAMES: tuple[str, ...] = ('64KB', '256KB', '1MB', '4MB', '16MB', '64MB', '128MB', '256MB')
_SLABS_PER_CLASS: int = 2
_MAX_SLAB_TOTAL_BYTES: int = 512 * 1024 * 1024

class _Slab(msgspec.Struct, gc=False):
    """A single Metal buffer slab."""
    slab_id: str
    size_class: int
    size_bytes: int
    memoryview: Any = field(default=None)
    last_access: float = field(default=0.0)
    in_use: bool = field(default=False)

class MetalSlabPool:
    """
    Thread-safe slab allocator for Metal buffers.

    Usage:
        pool = MetalSlabPool.get_instance()
        slab = pool.acquire_slab(1024 * 1024)  # 1MB slab
        if slab is not None:
            try:
                # use slab.memoryview
            finally:
                pool.release_slab(slab)
    """
    _instance: 'MetalSlabPool | None' = None
    _init_lock = threading.Lock()
    _slabs: dict[int, dict[str, _Slab]]
    _slab_lock: threading.Lock
    _stats_hits: int
    _stats_misses: int
    _stats_allocated_bytes: int
    __slots__ = tuple(('_slab_lock', '_slabs', '_stats_allocated_bytes', '_stats_hits', '_stats_misses'))

    def __init__(self) -> None:
        self._slabs = {i: {} for i in range(len(_SLAB_CLASSES_BYTES))}
        self._slab_lock = threading.Lock()
        self._stats_hits = 0
        self._stats_misses = 0
        self._stats_allocated_bytes = 0

    @classmethod
    def get_instance(cls) -> 'MetalSlabPool':
        """Get the singleton MetalSlabPool instance."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def release_slab_pool(cls) -> None:
        """Release the singleton (called by mlx_cleanup_sync)."""
        if cls._instance is not None:
            with cls._init_lock:
                if cls._instance is not None:
                    cls._instance.release_all()
                cls._instance = None

    @staticmethod
    def _size_class_for(size_bytes: int) -> int:
        """Return the smallest size class >= size_bytes."""
        for i, cls in enumerate(_SLAB_CLASSES_BYTES):
            if cls >= size_bytes:
                return i
        return len(_SLAB_CLASSES_BYTES) - 1

    def acquire_slab(self, size_bytes: int) -> _Slab | None:
        """
        Acquire a slab of at least size_bytes.

        Returns a _Slab with a memoryview, or None on failure.
        Caller must call release_slab() when done.
        """
        size_cls = self._size_class_for(size_bytes)
        actual_size = _SLAB_CLASSES_BYTES[size_cls]
        with self._slab_lock:
            slabs = self._slabs[size_cls]
            for slab_id, slab in slabs.items():
                if not slab.in_use:
                    slab.in_use = True
                    slab.last_access = _time.monotonic()
                    self._stats_hits += 1
                    logger.debug(f'[MetalSlabPool] HIT slab={slab_id[:8]} size={actual_size // 1024}KB')
                    # MODERN-43: Track slab pool hit via Rust atomic
                    try:
                        from utils.mlx_memory._core import _cache_hit
                        _cache_hit()
                    except Exception:
                        pass  # Non-critical, don't fail allocation
                    return slab
            if len(slabs) >= _SLABS_PER_CLASS:
                lru_slab = min(slabs.values(), key=attrgetter("last_access"))
                self._evict_slab(lru_slab, size_cls)
            if self._stats_allocated_bytes + actual_size > _MAX_SLAB_TOTAL_BYTES:
                self._aggressive_cleanup()
                if self._stats_allocated_bytes + actual_size > _MAX_SLAB_TOTAL_BYTES:
                    self._stats_misses += 1
                    logger.debug(f'[MetalSlabPool] MISS — total cap reached ({self._stats_allocated_bytes / 1024 ** 2:.0f}MB)')
                    # MODERN-43: Track slab pool miss via Rust atomic
                    try:
                        from utils.mlx_memory._core import _cache_miss
                        _cache_miss()
                    except Exception:
                        pass  # Non-critical
                    return None
        try:
            import mlx.core as mx
            buf = mx.zeros([actual_size // 4], dtype=mx.int32)
            slab = _Slab(slab_id=str(uuid.uuid4()), size_class=size_cls, size_bytes=actual_size, memoryview=buf, last_access=_time.monotonic(), in_use=True)
            with self._slab_lock:
                self._slabs[size_cls][slab.slab_id] = slab
                self._stats_allocated_bytes += actual_size
                self._stats_hits += 1
            # MODERN-43: Track MLX allocation via Rust atomic ledger
            if _RUST_ALLOC_AVAILABLE:
                try:
                    _mlx_alloc_bytes_add(actual_size)
                except Exception as e:
                    logger.debug(f"[MODERN-43] mlx_alloc_bytes_add failed: {e}")
            logger.debug(f'[MetalSlabPool] ALLOC slab={slab.slab_id[:8]} size={actual_size // 1024}KB')
            return slab
        except Exception as e:
            logger.debug(f'[MetalSlabPool] ALLOC FAILED: {e}')
            with self._slab_lock:
                self._stats_misses += 1
            return None

    def release_slab(self, slab: _Slab) -> None:
        """Return a slab to the pool (does not free, just marks free)."""
        with self._slab_lock:
            if slab.slab_id in self._slabs[slab.size_class]:
                slab.in_use = False
                slab.last_access = _time.monotonic()
                logger.debug(f'[MetalSlabPool] RELEASE slab={slab.slab_id[:8]} size={slab.size_bytes // 1024}KB')

    def release_all(self) -> None:
        """Release all slabs back to the system."""
        with self._slab_lock:
            # Track total bytes being released for Rust atomic ledger
            total_bytes = self._stats_allocated_bytes
            for size_cls, slabs in self._slabs.items():
                for slab in list(slabs.values()):
                    self._evict_slab(slab, size_cls)
            self._stats_allocated_bytes = 0
        # MODERN-43: Track full pool release via Rust atomic
        if _RUST_ALLOC_AVAILABLE and total_bytes > 0:
            try:
                _mlx_alloc_bytes_sub(total_bytes)
            except Exception:
                pass
        gc.collect()

    def _evict_slab(self, slab: _Slab, size_cls: int) -> None:
        """Remove a slab from the pool."""
        if slab.slab_id in self._slabs[size_cls]:
            del self._slabs[size_cls][slab.slab_id]
            self._stats_allocated_bytes -= slab.size_bytes
            slab.memoryview = None
            slab.in_use = False
            # MODERN-43: Track MLX deallocation via Rust atomic ledger
            if _RUST_ALLOC_AVAILABLE:
                try:
                    _mlx_alloc_bytes_sub(slab.size_bytes)
                except Exception as e:
                    logger.debug(f"[MODERN-43] mlx_alloc_bytes_sub failed: {e}")
            logger.debug(f'[MetalSlabPool] EVICT slab={slab.slab_id[:8]} size={slab.size_bytes // 1024}KB')

    def _aggressive_cleanup(self) -> None:
        """Aggressive cleanup: clear MLX cache and retry."""
        try:
            import mlx.core as mx
            mx.eval([])
            gc.collect()
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
            elif hasattr(mx, 'metal') and hasattr(mx.metal, 'clear_cache'):
                mx.metal.clear_cache()
            gc.collect()
        except Exception as e:
            logger.debug(f'[MetalSlabPool] aggressive_cleanup: {e}')

    def get_stats(self) -> dict[str, Any]:
        """Return pool statistics."""
        with self._slab_lock:
            total_slabs = sum((len(s) for s in self._slabs.values()))
            # MODERN-43: Include Rust atomic ledger total if available
            rust_alloc_bytes = 0
            if _RUST_ALLOC_AVAILABLE and _mlx_alloc_bytes_get is not None:
                try:
                    rust_alloc_bytes = _mlx_alloc_bytes_get()
                except Exception:
                    pass
            return {
                'total_slabs': total_slabs,
                'max_slabs': len(_SLAB_CLASSES_BYTES) * _SLABS_PER_CLASS,
                'allocated_bytes': self._stats_allocated_bytes,
                'rust_alloc_bytes': rust_alloc_bytes,
                'max_bytes': _MAX_SLAB_TOTAL_BYTES,
                'hits': self._stats_hits,
                'misses': self._stats_misses,
                'rust_atomic': _RUST_ALLOC_AVAILABLE,
            }

    def get_buffer_for_size(self, size_bytes: int) -> Any | None:
        """
        Convenience: acquire and return the memoryview directly.
        The slab is NOT released — caller is responsible for releasing.
        """
        slab = self.acquire_slab(size_bytes)
        return slab.memoryview if slab else None

def release_slab_pool() -> None:
    """Module-level convenience alias."""
    MetalSlabPool.release_slab_pool()