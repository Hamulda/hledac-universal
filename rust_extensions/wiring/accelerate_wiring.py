"""
Accelerate vDSP Integration Wiring
=================================

Wires rust_extensions/src/accelerate.rs to:
- brain/ner_engine.py

Purpose:
- vDSP FFI for Apple Accelerate framework
- Cosine similarity for embedding comparison
- Batch cosine scores for re-ranking

Integration Point:
- NER entity embedding similarity
- Batch cosine comparison

Usage:
    from rust_extensions.wiring.accelerate_wiring import accelerate_wired
    
    score = accelerate_wired.cosine_similarity(query_emb, candidate_emb)
    scores = accelerate_wired.batch_cosine_scores(query_emb, candidate_embs)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:

logger = logging.getLogger(__name__)

from rust_extensions.integrations import get_accelerate

_accelerate = get_accelerate()

def accelerate_wired():
    """Get the wired accelerate integration."""
    return _accelerate

def cosine_similarity(
    vec_a: list[float],
    vec_b: list[float],
) -> float:
    """
    Compute cosine similarity between two vectors.

    Uses vDSP on macOS when available, falls back to Python.

    Args:
        vec_a: First vector
        vec_b: Second vector

    Returns:
        Cosine similarity score [-1.0, 1.0]
    """
    return _accelerate.cosine_similarity(vec_a, vec_b)

def batch_cosine_scores(
    query: list[float],
    candidates: list[list[float]],
) -> list[float]:
    """
    Compute cosine similarity between query and batch of candidates.

    Args:
        query: Query vector
        candidates: List of candidate vectors

    Returns:
        List of cosine similarity scores, one per candidate.
    """
    return _accelerate.batch_cosine_scores(query, candidates)

def embedding_similarity_scores(
    query_embedding: list[float],
    candidate_embeddings: list[list[float]],
    top_k: int | None = None,
) -> list[tuple[int, float]]:
    """
    Get similarity scores for embeddings, optionally limited to top-K.

    Args:
        query_embedding: Query vector
        candidate_embeddings: List of candidate vectors
        top_k: If set, return only top-K results

    Returns:
        List of (index, score) tuples sorted by score descending.
    """
    scores = batch_cosine_scores(query_embedding, candidate_embeddings)

    indexed_scores = [(i, s) for i, s in enumerate(scores)]

    # Sort by score descending
    indexed_scores.sort(key=lambda x: x[1], reverse=True)

    if top_k is not None:
        return indexed_scores[:top_k]
    return indexed_scores

if _accelerate.available:
    backend = _accelerate.backend
    logger.info(f"[Accelerate] Rust accelerate.rs integration: ENABLED (backend: {backend})")
else:
    logger.info("[Accelerate] Rust accelerate.rs integration: DISABLED (using Python fallback)")
