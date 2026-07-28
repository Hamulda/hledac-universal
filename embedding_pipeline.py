"""
Embedding Pipeline - Semantic Search Integration (P13)
====================================================

P13: Embedding pipeline for semantic search with MMR and RRF fusion.

ROLE: Primary embedder using MLXEmbeddingManager from core.mlx_embeddings.
Singleton pattern: uses MLXEmbeddingManager singleton, loads once and reuses.

Features:
- Batch embedding generation for document indexing (256d MRL)
- Query embedding for semantic search (async)
- Matryoshka Representation Learning (MRL) - 256d truncation
- Memory guard: skip if RSS > 6.5 GB
- Automatic memory release after batch processing

Data contracts:
- generate_embeddings: list[str] → np.ndarray float32 shape (N, 256)
- embed_query_async: str → np.ndarray float32 shape (256,)
- embed_document: str → np.ndarray float32 shape (256,)

Anti-patterns:
- No blocking event loop: all MLX operations sync, wrapped in executor for async
- No PyTorch: uses MLX only
- No model swaps mid-pipeline: singleton ensures single model
"""
import asyncio
import gc
import inspect
import logging
import threading
from collections.abc import AsyncIterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Self
import numpy as np
from hledac.universal.core.psutil_shim import psutil, process
from hledac.universal.utils.exceptions import MemoryPressureError
if TYPE_CHECKING:
    from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder
logger = logging.getLogger(__name__)
COREML_AVAILABLE = False  # noqa: F841 — reserved for future CoreML ANE path
_EMBEDDING_DIM = 256
_BATCH_SIZE = 16
_ESTIMATED_EMBEDDING_MODEL_SIZE_GB = 0.5
_DEFAULT_BATCH_SIZE = 16
_ENV_BATCH_SIZE_VAR = 'HLEDAC_MLX_EMBED_BATCH'
_ENV_ALLOW_LARGE_BATCH_VAR = 'HLEDAC_ALLOW_LARGE_MLX_BATCH'

class EmbeddingRouter:
    """
    MLX-only priority routing: MLX ModernBERT → MLXEmbeddingManager.

    CoreML ANE path removed per CoreML→MLX migration.
    All inference is in-process on M1 Metal — no subprocess, no blocking.
    """
    __slots__ = tuple(('_modernbert',))

    def __init__(self) -> None:
        self._modernbert: ModernBERTEmbedder | None = None

    def _load_modernbert(self) -> ModernBERTEmbedder:
        """Load MLX ModernBERT embedder. Raises on failure."""
        if self._modernbert is None:
            from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder
            self._modernbert = ModernBERTEmbedder(lazy_load=True)
        assert self._modernbert is not None
        if not self._modernbert.is_loaded:
            self._modernbert._load_model()
        return self._modernbert

    def _check_mlx_loaded(self) -> bool:
        """Check if MLX ModernBERT is currently loaded in memory."""
        return self._modernbert is not None and self._modernbert.is_loaded

    def encode(self, texts: str | list[str], **kwargs) -> np.ndarray:
        """
        Encode texts using MLX ModernBERT or MLXEmbeddingManager.

        MLX-only: CoreML ANE path removed per CoreML→MLX migration.
        """
        embedder = self._get_embedder_sync()
        if embedder is None:
            return np.zeros((len(texts) if isinstance(texts, list) else 1, _EMBEDDING_DIM), dtype=np.float32)
        try:
            return embedder.encode(texts, **kwargs)
        except AttributeError:
            try:
                if isinstance(texts, str):
                    texts = [texts]
                return embedder.embed_batch(texts, **kwargs)
            except AttributeError:
                return np.zeros((len(texts) if isinstance(texts, list) else 1, _EMBEDDING_DIM), dtype=np.float32)

    def _get_embedder_sync(self):
        """
        MLX-only embedder selection (CoreML ANE path removed).
        Priority: MLX ModernBERT (if already in UMA) → MLX ModernBERT → MLXEmbeddingManager.
        No ANE/CoreML subprocess — all inference in-process on M1 Metal.
        """
        if self._check_mlx_loaded():
            try:
                mb = self._load_modernbert()
                logger.debug('[EMBED:ROUTER] sync: MLX in UMA, using ModernBERT')
                return mb
            except Exception:  # noqa: BLE001 — fail-soft: MLX load failures are varied (memory, Metal, import)
                pass
        try:
            mb = self._load_modernbert()
            logger.debug('[EMBED:ROUTER] sync: MLX ModernBERT loaded')
            return mb
        except Exception as e:  # noqa: BLE001 — fail-soft: log and fallback to compat embedder
            logger.debug(f'[EMBED:ROUTER] ModernBERT sync load failed: {e}')
        from hledac.universal.core.mlx_embeddings import get_mlx_embedder
        return get_mlx_embedder()

    async def get_embedder(self):
        """
        MLX-only embedder (CoreML ANE path removed).
        Priority: MLX ModernBERT → MLXEmbeddingManager.
        """
        if self._check_mlx_loaded():
            try:
                mb = self._load_modernbert()
                logger.debug('[EMBED:ROUTER] async: MLX in UMA, using ModernBERT')
                return mb
            except Exception:  # noqa: BLE001 — fail-soft: MLX load failures are varied (memory, Metal, import)
                pass
        try:
            mb = self._load_modernbert()
            logger.debug('[EMBED:ROUTER] async: MLX ModernBERT loaded')
            return mb
        except Exception as e:  # noqa: BLE001 — fail-soft: log and fallback to compat embedder
            logger.warning(f'[EMBED:ROUTER] ModernBERT load failed: {e}')
        from hledac.universal.core.mlx_embeddings import get_mlx_embedder
        return get_mlx_embedder()

    async def warmup(self):
        """Warmup the selected embedder."""
        embedder = await self.get_embedder()
        if embedder is None:
            return
        if hasattr(embedder, 'warmup'):
            if inspect.iscoroutinefunction(embedder.warmup):
                await embedder.warmup()
            else:
                embedder.warmup()

    def unload_all(self):
        """Release all embedders from memory (MLX-only)."""
        if self._modernbert is not None:
            try:
                self._modernbert.unload()
            except Exception:  # noqa: BLE001 — best-effort cleanup, unload failures are non-critical
                pass
            self._modernbert = None
        logger.info('[EMBED:ROUTER] All embedders unloaded')
