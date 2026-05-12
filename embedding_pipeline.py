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

from __future__ import annotations

import asyncio
import gc
import logging
from typing import AsyncIterator, Dict, List, Optional, TYPE_CHECKING, Union

import numpy as np
import psutil

from hledac.universal.utils.exceptions import MemoryPressureError

if TYPE_CHECKING:
    from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder

logger = logging.getLogger(__name__)

# MRL dimension (Matryoshka Representation Learning) - 256d output
_EMBEDDING_DIM = 256
_BATCH_SIZE = 16

# P19: Estimated embedding model size (ModernBERT ~500MB in 4bit)
_ESTIMATED_EMBEDDING_MODEL_SIZE_GB = 0.5

# F214OPT-F: Adaptive batch size default (never larger than this)
_DEFAULT_BATCH_SIZE = 16

# F214OPT-F: Env var names for batch override
_ENV_BATCH_SIZE_VAR = "HLEDAC_MLX_EMBED_BATCH"
_ENV_ALLOW_LARGE_BATCH_VAR = "HLEDAC_ALLOW_LARGE_MLX_BATCH"


class EmbeddingRouter:
    """
    Priority routing: ANE (CoreML) → MLX ModernBERT → CPU sentence-transformers.

    M1 8GB UMA constraint: ANE and MLX ModernBERT are never loaded simultaneously.
    ANE uses ~300MB CoreML model; MLX ModernBERT uses ~500MB.
    Loading both would exceed the 5.5GB working limit.

    Wiring: Replaces _get_embedder() singleton in embedding_pipeline.py.
    Lazy initialization — no models loaded at construction.
    """

    def __init__(self):
        self._ane = None
        self._modernbert: Optional["ModernBERTEmbedder"] = None
        self._ane_available = False
        self._initialized = False

    def _ensure_initialized(self):
        """Deferred import to avoid circular deps at module load."""
        if self._initialized:
            return
        self._initialized = True
        try:
            from hledac.universal.brain.ane_embedder import ANE_AVAILABLE
            self._ane_available = ANE_AVAILABLE
        except ImportError:
            self._ane_available = False

    async def _load_ane(self) -> bool:
        """Load ANE embedder. Returns True if ANE is ready."""
        self._ensure_initialized()
        if not self._ane_available:
            return False

        from hledac.universal.brain.ane_embedder import ANEEmbedder

        if self._ane is None:
            self._ane = ANEEmbedder()
        if not self._ane.is_loaded:
            await self._ane.load()
        return self._ane.is_loaded

    def _load_modernbert(self) -> "ModernBERTEmbedder":
        """Load MLX ModernBERT embedder. Raises on failure."""
        if self._modernbert is None:
            from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder
            self._modernbert = ModernBERTEmbedder(lazy_load=True)
        if not self._modernbert.is_loaded:
            self._modernbert._load_model()
        return self._modernbert

    def _check_mlx_loaded(self) -> bool:
        """Check if MLX ModernBERT is currently in memory via ModelManager."""
        try:
            from hledac.universal.brain.model_manager import ModelManager
            mm = ModelManager.instance()
            current = mm.get_current_model()
            # MLX model loaded means ANE would compete for UMA
            return current is not None and current != "ane"
        except Exception:
            return False

    def encode(self, texts: Union[str, List[str]], **kwargs) -> np.ndarray:
        """
        Encode texts using the selected embedder (ANE or ModernBERT).

        Implements the .encode() interface expected by generate_embeddings().
        Delegated to the underlying embedder's native method:
          - ANEEmbedder.embed()        (async)
          - ModernBERTEmbedder.embed_batch()  (sync)
          - MLXEmbeddingManager.encode()       (sync)

        Args:
            texts: Single text or list of texts.
            kwargs: Passed through to the underlying embedder.

        Returns:
            np.ndarray of shape (len(texts), 256) or (len(texts), 768).
        """
        # Use sync path — determine which embedder is active
        embedder = self._get_embedder_sync()
        if embedder is None:
            return np.zeros((len(texts) if isinstance(texts, list) else 1, _EMBEDDING_DIM), dtype=np.float32)

        # MLXEmbeddingManager already has .encode()
        if hasattr(embedder, 'encode'):
            return embedder.encode(texts, **kwargs)

        # ModernBERTEmbedder — use .embed_batch()
        if hasattr(embedder, 'embed_batch'):
            if isinstance(texts, str):
                texts = [texts]
            return embedder.embed_batch(texts, **kwargs)

        # ANEEmbedder — use .embed() (async)
        if hasattr(embedder, 'embed'):
            if isinstance(texts, str):
                texts = [texts]
            if asyncio.iscoroutinefunction(embedder.embed):
                # asyncio.run() creates its own loop — correct for executor thread
                return asyncio.run(embedder.embed(texts))
            else:
                return embedder.embed(texts)

        # Fallback
        return np.zeros((len(texts) if isinstance(texts, list) else 1, _EMBEDDING_DIM), dtype=np.float32)

    def _get_embedder_sync(self):
        """
        Internal sync embedder selection — returns the raw embedder instance.

        Priority: ANE (if already loaded) → MLX ModernBERT → MLXEmbeddingManager.
        Does NOT attempt to load ANE asynchronously — that happens via get_embedder() async.

        M1 memory rule: When MLX is in UMA, ANE doesn't add to Metal buffers (separate
        hardware), so if ANE is already cached, prefer it. ModernBERT is only chosen
        when ANE is not yet loaded.

        Returns:
            Embedder instance with .encode() / .embed() / .embed_batch() methods.
        """
        self._ensure_initialized()

        # ANE is already loaded → use it (ANE + MLX UMA are separate, not additive)
        if self._ane is not None and self._ane.is_loaded:
            logger.debug("[EMBED:ROUTER] sync: using cached ANE")
            return self._ane

        # ANE not loaded but MLX is loaded → stick with MLX (don't load another model)
        if self._check_mlx_loaded():
            try:
                mb = self._load_modernbert()
                logger.debug("[EMBED:ROUTER] sync: MLX in UMA, using ModernBERT")
                return mb
            except Exception:
                pass

        # Fallback to MLX ModernBERT (normal path when nothing loaded)
        try:
            mb = self._load_modernbert()
            logger.debug("[EMBED:ROUTER] sync: falling back to MLX ModernBERT")
            return mb
        except Exception as e:
            logger.warning(f"[EMBED:ROUTER] ModernBERT sync load failed: {e}")

        # Final fallback: MLXEmbeddingManager
        from hledac.universal.core.mlx_embeddings import get_embedding_manager
        return get_embedding_manager()

    async def get_embedder(self):
        """
        Priority: ANE → MLX ModernBERT → CPU fallback.

        M1 memory rule: If MLX model is already loaded, prefer ANE (doesn't
        compete with MLX Metal buffers). If no MLX model loaded, prefer ANE
        since it's cheaper on UMA than MLX ModernBERT.

        Returns:
            embedder with .embed(texts) → np.ndarray and .is_loaded property
        """
        self._ensure_initialized()

        # Try ANE first
        if self._ane_available:
            ane_ready = await self._load_ane()
            if ane_ready:
                # M1 guard: if MLX model is loaded, ANE doesn't add UMA pressure
                if self._check_mlx_loaded():
                    logger.debug("[EMBED:ROUTER] ANE ready, MLX already loaded — using ANE")
                    return self._ane
                # No MLX loaded: prefer ANE (300MB CoreML vs 500MB MLX)
                if not self._check_mlx_loaded():
                    logger.debug("[EMBED:ROUTER] ANE ready, no MLX loaded — using ANE")
                    return self._ane

        # Fallback to MLX ModernBERT
        try:
            mb = self._load_modernbert()
            logger.debug("[EMBED:ROUTER] Falling back to MLX ModernBERT")
            return mb
        except Exception as e:
            logger.warning(f"[EMBED:ROUTER] ModernBERT load failed: {e}")

        # Final fallback: MLXEmbeddingManager (CPU sentence-transformers)
        from hledac.universal.core.mlx_embeddings import get_embedding_manager
        return get_embedding_manager()

    async def warmup(self):
        """Warmup the selected embedder. Called after model selection."""
        embedder = await self.get_embedder()
        if embedder is None:
            return
        if hasattr(embedder, 'warmup') and asyncio.iscoroutinefunction(embedder.warmup):
            await embedder.warmup()
        elif hasattr(embedder, 'warmup'):
            embedder.warmup()

    def unload_all(self):
        """Release all embedders from memory."""
        if self._ane is not None:
            self._ane._loaded = False
            self._ane.model = None
            self._ane = None
        if self._modernbert is not None:
            try:
                self._modernbert.unload()
            except Exception:
                pass
            self._modernbert = None
        logger.info("[EMBED:ROUTER] All embedders unloaded")


