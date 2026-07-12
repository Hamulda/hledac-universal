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
import hashlib
import logging
import threading
from pathlib import Path
import numpy as np
from hledac.universal.utils.cache import PyCacheDict
logger = logging.getLogger(__name__)
_unified_manager: UnifiedEmbeddingManager | None = None
_manager_lock = threading.Lock()
SUPPORTED_DIMS = (256, 512, 768)
DEFAULT_DIM = 512

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
                from compat.core_mlx_embeddings import MLXEmbeddingManager
                self._mlx_manager = MLXEmbeddingManager(model_path=self._model_path, lazy_load=True)
                if not self._mlx_manager._is_loaded:
                    self._mlx_manager._load_model()
                self._is_loaded = True
                logger.info(f'[UnifiedEmbedder] MLX backend loaded: dim={self._dim}, model={self._mlx_manager.model_path}')
            except Exception as e:
                logger.warning(f'[UnifiedEmbedder] MLX load failed: {e}')
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
        cache = self._embed_cache
        cached_results: list[tuple[int, list[float]]] = []
        uncached: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            key = hashlib.sha256(text.encode()).hexdigest()[:32]
            cached = cache.get(key)
            if cached is not None:
                cached_results.append((i, cached))
            else:
                uncached.append((i, text))
        if not uncached:
            results = [[0.0] * self._dim for _ in texts]
            for idx, emb in cached_results:
                results[idx] = emb
            return results
        self._ensure_loaded()
        if self._mlx_manager is None:
            results = [[0.0] * self._dim for _ in texts]
            for idx, emb in cached_results:
                results[idx] = emb
            return results
        try:
            import concurrent.futures
            uncached_texts = [t for _, t in uncached]
            n = len(uncached_texts)
            if n > 4:
                mid = (n + 1) // 2
                chunk_a = uncached_texts[:mid]
                chunk_b = uncached_texts[mid:]
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    fut_a = pool.submit(self._mlx_manager.encode, chunk_a, self._dim, True)
                    fut_b = pool.submit(self._mlx_manager.encode, chunk_b, self._dim, True)
                    chunks: list[np.ndarray] = []
                    for fut in concurrent.futures.as_completed([fut_a, fut_b], timeout=30):
                        chunks.append(fut.result(timeout=0))
                    arr = np.concatenate(chunks, axis=0) if chunks else np.zeros((0, self._dim), dtype=np.float32)
            else:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(self._mlx_manager.encode, uncached_texts, self._dim, True)
                    arr = fut.result(timeout=30)
            results = [[0.0] * self._dim for _ in texts]
            for idx, emb in cached_results:
                results[idx] = emb
            for j, (i, text) in enumerate(uncached):
                emb = arr[j].tolist()
                results[i] = emb
                key = hashlib.sha256(text.encode()).hexdigest()[:32]
                cache[key] = emb
            return results
        except Exception as e:
            logger.warning(f'[UnifiedEmbedder] embed failed: {e}')
            results = [[0.0] * self._dim for _ in texts]
            for idx, emb in cached_results:
                results[idx] = emb
            return results

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
            n = len(texts)

            def encode_chunk(chunk_texts: list[str]) -> list[list[float]]:
                """Encode a single chunk — runs in thread pool."""
                mgr = self._mlx_manager
                if mgr is None:
                    return [[0.0] * self._dim for _ in chunk_texts]
                arr = mgr.encode(chunk_texts, truncate_dim=self._dim, normalize=True)
                if arr.shape[0] != len(chunk_texts) or (len(arr.shape) > 1 and arr.shape[1] != self._dim):
                    logger.warning(f'[UnifiedEmbedder] encode shape mismatch: {arr.shape}')
                    return [[0.0] * self._dim for _ in chunk_texts]
                return [arr[i].tolist() for i in range(arr.shape[0])]
            if n <= 4:
                embeddings = await asyncio.to_thread(encode_chunk, texts)
                return [list(e) for e in embeddings]
            elif n <= 16:
                mid = (n + 1) // 2
                chunk_a = texts[:mid]
                chunk_b = texts[mid:]
                results_a, results_b = await asyncio.gather(asyncio.to_thread(encode_chunk, chunk_a), asyncio.to_thread(encode_chunk, chunk_b))
                return list(results_a) + list(results_b)
            else:
                chunk_size = (n + 3) // 4
                chunks = [texts[i:i + chunk_size] for i in range(0, n, chunk_size)]
                chunk_results = await asyncio.gather(*[asyncio.to_thread(encode_chunk, chunk) for chunk in chunks])
                embeddings: list[list[float]] = []
                for result in chunk_results:
                    embeddings.extend(result)
                return embeddings
        except Exception as e:
            logger.warning(f'[UnifiedEmbedder] embed_async failed: {e}')
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