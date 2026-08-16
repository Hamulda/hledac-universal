"""
embeddings/ane/_encoder.py — CoreML ANE Encoder (F330-MLX-DUP-007)

CoreML-accelerated ModernBERT encoder pro Apple Neural Engine.

Pre-converted .mlpackage model → ANE → embeddings.

Tento modul obsahuje low-level CoreML encoder.
Vysokoúrovňový unified embedder je v `embeddings/ane/__init__.py`.
"""
import logging
from pathlib import Path
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)


def _parse_coreml_version() -> tuple[int, ...] | None:
    """
    E-31 FIX: Parse coremltools version as tuple for safe semantic comparison.

    Reason: string comparison '10.0' < '6.0' is True (lexicographic),
    but (10, 0) > (6, 0) correctly identifies 10.x > 6.x.
    Returns None if coremltools is not installed.
    """
    try:
        import coremltools as ct
        parts = ct.__version__.split('.')
        return tuple(int(p) for p in parts[:2] if p.isdigit())
    except Exception:
        return None
_MODELS_DIR = Path.home() / '.hledac' / 'models'
_ANNOT_MODEL_PATH = _MODELS_DIR / 'modernbert_ane.mlpackage'

def _check_coreml_engine_available() -> bool:
    """
    Probe CoreML ANE engine availability.

    Checks:
    1. coremltools >= 6.0 installed
    2. .mlpackage exists at _ANE_MODEL_PATH
    3. Apple Silicon (darwin arm64)
    4. Python version compatible (no PyPI wheels for 3.14 yet)
    """
    import platform
    import sys
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        logger.debug('[CoreML-ANE] Not Apple Silicon — ANE unavailable')
        return False

    # Python 3.14: coremltools lacks PyPI wheels — detect and guide user
    if sys.version_info >= (3, 14):
        try:
            import coremltools as ct
        except ImportError:
            logger.debug(
                '[CoreML-ANE] Python 3.14 detected — coremltools PyPI wheels unavailable. '
                'Install from GitHub: pip install --extra-index-url https://pypi.anaconda.org/apple/repo/simple coremltools'
    )
            return False
        # If we get here, coremltools is somehow installed (maybe from conda)
        version_tuple = _parse_coreml_version()
        if version_tuple is not None and version_tuple < (6, 0):
            logger.debug(f'[CoreML-ANE] coremltools {".".join(map(str, version_tuple))} < 6.0 — upgrade recommended')
        return _ANNOT_MODEL_PATH.exists()

    try:
        import coremltools as ct
        version_tuple = _parse_coreml_version()
        if version_tuple is None:
            logger.debug(
                '[CoreML-ANE] coremltools not installed. '
                'Install: pip install coremltools'
    )
            return False
        if version_tuple < (6, 0):
            logger.debug(f'[CoreML-ANE] coremltools {".".join(map(str, version_tuple))} < 6.0')
            return False
    except ImportError:
        logger.debug(
            '[CoreML-ANE] coremltools not installed. '
            'Install: pip install coremltools'
    )
        return False
    if not _ANNOT_MODEL_PATH.exists():
        logger.debug(f'[CoreML-ANE] Model not found at {_ANNOT_MODEL_PATH}')
        return False
    return True

class CoreMLModernBERTEncoder:
    """
    Low-level CoreML ANE encoder.

    Wrapper kolem pre-converted modernbert_ane.mlpackage.
    Používá se přes `CoreMLModernBERTEmbedder` v `embeddings/coreml_modernbert_embedder.py`.

    Usage:
        encoder = CoreMLModernBERTEncoder(_ANE_MODEL_PATH)
        encoder._ensure_model()
        embeddings = encoder.encode(["text1", "text2"])
    """
    __slots__ = tuple(('_model', '_model_path', '_tokenizer'))

    def __init__(self, model_path: Path | str | None=None) -> None:
        self._model_path = Path(model_path) if model_path else _ANNOT_MODEL_PATH
        self._model: Any = None
        self._tokenizer: Any = None

    def _ensure_model(self) -> bool:
        """Load CoreML model. Returns True on success."""
        if self._model is not None:
            return True
        try:
            import coremltools as ct
            self._model = ct.models.MLModel(str(self._model_path))
            logger.info(f'[CoreML-ANE] Model loaded from {self._model_path}')
            return True
        except Exception as e:
            logger.error(f'[CoreML-ANE] Model load failed: {e}')
            return False

    def _load_tokenizer(self) -> bool:
        """Lazy-load tokenizer."""
        if self._tokenizer is not None:
            return True
        try:
            from transformers import AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained('answerdotai/ModernBERT-base')
            return True
        except Exception as e:
            logger.error(f'[CoreML-ANE] Tokenizer load failed: {e}')
            return False

    def encode(self, texts: list[str]) -> Any | None:
        """
        Encode texts to embeddings via CoreML ANE.

        Args:
            texts: List of text strings.

        Returns:
            numpy array of embeddings or None on failure.
        """
        if not self._ensure_model():
            return None
        if not self._load_tokenizer():
            return None
        try:
            import numpy as np
            inputs = self._tokenizer(texts, padding=True, truncation=True, max_length=512, return_tensors='np')
            input_ids = inputs['input_ids'].astype(np.int32)
            attention_mask = inputs['attention_mask'].astype(np.int32)
            out = self._model.predict({'input_ids': input_ids, 'attention_mask': attention_mask})
            embeddings = out['sequence_embedding']
            return embeddings
        except Exception as e:
            logger.error(f'[CoreML-ANE] encode failed: {e}')
            return None