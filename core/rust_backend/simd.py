# simd.py — SIMD / Cosine Similarity domain
"""
SIMD-accelerated cosine similarity for vector comparison.
Used for semantic similarity calculations in MLX inference pipeline.

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# SIMD / Cosine Similarity Domain
# =============================================================================


class _RustSimdDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        return self._ext.simd_cosine_similarity(a, b)

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        """Compute cosine similarity between query and multiple vectors."""
        return self._ext.simd_batch_cosine_similarity(vectors, query)


class _PythonSimdDomain:
    __slots__ = ()

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Python fallback: compute cosine similarity."""
        return _python_cosine_similarity(a, b)

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        """Python fallback: batch cosine similarity."""
        return _python_batch_cosine_similarity(vectors, query)


def _python_cosine_similarity(a: list[float], b: list[float]) -> float:
    """Python fallback: compute cosine similarity between two vectors."""
    if len(a) != len(b) or len(a) == 0:
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def _python_batch_cosine_similarity(vectors: list[list[float]], query: list[float]) -> list[float]:
    """Python fallback: compute cosine similarity for multiple vectors."""
    if not vectors or not query:
        return []
    return [_python_cosine_similarity(v, query) for v in vectors]


def get_simd_domain(ext: object | None) -> _RustSimdDomain | _PythonSimdDomain:
    """Factory: return Rust or Python SimdDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustSimdDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonSimdDomain()
