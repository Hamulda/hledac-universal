"""
Embedding Pipeline - Semantic Search Integration (P13)
====================================================

P13: Embedding pipeline for semantic search with MMR and RRF fusion.

ROLE: Primary embedder using MLXEmbeddingManager from core.mlx_embeddings.
Singleton pattern: uses MLXEmbeddingManager singleton, loads once and reuses.

Features:
- Batch embedding generation for document indexing (256d MRL)
- Query embedding for semantic search (async)
- Matryoshka Representation Learning (MRL) - 256d truncation
- Memory guard: skip if RSS > 6.5 GB
- Automatic memory release after batch processing

Data contracts:
- generate_embeddings: list[str] → np.ndarray float32 shape (N, 256)
- embed_query_async: str → np.ndarray float32 shape (256,)
- embed_document: str → np.ndarray float32 shape (256,)

Anti-patterns:
- No blocking event loop: all MLX operations sync, wrapped in executor for async
- No PyTorch: uses MLX only
- No model swaps mid-pipeline: singleton ensures single model
"""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import List, Optional

import numpy as np
import psutil

from hledac.universal.utils.exceptions import MemoryPressureError

logger = logging.getLogger(__name__)

# MRL dimension (Matryoshka Representation Learning) - 256d output
_EMBEDDING_DIM = 256
_BATCH_SIZE = 16

# P19: Estimated embedding model size (ModernBERT ~500MB in 4bit)
_ESTIMATED_EMBEDDING_MODEL_SIZE_GB = 0.5


def _get_embedder():
    """
    Get MLXEmbeddingManager singleton.

    Uses core.mlx_embeddings.get_embedding_manager() for ModernBERT via MLX.
    P20: Changed to local import to break circular dependency on hledac.config.
    """
    from hledac.universal.core.mlx_embeddings import get_embedding_manager
    return get_embedding_manager()


def _get_current_rss_gb() -> float:
    """Get current RSS memory in GB. P19: For memory guard checks."""
    try:
        return psutil.Process().memory_info().rss / 1e9
    except Exception:
        return 0.0


def _check_memory_before_load(max_rss_gb: float, model_size_gb: float) -> None:
    """
    Check memory before model load. P19: Memory guard implementation.

    Args:
        max_rss_gb: Maximum allowed RSS before loading
        model_size_gb: Estimated model size to load

    Raises:
        MemoryPressureError: If RSS too high to safely load model
    """
    current_rss = _get_current_rss_gb()
    threshold = max_rss_gb - model_size_gb
    if current_rss > threshold:
        raise MemoryPressureError(
            f"[EMBED] Memory pressure: RSS {current_rss:.2f}GB > threshold {threshold:.2f}GB "
            f"(max_rss_gb={max_rss_gb}, model_size_gb={model_size_gb}). "
            f"Skipping embedder load."
        )


def _check_memory_guard() -> bool:
    """
    Check if memory allows embedding generation.

    Returns True if memory is OK (can proceed), False if should skip.
    """
    try:
        import psutil
        process = psutil.Process()
        rss = process.memory_info().rss
        threshold = 6.5 * 1024**3  # 6.5 GB
        if rss > threshold:
            logger.warning(
                f"[EMBED] Memory guard triggered: RSS={rss / 1024**3:.2f}GB > 6.5GB threshold. "
                f"Skipping embedding generation."
            )
            return False
        return True
    except ImportError:
        logger.debug("[EMBED] psutil not available, skipping memory guard")
        return True
    except Exception as e:
        logger.debug(f"[EMBED] Memory check failed: {e}, proceeding anyway")
        return True


def _release_embedder() -> None:
    """Release embedder from memory if loaded."""
    try:
        embedder = _get_embedder()
        if embedder.is_loaded:
            embedder.unload()
            logger.info("[EMBED] MLXEmbeddingManager unloaded")
    except Exception as e:
        logger.debug(f"[EMBED] Failed to unload embedder: {e}")