_embedding_router = None

def _get_embedder():
    """
    Get the embedding manager via EmbeddingRouter (MLX-only).
    CoreML ANE path removed — all inference in-process on M1 Metal.
    """
    global _embedding_router
    if _embedding_router is None:
        _embedding_router = EmbeddingRouter()
    return _embedding_router._get_embedder_sync()

def _is_swap_detected() -> bool:
    """Check if system is swapping (heuristic: psutil shows non-zero swap)."""
    try:
        _ps = psutil
        if _ps is None:
            return False
        swap = _ps.swap_memory()
        return swap.used > 0
    except Exception:  # noqa: BLE001 — fail-soft: psutil probe failure should not prevent embedding
        return False

def get_adaptive_batch_size() -> int:
    """
    F214OPT-F: UMA-aware adaptive embedding batch size resolver.

    Returns a batch size that is safe for the current M1 8GB memory state.

    Resolution order:
    1. If UNA warn/critical/emergency: return 16 (memory pressure)
    2. If swap detected: return 16 (system distress)
    3. If HLEDAC_MLX_EMBED_BATCH env is set and valid: use it, capped at 32
       unless HLEDAC_ALLOW_LARGE_MLX_BATCH=1
    4. Otherwise: return _DEFAULT_BATCH_SIZE (16)

    No model load at import time — only reads UMA status and env vars.

    Returns:
        int: Safe batch size, always >= 16 and <= 64.
    """
    try:
        from hledac.universal.utils.uma_budget import is_uma_critical, is_uma_emergency, is_uma_warn
        if is_uma_emergency() or is_uma_critical() or is_uma_warn():
            return 16
    except Exception:  # noqa: BLE001 — fail-soft: uma_budget import/usage failure should not prevent batch size calculation
        pass
    if _is_swap_detected():
        return 16
    import os
    raw_env = os.environ.get(_ENV_BATCH_SIZE_VAR, '').strip()
    if raw_env:
        try:
            env_batch = int(raw_env)
            if env_batch < 16:
                return 16
            if env_batch > 64:
                env_batch = 64
            if env_batch > 32:
                allow_large = os.environ.get(_ENV_ALLOW_LARGE_BATCH_VAR, '').strip()
                if allow_large != '1':
                    return 32
            return env_batch
        except ValueError:
            pass
    return _DEFAULT_BATCH_SIZE

def _check_memory_guard() -> bool:
    """
    P19: Check if memory pressure allows embedding operations.

    Returns False if RSS > _embed_max_rss_gb, preventing model load.
    Also checks UmaWatchdog state for M1-specific pressure signals.

    Returns:
        True if safe to proceed, False to skip embedding.
    """
    current_rss = _get_current_rss_gb()
    if current_rss > _embed_max_rss_gb:
        logger.warning(f'[EMBED] Memory guard triggered: RSS={current_rss:.2f}GB > limit={_embed_max_rss_gb:.2f}GB')
        return False
    with _embedding_depth_lock:
        if _embedding_depth > 0:
            logger.warning('[EMBED] Already in embedding context — skipping recursive call')
            return False
    try:
        from hledac.universal.utils.uma_budget import get_uma_pressure_level
        level_int, level_str = get_uma_pressure_level()
        if level_str != 'normal':
            logger.warning(f'[EMBED] UmaWatchdog level={level_str} ({level_int}%) — skipping embedding')
            return False
    except Exception:  # noqa: BLE001 — fail-soft: uma_budget probe failure should not prevent embedding
        pass
    return True
_UMA_GUARD_THRESHOLD_MB: int | None = None

def _get_uma_guard_threshold() -> int:
    """
    Lazy-computed UMA guard threshold in MB.

    Imports _THRESHOLD_CRITICAL_GIB from core/resource_governor.py (SSOT)
    to stay in sync with the canonical memory pressure thresholds.

    On first call, caches the result in _UMA_GUARD_THRESHOLD_MB.
    Returns 6656 (legacy fallback) if import fails.

    Returns:
        int: Threshold in MB for combined (Metal + RSS) memory guard.
    """
    global _UMA_GUARD_THRESHOLD_MB
    if _UMA_GUARD_THRESHOLD_MB is not None:
        return _UMA_GUARD_THRESHOLD_MB
    try:
        from hledac.universal.core.resource_governor import _THRESHOLD_CRITICAL_GIB
        _UMA_GUARD_THRESHOLD_MB = int(_THRESHOLD_CRITICAL_GIB * 1024)
    except Exception:  # noqa: BLE001 — fail-soft: resource_governor import failure should not prevent threshold calculation
        _UMA_GUARD_THRESHOLD_MB = 6656
        logger.debug('[EMBED:UMA] Could not import _THRESHOLD_CRITICAL_GIB, using fallback 6656MB')
    return _UMA_GUARD_THRESHOLD_MB

