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

Architecture (Sprint Split-Brain):
- MLXDispatcher: Facade delegating to Loader + MemoryPolicy
- MLXMemoryPolicy: Priority management, unload, preload
- AsyncEmbeddingBatcher: Async batch queue for embeddings

Žádné top-level MLX importy — vše lazy, přes try/except ImportError.
M1 8GB: unified memory znamená žádný IPC marshalling overhead.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import threading
from pathlib import Path
from typing import Any

import numpy as np
from msgspec import field

from compat.msgspec_gc_compat import Struct
from hledac.universal._core.locks import LockCategory, register_lock
from hledac.universal.utils.asyncx import parallel_ok, safe_wait_for

logger = logging.getLogger(__name__)
PRIORITY_HIGH: int = 1
PRIORITY_LOW: int = 10

_MLX_AVAILABLE: bool | None = None
_MLX_EMBED_AVAILABLE: bool = False
_MLX_GLINER2_AVAILABLE: bool = False
_MLX_OUTLINES_AVAILABLE: bool = False
_ANE_AVAILABLE: bool | None = None
_HLEDAC_MLX_ENABLED: bool = False

# Lazy-loaded model singletons
_ANE_EMBEDDER: Any = None
_EMBEDDER: Any = None
_GLINER2: Any = None
_OUTLINES: Any = None


def _get_mx() -> Any | None:
    """Get MLX module if available."""
    try:
        import mlx.core as mx

        return mx
    except ImportError:
        return None


def _is_mlx_enabled() -> bool:
    """Check if HLEDAC_MLX=1."""
    return os.environ.get("HLEDAC_MLX", "0") == "1"


def _check_mlx_availability() -> None:
    """Check and cache MLX availability."""
    global _MLX_AVAILABLE, _MLX_EMBED_AVAILABLE, _MLX_GLINER2_AVAILABLE, _MLX_OUTLINES_AVAILABLE
    if _MLX_AVAILABLE is not None:
        return
    _HLEDAC_MLX_ENABLED = _is_mlx_enabled()
    _MLX_AVAILABLE = _HLEDAC_MLX_ENABLED
    if not _MLX_AVAILABLE:
        return
    try:
        import mlx.core as mx

        _MLX_AVAILABLE = True
    except ImportError:
        _MLX_AVAILABLE = False
        return
    try:
        import mlx_embedding_models

        _MLX_EMBED_AVAILABLE = True
    except ImportError:
        _MLX_EMBED_AVAILABLE = False
    try:
        import mlx_gliner2

        _MLX_GLINER2_AVAILABLE = True
    except ImportError:
        _MLX_GLINER2_AVAILABLE = False
    try:
        import outlines.models.mlx

        _MLX_OUTLINES_AVAILABLE = True
    except ImportError:
        _MLX_OUTLINES_AVAILABLE = False


def _check_ane_availability() -> bool:
    """Check if ANE embedder (modernbert_ane.mlpackage) is available."""
    global _ANE_AVAILABLE
    if _ANE_AVAILABLE is not None:
        return _ANE_AVAILABLE
    try:
        import coremltools

        parts = coremltools.__version__.split(".")
        version_tuple = tuple(int(p) for p in parts[:2] if p.isdigit())
        if version_tuple < (6, 0):
            _ANE_AVAILABLE = False
            return False
    except ImportError:
        _ANE_AVAILABLE = False
        return False
    model_path = Path.home() / ".hledac" / "models" / "modernbert_ane.mlpackage"
    if not model_path.exists():
        _ANE_AVAILABLE = False
        return False
    _ANE_AVAILABLE = True
    logger.info("[MLXDispatcher-ANE] Available — model: %s", model_path)
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
            logger.info("[MLXDispatcher-ANE] ANE embedder loaded")
            return _ANE_EMBEDDER
        return None
    except Exception as e:
        logger.warning("[MLXDispatcher-ANE] ANE embedder load failed: %s", e)
        return None


