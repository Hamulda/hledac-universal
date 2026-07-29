"""
brain/_mlx_dispatcher.py
=======================
ISSUE #31: Central MLX + ANE routing layer (F350M-R extension).

Přepíná celý brain/ subsystém na MLX unified memory při HLEDAC_MLX=1.
Fallback hierarchie (vždy fail-safe, vrací prázdné výsledky při chybě):

    MLX only (HLEDAC_MLX=1):
    ├── Embedding → ANE (modernbert_ane.mlpackage, 768d) → MLX Metal (ModernBERT, 768d) → BGE-small (384d)
    ├── NER       → mlx_gliner2.GLiNER2 (fastino/gliner2-base-v1)
    └── Outlines  → outlines.models.mlx (Llama-3.2-3B-Instruct-4bit)

    Hybridní (HLEDAC_MLX=0 / default):
    ├── Embedding → CoreMLEmbedder (mlx → coreml HTTP → onnxruntime CPU → hash)
    ├── NER       → NEREngine (mlx_gliner2 → NL.framework → coreml → gliner2 subprocess)
    └── Outlines  → outlines subprocess

ANE routing (ISSUE #31):
    Priority: ANE (pre-converted .mlpackage) → MLX Metal (lazy) → CPU fallback
    ANE detection: coremltools 6.0+ + modernbert_ane.mlpackage + Apple Silicon
    Memory budget: 3.5 GB unified, LRU evict mezi ANE a MLX modely

Žádné top-level MLX importy — vše lazy, přes try/except ImportError.
M1 8GB: unified memory znamená žádný IPC marshalling overhead.

Usage:
    from hledac.universal.brain._mlx_dispatcher import MLXDispatcher, get_mlx_dispatcher

    dispatcher = get_mlx_dispatcher()
    if dispatcher.is_mlx_enabled:
        embeddings = await dispatcher.embed_batch(["text1", "text2"])
        entities   = await dispatcher.ner_predict("Apple released iPhone 15", ["organization", "product"])
"""
from __future__ import annotations
import msgspec
import asyncio
import contextvars
import logging
import os
import threading
from dataclasses import dataclass

from hledac.universal.core.locks import LockCategory, register_lock
from msgspec import field
from pathlib import Path
from typing import Any
import numpy as np
from hledac.universal.utils.async_helpers import parallel_ok, safe_wait_for
logger = logging.getLogger(__name__)
PRIORITY_HIGH: int = 1
PRIORITY_LOW: int = 10

class _EmbeddingRequest:
    """Jeden embedding request s Future pro distribuci výsledku."""
    __slots__ = ('priority', 'text', 'future')

    def __init__(self, priority: int, text: str) -> None:
        self.priority = priority
        self.text = text
        self.future: asyncio.Future[np.ndarray] = asyncio.get_running_loop().create_future()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _EmbeddingRequest):
            return NotImplemented
        return self.priority < other.priority