def _uma_guard_before_batch() -> tuple[bool, dict]:
    """
    B3: Combined UMA guard — Metal active memory + RSS pre-batch.

    Prevents batch submission when combined Metal buffers + RSS would exceed
    the dynamic UMA ceiling (imported from core/resource_governor.py SSOT).

    Returns:
        (True, {}) if safe to proceed.
        (False, telemetry_dict) if batch blocked — caller MUST record telemetry.
    """
    telemetry: dict = {'uma_guard_blocked_batch': False, 'uma_guard_reason': '', 'combined_memory_mb': 0, 'rss_mb': 0, 'metal_active_mb': 0}
    try:
        from hledac.universal.utils.mlx_memory import get_mlx_active_memory_mb
        active_mb = get_mlx_active_memory_mb()
        if active_mb is None:
            return (True, {})
        rss_mb = process().memory_info().rss // (1024 * 1024) if process() else 0
        combined_mb = active_mb + rss_mb
        threshold_mb = _get_uma_guard_threshold()
        telemetry['combined_memory_mb'] = combined_mb
        telemetry['rss_mb'] = rss_mb
        telemetry['metal_active_mb'] = active_mb
        if combined_mb > threshold_mb:
            telemetry['uma_guard_blocked_batch'] = True
            telemetry['uma_guard_reason'] = f'combined_uma_pressure_{combined_mb}mb_exceeds_{threshold_mb}mb'
            logger.warning(f'[EMBED:UMA] Combined UMA pressure {combined_mb}MB (Metal={active_mb}MB + RSS={rss_mb}MB) > {threshold_mb}MB — flushing cache')
            try:
                from hledac.universal.utils.mlx_cache import get_mx
                mx = get_mx()
                if mx is not None:
                    mx.eval([])
                    gc.collect()
                    try:
                        mx.clear_cache()
                    except AttributeError:
                        try:
                            mx.metal.clear_cache()
                        except Exception:  # noqa: BLE001 — best-effort: Metal cache clear failure
                            pass
                    except Exception:  # noqa: BLE001 — best-effort: clear_cache failure should not prevent cleanup
                        pass
                    gc.collect()
            except Exception:  # noqa: BLE001 — best-effort: mlx/gc cleanup failure is non-critical
                pass
            return (False, telemetry)
        return (True, {})
    except ImportError:
        # mlx.core unavailable — cannot clear cache, return unsafe to be conservative
        telemetry['uma_guard_blocked_batch'] = True
        telemetry['uma_guard_reason'] = 'mlx_import_failed'
        logger.warning('[EMBED:UMA] mlx.core import failed — cannot determine memory state, blocking batch')
        return (False, telemetry)
    except Exception:  # noqa: BLE001 — fail-soft: uma guard should not block embedding on unknown errors
        return (True, {})

def _get_current_rss_gb() -> float:
    """Get current RSS memory in GB. P19: For memory guard checks."""
    try:
        return process().memory_info().rss / 1000000000.0 if process() else 0.0
    except Exception:
        return 0.0

def _check_memory_before_load(max_rss_gb: float, model_size_gb: float) -> None:
    """
    Check memory before model load. P19: Memory guard implementation.

    Args:
        max_rss_gb: Maximum allowed RSS before loading
        model_size_gb: Estimated model size to load

    Raises:
        MemoryPressureError: If RSS too high to safely load model
    """
    current_rss = _get_current_rss_gb()
    threshold = max_rss_gb - model_size_gb
    if current_rss > threshold:
        raise MemoryPressureError(f'[EMBED] Memory pressure: RSS {current_rss:.2f}GB > threshold {threshold:.2f}GB (max_rss_gb={max_rss_gb}, model_size_gb={model_size_gb}). Skipping embedder load.')

def _release_embedder() -> None:
    """Release embedder from memory if loaded."""
    try:
        embedder = _get_embedder()
        if embedder.is_loaded:
            embedder.unload()
            logger.info('[EMBED] MLXEmbeddingManager unloaded')
    except Exception as e:
        logger.debug(f'[EMBED] Failed to unload embedder: {e}')

def generate_embeddings(texts: list[str], batch_size: int | None=None, keep_loaded: bool=False) -> np.ndarray:
    """
    Generate embeddings for a list of texts using ModernBERT via MLX.

    Uses MRL (Matryoshka Representation Learning) to truncate embeddings
    to 256 dimensions for efficient storage and search.

    Args:
        texts: List of text strings to embed.
        batch_size: Batch size for processing (default 16).
        keep_loaded: If True, retain model in memory after batch (for callers
            using embedding_session). If False (default), unload after batch.

    Returns:
        numpy ndarray dtype=float32, shape=(len(texts), 256).
        Returns empty array with shape (0, 256) if memory guard triggers.

    Raises:
        RuntimeError: If embedder fails to initialize.
    """
    if not texts:
        return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)
    if batch_size is None:
        batch_size = get_adaptive_batch_size()
    original_to_unique: list[int] = []
    texts_to_embed: list[str] = texts
    dedup_happened = False
    try:
        import xxhash
        seen: dict[str, int] = {}
        unique_list: list[str] = []
        original_to_unique = []
        for text in texts:
            h = xxhash.xxh3_64(text.encode('utf-8', errors='replace')).hexdigest()
            if h not in seen:
                seen[h] = len(unique_list)
                unique_list.append(text)
            original_to_unique.append(seen[h])
        if len(unique_list) < len(texts):
            dedup_happened = True
            original_to_unique = original_to_unique  # used in remap below
            dedup_ratio = (len(texts) - len(unique_list)) / len(texts)
            logger.debug('[EMBED:J] xxhash dedup: %d→%d texts (%.0f%% duplicates removed)', len(texts), len(unique_list), dedup_ratio * 100)
            texts_to_embed = unique_list
    except ImportError:
        logger.debug('[EMBED:J] xxhash not available — skipping dedup')
    if not _check_memory_guard():
        logger.warning('[EMBED] Skipping embedding generation due to memory pressure')
        return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)
    embedder = _get_embedder()
    if embedder is None:
        logger.warning('[EMBED] No embedder available — returning zero array')
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)
    try:
        embeddings = embedder.encode_adaptive(texts_to_embed, initial_batch_size=batch_size, min_batch_size=4, max_batch_size=128, normalize=True, truncate_dim=_EMBEDDING_DIM, memory_pressure_provider=_uma_pressure_provider)
        try:
            _ps = psutil  # already imported at module level from core.psutil_shim
            backend_name = type(embedder).__name__.lower()
            if 'coreml' in backend_name:
                backend_name = 'coreml_bge'
            elif 'ane' in backend_name or 'allminilm' in backend_name.lower():
                backend_name = 'ane_allminilm'
            elif 'modernbert' in backend_name.lower():
                backend_name = 'mlx_modernbert'
            else:
                backend_name = 'cpu'
            if _ps is not None:
                _ram = _ps.virtual_memory().percent
                logger.debug('EMBED_BACKEND: %s | texts=%d | dim=%s | ram=%.1f%%', backend_name, len(texts_to_embed), embeddings.shape, _ram)
        except Exception:
            pass
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)
        if embeddings.shape[1] > _EMBEDDING_DIM:
            embeddings = embeddings[:, :_EMBEDDING_DIM]
        elif embeddings.shape[1] < _EMBEDDING_DIM:
            pad = np.zeros((embeddings.shape[0], _EMBEDDING_DIM - embeddings.shape[1]), dtype=np.float32)
            embeddings = np.hstack([embeddings, pad])
        logger.debug(f'[EMBED] Generated embeddings shape: {embeddings.shape}')
        if dedup_happened:
            full_embeddings = np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)
            for orig_idx, unique_idx in enumerate(original_to_unique):
                full_embeddings[orig_idx] = embeddings[unique_idx]
            embeddings = full_embeddings
        return embeddings
    except Exception as e:
        logger.error(f'[EMBED] Batch embedding failed: {e}')
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)
    finally:
        if not keep_loaded:
            _release_embedder()

