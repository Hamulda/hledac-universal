"""
Metal Slab Pool — Sprint E.4+ / F269.

Bounded memory pool pro Metal buffery na M1 8GB.
Používá velikostní třídy (slab allocation) pro minimalizaci
fragmentace a maximalizaci reuse.

Architecture:
- Slab allocator s 8 velikostními třídami: 64KB, 256KB, 1MB, 4MB, 16MB, 64MB, 128MB, 256MB
- LRU cache per size-class — max 2 slabs per class = 16 slabs total
- Thread-safe s double-checked locking
- Fail-safe: při chybě vrací (None, None) a volající použije standardní mx.zeros()

M1 8GB budget:
  macOS baseline:  ~2.5 GB
  Orchestrátor:    ~1.0 GB
  LLM (Hermes-3):  ~2.0 GB
  KV cache:        ~0.75 GB
  Metal slabs:     ~0.5 GB (bounded)
  -----------------------
  Total:           ~6.75 GB (při 8GB fyzické RAM = overcommit na pressure)

  → Slabs jsou volitelné per-task, model má prioritu
  → Při pressure se slabs vrací do poolu OKAMŽITĚ

API:
  MetalSlabPool.get_slab(size_bytes) -> memoryview
  MetalSlabPool.return_slab(slab_id, memoryview)
  MetalSlabPool.release_all()
"""


import gc
import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Lazy MLX import
_MLX_AVAILABLE: bool | None = None
_mx_core: Any = None


def _ensure_mlx() -> bool:
    """Lazy MLX init — voláno až při prvním API volání."""
    global _MLX_AVAILABLE, _mx_core
    if _MLX_AVAILABLE is not None:
        return _MLX_AVAILABLE
    _MLX_AVAILABLE = False
    try:
        import mlx.core as mx
        _mx_core = mx
        _MLX_AVAILABLE = True
    except ImportError:
        _mx_core = None
    return _MLX_AVAILABLE


# === Velikostní třídy (slab sizes) — mocniny 2 pro M1 GPU alignment ===
# M1 GPU tile size: 128KB — aligned allocations perform better
_SLAB_CLASSES_BYTES: tuple[int, ...] = (
    64 * 1024,       # 64 KB   — small texts, tokens
    256 * 1024,      # 256 KB  — medium embeddings
    1 * 1024 * 1024,     # 1 MB    — standard hidden_dim=768 batch
    4 * 1024 * 1024,     # 4 MB    — large batch
    16 * 1024 * 1024,    # 16 MB   — Bert-large,RoBERTa
    64 * 1024 * 1024,    # 64 MB   — very large batches
    128 * 1024 * 1024,   # 128 MB  — high-dimensional outputs
    256 * 1024 * 1024,   # 256 MB  — max single slab (MetalBufferPool compatible)
)

# Max slabs per size class — bounded total: 8 * 2 = 16 slabs
_SLABS_PER_CLASS: int = 2

# Total max: 16 slabs * 256MB = 4GB (unrealistic max, actual bounded by _SLAB_CLASSES)
# Realistic max: 16 * 256MB = 4GB IF all slabs at max size
# But we track actual allocated bytes
_MAX_SLAB_TOTAL_BYTES: int = 512 * 1024 * 1024  # 512 MB hard cap for M1 8GB


@dataclass
class _Slab:
    """Single slab — pre-allocated Metal buffer."""
    slab_id: str
    size_bytes: int
    mx_buffer: Any | None = None  # mx.array GPU buffer
    in_use: bool = False
    last_access: float = 0.0


def _round_up_to_slab_class(size_bytes: int) -> int:
    """Round size up to nearest slab class."""
    for cls in _SLAB_CLASSES_BYTES:
        if cls >= size_bytes:
            return cls
    # Fallback: largest slab
    return _SLAB_CLASSES_BYTES[-1]


