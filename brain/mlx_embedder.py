"""
MLX embedding backend — Apple Silicon native, unified memory, py3.14 compatible.
Priority: MLX (ANE/GPU unified) → CoreML HTTP → ONNX CPU → hash fallback.
No py3.12 subprocess, no CoreML conversion required.
"""
from __future__ import annotations


import asyncio
import logging
import time as time_module
from typing import TYPE_CHECKING, Awaitable, Callable

import numpy as np


class AdaptiveEmbeddingBatcher:
    """
    Streaming batcher with dynamic memory pressure feedback (Issue #23).

    Unlike static batching, this adjusts batch size BETWEEN sub-batch calls
    based on real-time memory pressure readings.

    Always-on, fail-safe, bounded for M1 8GB UMA.

    Usage:
        batcher = AdaptiveEmbeddingBatcher(
            initial_batch_size=32,
            min_batch_size=4,
            max_batch_size=128,
        )
        results = await batcher.process(
            texts,
            embedder,
            memory_provider=scheduler._sample_memory_pressure,
        )
    """

    __slots__ = (
        "_initial_batch_size",
        "_min_batch_size",
        "_max_batch_size",
        "_pressure_high",
        "_pressure_low",
        "_scale_up_factor",
        "_scale_down_factor",
        "_stats",
    )

    def __init__(
        self,
        initial_batch_size: int = 32,
        min_batch_size: int = 4,
        max_batch_size: int = 128,
        *,
        pressure_high: float = 0.80,
        pressure_low: float = 0.50,
        scale_up_factor: float = 1.5,
        scale_down_factor: float = 0.5,
    ) -> None:
        self._initial_batch_size = initial_batch_size
        self._min_batch_size = min_batch_size
        self._max_batch_size = max_batch_size
        self._pressure_high = pressure_high
        self._pressure_low = pressure_low
        self._scale_up_factor = scale_up_factor
        self._scale_down_factor = scale_down_factor
        self._stats: dict[str, int | float] = {
            "batches_processed": 0,
            "memory_pressure_events": 0,
            "total_texts": 0,
            "peak_batch_size": initial_batch_size,
            "min_batch_size_used": initial_batch_size,
        }

    def _record_batch_size(self, batch_size: int) -> None:
        self._stats["peak_batch_size"] = max(self._stats["peak_batch_size"], batch_size)
        self._stats["min_batch_size_used"] = min(self._stats["min_batch_size_used"], batch_size)

    async def process(
        self,
        texts: list[str],
        embedder: "MLXEmbedder",
        memory_provider: Callable[[], Awaitable[float]] | Callable[[], float],
    ) -> list[list[float]]:
        """
        Process all texts with dynamic batch sizing.

        Memory pressure is checked BEFORE each sub-batch, enabling
        mid-stream batch size adjustment (Issue #23 fix).
        """
        if not texts:
            return []

        self._stats["total_texts"] = len(texts)
        results: list[list[float]] = []
        current_batch_size = self._initial_batch_size
        i = 0

        while i < len(texts):
            # === Issue #23 fix: check memory pressure BEFORE each sub-batch ===
            try:
                pressure_val = memory_provider()
                if asyncio.iscoroutine(pressure_val):
                    pressure = await pressure_val
                else:
                    pressure = pressure_val
            except Exception:
                pressure = 0.5  # fail-safe: neutral pressure

            # Dynamic batch size adjustment based on real-time pressure
            if pressure >= self._pressure_high:
                new_size = max(
                    self._min_batch_size,
                    int(current_batch_size * self._scale_down_factor),
                )
                if new_size < current_batch_size:
                    current_batch_size = new_size
                    self._stats["memory_pressure_events"] += 1
                    logger.debug(
                        "[AdaptiveBatcher] Pressure %.2f → shrinking to %d",
                        pressure,
                        current_batch_size,
                    )
            elif pressure <= self._pressure_low and current_batch_size < self._max_batch_size:
                new_size = min(
                    self._max_batch_size,
                    int(current_batch_size * self._scale_up_factor),
                )
                if new_size > current_batch_size:
                    current_batch_size = new_size
                    logger.debug(
                        "[AdaptiveBatcher] Pressure %.2f → growing to %d",
                        pressure,
                        current_batch_size,
                    )

            self._record_batch_size(current_batch_size)

            # Execute sub-batch
            batch = texts[i : i + current_batch_size]
            try:
                batch_result = await embedder.encode_batch(batch)
                if hasattr(batch_result, "tolist"):
                    results.extend(batch_result.tolist())
                else:
                    results.extend(batch_result)
            except Exception as e:
                logger.warning("[AdaptiveBatcher] Batch encode failed: %s", e)
                zero_emb = [0.0] * 384
                results.extend([zero_emb] * len(batch))

            i += current_batch_size
            self._stats["batches_processed"] += 1

        return results

    @property
    def stats(self) -> dict[str, int | float]:
        """Return batching statistics for telemetry."""
        return dict(self._stats)