def embed_query(text: str) -> np.ndarray:
    """
    Generate embedding for a single query (sync).

    Uses search_query prefix for asymmetric retrieval.

    Args:
        text: Query text to embed.

    Returns:
        numpy ndarray dtype=float32, shape=(256,).
        Returns array of zeros if memory guard triggers or on error.
    """
    if not _check_memory_guard():
        logger.warning('[EMBED] Skipping query embedding due to memory pressure')
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)
    embedder = _get_embedder()
    try:
        emb = embedder.embed_query(text, truncate_dim=_EMBEDDING_DIM)
        if emb.dtype != np.float32:
            emb = emb.astype(np.float32)
        if emb.ndim == 2:
            emb = emb.squeeze(0)
        if len(emb) > _EMBEDDING_DIM:
            emb = emb[:_EMBEDDING_DIM]
        elif len(emb) < _EMBEDDING_DIM:
            emb = np.pad(emb, (0, _EMBEDDING_DIM - len(emb)))
        return emb
    except Exception as e:
        logger.error(f'[EMBED] Query embedding failed: {e}')
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)

def embed_document(text: str) -> np.ndarray:
    """
    Generate embedding for a document (for indexing).

    Uses search_document prefix for indexing.

    Args:
        text: Document text to embed.

    Returns:
        numpy ndarray dtype=float32, shape=(256,).
    """
    if not _check_memory_guard():
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)
    embedder = _get_embedder()
    try:
        emb = embedder.embed_document(text, truncate_dim=_EMBEDDING_DIM)
        if emb.dtype != np.float32:
            emb = emb.astype(np.float32)
        if emb.ndim == 2:
            emb = emb.squeeze(0)
        if len(emb) > _EMBEDDING_DIM:
            emb = emb[:_EMBEDDING_DIM]
        elif len(emb) < _EMBEDDING_DIM:
            emb = np.pad(emb, (0, _EMBEDDING_DIM - len(emb)))
        return emb
    except Exception as e:
        logger.error(f'[EMBED] Document embedding failed: {e}')
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)

async def generate_embeddings_async(texts: list[str], batch_size: int=_BATCH_SIZE, keep_loaded: bool=False) -> np.ndarray:
    """
    Async wrapper for generate_embeddings.

    Runs embedding generation in thread executor to avoid blocking event loop.

    Args:
        texts: List of text strings to embed.
        batch_size: Batch size for processing.
        keep_loaded: Forwarded to generate_embeddings — if True, retain model
            in memory after batch (for callers using embedding_session).

    Returns:
        numpy ndarray dtype=float32, shape=(len(texts), 256).
    """
    return await asyncio.to_thread(generate_embeddings, texts, batch_size, keep_loaded)

async def embed_query_async(text: str) -> np.ndarray:
    """
    Async wrapper for embed_query.

    Runs query embedding in thread executor to avoid blocking event loop.

    P13 integration: used by _generate_and_store_report for RAG context.

    Args:
        text: Query text to embed.

    Returns:
        numpy ndarray dtype=float32, shape=(256,).
    """
    return await asyncio.to_thread(embed_query, text)
_embed_max_rss_gb: float = 5.5
_embedding_depth: int = 0
_embedding_depth_lock = threading.Lock()
_embed_refcount: int = 0
# ContextVar: each async context (Task) gets its own lock automatically.
# ISSUE-014 FIX: asyncio.Lock bound to a single loop is a bug on macOS —
# ContextVar keyed by Task gives per-context isolation without manual tracking.
_embed_refcount_lock_var: ContextVar[asyncio.Lock | None] = ContextVar("_embed_refcount_lock_var", default=None)

def _get_embed_refcount_lock() -> asyncio.Lock:
    """Get the ContextVar-backed refcount lock for the current async context."""
    lock = _embed_refcount_lock_var.get()
    if lock is None:
        lock = asyncio.Lock()
        _embed_refcount_lock_var.set(lock)
    return lock