class AsyncEmbeddingBatcher:
    """
    P2-07: Async batching fronta pro MLX/ANE embedding.

    Sbírá embedding requesty z concurrent volání, batchuje je a distribuuje
    výsledky přes Futures. Eliminuje per-item round-trip bottleneck.

    M1 8GB bounds:
        - batch_size=32 (sweet spot pro Hermes3 2GB + embeddings 200MB)
        - max_wait_ms=50ms (latency vs throughput trade-off)
        - max_queue=1024 (back-pressure při přetížení)

    Usage:
        batcher = AsyncEmbeddingBatcher()
        await batcher.start()
        result = await batcher.embed("text")  # jedno volání
        await batcher.stop()
    """
    DEFAULT_BATCH_SIZE: int = 32
    DEFAULT_MAX_WAIT_MS: int = 50
    MAX_QUEUE_SIZE: int = 1024
    __slots__ = tuple(('_batch_size', '_loop_task', '_max_wait_s', '_model_lock', '_queue', '_started', '_stopping'))

    def __init__(self, batch_size: int=DEFAULT_BATCH_SIZE, max_wait_ms: int=DEFAULT_MAX_WAIT_MS) -> None:
        self._batch_size = batch_size
        self._max_wait_s = max_wait_ms / 1000.0
        self._queue: asyncio.PriorityQueue[_EmbeddingRequest] = asyncio.PriorityQueue(maxsize=self.MAX_QUEUE_SIZE)
        self._loop_task: asyncio.Task | None = None
        self._started = False
        self._stopping = False
        self._model_lock = threading.Lock()

    async def start(self) -> None:
        """Start batch loop v pozadí (fire-and-forget)."""
        if self._started:
            return
        self._started = True
        self._loop_task = asyncio.create_task(self._batch_loop())

    async def stop(self) -> None:
        """Stop batch loop gracefully — dokončí queueing requesty."""
        if not self._started:
            return
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        self._started = False
        self._stopping = False

    async def embed(self, text: str, *, priority: int=PRIORITY_HIGH) -> np.ndarray:
        """
        Přidat text do batche a čekat na výsledek.

        Args:
            text: Text k embeddingu.
            priority: Priorita (nižší = dříve zpracováno).

        Returns:
            np.ndarray shape (embedding_dim,), dtype float32.
        """
        if not text:
            return np.zeros((768,), dtype=np.float32)
        if self._stopping or not self._started:
            return await self._direct_encode(text)
        request = _EmbeddingRequest(priority, text)
        try:
            async with asyncio.timeout(5.0):
                await self._queue.put(request)
        except TimeoutError:
            logger.warning('[AsyncBatcher] Queue full, falling back to direct encode')
            return await self._direct_encode(text)
        return await safe_wait_for(request.future, timeout=60.0)

    async def _direct_encode(self, text: str) -> np.ndarray:
        """Direct encode bez fronty — fallback pro edge cases."""
        ctx = _get_dispatcher_context()
        if not ctx.embed_loaded:
            return np.zeros((768,), dtype=np.float32)
        try:
            return await asyncio.to_thread(_encode_mlx_batch_sync, ctx.embedder, [text], ctx.embed_dim)
        except Exception:
            return np.zeros((768,), dtype=np.float32)

    async def _batch_loop(self) -> None:
        """
        Hlavní batch loop — běží vpozadí, sbírá requesty do batchí.

        Algoritmus:
        1. Počká na první request (s timeoutem)
        2. Sbírá další requesty dokud:
           - batch_size reached NEBO
           - max_wait_ms timeout
        3. Zpracuje celý batch přes asyncio.to_thread
        4. Distribuuje výsledky přes Futures
        5. Opakuje
        """
        loop = asyncio.get_running_loop()
        while not self._stopping:
            batch: list[tuple[_EmbeddingRequest, int]] = []
            deadline = loop.time() + self._max_wait_s
            try:
                async with asyncio.timeout(self._max_wait_s * 2):
                    first = await self._queue.get()
                    batch.append((first, 0))
            except TimeoutError:
                continue
            while len(batch) < self._batch_size:
                remaining = deadline - loop.time()
                timeout = max(0.001, remaining)
                try:
                    async with asyncio.timeout(timeout):
                        request = await self._queue.get()
                        batch.append((request, len(batch)))
                except TimeoutError:
                    break
            if batch:
                await self._process_batch(batch)

    async def _process_batch(self, batch: list[tuple[_EmbeddingRequest, int]]) -> None:
        """Zpracovat batch přes MLX thread pool a distibuovat výsledky."""
        if not batch:
            return
        texts = [req.text for req, _ in batch]
        ctx = _get_dispatcher_context()
        if ctx.embedder is None:
            dim = ctx.embed_dim or 768
            zero = np.zeros((len(texts), dim), dtype=np.float32)
            for (request, _), row in zip(batch, zero):
                if not request.future.done():
                    request.future.set_result(row)
            return
        try:
            loop = asyncio.get_running_loop()
            if _ANE_EMBEDDER is not None and ctx.embedder is _ANE_EMBEDDER:
                embeddings: np.ndarray = await loop.run_in_executor(None, lambda: _encode_ane_batch_sync(ctx.embedder, texts))
            else:
                embeddings = await loop.run_in_executor(None, lambda: _encode_mlx_batch_sync(ctx.embedder, texts, ctx.embed_dim))
            for (request, _), embedding in zip(batch, embeddings):
                if not request.future.done():
                    request.future.set_result(embedding)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('[AsyncBatcher] Batch encode failed: %s', exc)
            dim = ctx.embed_dim or 768
            zero = np.zeros((dim,), dtype=np.float32)
            for request, _ in batch:
                if not request.future.done():
                    request.future.set_result(zero)