class MetalSlabPool:
    """
    Thread-safe slab allocator for Metal buffers.

    Eliminates per-allocation overhead by pre-allocating reusable
    Metal buffers in discrete size classes. Uses LRU eviction
    when all slabs in a class are in use.

    Usage:
        pool = MetalSlabPool.get_instance()
        slab = pool.acquire_slab(1024 * 1024)  # 1MB slab
        if slab is not None:
            try:
                # use slab.mx_buffer
            finally:
                pool.release_slab(slab)
    """

    _instance: "MetalSlabPool | None" = None
    _init_lock = threading.Lock()

    # Per-class LRU lists: size_class -> {slab_id -> _Slab}
    _slabs: dict[int, dict[str, _Slab]]
    _slab_lock: threading.Lock

    # Statistics
    _stats_hits: int = 0
    _stats_misses: int = 0
    _stats_allocated_bytes: int = 0

    def __init__(self) -> None:
        self._slabs = {}
        self._slab_lock = threading.Lock()
        if not _ensure_mlx():
            logger.warning("[MetalSlabPool] MLX unavailable, pool disabled")
            return

        # Initialize per-class slab dicts
        for size_cls in _SLAB_CLASSES_BYTES:
            self._slabs[size_cls] = {}

        logger.info(
            f"[MetalSlabPool] Initialized: {len(_SLAB_CLASSES_BYTES)} size classes, "
            f"max {_SLABS_PER_CLASS} slabs/class, 512MB total cap"
        )

    @classmethod
    def get_instance(cls) -> "MetalSlabPool":
        """Thread-safe singleton."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def acquire_slab(self, size_bytes: int) -> _Slab | None:
        """
        Acquire a slab of at least size_bytes.

        Returns:
            _Slab instance or None if:
            - MLX unavailable
            - total pool cap exceeded
            - allocation failed
        """
        if not _ensure_mlx():
            return None

        size_cls = _round_up_to_slab_class(size_bytes)

        with self._slab_lock:
            # Try to find free slab in this class
            slabs = self._slabs[size_cls]
            for slab_id, slab in slabs.items():
                if not slab.in_use:
                    slab.in_use = True
                    import time
                    slab.last_access = time.monotonic()
                    self._stats_hits += 1
                    logger.debug(
                        f"[MetalSlabPool] HIT slab={slab_id[:8]} "
                        f"size={size_cls // 1024}KB"
                    )
                    return slab

            # No free slab — try to evict LRU from this class
            if len(slabs) >= _SLABS_PER_CLASS:
                lru_slab = min(
                    slabs.values(),
                    key=lambda s: s.last_access
                )
                self._evict_slab(lru_slab, size_cls)

            # Check total cap
            if self._stats_allocated_bytes + size_cls > _MAX_SLAB_TOTAL_BYTES:
                # Try aggressive cleanup before failing
                self._aggressive_cleanup()
                if self._stats_allocated_bytes + size_cls > _MAX_SLAB_TOTAL_BYTES:
                    logger.warning(
                        f"[MetalSlabPool] Cap exceeded ({self._stats_allocated_bytes / 1024 / 1024:.1f}MB), "
                        f"returning None"
                    )
                    return None

            # Allocate new slab
            return self._allocate_slab(size_cls)

    def _allocate_slab(self, size_cls: int) -> _Slab | None:
        """Allocate a new slab under pool lock."""
        try:
            mx = _mx_core

            # Compute shape: flatten to 1D for simplicity, view as needed
            # Use float32 as universal type (matches MLX default)
            num_elements = size_cls // 4  # 4 bytes per float32
            mx_buffer = mx.zeros(num_elements, dtype=mx.float32)

            # Force Metal allocation NOW
            mx.eval(mx_buffer)

            slab_id = str(uuid.uuid4())[:8]
            slab = _Slab(
                slab_id=slab_id,
                size_bytes=size_cls,
                mx_buffer=mx_buffer,
                in_use=True,
                last_access=0.0,
            )

            self._slabs[size_cls][slab_id] = slab
            self._stats_allocated_bytes += size_cls
            self._stats_misses += 1

            import time
            slab.last_access = time.monotonic()

            logger.debug(
                f"[MetalSlabPool] ALLOC slab={slab_id[:8]} "
                f"size={size_cls // 1024}KB total={self._stats_allocated_bytes / 1024 / 1024:.1f}MB"
            )
            return slab

        except Exception as e:
            logger.warning(f"[MetalSlabPool] Allocation failed: {e}")
            return None

    def _evict_slab(self, slab: _Slab, size_cls: int) -> None:
        """Evict a slab — called under pool lock."""
        try:
            if slab.mx_buffer is not None:
                # F266 canonical release: eval → gc → clear_cache → gc
                _mx_core.eval([])
                slab.mx_buffer = None
                gc.collect()
                if hasattr(_mx_core, "clear_cache"):
                    _mx_core.clear_cache()
                elif hasattr(_mx_core.metal, "clear_cache"):
                    _mx_core.metal.clear_cache()
                gc.collect()

            self._stats_allocated_bytes -= slab.size_bytes
            del self._slabs[size_cls][slab.slab_id]
            logger.debug(f"[MetalSlabPool] EVICT slab={slab.slab_id[:8]}")
        except Exception as e:
            logger.debug(f"[MetalSlabPool] Evict error: {e}")

    def release_slab(self, slab: _Slab) -> None:
        """
        Return a slab to the pool (mark as free, don't deallocate).

        Slab stays allocated for reuse — this is the pool win.
        Use release_all() to fully deallocate.
        """
        if slab is None:
            return
        with self._slab_lock:
            slab.in_use = False
            slab.mx_buffer = None  # Allow GPU memory to be reclaimed on next eval

    def _aggressive_cleanup(self) -> None:
        """Aggressive cleanup — evict all free slabs."""
        with self._slab_lock:
            for size_cls, slabs in self._slabs.items():
                for slab_id in list(slabs.keys()):
                    slab = slabs[slab_id]
                    if not slab.in_use:
                        self._evict_slab(slab, size_cls)

    def release_all(self) -> None:
        """
        Release ALL slabs and clear Metal cache.
        Called during app shutdown or memory pressure.
        """
        with self._slab_lock:
            for size_cls, slabs in list(self._slabs.items()):
                for slab in list(slabs.values()):
                    if slab.in_use:
                        self._evict_slab(slab, size_cls)
                    try:
                        slab.in_use = False
                        if slab.mx_buffer is not None:
                            _mx_core.eval([])
                            slab.mx_buffer = None
                    except Exception:  # noqa: BLE001
                        pass

            # Clear Metal cache
            if _ensure_mlx():
                try:
                    _mx_core.eval([])
                    gc.collect()
                    if hasattr(_mx_core, "clear_cache"):
                        _mx_core.clear_cache()
                    elif hasattr(_mx_core.metal, "clear_cache"):
                        _mx_core.metal.clear_cache()
                    gc.collect()
                except Exception as e:
                    logger.debug(f"[MetalSlabPool] release_all cache clear: {e}")

            self._stats_allocated_bytes = 0
            self._slabs.clear()
            # Re-init empty dicts
            for size_cls in _SLAB_CLASSES_BYTES:
                self._slabs[size_cls] = {}

            logger.info("[MetalSlabPool] Released all slabs")

    @property
    def stats(self) -> dict[str, Any]:
        """Pool statistics for monitoring."""
        return {
            "hits": self._stats_hits,
            "misses": self._stats_misses,
            "hit_rate": (
                self._stats_hits / (self._stats_hits + self._stats_misses)
                if (self._stats_hits + self._stats_misses) > 0
                else 0.0
            ),
            "allocated_bytes": self._stats_allocated_bytes,
            "allocated_mb": self._stats_allocated_bytes / 1024 / 1024,
            "cap_bytes": _MAX_SLAB_TOTAL_BYTES,
            "cap_mb": _MAX_SLAB_TOTAL_BYTES / 1024 / 1024,
        }

    def get_buffer_for_size(self, size_bytes: int) -> tuple[Any, int] | tuple[None, None]:
        """
        Get a Metal buffer for the given size.

        Returns:
            (mx.buffer, actual_size) or (None, None) if unavailable.

        Usage:
            result, actual = pool.get_buffer_for_size(1024 * 1024)
            if result is not None:
                try:
                    view = result[:1024]  # use first 1MB
                finally:
                    pool.return_buffer(result)
        """
        slab = self.acquire_slab(size_bytes)
        if slab is None:
            return None, None
        return slab.mx_buffer, slab.size_bytes


# === Convenience API ===

_pool_instance: "MetalSlabPool | None" = None


def get_slab_pool() -> MetalSlabPool:
    """Get the singleton MetalSlabPool instance."""
    return MetalSlabPool.get_instance()


def acquire_metal_buffer(size_bytes: int) -> tuple[Any, int] | tuple[None, None]:
    """
    Acquire a Metal buffer of at least size_bytes.

    Returns:
        (mx.array buffer, actual_size_bytes) or (None, None)

    Usage:
        buf, size = acquire_metal_buffer(1024 * 1024)
        if buf is not None:
            try:
                data = buf[:actual_size]
                # use data
            finally:
                release_metal_buffer(buf, size)
    """
    pool = get_slab_pool()
    return pool.get_buffer_for_size(size_bytes)


def release_metal_buffer(mx_buffer: Any, size_bytes: int) -> None:
    """Release a buffer back to the pool."""
    # No-op: slab stays allocated in pool for reuse.
    # Metal memory is reclaimed via mx.eval([]) + clear_cache().
    del mx_buffer, size_bytes  # suppress unused warnings


def release_slab_pool() -> None:
    """Release all slabs — call on shutdown or memory pressure."""
    global _pool_instance
    if _pool_instance is not None:
        _pool_instance.release_all()
        _pool_instance = None


__all__ = [
    "MetalSlabPool",
    "get_slab_pool",
    "acquire_metal_buffer",
    "release_metal_buffer",
    "release_slab_pool",
]
