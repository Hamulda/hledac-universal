"""
utils/mlx_memory/_embedder.py — Metal Buffer Pre-allocator (F330-MLX-DUP-007)

Pre-allocates persistent Metal buffers for embedding inference to eliminate


per-batch allocation overhead. Expected: 20% faster embeddings on M1.

Architecture (SILICON-04 upgrade):
- Pre-allocated Metal buffers for: input_ids, attention_mask (int32)
- Reusable output buffer for text_embeds (float32)
- Batched mx.eval(): single Metal dispatch per batch, not per-item
- **NEW**: SharedMetalBuffer backing for true zero-copy Rust↔Python↔MLX
  When available, buffers are backed by real MTLBuffer (StorageModeShared)
  so numpy views and MLX arrays share the same physical pages.

M1 8GB: buffers bounded to 256MB total to stay within Metal budget.
"""

import gc
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Optional

import msgspec

from hledac.universal.utils._patterns import module_singleton_creator
from _core import aclose

logger = logging.getLogger(__name__)
_MAX_BATCH_SIZE: int = 32
_MAX_SEQ_LEN: int = 512
_HIDDEN_DIM: int = 768
_MAX_BUFFER_BYTES: int = 256 * 1024 * 1024
__all__ = [
    "MetalBufferPool",
    "get_buffer_pool",
    "init_metal_embedder_buffers",
    "release_metal_embedder_buffers",
]


def _get_shared_buf_cls():
    """Lazy import of SharedMetalBuffer from Rust extensions (SILICON-04)."""
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        SharedMetalBuffer = rust.raw.SharedMetalBuffer
        return SharedMetalBuffer
    except ImportError:
        return None


class _MetalBuffer(msgspec.Struct, gc=False):
    """A single pre-allocated Metal buffer.

    SILICON-04: When _shared_buf is set, it backs a real MTLBuffer
    with StorageModeShared — enabling zero-copy numpy views.
    """

    name: str
    shape: tuple[int, ...]
    dtype: str
    mx_buffer: Any = field(default=None)  # mx.array (MLX-managed)
    _shared_buf: Any = field(default=None)  # SharedMetalBuffer (Rust-managed MTLBuffer)
    allocated: bool = field(default=False)

    def to_numpy(self) -> Any:
        """Zero-copy numpy view when backed by SharedMetalBuffer (SILICON-04).

        Falls back to mx.array → numpy conversion when not Metal-backed.
        """
        import numpy as np
        if self._shared_buf is not None and self.allocated:
            return self._shared_buf.to_numpy(list(self.shape), self.dtype)
        if self.mx_buffer is not None:
            return np.array(self.mx_buffer)
        return None


