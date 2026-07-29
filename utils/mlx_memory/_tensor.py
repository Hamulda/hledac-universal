"""
utils/mlx_memory/_tensor.py — SharedTensor Zero-Copy Wrapper (F330-MLX-DUP-007)

Wrapper around mlx.core.array enabling reference passing semantics.
True zero-copy requires Metal buffer — this is the envelope that will
support it once Metal buffer sharing is implemented.
"""

from typing import TYPE_CHECKING, Any

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


class SharedTensor:
    """
    Zero-copy wrapper for MLX arrays.

    Currently wraps an mlx.core.array. When Metal buffer sharing is
    implemented, this will provide true zero-copy semantics across
    thread/executor boundaries.

    Usage:
        t = SharedTensor([1.0, 2.0, 3.0])
        arr = t.array  # underlying mlx.core.array
    """

    __slots__ = ("_array",)

    def __init__(self, data: Any) -> None:
        mx = _get_mx()
        if mx is None:
            raise RuntimeError("MLX not available — cannot create SharedTensor")
        if isinstance(data, mx.array):
            self._array: mx.array = data
        else:
            self._array = mx.array(data)

    @property
    def array(self) -> mx.array:
        """Return the underlying mlx.core.array."""
        return self._array

    def to_list(self) -> Any:
        """Convert to Python list."""
        return self._array.tolist()

    def __repr__(self) -> str:
        return f"SharedTensor(shape={self._array.shape}, dtype={self._array.dtype})"
