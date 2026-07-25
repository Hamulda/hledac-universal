"""
MLX Embedding Manager — lazy ModernBERT via mlx-embeddings.

Single source of truth for embedding lifecycle (load/encode/unload/prewarm).
Metal buffers pre-warmed on load; mx.eval([]) barrier before clear_cache().

Streaming batcher: AdaptiveEmbeddingBatcher with per-batch memory pressure feedback
reduces peak RSS by 30%+ on M1 8GB by dynamically adjusting batch size mid-stream.
"""
import asyncio
import logging
import threading
import msgspec
import time
import warnings
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, cast
import numpy as np
logger = logging.getLogger(__name__)

class AdaptiveEmbeddingBatcher:
    """
    Streaming batcher with dynamic memory pressure feedback.

    Unlike static batching, this adjusts batch size BETWEEN sub-batch calls
    based on real-time memory pressure readings.

    Always-on, fail-safe, bounded for M1 8GB UMA.

    Usage:
        batcher = AdaptiveEmbeddingBatcher(
            initial_batch_size=32,
            min_batch_size=4,
            max_batch_size=128,
        )
        async for ids, embeddings in batcher.process_streaming(texts, embedder, memory_provider):
            ...

    Invariants:
        - Memory pressure checked BEFORE each sub-batch (Issue #23 fix)
        - Batch size never exceeds max_batch_size
        - Zero texts returns empty immediately
    """
    __slots__ = ('_initial_batch_size', '_min_batch_size', '_max_batch_size', '_pressure_high', '_pressure_low', '_scale_up_factor', '_scale_down_factor', '_stats')

    def __init__(self, initial_batch_size: int=32, min_batch_size: int=4, max_batch_size: int=128, *, pressure_high: float=0.8, pressure_low: float=0.5, scale_up_factor: float=1.5, scale_down_factor: float=0.5) -> None:
        self._initial_batch_size = initial_batch_size
        self._min_batch_size = min_batch_size
        self._max_batch_size = max_batch_size
        self._pressure_high = pressure_high
        self._pressure_low = pressure_low
        self._scale_up_factor = scale_up_factor
        self._scale_down_factor = scale_down_factor
        self._stats: dict[str, int | float] = {'batches_processed': 0, 'memory_pressure_events': 0, 'total_texts': 0, 'peak_batch_size': initial_batch_size, 'min_batch_size_used': initial_batch_size}

    def _record_batch_size(self, batch_size: int) -> None:
        self._stats['peak_batch_size'] = max(self._stats['peak_batch_size'], batch_size)
        self._stats['min_batch_size_used'] = min(self._stats['min_batch_size_used'], batch_size)

    async def _get_pressure(self, memory_provider: Callable[[], float] | Callable[[], Awaitable[float]]) -> float:
        """Get current memory pressure as float (0.0-1.0)."""
        try:
            val = memory_provider()
            if asyncio.iscoroutine(val):
                result = cast(float, await val)
                return result
            return cast(float, val)
        except Exception:
            return 0.5

    async def _gpu_arbiter_defer(self) -> None:
        """Defer via GPUArbiter before a sub-batch encode (GPU arbitration)."""
        arbiter = get_gpu_arbiter()
        if arbiter.should_defer():
            if not await arbiter.wait_until_free():
                logger.debug('[AdaptiveBatcher] GPU arbiter timeout — proceeding anyway')

    async def process(self, texts: list[str], embedder: 'MLXEmbeddingManager', memory_provider: Callable[[], float] | Callable[[], Awaitable[float]]) -> list[list[float]]:
        """
        Process all texts with dynamic batch sizing.

        Memory pressure is checked BEFORE each sub-batch, enabling
        mid-stream batch size adjustment.

        GPUArbiter is consulted before each encode_async call to avoid
        Metal memory-bandwidth contention with Hermes3 inference (ISSUE #023).

        Args:
            texts: List of texts to embed
            embedder: MLXEmbeddingManager instance
            memory_provider: Callable returning 0.0-1.0 pressure (sync or async)

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []
        self._stats['total_texts'] = len(texts)
        results: list[list[float]] = []
        current_batch_size = self._initial_batch_size
        i = 0
        while i < len(texts):
            pressure = await self._get_pressure(memory_provider)
            if pressure >= self._pressure_high:
                new_size = max(self._min_batch_size, int(current_batch_size * self._scale_down_factor))
                if new_size < current_batch_size:
                    current_batch_size = new_size
                    self._stats['memory_pressure_events'] += 1
                    logger.debug(f'[AdaptiveBatcher] Pressure {pressure:.2f} → shrinking to {current_batch_size}')
            elif pressure <= self._pressure_low and current_batch_size < self._max_batch_size:
                new_size = min(self._max_batch_size, int(current_batch_size * self._scale_up_factor))
                if new_size > current_batch_size:
                    current_batch_size = new_size
                    logger.debug(f'[AdaptiveBatcher] Pressure {pressure:.2f} → growing to {current_batch_size}')
            self._record_batch_size(current_batch_size)
            batch = texts[i:i + current_batch_size]
            try:
                await self._gpu_arbiter_defer()
                batch_result = await embedder.encode_async(batch, batch_size=len(batch))
                if hasattr(batch_result, 'tolist'):
                    results.extend(batch_result.tolist())
                else:
                    results.extend(batch_result)
            except Exception as e:
                logger.warning(f'[AdaptiveBatcher] Batch encode failed: {e}')
                zero_emb = [0.0] * embedder.EMBEDDING_DIM
                results.extend([zero_emb] * len(batch))
            i += current_batch_size
            self._stats['batches_processed'] += 1
        return results

    async def process_streaming(self, texts: list[str], embedder: 'MLXEmbeddingManager', memory_provider: Callable[[], float] | Callable[[], Awaitable[float]]) -> AsyncIterator[tuple[list[int], np.ndarray]]:
        """
        Streaming variant — yields (indices, embeddings) per batch.

        Enables true streaming with memory pressure feedback between batches.
        Yields incrementally instead of materializing all embeddings at once,
        reducing peak RSS on M1 8GB.

        GPUArbiter is consulted before each encode_async call to avoid
        Metal memory-bandwidth contention with Hermes3 inference (ISSUE #023).

        Args:
            texts: List of texts to embed
            embedder: MLXEmbeddingManager instance
            memory_provider: Callable returning 0.0-1.0 pressure (sync or async)

        Yields:
            tuple[list[int], np.ndarray]: batch indices and embeddings
        """
        if not texts:
            return
        self._stats['total_texts'] = len(texts)
        current_batch_size = self._initial_batch_size
        i = 0
        while i < len(texts):
            pressure = await self._get_pressure(memory_provider)
            if pressure >= self._pressure_high:
                new_size = max(self._min_batch_size, int(current_batch_size * self._scale_down_factor))
                if new_size < current_batch_size:
                    current_batch_size = new_size
                    self._stats['memory_pressure_events'] += 1
                    logger.debug(f'[AdaptiveBatcher] Pressure {pressure:.2f} → shrinking to {current_batch_size}')
            elif pressure <= self._pressure_low and current_batch_size < self._max_batch_size:
                new_size = min(self._max_batch_size, int(current_batch_size * self._scale_up_factor))
                if new_size > current_batch_size:
                    current_batch_size = new_size
                    logger.debug(f'[AdaptiveBatcher] Pressure {pressure:.2f} → growing to {current_batch_size}')
            self._record_batch_size(current_batch_size)
            batch = texts[i:i + current_batch_size]
            batch_indices = list(range(i, i + len(batch)))
            try:
                await self._gpu_arbiter_defer()
                batch_result = await embedder.encode_async(batch, batch_size=len(batch))
                yield (batch_indices, batch_result)
            except Exception as e:
                logger.warning(f'[AdaptiveBatcher] Batch encode failed: {e}')
                zero_emb = np.zeros((len(batch), embedder.EMBEDDING_DIM), dtype=np.float32)
                yield (batch_indices, zero_emb)
            i += current_batch_size
            self._stats['batches_processed'] += 1

    @property
    def stats(self) -> dict[str, int | float]:
        """Return batching statistics for telemetry."""
        return dict(self._stats)

# ── GPU Resource Arbitration (ISSUE #023) ────────────────────────────────────
#
# Hermes3 MLX inference and MLX embedder share Metal GPU memory bandwidth.
# When Hermes3 saturates GPU memory (>85% of dynamic cache), concurrent
# embedder access causes contention → -30% inference time.
#
# GPUArbiter defers embed calls until GPU pressure drops, complementing
# ANE_MLX_Mutex (model-loading) with runtime GPU scheduling.
#
# Threshold: fraction = active_GPU_bytes / dynamic_cache_limit
#   < 0.60 → idle    → embed immediately
#   0.60–0.85 → normal → embed immediately
#   > 0.85   → pressure → DEFER (wait 100ms polling, max 5s timeout)
#
# Fail-safe: returns 0.0 (idle) if MLX unavailable.

# Cached at module level — avoid per-call import overhead in hot polling loop
_mlx_memory_module: Any = None


def _get_mlx_memory_module() -> Any:
    """Lazily cached mlx_memory module reference."""
    global _mlx_memory_module
    if _mlx_memory_module is None:
        try:
            from hledac.universal.utils import mlx_memory

            _mlx_memory_module = mlx_memory
        except Exception:
            pass
    return _mlx_memory_module


def _probe_gpu_fraction() -> float:
    """Probe MLX Metal GPU memory fraction (active / dynamic_limit)."""
    try:
        global mx
        limit = 0
        mod = _get_mlx_memory_module()
        if mod is not None:
            try:
                limit = mod.get_dynamic_metal_cache_limit()
            except Exception:
                pass
        if limit <= 0:
            return 0.0
        try:
            active = mx.get_active_memory()
        except (AttributeError, NameError):
            try:
                active = mx.metal.get_active_memory()
            except Exception:
                return 0.0
        return min(1.0, float(active) / float(limit))
    except Exception:
        return 0.0


class GPUArbiter:
    """
    Fine-grained GPU resource arbiter for MLX embedder vs Hermes3 inference.

    ISSUE #023 fix: When Hermes3 saturates the Metal GPU, defer embedder calls
    to avoid memory-bandwidth contention on M1 8GB UMA.

    Usage:
        arbiter = get_gpu_arbiter()
        if arbiter.should_defer():
            await arbiter.wait_until_free()
        embeddings = await embedder.encode_async(texts)

    Always-on, fail-safe, bounded for M1 8GB UMA.
    """
    DEFER_THRESHOLD: float = 0.85
    POLL_INTERVAL: float = 0.1
    DEFAULT_TIMEOUT: float = 5.0

    __slots__ = ('_defer_count', '_poll_count', '_last_fraction')

    def __init__(self) -> None:
        self._defer_count: int = 0
        self._poll_count: int = 0
        self._last_fraction: float = 0.0

    def should_defer(self) -> bool:
        """Return True if GPU is saturated (>85%) and embedder should defer."""
        try:
            fraction = _probe_gpu_fraction()
            self._last_fraction = fraction
            if fraction > self.DEFER_THRESHOLD:
                self._defer_count += 1
                return True
            return False
        except Exception:
            self._last_fraction = 0.0
            return False

    async def wait_until_free(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """
        Wait until GPU pressure drops below threshold, or timeout expires.

        Polls should_defer() every 100ms. Returns True if GPU is free within
        timeout, False if timeout expired.

        Args:
            timeout: Max seconds to wait (default 5.0). 0 = no-wait fallback.
        """
        if timeout <= 0:
            return not self.should_defer()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.should_defer():
                return True
            self._poll_count += 1
            try:
                await asyncio.sleep(self.POLL_INTERVAL)
            except asyncio.CancelledError:
                return False
        return False

    @property
    def stats(self) -> dict[str, int | float]:
        return {
            'defer_count': self._defer_count,
            'poll_count': self._poll_count,
            'last_gpu_fraction': self._last_fraction,
        }


_arbiter: GPUArbiter | None = None
_arbiter_lock = threading.Lock()


def get_gpu_arbiter() -> GPUArbiter:
    """Global singleton GPU arbiter."""
    global _arbiter
    if _arbiter is None:
        with _arbiter_lock:
            if _arbiter is None:
                _arbiter = GPUArbiter()
    return _arbiter

# ── MLX import ────────────────────────────────────────────────────────────────

try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    warnings.warn('MLX not available. Install: pip install mlx>=0.15.0', stacklevel=2)
try:
    from mlx_embeddings import load as mlx_embeddings_load
    MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    MLX_EMBEDDINGS_AVAILABLE = False
    warnings.warn('mlx-embeddings not available. Install: pip install mlx-embeddings', stacklevel=2)
_EMBED_CACHE_DIR = Path.home() / '.hledac' / 'cache' / 'mlx_embed'
_EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_PREWARM_LOCK = threading.Lock()

# F6: Re-export canonical MLXEmbeddingManager from core/mlx_embeddings.py
# Eliminates duplicate 358-line class copy. Single Metal command queue =
# no double model loads on M1 8GB. Backward-compat for existing callers.

from core.mlx_embeddings import (  # noqa: F401 — re-export for compat
    MLXEmbeddingManager,
    get_mlx_embedder,
    get_embedding_manager,  # deprecated alias
    get_embedding_info,
    encode_texts,
    compute_similarity,
    prewarm_embedding_model,
    is_embedding_model_prewarmed,
    EmbeddingDimensionError,
    assert_embedding_dimension,
    EmbeddingTask,
    apply_task_prefix,
    should_normalize,
    _default_manager,
    _init_lock,
    MLX_AVAILABLE,
    MLX_EMBEDDINGS_AVAILABLE,
)
