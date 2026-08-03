"""
utils/mlx_memory/_tensor.py — SharedTensor Zero-Copy Wrapper (F330-MLX-DUP-007)

Wrapper around mlx.core.array enabling reference passing semantics.
Now with Metal buffer backing (SILICON-04) for true zero-copy on M1 UMA.

Architecture:
- SharedTensor(wrapped) → standard mx.array wrapper (backward compatible)
- SharedTensor.from_metal_buffer(buf, shape, dtype) → wraps SharedMetalBuffer
- SharedTensor.from_batch(vectors, dtype) → batch-allocates one Metal buffer
- .array → underlying mx.array (zero-copy when Metal-backed, one copy otherwise)

M1 UMA: when Metal-backed, the same physical pages back both the Rust MTLBuffer
and the MLX array — no copy, no L2 cache eviction.
"""

from typing import TYPE_CHECKING, Any, Optional

import numpy as np

if TYPE_CHECKING:
    import mlx.core as mx

__all__ = ["SharedTensor"]


def _get_mx():
    """Lazily cached mlx.core module reference (ISSUE 3.2 fix).

    Replaced find_spec + sys.modules.get with direct import —
    find_spec loads mlx.core on macOS, violating PLANNER: ZERO MLX invariant.
    """
    try:
        import mlx.core as _mx
        return _mx
    except ImportError:
        return None


def _get_shared_buf():
    """Lazy import of SharedMetalBuffer from Rust extensions (SILICON-04)."""
    try:
        from hledac_rust_extensions import SharedMetalBuffer
        return SharedMetalBuffer
    except ImportError:
        return None


class SharedTensor:
    """
    Zero-copy wrapper for MLX arrays with optional Metal buffer backing.

    Two modes:
    1. Standard: wraps an mlx.core.array (backward compatible)
    2. Metal-backed: wraps a SharedMetalBuffer, MLX array is a view (SILICON-04)

    Usage:
        # Standard mode
        t = SharedTensor([1.0, 2.0, 3.0])

        # Metal-backed mode (zero-copy from Rust/numpy → MLX)
        t = SharedTensor.from_metal_buffer(buf, shape=(1000, 256), dtype="float32")

        # Batch allocation
        t = SharedTensor.from_batch(vectors_list, dtype="float32")

        arr = t.array  # mx.array — zero-copy when Metal-backed
    """

    __slots__ = ("_array", "_metal_buf", "_shape", "_dtype", "_mx_cached")

    def __init__(self, data: Any) -> None:
        """Wrap data in a SharedTensor (standard mode).

        Args:
            data: An mx.array, numpy array, list, or other mx.array-compatible input.
        """
        mx = _get_mx()
        if mx is None:
            raise RuntimeError("MLX not available — cannot create SharedTensor")
        if isinstance(data, mx.array):
            self._array: Any = data
        else:
            self._array = mx.array(data)
        self._metal_buf: Any = None
        self._shape: tuple[int, ...] = self._array.shape
        self._dtype: str = str(self._array.dtype)
        self._mx_cached: bool = True

    @classmethod
    def from_metal_buffer(
        cls,
        buf: Any,
        shape: tuple[int, ...],
        dtype: str = "float32",
    ) -> "SharedTensor":
        """Create a SharedTensor backed by a SharedMetalBuffer (SILICON-04).

        On M1 UMA, the MLX array shares the same physical pages as the
        Metal buffer — true zero-copy, no L2 cache eviction.

        Args:
            buf: SharedMetalBuffer instance (from hledac_rust_extensions)
            shape: Desired tensor shape
            dtype: Data type string ("float32", "int32", etc.)

        Returns:
            SharedTensor with Metal buffer backing
        """
        mx = _get_mx()
        if mx is None:
            raise RuntimeError("MLX not available")

        mlx_dtype = getattr(mx, dtype)
        if mlx_dtype is None:
            raise ValueError(f"Unknown MLX dtype: {dtype}")

        inst = object.__new__(cls)
        inst._metal_buf = buf
        inst._shape = shape
        inst._dtype = dtype
        inst._mx_cached = False
        inst._array = None

        # Create MLX array from the Metal buffer (one copy)
        # On M1 UMA, this wraps the same MTLBuffer physically
        inst._array = buf.to_mlx_array(shape, mlx_dtype)
        inst._mx_cached = True

        return inst

    @classmethod
    def from_batch(
        cls,
        vectors: list[np.ndarray],
        dtype: str = "float32",
    ) -> "SharedTensor":
        """Batch-allocate a Metal buffer and create SharedTensor (SILICON-04).

        Optimized for ANN reranking: N candidate vectors → one Metal buffer
        → one MLX array. Eliminates per-vector mx.array() calls.

        Args:
            vectors: List of 1-D numpy arrays (all same length)
            dtype: Data type string

        Returns:
            SharedTensor backed by a single Metal buffer
        """
        if not vectors:
            raise ValueError("vectors list is empty")

        SharedMetalBuffer = _get_shared_buf()
        if SharedMetalBuffer is None:
            # Fallback: use standard numpy batch path
            mx = _get_mx()
            if mx is None:
                raise RuntimeError("MLX not available")
            batch_np = np.stack(vectors).astype(getattr(np, dtype), copy=False)
            inst = object.__new__(cls)
            inst._array = mx.array(batch_np)
            inst._metal_buf = None
            inst._shape = inst._array.shape
            inst._dtype = dtype
            inst._mx_cached = True
            return inst

        # Metal-backed path
        batch_np = np.stack(vectors).astype(getattr(np, dtype), copy=False)
        buf = SharedMetalBuffer.from_numpy(batch_np)
        return cls.from_metal_buffer(buf, shape=batch_np.shape, dtype=dtype)

    @property
    def array(self) -> Any:
        """Return the underlying mlx.core.array.

        When Metal-backed and the MLX array has been evicted from cache,
        re-creates it from the Metal buffer (one copy).
        """
        if self._mx_cached and self._array is not None:
            return self._array

        mx = _get_mx()
        if mx is None:
            raise RuntimeError("MLX not available")

        if self._metal_buf is not None:
            mlx_dtype = getattr(mx, self._dtype)
            self._array = self._metal_buf.to_mlx_array(self._shape, mlx_dtype)
            self._mx_cached = True
            return self._array

        raise RuntimeError("SharedTensor has no backing data")

    @property
    def is_metal_backed(self) -> bool:
        """Whether this tensor is backed by a Metal shared buffer."""
        return self._metal_buf is not None

    @property
    def shape(self) -> tuple[int, ...]:
        """Tensor shape."""
        return self._shape

    @property
    def dtype_str(self) -> str:
        """Data type as string."""
        return self._dtype

    def to_numpy(self) -> np.ndarray:
        """Convert to numpy array.

        When Metal-backed: zero-copy view into the MTLBuffer.
        When standard: mx.array → numpy (one copy).
        """
        if self._metal_buf is not None:
            return self._metal_buf.to_numpy(list(self._shape), self._dtype)
        return np.array(self._array)

    def to_list(self) -> Any:
        """Convert to Python list."""
        if self._metal_buf is not None:
            return self.to_numpy().tolist()
        return self._array.tolist()

    def evict_mlx_cache(self) -> None:
        """Release the cached MLX array to free Metal memory.

        The Metal buffer is preserved — .array will re-create
        the MLX array on next access.
        """
        if self._array is not None:
            del self._array
            self._array = None
            self._mx_cached = False

    def __repr__(self) -> str:
        backing = "metal" if self._metal_buf is not None else "array"
        return (
            f"SharedTensor(shape={self._shape}, dtype={self._dtype}, backing={backing})"
        )
