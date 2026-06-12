"""
MLX embedding backend — Apple Silicon native, unified memory, py3.14 compatible.
Priority: MLX (ANE/GPU unified) → CoreML HTTP → ONNX CPU → hash fallback.
No py3.12 subprocess, no CoreML conversion required.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

logger = logging.getLogger(__name__)

# ── MLX availability (lazy — no top-level import) ─────────────────────────────
_MLX_AVAILABLE = False
try:
    import mlx.core as mx

    _MLX_AVAILABLE = True
except ImportError:
    mx = None  # type: ignore[assignment]

if TYPE_CHECKING:
    pass

_MLX_EMBEDDINGS_AVAILABLE = False
try:
    from mlx_embedding_models.embedding import EmbeddingModel

    _MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    EmbeddingModel = None  # type: ignore[assignment]

# ── Constants ───────────────────────────────────────────────────────────────────
_MODEL_ID = "BAAI/bge-small-en-v1.5"
_EMBED_DIM = 384
_BATCH_SIZE = 64  # MLX unified memory — higher batch than CoreML


class MLXEmbedder:
    """
    MLX-native embedder — runs directly in py3.14 on Apple Silicon.
    No subprocess, no HTTP bridge, no conversion.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._is_loaded = False

    @property
    def is_available(self) -> bool:
        return _MLX_EMBEDDINGS_AVAILABLE

    async def load(self) -> bool:
        if self._is_loaded:
            return True
        if not _MLX_EMBEDDINGS_AVAILABLE:
            logger.warning("[MLX] mlx-embedding-models not available")
            return False
        try:
            loop = asyncio.get_running_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: EmbeddingModel.from_registry(_MODEL_ID),
            )
            self._is_loaded = True
            logger.info("[MLX] Embedder loaded — unified memory, no conversion needed")
            return True
        except Exception as e:
            logger.warning("[MLX] Load failed: %s", e)
            return False

    async def encode_batch(
        self,
        texts: str | list[str],
        batch_size: int = _BATCH_SIZE,
    ) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        if not texts or not self._is_loaded:
            return np.zeros((len(texts), _EMBED_DIM), dtype=np.float32)

        loop = asyncio.get_running_loop()
        all_embs: list[np.ndarray] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embs = await loop.run_in_executor(
                None,
                lambda b=batch: np.array(self._model.encode(b)),
            )
            all_embs.append(embs)

        result = np.vstack(all_embs).astype(np.float32)
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        return result / (norms + 1e-8)

    def unload(self) -> None:
        self._model = None
        self._is_loaded = False