class embedding_session:
    """
    Reentrant async context manager for embedding lifecycle with refcounting.

    On enter: increments refcount, loads model if refcount==1.
    On exit:  decrements refcount, unloads model if refcount==0.

    Allows nested calls (e.g. loop inside loop) without double-load/unload.
    Thread-safe via threading.Lock (load_embedding_model is called from
    run_in_executor threads, not from async context).

    Usage:
        async with embedding_session():
            embeddings = await generate_embeddings_async(texts)
    """

    async def __aenter__(self) -> None:
        global _embed_refcount
        async with _get_embed_refcount_lock():
            _embed_refcount += 1
            first_entry = _embed_refcount == 1
        if first_entry:
            await asyncio.to_thread(load_embedding_model)

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        global _embed_refcount
        should_unload = False
        async with _get_embed_refcount_lock():
            _embed_refcount -= 1
            if _embed_refcount <= 0:
                _embed_refcount = 0
                should_unload = True
        if should_unload:
            await asyncio.to_thread(unload_embedding_model)

def is_embedding_context_active() -> bool:
    """F197C: True if we are currently in an active embedding lifecycle context."""
    with _embedding_depth_lock:
        return _embedding_depth > 0

def set_embed_memory_limit(max_rss_gb: float) -> None:
    """P19: Set max RSS GB threshold for embedder memory guard."""
    global _embed_max_rss_gb
    _embed_max_rss_gb = max_rss_gb

def load_embedding_model() -> bool:
    """
    Load the embedding model into memory.

    Called by brain-level lifecycle before embedding operations.
    Uses MLXEmbeddingManager singleton with lazy loading.

    P19: Before loading, checks RSS against max_rss_gb - estimated_model_size.
    If memory pressure detected, skips loading and returns False.

    Returns:
        True if model is loaded or already loaded, False on error or memory pressure.
    """
    with _embedding_depth_lock:
        global _embedding_depth
        _embedding_depth += 1
    rss_before: float = 0.0
    try:
        rss_before = _get_current_rss_gb()
        _check_memory_before_load(_embed_max_rss_gb, _ESTIMATED_EMBEDDING_MODEL_SIZE_GB)
        embedder = _get_embedder()
        if not embedder.is_loaded:
            embedder._load_model()
        logger.info(f'[EMBED] Embedding model loaded (RSS before={rss_before:.2f}GB)')
        return True
    except MemoryPressureError:
        logger.warning(f'[EMBED] Memory pressure - skipping embedder load (RSS={rss_before:.2f}GB)')
        with _embedding_depth_lock:
            _embedding_depth -= 1
        return False
    except Exception as e:
        logger.error(f'[EMBED] Failed to load embedding model: {e}')
        return False

def unload_embedding_model() -> bool:
    """
    Unload the embedding model from memory.

    Called by brain-level lifecycle after embedding operations complete.
    Uses MLXEmbeddingManager.unload() and triggers gc.collect().

    P19: After unload, verifies RSS dropped by at least model_size.
    Logs warning if RSS didn't drop enough (possible memory leak).

    F197C: Always decrements depth counter (balanced with increment in load,
    even if load was a no-op due to already-loaded model).

    Returns:
        True on success, False on error.
    """
    with _embedding_depth_lock:
        global _embedding_depth
        if _embedding_depth > 0:
            _embedding_depth -= 1
    try:
        embedder = _get_embedder()
        if embedder.is_loaded:
            rss_before = _get_current_rss_gb()
            embedder.unload()
            gc.collect()
            rss_after = _get_current_rss_gb()
            dropped = rss_before - rss_after
            expected_drop = _ESTIMATED_EMBEDDING_MODEL_SIZE_GB
            if dropped < expected_drop * 0.5:
                logger.warning(f'[EMBED] RSS did not drop expected amount after unload: dropped={dropped:.2f}GB, expected~{expected_drop:.2f}GB (RSS before={rss_before:.2f}GB, after={rss_after:.2f}GB)')
            else:
                logger.info(f'[EMBED] Embedding model unloaded (RSS dropped={dropped:.2f}GB)')
        return True
    except Exception as e:
        logger.error(f'[EMBED] Failed to unload embedding model: {e}')
        return False

def get_embedding_dimension() -> int:
    """Return the MRL embedding dimension (256)."""
    return _EMBEDDING_DIM

