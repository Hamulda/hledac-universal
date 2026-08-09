"""
hledac.universal.compat.core_simd_similarity — MRL-2 FIX

Binary embeddings (1-bit) + NEON Hamming Index module.
Provides SIMD-accelerated Hamming distance computation for binary embeddings.

Architecture:
- Tier 0: Rust SIMD (batch_hamming_scores) — NEON popcount on aarch64, falls back to portable SWAR
- Tier 1: MLX fallback (popcount lookup table) — used when Rust unavailable

MRL-2 fixes applied:
1. popcount_neon_chunk: Added missing vcntq_u8 instruction for correct bit counting
2. Sign convention: Unified to (>= 0) for consistent pack/unpack
3. Module structure: Created this wrapper for Rust SIMD dispatch

Usage:
    from hledac.universal.compat.core_simd_similarity import batch_hamming_scores

    query_packed = bytes  # num_bytes = (dim + 7) // 8
    candidates_packed = bytes  # N * num_bytes
    scores = batch_hamming_scores(query_packed, candidates_packed, N, num_bytes)
    # Returns list[float] of Hamming similarities in [0.0, 1.0]

Author: Hledac Team
Issue: MRL-2
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Type hints for clarity
if TYPE_CHECKING:
    pass

# Try to import from Rust extension (advanced feature)
_RUST_AVAILABLE = False
_batch_hamming_scores_impl = None

try:
    from hledac_rust_extensions import (
        batch_hamming_scores as _rust_batch_hamming_scores,
        batch_hamming_scores_batched as _rust_batch_hamming_scores_batched,
    )
    _RUST_AVAILABLE = True
    _batch_hamming_scores_impl = _rust_batch_hamming_scores
    logger.debug("[MRL-2] Rust SIMD Hamming loaded (NEON on M1)")
except ImportError:
    logger.debug("[MRL-2] Rust SIMD unavailable — using MLX fallback")
    _RUST_AVAILABLE = False


def batch_hamming_scores(
    query_packed: bytes,
    candidates_packed: bytes,
    num_candidates: int,
    num_bytes: int,
) -> list[float]:
    """
    Compute Hamming similarities for one query against N candidates.

    MRL-2: Uses Rust SIMD when available (NEON popcount), falls back to pure Python.

    Args:
        query_packed: Packed binary query vector (num_bytes).
        candidates_packed: Flattened candidate vectors (N * num_bytes).
        num_candidates: Number of candidates (N).
        num_bytes: Bytes per vector (dim // 8, rounded up).

    Returns:
        List of N similarity scores in [0.0, 1.0], where 1.0 = identical.

    Raises:
        ValueError: If input sizes don't match.
    """
    if _batch_hamming_scores_impl is not None:
        # Rust path — NEON popcount on aarch64, portable SWAR elsewhere
        result = _batch_hamming_scores_impl(
            query_packed,
            candidates_packed,
            num_candidates,
            num_bytes,
        )
        return list(result)

    # MLX fallback path — popcount via lookup table
    return _mlx_hamming_fallback(query_packed, candidates_packed, num_candidates, num_bytes)


def batch_hamming_scores_batched(
    queries_packed: bytes,
    candidates_packed: bytes,
    num_queries: int,
    num_candidates: int,
    num_bytes: int,
) -> list[list[float]]:
    """
    Compute Hamming similarities for multiple queries against same candidate set.

    Args:
        queries_packed: Flattened query vectors (Q * num_bytes).
        candidates_packed: Flattened candidate vectors (N * num_bytes).
        num_queries: Number of queries (Q).
        num_candidates: Number of candidates (N).
        num_bytes: Bytes per vector.

    Returns:
        List of Q lists, each with N similarity scores.
    """
    if _RUST_AVAILABLE and _rust_batch_hamming_scores_batched is not None:
        result = _rust_batch_hamming_scores_batched(
            queries_packed,
            candidates_packed,
            num_queries,
            num_candidates,
            num_bytes,
        )
        return [list(r) for r in result]

    # MLX fallback — call batch_hamming_scores for each query
    results = []
    for q in range(num_queries):
        q_start = q * num_bytes
        q_bytes = queries_packed[q_start:q_start + num_bytes]
        scores = batch_hamming_scores(q_bytes, candidates_packed, num_candidates, num_bytes)
        results.append(scores)
    return results


def _mlx_hamming_fallback(
    query_packed: bytes,
    candidates_packed: bytes,
    num_candidates: int,
    num_bytes: int,
) -> list[float]:
    """
    MLX fallback for Hamming distance (pure Python popcount via lookup table).

    Used when Rust extension is unavailable (non-aarch64, or build without advanced feature).
    Popcount via 4-bit lookup table: popcount(byte) = table[byte & 0x0F] + table[byte >> 4].
    """
    # 4-bit popcount lookup table (0-15 → number of set bits)
    _POPCOUNT_TABLE = bytes(
        (bin(i).count("1") for i in range(16))
    )

    def _popcount_byte(b: int) -> int:
        """Popcount via 4-bit lookup table."""
        return _POPCOUNT_TABLE[b & 0x0F] + _POPCOUNT_TABLE[b >> 4]

    def _popcount_bytes(data: bytes) -> int:
        """Popcount for full byte vector."""
        return sum(_popcount_byte(b) for b in data)

    query = query_packed
    max_bits = num_bytes * 8
    scores: list[float] = []

    for i in range(num_candidates):
        start = i * num_bytes
        end = start + num_bytes
        cand = candidates_packed[start:end]

        # XOR then popcount: number of differing bits
        xor_result = bytes(q ^ c for q, c in zip(query, cand))
        diff_bits = _popcount_bytes(xor_result)

        # Convert to similarity: fewer bits differ = higher similarity
        similarity = 1.0 - (diff_bits / max_bits)
        scores.append(similarity)

    return scores


# Expose feature availability for diagnostics
def is_rust_simd_available() -> bool:
    """Check if Rust SIMD (NEON) path is available."""
    return _RUST_AVAILABLE


def simd_feature_level() -> int:
    """
    Report SIMD feature level.

    Returns:
        2 = Rust SIMD with NEON (M1 optimized)
        1 = Rust SIMD portable (non-aarch64)
        0 = MLX fallback only
    """
    if _RUST_AVAILABLE:
        # Try to get Rust feature level
        try:
            from hledac_rust_extensions import simd_feature_level
            return simd_feature_level()
        except (ImportError, AttributeError):
            return 1  # Rust available, but simd_feature_level not exposed
    return 0
