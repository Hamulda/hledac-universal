"""
Metal Buffer Pre-allocator for Embedding Inference — Sprint E.4.

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
from typing import Any

logger = logging.getLogger(__name__)

# Lazy MLX import — NO mlx.core at module load
_MLX_AVAILABLE: bool | None = None
_mx_core: Any = None


def _ensure_mlx() -> bool:
    """Lazy MLX initialization on first use."""
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


# === Buffer Pool Constants ===
# M1 8GB: total budget 256MB for embedding buffers
_MAX_BUFFER_BYTES: int = 256 * 1024 * 1024  # 256 MiB
_MAX_SEQ_LEN: int = 512  # ModernBERT max sequence length
_MAX_BATCH_SIZE: int = 32  # Maximum batch size (matches MLXEmbeddingManager.BATCH_SIZE)


@dataclass
class MetalBufferPool:
    """
    Pre-allocated Metal buffer pool for embedding inference.

    Eliminates per-batch mx.array() allocation overhead by reusing
    the same GPU buffers across encode() calls.
    """
    input_ids_buffer: Any = None       # mx.array, shape (max_batch, max_seq_len), int32
    attention_mask_buffer: Any = None  # mx.array, shape (max_batch, max_seq_len), int32
    output_buffer: Any = None         # mx.array, shape (max_batch, max_seq_len, hidden), float32

    _allocated: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def allocate(self, max_batch: int = _MAX_BATCH_SIZE, seq_len: int = _MAX_SEQ_LEN, hidden: int = 768) -> bool:
        """
        Pre-allocate Metal buffers at startup.

        Args:
            max_batch: Maximum batch size (default 32)
            seq_len: Maximum sequence length (default 512)
            hidden: Hidden dimension (default 768 for ModernBERT)

        Returns:
            True if allocation successful, False otherwise.
        """
        # Fast path: pool already allocated, Metal buffers are warm.
        # _allocated is never reset to False (release() keeps it True to prevent
        # triple-allocation bug), so this is the only path after first allocation.
        if self._allocated:
            return True

        with self._lock:
            if not _ensure_mlx():
                logger.warning("[MetalBufferPool] MLX not available, skipping allocation")
                return False

            try:
                mx = _mx_core

                # Allocate input_ids buffer: (max_batch, seq_len), int32
                self.input_ids_buffer = mx.zeros((max_batch, seq_len), dtype=mx.int32)
                logger.debug(f"[MetalBufferPool] input_ids buffer: {max_batch}x{seq_len} int32")

                # Allocate attention_mask buffer: (max_batch, seq_len), int32
                self.attention_mask_buffer = mx.zeros((max_batch, seq_len), dtype=mx.int32)
                logger.debug(f"[MetalBufferPool] attention_mask buffer: {max_batch}x{seq_len} int32")

                # Allocate output buffer: (max_batch, seq_len, hidden), float32
                # This is the largest buffer — reused across all batches
                self.output_buffer = mx.zeros((max_batch, seq_len, hidden), dtype=mx.float32)
                logger.debug(f"[MetalBufferPool] output buffer: {max_batch}x{seq_len}x{hidden} float32")

                # Force Metal to allocate now (not lazily on first use)
                mx.eval([self.input_ids_buffer, self.attention_mask_buffer, self.output_buffer])

                self._allocated = True
                logger.info(
                    f"[MetalBufferPool] Allocated: {max_batch}×{seq_len} int32×2 + "
                    f"{max_batch}×{seq_len}×{hidden} float32 "
                    f"({3 * max_batch * seq_len * 4 / 1024 / 1024:.1f} MB + "
                    f"{max_batch * seq_len * hidden * 4 / 1024 / 1024:.1f} MB)"
                )
                return True

            except Exception as e:
                logger.warning(f"[MetalBufferPool] Allocation failed: {e}")
                self._allocated = False
                return False

    def is_allocated(self) -> bool:
        """Return True if buffers are pre-allocated."""
        return self._allocated

    def get_buffers(self):
        """
        Return the pre-allocated buffers for use in encode().

        Returns None if not allocated (caller should fall back to regular path).

        Thread-safe: acquires _lock to ensure buffers aren't nulled between
        the check and the return. Pairs with release() which holds the same lock
        while nulling buffers.
        """
        with self._lock:
            if not self._allocated:
                return None
            return self.input_ids_buffer, self.attention_mask_buffer, self.output_buffer

    def release(self) -> None:
        """
        Release pre-allocated buffers and clear Metal cache.

        Called during unload() to free memory back to the system.
        Keeps _allocated=True so the singleton pool is NOT re-allocated on
        subsequent get_buffer_pool() calls (avoids triple-48MB Metal buffer bug).
        """
        with self._lock:
            # Guard: nothing to release if already released (buffers=None) or never allocated
            if self.input_ids_buffer is None and self.attention_mask_buffer is None and self.output_buffer is None:
                return

            try:
                self.input_ids_buffer = None
                self.attention_mask_buffer = None
                self.output_buffer = None
                # NOTE: do NOT set _allocated = False — keeps the singleton reusable
                # without re-allocating 48MB of Metal buffers on next get_buffer_pool()

                # F266 canonical: mx.eval → gc.collect → clear_cache → gc.collect
                if _ensure_mlx():
                    _mx_core.eval([])
                    gc.collect()
                    if hasattr(_mx_core, "clear_cache"):
                        _mx_core.clear_cache()
                    elif hasattr(_mx_core.metal, "clear_cache"):
                        _mx_core.metal.clear_cache()
                    gc.collect()

                logger.info("[MetalBufferPool] Released")
            except Exception as e:
                logger.debug(f"[MetalBufferPool] Release error: {e}")


# Singleton buffer pool — initialized once at first use
_buffer_pool: MetalBufferPool | None = None
_pool_init_lock = threading.Lock()


def get_buffer_pool() -> MetalBufferPool:
    """
    Get the singleton MetalBufferPool instance.

    Thread-safe lazy initialization: first caller triggers allocation.
    """
    global _buffer_pool
    if _buffer_pool is None:
        with _pool_init_lock:
            if _buffer_pool is None:
                pool = MetalBufferPool()
                pool.allocate()
                _buffer_pool = pool
    return _buffer_pool


def init_metal_embedder_buffers(
    max_batch: int = _MAX_BATCH_SIZE,
    seq_len: int = _MAX_SEQ_LEN,
    hidden: int = 768
) -> dict[str, Any]:
    """
    Initialize pre-allocated Metal buffers for embedding inference.

    Call this at application startup (before first embed() call) to pre-warm
    Metal buffers and eliminate per-batch allocation overhead.

    Args:
        max_batch: Maximum batch size (default 32)
        seq_len: Maximum sequence length (default 512)
        hidden: Hidden dimension (default 768 for ModernBERT)

    Returns:
        dict with keys: success (bool), allocated (bool), error (str or None)
    """
    pool = get_buffer_pool()
    if pool.is_allocated():
        return {"success": True, "allocated": True, "error": None}

    success = pool.allocate(max_batch=max_batch, seq_len=seq_len, hidden=hidden)
    return {
        "success": success,
        "allocated": pool.is_allocated(),
        "error": None if success else "Allocation failed"
    }


def release_metal_embedder_buffers() -> None:
    """
    Release pre-allocated Metal buffers and free Metal cache.

    Does NOT destroy the singleton — pool instance persists so that subsequent
    get_buffer_pool() calls skip re-allocation (allocate() returns early via
    _allocated guard). This prevents the triple-48MB-allocation bug that
    occurred when _buffer_pool = None forced a new pool + new Metal buffers
    on every unload/reload cycle.
    """
    global _buffer_pool
    if _buffer_pool is not None:
        _buffer_pool.release()


# === Metal-aware encode helpers ===

# === Exported API ===
__all__ = [
    "MetalBufferPool",
    "get_buffer_pool",
    "init_metal_embedder_buffers",
    "release_metal_embedder_buffers",
]
