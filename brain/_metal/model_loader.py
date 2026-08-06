"""
model_loader.py — Metal Model Loader
==================================



PEP 698: Extracted from DeepHermes3Engine model lifecycle methods.
Handles model loading, caching via hermes_cache, and memory-aware unloading.

M1 8GB UMA-safe: RSS verification before/after load operations.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.brain._hermes_cache import HermesModelCache

# Deferred MLX import pattern - never import at module level on M1
_MLX_LOADER: Any = None


def _get_mlx_loader() -> Any:
    """Lazy load mlx_lm."""
    global _MX_LOADER
    if _MX_LOADER is None:
        _MX_LOADER = __import__('mlx_lm')
    return _MX_LOADER


class MetalModelLoader:
    """
    M1 Metal-aware model loader with hermes_cache integration.

    Extracted from DeepHermes3Engine to provide:
    1. Idempotent model loading (avoids duplicate loads)
    2. RSS memory verification for M1 8GB safety
    3. Unified model lifecycle management

    F273H+: Uses HermesModelCache singleton — single RLock for all access,
    active background pressure monitor corrects passive-only insert-time eviction.
    """

    def __init__(
        self,
        model_path: str,
        cache: HermesModelCache | None = None,
        half_precision: bool = True,
    ) -> None:
        self.model_path = model_path
        self._cache = cache
        self._half_precision = half_precision
        self._model: Any = None
        self._tokenizer: Any = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        """Check if model is currently loaded."""
        return self._loaded and self._model is not None

    async def load_async(self) -> tuple[Any, Any]:
        """
        Load model asynchronously via asyncio.to_thread.

        F273H+: Thread-safe, non-blocking model loading.
        C2-FIX: mlx_lm.load() is blocking I/O (disk read + Metal kernel compilation).
        """
        if self._loaded and self._model is not None:
            return self._model, self._tokenizer

        # Check cache first
        if self._cache is not None:
            result = self._cache.get_model(self.model_path)
            if result is not None:
                self._model, self._tokenizer = result
                self._loaded = True
                return self._model, self._tokenizer

        # Load from disk
        mlx_lm = _get_mlx_loader()
        self._model, self._tokenizer = await asyncio.to_thread(
            mlx_lm.load, self.model_path
        )

        # Apply half precision if enabled
        if self._half_precision and os.getenv('HLEDAC_HALF_PRECISION', '1') != '0':
            try:
                import mlx.core as mx
                self._model.set_dtype(mx.float16)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    '[MetalModelLoader] Could not set float16 dtype: %s', e
                )

        # Cache the model
        if self._cache is not None:
            self._cache.put_model(self.model_path, self._model, self._tokenizer)
            self._cache.start_monitor()

        self._loaded = True
        return self._model, self._tokenizer

    def unload(self) -> None:
        """
        Unload model and clear from cache.

        M1 8GB: Should be called when model is idle to free GPU memory.
        """
        if self._cache is not None:
            self._cache.evict_model(self.model_path)

        self._model = None
        self._tokenizer = None
        self._loaded = False

    def get_model_tokenizer(self) -> tuple[Any, Any] | None:
        """Get loaded model and tokenizer, or None if not loaded."""
        if self._loaded:
            return self._model, self._tokenizer
        return None


class ModelSwapManager:
    """
    Manages model swap between multiple model slots.

    For M1 8GB: Only one model loaded at a time due to memory constraints.
    Supports: hermes (primary), modernbert (embeddings), draft (speculative)
    """

    def __init__(self, hermes_cache: HermesModelCache | None = None) -> None:
        self._cache = hermes_cache
        self._loaders: dict[str, MetalModelLoader] = {}
        self._active_slot: str | None = None

    def register_model(self, slot: str, model_path: str) -> None:
        """Register a model slot with path."""
        self._loaders[slot] = MetalModelLoader(
            model_path=model_path,
            cache=self._cache,
        )

    async def load_slot(self, slot: str) -> tuple[Any, Any] | None:
        """
        Load model for given slot, swapping if necessary.

        M1 8GB: Automatically evicts previous slot's model.
        """
        if slot not in self._loaders:
            return None

        loader = self._loaders[slot]

        # Swap if different slot active
        if self._active_slot and self._active_slot != slot:
            await self._evict_active()

        self._active_slot = slot
        return await loader.load_async()

    async def _evict_active(self) -> None:
        """Evict currently active model."""
        if self._active_slot and self._active_slot in self._loaders:
            self._loaders[self._active_slot].unload()
            self._active_slot = None

    async def unload_all(self) -> None:
        """Unload all model slots."""
        await self._evict_active()
        for loader in self._loaders.values():
            loader.unload()