# Module-level router singleton
_embedding_router: Optional[EmbeddingRouter] = None


def _get_embedder():
    """
    Get the embedding manager via EmbeddingRouter (ANE → MLX → CPU).

    P20: Changed to local import to break circular dependency on hledac.config.
    Sprint F228B: Now uses EmbeddingRouter for ANE-aware priority routing.

    Note: EmbeddingRouter.get_embedder() is async but this sync wrapper handles
    model selection inline to avoid blocking the event loop for callers that
    call _get_embedder() from sync contexts (embedding_pipeline.py callers).
    ANE loading is done via asyncio.run() here — it only happens once per
    EmbeddingRouter instance at first embed call.
    """
    global _embedding_router
    if _embedding_router is None:
        _embedding_router = EmbeddingRouter()
    return _embedding_router._get_embedder_sync()


def _is_swap_detected() -> bool:
    """Check if system is swapping (heuristic: psutil shows non-zero swap)."""
    try:
        import psutil
        swap = psutil.swap_memory()
        return swap.used > 0
    except Exception:
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
    # Step 1: UMA pressure — downgrade to safe minimum
    try:
        from hledac.universal.utils.uma_budget import (
            is_uma_warn,
            is_uma_critical,
            is_uma_emergency,
        )

        if is_uma_emergency() or is_uma_critical() or is_uma_warn():
            return 16
    except Exception:
        pass  # UMA not available — continue to env check

    # Step 2: Swap detected — downgrade to safe minimum
    if _is_swap_detected():
        return 16

    # Step 3: Env override
    import os

    raw_env = os.environ.get(_ENV_BATCH_SIZE_VAR, "").strip()
    if raw_env:
        try:
            env_batch = int(raw_env)
            if env_batch < 16:
                return 16  # Invalid — fall back to safe minimum
            if env_batch > 64:
                env_batch = 64  # Cap at maximum

            # Large batch (>32) requires explicit allow env
            if env_batch > 32:
                allow_large = os.environ.get(_ENV_ALLOW_LARGE_BATCH_VAR, "").strip()
                if allow_large != "1":
                    return 32  # Cap at 32 without explicit allow

            return env_batch
        except ValueError:
            pass  # Non-integer env — ignore and fall back

    # Step 4: Default safe
    return _DEFAULT_BATCH_SIZE


