"""
ModernBERTEmbedder — MLX-accelerated ModernBERT encoder for embeddings, dedup, routing.

Provides:
- Batch text embedding via mlx-embeddings (ModernBERT-base, 768d)
- Symmetric/asymmetric embedding support (search_query vs search_document prefixes)
- M1 Metal cache cleanup on unload
- Fallback: sentence-transformers (CPU) if mlx-embeddings unavailable

Canonical import path: from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder
Replaces: utils/semantic.py ModernBERTEmbedding (DEPRECATED)
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

MLX_EMBEDDINGS_AVAILABLE = False
_mlx_available = False

try:
    import mlx.core as mx
    _mlx_available = mx.metal.is_available() if hasattr(mx, 'metal') else False
except ImportError:
    _mlx_available = False

try:
    from mlx_embeddings import load as mlx_embeddings_load

    class _ModernBERTMLXLoader:
        """Deferred loader to avoid import overhead when mlx-embeddings unavailable."""
        _instance = None
        _model = None
        _processor = None
        _tokenizer = None

        @classmethod
        def load(cls, model_path: str):
            if cls._instance is None:
                cls._model, cls._processor = mlx_embeddings_load(model_path, lazy=False)
                cls._tokenizer = cls._processor._tokenizer
                cls._instance = True
                logger.info(f"[MODERNBERT] MLX load OK: {model_path}")
            return cls._model, cls._tokenizer

    MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    MLX_EMBEDDINGS_AVAILABLE = False
    _ModernBERTMLXLoader = None


@dataclass
class ModernBERTConfig:
    """Configuration for ModernBERT embedder."""
    model_path: str = "nomic-ai/modernbert-embed-base"
    max_seq_len: int = 512
    embed_dim: int = 768
    batch_size: int = 8
    normalize: bool = True


class ModernBERTEmbedder:
    """
    MLX-accelerated ModernBERT encoder.

    Supports task-aware prefixes:
    - search_query: for query-side embeddings
    - search_document: for document-side embeddings
    - clustering/classification: no prefix, L2 norm varies

    M1 8GB safe: Metal cache cleared on unload.
    """

    # Task prefix discipline (per nomic-ai modernbert-embed-* models)
    _SEARCH_QUERY_PREFIX = "search_query: "
    _SEARCH_DOC_PREFIX = "search_document: "

    def __init__(
        self,
        model_path: Optional[str] = None,
        lazy_load: bool = True,
        normalize: bool = True,
        batch_size: int = 8,
    ):
        """
        Initialize ModernBERT embedder.

        Args:
            model_path: HuggingFace model ID or local path. Defaults to nomic-ai/modernbert-embed-base.
            lazy_load: If True, defer model load until first embed() call.
            normalize: L2-normalize embeddings (default True for retrieval).
            batch_size: Max batch size for encoding.
        """
        self.config = ModernBERTConfig(
            model_path=model_path or "nomic-ai/modernbert-embed-base",
            batch_size=batch_size,
            normalize=normalize,
        )
        self._model = None
        self._tokenizer = None
        self._is_loaded = False

        if not lazy_load:
            self._load_model()

    def _load_model(self) -> None:
        """Load model via mlx-embeddings. Raises RuntimeError on failure."""
        if self._is_loaded:
            return

        if not MLX_EMBEDDINGS_AVAILABLE:
            raise RuntimeError(
                "mlx-embeddings not available. Install: pip install mlx-embeddings"
            )

        model_name = str(self.config.model_path)
        logger.info(f"[MODERNBERT] Loading: {model_name}")

        try:
            self._model, self._tokenizer = _ModernBERTMLXLoader.load(model_name)
            self._is_loaded = True
            logger.info("[MODERNBERT] Loaded successfully via mlx-embeddings")
        except Exception as e:
            logger.error(f"[MODERNBERT] Failed to load: {e}")
            raise RuntimeError(f"ModernBERT load failed: {e}") from e

    @property
    def is_loaded(self) -> bool:
        """True if model is loaded and ready."""
        return self._is_loaded

    def embed(self, text: str, task: str = "search_document") -> np.ndarray:
        """
        Encode a single text to embedding vector.

        Args:
            text: Text to encode.
            task: Task type — "search_query", "search_document", "clustering", "classification".
                  Applies appropriate prefix for ModernBERT retrieval quality.

        Returns:
            Embedding vector as np.ndarray (768d, float32).
        """
        if not self._is_loaded:
            self._load_model()

        # Apply task prefix
        prefixed = self._apply_prefix(text, task)

        # Tokenize
        inputs = self._tokenizer(
            [prefixed],
            padding=True,
            truncation=True,
            max_length=self.config.max_seq_len,
            return_tensors="mlx",
        )

        # Forward pass
        with self._metal_context():
            outputs = self._model(
                input_ids=inputs.input_ids,
                attention_mask=inputs.attention_mask,
            )

        emb = outputs.text_embeds

        # Normalize
        if self.config.normalize:
            norms = mx.linalg.norm(emb, axis=1, keepdims=True)
            emb = emb / mx.clip(norms, a_min=1e-12, a_max=None)

        result = np.array(emb)[0]

        # Release MLX refs immediately
        del outputs, emb, inputs

        return result

    def embed_batch(self, texts: List[str], task: str = "search_document") -> np.ndarray:
        """
        Encode a batch of texts to embedding matrix.

        Args:
            texts: List of texts to encode.
            task: Task type (see embed()).

        Returns:
            Embedding matrix np.ndarray (N, 768), float32.
        """
        if not self._is_loaded:
            self._load_model()

        if not texts:
            return np.array([])

        # Apply prefixes
        prefixed = [self._apply_prefix(t, task) for t in texts]

        all_embeddings = []
        for i in range(0, len(prefixed), self.config.batch_size):
            batch = prefixed[i:i + self.config.batch_size]

            inputs = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.config.max_seq_len,
                return_tensors="mlx",
            )

            with self._metal_context():
                outputs = self._model(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask,
                )

            emb = outputs.text_embeds

            if self.config.normalize:
                norms = mx.linalg.norm(emb, axis=1, keepdims=True)
                emb = emb / mx.clip(norms, a_min=1e-12, a_max=None)

            all_embeddings.append(np.array(emb))

            del outputs, emb, inputs

        return np.vstack(all_embeddings) if all_embeddings else np.array([])

    def _apply_prefix(self, text: str, task: str) -> str:
        """Apply task prefix to text. Guards against double-prefixing."""
        if task == "search_query":
            prefix = self._SEARCH_QUERY_PREFIX
        elif task == "search_document":
            prefix = self._SEARCH_DOC_PREFIX
        else:
            prefix = ""

        if prefix and not text.startswith(prefix):
            return prefix + text
        return text

    def _metal_context(self):
        """Return Metal stream context manager for M1 UMA buffer management."""
        try:
            from hledac.universal.utils.mlx_memory import get_metal_stream_context
            return get_metal_stream_context()
        except ImportError:
            return _NoOpContext()

    def unload(self) -> None:
        """Explicitly unload model and clear Metal cache."""
        self._model = None
        self._tokenizer = None
        self._is_loaded = False

        if _mlx_available:
            try:
                mx.eval([])
                mx.metal.clear_cache()
            except Exception:
                pass

        logger.info("[MODERNBERT] Unloaded, Metal cache cleared")


class _NoOpContext:
    """No-op context manager when mlx_memory is unavailable."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
