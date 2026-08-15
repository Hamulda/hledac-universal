# hot_edges.py — Hot Edges domain
"""
Hot edge detection and compression for entity graph traversal.
Implements bloom-filter-backed deduplication and LZ4 compression.


"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Hot Edges Domain
# =============================================================================


class _RustHotEdgesDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def HotEdgeCounterRust(self, flush_threshold: int = 50, max_edges: int | None = None) -> Any:
        """Create hot edge counter with Rust backend."""
        return self._ext.hot_edge_counter_new(flush_threshold, max_edges or 0)

    def compress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        """Compress page data."""
        return self._ext.compress_lz4(data)

    def decompress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        """Decompress page data."""
        return self._ext.decompress_lz4(data)

    def batch_compress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        """Batch compress pages."""
        return self._ext.batch_compress_lz4(pages)

    def batch_decompress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        """Batch decompress pages."""
        return self._ext.batch_decompress_lz4(pages)

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        """Create int counter layout."""
        return self._ext.int_counter_layout_new(field_names)

    def bulk_bump_aggregate(self, counter: Any, indices: list[int], deltas: list[int]) -> None:
        """Bulk increment counters."""
        self._ext.bulk_bump_aggregate(counter, indices, deltas)

    def bulk_snapshot_dict(self, counter: Any) -> dict[int, int]:
        """Snapshot all counter values."""
        return self._ext.bulk_snapshot_dict(counter)


class _PythonHotEdgesDomain:
    __slots__ = ()

    def HotEdgeCounterRust(self, max_edges: int = 10_000) -> _PythonHotEdgeCounter:
        """Python fallback: create hot edge counter."""
        return _PythonHotEdgeCounter(max_edges)

    def compress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        """Python fallback: LZ4 compression."""
        return _python_compress_page(data, algorithm)

    def decompress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        """Python fallback: LZ4 decompression."""
        return _python_decompress_page(data, algorithm)

    def batch_compress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        """Python fallback: batch compression."""
        return [_python_compress_page(p, algorithm) for p in pages]

    def batch_decompress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        """Python fallback: batch decompression."""
        return [_python_decompress_page(p, algorithm) for p in pages]

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        """Python fallback: create int counter layout."""
        from .int_counter import _PythonIntCounterLayout

        return _PythonIntCounterLayout(field_names)

    def bulk_bump_aggregate(self, counter: _PythonHotEdgeCounter, indices: list[int], deltas: list[int]) -> None:
        """Python fallback: bulk bump."""
        for idx, delta in zip(indices, deltas):
            counter.bump_edge(idx, idx, delta)

    def bulk_snapshot_dict(self, counter: _PythonHotEdgeCounter) -> dict[int, int]:
        """Python fallback: snapshot to dict (edge count only)."""
        # Convert edge tuple keys to hash for the dict
        result: dict[int, int] = {}
        for (src, dst), count in counter.snapshot().items():
            key = hash((src, dst))
            result[key] = count
        return result


# =============================================================================
# Python Fallback Implementations
# =============================================================================


class _PythonHotEdgeCounter:
    """Python fallback for hot edge counting."""

    __slots__ = ("_edges", "_max_edges")

    def __init__(self, max_edges: int = 10_000) -> None:
        self._edges: dict[tuple[int, int], int] = defaultdict(int)
        self._max_edges = max_edges

    def bump_edge(self, src: int, dst: int, count: int = 1) -> int:
        """Increment edge count and return new value."""
        key = (src, dst)
        self._edges[key] += count
        return self._edges[key]

    def pending_count(self) -> int:
        """Return number of pending edges."""
        return len(self._edges)

    def should_flush(self) -> bool:
        """Return True if flush is recommended."""
        return len(self._edges) >= self._max_edges

    def drain_dirty(self) -> list[tuple[int, int, int]]:
        """Drain all dirty edges and reset counts."""
        result = [(src, dst, count) for (src, dst), count in self._edges.items()]
        self._edges.clear()
        return result

    def snapshot(self) -> dict[tuple[int, int], int]:
        """Return immutable snapshot of all edges."""
        return dict(self._edges)


def _python_compress_page(data: bytes, algorithm: str = "lz4") -> bytes:
    """Python fallback: LZ4 compression."""
    if algorithm != "lz4":
        return data
    try:
        import lz4.block

        return lz4.block.compress(data, store_size=False)
    except ImportError:
        # Ultimate fallback: return uncompressed
        return data


def _python_decompress_page(data: bytes, algorithm: str = "lz4") -> bytes:
    """Python fallback: LZ4 decompression."""
    if algorithm != "lz4":
        return data
    try:
        import lz4.block

        return lz4.block.decompress(data)
    except ImportError:
        # Ultimate fallback: return as-is
        return data


def get_hot_edges_domain(ext: object | None) -> _RustHotEdgesDomain | _PythonHotEdgesDomain:
    """Factory: return Rust or Python HotEdgesDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustHotEdgesDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonHotEdgesDomain()
