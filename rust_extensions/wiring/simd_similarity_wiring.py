"""
SIMD Similarity Rust Integration Wiring
=====================================

Wires rust_extensions/src/simd_similarity.rs to:
- intel/ re-ranking modules
- brain/embedding similarity

Purpose:
- SIMD batch cosine similarity for re-ranking
- NEON (M1) / SSE3 (x86) acceleration
- Pre-normalized candidate caching

Integration Point:
- Embedding-based re-ranking
- Similarity search results

Usage:
    from rust_extensions.wiring.simd_similarity_wiring import simd_similarity_wired
    
    top_results = simd_similarity_wired.batch_cosine_scores(
        query_embedding,
        candidate_embeddings,
        top_k=10
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:

logger = logging.getLogger(__name__)

from rust_extensions.integrations import get_simd_similarity

_simd_similarity = get_simd_similarity()

def simd_similarity_wired():
    """Get the wired SIMD similarity integration."""
    return _simd_similarity

def batch_cosine_scores(
    query_embedding: list[float],
    candidate_embeddings: list[list[float]],
    top_k: int = 10,
) -> list[tuple[int, float]]:
    """
    Compute cosine similarity, return top-K results.

    Uses SIMD acceleration when available.

    Args:
        query_embedding: Query vector
        candidate_embeddings: List of candidate vectors
        top_k: Number of top results to return

    Returns:
        List of (index, score) tuples sorted by score descending.
    """
    return _simd_similarity.batch_cosine_scores(
        query_embedding, candidate_embeddings, top_k
    )

def rerank_embeddings(
    query_embedding: list[float],
    candidate_embeddings: list[list[float]],
    top_k: int = 10,
) -> list[dict]:
    """
    Re-rank embeddings by similarity to query.

    Args:
        query_embedding: Query embedding vector
        candidate_embeddings: List of candidate embeddings
        top_k: Number of results to return

    Returns:
        List of dicts with index, score, and rank.
    """
    scores = batch_cosine_scores(query_embedding, candidate_embeddings, top_k)

    return [
        {"index": idx, "score": score, "rank": rank + 1}
        for rank, (idx, score) in enumerate(scores)
    ]

def batch_hamming_scores(
    query_packed: list[int],
    candidates_packed: list[int],
    num_candidates: int,
    num_bytes: int,
) -> list[float]:
    """
    Compute Hamming similarity scores between query and candidates.

    Hamming similarity = 1.0 - (hamming_distance / max_bits)
    where max_bits = num_bytes * 8.

    Uses SIMD acceleration when available (NEON on M1, SSE3 on x86).

    Args:
        query_packed: Query as list of bytes (0-255)
        candidates_packed: Flat list of bytes for all candidates
        num_candidates: Number of candidate vectors
        num_bytes: Bytes per vector (must be 1-256)

    Returns:
        List of similarity scores in [0.0, 1.0]
    """
    return _simd_similarity.batch_hamming_scores(
        query_packed, candidates_packed, num_candidates, num_bytes
    )

def similarity_matrix(
    embeddings_a: list[list[float]],
    embeddings_b: list[list[float]] | None = None,
) -> list[list[float]]:
    """
    Compute pairwise similarity matrix between embeddings.

    Args:
        embeddings_a: First set of embeddings
        embeddings_b: Second set (if None, computes self-similarity)

    Returns:
        Matrix of similarity scores.
    """
    if embeddings_b is None:
        embeddings_b = embeddings_a

    # Pre-normalize candidates
    normalized_b = [_normalize(v) for v in embeddings_b]
    normalized_a = [_normalize(v) for v in embeddings_a]

    # Compute similarities
    matrix = []
    for emb_a in normalized_a:
        row = []
        for emb_b in normalized_b:
            score = sum(a * b for a, b in zip(emb_a, emb_b))
            row.append(score)
        matrix.append(row)

    return matrix

def _normalize(vec: list[float]) -> list[float]:
    """L2 normalize a vector."""
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0:
        return vec
    return [v / norm for v in vec]

if _simd_similarity.available:
    logger.info("[SIMDSimilarity] Rust simd_similarity.rs integration: ENABLED")
else:
    logger.info("[SIMDSimilarity] Rust simd_similarity.rs integration: DISABLED (using Python fallback)")
