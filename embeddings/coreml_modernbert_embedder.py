"""
CoreMLModernBERTEmbedder — ANE-accelerated ModernBERT encoder for embeddings.

Provides:
- Batch text embedding via CoreML on Apple Neural Engine (ANE)
- Pre-converted .mlpackage models (coremltools, py3.12 compatible)
- Fallback: ModernBERTEmbedder (MLX/Metal) if ANE unavailable
- Lazy loading — no network/CoreML at import time

F4.3: Canonical CoreML ANE backend for ModernBERT.
Requires pre-converted model at MODELS_DIR / "modernbert_ane.mlpackage".
Conversion: coremltools.convert(model, compute_units=ComputeUnit.ANE)

Canonical import: from hledac.universal.embeddings.coreml_modernbert_embedder import CoreMLModernBERTEmbedder
"""
import logging
import os
from dataclasses import dataclass, field
import msgspec
from pathlib import Path
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    import numpy as np
logger = logging.getLogger(__name__)
_MODELS_DIR = Path.home() / '.hledac' / 'models'
_ANE_MODEL_PATH = _MODELS_DIR / 'modernbert_ane.mlpackage'
COREML_ENGINE_AVAILABLE = False
_COREML_CHECKED = False

def _check_coreml_engine_available() -> bool:
    """
    Probe CoreML ANE engine availability.

    Checks:
    1. coremltools >= 6.0 installed
    2. .mlpackage exists at _ANE_MODEL_PATH
    3. Apple Silicon (darwin arm64)

    Called lazily on first embed() call — no side effects at import time.
    Cached after first call.
    """
    global COREML_ENGINE_AVAILABLE, _COREML_CHECKED
    if _COREML_CHECKED:
        return COREML_ENGINE_AVAILABLE
    _COREML_CHECKED = True
    import platform
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        logger.debug('[CoreML-ANE] Not Apple Silicon — ANE unavailable')
        return False
    try:
        import coremltools as ct
        if ct.__version__ < '6.0':
            logger.debug(f'[CoreML-ANE] coremltools {ct.__version__} < 6.0 — ANE unavailable')
            return False
    except ImportError:
        logger.debug('[CoreML-ANE] coremltools not installed')
        return False
    if not _ANE_MODEL_PATH.exists():
        logger.debug(f'[CoreML-ANE] Model not found at {_ANE_MODEL_PATH}')
        return False
    COREML_ENGINE_AVAILABLE = True
    logger.info(f'[CoreML-ANE] Engine available — model: {_ANE_MODEL_PATH}')
    return True

@dataclass(True)
class CoreMLModernBERTConfig:
    """Configuration for CoreML ANE ModernBERT embedder."""
    model_path: Path = field(default_factory=lambda: _ANE_MODEL_PATH)
    max_seq_len: int = 512
    embed_dim: int = 768
    batch_size: int = 16
    normalize: bool = True
    fallback_to_mlx: bool = True

class CoreMLModernBERTEncoder:
    """
    CoreML ANE encoder for ModernBERT — pre-converted .mlpackage.

    Uses coremltools prediction API (synchronous, thread-safe after GIL release).
    Requires pre-converted model with compute_units=ct.ComputeUnit.ANE.

    Lazy: model loaded on first embed() call.
    Fail-soft: logs error and returns None on any failure (ANE is optional).
    """
    __slots__ = tuple(('_model', '_model_path'))

    def __init__(self, model_path: Path | str) -> None:
        self._model_path = Path(model_path)
        self._model: Any = None

    def _ensure_model(self) -> bool:
        """Load model on first use. Returns True if loaded."""
        if self._model is not None:
            return True
        try:
            import coremltools as ct
            logger.info(f'[CoreML-ANE] Loading: {self._model_path}')
            self._model = ct.models.MLModel(str(self._model_path))
            logger.info('[CoreML-ANE] Model loaded successfully')
            return True
        except Exception as e:
            logger.error(f'[CoreML-ANE] Failed to load model: {e}')
            self._model = None
            return False

    def encode(self, texts: list[str]) -> list[list[float]] | None:
        """
        Encode texts via CoreML ANE.

        Args:
            texts: List of strings to encode.

        Returns:
            List of embedding vectors (embed_dim each), or None on failure.
        """
        if not self._ensure_model():
            return None
        try:
            import coremltools as ct
            spec = self._model._spec
            input_name = spec.description.input[0].name
            output_name = spec.description.output[0].name
            if len(texts) == 1:
                if hasattr(self._model, 'predict'):
                    result = self._model.predict({input_name: texts[0]})
                else:
                    result = self._model._model.predict({input_name: texts[0]})
                embeddings = result.get(output_name) if result else None
            else:
                vectors: list[list[float]] = []
                for text in texts:
                    if hasattr(self._model, 'predict'):
                        r = self._model.predict({input_name: text})
                    else:
                        r = self._model._model.predict({input_name: text})
                    vec = r.get(output_name)
                    if vec is None:
                        break
                    if isinstance(vec, list):
                        vectors.append(vec)
                    elif hasattr(vec, 'tolist'):
                        vectors.append(vec.tolist())
                    else:
                        break
                embeddings = vectors if len(vectors) == len(texts) else None
            if embeddings is None:
                logger.error('[CoreML-ANE] No embedding in model output')
                return None
            if isinstance(embeddings, list) and len(embeddings) > 0:
                vectors: list[list[float]] = embeddings
                if self._normalize:
                    vectors = [_normalize_l2(v) for v in vectors]
                return vectors
            return None
        except Exception as e:
            logger.error(f'[CoreML-ANE] Encode failed: {e}')
            return None

    @property
    def _normalize(self) -> bool:
        return True

