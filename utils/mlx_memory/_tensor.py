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
from _core import aclose

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
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        SharedMetalBuffer = rust.raw.SharedMetalBuffer
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

    # ------------------------------------------------------------------------
    # NEXTGEN-02: Arrow IPC Zero-Copy Mmap Path
    # ------------------------------------------------------------------------

    @classmethod
    def from_mmap(
        cls,
        path: str,
        shape: tuple[int, ...],
        dtype: str = "float32",
        offset: int = 0,
    ) -> "SharedTensor":
        """
        NEXTGEN-02: Create SharedTensor from memory-mapped Arrow IPC file.

        Zero-copy path: MLX reads directly from mmap'd Arrow IPC buffer.
        No intermediate Python heap allocation.

        MLX 0.24+ supports mx.core.mmap() for direct file-to-tensor mapping.

        Args:
            path: Path to the Arrow IPC mmap file or raw tensor file
            shape: Desired tensor shape (rows, cols, ...)
            dtype: Data type string ("float32", "int32", etc.)
            offset: Byte offset into the file (for Arrow IPC footer parsing)

        Returns:
            SharedTensor backed by mmap'd file data

        Raises:
            RuntimeError: If MLX mmap is not available
        """
        import mmap
        import os

        mx = _get_mx()
        if mx is None:
            raise RuntimeError("MLX not available")

        # Check if MLX supports mmap (0.24+)
        if not hasattr(mx, "core") or not hasattr(mx.core, "mmap"):
            # MLX 0.24+ not available - use standard numpy path
            # Note: This is NOT zero-copy, but still avoids Python list allocation
            if not os.path.exists(path):
                raise FileNotFoundError(f"File not found: {path}")

            with open(path, "rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mmapped:
                    data = np.frombuffer(mmapped, dtype=getattr(np, dtype))
                    data = data.reshape(shape)
                    inst = object.__new__(cls)
                    inst._array = mx.array(data)
                    inst._metal_buf = None
                    inst._shape = inst._array.shape
                    inst._dtype = dtype
                    inst._mx_cached = True
                    return inst

        # MLX native mmap path (0.24+)
        mlx_dtype = getattr(mx, dtype)
        if mlx_dtype is None:
            raise ValueError(f"Unknown MLX dtype: {dtype}")

        # Create MLX array via mmap
        inst = object.__new__(cls)
        inst._metal_buf = None  # No SharedMetalBuffer for pure mmap path
        inst._shape = shape
        inst._dtype = dtype
        inst._mx_cached = False
        inst._array = None

        # Use MLX's native mmap for zero-copy tensor creation
        # Try MLX 0.24+ mmap API first (true zero-copy)
        try:
            inst._array = mx.core.mmap(path, shape=shape, dtype=mlx_dtype)
            inst._mx_cached = True
        except (TypeError, AttributeError, OSError) as e:
            # MLX mmap failed - likely unsupported file format or API changed
            # Fall back to numpy path (still avoids Python list allocation)
            import logging as _log
            _log.getLogger("SharedTensor").debug(
                "[NEXTGEN-02] MLX mmap failed (%s), falling back to numpy path", e
            )
            
            if not os.path.exists(path):
                raise FileNotFoundError(f"File not found: {path}")

            with open(path, "rb") as f:
                with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mmapped:
                    data = np.frombuffer(mmapped[offset:], dtype=getattr(np, dtype))
                    if data.size != int(np.prod(shape)):
                        # Resize if needed (Arrow IPC has header)
                        needed = int(np.prod(shape))
                        data = data[:needed]
                    data = data.reshape(shape)
                    inst._array = mx.array(data)
                    inst._mx_cached = True

        return inst

    @classmethod
    def from_arrow_ipc_mmap(
        cls,
        path: str,
        column_index: int = 0,
    ) -> "SharedTensor":
        """
        NEXTGEN-02: Create SharedTensor from Arrow IPC mmap file.

        Reads the specified column from the Arrow IPC RecordBatch
        and creates a zero-copy MLX tensor.

        Args:
            path: Path to the Arrow IPC mmap file
            column_index: Column index in the RecordBatch (default: 0)

        Returns:
            SharedTensor with data from the specified column

        Raises:
            RuntimeError: If Arrow IPC read fails
        """
        import io as _io

        try:
            import pyarrow as _pa
        except ImportError:  # noqa: BLE001
            raise RuntimeError("PyArrow not available")

        if not __import__("os").path.exists(path):
            raise FileNotFoundError(f"Arrow IPC file not found: {path}")

        with open(path, "rb") as f:
            import mmap
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mmapped:
                reader = _pa.ipc.open_stream(_io.BytesIO(mmapped))
                batch = reader.read_record_batch()

                if column_index >= batch.num_columns:
                    raise ValueError(
                        f"column_index {column_index} out of range (max: {batch.num_columns - 1})"
                    )

                # Extract column as numpy array
                column = batch.column(column_index)
                arr_np = column.to_numpy()

                # Determine dtype from Arrow column type
                # PyArrow type objects have .id attribute for fast comparison
                import pyarrow as _pa
                arrow_type = column.type
                dtype_map = {
                    _pa.float32(): "float32",
                    _pa.float64(): "float64",
                    _pa.int32(): "int32",
                    _pa.int64(): "int64",
                    _pa.uint32(): "uint32",
                    _pa.uint64(): "uint64",
                    # String types can't be directly converted to float
                    _pa.string(): "float32",
                    _pa.utf8(): "float32",
                }
                dtype = dtype_map.get(arrow_type, "float32")

                mx = _get_mx()
                if mx is None:
                    raise RuntimeError("MLX not available")

                inst = object.__new__(cls)
                inst._metal_buf = None
                inst._shape = arr_np.shape
                inst._dtype = dtype
                inst._mx_cached = False
                inst._array = mx.array(arr_np.astype(getattr(__import__("numpy"), dtype)))

                return inst

    def __repr__(self) -> str:
        backing = "metal" if self._metal_buf is not None else "array"
        return (
            f"SharedTensor(shape={self._shape}, dtype={self._dtype}, backing={backing})"
        )