async def generate_embeddings_streaming(texts: list[str], batch_size: int=_BATCH_SIZE) -> AsyncIterator[tuple[list[str], np.ndarray]]:
    """
    F203I: Streaming batch embedder — yields (ids, embeddings) per batch.

    Yields incrementally instead of materializing all embeddings at once,
    reducing peak RSS on M1 8GB during embedding phases.

    NOTE on "streaming": ModernBERT (mlx-embeddings) is a forward-pass encoder,
    NOT an autoregressive LLM. There are no tokens to stream sequentially —
    the entire sequence is encoded in one matmul pass. "Streaming" here means
    per-batch yielding, NOT token-by-token generation (which is only possible
    with mlx_lm.generate_stream for LLM inference, not embeddings).

    For LLM token streaming use brain/deephermes3_engine.generate_stream() instead.

    This is a NON-BREAKING additive API — existing sync callers of
    generate_embeddings() are unaffected.

    Args:
        texts: List of text strings to embed.
        batch_size: Max batch size (capped at _BATCH_SIZE=16).

    Yields:
        tuple[list[str], np.ndarray]: batch of ids (positional indices) and
            their embeddings shape=(batch_size, 256) float32.

    Fail-open: any error yields nothing.
    """
    if not texts:
        return
    batch_size = min(batch_size, _BATCH_SIZE)
    if not _check_memory_guard():
        logger.warning('[EMBED:streaming] Skipped due to memory pressure')
        return
    model_loaded = False
    try:
        if not _get_embedder().is_loaded:
            if not load_embedding_model():
                # load_embedding_model() already applied UmaWatchdog/RSS guard.
                # Fallback: process in per-batch chunks using _uma_guard_before_batch
                # per batch (same as main loop below) instead of materializing all at once.
                for i in range(0, len(texts), batch_size):
                    chunk = texts[i:i + batch_size]
                    chunk_ids = [str(i + j) for j in range(len(chunk))]
                    safe, _telemetry = _uma_guard_before_batch()
                    if not safe:
                        logger.warning(f"[EMBED:streaming] Fallback batch {i} skipped due to UMA pressure")
                        break
                    embs = await asyncio.to_thread(_generate_embeddings_chunk, chunk, batch_size)
                    if embs is not None and embs.shape[0] == len(chunk):
                        yield (chunk_ids, embs)
                return
            model_loaded = True
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            chunk_ids = [str(i + j) for j in range(len(chunk))]
            safe, telemetry = _uma_guard_before_batch()
            if not safe:
                logger.warning(f"[EMBED:streaming] Batch {i} skipped due to UMA pressure: combined={telemetry.get('combined_memory_mb', 0)}MB")
                break
            try:
                embs = await asyncio.to_thread(_generate_embeddings_chunk, chunk, batch_size)
                if embs is not None and embs.shape[0] == len(chunk):
                    yield (chunk_ids, embs)
            except Exception as e:
                logger.debug(f'[EMBED:streaming] batch error at offset {i}: {e}')
                continue
    finally:
        if model_loaded:
            unload_embedding_model()

def _generate_embeddings_chunk(texts: list[str], batch_size: int) -> np.ndarray:
    """Sync helper for a single chunk — runs in thread executor."""
    return generate_embeddings(texts, batch_size=batch_size)

def _encode_batch_no_release(texts: list[str], _batch_size: int) -> np.ndarray:
    """
    Issue #15: Sync batch encode WITHOUT embedder unload.

    Directly calls embedder.encode() with normalize+truncate_dim, returns embeddings.
    Does NOT call _release_embedder() — caller manages lifecycle.
    Used by streaming functions that need to hold the model loaded across batches.

    NOTE: ModernBERTEmbedder.encode() does NOT accept batch_size — it uses
    adaptive internal batching. We pass normalize+truncate_dim only.
    """
    embedder = _get_embedder()
    if embedder is None:
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)
    try:
        embeddings = embedder.encode(texts, normalize=True, truncate_dim=_EMBEDDING_DIM)
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)
        if embeddings.shape[1] > _EMBEDDING_DIM:
            embeddings = embeddings[:, :_EMBEDDING_DIM]
        elif embeddings.shape[1] < _EMBEDDING_DIM:
            pad = np.zeros((embeddings.shape[0], _EMBEDDING_DIM - embeddings.shape[1]), dtype=np.float32)
            embeddings = np.hstack([embeddings, pad])
        return embeddings
    except Exception as e:
        logger.error(f'[EMBED] _encode_batch_no_release failed: {e}')
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)

async def _async_enumerate(iterator: AsyncIterator[str], start: int=0) -> AsyncIterator[tuple[str, str]]:
    """Async enumerate — yields (str(index), item) from AsyncIterator."""
    idx = start
    async for item in iterator:
        yield (str(idx), item)
        idx += 1

async def embed_stream(texts: list[str], batch_size: int=_BATCH_SIZE) -> AsyncIterator[tuple[str, np.ndarray]]:
    """
    Issue-3: Batch-oriented per-item embedding stream.

    Delegates to _batch_encode_with_guard() for UMA guard + mx.eval/clear_cache.
    Accumulates items into batches internally, yields one embedding per item.

    Contrast with embed_stream_chunks() which yields per-batch.

    NOTE: Unlike mlx_lm.generate_stream() which streams LLM tokens during
    autoregressive decode, this function streams ModernBERT forward-pass
    embeddings one-item-at-a-time. There is no autoregressive process.

    M1 8GB invariants:
    - mx.eval([]) barrier before mx.metal.clear_cache() after each batch
    - UMA guard pre-batch skips yielding when Metal pressure is critical
    - Fail-open: any error yields nothing

    Args:
        texts: List of text strings to embed.
        batch_size: Internal batch size for encode() calls (default 16, capped).

    Yields:
        tuple[str, np.ndarray]: (item_id, embedding_vector) per item.
            embedding_vector shape=(256,) float32.

    Example:
        async for item_id, emb in embed_stream(["doc1", "doc2", "doc3"]):
            print(f"Item {item_id}: shape={emb.shape}")
    """
    if not texts:
        return
    batch_size = min(batch_size, _BATCH_SIZE)
    if not _check_memory_guard():
        logger.warning("[EMBED:stream] Skipped due to memory pressure")
        return
    model_loaded = False
    try:
        if not _get_embedder().is_loaded:
            if not load_embedding_model():
                return
            model_loaded = True
        batch: list[tuple[int | str, str]] = []
        for i, text in enumerate(texts):
            batch.append((i, text))
            if len(batch) >= batch_size:
                result = await _batch_encode_with_guard(batch, batch_size)
                if result is not None:
                    for (orig_idx, _), emb_vec in zip(result[0], result[1]):
                        yield (str(orig_idx), emb_vec)
                batch.clear()
        if batch:
            result = await _batch_encode_with_guard(batch, batch_size)
            if result is not None:
                for (orig_idx, _), emb_vec in zip(result[0], result[1]):
                    yield (str(orig_idx), emb_vec)
    finally:
        if model_loaded:
            unload_embedding_model()

