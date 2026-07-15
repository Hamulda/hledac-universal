"""
brain/_mlx_dispatcher.py
=======================
Sprint F320: Central MLX routing layer.

Přepíná celý brain/ subsystém na MLX unified memory při HLEDAC_MLX=1.
Fallback hierarchie (vždy fail-safe, vrací prázdné výsledky při chybě):

    MLX only (HLEDAC_MLX=1):
    ├── Embedding → mlx_embedding_models.EmbeddingModel (BAAI/bge-small-en-v1.5)
    ├── NER       → mlx_gliner2.GLiNER2 (fastino/gliner2-base-v1)
    └── Outlines  → outlines.models.mlx (Llama-3.2-3B-Instruct-4bit)

    Hybridní (HLEDAC_MLX=0 / default):
    ├── Embedding → CoreMLEmbedder (mlx → coreml HTTP → onnxruntime CPU → hash)
    ├── NER       → NEREngine (mlx_gliner2 → NL.framework → coreml → gliner2 subprocess)
    └── Outlines  → outlines subprocess

Žádné top-level MLX importy — vše lazy, přes try/except ImportError.
M1 8GB: unified memory znamená žádný IPC marshalling overhead.

Usage:
    from brain._mlx_dispatcher import MLXDispatcher, get_mlx_dispatcher

    dispatcher = get_mlx_dispatcher()
    if dispatcher.is_mlx_enabled:
        embeddings = await dispatcher.embed_batch(["text1", "text2"])
        entities   = await dispatcher.ner_predict("Apple released iPhone 15", ["organization", "product"])
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ── Lazy MLX availability flags ──────────────────────────────────────────────

_MLX_AVAILABLE: bool = False
_MLX_EMBED_AVAILABLE: bool = False
_MLX_GLINER2_AVAILABLE: bool = False
_MLX_OUTLINES_AVAILABLE: bool = False

_EMBEDDER: Any = None
_GLINER2: Any = None
_OUTLINES: Any = None
_INIT_LOCK = threading.Lock()
_INITIALIZED = False

# ── Env gate ─────────────────────────────────────────────────────────────────

_HLEDAC_MLX_ENABLED = os.environ.get('HLEDAC_MLX', '0') == '1'


def _is_mlx_enabled() -> bool:
    """Globální MLX routing gate — nastavuje celý brain/ do MLX-only režimu."""
    return _HLEDAC_MLX_ENABLED


# ── Lazy init ────────────────────────────────────────────────────────────────

def _check_mlx_availability() -> None:
    """Jednorázová kontrola MLX knihoven — thread-safe DCLP."""
    global _MLX_AVAILABLE, _MLX_EMBED_AVAILABLE, _MLX_GLINER2_AVAILABLE
    global _MLX_OUTLINES_AVAILABLE, _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        _INITIALIZED = True

        # MLX core
        try:
            import mlx.core as _mx
            _MLX_AVAILABLE = True
            logger.debug('[MLXDispatcher] mlx.core available')
        except ImportError:
            _MLX_AVAILABLE = False
            logger.debug('[MLXDispatcher] mlx.core not available')
            return

        # mlx_embedding_models
        try:
            from mlx_embedding_models.embedding import EmbeddingModel
            _MLX_EMBED_AVAILABLE = True
            logger.debug('[MLXDispatcher] mlx_embedding_models available')
        except ImportError:
            _MLX_EMBED_AVAILABLE = False
            logger.debug('[MLXDispatcher] mlx_embedding_models not available')

        # mlx_gliner2
        try:
            import mlx_gliner2
            _MLX_GLINER2_AVAILABLE = True
            logger.debug('[MLXDispatcher] mlx_gliner2 available')
        except ImportError:
            _MLX_GLINER2_AVAILABLE = False
            logger.debug('[MLXDispatcher] mlx_gliner2 not available')

        # outlines mlx
        try:
            from outlines.models import mlx as _mlx_outlines
            _MLX_OUTLINES_AVAILABLE = True
            logger.debug('[MLXDispatcher] outlines[mlx] available')
        except ImportError:
            _MLX_OUTLINES_AVAILABLE = False
            logger.debug('[MLXDispatcher] outlines[mlx] not available')


# ── Embedder ─────────────────────────────────────────────────────────────────

async def _load_mlx_embedder() -> Any:
    """Lazy load MLX embedder (EmbeddingModel from mlx-embedding-models)."""
    global _EMBEDDER
    if _EMBEDDER is not None:
        return _EMBEDDER
    _check_mlx_availability()
    if not _MLX_EMBED_AVAILABLE:
        return None
    try:
        from mlx_embedding_models.embedding import EmbeddingModel

        def _load() -> Any:
            # nativní MLX: model běží v unified memory, žádný subprocess
            return EmbeddingModel.from_registry('BAAI/bge-small-en-v1.5')

        loop = asyncio.get_running_loop()
        _EMBEDDER = await loop.run_in_executor(None, _load)
        logger.info('[MLXDispatcher] MLX embedder loaded — unified memory')
        return _EMBEDDER
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX embedder load failed: %s', e)
        return None


def _encode_mlx_batch_sync(model: Any, texts: list[str]) -> np.ndarray:
    """L2-normalizované embeddings přes MLX — volá se z thread poolu."""
    try:
        raw: list[list[float]] = model.encode(texts)  # type: ignore[attr-defined]
        arr = np.array(raw, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / (norms + 1e-08)
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX encode failed: %s', e)
        n = len(texts)
        return np.zeros((n, 384), dtype=np.float32)


# ── GLiNER2 ──────────────────────────────────────────────────────────────────

async def _load_mlx_gliner2() -> Any:
    """Lazy load MLX GLiNER2 extractor."""
    global _GLINER2
    if _GLINER2 is not None:
        return _GLINER2
    _check_mlx_availability()
    if not _MLX_GLINER2_AVAILABLE:
        return None
    try:
        import mlx_gliner2
        import os

        def _load() -> Any:
            model_path = os.environ.get(
                'MLX_GLINER2_MODEL',
                str(Path.home() / '.hledac' / 'models' / 'fastino_gliner2-base-v1')
            )
            return mlx_gliner2.GLiNER2.from_pretrained(model_path)

        loop = asyncio.get_running_loop()
        _GLINER2 = await loop.run_in_executor(None, _load)
        logger.info('[MLXDispatcher] MLX GLiNER2 loaded — Metal GPU / ANE')
        return _GLINER2
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX GLiNER2 load failed: %s', e)
        return None


def _gliner2_extract_sync(
    model: Any, text: str, labels: list[str], threshold: float = 0.5
) -> list[dict[str, Any]]:
    """Single-text GLiNER2 inference — volá se z thread poolu."""
    try:
        # SPRINT F320: List[Dict] API s text/label/score/start/end
        items: list[dict[str, Any]] = model.extract_entities(
            text, labels, threshold=threshold,
            include_confidence=True, include_spans=True
        )
        return [
            {
                'entity': item.get('text', ''),
                'label': item.get('label', ''),
                'span': (item.get('start', 0), item.get('end', 0)),
                'score': item.get('score', 0.9),
            }
            for item in items
        ]
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX GLiNER2 extract failed: %s', e)
        return []


def _gliner2_batch_sync(
    model: Any, texts: list[str], labels: list[str],
    threshold: float = 0.5, batch_size: int = 8
) -> list[list[dict[str, Any]]]:
    """Batch GLiNER2 inference — plně paralelní přes Metal."""
    try:
        # SPRINT F320: batch_extract_entities — jeden Metal kernel pro celý batch
        results: list[list[dict[str, Any]]] = model.batch_extract_entities(
            texts, labels, threshold=threshold, batch_size=batch_size,
            include_confidence=True, include_spans=True
        )
        normalized: list[list[dict[str, Any]]] = []
        for batch_result in results:
            normalized.append([
                {
                    'entity': item.get('text', ''),
                    'label': item.get('label', ''),
                    'span': (item.get('start', 0), item.get('end', 0)),
                    'score': item.get('score', 0.9),
                }
                for item in batch_result
            ])
        return normalized
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX GLiNER2 batch failed: %s', e)
        return [[] for _ in texts]


# ── Outlines ─────────────────────────────────────────────────────────────────

async def _load_mlx_outlines() -> Any:
    """Lazy load MLX Outlines extractor (Llama structured generation)."""
    global _OUTLINES
    if _OUTLINES is not None:
        return _OUTLINES
    _check_mlx_availability()
    if not _MLX_OUTLINES_AVAILABLE:
        return None
    try:
        from outlines.models import mlx as mlx_outlines

        def _load() -> Any:
            return mlx_outlines('mlx-community/Llama-3.2-3B-Instruct-4bit')

        loop = asyncio.get_running_loop()
        _OUTLINES = await loop.run_in_executor(None, _load)
        logger.info('[MLXDispatcher] MLX Outlines loaded — Metal GPU')
        return _OUTLINES
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX Outlines load failed: %s', e)
        return None


# ── Dispatcher ───────────────────────────────────────────────────────────────

class MLXDispatcher:
    """
    Central MLX routing pro celý brain/ subsystém.

    Při HLEDAC_MLX=1:
    - veškerý inference jde přes MLX unified memory
    - žádné CoreML HTTP subprocess, žádné ONNX CPU fallback

    Při HLEDAC_MLX=0 (default):
    - MLXDispatcher funguje jako thin proxy
    - skutečný routing dědí jednotlivé enginy (CoreMLEmbedder, NEREngine, …)
    """

    __slots__ = tuple((
        '_embedder', '_gliner2', '_outlines',
        '_embed_loaded', '_gliner2_loaded', '_outlines_loaded',
    ))

    def __init__(self) -> None:
        self._embedder: Any = None
        self._gliner2: Any = None
        self._outlines: Any = None
        self._embed_loaded: bool = False
        self._gliner2_loaded: bool = False
        self._outlines_loaded: bool = False

    @property
    def is_mlx_enabled(self) -> bool:
        """True pokud HLEDAC_MLX=1 — vynucuje MLX-only režim."""
        # Env var je konstanta nastavená při importu — žádný lock nutný
        return _HLEDAC_MLX_ENABLED

    @property
    def is_embed_available(self) -> bool:
        """True pokud mlx_embedding_models lze načíst."""
        _check_mlx_availability()
        return _MLX_EMBED_AVAILABLE

    @property
    def is_gliner2_available(self) -> bool:
        """True pokud mlx_gliner2 lze načíst."""
        _check_mlx_availability()
        return _MLX_GLINER2_AVAILABLE

    @property
    def is_outlines_available(self) -> bool:
        """True pokud outlines[mlx] lze načíst."""
        _check_mlx_availability()
        return _MLX_OUTLINES_AVAILABLE

    # ── Embedding ──────────────────────────────────────────────────────────────

    async def load_embedder(self) -> bool:
        """Async lazy load MLX embedder. Vrací True pokud uspěšně načten."""
        if self._embed_loaded and self._embedder is not None:
            return True
        self._embedder = await _load_mlx_embedder()
        self._embed_loaded = self._embedder is not None
        return self._embed_loaded

    async def embed_batch(
        self, texts: str | list[str]
    ) -> np.ndarray:
        """
        Embed batch přes MLX unified memory.

        Fallback: při HLEDAC_MLX=0 nebo chybě vrací zeros.

        Note:
            batch_size je ignorován — MLX embedder používá vlastní interní
            adaptivní batching (32/64/128). Parametr je tu pro API kompatibilitu
            s ostatními embeddery.

        Returns:
            np.ndarray shape (len(texts), 384), dtype float32, L2-normalized.
        """
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)

        if not self._embed_loaded:
            await self.load_embedder()
        if self._embedder is None:
            return np.zeros((len(texts), 384), dtype=np.float32)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _encode_mlx_batch_sync, self._embedder, texts
        )

    # ── NER ──────────────────────────────────────────────────────────────────

    async def load_gliner2(self) -> bool:
        """Async lazy load MLX GLiNER2. Vrací True pokud uspěšně načten."""
        if self._gliner2_loaded and self._gliner2 is not None:
            return True
        self._gliner2 = await _load_mlx_gliner2()
        self._gliner2_loaded = self._gliner2 is not None
        return self._gliner2_loaded

    async def ner_predict(
        self, text: str, labels: list[str], *, threshold: float = 0.5
    ) -> list[dict[str, Any]]:
        """
        Single-text NER přes MLX GLiNER2.

        Fallback: při HLEDAC_MLX=0 nebo chybě vrací [].

        Returns:
            List[Dict] s keys: entity, label, span, score.
        """
        if not text or not text.strip():
            return []
        if not labels:
            return []

        if not self._gliner2_loaded:
            await self.load_gliner2()
        if self._gliner2 is None:
            return []

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _gliner2_extract_sync, self._gliner2, text, labels, threshold
        )

    async def ner_predict_batch(
        self, texts: list[str], labels: list[str], *,
        threshold: float = 0.5, batch_size: int = 8
    ) -> list[list[dict[str, Any]]]:
        """
        Batch NER přes MLX GLiNER2 — paralelní Metal inference.

        Sprint F320: používá batch_extract_entities — jeden Metal kernel
        pro všechny texty v batchi, výrazně rychlejší než per-text loop.

        Fallback: při HLEDAC_MLX=0 nebo chybě vrací [[] for _ in texts].

        Returns:
            List[List[Dict]] — pro každý text seznam entit.
        """
        if not texts:
            return []
        if not labels:
            return [[] for _ in texts]

        if not self._gliner2_loaded:
            await self.load_gliner2()
        if self._gliner2 is None:
            return [[] for _ in texts]

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _gliner2_batch_sync,
            self._gliner2, texts, labels, threshold, batch_size
        )

    # ── Outlines ─────────────────────────────────────────────────────────────

    async def load_outlines(self) -> bool:
        """Async lazy load MLX Outlines. Vrací True pokud uspěšně načten."""
        if self._outlines_loaded and self._outlines is not None:
            return True
        self._outlines = await _load_mlx_outlines()
        self._outlines_loaded = self._outlines is not None
        return self._outlines_loaded

    async def structured_predict(
        self, text: str, schema: str, *, model_name: str = 'mlx-community/Llama-3.2-3B-Instruct-4bit'
    ) -> dict[str, Any]:
        """
        Structured generation přes MLX Outlines.

        Fallback: při HLEDAC_MLX=0 nebo chybě vrací {}.
        """
        if not text or not text.strip():
            return {}
        if not schema:
            return {}

        if not self._outlines_loaded:
            await self.load_outlines()
        if self._outlines is None:
            return {}

        try:
            import outlines

            def _run() -> dict[str, Any]:
                generator = outlines.generate.text(self._outlines, schema)
                result = generator(text)
                try:
                    import msgspec
                    return msgspec.json.decode(result)
                except Exception:
                    return {'raw': result}

            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _run)
        except Exception as e:
            logger.warning('[MLXDispatcher] MLX Outlines failed: %s', e)
            return {}

    # ── Unload ────────────────────────────────────────────────────────────────

    def unload(self) -> None:
        """Uvolní všechny MLX modely z paměti."""
        self._embedder = None
        self._gliner2 = None
        self._outlines = None
        self._embed_loaded = False
        self._gliner2_loaded = False
        self._outlines_loaded = False
        try:
            import mlx.core as mx
            mx.eval([])
            mx.metal.clear_cache()
        except Exception:
            pass
        logger.info('[MLXDispatcher] Unloaded — metal cache cleared')


# ── Singleton ────────────────────────────────────────────────────────────────

_dispatcher: MLXDispatcher | None = None
_dispatcher_lock = threading.Lock()


def get_mlx_dispatcher() -> MLXDispatcher:
    """Vrací singleton MLXDispatcher — thread-safe DCLP."""
    global _dispatcher
    if _dispatcher is None:
        with _dispatcher_lock:
            if _dispatcher is None:
                _dispatcher = MLXDispatcher()
    return _dispatcher


# ── Backward-compat shims ────────────────────────────────────────────────────

def is_mlx_available() -> bool:
    """Deprecated — použij MLXDispatcher().is_mlx_enabled."""
    _check_mlx_availability()
    return _MLX_AVAILABLE


def is_mlx_embed_available() -> bool:
    """Deprecated — použij MLXDispatcher().is_embed_available."""
    _check_mlx_availability()
    return _MLX_EMBED_AVAILABLE


def is_mlx_gliner2_available() -> bool:
    """Deprecated — použij MLXDispatcher().is_gliner2_available."""
    _check_mlx_availability()
    return _MLX_GLINER2_AVAILABLE