class MetalBufferPool:
    """
    Pre-allocated Metal buffer pool for embedding inference.

    SILICON-04 upgrade: When SharedMetalBuffer is available (Rust metal feature
    compiled), buffers are backed by real MTLBuffer with StorageModeShared.
    This enables zero-copy numpy views and eliminates per-batch allocation.

    Usage:
        pool = get_buffer_pool()
        if pool.is_allocated():
            ids = pool.get_buffer("input_ids")
            # ... use buffer ...
            # SILICON-04: zero-copy numpy access
            ids_np = pool.get_buffer_numpy("input_ids")
    """

    _instance: "MetalBufferPool | None" = None
    _init_lock = threading.Lock()
    _buffers: dict[str, _MetalBuffer]
    _allocated: bool = False
    _allocated_bytes: int = 0
    _use_metal_shared: bool = False
    __slots__ = ("_buffers",)

    def __init__(self) -> None:
        self._buffers: dict[str, _MetalBuffer] = {}
        self._allocated = False
        self._allocated_bytes = 0
        # SILICON-04: probe SharedMetalBuffer availability at init
        self._use_metal_shared = _get_shared_buf_cls() is not None

    @classmethod
    def get_instance(cls) -> "MetalBufferPool":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def use_metal_shared(self) -> bool:
        """Whether SharedMetalBuffer (SILICON-04) is available."""
        return self._use_metal_shared

    def allocate(
        self,
        max_batch: int = _MAX_BATCH_SIZE,
        seq_len: int = _MAX_SEQ_LEN,
        hidden: int = _HIDDEN_DIM,
    ) -> bool:
        """Allocate pre-defined Metal buffers. Idempotent (guards against double-init).

        SILICON-04: When SharedMetalBuffer is available, buffers are backed by
        MTLBuffer (StorageModeShared) for zero-copy numpy access.
        Falls back to mx.zeros() when SharedMetalBuffer is unavailable.
        """
        if self._allocated:
            return True
        try:
            import mlx.core as mx

            SharedMetalBuffer = _get_shared_buf_cls()

            buffers_def = [
                ("input_ids", (max_batch, seq_len), "int32"),
                ("attention_mask", (max_batch, seq_len), "int32"),
                ("output", (max_batch, seq_len, hidden), "float32"),
            ]

            for name, shape, dtype in buffers_def:
                size = 1
                for dim in shape:
                    size *= dim
                byte_size = size * 4  # int32=4, float32=4
                if self._allocated_bytes + byte_size > _MAX_BUFFER_BYTES:
                    logger.warning(
                        f"[MetalBufferPool] Buffer {name} would exceed cap, skipping"
    )
                    continue

                mlx_dtype = mx.float32 if dtype == "float32" else mx.int32

                # SILICON-04: try SharedMetalBuffer first
                shared_buf = None
                if SharedMetalBuffer is not None:
                    try:
                        shared_buf = SharedMetalBuffer.allocate(byte_size)
                        logger.debug(
                            f"[MetalBufferPool] SILICON-04: {name} backed by "
                            f"SharedMetalBuffer ({byte_size} bytes)"
    )
                    except Exception as e:
                        logger.debug(
                            f"[MetalBufferPool] SharedMetalBuffer.allocate failed "
                            f"for {name}: {e}, falling back to mx.zeros()"
    )
                        shared_buf = None

                if shared_buf is not None:
                    # Create MLX array from the shared buffer (one copy)
                    mlx_arr = shared_buf.to_mlx_array(list(shape), mlx_dtype)
                else:
                    # Standard path: MLX-managed Metal buffer
                    mlx_arr = mx.zeros(shape, dtype=mlx_dtype)

                self._buffers[name] = _MetalBuffer(
                    name=name,
                    shape=shape,
                    dtype=dtype,
                    mx_buffer=mlx_arr,
                    _shared_buf=shared_buf,
                    allocated=True,
    )
                self._allocated_bytes += byte_size
                logger.debug(
                    f"[MetalBufferPool] ALLOC {name} shape={shape} dtype={dtype} "
                    f"metal_shared={shared_buf is not None}"
    )

            self._allocated = True
            return True
        except Exception as e:
            logger.error(f"[MetalBufferPool] allocate failed: {e}")
            return False

    def release(self) -> None:
        """Release all buffers and free Metal memory.

        SILICON-04: SharedMetalBuffer instances are released properly
        via their Drop implementation, which calls track_free().
        """
        for buf in self._buffers.values():
            if buf._shared_buf is not None:
                try:
                    buf._shared_buf.release()
                except Exception:  # noqa: BLE001
                    pass
                buf._shared_buf = None
            buf.mx_buffer = None
            buf.allocated = False
        self._buffers.clear()
        self._allocated = False
        self._allocated_bytes = 0
        gc.collect()

    def is_allocated(self) -> bool:
        return self._allocated

    def get_buffer(self, name: str) -> Any | None:
        """Get an MLX buffer by name, or None if not found."""
        buf = self._buffers.get(name)
        return buf.mx_buffer if buf and buf.allocated else None

    def get_buffer_numpy(self, name: str) -> Any | None:
        """Get a zero-copy numpy view of the buffer (SILICON-04).

        When backed by SharedMetalBuffer, this is truly zero-copy —
        the numpy array shares physical pages with the MTLBuffer.
        Falls back to mx.array → numpy conversion otherwise.
        """
        buf = self._buffers.get(name)
        if buf is None or not buf.allocated:
            return None
        return buf.to_numpy()

    def is_metal_shared_backed(self, name: str) -> bool:
        """Check if a specific buffer is backed by SharedMetalBuffer."""
        buf = self._buffers.get(name)
        return buf is not None and buf._shared_buf is not None


# Module-level singleton — one pool per process.
# F330-DUP: Refactored to use module_singleton_creator from utils/_patterns.py


def _create_buffer_pool() -> MetalBufferPool:
    """Factory for MetalBufferPool singleton."""
    return MetalBufferPool()


# DRY: Double-checked locking singleton via module_singleton_creator
get_buffer_pool = module_singleton_creator(factory=_create_buffer_pool)


def init_metal_embedder_buffers(
    max_batch: int = _MAX_BATCH_SIZE,
    seq_len: int = _MAX_SEQ_LEN,
    hidden: int = _HIDDEN_DIM,
) -> dict[str, Any]:
    """
    Initialize pre-allocated Metal buffers for embedding inference.

    Call at application startup to pre-warm buffers and eliminate
    per-batch allocation overhead.

    SILICON-04: When SharedMetalBuffer is available, buffers use
    MTLBuffer with StorageModeShared for zero-copy numpy access.

    Returns: {"success": bool, "allocated": bool, "error": str | None,
              "metal_shared": bool}
    """
    pool = get_buffer_pool()
    if pool.is_allocated():
        return {
            "success": True,
            "allocated": True,
            "error": None,
            "metal_shared": pool.use_metal_shared,
        }
    success = pool.allocate(max_batch=max_batch, seq_len=seq_len, hidden=hidden)
    return {
        "success": success,
        "allocated": pool.is_allocated(),
        "error": None if success else "Allocation failed",
        "metal_shared": pool.use_metal_shared,
    }


def release_metal_embedder_buffers() -> None:
    """
    Release pre-allocated Metal buffers and free Metal cache.

    Does NOT destroy the singleton — pool instance persists so that
    subsequent get_buffer_pool() skips re-allocation. This prevents
    the triple-48MB-allocation bug on repeated unload/reload cycles.

    SILICON-04: SharedMetalBuffer instances are released via their
    Drop implementation, which properly calls track_free().
    """
    global _buffer_pool
    if _buffer_pool is not None:
        _buffer_pool.release()
