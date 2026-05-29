"""
MMR - Maximal Marginal Relevance for diversity in search results.

ROLE: Context optimization component for reranking candidates.

Features:
- Diversity-aware reranking using MMR algorithm
- Configurable lambda parameter (balance relevance vs diversity)
- Used for reranking semantic search candidates before report generation

Reference:
- Carbonell & Goldstein (1998): "The Use of MMR, Diversity-Based Reranking"
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def maximal_marginal_relevance(
    query_vector: np.ndarray,
    candidate_vectors: list[np.ndarray],
    top_k: int = 5,
    lambda_param: float = 0.5
) -> list[int]:
    """
    Select top-k diverse candidates using Maximal Marginal Relevance.

    MMR formula: argmax_{d in C\\D} [lambda * sim(d, q) - (1-lambda) * max_{d' in D} sim(d, d')]

    Args:
        query_vector: Query embedding vector, shape (1, dim) or (dim,).
        candidate_vectors: List of candidate embedding vectors.
        top_k: Number of candidates to select.
        lambda_param: Balance between relevance (1.0) and diversity (0.0).
                      Default 0.5 balances both equally.

    Returns:
        List of indices of selected candidates, in selection order.

    Example:
        >>> import numpy as np
        >>> query = np.array([[1.0, 0.0, 0.0]])
        >>> candidates = [
        ...     np.array([0.99, 0.01, 0.0]),   # similar to query
        ...     np.array([0.5, 0.5, 0.0]),     # diverse
        ...     np.array([0.98, 0.02, 0.0]),  # similar to first
        ... ]
        >>> selected = maximal_marginal_relevance(query, candidates, top_k=2)
        >>> # Should pick one similar + one diverse
    """
    if len(candidate_vectors) == 0:
        return []

    if len(candidate_vectors) <= top_k:
        return list(range(len(candidate_vectors)))

    # Normalize query vector
    if query_vector.ndim == 2:
        query_vector = query_vector.squeeze(0)

    query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-8)

    # Normalize candidate vectors
    cand_norms = []
    for c in candidate_vectors:
        c_norm = np.linalg.norm(c) + 1e-8
        cand_norms.append(c / c_norm)
    cand_matrix = np.stack(cand_norms, axis=0) if cand_norms else np.array([])

    selected: list[int] = []
    remaining = set(range(len(candidate_vectors)))

    # Pre-normalize all candidates into a matrix (vectorized)
    cand_matrix = np.stack(cand_norms, axis=0) if cand_norms else np.array([])

    for _ in range(top_k):
        if not remaining:
            break

        remaining_list = list(remaining)
        cand_subset = cand_matrix[remaining_list]

        # Relevance: cosine similarity to query — vectorized
        relevances = np.dot(cand_subset, query_norm)

        # Diversity: max similarity to already selected — vectorized
        if selected:
            selected_matrix = cand_matrix[selected]
            diversity = np.max(np.dot(selected_matrix, cand_subset.T), axis=0)
        else:
            diversity = np.zeros(len(remaining_list))

        # MMR score per candidate — fully vectorized
        mmr_scores = lambda_param * relevances - (1 - lambda_param) * diversity

        # Select best and update state
        best_local_idx = int(np.argmax(mmr_scores))
        best_idx = remaining_list[best_local_idx]
        selected.append(best_idx)
        remaining.remove(best_idx)

    return selected


def rerank_with_mmr(
    query_vector: np.ndarray,
    candidates: list[tuple[str, np.ndarray]],
    top_k: int = 5,
    lambda_param: float = 0.5
) -> list[tuple[str, float]]:
    """
    Rerank candidates using MMR and return with relevance scores.

    Args:
        query_vector: Query embedding vector.
        candidates: List of (id, embedding_vector) tuples.
        top_k: Number of candidates to return.
        lambda_param: Balance between relevance and diversity.

    Returns:
        List of (id, relevance_score) tuples, reranked by MMR.
    """
    if not candidates:
        return []

    ids = [c[0] for c in candidates]
    vectors = [c[1] for c in candidates]

    selected_indices = maximal_marginal_relevance(
        query_vector, vectors, top_k=top_k, lambda_param=lambda_param
    )

    # Compute relevance scores for selected candidates
    results = []
    for idx in selected_indices:
        # Cosine similarity to query as relevance score
        if query_vector.ndim == 2:
            q = query_vector.squeeze(0)
        else:
            q = query_vector

        v = vectors[idx]
        q_norm = q / (np.linalg.norm(q) + 1e-8)
        v_norm = v / (np.linalg.norm(v) + 1e-8)
        relevance = float(np.dot(q_norm, v_norm))

        results.append((ids[idx], relevance))

    return results