def generate_embeddings(texts: List[str], batch_size: int = _BATCH_SIZE) -> np.ndarray:
    """
    Generate embeddings for a list of texts using ModernBERT via MLX.

    Uses MRL (Matryoshka Representation Learning) to truncate embeddings
    to 256 dimensions for efficient storage and search.

    Args:
        texts: List of text strings to embed.
        batch_size: Batch size for processing (default 16).

    Returns:
        numpy ndarray dtype=float32, shape=(len(texts), 256).
        Returns empty array with shape (0, 256) if memory guard triggers.

    Raises:
        RuntimeError: If embedder fails to initialize.
    """
    if not texts:
        return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)

    # Memory guard check
    if not _check_memory_guard():
        logger.warning("[EMBED] Skipping embedding generation due to memory pressure")
        return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)

    embedder = _get_embedder()

    try:
        # Use encode with truncate_dim for MRL 256d output
        embeddings = embedder.encode(
            texts,
            batch_size=batch_size,
            normalize=True,
            truncate_dim=_EMBEDDING_DIM,
        )

        # Ensure float32 dtype
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)

        # Ensure correct shape
        if embeddings.shape[1] > _EMBEDDING_DIM:
            embeddings = embeddings[:, :_EMBEDDING_DIM]
        elif embeddings.shape[1] < _EMBEDDING_DIM:
            pad = np.zeros((embeddings.shape[0], _EMBEDDING_DIM - embeddings.shape[1]), dtype=np.float32)
            embeddings = np.hstack([embeddings, pad])

        logger.debug(f"[EMBED] Generated embeddings shape: {embeddings.shape}")
        return embeddings

    except Exception as e:
        logger.error(f"[EMBED] Batch embedding failed: {e}")
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)

    finally:
        # Release embedder after batch processing to free memory
        _release_embedder()


def embed_query(text: str) -> np.ndarray:
    """
    Generate embedding for a single query (sync).

    Uses search_query prefix for asymmetric retrieval.

    Args:
        text: Query text to embed.

    Returns:
        numpy ndarray dtype=float32, shape=(256,).
        Returns array of zeros if memory guard triggers or on error.
    """
    # Memory guard check
    if not _check_memory_guard():
        logger.warning("[EMBED] Skipping query embedding due to memory pressure")
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)

    embedder = _get_embedder()

    try:
        # Use embed_query with truncate_dim for MRL 256d output
        emb = embedder.embed_query(text, truncate_dim=_EMBEDDING_DIM)

        # Ensure float32 and correct shape
        if emb.dtype != np.float32:
            emb = emb.astype(np.float32)

        # Flatten to 1D
        if emb.ndim == 2:
            emb = emb.squeeze(0)

        # Ensure correct dimension
        if len(emb) > _EMBEDDING_DIM:
            emb = emb[:_EMBEDDING_DIM]
        elif len(emb) < _EMBEDDING_DIM:
            emb = np.pad(emb, (0, _EMBEDDING_DIM - len(emb)))

        return emb

    except Exception as e:
        logger.error(f"[EMBED] Query embedding failed: {e}")
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)


def embed_document(text: str) -> np.ndarray:
    """
    Generate embedding for a document (for indexing).

    Uses search_document prefix for indexing.

    Args:
        text: Document text to embed.

    Returns:
        numpy ndarray dtype=float32, shape=(256,).
    """
    if not _check_memory_guard():
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)

    embedder = _get_embedder()

    try:
        emb = embedder.embed_document(text, truncate_dim=_EMBEDDING_DIM)

        if emb.dtype != np.float32:
            emb = emb.astype(np.float32)

        if emb.ndim == 2:
            emb = emb.squeeze(0)

        if len(emb) > _EMBEDDING_DIM:
            emb = emb[:_EMBEDDING_DIM]
        elif len(emb) < _EMBEDDING_DIM:
            emb = np.pad(emb, (0, _EMBEDDING_DIM - len(emb)))

        return emb

    except Exception as e:
        logger.error(f"[EMBED] Document embedding failed: {e}")
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)


async def generate_embeddings_async(texts: List[str], batch_size: int = _BATCH_SIZE) -> np.ndarray:
    """
    Async wrapper for generate_embeddings.

    Runs embedding generation in thread executor to avoid blocking event loop.

    Args:
        texts: List of text strings to embed.
        batch_size: Batch size for processing.

    Returns:
        numpy ndarray dtype=float32, shape=(len(texts), 256).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, generate_embeddings, texts, batch_size
    )


async def embed_query_async(text: str) -> np.ndarray:
    """
    Async wrapper for embed_query.

    Runs query embedding in thread executor to avoid blocking event loop.

    P13 integration: used by _generate_and_store_report for RAG context.

    Args:
        text: Query text to embed.

    Returns:
        numpy ndarray dtype=float32, shape=(256,).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_query, text)