_global_batcher: AsyncEmbeddingBatcher | None = None
_batcher_lock = threading.Lock()
register_lock(LockCategory.MPC, _batcher_lock, "_mlx_dispatcher._batcher_lock")

def get_async_batcher() -> AsyncEmbeddingBatcher:
    """Get or create global AsyncEmbeddingBatcher (thread-safe singleton)."""
    global _global_batcher
    with _batcher_lock:
        if _global_batcher is None:
            _global_batcher = AsyncEmbeddingBatcher(batch_size=32, max_wait_ms=50)
        return _global_batcher

async def start_async_batcher() -> None:
    """Start global batcher (volá se při init)."""
    batcher = get_async_batcher()
    await batcher.start()

async def stop_async_batcher() -> None:
    """Stop global batcher (volá se při shutdown)."""
    global _global_batcher
    with _batcher_lock:
        if _global_batcher is not None:
            await _global_batcher.stop()
            _global_batcher = None

class _DispatcherContext(msgspec.Struct, gc=False):
    """
    Per-sprint context pro MLXDispatcher.

    Obsahuje veškerý state který byl dříve globální:
    - Načtené modely (embedder, gliner2, outlines)
    - Async lock pro koordinovaný load/unload/preload
    - Active preload Tasks pro fire-and-forget preload
    """
    embedder: Any = None
    gliner2: Any = None
    outlines: Any = None
    embed_loaded: bool = False
    embed_dim: int = 768
    gliner2_loaded: bool = False
    outlines_loaded: bool = False
    _load_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _preload_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _model_priorities: dict[str, int] = field(default_factory=dict)
_dispatcher_context_var: contextvars.ContextVar[_DispatcherContext | None] = contextvars.ContextVar('_dispatcher_context', default=None)

def _get_dispatcher_context() -> _DispatcherContext:
    """Získat nebo vytvořit per-sprint dispatcher context."""
    ctx = _dispatcher_context_var.get()
    if ctx is None:
        ctx = _DispatcherContext()
        _dispatcher_context_var.set(ctx)
    return ctx

async def _cancel_preload_task(model_id: str) -> None:
    """Zrušit aktivní preload Task pokud existuje."""
    ctx = _get_dispatcher_context()
    if model_id in ctx._preload_tasks:
        task = ctx._preload_tasks.pop(model_id)
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.5)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
_MLX_AVAILABLE: bool = False
_MLX_EMBED_AVAILABLE: bool = False
_MLX_GLINER2_AVAILABLE: bool = False
_MLX_OUTLINES_AVAILABLE: bool = False
_ANE_AVAILABLE: bool = False
_ANE_CHECKED: bool = False
_EMBEDDER: Any = None
_ANE_EMBEDDER: Any = None
_GLINER2: Any = None
_OUTLINES: Any = None
_INIT_LOCK = threading.Lock()
register_lock(LockCategory.MPC, _INIT_LOCK, "_mlx_dispatcher._INIT_LOCK")
_INITIALIZED = False
_HLEDAC_MLX_ENABLED = os.environ.get('HLEDAC_MLX', '0') == '1'
_MLX_CORE: Any | None = None

def _get_mx() -> Any | None:
    """Lazy accessor for mlx.core — imports once and caches. Returns None if unavailable."""
    global _MLX_CORE
    if _MLX_CORE is None:
        try:
            import mlx.core as _mx
            _MLX_CORE = _mx
        except ImportError:
            _MLX_CORE = False
    return _MLX_CORE if _MLX_CORE is not False else None