def _check_memory_guard() -> bool:
    """
    P19: Check if memory pressure allows embedding operations.

    Returns False if RSS > _embed_max_rss_gb, preventing model load.
    Also checks UmaWatchdog state for M1-specific pressure signals.

    Returns:
        True if safe to proceed, False to skip embedding.
    """
    # Check RSS against configurable limit
    current_rss = _get_current_rss_gb()
    if current_rss > _embed_max_rss_gb:
        logger.warning(
            f"[EMBED] Memory guard triggered: RSS={current_rss:.2f}GB "
            f"> limit={_embed_max_rss_gb:.2f}GB"
        )
        return False

    # F197C: Also check embedding depth (JS renderer conflict detection)
    # If depth > 0, we are already inside an embedding lifecycle — don't recurse
    with _embedding_depth_lock:
        if _embedding_depth > 0:
            logger.warning("[EMBED] Already in embedding context — skipping recursive call")
            return False

    # P13: Also check UmaWatchdog state
    try:
        from hledac.universal.utils.uma_budget import get_uma_pressure_level

        level_int, level_str = get_uma_pressure_level()
        if level_int >= 3:  # >= warning
            logger.warning(f"[EMBED] UmaWatchdog level={level_str} — skipping embedding")
            return False
    except Exception:
        pass  # uma_budget not available

    return True


