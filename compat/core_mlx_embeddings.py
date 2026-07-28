"""
Deprecated: use ``core.mlx_embeddings`` directly.

Moved to core/mlx_embeddings.py (F350M-R A-01).
This stub exists only for backward compatibility during migration.
"""
import warnings

__all__ = [
    "MLXEmbeddingManager",
    "get_mlx_embedder",
    "get_embedding_manager",
    "EmbeddingTask",
    "EmbeddingDimensionError",
    "assert_embedding_dimension",
    "should_normalize",
    "apply_task_prefix",
    "prewarm_embedding_model",
    "is_embedding_model_prewarmed",
]

warnings.warn(
    "compat.core_mlx_embeddings is deprecated. Use core.mlx_embeddings directly. "
    "This shim will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.core.mlx_embeddings import (
    MLXEmbeddingManager,
    get_mlx_embedder,
    get_embedding_manager,
    EmbeddingTask,
    EmbeddingDimensionError,
    assert_embedding_dimension,
    should_normalize,
    apply_task_prefix,
    prewarm_embedding_model,
    is_embedding_model_prewarmed,
)