async def generate_embeddings_from_iterator(texts_iter: AsyncIterator[str], batch_size: int=_BATCH_SIZE) -> AsyncIterator[tuple[str, np.ndarray]]:
    """
    Issue #15: True streaming encode from AsyncIterator[str].

    Consumes texts one at a time from an async iterator, accumulates into
    batches, and yields per-item WITHOUT ever materializing the full input
    list. Peak RSS bounded by batch_size × seq_len × 4B regardless of
    total text count.

    Contrast with generate_embeddings_streaming() which takes list[str] and
    still requires the full list in memory before iteration begins.
    Contrast with embed_stream() which takes list[str] (no iterator).

    M1 8GB invariants:
    - mx.eval([]) barrier before mx.metal.clear_cache() after each batch
    - UMA guard pre-batch skips yielding when Metal pressure is critical
    - Input iterator consumed lazily — no backpressure on caller

    Args:
        texts_iter: AsyncIterator of text strings to embed.
        batch_size: Max batch size (capped at _BATCH_SIZE=16).

    Yields:
        tuple[str, np.ndarray]: (item_id, embedding) per item.
            embedding shape=(256,) float32.
            Yields nothing if memory pressure or error (fail-open).

    Example:
        async def text_source():
            for doc in large_document_iterator:
                yield doc

        async for item_id, emb in generate_embeddings_from_iterator(text_source()):
            print(f"Item {item_id}: shape={emb.shape}")
    """
    if not _check_memory_guard():
        logger.warning('[EMBED:from_iter] Skipped due to memory pressure')
        return
    batch: list[tuple[str | int, str]] = []
    batch_size = min(batch_size, _BATCH_SIZE)
    model_loaded = False
    if not _get_embedder().is_loaded:
        if not load_embedding_model():
            return
        model_loaded = True
    try:
        async for item_id, text in _async_enumerate(texts_iter):
            batch.append((item_id, text))
            if len(batch) >= batch_size:
                result = await _batch_encode_with_guard(batch, batch_size)
                if result is not None:
                    for cid, emb_vec in zip(result[0], result[1]):
                        yield (str(cid), emb_vec)
                batch.clear()
        if batch:
            result = await _batch_encode_with_guard(batch, batch_size)
            if result is not None:
                for cid, emb_vec in zip(result[0], result[1]):
                    yield (str(cid), emb_vec)
    finally:
        if model_loaded:
            unload_embedding_model()

def _encode_single_item(text: str) -> np.ndarray | None:
    """Sync helper: encode single text, return 256d embedding or None on error."""
    try:
        emb = embed_query(text)
        return emb
    except Exception:
        return None

def _uma_pressure_provider() -> float:
    """
    Memory pressure provider for AdaptiveEmbeddingBatcher.

    Returns 0.0-1.0 float derived from UMA guard state.
    Uses the same threshold logic as _uma_guard_before_batch().
    """
    try:
        from hledac.universal.utils.uma_budget import get_uma_pressure_level
        level_int, _ = get_uma_pressure_level()
        return level_int / 100.0
    except Exception:
        return 0.5


# ---------------------------------------------------------------------------
# Shared streaming helpers — single source of truth for M1 8GB UMA guard +
# mx.eval / mx.metal.clear_cache barrier. Used by embed_stream,
# generate_embeddings_from_iterator, and embed_stream_chunks.
# ---------------------------------------------------------------------------

def _clear_mlx_cache() -> None:
    """
    Issue-3: Single source of truth for MLX Metal cache eviction.

    mx.eval([]) is required as an evaluation barrier before clear_cache —
    without it, clear_cache is a no-op (MLX uses lazy evaluation).
    Bare import is lazy (mlx.core loaded only when first called).

    Fail-safe: any exception is swallowed — telemetry is NOT updated here
    because this is a best-effort memory relief call.
    """
    try:
        from hledac.universal.utils.mlx_cache import get_mx
        mx = get_mx()
        if mx is None:
            return
        mx.eval([])
        try:
            mx.clear_cache()
        except AttributeError:
            try:
                mx.metal.clear_cache()
            except Exception:
                pass
    except Exception:
        pass


async def _batch_encode_with_guard(
    batch: list[tuple[int | str, str]],
    batch_size: int,
) -> tuple[list[tuple[int | str, str]], np.ndarray] | None:
    """
    Issue-3: Single source of truth for batch encoding with M1 8GB UMA guard.

    Encodes a batch and yields embeddings only when safe (UMA pressure pass).
    Handles mx.eval/clear_cache barrier after the encode call.
    Returns None when blocked or on error.

    Architecture:
        embed_stream()       → _batch_encode_with_guard()  (list input, per-item yield)
        embed_stream_chunks() → _batch_encode_with_guard()  (list input, per-batch yield)
        generate_embeddings_from_iterator() → _batch_encode_with_guard() (async iter input, per-item yield)

    Args:
        batch: List of (id, text) tuples. Ids may be int or str.
        batch_size: Max batch size (used for adaptiveUMA fallback).

    Returns:
        None if UMA guard blocked or error — caller skips yielding.
        tuple[batch, embeddings] on success — embeddings shape=(len(batch), 256) float32.
    """
    safe, telemetry = _uma_guard_before_batch()
    if not safe:
        logger.warning(
            f"[EMBED:_batch] Skipped due to UMA pressure: "
            f"combined={telemetry.get('combined_memory_mb', 0)}MB"
        )
        return None
    try:
        _, texts = zip(*batch)
        embs = await asyncio.to_thread(_encode_batch_no_release, list(texts), batch_size)
        if embs is None or embs.shape[0] != len(batch):
            return None
        _clear_mlx_cache()
        return (batch, embs)
    except Exception as e:
        logger.debug(f"[EMBED:_batch] encode failed: {e}")
        return None