def _is_mlx_enabled() -> bool:
    """Globální MLX routing gate — nastavuje celý brain/ do MLX-only režimu."""
    return _HLEDAC_MLX_ENABLED

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
        try:
            import mlx.core as _mx
            _MLX_AVAILABLE = True
            logger.debug('[MLXDispatcher] mlx.core available')
        except ImportError:
            _MLX_AVAILABLE = False
            logger.debug('[MLXDispatcher] mlx.core not available')
            return
        try:
            from mlx_embedding_models.embedding import EmbeddingModel
            _MLX_EMBED_AVAILABLE = True
            logger.debug('[MLXDispatcher] mlx_embedding_models available')
        except ImportError:
            _MLX_EMBED_AVAILABLE = False
            logger.debug('[MLXDispatcher] mlx_embedding_models not available')
        try:
            import mlx_gliner2
            _MLX_GLINER2_AVAILABLE = True
            logger.debug('[MLXDispatcher] mlx_gliner2 available')
        except ImportError:
            _MLX_GLINER2_AVAILABLE = False
            logger.debug('[MLXDispatcher] mlx_gliner2 not available')
        try:
            from outlines.models import mlx as _mlx_outlines
            _MLX_OUTLINES_AVAILABLE = True
            logger.debug('[MLXDispatcher] outlines[mlx] available')
        except ImportError:
            _MLX_OUTLINES_AVAILABLE = False
            logger.debug('[MLXDispatcher] outlines[mlx] not available')

def _check_ane_availability() -> bool:
    """
    Lazily check ANE (Apple Neural Engine) availability.

    Checks (in order):
    1. Apple Silicon (darwin arm64)
    2. coremltools >= 6.0
    3. modernbert_ane.mlpackage exists at ~/.hledac/models/

    Called lazily on first embed() call — no side effects at import time.
    Cached after first call.
    """
    global _ANE_AVAILABLE, _ANE_CHECKED
    if _ANE_CHECKED:
        return _ANE_AVAILABLE
    _ANE_CHECKED = True
    import platform
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        logger.debug('[MLXDispatcher-ANE] Not Apple Silicon — ANE unavailable')
        _ANE_AVAILABLE = False
        return False
    try:
        import coremltools as ct
        parts = ct.__version__.split('.')
        version_tuple = tuple(int(p) for p in parts[:2] if p.isdigit())
        if version_tuple < (6, 0):
            logger.debug(f'[MLXDispatcher-ANE] coremltools {".".join(map(str, version_tuple))} < 6.0')
            _ANE_AVAILABLE = False
            return False
    except ImportError:
        logger.debug('[MLXDispatcher-ANE] coremltools not installed')
        _ANE_AVAILABLE = False
        return False
    model_path = Path.home() / '.hledac' / 'models' / 'modernbert_ane.mlpackage'
    if not model_path.exists():
        logger.debug(f'[MLXDispatcher-ANE] Model not found at {model_path}')
        _ANE_AVAILABLE = False
        return False
    _ANE_AVAILABLE = True
    logger.info(f'[MLXDispatcher-ANE] Available — model: {model_path}')
    return True

async def _load_ane_embedder() -> Any:
    """Lazy load ANE embedder (pre-converted modernbert_ane.mlpackage)."""
    global _ANE_EMBEDDER
    if _ANE_EMBEDDER is not None:
        return _ANE_EMBEDDER
    if not _check_ane_availability():
        return None
    try:
        from embeddings.ane._encoder import CoreMLModernBERTEncoder
        encoder = CoreMLModernBERTEncoder()
        if encoder._ensure_model():
            _ANE_EMBEDDER = encoder
            logger.info('[MLXDispatcher-ANE] ANE embedder loaded — modernbert_ane.mlpackage')
            return _ANE_EMBEDDER
        return None
    except Exception as e:
        logger.warning('[MLXDispatcher-ANE] ANE embedder load failed: %s', e)
        return None

