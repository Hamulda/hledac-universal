"""
brain/lora_manager.py — Sprint G2: LoRA Manager
=========================================



Extracted from DeepHermes3Engine to reduce complexity.
Manages LoRA adapter lifecycle, caching, and application.

Responsibilities:
- Lazy LoRA adapter loading with bounded LRU cache
- Async and sync adapter application
- LoRA KV cache size reduction (8192 → 4096)
- Statistics tracking for cache hits/misses/evictions

M1 8GB: LoRA adapters occupy ~50-200 MB Metal SRAM.
KV cache is halved when LoRA is active to stay within budget.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from core import aclose

logger = logging.getLogger(__name__)


@dataclass
class LoRAStats:
    """LoRA cache and application statistics."""
    lora_cache_hits: int = 0
    lora_cache_misses: int = 0
    lora_cache_evictions: int = 0
    lora_applications: int = 0


class LoRAManager:
    """
    Manages LoRA adapter lifecycle with lazy loading and LRU caching.

    Extracted from DeepHermes3Engine for better separation of concerns.
    Thread-compatible: async methods for event loop, sync for emergencies.
    """

    def __init__(self) -> None:
        self._adapter_path: str | None = None
        self._stats = LoRAStats()

    @property
    def active_adapter(self) -> str | None:
        """Currently active LoRA adapter path, or None for base model."""
        return self._adapter_path

    @property
    def stats(self) -> LoRAStats:
        """Get LoRA statistics."""
        return self._stats

    def apply_lora_adapter(self, adapter_path: str | None, model: Any, cache: Any) -> None:
        """
        Synchronously apply or swap LoRA adapter (sync wrapper).

        For non-async contexts. Prefer apply_lora_adapter_async.

        Args:
            adapter_path: Path to LoRA adapter safetensors, or None for base model
            model: Base MLX model
            cache: HermesModelCache singleton for LRU cache
        """
        if adapter_path == self._adapter_path:
            return
        if adapter_path is None:
            self._adapter_path = None
            logger.debug('[LoRA] Switched to base model (no adapter)')
            return

        lora_result = cache.get_lora(adapter_path)
        if lora_result is not None:
            self._adapter_path = adapter_path
            self._stats.lora_cache_hits += 1
            logger.debug(f'[LoRA] Cache hit (LRU updated): {adapter_path}')
            return

        try:
            import mlx_lm
            logger.info(f'[LoRA] Loading adapter: {adapter_path}')
            lora_model, lora_tokenizer = mlx_lm.lora.load_lora_model(model, adapter_path)
            cache.put_lora(adapter_path, lora_model, lora_tokenizer)
            self._adapter_path = adapter_path
            self._stats.lora_cache_misses += 1
            logger.info(f'[LoRA] Adapter loaded and cached: {adapter_path}')
        except Exception as e:
            logger.warning(f'[LoRA] Failed to load adapter {adapter_path}: {e}')
            self._adapter_path = None

    async def apply_lora_adapter_async(
        self,
        adapter_path: str | None,
        model: Any,
        cache: Any,
    ) -> None:
        """
        Async version of apply_lora_adapter.

        Wraps mlx_lm.lora.load_lora_model() in asyncio.to_thread
        to avoid blocking the event loop.

        Args:
            adapter_path: Path to LoRA adapter, or None for base model
            model: Base MLX model
            cache: HermesModelCache singleton
        """
        if adapter_path == self._adapter_path:
            return
        if adapter_path is None:
            self._adapter_path = None
            logger.debug('[LoRA] Switched to base model (no adapter)')
            return

        lora_result = cache.get_lora(adapter_path)
        if lora_result is not None:
            self._adapter_path = adapter_path
            self._stats.lora_cache_hits += 1
            logger.debug(f'[LoRA] Cache hit (LRU updated): {adapter_path}')
            return

        try:
            import mlx_lm
            logger.info(f'[LoRA] Loading adapter: {adapter_path}')
            lora_model, lora_tokenizer = await asyncio.to_thread(
                mlx_lm.lora.load_lora_model, model, adapter_path
            )
            cache.put_lora(adapter_path, lora_model, lora_tokenizer)
            self._adapter_path = adapter_path
            self._stats.lora_cache_misses += 1
            logger.info(f'[LoRA] Adapter loaded and cached: {adapter_path}')
        except Exception as e:
            logger.warning(f'[LoRA] Failed to load adapter {adapter_path}: {e}')
            self._adapter_path = None

    def unload_all(self, cache: Any) -> None:
        """
        Evict all LoRA adapters from cache and reset active adapter.

        Args:
            cache: HermesModelCache singleton
        """
        cache.clear_loras()
        self._adapter_path = None
        logger.debug('[LoRA] All adapters unloaded')

    def get_kv_kwargs_adjustment(self, base_kwargs: dict) -> dict:
        """
        Adjust KV cache kwargs when LoRA is active.

        LoRA adapters occupy ~50-200 MB Metal SRAM.
        Reduce max_kv_size from 8192→4096 (or half) to compensate.

        Args:
            base_kwargs: Original kwargs dict with max_kv_size

        Returns:
            Modified kwargs with reduced max_kv_size
        """
        if self._adapter_path is None:
            return base_kwargs

        if 'max_kv_size' not in base_kwargs:
            return base_kwargs

        current_size = base_kwargs.get('max_kv_size', 8192)
        reduced_size = max(2048, current_size // 2)
        self._stats.lora_applications += 1
        logger.debug(f'[LoRA] KV cache reduced: {current_size} → {reduced_size} (LoRA active)')
        return {**base_kwargs, 'max_kv_size': reduced_size}

    def reset(self) -> None:
        """Reset manager state (for testing)."""
        self._adapter_path = None
        self._stats = LoRAStats()
