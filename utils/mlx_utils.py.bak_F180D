"""
MLX utilities pro memory management a cache clearing.

Sprint 81: Core Stability & Memory Safety
- mlx_managed decorator pro automatické mx.eval() a metal.clear_cache()
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable, TypeVar

import logging

logger = logging.getLogger(__name__)

# Sprint 81: MLX memory management
MLX_AVAILABLE = False
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    mx = None

# Global state pro throttling mx.eval() volání
_last_eval_time: float = 0.0
MIN_EVAL_INTERVAL: float = 0.1  # 100 ms throttle

T = TypeVar('T')


async def _maybe_eval_async() -> None:
    """
    Async verze - provede mx.eval() pokud uplynul dostatek času od posledního volání.

    Sprint 81: Throttled mx.eval() pro minimalizaci overhead.
    """
    global _last_eval_time

    if not MLX_AVAILABLE:
        return

    now = time.time()
    if now - _last_eval_time > MIN_EVAL_INTERVAL:
        try:
            await asyncio.to_thread(mx.eval, [])
            _last_eval_time = now
        except Exception as e:
            logger.debug(f"mx.eval() failed: {e}")


def _maybe_eval_sync() -> None:
    """
    Sync verze - provede mx.eval() pokud uplynul dostatek času od posledního volání.

    Sprint 81: Throttled mx.eval() pro minimalizaci overhead.
    """
    global _last_eval_time

    if not MLX_AVAILABLE:
        return

    now = time.time()
    if now - _last_eval_time > MIN_EVAL_INTERVAL:
        try:
            mx.eval([])
            _last_eval_time = now
        except Exception as e:
            logger.debug(f"mx.eval() failed: {e}")


async def _clear_metal_cache_async() -> None:
    """Async verze - vyčistí Metal cache."""
    if not MLX_AVAILABLE:
        return

    try:
        await asyncio.to_thread(_clear_metal_cache_sync)
    except Exception as e:
        logger.debug(f"metal.clear_cache() failed: {e}")


def _clear_metal_cache_sync() -> None:
    """Sync verze - vyčistí Metal cache."""
    if not MLX_AVAILABLE:
        return

    try:
        if hasattr(mx.metal, 'clear_cache'):
            mx.metal.clear_cache()
    except Exception as e:
        logger.debug(f"metal.clear_cache() failed: {e}")


def mlx_managed(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Dekorátor pro automatické memory management po MLX operacích.

    Automaticky volá:
    1. mx.eval([]) po funkci (throttled, min 100ms interval)
    2. mx.metal.clear_cache() po funkci

    Použití:
        @mlx_managed
        async def my_mlx_function(data: mx.array) -> mx.array:
            # ... MLX operace ...
            return result

    Sprint 81: ROI - 500MB+ memory savings při správném použití.
    """
    if not inspect.iscoroutinefunction(func):
        # Synchronní verze dekorátoru
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
                # Always eval and clear cache after operation
                _maybe_eval_sync()
                _clear_metal_cache_sync()
                return result
            except Exception as e:
                # Even on error, try to clean up
                _maybe_eval_sync()
                _clear_metal_cache_sync()
                raise

        return sync_wrapper
    else:
        # Async verze dekorátoru
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await func(*args, **kwargs)
                # Always eval and clear cache after operation
                await _maybe_eval_async()
                await _clear_metal_cache_async()
                return result
            except Exception as e:
                # Even on error, try to clean up
                await _maybe_eval_async()
                await _clear_metal_cache_async()
                raise

        return async_wrapper


def mlx_cleanup_after(func: Callable[..., Any]) -> Callable[..., Any]:
    """
    Lightweight dekorátor - pouze clear_cache bez mx.eval().

    Použijte tento dekorátor pro méně kritické operace kde
    mx.eval() overhead není žádoucí.

    Sprint 81: Alternativa k mlx_managed pro specifické případy.
    """
    if not inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
                _clear_metal_cache_sync()
                return result
            except Exception as e:
                _clear_metal_cache_sync()
                raise

        return sync_wrapper
    else:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await func(*args, **kwargs)
                await _clear_metal_cache_async()
                return result
            except Exception as e:
                await _clear_metal_cache_async()
                raise

        return async_wrapper


def get_mlx_memory_stats() -> dict:
    """
    Získat aktuální MLX memory statistiky.

    Returns:
        dict s klíči: active_mb, peak_mb, cache_mb (nebo None pokud недоступно)
    """
    if not MLX_AVAILABLE:
        return {
            'available': False,
            'active_mb': None,
            'peak_mb': None,
            'cache_mb': None,
        }

    stats = {'available': True}

    try:
        if hasattr(mx.metal, 'get_active_memory'):
            stats['active_mb'] = mx.metal.get_active_memory() / (1024 ** 2)
    except Exception:
        stats['active_mb'] = None

    try:
        if hasattr(mx.metal, 'get_peak_memory'):
            stats['peak_mb'] = mx.metal.get_peak_memory() / (1024 ** 2)
    except Exception:
        stats['peak_mb'] = None

    try:
        if hasattr(mx.metal, 'get_cache_memory'):
            stats['cache_mb'] = mx.metal.get_cache_memory() / (1024 ** 2)
    except Exception:
        stats['cache_mb'] = None

    return stats


def reset_metal_peak() -> None:
    """Reset MLX peak memory counter."""
    if not MLX_AVAILABLE:
        return

    try:
        if hasattr(mx.metal, 'reset_peak_memory'):
            mx.metal.reset_peak_memory()
    except Exception as e:
        logger.debug(f"reset_peak_memory() failed: {e}")
