# simd.py — SIMD / Cosine Similarity domain
"""
SIMD-accelerated cosine similarity for vector comparison.
Used for semantic similarity calculations in MLX inference pipeline.

[SAFE-3] FFI Circuit Breaker integration for simd_similarity module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose
from utils._patterns import cosine_similarity, batch_cosine_similarity  # noqa: E402

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# [SAFE-3] FFI Circuit Breaker
try:
    from hledac.universal._core.ffi_circuit_breaker import (
        FFI_MODULE_SIMD_SIMILARITY,
        get_ffi_circuit_breaker,
    )
    _FFI_CB_AVAILABLE = True
except ImportError:
    _FFI_CB_AVAILABLE = False
    FFI_MODULE_SIMD_SIMILARITY = "simd_similarity"


# =============================================================================
# SIMD / Cosine Similarity Domain
# =============================================================================


class _RustSimdDomain:
    """
    [SAFE-3] Rust SIMD domain with FFI circuit breaker.

    Properly delegates to simd_similarity.rs via batch_cosine_scores.
    Uses batch API for both single and batch operations to leverage NEON/SSE3 SIMD.
    """
    __slots__ = ("_ext", "_ffi_cb", "_batch_fn", "_batch_npy_fn")

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext
        # [SAFE-3] Initialize FFI circuit breaker
        self._ffi_cb = get_ffi_circuit_breaker() if _FFI_CB_AVAILABLE else None
        # Cache Rust batch functions for performance
        self._batch_fn = getattr(ext, "batch_cosine_scores", None)
        self._batch_npy_fn = getattr(ext, "batch_cosine_scores_npy", None)

    def _rust_batch_scores(
        self, vectors: list[list[float]], query: list[float]
    ) -> list[float]:
        """
        Call Rust batch_cosine_scores with proper marshaling.

        Marshals nested lists to flat arrays + dimensions as expected by Rust API.
        """
        if not vectors or not query:
            return []

        # Flatten: [v1, v2, ...] + query → single query batch
        all_vecs = [query] + vectors
        num_vectors = len(all_vecs)
        dim = len(query)

        # Flatten to 1D arrays
        all_flat: list[float] = []
        for vec in all_vecs:
            all_flat.extend(vec)

        # Call Rust: batch_cosine_scores(query_flat, candidates_flat, num_queries, num_candidates, dim)
        # Returns Q×N matrix where Q=1 (single query) and N=len(vectors)
        try:
            if self._batch_npy_fn is not None:
                # Prefer zero-copy npy path
                result: list[list[float]] = self._batch_npy_fn(
                    all_flat, all_flat, num_vectors, num_vectors, dim
                )
            elif self._batch_fn is not None:
                result = self._batch_fn(
                    all_flat, all_flat, num_vectors, num_vectors, dim
                )
            else:
                return batch_cosine_similarity(vectors, query)

            # result is Q×N matrix; row 0 is self-similarity, rows 1..N are query vs candidates
            # We want query vs candidates (skip row 0 self-similarity)
            if len(result) > 1 and len(result[1]) == len(vectors):
                return result[1]
            return batch_cosine_similarity(vectors, query)
        except Exception:  # noqa: BLE001
            return batch_cosine_similarity(vectors, query)

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors with circuit breaker."""
        if self._ffi_cb is not None:
            def rust_call() -> float:
                scores = self._rust_batch_scores([b], a)
                return scores[0] if scores else 0.0
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMD_SIMILARITY, rust_call, a, b
            )
            if result.success:
                return float(result.value)  # type: ignore[return-value]
            return cosine_similarity(a, b)
        scores = self._rust_batch_scores([b], a)
        return scores[0] if scores else 0.0

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        """Compute cosine similarity between query and multiple vectors with circuit breaker."""
        if self._ffi_cb is not None:
            def rust_call() -> list[float]:
                return self._rust_batch_scores(vectors, query)
            result = self._ffi_cb.call_or_fallback(
                FFI_MODULE_SIMD_SIMILARITY, rust_call, vectors, query
            )
            if result.success:
                return list(result.value)  # type: ignore[return-value]
            return batch_cosine_similarity(vectors, query)
        return self._rust_batch_scores(vectors, query)


class _PythonSimdDomain:
    __slots__ = ()

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Python fallback: compute cosine similarity."""
        return cosine_similarity(a, b)

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        """Python fallback: batch cosine similarity."""
        return batch_cosine_similarity(vectors, query)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Python fallback: compute cosine similarity between two vectors."""
    if len(a) != len(b) or len(a) == 0:
        return 0.0

    dot_product = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5

    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)


def batch_cosine_similarity(vectors: list[list[float]], query: list[float]) -> list[float]:
    """Python fallback: compute cosine similarity for multiple vectors."""
    if not vectors or not query:
        return []
    return [cosine_similarity(v, query) for v in vectors]


def get_simd_domain(ext: object | None) -> _RustSimdDomain | _PythonSimdDomain:
    """Factory: return Rust or Python SimdDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustSimdDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonSimdDomain()