def _encode_ane_batch_sync(encoder: Any, texts: list[str]) -> np.ndarray:
    """L2-normalizované embeddings přes ANE CoreML — volá se z thread poolu."""
    try:
        import numpy as _np

        raw = encoder.encode(texts)
        if raw is None:
            raise RuntimeError("ANE encode returned None")
        arr = _np.array(raw, dtype=_np.float32)
        norms = _np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / (norms + 1e-08)
    except Exception as e:
        logger.warning("[MLXDispatcher-ANE] ANE encode failed: %s", e)
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
            return EmbeddingModel.from_registry("BAAI/bge-small-en-v1.5")

        _EMBEDDER = await asyncio.to_thread(_load)
        logger.info("[MLXDispatcher] MLX embedder loaded — unified memory")
        return _EMBEDDER
    except Exception as e:
        logger.warning("[MLXDispatcher] MLX embedder load failed: %s", e)
        return None


def _encode_mlx_batch_sync(model: Any, texts: list[str], embed_dim: int = 384) -> np.ndarray:
    """L2-normalizované embeddings přes MLX — volá se z thread poolu."""
    try:
        raw: list[list[float]] = model.encode(texts)
        arr = np.array(raw, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / (norms + 1e-08)
    except Exception as e:
        logger.warning("[MLXDispatcher] MLX encode failed: %s", e)
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
        import os

        import mlx_gliner2

        def _load() -> Any:
            model_path = os.environ.get(
                "MLX_GLINER2_MODEL", str(Path.home() / ".hledac" / "models" / "fastino_gliner2-base-v1")
            )
            return mlx_gliner2.GLiNER2.from_pretrained(model_path)

        _GLINER2 = await asyncio.to_thread(_load)
        logger.info("[MLXDispatcher] MLX GLiNER2 loaded — Metal GPU / ANE")
        return _GLINER2
    except Exception as e:
        logger.warning("[MLXDispatcher] MLX GLiNER2 load failed: %s", e)
        return None


def _gliner2_extract_sync(model: Any, text: str, labels: list[str], threshold: float = 0.5) -> list[dict[str, Any]]:
    """Single-text GLiNER2 inference — volá se z thread poolu."""
    try:
        items: list[dict[str, Any]] = model.extract_entities(
            text, labels, threshold=threshold, include_confidence=True, include_spans=True
        )
        return [
            {
                "entity": item.get("text", ""),
                "label": item.get("label", ""),
                "span": (item.get("start", 0), item.get("end", 0)),
                "score": item.get("score", 0.9),
            }
            for item in items
        ]
    except Exception as e:
        logger.warning("[MLXDispatcher] MLX GLiNER2 extract failed: %s", e)
        return []


def _gliner2_batch_sync(
    model: Any, texts: list[str], labels: list[str], threshold: float = 0.5, batch_size: int = 8
) -> list[list[dict[str, Any]]]:
    """Batch GLiNER2 inference — volá se z thread poolu."""
    results = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        batch_results = [_gliner2_extract_sync(model, text, labels, threshold) for text in batch]
        results.extend(batch_results)
    return results


async def _load_mlx_outlines() -> Any:
    """Lazy load MLX Outlines structured generation."""
    global _OUTLINES
    if _OUTLINES is not None:
        return _OUTLINES
    _check_mlx_availability()
    if not _MLX_OUTLINES_AVAILABLE:
        return None
    try:
        from outlines.models.mlx import mlx_outlines

        def _load() -> Any:
            return mlx_outlines("mlx-community/Llama-3.2-3B-Instruct-4bit")

        _OUTLINES = await asyncio.to_thread(_load)
        logger.info("[MLXDispatcher] MLX Outlines loaded — Metal GPU")
        return _OUTLINES
    except Exception as e:
        logger.warning("[MLXDispatcher] MLX Outlines load failed: %s", e)
        return None


class _EmbeddingRequest:
    """Jeden embedding request s Future pro distribuci výsledku."""

    __slots__ = ("priority", "text", "future")

    def __init__(self, priority: int, text: str) -> None:
        self.priority = priority
        self.text = text
        # ISSUE-11: name= param for better async diagnostics (Python 3.14+)
        self.future: asyncio.Future[np.ndarray] = asyncio.get_running_loop().create_future(
            name="mlx_dispatcher:embedding"
        )

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, _EmbeddingRequest):
            return NotImplemented
        return self.priority < other.priority