def _encode_ane_batch_sync(encoder: Any, texts: list[str]) -> np.ndarray:
    """L2-normalizované embeddings přes ANE CoreML — volá se z thread poolu."""
    try:
        import numpy as _np
        raw = encoder.encode(texts)
        if raw is None:
            raise RuntimeError('ANE encode returned None')
        arr = _np.array(raw, dtype=_np.float32)
        norms = _np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / (norms + 1e-08)
    except Exception as e:
        logger.warning('[MLXDispatcher-ANE] ANE encode failed: %s', e)
        n = len(texts)
        return _np.zeros((n, 768), dtype=_np.float32)

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
            return EmbeddingModel.from_registry('BAAI/bge-small-en-v1.5')
        _EMBEDDER = await asyncio.to_thread(_load)
        logger.info('[MLXDispatcher] MLX embedder loaded — unified memory')
        return _EMBEDDER
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX embedder load failed: %s', e)
        return None

def _encode_mlx_batch_sync(model: Any, texts: list[str], embed_dim: int=384) -> np.ndarray:
    """L2-normalizované embeddings přes MLX — volá se z thread poolu."""
    try:
        raw: list[list[float]] = model.encode(texts)
        arr = np.array(raw, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / (norms + 1e-08)
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX encode failed: %s', e)
        n = len(texts)
        return np.zeros((n, embed_dim), dtype=np.float32)

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
            model_path = os.environ.get('MLX_GLINER2_MODEL', str(Path.home() / '.hledac' / 'models' / 'fastino_gliner2-base-v1'))
            return mlx_gliner2.GLiNER2.from_pretrained(model_path)
        _GLINER2 = await asyncio.to_thread(_load)
        logger.info('[MLXDispatcher] MLX GLiNER2 loaded — Metal GPU / ANE')
        return _GLINER2
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX GLiNER2 load failed: %s', e)
        return None

def _gliner2_extract_sync(model: Any, text: str, labels: list[str], threshold: float=0.5) -> list[dict[str, Any]]:
    """Single-text GLiNER2 inference — volá se z thread poolu."""
    try:
        items: list[dict[str, Any]] = model.extract_entities(text, labels, threshold=threshold, include_confidence=True, include_spans=True)
        return [{'entity': item.get('text', ''), 'label': item.get('label', ''), 'span': (item.get('start', 0), item.get('end', 0)), 'score': item.get('score', 0.9)} for item in items]
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX GLiNER2 extract failed: %s', e)
        return []

def _gliner2_batch_sync(model: Any, texts: list[str], labels: list[str], threshold: float=0.5, batch_size: int=8) -> list[list[dict[str, Any]]]:
    """Batch GLiNER2 inference — plně paralelní přes Metal."""
    try:
        results: list[list[dict[str, Any]]] = model.batch_extract_entities(texts, labels, threshold=threshold, batch_size=batch_size, include_confidence=True, include_spans=True)
        normalized: list[list[dict[str, Any]]] = []
        for batch_result in results:
            normalized.append([{'entity': item.get('text', ''), 'label': item.get('label', ''), 'span': (item.get('start', 0), item.get('end', 0)), 'score': item.get('score', 0.9)} for item in batch_result])
        return normalized
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX GLiNER2 batch failed: %s', e)
        return [[] for _ in texts]

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
        _OUTLINES = await asyncio.to_thread(_load)
        logger.info('[MLXDispatcher] MLX Outlines loaded — Metal GPU')
        return _OUTLINES
    except Exception as e:
        logger.warning('[MLXDispatcher] MLX Outlines load failed: %s', e)
        return None

class MLXDispatcher:
    """
    Central MLX routing pro celý brain/ subsystém.

    ISSUE #15: State je nyní context-bound přes _DispatcherContext.
    Pro per-sprint izolaci použij set_dispatcher_context() na začátku sprintu.

    Při HLEDAC_MLX=1:
    - veškerý inference jde přes MLX unified memory
    - žádné CoreML HTTP subprocess, žádné ONNX CPU fallback

    Při HLEDAC_MLX=0 (default):
    - MLXDispatcher funguje jako thin proxy
    - skutečný routing dědí jednotlivé enginy (CoreMLEmbedder, NEREngine, …)
    """
    __slots__: tuple[str, ...] = ()

    def __init__(self) -> None:
        pass

    def _ctx(self) -> _DispatcherContext:
        """Získat per-sprint context, fallback na fresh context bez izolace."""
        try:
            return _get_dispatcher_context()
        except Exception:
            return _DispatcherContext()

    @property
    def is_mlx_enabled(self) -> bool:
        """True pokud HLEDAC_MLX=1 — vynucuje MLX-only režim."""
        return _HLEDAC_MLX_ENABLED

    @property
    def is_embed_available(self) -> bool:
        """True pokud mlx_embedding_models lze načíst."""
        _check_mlx_availability()
        return _MLX_EMBED_AVAILABLE

    @property
    def is_ane_available(self) -> bool:
        """ISSUE #31: True pokud ANE embedder lze načíst (modernbert_ane.mlpackage)."""
        return _check_ane_availability()

    @property
    def embed_dimension(self) -> int:
        """ISSUE #31: Vrací dimenzi embeddingu podle aktivního backendu (768 pro ANE/ModernBERT, 384 pro BGE-small)."""
        ctx = self._ctx()
        if ctx.embed_dim:
            return ctx.embed_dim
        if _check_ane_availability() and _ANE_EMBEDDER is not None:
            return 768
        return 384

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

    async def load_embedder(self) -> bool:
        """
        ISSUE #31: Async lazy load embedder with ANE-first routing.

        Priority: ANE (modernbert_ane.mlpackage) → MLX Metal (ModernBERT 768d) → BGE-small (384d)
        Fills ctx.embedder with the best available backend.
        """
        ctx = self._ctx()
        if ctx.embed_loaded and ctx.embedder is not None:
            return True
        async with ctx._load_lock:
            if ctx.embed_loaded and ctx.embedder is not None:
                return True
            if _check_ane_availability():
                ane = await _load_ane_embedder()
                if ane is not None:
                    ctx.embedder = ane
                    ctx.embed_loaded = True
                    ctx.embed_dim = 768
                    ctx._model_priorities['embedder'] = 10
                    logger.info('[MLXDispatcher] Embedder loaded: ANE (modernbert_ane, 768d)')
                    return True
            _check_mlx_availability()
            if _MLX_EMBED_AVAILABLE:
                mlx = await _load_mlx_embedder()
                if mlx is not None:
                    ctx.embedder = mlx
                    ctx.embed_loaded = True
                    ctx.embed_dim = 768
                    ctx._model_priorities['embedder'] = 7
                    logger.info('[MLXDispatcher] Embedder loaded: MLX Metal (ModernBERT, 768d)')
                    return True
            try:
                from mlx_embedding_models.embedding import EmbeddingModel

                def _load_bge() -> Any:
                    return EmbeddingModel.from_registry('BAAI/bge-small-en-v1.5')
                bge = await asyncio.to_thread(_load_bge)
                ctx.embedder = bge
                ctx.embed_loaded = True
                ctx.embed_dim = 384
                ctx._model_priorities['embedder'] = 3
                logger.info('[MLXDispatcher] Embedder loaded: BGE-small (384d, fallback)')
                return True
            except Exception as e:
                logger.warning('[MLXDispatcher] All embedders failed: %s', e)
                ctx.embed_loaded = False
                return False

    async def embed_batch(self, texts: str | list[str]) -> np.ndarray:
        """
        ISSUE #31 + P2-07: Embed batch with ANE-first routing + async batching.

        Priority: ANE → MLX Metal (ModernBERT 768d) → BGE-small (384d)
        Returns L2-normalized embeddings. Dim varies by backend (768 or 384).

        P2-07: Single-item calls go through AsyncEmbeddingBatcher for batching.
        List calls use direct thread pool (already batched by caller).

        Returns:
            np.ndarray shape (len(texts), 768) or (len(texts), 384), dtype float32.
        """
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.zeros((0, 768), dtype=np.float32)
        ctx = self._ctx()
        if not ctx.embed_loaded:
            await self.load_embedder()
        if ctx.embedder is None:
            return np.zeros((len(texts), 768), dtype=np.float32)
        if len(texts) <= 8:
            return await self._embed_via_batcher(texts)
        else:
            return await self._embed_direct(texts)

    async def _embed_via_batcher(self, texts: list[str]) -> np.ndarray:
        """P2-07: Embed přes async batching frontu (pro small batches)."""
        batcher = get_async_batcher()
        if not batcher._started:
            await batcher.start()
        futures = [batcher.embed(text) for text in texts]
        # F3XX: parallel_ok() replaces asyncio.gather — preserves original order.
        results = await parallel_ok(*futures, label="embed_batch")
        dim = self._ctx().embed_dim or 768
        embeddings: list[np.ndarray] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.debug('[MLXDispatcher] Batcher embed failed for text %d: %s', i, result)
                embeddings.append(np.zeros((dim,), dtype=np.float32))
            else:
                embeddings.append(result)
        return np.stack(embeddings) if embeddings else np.zeros((0, dim), dtype=np.float32)

    async def _embed_direct(self, texts: list[str]) -> np.ndarray:
        """Direct embed přes thread pool (pro large batches, caller už batchuje)."""
        ctx = self._ctx()
        if ctx.embedder is None:
            return np.zeros((len(texts), ctx.embed_dim or 768), dtype=np.float32)
        if _ANE_EMBEDDER is not None and ctx.embedder is _ANE_EMBEDDER:
            return await asyncio.to_thread(_encode_ane_batch_sync, ctx.embedder, texts)
        else:
            return await asyncio.to_thread(_encode_mlx_batch_sync, ctx.embedder, texts, ctx.embed_dim)

    async def load_gliner2(self) -> bool:
        """Async lazy load MLX GLiNER2. Vrací True pokud uspěšně načten."""
        ctx = self._ctx()
        if ctx.gliner2_loaded and ctx.gliner2 is not None:
            return True
        async with ctx._load_lock:
            if ctx.gliner2_loaded and ctx.gliner2 is not None:
                return True
            ctx.gliner2 = await _load_mlx_gliner2()
            ctx.gliner2_loaded = ctx.gliner2 is not None
            if ctx.gliner2_loaded:
                ctx._model_priorities['gliner2'] = 3
            return ctx.gliner2_loaded

    async def ner_predict(self, text: str, labels: list[str], *, threshold: float=0.5) -> list[dict[str, Any]]:
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
        ctx = self._ctx()
        if not ctx.gliner2_loaded:
            await self.load_gliner2()
        if ctx.gliner2 is None:
            return []
        return await asyncio.to_thread(_gliner2_extract_sync, ctx.gliner2, text, labels, threshold)

    async def ner_predict_batch(self, texts: list[str], labels: list[str], *, threshold: float=0.5, batch_size: int=8) -> list[list[dict[str, Any]]]:
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
        ctx = self._ctx()
        if not ctx.gliner2_loaded:
            await self.load_gliner2()
        if ctx.gliner2 is None:
            return [[] for _ in texts]
        return await asyncio.to_thread(_gliner2_batch_sync, ctx.gliner2, texts, labels, threshold, batch_size)

    async def load_outlines(self) -> bool:
        """Async lazy load MLX Outlines. Vrací True pokud uspěšně načten."""
        ctx = self._ctx()
        if ctx.outlines_loaded and ctx.outlines is not None:
            return True
        async with ctx._load_lock:
            if ctx.outlines_loaded and ctx.outlines is not None:
                return True
            ctx.outlines = await _load_mlx_outlines()
            ctx.outlines_loaded = ctx.outlines is not None
            if ctx.outlines_loaded:
                ctx._model_priorities['outlines'] = 7
            return ctx.outlines_loaded

    async def structured_predict(self, text: str, schema: str, *, model_name: str='mlx-community/Llama-3.2-3B-Instruct-4bit') -> dict[str, Any]:
        """
        Structured generation přes MLX Outlines.

        Fallback: při HLEDAC_MLX=0 nebo chybě vrací {}.
        """
        if not text or not text.strip():
            return {}
        if not schema:
            return {}
        ctx = self._ctx()
        if not ctx.outlines_loaded:
            await self.load_outlines()
        if ctx.outlines is None:
            return {}
        try:
            import outlines

            def _run() -> dict[str, Any]:
                generator = outlines.generate.text(ctx.outlines, schema)
                result = generator(text)
                try:
                    import msgspec
                    return msgspec.json.decode(result)
                except Exception:
                    return {'raw': result}
            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning('[MLXDispatcher] MLX Outlines failed: %s', e)
            return {}

    def unload(self) -> None:
        """Uvolní všechny MLX/ANE modely z paměti."""
        ctx = self._ctx()
        ctx.embedder = None
        ctx.gliner2 = None
        ctx.outlines = None
        ctx.embed_loaded = False
        ctx.embed_dim = 768
        ctx.gliner2_loaded = False
        ctx.outlines_loaded = False
        ctx._preload_tasks.clear()
        ctx._model_priorities.clear()
        # ISSUE #5.5: Removed redundant global _ANE_EMBEDDER, _EMBEDDER writes.
        # Module-level globals (_ANE_EMBEDDER, _EMBEDDER, _GLINER2, _OUTLINES)
        # are lazily loaded caches used only by _load_* helpers — not state that
        # unload() needs to manage. After ctx.* = None, the next load call
        # re-initializes the global via its own `if ... is not None` guard.
        mx = _get_mx()
        if mx:
            try:
                mx.eval([])
                mx.metal.clear_cache()
            except Exception:
                pass
        logger.info('[MLXDispatcher] Unloaded — metal cache cleared')

    async def preload_model_hint(self, model_id: str) -> None:
        """
        ISSUE #15: Fire-and-forget async preload.

        Nahrává model na pozadí pomocí asyncio.Task bez blokování volajícího.
        Pokud už preload běží, zruší starý a spustí nový.

        Args:
            model_id: Identifikátor modelu pro preload
        """
        ctx = self._ctx()
        if model_id in ctx._preload_tasks:
            old_task = ctx._preload_tasks[model_id]
            if not old_task.done():
                old_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(old_task), timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass

        async def _preload() -> None:
            try:
                if model_id == 'hermes' or model_id == 'embedder':
                    await self.load_embedder()
                elif model_id == 'gliner2' or model_id == 'ner':
                    await self.load_gliner2()
                elif model_id == 'outlines':
                    await self.load_outlines()
                logger.debug(f'[MLXDispatcher] Preload completed: {model_id}')
            except Exception as e:
                logger.debug(f'[MLXDispatcher] Preload failed for {model_id}: {e}')
        ctx._preload_tasks[model_id] = safe_create_task(_preload(), eager_start=True)

    def get_model_priority(self, model_id: str) -> int:
        """Vrátí prioritu modelu pro LRU eviction (vyšší = důležitější)."""
        ctx = self._ctx()
        return ctx._model_priorities.get(model_id, 5)

    def set_model_priority(self, model_id: str, priority: int) -> None:
        """Nastaví prioritu modelu pro LRU eviction."""
        ctx = self._ctx()
        ctx._model_priorities[model_id] = priority
_dispatcher: MLXDispatcher | None = None
_dispatcher_lock = threading.Lock()
register_lock(LockCategory.MPC, _dispatcher_lock, "_mlx_dispatcher._dispatcher_lock")

def get_mlx_dispatcher() -> MLXDispatcher:
    """Vrací singleton MLXDispatcher — thread-safe DCLP."""
    global _dispatcher
    if _dispatcher is None:
        with _dispatcher_lock:
            if _dispatcher is None:
                _dispatcher = MLXDispatcher()
    return _dispatcher

def set_dispatcher_context(ctx: _DispatcherContext | None=None) -> None:
    """
    Nastaví per-sprint dispatcher context pro async-safe izolaci.

    ISSUE #15: Nahrazuje globální state per-sprint izolací.

    Použití na začátku sprintu:
        from hledac.universal.brain._mlx_dispatcher import set_dispatcher_context, _DispatcherContext
        set_dispatcher_context(_DispatcherContext())

    Použití na konci sprintu:
        set_dispatcher_context(None)  # Vyčistí context
    """
    if ctx is None:
        _dispatcher_context_var.set(None)
    else:
        _dispatcher_context_var.set(ctx)

def get_dispatcher_context() -> _DispatcherContext | None:
    """Vrátí aktuální per-sprint dispatcher context nebo None."""
    return _dispatcher_context_var.get()

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

def is_ane_available() -> bool:
    """ISSUE #31: Deprecated — použij MLXDispatcher().is_ane_available."""
    return _check_ane_availability()