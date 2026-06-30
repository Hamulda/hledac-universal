"""
embeddings/reranker.py — Batch cosine similarity reranker with Rust SIMD acceleration.

Sprint P2-4: Python wrapper for rust_extensions/src/simd_similarity.rs

Architecture:
  batch_rerank(query_emb, candidates) → scores
    ├── _RUST_SIMD_AVAILABLE → _rust_batch_cosine_scores (NEON/SSE3)
    └── fallback              → _numpy_batch_cosine_scores

Rust API (simd_similarity.rs) — registered directly on hledac_rust_extensions:
  batch_cosine_scores(query_flat, candidates_flat, num_queries, num_candidates, dim)
    → Vec<Vec<f32>>  (Q×N matrix as list of lists)

Invarianty:
  • Always-on: žádné feature flagy, vždy dostupný (NumPy fallback vždy funguje)
  • Fail-safe: chyba → prázdný list / fallback na NumPy
  • Bounded: MAX_QUERIES=100, MAX_CANDIDATES=10_000, MAX_DIM=2048 (hard cap z Rust)
  • M1 8GB safe: žádná alokace mimo NumPy pole, žádné blocking I/O
"""


import logging
from collections.abc import Callable
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rust SIMD detection
# ---------------------------------------------------------------------------

_RUST_SIMD_AVAILABLE = False
# Rust batch_cosine_scores signature: (query_flat, candidates_flat, num_queries, num_candidates, dim) → list[list[float]]
_rust_fn: Callable[..., Any] | None = None

try:
    import hledac_rust_extensions as _rust_mod  # type: ignore[unresolved-import]

    _raw = getattr(_rust_mod, "batch_cosine_scores", None)
    if _raw is not None:
        _rust_fn = _raw
        _RUST_SIMD_AVAILABLE = True
        logger.debug("[reranker] Rust SIMD (batch_cosine_scores) loaded OK")
    else:
        logger.warning("[reranker] hledac_rust_extensions has no batch_cosine_scores")
except ImportError as _exc:
    logger.debug(f"[reranker] Rust extensions not available (ImportError): {_exc}")
except Exception as _exc:
    logger.warning(f"[reranker] Rust extensions load failed: {_exc}")


# ---------------------------------------------------------------------------
# Constants (mirror Rust constants for validation)
# ---------------------------------------------------------------------------

_MAX_DIM: int = 2048
_MAX_CANDIDATES: int = 10_000
_MAX_QUERIES: int = 100


# ---------------------------------------------------------------------------
# NumPy fallback — pure Python batch cosine
# ---------------------------------------------------------------------------

def _numpy_batch_cosine_scores(
    query_emb: np.ndarray,
    candidates: np.ndarray,
) -> np.ndarray:
    """
    NumPy batch cosine similarity (fallback when Rust SIMD unavailable).

    Args:
        query_emb: np.ndarray shape (Q, D) — Q query embeddings
        candidates: np.ndarray shape (N, D) — N candidate embeddings

    Returns:
        np.ndarray shape (Q, N) — cosine similarity scores in [-1, 1]
    """
    q_norm = query_emb / (np.linalg.norm(query_emb, axis=1, keepdims=True) + 1e-8)
    c_norm = candidates / (np.linalg.norm(candidates, axis=1, keepdims=True) + 1e-8)
    return q_norm @ c_norm.T


# ---------------------------------------------------------------------------
# Rust SIMD wrapper — validates → flatten → call → reshape
# ---------------------------------------------------------------------------

def _rust_batch_cosine_scores(
    query_emb: np.ndarray,
    candidates: np.ndarray,
) -> np.ndarray:
    """
    Wrapper around Rust batch_cosine_scores with NumPy I/O.

    Rust API:
      batch_cosine_scores(query_flat, candidates_flat, num_queries, num_candidates, dim)
        → Vec<Vec<f32>>

    Bounds:
      MAX_QUERIES=100, MAX_CANDIDATES=10_000, MAX_DIM=2_048
    """
    q = np.ascontiguousarray(query_emb, dtype=np.float32)
    c = np.ascontiguousarray(candidates, dtype=np.float32)

    num_queries, dim = q.shape
    num_candidates = c.shape[0]

    if num_queries > _MAX_QUERIES:
        raise ValueError(f"Too many queries: {num_queries} > {_MAX_QUERIES}")
    if num_candidates > _MAX_CANDIDATES:
        raise ValueError(f"Too many candidates: {num_candidates} > {_MAX_CANDIDATES}")
    if dim == 0 or dim > _MAX_DIM:
        raise ValueError(f"Dimension out of range: {dim} (must be 1..{_MAX_DIM})")

    query_flat = q.flatten().tolist()
    candidates_flat = c.flatten().tolist()

    # _RUST_SIMD_AVAILABLE guarantees _rust_fn is not None
    assert _rust_fn is not None, "bug: _rust_fn is None despite _RUST_SIMD_AVAILABLE=True"

    # Call Rust SIMD: batch_cosine_scores(query_flat, candidates_flat, num_queries, num_candidates, dim)
    result: list[list[float]] = _rust_fn(
        query_flat,
        candidates_flat,
        num_queries,
        num_candidates,
        dim,
    )

    # Convert list-of-lists → np.ndarray shape (Q, N)
    return np.array(result, dtype=np.float32)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def batch_rerank(
    query_emb: np.ndarray,
    candidates: np.ndarray,
) -> np.ndarray:
    """
    Batch cosine similarity reranking — SIMD-accelerated when available.

    Args:
        query_emb: np.ndarray shape (Q, D) — query embeddings (Q ≥ 1)
        candidates: np.ndarray shape (N, D) — candidate embeddings (N ≥ 1)

    Returns:
        np.ndarray shape (Q, N) — cosine similarity scores in [-1, 1]
        Row i = scores of query i against all N candidates.

    Raises:
        ValueError: if inputs have wrong shape or are empty

    Performance:
        Rust path (M1 NEON / x86 SSE3): ~3-5× faster than NumPy for large batches
        NumPy path: standard vectorized multiply-add (BLAS under np.dot)
    """
    if query_emb.ndim != 2:
        raise ValueError(f"query_emb must be 2D, got {query_emb.ndim}D")
    if candidates.ndim != 2:
        raise ValueError(f"candidates must be 2D, got {candidates.ndim}D")
    if query_emb.shape[1] != candidates.shape[1]:
        raise ValueError(
            f"Embedding dimension mismatch: query={query_emb.shape[1]}, candidates={candidates.shape[1]}"
        )
    if query_emb.size == 0 or candidates.size == 0:
        raise ValueError("query_emb and candidates must be non-empty")

    if _RUST_SIMD_AVAILABLE:
        try:
            return _rust_batch_cosine_scores(query_emb, candidates)
        except Exception as exc:
            logger.warning(f"[reranker] Rust SIMD failed ({exc}), falling back to NumPy")
            # Fall through to NumPy fallback

    return _numpy_batch_cosine_scores(query_emb, candidates)


