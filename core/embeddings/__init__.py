"""
core/embeddings — Unified MLX embedding package.

Modules:
- manager: MLXEmbeddingManager (lazy load, encode, similarity)
- cache: EmbeddingCache (np.memmap float16 two-layer LRU)
- pool: MLX worker thread + Metal limits

Architectural invariants:
- Single MLX worker thread (no per-call thread spawn)
- mx.eval([]) before mx.metal.clear_cache() — always
- asyncio.Lock for cache, not threading.Lock (async context)
- Lazy import mlx.core — never at module level
"""
from __future__ import annotations

from core.embeddings.manager import (
    EmbeddingTask,
    EmbeddingDimensionError,
    MLXEmbeddingManager,
    get_mlx_embedder,
    get_embedding_manager,
    get_embedding_info,
    encode_texts,
    compute_similarity,
    assert_embedding_dimension,
    prewarm_embedding_model,
    is_embedding_model_prewarmed,
    apply_task_prefix,
    should_normalize,
)

from core.embeddings.cache import EmbeddingCache, get_embedding_cache, get_embedding_cache_stats

from core.embeddings.pool import (
    init_mlx_buffers,
    mlx_cleanup_sync,
    mlx_cleanup_aggressive,
    get_metal_limits_status,
    reconfigure_metal_cache_limit,
    MLX_AVAILABLE,
)

__all__ = [
    # manager
    "EmbeddingTask",
    "EmbeddingDimensionError",
    "MLXEmbeddingManager",
    "get_mlx_embedder",
    "get_embedding_manager",
    "get_embedding_info",
    "encode_texts",
    "compute_similarity",
    "assert_embedding_dimension",
    "prewarm_embedding_model",
    "is_embedding_model_prewarmed",
    "apply_task_prefix",
    "should_normalize",
    # cache
    "EmbeddingCache",
    "get_embedding_cache",
    "get_embedding_cache_stats",
    # pool
    "init_mlx_buffers",
    "mlx_cleanup_sync",
    "mlx_cleanup_aggressive",
    "get_metal_limits_status",
    "reconfigure_metal_cache_limit",
    "MLX_AVAILABLE",
]
