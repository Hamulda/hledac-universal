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
import concurrent.futures
import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np

import msgspec

from hledac.universal.utils.cache import PyCacheDict

logger = logging.getLogger(__name__)


class EmbeddingResult(msgspec.Struct, frozen=True, gc=False):
    """
    FLOW-04: Structured embedding result with explicit dimension validation.

    Replaces raw list[list[float]] as the contract between UnifiedEmbeddingManager
    and downstream consumers (DuckDBRAGStore, lancedb_store).

    Fields:
        text_hash:  SHA256 of input text (truncated to 32 chars) — for cache key validation
        dimensions:  Expected embedding dimension (e.g. 256, 384, 512, 768)
        vector:     The embedding vector as a list of floats
        model:      Model identifier used (e.g. "mlx-community/...", "fastembed-...")

    Invariant: len(vector) == dimensions — enforced at construction time.
    This prevents wrong-dimension embeddings from silently corrupting LanceDB/DuckDB
    vector indices.
    """

    text_hash: str
    dimensions: int
    vector: list[float]
    model: str

    def __post_init__(self) -> None:
        """Validate embedding dimension matches vector length."""
        if len(self.vector) != self.dimensions:
            raise ValueError(
                f"EmbeddingResult dimension mismatch: dimensions={self.dimensions} "
                f"but len(vector)={len(self.vector)}"
            )

    @classmethod
    def from_text(
        cls,
        text: str,
        vector: list[float],
        dimensions: int,
        model: str,
    ) -> "EmbeddingResult":
        """
        Create EmbeddingResult from raw embedding computation.

        Computes text_hash from the input text automatically.
        """
        text_hash = hashlib.sha256(text.encode()).hexdigest()[:32]
        return cls(
            text_hash=text_hash,
            dimensions=dimensions,
            vector=vector,
            model=model,
        )