# P13 integration point for brain-level lifecycle management
# P19: Memory guard - configurable max_rss_gb (default 5.5 for M1 8GB)
_embed_max_rss_gb: float = 5.5

# F197C: Embedding context depth tracker for M1 memory guard.
# Prevents JS renderer (Camoufox/nodriver) from running simultaneously with
# loaded embedding model on M1 Air 8GB. BROKEN check in public_fetcher used
# semaphore._value which is always <= max, causing the guard to always fire.
# Increment before model load attempt, decrement after unload — balanced per call.
import threading
_embedding_depth: int = 0
_embedding_depth_lock = threading.Lock()


def is_embedding_context_active() -> bool:
    """F197C: True if we are currently in an active embedding lifecycle context."""
    with _embedding_depth_lock:
        return _embedding_depth > 0


def set_embed_memory_limit(max_rss_gb: float) -> None:
    """P19: Set max RSS GB threshold for embedder memory guard."""
    global _embed_max_rss_gb
    _embed_max_rss_gb = max_rss_gb


def load_embedding_model() -> bool:
    """
    Load the embedding model into memory.

    Called by brain-level lifecycle before embedding operations.
    Uses MLXEmbeddingManager singleton with lazy loading.

    P19: Before loading, checks RSS against max_rss_gb - estimated_model_size.
    If memory pressure detected, skips loading and returns False.

    Returns:
        True if model is loaded or already loaded, False on error or memory pressure.
    """
    # F197C: Increment depth before load attempt (balanced with decrement in unload)
    with _embedding_depth_lock:
        global _embedding_depth
        _embedding_depth += 1
    rss_before: float = 0.0  # initialized before try so except block always sees it
    try:
        # P19: Memory pressure check before load
        rss_before = _get_current_rss_gb()
        _check_memory_before_load(_embed_max_rss_gb, _ESTIMATED_EMBEDDING_MODEL_SIZE_GB)

        embedder = _get_embedder()
        if not embedder.is_loaded:
            embedder._load_model()
        logger.info(f"[EMBED] Embedding model loaded (RSS before={rss_before:.2f}GB)")
        return True
    except MemoryPressureError:
        logger.warning(f"[EMBED] Memory pressure - skipping embedder load (RSS={rss_before:.2f}GB)")
        return False
    except Exception as e:
        logger.error(f"[EMBED] Failed to load embedding model: {e}")
        return False


def unload_embedding_model() -> bool:
    """
    Unload the embedding model from memory.

    Called by brain-level lifecycle after embedding operations complete.
    Uses MLXEmbeddingManager.unload() and triggers gc.collect().

    P19: After unload, verifies RSS dropped by at least model_size.
    Logs warning if RSS didn't drop enough (possible memory leak).

    F197C: Always decrements depth counter (balanced with increment in load,
    even if load was a no-op due to already-loaded model).

    Returns:
        True on success, False on error.
    """
    # F197C: Decrement depth — always balanced with increment in load_embedding_model()
    # _embedding_depth can legitimately be 0 if load was a no-op (already loaded)
    # but we still decrement to keep the pair balanced for the lifecycle caller
    with _embedding_depth_lock:
        global _embedding_depth
        if _embedding_depth > 0:
            _embedding_depth -= 1
    try:
        embedder = _get_embedder()
        if embedder.is_loaded:
            rss_before = _get_current_rss_gb()
            embedder.unload()
            gc.collect()  # Force collection after unload
            rss_after = _get_current_rss_gb()
            dropped = rss_before - rss_after
            expected_drop = _ESTIMATED_EMBEDDING_MODEL_SIZE_GB

            if dropped < expected_drop * 0.5:  # Allow 50% tolerance
                logger.warning(
                    f"[EMBED] RSS did not drop expected amount after unload: "
                    f"dropped={dropped:.2f}GB, expected~{expected_drop:.2f}GB "
                    f"(RSS before={rss_before:.2f}GB, after={rss_after:.2f}GB)"
                )
            else:
                logger.info(f"[EMBED] Embedding model unloaded (RSS dropped={dropped:.2f}GB)")
        return True
    except Exception as e:
        logger.error(f"[EMBED] Failed to unload embedding model: {e}")
        return False


def get_embedding_dimension() -> int:
    """Return the MRL embedding dimension (256)."""
    return _EMBEDDING_DIM
