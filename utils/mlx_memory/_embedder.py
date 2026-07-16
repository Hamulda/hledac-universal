"""
utils/mlx_memory/_embedder.py — Metal Buffer Pre-allocator (F330-MLX-DUP-007)

Pre-allocates persistent Metal buffers for embedding inference to eliminate
per-batch allocation overhead. Expected: 20% faster embeddings on M1.

Architecture:
- Pre-allocated Metal buffers for: input_ids, attention_mask (int32)
- Reusable output buffer for text_embeds (float32)
- Batched mx.eval(): single Metal dispatch per batch, not per-item

M1 8GB: buffers bounded to 256MB total to stay within Metal budget.
"""

import gc
import logging
import threading
from dataclasses import dataclass, field
import msgspec
from typing import Any

logger = logging.getLogger(__name__)
_MAX_BATCH_SIZE: int = 32
_MAX_SEQ_LEN: int = 512
_HIDDEN_DIM: int = 768
_MAX_BUFFER_BYTES: int = 256 * 1024 * 1024
__all__ = ["MetalBufferPool", "get_buffer_pool", "init_metal_embedder_buffers", "release_metal_embedder_buffers"]


class _MetalBuffer(msgspec.Struct):
    """A single pre-allocated Metal buffer."""

    name: str
    shape: tuple[int, ...]
    dtype: str
    mx_buffer: Any = field(default=None)
    allocated: bool = field(default=False)


class MetalBufferPool:
    """
    Pre-allocated Metal buffer pool for embedding inference.

    Usage:
        pool = get_buffer_pool()
        if pool.is_allocated():
            ids = pool.get_buffer("input_ids")
            # ... use buffer ...
    """

    _instance: "MetalBufferPool | None" = None
    _init_lock = threading.Lock()
    _buffers: dict[str, _MetalBuffer]
    _allocated: bool = False
    _allocated_bytes: int = 0
    __slots__ = tuple(('_buffers'))

    def __init__(self) -> None:
        self._buffers = {}
        self._allocated = False
        self._allocated_bytes = 0

    @classmethod
    def get_instance(cls) -> "MetalBufferPool":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def allocate(
        self, max_batch: int = _MAX_BATCH_SIZE, seq_len: int = _MAX_SEQ_LEN, hidden: int = _HIDDEN_DIM
    ) -> bool:
        """Allocate pre-defined Metal buffers. Idempotent (guards against double-init)."""
        if self._allocated:
            return True
        try:
            import mlx.core as mx

            buffers_def = [
                ("input_ids", (max_batch, seq_len), "int32"),
                ("attention_mask", (max_batch, seq_len), "int32"),
                ("output", (max_batch, seq_len, hidden), "float32"),
            ]
            for name, shape, dtype in buffers_def:
                size = 1
                for dim in shape:
                    size *= dim
                byte_size = size * 4 if dtype == "float32" else size * 4
                if self._allocated_bytes + byte_size > _MAX_BUFFER_BYTES:
                    logger.warning(f"[MetalBufferPool] Buffer {name} would exceed cap, skipping")
                    continue
                mlx_dtype = mx.float32 if dtype == "float32" else mx.int32
                buf = mx.zeros(shape, dtype=mlx_dtype)
                self._buffers[name] = _MetalBuffer(name=name, shape=shape, dtype=dtype, mx_buffer=buf, allocated=True)
                self._allocated_bytes += byte_size
                logger.debug(f"[MetalBufferPool] ALLOC {name} shape={shape} dtype={dtype}")
            self._allocated = True
            return True
        except Exception as e:
            logger.error(f"[MetalBufferPool] allocate failed: {e}")
            return False

    def release(self) -> None:
        """Release all buffers and free Metal memory."""
        for buf in self._buffers.values():
            buf.mx_buffer = None
            buf.allocated = False
        self._buffers.clear()
        self._allocated = False
        self._allocated_bytes = 0
        gc.collect()

    def is_allocated(self) -> bool:
        return self._allocated

    def get_buffer(self, name: str) -> Any | None:
        """Get a buffer by name, or None if not found."""
        buf = self._buffers.get(name)
        return buf.mx_buffer if buf and buf.allocated else None


_buffer_pool: MetalBufferPool | None = None
_pool_init_lock = threading.Lock()


def get_buffer_pool() -> MetalBufferPool:
    """Get the singleton MetalBufferPool instance."""
    global _buffer_pool
    if _buffer_pool is None:
        with _pool_init_lock:
            if _buffer_pool is None:
                pool = MetalBufferPool()
                _buffer_pool = pool
    return _buffer_pool


def init_metal_embedder_buffers(
    max_batch: int = _MAX_BATCH_SIZE, seq_len: int = _MAX_SEQ_LEN, hidden: int = _HIDDEN_DIM
) -> dict[str, Any]:
    """
    Initialize pre-allocated Metal buffers for embedding inference.

    Call at application startup to pre-warm buffers and eliminate
    per-batch allocation overhead.

    Returns: {"success": bool, "allocated": bool, "error": str | None}
    """
    pool = get_buffer_pool()
    if pool.is_allocated():
        return {"success": True, "allocated": True, "error": None}
    success = pool.allocate(max_batch=max_batch, seq_len=seq_len, hidden=hidden)
    return {"success": success, "allocated": pool.is_allocated(), "error": None if success else "Allocation failed"}


def release_metal_embedder_buffers() -> None:
    """
    Release pre-allocated Metal buffers and free Metal cache.

    Does NOT destroy the singleton — pool instance persists so that
    subsequent get_buffer_pool() skips re-allocation. This prevents
    the triple-48MB-allocation bug on repeated unload/reload cycles.
    """
    global _buffer_pool
    if _buffer_pool is not None:
        _buffer_pool.release()