def _uma_guard_before_batch() -> tuple[bool, dict]:
    """
    B3: Combined UMA guard — Metal active memory + RSS pre-batch.

    Prevents batch submission when combined Metal buffers + RSS would exceed
    the 6.5GB UMA ceiling (6656MB). Flushes Metal cache if threshold breached.

    Returns:
        (True, {}) if safe to proceed.
        (False, telemetry_dict) if batch blocked — caller MUST record telemetry.
    """
    telemetry: dict = {
        "uma_guard_blocked_batch": False,
        "uma_guard_reason": "",
        "combined_memory_mb": 0,
        "rss_mb": 0,
        "metal_active_mb": 0,
    }
    try:
        from hledac.universal.utils.mlx_memory import get_mlx_active_memory_mb

        active_mb = get_mlx_active_memory_mb()
        if active_mb is None:
            return True, {}

        rss_mb = psutil.Process().memory_info().rss // (1024 * 1024)
        combined_mb = active_mb + rss_mb

        telemetry["combined_memory_mb"] = combined_mb
        telemetry["rss_mb"] = rss_mb
        telemetry["metal_active_mb"] = active_mb

        if combined_mb > 6656:
            telemetry["uma_guard_blocked_batch"] = True
            telemetry["uma_guard_reason"] = f"combined_uma_pressure_{combined_mb}mb_exceeds_6656mb"
            logger.warning(
                f"[EMBED:UMA] Combined UMA pressure {combined_mb}MB "
                f"(Metal={active_mb}MB + RSS={rss_mb}MB) > 6656MB — flushing cache"
            )
            try:
                import mlx.core as mx
                mx.eval([])
                if hasattr(mx, 'metal') and hasattr(mx.metal, 'clear_cache'):
                    mx.metal.clear_cache()
            except Exception:
                pass
            return False, telemetry
        return True, {}
    except Exception:
        return True, {}  # Fail-safe — allow through


def _get_current_rss_gb() -> float:
    """Get current RSS memory in GB. P19: For memory guard checks."""
    try:
        return psutil.Process().memory_info().rss / 1e9
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
        raise MemoryPressureError(
            f"[EMBED] Memory pressure: RSS {current_rss:.2f}GB > threshold {threshold:.2f}GB "
            f"(max_rss_gb={max_rss_gb}, model_size_gb={model_size_gb}). "
            f"Skipping embedder load."
        )


def _release_embedder() -> None:
    """Release embedder from memory if loaded."""
    try:
        embedder = _get_embedder()
        if embedder.is_loaded:
            embedder.unload()
            logger.info("[EMBED] MLXEmbeddingManager unloaded")
    except Exception as e:
        logger.debug(f"[EMBED] Failed to unload embedder: {e}")