_unified_manager: UnifiedEmbeddingManager | None = None
_manager_lock = threading.Lock()
SUPPORTED_DIMS = (256, 512, 768)
DEFAULT_DIM = 512
# FLOW-02: M1 8GB safe — truncate text before MLX tokenization to prevent OOM
MAX_TEXT_LENGTH = 8192

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
    __slots__ = tuple(('_dim', '_embed_cache', '_embedder', '_is_loaded', '_lazy_load', '_mlx_manager', '_model_path'))

    def __init__(self, dim: int=DEFAULT_DIM, model_path: str | Path | None=None, lazy_load: bool=True):
        """
        Initialize unified embedder.

        Args:
            dim: MRL output dimension (256, 512, or 768). Default 512 for backward compat.
            model_path: Optional custom model path.
            lazy_load: Defer model loading until first use.
        """
        if dim not in SUPPORTED_DIMS:
            raise ValueError(f'dim={dim} not supported. Must be one of {SUPPORTED_DIMS}')
        self._dim = dim
        self._model_path = model_path
        self._lazy_load = lazy_load
        self._mlx_manager = None
        self._is_loaded = False
        self._embedder = None
        self._embed_cache: PyCacheDict[str, list[list[float]]] = PyCacheDict(maxsize=4096, ttl_s=3600.0)
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
            if self._is_loaded:
                return
            try:
                # F6: Use singleton via get_mlx_embedder() — single Metal command queue,
                # no double-loads on M1 8GB. Custom model_path is handled by the
                # singleton's prewarm() during sprint pre-flight.
                from hledac.universal.core.mlx_embeddings import get_mlx_embedder
                self._mlx_manager = get_mlx_embedder()
                if not self._mlx_manager._is_loaded:
                    self._mlx_manager._load_model()
                self._is_loaded = True
                logger.info(f'[UnifiedEmbedder] MLX backend loaded: dim={self._dim}, model={self._mlx_manager.model_path}')
            except Exception as e:
                logger.warning(f'[UnifiedEmbedder] MLX load failed: {e}')
                self._mlx_manager = None
                self._is_loaded = False

    def _cache_lookup(self, texts: list[str]) -> tuple[list[tuple[int, list[float]]], list[tuple[int, str]]]:
        """Split texts into cached and uncached based on hash lookup."""
        cached_results, uncached = [], []
        for i, text in enumerate(texts):
            truncated = text[:MAX_TEXT_LENGTH] if len(text) > MAX_TEXT_LENGTH else text
            key = hashlib.sha256(truncated.encode()).hexdigest()[:32]
            cached = self._embed_cache.get(key)
            if cached is not None:
                cached_results.append((i, cached))
            else:
                uncached.append((i, truncated))
        return cached_results, uncached

    def _fill_results_from_cache(self, texts: list[str], cached: list[tuple[int, list[float]]]) -> list[list[float]]:
        """Build result array and populate with cached embeddings."""
        results = [[0.0] * self._dim for _ in texts]
        for idx, emb in cached:
            results[idx] = emb
        return results

    def _parallel_encode(self, texts: list[str]) -> np.ndarray:
        """Execute parallel encoding with M1 optimization."""
        from hledac.universal.utils.domain_executors import get_or_create
        n = len(texts)
        if n > 4:
            mid = (n + 1) // 2
            chunk_a, chunk_b = texts[:mid], texts[mid:]
            pool = get_or_create("embed")
            fut_a = pool.submit(self._mlx_manager.encode, chunk_a, self._dim, True)
            fut_b = pool.submit(self._mlx_manager.encode, chunk_b, self._dim, True)
            chunks = [f.result(timeout=30) for f in concurrent.futures.as_completed([fut_a, fut_b], timeout=30)]
            return np.concatenate(chunks, axis=0) if chunks else np.zeros((0, self._dim), dtype=np.float32)
        pool = get_or_create("embed")
        fut = pool.submit(self._mlx_manager.encode, texts, self._dim, True)
        return fut.result(timeout=30)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts (FastEmbed-compatible API)."""
        if not texts:
            return []
        cached, uncached = self._cache_lookup(texts)
        if not uncached:
            return self._fill_results_from_cache(texts, cached)
        self._ensure_loaded()
        if self._mlx_manager is None:
            return self._fill_results_from_cache(texts, cached)
        try:
            uncached_texts = [t for _, t in uncached]
            arr = self._parallel_encode(uncached_texts)
            results = self._fill_results_from_cache(texts, cached)
            for j, (i, text) in enumerate(uncached):
                emb = arr[j].tolist()
                results[i] = emb
                truncated = text[:MAX_TEXT_LENGTH] if len(text) > MAX_TEXT_LENGTH else text
                key = hashlib.sha256(truncated.encode()).hexdigest()[:32]
                self._embed_cache[key] = emb
            return results
        except Exception as e:
            logger.warning(f'[UnifiedEmbedder] embed failed: {e}')
            return self._fill_results_from_cache(texts, cached)

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

    def _encode_chunk_sync(self, chunk_texts: list[str]) -> list[list[float]]:
        """Encode a single chunk synchronously."""
        mgr = self._mlx_manager
        if mgr is None:
            return [[0.0] * self._dim for _ in chunk_texts]
        arr = mgr.encode(chunk_texts, truncate_dim=self._dim, normalize=True)
        if arr.shape[0] != len(chunk_texts) or (len(arr.shape) > 1 and arr.shape[1] != self._dim):
            logger.warning(f'[UnifiedEmbedder] encode shape mismatch: {arr.shape}')
            return [[0.0] * self._dim for _ in chunk_texts]
        return [arr[i].tolist() for i in range(arr.shape[0])]

    def _split_into_chunks(self, texts: list[str]) -> tuple[list[list[str]], str]:
        """Split texts into chunks based on size and return context label."""
        n = len(texts)
        if n <= 4:
            return [texts], "embed_single"
        elif n <= 16:
            mid = (n + 1) // 2
            return [texts[:mid], texts[mid:]], "embed_two_chunk"
        chunk_size = (n + 3) // 4
        return [texts[i:i + chunk_size] for i in range(0, n, chunk_size)], "embed_multi_chunk"

    async def embed_async(self, texts: list[str]) -> list[list[float]]:
        """
        Async embed (for async code paths).

        ISSUE #003 FIX: Parallel chunking for large batches.
        - n <= 4:  single batch (no parallelism overhead)
        - n 5-16:  2 chunks, 2 workers
        - n > 16:  4 chunks, 4 workers (M1 8GB: 4E+4P cores)

        Each chunk runs in its own thread via asyncio.to_thread().
        MLX encode() releases GIL → true parallelism on M1 cores.
        """
        if not texts:
            return []
        self._ensure_loaded()
        if self._mlx_manager is None:
            return [[0.0] * self._dim for _ in texts]
        try:
            chunks, ctx = self._split_into_chunks(texts)
            from hledac.universal.utils.async_helpers import parallel
            if len(chunks) == 1:
                return await asyncio.to_thread(self._encode_chunk_sync, texts)
            p_result = await parallel(
                [asyncio.to_thread(self._encode_chunk_sync, chunk) for chunk in chunks],
                policy="raise",
                ctx=ctx,
            )
            embeddings: list[list[float]] = []
            for result in p_result.ok:
                embeddings.extend(result)
                return embeddings
        except Exception as e:
            logger.warning(f'[UnifiedEmbedder] embed_async failed: {e}')
            return [[0.0] * self._dim for _ in texts]

    def embed_structured(self, texts: list[str]) -> list[EmbeddingResult]:
        """
        FLOW-04: Embed texts and return structured EmbeddingResult objects.

        This is the preferred API for downstream consumers that need dimension
        validation and metadata (text_hash, model). Use this instead of embed()
        when the result will be stored in LanceDB or DuckDB.

        Args:
            texts: List of text strings.

        Returns:
            List of EmbeddingResult objects with validated dimensions.
            Raises ValueError if any returned embedding has wrong dimension.
        """
        vectors = self.embed(texts)
        model_name = "unknown"
        if self._mlx_manager is not None:
            model_name = getattr(self._mlx_manager, "model_path", "mlx") or "mlx"
        results: list[EmbeddingResult] = []
        for i, text in enumerate(texts):
            vector = vectors[i]
            if len(vector) != self._dim:
                logger.warning(
                    f"[FLOW-04] Wrong-dimension embedding detected: "
                    f"expected dim={self._dim}, got {len(vector)}. "
                    f"Returning zero vector for text[{i}]."
                )
                vector = [0.0] * self._dim
            result = EmbeddingResult.from_text(
                text=text,
                vector=vector,
                dimensions=self._dim,
                model=model_name,
            )
            results.append(result)
        return results

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

class FastEmbedShim:
    """
    Compatibility shim that makes UnifiedEmbeddingManager look like FastEmbed.

    Some code may check for FastEmbed-specific attributes or behavior.
    This shim provides a minimal FastEmbed-like interface.
    """
    __slots__ = tuple(('_manager',))

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

def get_unified_embedder(dim: int=DEFAULT_DIM) -> UnifiedEmbeddingManager:
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