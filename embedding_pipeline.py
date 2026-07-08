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
import inspect
import logging
import threading
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Self

import numpy as np
import psutil

from hledac.universal.utils.exceptions import MemoryPressureError

if TYPE_CHECKING:
    from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder

logger = logging.getLogger(__name__)

# CoreML→MLX migration:
# OLD: CoreMLEmbedder (bge-small-en-v1.5.mlpackage on ANE) + _ANE_EMBEDDER singleton
# NEW: MLXEmbeddingManager / ModernBERTEmbedder only — no CoreML subprocess
# Boot impact: was eager+blocking (mlpackage load at first embed call); now fully lazy
COREML_AVAILABLE = False  # CoreML removed — MLX only

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
    MLX-only priority routing: MLX ModernBERT → MLXEmbeddingManager.

    CoreML ANE path removed per CoreML→MLX migration.
    All inference is in-process on M1 Metal — no subprocess, no blocking.
    """

    def __init__(self) -> Self:
        self._modernbert: ModernBERTEmbedder | None = None

    def _load_modernbert(self) -> ModernBERTEmbedder:
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
            return current is not None
        except Exception:
            return False

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
                logger.debug("[EMBED:ROUTER] sync: MLX in UMA, using ModernBERT")
                return mb
            except Exception:  # noqa: BLE001
                pass
        try:
            mb = self._load_modernbert()
            logger.debug("[EMBED:ROUTER] sync: MLX ModernBERT loaded")
            return mb
        except Exception as e:
            logger.debug(f"[EMBED:ROUTER] ModernBERT sync load failed: {e}")
        from compat.core_mlx_embeddings import get_mlx_embedder
        return get_mlx_embedder()

    async def get_embedder(self):
        """
        MLX-only embedder (CoreML ANE path removed).
        Priority: MLX ModernBERT → MLXEmbeddingManager.
        """
        if self._check_mlx_loaded():
            try:
                mb = self._load_modernbert()
                logger.debug("[EMBED:ROUTER] async: MLX in UMA, using ModernBERT")
                return mb
            except Exception:  # noqa: BLE001
                pass
        try:
            mb = self._load_modernbert()
            logger.debug("[EMBED:ROUTER] async: MLX ModernBERT loaded")
            return mb
        except Exception as e:
            logger.warning(f"[EMBED:ROUTER] ModernBERT load failed: {e}")
        from compat.core_mlx_embeddings import get_mlx_embedder
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
            except Exception:  # noqa: BLE001
                pass
            self._modernbert = None
        logger.info("[EMBED:ROUTER] All embedders unloaded")

# === MLX-only embedder (CoreML ANE removed per CoreML→MLX migration) ===
# All embedding now via MLXEmbeddingManager / ModernBERTEmbedder — no CoreML subprocess.

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
            is_uma_critical,
            is_uma_emergency,
            is_uma_warn,
        )

        if is_uma_emergency() or is_uma_critical() or is_uma_warn():
            return 16
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # UMA not available — continue to env check

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
        if level_str != "normal":
            logger.warning(f"[EMBED] UmaWatchdog level={level_str} ({level_int}%) — skipping embedding")
            return False
    except Exception:  # noqa: BLE001
        pass  # noqa: BLE001  # uma_budget not available

    return True


# Issue #25: Dynamic UMA guard threshold — computed from resource_governor SSOT.
# Previously hardcoded 6656MB (6.5 GB) which didn't adapt to different RAM configs.
# Now imports _THRESHOLD_CRITICAL_GIB from core/resource_governor.py (SSOT).
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
        from core.resource_governor import _THRESHOLD_CRITICAL_GIB
        _UMA_GUARD_THRESHOLD_MB = int(_THRESHOLD_CRITICAL_GIB * 1024)
    except Exception:  # noqa: BLE001
        _UMA_GUARD_THRESHOLD_MB = 6656  # legacy fallback
        logger.debug("[EMBED:UMA] Could not import _THRESHOLD_CRITICAL_GIB, using fallback 6656MB")
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
        threshold_mb = _get_uma_guard_threshold()

        telemetry["combined_memory_mb"] = combined_mb
        telemetry["rss_mb"] = rss_mb
        telemetry["metal_active_mb"] = active_mb

        if combined_mb > threshold_mb:
            telemetry["uma_guard_blocked_batch"] = True
            telemetry["uma_guard_reason"] = f"combined_uma_pressure_{combined_mb}mb_exceeds_{threshold_mb}mb"
            logger.warning(
                f"[EMBED:UMA] Combined UMA pressure {combined_mb}MB "
                f"(Metal={active_mb}MB + RSS={rss_mb}MB) > {threshold_mb}MB — flushing cache"
            )
            try:
                import mlx.core as mx
                mx.eval([])
                import gc
                gc.collect()  # F266: Python GC BEFORE Metal release
                try:
                    mx.clear_cache()
                except AttributeError:
                    try:
                        mx.metal.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass
                gc.collect()  # F266: second GC pass
            except Exception:  # noqa: BLE001
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


def generate_embeddings(texts: list[str], batch_size: int | None = None, keep_loaded: bool = False) -> np.ndarray:
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

    # F218A KROK 4: Use adaptive batch size if caller didn't specify
    if batch_size is None:
        batch_size = get_adaptive_batch_size()

    # AREA J: xxhash dedup — avoid embedding identical texts twice
    original_to_unique: list[int] = []
    texts_to_embed: list[str] = texts
    dedup_happened = False
    try:
        import xxhash
        seen: dict[str, int] = {}
        unique_list: list[str] = []
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
    if embedder is None:
        logger.warning("[EMBED] No embedder available — returning zero array")
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)

    try:
        # Use adaptive encode with memory-pressure-aware batch sizing.
        # encode_adaptive checks UMA pressure before each sub-batch and
        # shrinks/grows batch size dynamically — reduces peak RSS 30%+ on M1 8GB.
        embeddings = embedder.encode_adaptive(
            texts_to_embed,
            initial_batch_size=batch_size,
            min_batch_size=4,
            max_batch_size=128,
            normalize=True,
            truncate_dim=_EMBEDDING_DIM,
            memory_pressure_provider=_uma_pressure_provider,
        )

        # === F218A: embed_backend_telemetry ===
        try:
            import psutil as _psutil
            backend_name = type(embedder).__name__.lower()
            if "coreml" in backend_name:
                backend_name = "coreml_bge"
            elif "ane" in backend_name or "allminilm" in backend_name.lower():
                backend_name = "ane_allminilm"
            elif "modernbert" in backend_name.lower():
                backend_name = "mlx_modernbert"
            else:
                backend_name = "cpu"
            _ram = _psutil.virtual_memory().percent
            logger.debug(
                "EMBED_BACKEND: %s | texts=%d | dim=%s | ram=%.1f%%",
                backend_name,
                len(texts_to_embed),
                embeddings.shape,
                _ram,
            )
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001  # Telemetry failures never crash embedding

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


async def generate_embeddings_async(texts: list[str], batch_size: int = _BATCH_SIZE, keep_loaded: bool = False) -> np.ndarray:  # noqa: E501
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
    return await asyncio.to_thread(
        generate_embeddings, texts, batch_size, keep_loaded
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
    return await asyncio.to_thread(embed_query, text)


# P13 integration point for brain-level lifecycle management
# P19: Memory guard - configurable max_rss_gb (default 5.5 for M1 8GB)
_embed_max_rss_gb: float = 5.5

# F197C: Embedding context depth tracker for M1 memory guard.
# Prevents JS renderer (Camoufox/nodriver) from running simultaneously with
# loaded embedding model on M1 Air 8GB. BROKEN check in public_fetcher used
# semaphore._value which is always <= max, causing the guard to always fire.
# Increment before model load attempt, decrement after unload — balanced per call.

_embedding_depth: int = 0
_embedding_depth_lock = threading.Lock()

# ---------------------------------------------------------------------------
# F207L: Reentrant embedding session with refcounting — avoids cold start
# ---------------------------------------------------------------------------
_embed_refcount: int = 0
_embed_refcount_lock = asyncio.Lock()


class embedding_session:  # noqa: N801
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
            await asyncio.to_thread(load_embedding_model)

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
        # F197C: Decrement depth even on MemoryPressureError to keep pair balanced
        with _embedding_depth_lock:
            _embedding_depth -= 1
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
                embs = await asyncio.to_thread(
                    generate_embeddings, texts, batch_size
                )
                ids = [str(i) for i, _ in enumerate(texts)]
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

            try:
                embs = await asyncio.to_thread(
                    _generate_embeddings_chunk, chunk, batch_size
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


def _encode_batch_no_release(texts: list[str], batch_size: int) -> np.ndarray:
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
        embeddings = embedder.encode(
            texts,
            normalize=True,
            truncate_dim=_EMBEDDING_DIM,
        )
        if embeddings.dtype != np.float32:
            embeddings = embeddings.astype(np.float32)
        if embeddings.shape[1] > _EMBEDDING_DIM:
            embeddings = embeddings[:, :_EMBEDDING_DIM]
        elif embeddings.shape[1] < _EMBEDDING_DIM:
            pad = np.zeros((embeddings.shape[0], _EMBEDDING_DIM - embeddings.shape[1]), dtype=np.float32)
            embeddings = np.hstack([embeddings, pad])
        return embeddings
    except Exception as e:
        logger.error(f"[EMBED] _encode_batch_no_release failed: {e}")
        return np.zeros((len(texts), _EMBEDDING_DIM), dtype=np.float32)


async def _async_enumerate(iterator: AsyncIterator[str], start: int = 0) -> AsyncIterator[tuple[str, str]]:
    """Async enumerate — yields (str(index), item) from AsyncIterator."""
    idx = start
    async for item in iterator:
        yield (str(idx), item)
        idx += 1


# ---------------------------------------------------------------------------
# MLX Streaming for Embeddings (F265 Streaming)
# ---------------------------------------------------------------------------
# IMPORTANT: ModernBERT is a FORWARD-PASS encoder, NOT an autoregressive LLM.
# mlx_lm.generate_stream() streams LLM tokens during autoregressive decode.
# For embeddings, there are no "tokens to stream" — the entire sequence is
# encoded in one matmul forward pass. The streaming patterns below provide:
#   1. Batch-oriented per-item yields: embed_stream(list[str]) — Issue #15 fix
#   2. True AsyncIterator[str] input: generate_embeddings_from_iterator()
#   3. Memory pressure-aware yielding (skip items when UMA is tight)
#   4. Progressive delivery to caller without buffering full batch
#
# For true token-level streaming use brain/deephermes3_engine.generate_stream().
# ---------------------------------------------------------------------------

async def embed_stream(
    texts: list[str],
    batch_size: int = _BATCH_SIZE,
) -> AsyncIterator[tuple[str, np.ndarray]]:
    """
    Issue #15 fix: Batch-oriented per-item embedding stream.

    Accumulates items into batches internally, then yields one embedding
    at a time. Contrast with old single-item encode which had per-item
    tokenizer overhead. Peak RSS bounded by batch_size × seq_len × 4B.

    NOTE: Unlike mlx_lm.generate_stream() which streams LLM tokens during
    autoregressive decode, this function streams ModernBERT forward-pass
    embeddings one-item-at-a-time. There is no autoregressive process.

    M1 8GB invariants:
    - Batch-oriented encoding: amortizes tokenizer overhead across batch_size items
    - mx.eval([]) barrier before mx.metal.clear_cache() after each batch
    - UMA guard pre-batch skips yielding when Metal pressure is critical

    Args:
        texts: List of text strings to embed.
        batch_size: Internal batch size for encode() calls (default 16, capped).

    Yields:
        tuple[str, np.ndarray]: (item_id, embedding_vector) per item.
            embedding_vector shape=(256,) float32.
            Yields nothing if memory pressure or error (fail-open).

    Example:
        async for item_id, emb in embed_stream(["doc1", "doc2", "doc3"]):
            print(f"Item {item_id}: shape={emb.shape}")
    """
    if not texts:
        return

    # NOTE: Skipping _check_memory_guard() here — streaming functions are
    # designed to be called within embedding_session or after load, where
    # the depth guard would block. UMA guard below handles per-batch check.
    # Callers should use embedding_session() for lifecycle management.

    batch_size = min(batch_size, _BATCH_SIZE)
    batch: list[tuple[int, str]] = []  # (index, text) pairs

    # Load model once for all items
    model_loaded = False
    if not _get_embedder().is_loaded:
        if not load_embedding_model():
            return
        model_loaded = True

    try:
        for i, text in enumerate(texts):
            batch.append((i, text))

            if len(batch) >= batch_size:
                # B3: UMA guard pre-batch
                safe, telemetry = _uma_guard_before_batch()
                if not safe:
                    logger.warning(
                        f"[EMBED:stream] Batch behind item {i} skipped due to UMA pressure: "
                        f"combined={telemetry.get('combined_memory_mb', 0)}MB"
                    )
                    batch.clear()
                    continue

                _, batch_texts = zip(*batch)
                try:
                    embs = await asyncio.to_thread(
                        _encode_batch_no_release, list(batch_texts), batch_size
                    )
                    if embs is not None and embs.shape[0] == len(batch_texts):
                        for (orig_idx, _), emb_vec in zip(batch, embs):
                            yield (str(orig_idx), emb_vec)
                except Exception as e:
                    logger.debug(f"[EMBED:stream] batch error at item {i}: {e}")
                finally:
                    batch.clear()

                # F266: mx.eval([]) barrier before clear_cache after each batch
                try:
                    import mlx.core as mx
                    mx.eval([])
                    try:
                        mx.clear_cache()
                    except AttributeError:
                        try:
                            mx.metal.clear_cache()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass

        # Yield remaining items in final partial batch
        if batch:
            safe, telemetry = _uma_guard_before_batch()
            if safe:
                _, batch_texts = zip(*batch)
                try:
                    embs = await asyncio.to_thread(
                        _encode_batch_no_release, list(batch_texts), batch_size
                    )
                    if embs is not None and embs.shape[0] == len(batch_texts):
                        for (orig_idx, _), emb_vec in zip(batch, embs):
                            yield (str(orig_idx), emb_vec)
                except Exception as e:
                    logger.debug(f"[EMBED:stream] final batch error: {e}")
                finally:
                    batch.clear()

            # F266: Final mx.eval barrier
            try:
                import mlx.core as mx
                mx.eval([])
                try:
                    mx.clear_cache()
                except AttributeError:
                    try:
                        mx.metal.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

    finally:
        if model_loaded:
            unload_embedding_model()


async def generate_embeddings_from_iterator(
    texts_iter: AsyncIterator[str],
    batch_size: int = _BATCH_SIZE,
) -> AsyncIterator[tuple[str, np.ndarray]]:
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
    batch: list[tuple[str, str]] = []  # (id, text) pairs

    # NOTE: Skipping _check_memory_guard() here — same as embed_stream.
    # Streaming functions manage their own UMA guard per batch.
    # Callers should use embedding_session() for lifecycle management.

    batch_size = min(batch_size, _BATCH_SIZE)

    # Load model once for all batches
    model_loaded = False
    if not _get_embedder().is_loaded:
        if not load_embedding_model():
            return
        model_loaded = True

    try:
        async for item_id, text in _async_enumerate(texts_iter):
            batch.append((item_id, text))

            if len(batch) >= batch_size:
                # B3: UMA guard pre-batch
                safe, telemetry = _uma_guard_before_batch()
                if not safe:
                    logger.warning(
                        f"[EMBED:stream-iter] Batch behind {item_id} skipped due to UMA pressure: "
                        f"combined={telemetry.get('combined_memory_mb', 0)}MB"
                    )
                    batch.clear()
                    continue

                chunk_ids, chunk_texts = zip(*batch)
                try:
                    embs = await asyncio.to_thread(
                        _encode_batch_no_release, list(chunk_texts), batch_size
                    )
                    if embs is not None and embs.shape[0] == len(chunk_texts):
                        for cid, emb_vec in zip(chunk_ids, embs):
                            yield (str(cid), emb_vec)
                except Exception as e:
                    logger.debug(f"[EMBED:stream-iter] batch error at {item_id}: {e}")
                finally:
                    batch.clear()

                # F266: mx.eval([]) barrier before clear_cache after each batch
                try:
                    import mlx.core as mx
                    mx.eval([])
                    try:
                        mx.clear_cache()
                    except AttributeError:
                        try:
                            mx.metal.clear_cache()
                        except Exception:  # noqa: BLE001
                            pass
                except Exception:  # noqa: BLE001
                    pass

        # Yield remaining items in final partial batch
        if batch:
            safe, telemetry = _uma_guard_before_batch()
            if safe:
                chunk_ids, chunk_texts = zip(*batch)
                try:
                    embs = await asyncio.to_thread(
                        _encode_batch_no_release, list(chunk_texts), batch_size
                    )
                    if embs is not None and embs.shape[0] == len(chunk_texts):
                        for cid, emb_vec in zip(chunk_ids, embs):
                            yield (str(cid), emb_vec)
                except Exception as e:
                    logger.debug(f"[EMBED:stream-iter] final batch error: {e}")
                finally:
                    batch.clear()

            # F266: Final mx.eval barrier
            try:
                import mlx.core as mx
                mx.eval([])
                try:
                    mx.clear_cache()
                except AttributeError:
                    try:
                        mx.metal.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001
                pass

    finally:
        if model_loaded:
            unload_embedding_model()


def _encode_single_item(text: str) -> np.ndarray | None:
    """Sync helper: encode single text, return 256d embedding or None on error."""
    try:
        emb = embed_query(text)  # Uses search_query prefix for asymmetric
        return emb
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Adaptive Streaming — MLX Embedding Batch with Memory Pressure Feedback
# ---------------------------------------------------------------------------

def _uma_pressure_provider() -> float:
    """
    Memory pressure provider for AdaptiveEmbeddingBatcher.

    Returns 0.0-1.0 float derived from UMA guard state.
    Uses the same threshold logic as _uma_guard_before_batch().
    """
    try:
        from hledac.universal.utils.uma_budget import get_uma_pressure_level
        level_int, level_str = get_uma_pressure_level()
        # Map 0-100 int to 0.0-1.0 float
        return level_int / 100.0
    except Exception:
        return 0.5  # fail-safe neutral


async def generate_embeddings_adaptive_streaming(
    texts: list[str],
    initial_batch_size: int = 32,
    min_batch_size: int = 4,
    max_batch_size: int = 128,
    pressure_high: float = 0.80,
    pressure_low: float = 0.50,
) -> AsyncIterator[tuple[list[int], np.ndarray]]:
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

    # Memory guard check
    if not _check_memory_guard():
        logger.warning("[EMBED:adaptive] Skipped due to memory pressure")
        return

    # Lazy import to avoid circular dependency
    from core.embeddings.manager import AdaptiveEmbeddingBatcher, get_mlx_embedder

    embedder = get_mlx_embedder()
    await embedder.ensure_loaded()

    batcher = AdaptiveEmbeddingBatcher(
        initial_batch_size=initial_batch_size,
        min_batch_size=min_batch_size,
        max_batch_size=max_batch_size,
        pressure_high=pressure_high,
        pressure_low=pressure_low,
    )

    async for indices, emb_batch in batcher.process_streaming(
        texts, embedder, _uma_pressure_provider
    ):
        yield (indices, emb_batch)


# ---------------------------------------------------------------------------
# F218A: Embedding Ownership — Canonical entry point helpers
# ---------------------------------------------------------------------------

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


def embed_texts_canonical(texts: list[str], batch_size: int = _BATCH_SIZE) -> np.ndarray:
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
      - "ane"           — ANEEmbedder (CoreML MiniLM-L6-v2)
      - "mlx"           — MLX ModernBERT (ModernBERTEmbedder via mlx-embeddings)
      - "mlx_manager"   — MLXEmbeddingManager fallback (when ModernBERT unavailable)
      - "zero_fallback" — fail-open zero-array fallback (model load failed)
      - "not_loaded"    — router initialized but no model loaded yet
      - "unknown"       — cannot determine (error state)

    This is a read-only diagnostic — does not trigger model loading.
    """
    global _embedding_router
    if _embedding_router is None:
        return "not_loaded"

    try:
        router = _embedding_router

        # Check ANE first
        if router._ane_available and router._ane is not None and router._ane.is_loaded:
            return "ane"

        # Check MLX ModernBERT via ModelManager
        if router._check_mlx_loaded():
            if router._modernbert is not None and router._modernbert.is_loaded:
                return "mlx"

        # Check if ModernBERT was loaded via _load_modernbert (even if not in ModelManager yet)
        if router._modernbert is not None and router._modernbert.is_loaded:
            return "mlx"

        # Not loaded yet
        if not router._initialized:
            return "not_loaded"

        # If ANE not available, ModernBERT would be loaded next; report that intent
        if not router._ane_available:
            # ANE unavailable — router would fall through to ModernBERT path
            # but nothing is loaded yet, so report not_loaded (not mlx)
            return "not_loaded"

        return "not_loaded"

    except Exception:
        return "unknown"