def generate_embeddings(texts: List[str], batch_size: int = _BATCH_SIZE, keep_loaded: bool = False) -> np.ndarray:
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

    # AREA J: xxhash dedup — avoid embedding identical texts twice
    original_to_unique: List[int] = []
    texts_to_embed: List[str] = texts
    dedup_happened = False
    try:
        import xxhash
        seen: Dict[str, int] = {}
        unique_list: List[str] = []
        original_to_unique = []

        for text in texts:
            h = xxhash.xxh64(text.encode("utf-8", errors="replace")).hexdigest()
            if h not in seen:
                seen[h] = len(unique_list)
                unique_list.append(text)
            original_to_unique.append(seen[h])

        if len(unique_list) < len(texts):
            dedup_happened = True
            dedup_ratio = (len(texts) - len(unique_list)) / len(texts)
            logger.debug(
                "[EMBED:J] xxhash dedup: %d→%d texts (%.0f%% duplicates removed)",
                len(texts), len(unique_list), dedup_ratio * 100
            )
            texts_to_embed = unique_list
    except ImportError:
        logger.debug("[EMBED:J] xxhash not available — skipping dedup")
        original_to_unique = list(range(len(texts)))

    # Memory guard check
    if not _check_memory_guard():
        logger.warning("[EMBED] Skipping embedding generation due to memory pressure")
        return np.zeros((0, _EMBEDDING_DIM), dtype=np.float32)

    embedder = _get_embedder()

    try:
        # Use encode with truncate_dim for MRL 256d output
        embeddings = embedder.encode(
            texts_to_embed,
            batch_size=batch_size,
            normalize=True,
            truncate_dim=_EMBEDDING_DIM,
        )

        # Ensure float32 dtype
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)

        # Ensure correct shape
        if embeddings.shape[1] > _EMBEDDING_DIM:
            embeddings = embeddings[:, :_EMBEDDING_DIM]
        elif embeddings.shape[1] < _EMBEDDING_DIM:
            pad = np.zeros((embeddings.shape[0], _EMBEDDING_DIM - embeddings.shape[1]), dtype=np.float32)
            embeddings = np.hstack([embeddings, pad])

        logger.debug(f"[EMBED] Generated embeddings shape: {embeddings.shape}")

        # AREA J: Remap results back to original order (duplicate texts share embeddings)
        if dedup_happened:
            full_embeddings = np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)
            for orig_idx, unique_idx in enumerate(original_to_unique):
                full_embeddings[orig_idx] = embeddings[unique_idx]
            embeddings = full_embeddings

        return embeddings

    except Exception as e:
        logger.error(f"[EMBED] Batch embedding failed: {e}")
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)

    finally:
        # Release embedder after batch processing to free memory
        # Keep loaded if caller is using embedding_session context manager
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
    # Memory guard check
    if not _check_memory_guard():
        logger.warning("[EMBED] Skipping query embedding due to memory pressure")
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)

    embedder = _get_embedder()

    try:
        # Use embed_query with truncate_dim for MRL 256d output
        emb = embedder.embed_query(text, truncate_dim=_EMBEDDING_DIM)

        # Ensure float32 and correct shape
        if emb.dtype != np.float32:
            emb = emb.astype(np.float32)

        # Flatten to 1D
        if emb.ndim == 2:
            emb = emb.squeeze(0)

        # Ensure correct dimension
        if len(emb) > _EMBEDDING_DIM:
            emb = emb[:_EMBEDDING_DIM]
        elif len(emb) < _EMBEDDING_DIM:
            emb = np.pad(emb, (0, _EMBEDDING_DIM - len(emb)))

        return emb

    except Exception as e:
        logger.error(f"[EMBED] Query embedding failed: {e}")
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
        logger.error(f"[EMBED] Document embedding failed: {e}")
        return np.zeros(_EMBEDDING_DIM, dtype=np.float32)


async def generate_embeddings_async(texts: List[str], batch_size: int = _BATCH_SIZE, keep_loaded: bool = False) -> np.ndarray:
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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, generate_embeddings, texts, batch_size, keep_loaded
    )


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
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, embed_query, text)


# P13 integration point for brain-level lifecycle management
# P19: Memory guard - configurable max_rss_gb (default 5.5 for M1 8GB)
_embed_max_rss_gb: float = 5.5

# F197C: Embedding context depth tracker for M1 memory guard.
# Prevents JS renderer (Camoufox/nodriver) from running simultaneously with
# loaded embedding model on M1 Air 8GB. BROKEN check in public_fetcher used
# semaphore._value which is always <= max, causing the guard to always fire.
# Increment before model load attempt, decrement after unload — balanced per call.
import threading
_embedding_depth: int = 0
_embedding_depth_lock = threading.Lock()