class CoreMLModernBERTEmbedder:
    """
    ANE-accelerated ModernBERT embedder with MLX fallback.

    F4.3: Canonical CoreML ANE backend for ModernBERT embedding workloads.
    Prefer this over ModernBERTEmbedder when:
    - .mlpackage exists at ~/.hledac/models/modernbert_ane.mlpackage
    - ANE is available (M1/M2/M3 chip)

    Architecture:
    1. Check ANE availability on first embed() call
    2. If available: load CoreML model → encode via ANE
    3. If unavailable or fails: fall back to ModernBERTEmbedder (MLX/Metal)

    M1 8GB: ANE uses dedicated ~300MB ANE memory, separate from Metal/UMA.
    Metal cache remains available for MLX LLM inference.

    Lazy: no model loaded at __init__ time.
    Thread-safe: model loading guarded by threading.Lock.
    """
    __slots__ = tuple(('_encoder', '_lock', '_mlx_embedder', 'config'))

    def __init__(self, model_path: Path | str | None=None, lazy_load: bool=True, normalize: bool=True, batch_size: int=16, fallback_to_mlx: bool=True) -> None:
        """
        Initialize CoreML ModernBERT embedder.

        Args:
            model_path: Path to .mlpackage. Defaults to ~/.hledac/models/modernbert_ane.mlpackage.
            lazy_load: Defer model load until first embed() call (default True).
            normalize: L2-normalize embeddings (default True for retrieval).
            batch_size: Batch size for ANE encoding (default 16).
            fallback_to_mlx: Fall back to MLX if ANE unavailable (default True).
        """
        self.config = CoreMLModernBERTConfig(model_path=Path(model_path) if model_path else _ANE_MODEL_PATH, normalize=normalize, batch_size=batch_size, fallback_to_mlx=fallback_to_mlx)
        self._encoder: CoreMLModernBERTEncoder | None = None
        self._mlx_embedder: Any = None
        self._lock = __import__('threading').Lock()
        if not lazy_load:
            self._ensure_ane()

    def _ensure_ane(self) -> bool:
        """Ensure ANE encoder is loaded. Returns True if ANE is active."""
        with self._lock:
            if self._encoder is not None:
                return True
            if _check_coreml_engine_available():
                self._encoder = CoreMLModernBERTEncoder(self.config.model_path)
                return self._encoder._ensure_model()
            return False

    def _load_mlx_fallback(self) -> Any:
        """Lazy-load MLX fallback embedder."""
        if self._mlx_embedder is None:
            try:
                from .modernbert_embedder import ModernBERTEmbedder
                self._mlx_embedder = ModernBERTEmbedder(model_path=str(self.config.model_path), lazy_load=True, normalize=self.config.normalize, batch_size=self.config.batch_size)
            except Exception as e:
                logger.error(f'[CoreML-ANE] MLX fallback load failed: {e}')
                raise RuntimeError('CoreML ANE unavailable and MLX fallback also failed') from e
        return self._mlx_embedder

    @property
    def is_loaded(self) -> bool:
        """True if ANE model or MLX fallback is loaded."""
        return self._encoder is not None or self._mlx_embedder is not None

    def encode(self, texts: str | list[str], **kwargs: Any) -> np.ndarray:
        """
        Encode texts via CoreML ANE (or MLX fallback).

        Args:
            texts: Single text or list of texts.
            **kwargs: Passed through to MLX fallback if used.

        Returns:
            np.ndarray embedding matrix (N, 768) for list, (768,) for single.
        """
        if isinstance(texts, str):
            result = self.embed(texts, **kwargs)
            return result
        return self.embed_batch(list(texts), **kwargs)

    def embed(self, text: str, **kwargs: Any) -> np.ndarray:
        """
        Encode single text via CoreML ANE (or MLX fallback).

        Args:
            text: Text to encode.
            **kwargs: task="search_document" etc.

        Returns:
            np.ndarray embedding vector (768,)
        """
        result = self.embed_batch([text], **kwargs)
        if result is None or len(result) == 0:
            import numpy as np
            return np.zeros(self.config.embed_dim, dtype=np.float32)
        return result[0]

    def embed_batch(self, texts: list[str], **kwargs: Any) -> np.ndarray:
        """
        Encode batch of texts via CoreML ANE (or MLX fallback).

        Args:
            texts: List of texts to encode.
            **kwargs: Passed through to MLX fallback if used.

        Returns:
            np.ndarray embedding matrix (N, 768).
        """
        import numpy as np
        if self._ensure_ane() and self._encoder is not None:
            vectors = self._encoder.encode(texts)
            if vectors is not None:
                return np.array(vectors, dtype=np.float32)
        if self.config.fallback_to_mlx:
            logger.debug('[CoreML-ANE] Falling back to MLX embedder')
            mlx_emb = self._load_mlx_fallback()
            return mlx_emb.embed_batch(texts, **kwargs)
        logger.warning('[CoreML-ANE] ANE unavailable, fallback disabled — returning zeros')
        return np.zeros((len(texts), self.config.embed_dim), dtype=np.float32)

def _normalize_l2(vec: list[float]) -> list[float]:
    """L2-normalize a vector."""
    import math
    norm = math.sqrt(sum((x * x for x in vec)))
    if norm < 1e-10:
        return vec
    return [x / norm for x in vec]

def get_ane_embedder() -> CoreMLModernBERTEmbedder | None:
    """
    Get CoreML ANE ModernBERT embedder if available.

    Returns None if ANE engine not available (model missing, non-Apple Silicon,
    or coremltools < 6.0). Check .is_loaded to confirm successful load.
    """
    if not _check_coreml_engine_available():
        return None
    return CoreMLModernBERTEmbedder(lazy_load=True)