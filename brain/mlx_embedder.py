"""
MLX embedding backend — Apple Silicon native, unified memory, py3.14 compatible.
Priority: MLX (ANE/GPU unified) → CoreML HTTP → ONNX CPU → hash fallback.
No py3.12 subprocess, no CoreML conversion required.


AdaptiveEmbeddingBatcher is re-exported from core.embeddings.manager
(consolidated — Issue #35 fix, July 2026).
"""
import asyncio
import logging
import time as time_module
from typing import TYPE_CHECKING
from collections.abc import Awaitable, Callable
import numpy as np
from hledac.universal._core.embeddings.manager import AdaptiveEmbeddingBatcher
logger = logging.getLogger(__name__)

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE
from _core import aclose

# Lazy accessor for mlx.core - only used for Metal memory queries
def _get_mlx():
    """Lazy accessor for mlx.core — uses centralized get_mx() from SSOT."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core
    return _get_mx_from_core()
if TYPE_CHECKING:
    pass
_MLX_EMBEDDINGS_AVAILABLE = False
try:
    from mlx_embedding_models.embedding import EmbeddingModel
    _MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    EmbeddingModel = None
_MODEL_ID = 'BAAI/bge-small-en-v1.5'
_EMBED_DIM = 384
_BATCH_SIZE = 64
_BATCH_SIZE_HIGH = 128
_BATCH_SIZE_LOW = 32

class MLXEmbedder:
    """
    MLX-native embedder — runs directly in py3.14 on Apple Silicon.
    No subprocess, no HTTP bridge, no conversion.

    Adaptive batch sizing (Sprint F265D):
    - NORMAL memory: batch_size=128
    - WARNING memory: batch_size=64
    - CRITICAL memory: batch_size=32
    """
    __slots__ = tuple(('_is_loaded', '_model'))

    def __init__(self) -> None:
        self._model: 'EmbeddingModel | None' = None
        self._is_loaded = False

    @property
    def is_available(self) -> bool:
        return _MLX_EMBEDDINGS_AVAILABLE

    async def load(self) -> bool:
        if self._is_loaded:
            return True
        if not _MLX_EMBEDDINGS_AVAILABLE:
            logger.warning('[MLX] mlx-embedding-models not available')
            return False
        try:
            loop = asyncio.get_running_loop()
            self._model = await loop.run_in_executor(None, lambda: EmbeddingModel.from_registry(_MODEL_ID))
            self._is_loaded = True
            logger.info('[MLX] Embedder loaded — unified memory, no conversion needed')
            return True
        except Exception as e:
            logger.warning('[MLX] Load failed: %s', e)
            return False

    def _get_mlx_memory(self):
        """Lazy-load mlx_memory module for adaptive batching (Sprint F265D)."""
        from hledac.universal.utils.mlx_memory import get_mlx_memory_module
        return get_mlx_memory_module()

    def _get_adaptive_batch_size(self) -> int:
        """
        Sprint F265D: Return adaptive batch size based on Metal memory pressure.

        Memory pressure tiers:
        - NORMAL (<80% of budget): _BATCH_SIZE_HIGH (128)
        - WARNING (80-90%): _BATCH_SIZE (64)
        - CRITICAL (>90%): _BATCH_SIZE_LOW (32)

        Returns:
            Adaptive batch size in range [_BATCH_SIZE_LOW, _BATCH_SIZE_HIGH].
        """
        mlx_mem = self._get_mlx_memory()
        if mlx_mem is None:
            return _BATCH_SIZE
        try:
            _, pressure_level = mlx_mem.get_mlx_memory_pressure()
        except Exception:
            return _BATCH_SIZE
        if pressure_level == 'NORMAL':
            return _BATCH_SIZE_HIGH
        elif pressure_level == 'WARNING':
            return _BATCH_SIZE
        else:
            return _BATCH_SIZE_LOW

    async def encode_batch(self, texts: str | list[str], batch_size: int | None=None) -> np.ndarray:
        """
        Encode batch with adaptive sizing based on Metal memory (Sprint F265D).

        Args:
            texts: Text(s) to encode.
            batch_size: Override batch size. If None, uses adaptive sizing.
        """
        if isinstance(texts, str):
            texts = [texts]
        if not texts or not self._is_loaded:
            return np.zeros((len(texts), _EMBED_DIM), dtype=np.float32)
        effective_batch_size = batch_size if batch_size is not None else self._get_adaptive_batch_size()
        loop = asyncio.get_running_loop()
        all_embs: list[np.ndarray] = []
        for i in range(0, len(texts), effective_batch_size):
            batch = texts[i:i + effective_batch_size]
            embs = await loop.run_in_executor(None, lambda b=batch: np.array(self._model.encode(b)))
            all_embs.append(embs)
        result = np.vstack(all_embs).astype(np.float32)
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        return result / (norms + 1e-08)

    def unload(self) -> None:
        self._model = None
        self._is_loaded = False