def batch_rerank_topk(
    query_emb: np.ndarray,
    candidates: np.ndarray,
    top_k: int = 20,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Batch rerank + extract top-K indices per query.

    Args:
        query_emb: np.ndarray shape (Q, D)
        candidates: np.ndarray shape (N, D)
        top_k: number of top candidates to return (default 20)

    Returns:
        (scores, indices) — each np.ndarray shape (Q, top_k)
            scores[q, k] = similarity score of query q to its k-th best candidate
            indices[q, k] = index into candidates array
    """
    if top_k <= 0:
        raise ValueError(f"top_k must be > 0, got {top_k}")

    scores = batch_rerank(query_emb, candidates)  # (Q, N)

    # argpartition is O(N) vs O(N log N) sort — faster for large N
    q = scores.shape[0]
    top_indices = np.zeros((q, top_k), dtype=np.int32)
    top_scores = np.zeros((q, top_k), dtype=np.float32)

    for i in range(q):
        if top_k < scores.shape[1]:
            # Partial sort: get top_k largest
            ind = np.argpartition(scores[i], -top_k)[-top_k:]
            # Sort these top_k by score descending
            order = np.argsort(scores[i, ind])[::-1]
            top_indices[i] = ind[order]
            top_scores[i] = scores[i, ind[order]]
        else:
            # Return all sorted
            order = np.argsort(scores[i])[::-1]
            top_indices[i] = order[:top_k]
            top_scores[i] = scores[i, order[:top_k]]

    return top_scores, top_indices


def rerank_findings(
    findings: list[dict],
    query_emb: np.ndarray,
    top_k: int = 20,
    text_key: str = "rerank_text",
) -> list[dict]:
    """
    Rerank findings by cosine similarity to query embedding.

    Args:
        findings: list of dict objects (must have 'rerank_text' or 'title'+'snippet')
        query_emb: np.ndarray shape (1, D) or (D,) — query embedding
        top_k: number of top findings to return
        text_key: key in finding dict containing text for fallback (default 'rerank_text')

    Returns:
        Top-K findings sorted by cosine similarity descending.
        Falls back to 'confidence' sort if Rust SIMD fails.
    """
    if not findings:
        return []

    # Ensure query_emb is 2D (1, D)
    q_emb = np.atleast_2d(query_emb)
    if q_emb.shape[0] != 1:
        raise ValueError(f"Expected single query embedding (1, D), got {q_emb.shape}")

    # Build candidate embeddings via ModernBERTEmbedder (canonical embedder)
    try:
        from embeddings.modernbert_embedder import ModernBERTEmbedder
        embedder = ModernBERTEmbedder()
        texts = [
            (f.get(text_key) or f"{f.get('title', '')} {f.get('snippet', '')}".strip())[:512]
            for f in findings
        ]
        corp_emb = embedder.encode(texts)  # (N, D)
    except Exception as exc:
        logger.warning(f"[reranker] ModernBERTEmbedder failed ({exc}), using confidence fallback")
        fallback = sorted(
            findings,
            key=lambda x: x.get("confidence", 0.5),
            reverse=True,
        )[:top_k]
        for f in fallback:
            f["_rerank_score"] = f.get("confidence", 0.5)
        return fallback

    # Batch similarity via Rust SIMD or NumPy
    try:
        top_scores, top_indices = batch_rerank_topk(q_emb, corp_emb, top_k=top_k)
    except Exception as exc:
        logger.warning(f"[reranker] batch_rerank_topk failed ({exc}), using confidence fallback")
        fallback = sorted(
            findings,
            key=lambda x: x.get("confidence", 0.5),
            reverse=True,
        )[:top_k]
        for f in fallback:
            f["_rerank_score"] = f.get("confidence", 0.5)
        return fallback

    # Reorder findings by similarity
    reranked = []
    for k in range(top_k):
        idx = int(top_indices[0, k])
        score = float(top_scores[0, k])
        f = dict(findings[idx])
        f["_rerank_score"] = score
        reranked.append(f)

    return reranked
