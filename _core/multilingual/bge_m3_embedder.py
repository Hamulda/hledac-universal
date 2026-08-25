"""
BGE-M3 Multilingual Embedding Model for ANE/MLX.

BGE-M3 (BAAI/bge-m3) provides:



- 1024-dimensional dense embeddings
- 100+ language support (including Russian, Chinese, Arabic, etc.)
- Matryoshka Representation Learning (MRL) for dimension truncation
- Cross-lingual semantic alignment

On MacBook Air M1 8GB:
- Model size: ~2.2GB fp16 → ~570MB Int8 quantized
- Runs via MLX for unified memory efficiency
- Compatible with existing 256d USEARCH index via MRL truncation

Author: Hledac Team
Issue: [SWARM]-002
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)
MODEL_ID = "BAAI/bge-m3"
NATIVE_DIM = 1024
MRL_TARGET_DIM = 256
MAX_BATCH_SIZE = 32
MAX_BATCH_SIZE_LOW = 8
MAX_SEQ_LEN = 512
MODEL_CACHE_DIR = Path.home() / ".cache" / "hledac" / "models"


class BGEBackend(Enum):
    """Available backends for BGE-M3 inference."""

    MLX = auto()
    ONNX_CPU = auto()
    TRANSFORMERS = auto()


@dataclass(frozen=True, slots=True)
class BGEConfig:
    """Configuration for BGE-M3 embedder."""

    model_id: str = MODEL_ID
    native_dim: int = NATIVE_DIM
    mrl_target_dim: int = MRL_TARGET_DIM
    max_seq_len: int = MAX_SEQ_LEN
    batch_size: int = MAX_BATCH_SIZE
    batch_size_low: int = MAX_BATCH_SIZE_LOW
    normalize: bool = True
    pooling_strategy: str = "mean"


@dataclass(slots=True)
class BGEInferenceResult:
    """Result from BGE-M3 inference."""

    embeddings: np.ndarray
    model_dim: int
    truncated_dim: int | None
    language: str | None
    inference_ms: float


class BGEM3Embedder:
    """
    BGE-M3 Multilingual Embedding Model.

    Supports:
    - MLX backend (Apple Silicon GPU, unified memory)
    - ONNX Runtime CPU fallback
    - HuggingFace transformers CPU fallback
    - MRL truncation to 256d for index compatibility

    Usage:
        embedder = BGEM3Embedder()
        await embedder.load()

        # Single text
        emb = await embedder.embed("вредоносное ПО для кражи данных")

        # Batch (multilingual)
        texts = [
            "APT29 threat actor analysis",
            "APT29 анализ угроз",
            "APT29 威胁行为者分析"
        ]
        embeddings = await embedder.embed_batch(texts)

        # With MRL truncation to 256d
        embeddings_256d = await embedder.embed_batch(texts, truncate_to=256)
    """

    __slots__ = (
        "_config",
        "_model",
        "_processor",
        "_tokenizer",
        "_backend",
        "_is_loaded",
        "_mrl_truncator",
        "_hf_model",
    )

    def __init__(
        self,
        model_id: str = MODEL_ID,
        mrl_target_dim: int = MRL_TARGET_DIM,
        backend: BGEBackend | None = None,
        lazy_load: bool = True,
        batch_size: int = MAX_BATCH_SIZE,
        batch_size_low: int = MAX_BATCH_SIZE_LOW,
    ) -> None:
        """
        Initialize BGE-M3 embedder.

        Args:
            model_id: HuggingFace model ID for BGE-M3.
            mrl_target_dim: Target dimension for MRL truncation (256 for USEARCH).
            backend: Force specific backend (MLX, ONNX, transformers).
                     If None, auto-detect best available.
            lazy_load: Defer model loading until first embed call.
            batch_size: Default batch size for inference.
            batch_size_low: Batch size when memory is constrained.
        """
        self._config = BGEConfig(
            model_id=model_id, mrl_target_dim=mrl_target_dim, batch_size=batch_size, batch_size_low=batch_size_low
        )
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._backend = backend or self._detect_backend()
        self._is_loaded = False
        self._mrl_truncator = None
        if not lazy_load:
            self.load()

    def _detect_backend(self) -> BGEBackend:
        """Auto-detect best available backend."""
        if self._check_mlx_available():
            logger.info("[BGE-M3] Using MLX backend (Apple Silicon GPU)")
            return BGEBackend.MLX
        if self._check_onnx_available():
            logger.info("[BGE-M3] Using ONNX Runtime backend (CPU)")
            return BGEBackend.ONNX_CPU
        logger.info("[BGE-M3] Using transformers backend (CPU)")
        return BGEBackend.TRANSFORMERS

    def _check_mlx_available(self) -> bool:
        """Check if MLX backend is available."""
        try:
            import mlx.core as mx

            if hasattr(mx, "metal"):
                return mx.metal.is_available()
        except ImportError:
            pass
        return False

    def _check_onnx_available(self) -> bool:
        """Check if ONNX Runtime is available."""
        try:
            return True
        except ImportError:
            return False

    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded."""
        return self._is_loaded

    @property
    def backend(self) -> BGEBackend:
        """Current inference backend."""
        return self._backend

    @property
    def native_dim(self) -> int:
        """Native BGE-M3 embedding dimension (1024)."""
        return NATIVE_DIM

    @property
    def mrl_target_dim(self) -> int:
        """Target MRL dimension for USEARCH compatibility (256)."""
        return self._config.mrl_target_dim

    def load(self) -> bool:
        """
        Load BGE-M3 model with detected or specified backend.

        Returns:
            True if load successful.
        """
        if self._is_loaded:
            return True
        if self._backend == BGEBackend.MLX:
            return self._load_mlx()
        elif self._backend == BGEBackend.ONNX_CPU:
            return self._load_onnx()
        else:
            return self._load_transformers()

    def _load_mlx(self) -> bool:
        """Load BGE-M3 via MLX."""
        try:
            import mlx.core as mx
            from mlx_embedding_models.embedding import EmbeddingModel

            logger.info(f"[BGE-M3] Loading via MLX: {self._config.model_id}")
            try:
                self._model = EmbeddingModel.from_registry(self._config.model_id)
            except ValueError:
                logger.info("[BGE-M3] Model not in registry, using custom loader")
                self._model = self._create_mlx_model()
            from .mrl import MRLTruncator

            self._mrl_truncator = MRLTruncator(
                source_dim=NATIVE_DIM, target_dim=self._config.mrl_target_dim, normalize=self._config.normalize
            )
            self._is_loaded = True
            logger.info("[BGE-M3] MLX load successful")
            return True
        except ImportError as e:
            logger.warning(f"[BGE-M3] MLX dependencies not available: {e}")
            self._backend = BGEBackend.ONNX_CPU
            return self._load_onnx()
        except Exception as e:
            logger.error(f"[BGE-M3] MLX load failed: {e}")
            return False

    def _create_mlx_model(self) -> None:
        """
        Create MLX model for BGE-M3 from transformers.

        This is a placeholder for custom MLX model loading.
        In production, this would load a quantized BGE-M3 MLX model.
        """
        try:
            from transformers import AutoModel, AutoTokenizer

            logger.info(f"[BGE-M3] Loading transformers model: {self._config.model_id}")
            model_path = self._config.model_id
            cache_dir = MODEL_CACHE_DIR / "bge-m3"
            if cache_dir.exists():
                model_path = str(cache_dir)
            self._tokenizer = AutoTokenizer.from_pretrained(model_path, cache_dir=str(MODEL_CACHE_DIR))
            self._hf_model = AutoModel.from_pretrained(model_path, cache_dir=str(MODEL_CACHE_DIR))
            logger.warning(
                "[BGE-M3] Using transformers backend. For full MLX support, convert model with: mlx-transformers"
            )
            return None
        except Exception as e:
            logger.error(f"[BGE-M3] Custom model creation failed: {e}")
            raise

    def _load_onnx(self) -> bool:
        """Load BGE-M3 via ONNX Runtime (CPU fallback)."""
        try:
            from optimum.onnxruntime import ORTModelForFeatureExtraction

            logger.info(f"[BGE-M3] Loading via ONNX Runtime: {self._config.model_id}")
            self._model = ORTModelForFeatureExtraction.from_pretrained(
                self._config.model_id, export=True, cache_dir=str(MODEL_CACHE_DIR)
            )
            self._is_loaded = True
            logger.info("[BGE-M3] ONNX load successful")
            return True
        except ImportError:
            logger.warning("[BGE-M3] ONNX Runtime not available")
            self._backend = BGEBackend.TRANSFORMERS
            return self._load_transformers()
        except Exception as e:
            logger.error(f"[BGE-M3] ONNX load failed: {e}")
            return False

    def _load_transformers(self) -> bool:
        """Load BGE-M3 via HuggingFace transformers (CPU fallback)."""
        try:
            from transformers import AutoModel, AutoTokenizer

            logger.info(f"[BGE-M3] Loading via transformers (CPU): {self._config.model_id}")
            self._tokenizer = AutoTokenizer.from_pretrained(self._config.model_id, cache_dir=str(MODEL_CACHE_DIR))
            self._model = AutoModel.from_pretrained(self._config.model_id, cache_dir=str(MODEL_CACHE_DIR))
            self._model.eval()
            from .mrl import MRLTruncator

            self._mrl_truncator = MRLTruncator(
                source_dim=NATIVE_DIM, target_dim=self._config.mrl_target_dim, normalize=self._config.normalize
            )
            self._is_loaded = True
            logger.info("[BGE-M3] Transformers load successful")
            return True
        except Exception as e:
            logger.error(f"[BGE-M3] Transformers load failed: {e}")
            return False

    async def embed(self, text: str, truncate_to: int | None = None, normalize: bool = True) -> np.ndarray:
        """
        Embed single text to multilingual vector.

        Args:
            text: Input text (any language).
            truncate_to: Target dimension for MRL truncation.
                        If None, uses config.mrl_target_dim.
            normalize: L2-normalize output vector.

        Returns:
            Embedding vector (native_dim or truncate_to dim).
        """
        result = await self.embed_batch([text], truncate_to=truncate_to, normalize=normalize)
        return result[0]

    async def embed_batch(self, texts: list[str], truncate_to: int | None = None, normalize: bool = True) -> np.ndarray:
        """
        Embed batch of texts to multilingual vectors.

        Args:
            texts: List of input texts (can be mixed languages).
            truncate_to: Target dimension for MRL truncation.
            normalize: L2-normalize output vectors.

        Returns:
            Embedding matrix (batch, native_dim) or (batch, truncate_to).
        """
        if not self._is_loaded:
            if not self.load():
                raise RuntimeError("[BGE-M3] Failed to load model")
        target_dim = truncate_to or self._config.mrl_target_dim
        if self._backend == BGEBackend.MLX:
            embeddings = await self._embed_mlx(texts)
        elif self._backend == BGEBackend.ONNX_CPU:
            embeddings = self._embed_onnx(texts)
        else:
            embeddings = self._embed_transformers(texts)
        if target_dim != NATIVE_DIM:
            from .mrl import truncate_batch

            embeddings = truncate_batch(embeddings, target_dim, normalize=normalize)
        elif normalize:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            embeddings = embeddings / (norms + 1e-10)
        return embeddings

    async def _embed_mlx(self, texts: list[str]) -> np.ndarray:
        """Embed via MLX backend."""
        import asyncio

        loop = asyncio.get_running_loop()

        def _encode():
            import mlx.core as mx

            # M1 8GB: bound peak memory by embedding in batches of batch_size
            # instead of materializing the full sequence batch at once (OOM guard).
            batch_size = int(getattr(self._config, "batch_size", 1)) or 1
            pooled_chunks: list[np.ndarray] = []
            for i in range(0, len(texts), batch_size):
                chunk = texts[i : i + batch_size]
                inputs = self._tokenizer(
                    chunk, padding=True, truncation=True, max_length=self._config.max_seq_len, return_tensors="mlx"
                )
                outputs = self._model(input_ids=inputs.input_ids, attention_mask=inputs.attention_mask)
                attention_mask = mx.expand_dims(inputs.attention_mask, axis=-1)
                hidden = outputs.last_hidden_state
                pooled = (hidden * attention_mask).sum(axis=1) / attention_mask.sum(axis=1)
                pooled_chunks.append(np.array(pooled, dtype=np.float32))
            if not pooled_chunks:
                return np.empty((0, self._config.mrl_target_dim), dtype=np.float32)
            return np.concatenate(pooled_chunks, axis=0)

        return await loop.run_in_executor(None, _encode)

    def _embed_onnx(self, texts: list[str]) -> np.ndarray:
        """Embed via ONNX Runtime."""
        inputs = self._processor(
            texts=texts, padding=True, truncation=True, max_length=self._config.max_seq_len, return_tensors="np"
        )
        outputs = self._model(**inputs)
        return outputs.last_hidden_state.mean(axis=1).astype(np.float32)

    def _embed_transformers(self, texts: list[str]) -> np.ndarray:
        """Embed via HuggingFace transformers."""
        import torch

        inputs = self._tokenizer(
            texts, padding=True, truncation=True, max_length=self._config.max_seq_len, return_tensors="pt"
        )
        with torch.no_grad():
            outputs = self._model(**inputs)
        attention_mask = inputs.attention_mask.unsqueeze(-1)
        hidden = outputs.last_hidden_state.numpy()
        pooled = (hidden * attention_mask.numpy()).sum(axis=1) / attention_mask.numpy().sum(axis=1)
        return pooled.astype(np.float32)

    def unload(self) -> None:
        """Unload model and free memory."""
        self._model = None
        self._processor = None
        self._tokenizer = None
        self._hf_model = None
        self._is_loaded = False
        try:
            import mlx.core as mx

            # INVARIANT #2: mx.eval([]) barrier BEFORE clear_cache(),
            # otherwise clear_cache() is a no-op on M1 unified memory.
            mx.eval([])
            mx.metal.clear_cache()
        except (ImportError, AttributeError):
            pass
        logger.info("[BGE-M3] Model unloaded, memory freed")


_bge_m3_instance: BGEM3Embedder | None = None


def get_bge_m3_embedder(mrl_target_dim: int = MRL_TARGET_DIM, lazy_load: bool = True) -> BGEM3Embedder:
    """
    Get singleton BGE-M3 embedder instance.

    Args:
        mrl_target_dim: Target dimension for MRL truncation.
        lazy_load: Defer model loading until first use.

    Returns:
        Shared BGEM3Embedder instance.
    """
    global _bge_m3_instance
    if _bge_m3_instance is None:
        _bge_m3_instance = BGEM3Embedder(mrl_target_dim=mrl_target_dim, lazy_load=lazy_load)
    return _bge_m3_instance