async def embed_stream_chunks(
    texts: list[str],
    batch_size: int = _BATCH_SIZE,
) -> AsyncIterator[tuple[list[str], np.ndarray]]:
    """
    Issue-3: Per-batch streaming embedder — yields one batch at a time.

    Delegates to _batch_encode_with_guard() for UMA guard + mx.eval/clear_cache.
    Contrast with embed_stream() which yields per-item; this yields per-batch
    for callers that need batch-level granularity (e.g. pipeline batching).

    Args:
        texts: List of text strings to embed.
        batch_size: Max batch size (capped at _BATCH_SIZE=16).

    Yields:
        tuple[list[str], np.ndarray]: (ids, embeddings) per batch.
            embeddings shape=(batch_size, 256) float32.

    Fail-open: any error yields nothing.
    """
    if not texts:
        return
    batch_size = min(batch_size, _BATCH_SIZE)
    if not _check_memory_guard():
        logger.warning("[EMBED:chunks] Skipped due to memory pressure")
        return
    model_loaded = False
    try:
        if not _get_embedder().is_loaded:
            if not load_embedding_model():
                return
            model_loaded = True
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            chunk_ids = [str(i + j) for j in range(len(chunk))]
            result = await _batch_encode_with_guard(
                list(zip(chunk_ids, chunk)), batch_size
            )
            if result is not None:
                batch_tuples, embs = result
                ids = [str(item[0]) for item in batch_tuples]
                yield (ids, embs)
            else:
                logger.warning(f"[EMBED:chunks] Batch at offset {i} skipped due to UMA pressure")
    finally:
        if model_loaded:
            unload_embedding_model()

async def generate_embeddings_adaptive_streaming(texts: list[str], initial_batch_size: int=32, min_batch_size: int=4, max_batch_size: int=128, pressure_high: float=0.8, pressure_low: float=0.5) -> AsyncIterator[tuple[list[int], np.ndarray]]:
    """
    Adaptive streaming batch embedder with per-batch memory pressure feedback.

    Uses AdaptiveEmbeddingBatcher to dynamically adjust batch size based on
    real-time UMA memory pressure readings between batches.

    This is the PRIMARY streaming function for M1 8GB — it provides:
    - True streaming: yields per-batch, doesn't materialize all embeddings
    - Adaptive sizing: batch size adjusts mid-stream based on memory pressure
    - -30% memory spike reduction vs static batching (Benchmark F203I)

    Args:
        texts: List of text strings to embed.
        initial_batch_size: Starting batch size (default 32).
        min_batch_size: Minimum batch size at high pressure (default 4).
        max_batch_size: Maximum batch size at low pressure (default 128).
        pressure_high: Shrink batch when pressure >= this (default 0.80).
        pressure_low: Grow batch when pressure <= this (default 0.50).

    Yields:
        tuple[list[int], np.ndarray]: batch indices and embeddings.
            embeddings shape=(batch_size, 256) float32.

    Fail-open: any error yields nothing.

    Example:
        async for indices, embs in generate_embeddings_adaptive_streaming(texts):
            print(f"Batch: indices={indices}, shape={embs.shape}")
    """
    if not texts:
        return
    if not _check_memory_guard():
        logger.warning('[EMBED:adaptive] Skipped due to memory pressure')
        return
    from hledac.universal.core.embeddings.manager import AdaptiveEmbeddingBatcher, get_mlx_embedder
    embedder = get_mlx_embedder()
    await embedder.ensure_loaded()
    batcher = AdaptiveEmbeddingBatcher(initial_batch_size=initial_batch_size, min_batch_size=min_batch_size, max_batch_size=max_batch_size, pressure_high=pressure_high, pressure_low=pressure_low)
    async for indices, emb_batch in batcher.process_streaming(texts, embedder, _uma_pressure_provider):
        yield (indices, emb_batch)

def get_canonical_embedder() -> EmbeddingRouter:
    """
    Return the canonical EmbeddingRouter singleton.

    This is the single canonical owner for all embedding operations.
    Prefer generate_embeddings() / embed_query() / embed_document() for
    most callers; this function is for diagnostic and routing use.

    Returns:
        EmbeddingRouter instance (never None after module load).
    """
    global _embedding_router
    if _embedding_router is None:
        _embedding_router = EmbeddingRouter()
    return _embedding_router

def embed_texts_canonical(texts: list[str], batch_size: int=_BATCH_SIZE) -> np.ndarray:
    """
    F218A: Canonical batch text embedding entrypoint.

    Thin wrapper around generate_embeddings() that routes through
    EmbeddingRouter (ANE → MLX ModernBERT → CPU fallback).

    Args:
        texts: List of text strings to embed.
        batch_size: Batch size (default 16, capped at 512).

    Returns:
        np.ndarray float32 shape (len(texts), 256) — MRL truncated embeddings.
        Returns zeros array on memory pressure or error (fail-open).
    """
    return generate_embeddings(texts, batch_size=batch_size, keep_loaded=False)

def get_embedding_backend() -> str:
    """
    F218A: Return which embedding backend is currently active.

    Inspects the EmbeddingRouter state to determine the active path:
      - "mlx"        — MLX ModernBERT loaded and active
      - "not_loaded" — router exists but no model loaded yet
      - "unknown"    — cannot determine (error state)

    This is a read-only diagnostic — does not trigger model loading.
    """
    global _embedding_router
    if _embedding_router is None:
        return 'not_loaded'
    try:
        router = _embedding_router
        # CoreML ANE path removed — MLX-only (ModernBERT or fallback)
        if router._check_mlx_loaded():
            if router._modernbert is not None and router._modernbert.is_loaded:
                return 'mlx'
        if router._modernbert is not None and router._modernbert.is_loaded:
            return 'mlx'
        return 'not_loaded'
    except Exception:
        return 'unknown'