class _DispatcherContext(Struct):
    """
    ISSUE #15: Per-sprint context-bound state for MLX model instances.

    Each sprint gets its own context for async-safe isolation. Modely jsou
    drženy v tomto contextu, ne v globálních proměnných.
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


_dispatcher_context_var: contextvars.ContextVar[_DispatcherContext | None] = contextvars.ContextVar(
    "_dispatcher_context", default=None
)


def _get_dispatcher_context() -> _DispatcherContext:
    """Získat aktuální per-sprint context, fallback na fresh context."""
    ctx = _dispatcher_context_var.get()
    if ctx is None:
        ctx = _DispatcherContext()
        _dispatcher_context_var.set(ctx)
    return ctx


class AsyncEmbeddingBatcher:
    """
    P2-07: Async batching fronta pro MLX/ANE embedding.

    Sbírá embedding requesty z concurrent volání, batchuje je a distribuuje
    výsledky přes Futures.
    """

    DEFAULT_BATCH_SIZE: int = 32
    DEFAULT_MAX_WAIT_MS: int = 50
    MAX_QUEUE_SIZE: int = 1024
    __slots__ = ("_batch_size", "_loop_task", "_max_wait_s", "_model_lock", "_queue", "_started", "_stopping")

    def __init__(self, batch_size: int = DEFAULT_BATCH_SIZE, max_wait_ms: int = DEFAULT_MAX_WAIT_MS) -> None:
        self._batch_size = batch_size
        self._max_wait_s = max_wait_ms / 1000.0
        self._queue: asyncio.PriorityQueue[_EmbeddingRequest] = asyncio.PriorityQueue(maxsize=self.MAX_QUEUE_SIZE)
        self._loop_task: asyncio.Task | None = None
        self._started = False
        self._stopping = False
        self._model_lock = threading.Lock()

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._loop_task = asyncio.create_task(self._batch_loop())

    async def stop(self) -> None:
        if not self._started:
            return
        self._stopping = True
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
        self._started = False
        self._stopping = False

    async def embed(self, text: str, *, priority: int = PRIORITY_HIGH) -> np.ndarray:
        if not text:
            return np.zeros((768,), dtype=np.float32)
        if self._stopping or not self._started:
            return await self._direct_encode(text)
        request = _EmbeddingRequest(priority, text)
        try:
            async with asyncio.timeout(5.0):
                await self._queue.put(request)
        except TimeoutError:
            logger.warning("[AsyncBatcher] Queue full, falling back to direct encode")
            return await self._direct_encode(text)
        return await safe_wait_for(request.future, timeout=60.0)

    async def _direct_encode(self, text: str) -> np.ndarray:
        ctx = _get_dispatcher_context()
        if not ctx.embed_loaded:
            return np.zeros((768,), dtype=np.float32)
        try:
            return await asyncio.to_thread(_encode_mlx_batch_sync, ctx.embedder, [text], ctx.embed_dim)
        except Exception:
            return np.zeros((768,), dtype=np.float32)

    async def _batch_loop(self) -> None:
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
        if not batch:
            return
        texts = [req.text for req, _ in batch]
        ctx = _get_dispatcher_context()
        if ctx.embedder is None:
            for req, _ in batch:
                req.future.set_result(np.zeros((768,), dtype=np.float32))
            return
        try:
            if _check_ane_availability() and _ANE_EMBEDDER is not None:
                result = await asyncio.to_thread(_encode_ane_batch_sync, _ANE_EMBEDDER, texts)
            else:
                result = await asyncio.to_thread(_encode_mlx_batch_sync, ctx.embedder, texts, ctx.embed_dim)
            for (req, _idx), embedding in zip(batch, result, strict=False):
                req.future.set_result(embedding)
        except Exception as e:
            logger.warning("[AsyncBatcher] Batch encode failed: %s", e)
            for req, _ in batch:
                req.future.set_result(np.zeros((768,), dtype=np.float32))


_batcher: AsyncEmbeddingBatcher | None = None


def get_async_batcher() -> AsyncEmbeddingBatcher:
    """Get singleton AsyncEmbeddingBatcher."""
    global _batcher
    if _batcher is None:
        _batcher = AsyncEmbeddingBatcher()
    return _batcher


class MLXMemoryPolicy:
    """
    Memory management for MLX models — priorities, unload, preload.

    Sprint Split-Brain: Extracted from MLXDispatcher to isolate
    memory policy from inference routing. Enables independent testing
    and future policy swaps (LRU → ARC, WLRU, etc.).
    """

    __slots__ = ()

    def __init__(self) -> None:
        pass

    @staticmethod
    def get_model_priority(ctx: _DispatcherContext, model_id: str) -> int:
        """Get priority for a model (higher = more important)."""
        return ctx._model_priorities.get(model_id, 5)

    @staticmethod
    def set_model_priority(ctx: _DispatcherContext, model_id: str, priority: int) -> None:
        """Set priority for a model."""
        ctx._model_priorities[model_id] = priority

    @staticmethod
    def unload(ctx: _DispatcherContext) -> None:
        """Unload all MLX/ANE models and clear metal cache."""
        ctx.embedder = None
        ctx.gliner2 = None
        ctx.outlines = None
        ctx.embed_loaded = False
        ctx.embed_dim = 768
        ctx.gliner2_loaded = False
        ctx.outlines_loaded = False
        ctx._preload_tasks.clear()
        ctx._model_priorities.clear()
        mx = _get_mx()
        if mx:
            try:
                mx.eval([])
                mx.metal.clear_cache()
            except Exception:  # noqa: BLE001
                pass
        logger.info("[MLXMemoryPolicy] Unloaded — metal cache cleared")

    @staticmethod
    async def preload_model(ctx: _DispatcherContext, model_id: str, dispatcher: MLXDispatcher) -> None:
        """Fire-and-forget preload of a model."""
        if model_id in ctx._preload_tasks:
            old_task = ctx._preload_tasks[model_id]
            if not old_task.done():
                old_task.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(old_task), timeout=0.5)
                except TimeoutError, asyncio.CancelledError:  # noqa: BLE001
                    pass

        async def _preload() -> None:
            try:
                if model_id == "hermes" or model_id == "embedder":
                    await dispatcher.load_embedder()
                elif model_id == "gliner2" or model_id == "ner":
                    await dispatcher.load_gliner2()
                elif model_id == "outlines":
                    await dispatcher.load_outlines()
                logger.debug("[MLXMemoryPolicy] Preload completed: %s", model_id)
            except Exception as e:
                logger.debug("[MLXMemoryPolicy] Preload failed for %s: %s", model_id, e)

        from hledac.universal.utils.asyncx import safe_create_task

        ctx._preload_tasks[model_id] = safe_create_task(_preload(), eager_start=True)


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
        """ISSUE #31: True pokud ANE embedder lze načíst."""
        return _check_ane_availability()

    @property
    def embed_dimension(self) -> int:
        """ISSUE #31: Vrací dimenzi embeddingu podle aktivního backendu."""
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
        """ISSUE #31: Async lazy load embedder with ANE-first routing."""
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
                    ctx._model_priorities["embedder"] = 10
                    logger.info("[MLXDispatcher] Embedder loaded: ANE")
                    return True
            _check_mlx_availability()
            if _MLX_EMBED_AVAILABLE:
                mlx = await _load_mlx_embedder()
                if mlx is not None:
                    ctx.embedder = mlx
                    ctx.embed_loaded = True
                    ctx.embed_dim = 768
                    ctx._model_priorities["embedder"] = 7
                    logger.info("[MLXDispatcher] Embedder loaded: MLX Metal")
                    return True
            try:
                from mlx_embedding_models.embedding import EmbeddingModel

                def _load_bge() -> Any:
                    return EmbeddingModel.from_registry("BAAI/bge-small-en-v1.5")

                bge = await asyncio.to_thread(_load_bge)
                ctx.embedder = bge
                ctx.embed_loaded = True
                ctx.embed_dim = 384
                ctx._model_priorities["embedder"] = 3
                logger.info("[MLXDispatcher] Embedder loaded: BGE-small (fallback)")
                return True
            except Exception as e:
                logger.warning("[MLXDispatcher] All embedders failed: %s", e)
                ctx.embed_loaded = False
                return False

    async def embed_batch(self, texts: str | list[str]) -> np.ndarray:
        """ISSUE #31 + P2-07: Embed batch with ANE-first routing + async batching."""
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.zeros((1, 768), dtype=np.float32)
        batcher = get_async_batcher()
        if not batcher._started:
            await batcher.start()
        results = await parallel_ok(*[batcher.embed(t) for t in texts])
        return np.stack(results) if results else np.zeros((len(texts), 768), dtype=np.float32)

    async def load_gliner2(self) -> bool:
        """Lazy load MLX GLiNER2."""
        ctx = self._ctx()
        if ctx.gliner2_loaded and ctx.gliner2 is not None:
            return True
        async with ctx._load_lock:
            if ctx.gliner2_loaded and ctx.gliner2 is not None:
                return True
            gliner2 = await _load_mlx_gliner2()
            if gliner2 is not None:
                ctx.gliner2 = gliner2
                ctx.gliner2_loaded = True
                ctx._model_priorities["gliner2"] = 8
                return True
            return False

    async def ner_predict(self, text: str, labels: list[str]) -> list[dict[str, Any]]:
        """NER inference via MLX GLiNER2."""
        ctx = self._ctx()
        if not ctx.gliner2_loaded:
            await self.load_gliner2()
        if ctx.gliner2 is None:
            return []
        try:
            return await asyncio.to_thread(_gliner2_extract_sync, ctx.gliner2, text, labels)
        except Exception as e:
            logger.warning("[MLXDispatcher] NER predict failed: %s", e)
            return []

    async def load_outlines(self) -> bool:
        """Lazy load MLX Outlines."""
        ctx = self._ctx()
        if ctx.outlines_loaded and ctx.outlines is not None:
            return True
        async with ctx._load_lock:
            if ctx.outlines_loaded and ctx.outlines is not None:
                return True
            outlines = await _load_mlx_outlines()
            if outlines is not None:
                ctx.outlines = outlines
                ctx.outlines_loaded = True
                ctx._model_priorities["outlines"] = 5
                return True
            return False

    async def structured_generate(self, prompt: str, schema: dict) -> dict[str, Any]:
        """Structured generation via MLX Outlines."""
        ctx = self._ctx()
        if not ctx.outlines_loaded:
            await self.load_outlines()
        if ctx.outlines is None:
            return {}
        try:
            import outlines

            def _run() -> dict[str, Any]:
                return outlines.generate.json(ctx.outlines, prompt, schema)

            return await asyncio.to_thread(_run)
        except Exception as e:
            logger.warning("[MLXDispatcher] MLX Outlines failed: %s", e)
            return {}

    def unload(self) -> None:
        """Unload all MLX/ANE modely z paměti."""
        MLXMemoryPolicy.unload(self._ctx())

    async def preload_model_hint(self, model_id: str) -> None:
        """ISSUE #15: Fire-and-forget async preload."""
        await MLXMemoryPolicy.preload_model(self._ctx(), model_id, self)

    def get_model_priority(self, model_id: str) -> int:
        """Vrátí prioritu modelu pro LRU eviction."""
        return MLXMemoryPolicy.get_model_priority(self._ctx(), model_id)

    def set_model_priority(self, model_id: str, priority: int) -> None:
        """Nastaví prioritu modelu pro LRU eviction."""
        MLXMemoryPolicy.set_model_priority(self._ctx(), model_id, priority)


_dispatcher: MLXDispatcher | None = None


@register_lock(LockCategory.MPC)
def _dispatcher_lock() -> threading.Lock:
    """Module-level lock for MLXDispatcher singleton factory."""
    return threading.Lock()


def get_mlx_dispatcher() -> MLXDispatcher:
    """Vrací singleton MLXDispatcher — thread-safe DCLP."""
    global _dispatcher
    if _dispatcher is None:
        with _dispatcher_lock():
            if _dispatcher is None:
                _dispatcher = MLXDispatcher()
    return _dispatcher


def set_dispatcher_context(ctx: _DispatcherContext | None = None) -> None:
    """Nastaví per-sprint dispatcher context pro async-safe izolaci."""
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
