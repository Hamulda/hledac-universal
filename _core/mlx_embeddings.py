"""
Deprecated: use ``core.embeddings.legacy`` directly.

This module is a backward-compat re-export shim.
Moved to core/embeddings/legacy.py (F350M-R A-07).

Single canonical entry point:
    from hledac.universal._core.embeddings.legacy import MLXEmbeddingManager

This module will be removed in a future sprint.
"""
import warnings

warnings.warn(
    "core.mlx_embeddings is deprecated. Use core.embeddings.legacy instead. "
    "This shim will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
    )

# Re-export everything from the canonical implementation.
from hledac.universal._core.embeddings.legacy import (
    # Classes
    MLXEmbeddingManager,
    EmbeddingTask,
    EmbeddingDimensionError,
    # Functions
    get_mlx_embedder,
    get_embedding_manager,
    get_embedding_info,
    encode_texts,
    compute_similarity,
    prewarm_embedding_model,
    is_embedding_model_prewarmed,
    assert_embedding_dimension,
    apply_task_prefix,
    should_normalize,
    # Module-level lazy vars (via __getattr__)
    MLX_EMBEDDINGS_LOAD,
    MLX_EMBEDDINGS_AVAILABLE,
    MLX_AVAILABLE,
    )

__all__ = [
    "MLXEmbeddingManager",
    "EmbeddingTask",
    "EmbeddingDimensionError",
    "get_mlx_embedder",
    "get_embedding_manager",
    "get_embedding_info",
    "encode_texts",
    "compute_similarity",
    "prewarm_embedding_model",
    "is_embedding_model_prewarmed",
    "assert_embedding_dimension",
    "apply_task_prefix",
    "should_normalize",
    "MLX_EMBEDDINGS_LOAD",
    "MLX_EMBEDDINGS_AVAILABLE",
    "MLX_AVAILABLE",
]