# ---------------------------------------------------------------------------
# F207L: Reentrant embedding session with refcounting — avoids cold start
# ---------------------------------------------------------------------------
_embed_refcount: int = 0
_embed_refcount_lock = asyncio.Lock()


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
        async with _embed_refcount_lock:
            _embed_refcount += 1
            first_entry = _embed_refcount == 1
        if first_entry:
            # Load outside the lock — lock guards refcount only, not executor
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, load_embedding_model)

    async def __aexit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        global _embed_refcount
        should_unload = False
        async with _embed_refcount_lock:
            _embed_refcount -= 1
            if _embed_refcount <= 0:
                _embed_refcount = 0
                should_unload = True
        if should_unload:
            # Unload outside the lock — lock guards refcount only, not executor
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, unload_embedding_model)


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
    # F197C: Increment depth before load attempt (balanced with decrement in unload)
    with _embedding_depth_lock:
        global _embedding_depth
        _embedding_depth += 1
    rss_before: float = 0.0  # initialized before try so except block always sees it
    try:
        # P19: Memory pressure check before load
        rss_before = _get_current_rss_gb()
        _check_memory_before_load(_embed_max_rss_gb, _ESTIMATED_EMBEDDING_MODEL_SIZE_GB)

        embedder = _get_embedder()
        if not embedder.is_loaded:
            embedder._load_model()
        logger.info(f"[EMBED] Embedding model loaded (RSS before={rss_before:.2f}GB)")
        return True
    except MemoryPressureError:
        logger.warning(f"[EMBED] Memory pressure - skipping embedder load (RSS={rss_before:.2f}GB)")
        return False
    except Exception as e:
        logger.error(f"[EMBED] Failed to load embedding model: {e}")
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
    # F197C: Decrement depth — always balanced with increment in load_embedding_model()
    # _embedding_depth can legitimately be 0 if load was a no-op (already loaded)
    # but we still decrement to keep the pair balanced for the lifecycle caller
    with _embedding_depth_lock:
        global _embedding_depth
        if _embedding_depth > 0:
            _embedding_depth -= 1
    try:
        embedder = _get_embedder()
        if embedder.is_loaded:
            rss_before = _get_current_rss_gb()
            embedder.unload()
            gc.collect()  # Force collection after unload
            rss_after = _get_current_rss_gb()
            dropped = rss_before - rss_after
            expected_drop = _ESTIMATED_EMBEDDING_MODEL_SIZE_GB

            if dropped < expected_drop * 0.5:  # Allow 50% tolerance
                logger.warning(
                    f"[EMBED] RSS did not drop expected amount after unload: "
                    f"dropped={dropped:.2f}GB, expected~{expected_drop:.2f}GB "
                    f"(RSS before={rss_before:.2f}GB, after={rss_after:.2f}GB)"
                )
            else:
                logger.info(f"[EMBED] Embedding model unloaded (RSS dropped={dropped:.2f}GB)")
        return True
    except Exception as e:
        logger.error(f"[EMBED] Failed to unload embedding model: {e}")
        return False


def get_embedding_dimension() -> int:
    """Return the MRL embedding dimension (256)."""
    return _EMBEDDING_DIM


# ---------------------------------------------------------------------------
# F203I: Streaming batch API (non-breaking, additive)
# ---------------------------------------------------------------------------

async def generate_embeddings_streaming(
    texts: list[str],
    batch_size: int = _BATCH_SIZE,
) -> AsyncIterator[tuple[list[str], np.ndarray]]:
    """
    F203I: Streaming batch embedder — yields (ids, embeddings) per batch.

    Yields incrementally instead of materializing all embeddings at once,
    reducing peak RSS on M1 8GB during embedding phases.

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

    # Memory guard check
    if not _check_memory_guard():
        logger.warning("[EMBED:streaming] Skipped due to memory pressure")
        return

    # Load model once, use for all batches
    model_loaded = False
    try:
        if not _get_embedder().is_loaded:
            if not load_embedding_model():
                # Fall back: materialize all at once
                # B3: Combined UMA guard pre-batch
                safe, telemetry = _uma_guard_before_batch()
                if not safe:
                    logger.warning(
                        f"[EMBED:streaming] Fallback batch skipped due to UMA pressure: "
                        f"combined={telemetry.get('combined_memory_mb', 0)}MB"
                    )
                    return
                loop = asyncio.get_running_loop()
                embs = await loop.run_in_executor(
                    None, generate_embeddings, texts, batch_size
                )
                ids = [str(i) for i in range(len(texts))]
                if embs.shape[0] > 0:
                    yield (ids, embs)
                return
            model_loaded = True

        # Chunk and yield
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            chunk_ids = [str(i + j) for j in range(len(chunk))]

            # B3: Combined UMA guard pre-batch
            safe, telemetry = _uma_guard_before_batch()
            if not safe:
                logger.warning(
                    f"[EMBED:streaming] Batch {i} skipped due to UMA pressure: "
                    f"combined={telemetry.get('combined_memory_mb', 0)}MB"
                )
                break

            loop = asyncio.get_running_loop()
            try:
                embs = await loop.run_in_executor(
                    None, _generate_embeddings_chunk, chunk, batch_size
                )
                if embs is not None and embs.shape[0] == len(chunk):
                    yield (chunk_ids, embs)
            except Exception as e:
                logger.debug(f"[EMBED:streaming] batch error at offset {i}: {e}")
                continue

    finally:
        if model_loaded:
            unload_embedding_model()


def _generate_embeddings_chunk(texts: list[str], batch_size: int) -> np.ndarray:
    """Sync helper for a single chunk — runs in thread executor."""
    return generate_embeddings(texts, batch_size=batch_size)