logger = logging.getLogger(__name__)

# ── MLX availability (lazy — no top-level import) ─────────────────────────────
_MLX_AVAILABLE = False
try:
    import mlx.core as mx

    _MLX_AVAILABLE = True
except ImportError:
    mx = None  # type: ignore[assignment]

if TYPE_CHECKING:
    pass

_MLX_EMBEDDINGS_AVAILABLE = False
try:
    from mlx_embedding_models.embedding import EmbeddingModel

    _MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    EmbeddingModel = None  # type: ignore[assignment]

# ── Constants ───────────────────────────────────────────────────────────────────
_MODEL_ID = "BAAI/bge-small-en-v1.5"
_EMBED_DIM = 384
_BATCH_SIZE = 64  # MLX unified memory — higher batch than CoreML
# Adaptive batching thresholds (Sprint F265D)
_BATCH_SIZE_HIGH = 128  # NORMAL memory pressure
_BATCH_SIZE_LOW = 32  # CRITICAL memory pressure


class MLXEmbedder:
    """
    MLX-native embedder — runs directly in py3.14 on Apple Silicon.
    No subprocess, no HTTP bridge, no conversion.

    Adaptive batch sizing (Sprint F265D):
    - NORMAL memory: batch_size=128
    - WARNING memory: batch_size=64
    - CRITICAL memory: batch_size=32
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._is_loaded = False
        self._mlx_memory = None  # Lazy import for adaptive batching

    @property
    def is_available(self) -> bool:
        return _MLX_EMBEDDINGS_AVAILABLE

    async def load(self) -> bool:
        if self._is_loaded:
            return True
        if not _MLX_EMBEDDINGS_AVAILABLE:
            logger.warning("[MLX] mlx-embedding-models not available")
            return False
        try:
            loop = asyncio.get_running_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: EmbeddingModel.from_registry(_MODEL_ID),
            )
            self._is_loaded = True
            logger.info("[MLX] Embedder loaded — unified memory, no conversion needed")
            return True
        except Exception as e:
            logger.warning("[MLX] Load failed: %s", e)
            return False

    def _get_mlx_memory(self):
        """Lazy-load mlx_memory module for adaptive batching (Sprint F265D)."""
        if self._mlx_memory is None:
            try:
                from hledac.universal.utils import mlx_memory
                self._mlx_memory = mlx_memory
            except ImportError:
                self._mlx_memory = None
        return self._mlx_memory

    def _get_adaptive_batch_size(self) -> int:
        """
        Sprint F265D: Return adaptive batch size based on Metal memory pressure.

        Memory pressure tiers:
        - NORMAL (<80% of budget): _BATCH_SIZE_HIGH (128)
        - WARNING (80-90%): _BATCH_SIZE (64)
        - CRITICAL (>90%): _BATCH_SIZE_LOW (32)

        Returns:
            Adaptive batch size in range [_BATCH_SIZE_LOW, _BATCH_SIZE_HIGH].
        """
        mlx_mem = self._get_mlx_memory()
        if mlx_mem is None:
            return _BATCH_SIZE

        try:
            _, pressure_level = mlx_mem.get_mlx_memory_pressure()
        except Exception:
            return _BATCH_SIZE

        if pressure_level == "NORMAL":
            return _BATCH_SIZE_HIGH
        elif pressure_level == "WARNING":
            return _BATCH_SIZE
        else:  # CRITICAL or UNKNOWN
            return _BATCH_SIZE_LOW

    async def encode_batch(
        self,
        texts: str | list[str],
        batch_size: int | None = None,
    ) -> np.ndarray:
        """
        Encode batch with adaptive sizing based on Metal memory (Sprint F265D).

        Args:
            texts: Text(s) to encode.
            batch_size: Override batch size. If None, uses adaptive sizing.
        """
        if isinstance(texts, str):
            texts = [texts]
        if not texts or not self._is_loaded:
            return np.zeros((len(texts), _EMBED_DIM), dtype=np.float32)

        # Sprint F265D: Use adaptive batch sizing if no override
        effective_batch_size = batch_size if batch_size is not None else self._get_adaptive_batch_size()

        loop = asyncio.get_running_loop()
        all_embs: list[np.ndarray] = []
        for i in range(0, len(texts), effective_batch_size):
            batch = texts[i : i + effective_batch_size]
            embs = await loop.run_in_executor(
                None,
                lambda b=batch: np.array(self._model.encode(b)),
            )
            all_embs.append(embs)

        result = np.vstack(all_embs).astype(np.float32)
        norms = np.linalg.norm(result, axis=1, keepdims=True)
        return result / (norms + 1e-8)

    def unload(self) -> None:
        self._model = None
        self._is_loaded = False
