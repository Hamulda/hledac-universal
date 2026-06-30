"""
UnifiedEmbeddingManager — Single source for ALL embeddings.

Replaces:
- RAGEngine._fastembed_embedder (FastEmbed BAAI/bge-small-en-v1.5, 384d)
- SemanticStore._model (FastEmbed TextEmbedding, 384d)
- LanceDBIdentityStore._embedder (MLXEmbeddingManager, 256d MRL)

Uses MLXEmbeddingManager as backend with configurable MRL dimension.
Default 512d for backward compatibility with existing 384d code.

M1 8GB: Single model instance, lazy loading, fail-soft degradation.
"""


import asyncio
import threading
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Singleton instance
_unified_manager: UnifiedEmbeddingManager | None = None
_manager_lock = threading.Lock()

# MRL dimensions supported by ModernBERT
SUPPORTED_DIMS = (256, 512, 768)
DEFAULT_DIM = 512  # Backward compat with existing 384d code


class UnifiedEmbeddingManager:
    """
    Single embedding source for entire codebase.

    Wraps MLXEmbeddingManager with unified API compatible with FastEmbed.
    Supports dimensions: 256, 512, 768 (MRL).

    Usage:
        manager = get_unified_embedder()
        embeddings = manager.embed(["text1", "text2"])  # list of lists
        embedding = manager.embed_one("single text")     # single list
    """

    def __init__(
        self,
        dim: int = DEFAULT_DIM,
        model_path: str | Path | None = None,
        lazy_load: bool = True,
    ):
        """
        Initialize unified embedder.

        Args:
            dim: MRL output dimension (256, 512, or 768). Default 512 for backward compat.
            model_path: Optional custom model path.
            lazy_load: Defer model loading until first use.
        """
        if dim not in SUPPORTED_DIMS:
            raise ValueError(
                f"dim={dim} not supported. Must be one of {SUPPORTED_DIMS}"
            )

        self._dim = dim
        self._model_path = model_path
        self._lazy_load = lazy_load
        self._mlx_manager = None
        self._is_loaded = False

        # FastEmbed-compatible API cache
        self._embedder = None  # Will hold MLX manager for embed() calls

        if not lazy_load:
            self._ensure_loaded()

    @property
    def is_loaded(self) -> bool:
        """Check if backend is loaded."""
        return self._is_loaded

    @property
    def embedding_dim(self) -> int:
        """Return embedding dimension."""
        return self._dim

    def _ensure_loaded(self) -> None:
        """Ensure MLX backend is loaded (thread-safe via threading.Lock)."""
        if self._is_loaded:
            return

        with _manager_lock:
            # Double-check after acquiring lock
            if self._is_loaded:
                return

            try:
                from _shims.core_mlx_embeddings import MLXEmbeddingManager

                self._mlx_manager = MLXEmbeddingManager(
                    model_path=self._model_path,
                    lazy_load=True,  # F265-4×-FIX: lazy load, lock in _load_model handles concurrency
                )
                # Explicitly load (non-lazy since we hold the lock)
                if not self._mlx_manager._is_loaded:
                    self._mlx_manager._load_model()
                self._is_loaded = True
                logger.info(
                    f"[UnifiedEmbedder] MLX backend loaded: dim={self._dim}, "
                    f"model={self._mlx_manager.model_path}"
                )
            except Exception as e:
                logger.warning(f"[UnifiedEmbedder] MLX load failed: {e}")
                self._mlx_manager = None
                self._is_loaded = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        """
        Embed multiple texts (FastEmbed-compatible API).

        Args:
            texts: List of text strings.

        Returns:
            List of embedding vectors (each dim=self._dim).
        """
        if not texts:
            return []

        self._ensure_loaded()

        if self._mlx_manager is None:
            # Fail-soft: return zero vectors
            return [[0.0] * self._dim for _ in texts]

        try:
            # Call encode() directly via ThreadPool — embed() calls embed() recursively
            # via encode() which would deadlock on _load_lock. Direct encode() call bypasses this.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(self._mlx_manager.encode, texts, self._dim, True)
                arr = future.result(timeout=30)
                # arr is (n, self._dim) float32 numpy array from MRL truncation path
                return [arr[i].tolist() for i in range(arr.shape[0])]
        except Exception as e:
            logger.warning(f"[UnifiedEmbedder] embed failed: {e}")
            return [[0.0] * self._dim for _ in texts]

    def embed_one(self, text: str) -> list[float]:
        """
        Embed single text.

        Args:
            text: Single text string.

        Returns:
            Embedding vector (dim=self._dim).
        """
        results = self.embed([text])
        return results[0] if results else [0.0] * self._dim

    async def embed_async(self, texts: list[str]) -> list[list[float]]:
        """
        Async embed (for async code paths).

        Args:
            texts: List of text strings.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        self._ensure_loaded()

        if self._mlx_manager is None:
            return [[0.0] * self._dim for _ in texts]

        try:
            # Use encode() directly — embed_document returns (1, hidden_dim) with wrong
            # 2D flattening. encode() gives (n, MRL_DIM) numpy array directly.
            def batch_embed() -> list[list[float]]:
                mgr = self._mlx_manager
                if mgr is None:
                    return [[0.0] * self._dim for _ in texts]
                arr = mgr.encode(
                    texts,
                    truncate_dim=self._dim,
                    normalize=True,
                )
                if arr.shape[0] != len(texts) or (len(arr.shape) > 1 and arr.shape[1] != self._dim):
                    logger.warning(f"[UnifiedEmbedder] encode shape mismatch: {arr.shape}")
                    return [[0.0] * self._dim for _ in texts]
                return [arr[i].tolist() for i in range(arr.shape[0])]

            embeddings = await asyncio.to_thread(batch_embed)
            return [list(e) for e in embeddings]
        except Exception as e:
            logger.warning(f"[UnifiedEmbedder] embed_async failed: {e}")
            return [[0.0] * self._dim for _ in texts]

    def encode(self, texts: str | list[str]) -> np.ndarray:
        """
        Encode texts (compatible with FastEmbed TextEmbedding API).

        Args:
            texts: Single text or list of texts.

        Returns:
            NumPy array of embeddings.
        """
        if isinstance(texts, str):
            texts = [texts]
            single = True
        else:
            single = False

        embeddings = self.embed(texts)
        result = np.array(embeddings, dtype=np.float32)

        if single:
            return result[0] if result else np.array([])
        return result


# =============================================================================
# FastEmbed compatibility shim
# =============================================================================
# For code that checks isinstance(x, TextEmbedding) or similar

class FastEmbedShim:
    """
    Compatibility shim that makes UnifiedEmbeddingManager look like FastEmbed.

    Some code may check for FastEmbed-specific attributes or behavior.
    This shim provides a minimal FastEmbed-like interface.
    """

    def __init__(self, manager: UnifiedEmbeddingManager):
        self._manager = manager

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        """FastEmbed-style embed returning numpy arrays."""
        return [np.array(e) for e in self._manager.embed(texts)]

    def __call__(self, texts: str | list[str]) -> list[np.ndarray]:
        """Callable interface."""
        if isinstance(texts, str):
            texts = [texts]
        return self.embed(texts)


# =============================================================================
# Module-level singleton accessor
# =============================================================================

def get_unified_embedder(dim: int = DEFAULT_DIM) -> UnifiedEmbeddingManager:
    """
    Get or create the global UnifiedEmbeddingManager singleton.

    Args:
        dim: MRL output dimension. Only used on first call.

    Returns:
        Global UnifiedEmbeddingManager instance.
    """
    global _unified_manager

    if _unified_manager is None:
        _unified_manager = UnifiedEmbeddingManager(dim=dim, lazy_load=True)

    return _unified_manager


def reset_unified_embedder() -> None:
    """Reset singleton (for testing)."""
    global _unified_manager
    _unified_manager = None
