"""
MLX Pool — shared worker thread + Metal memory limits for M1 8GB UMA.

Responsibilities:
- Single shared MLX worker thread for all embedding inference
- Metal cache/wired limit configuration (MEM-2 dynamic sizing)
- mlx_cleanup_sync / mlx_cleanup_aggressive canonical cleanup
- LRU cache for MLX LLM models (separate from embedding cache)

Key invariants:
- mx.eval([]) before mx.metal.clear_cache() — always
- Fail-safe: MLX unavailable → no-op, never raises
- Dynamic Metal cache: min(max(available*0.2, 512MiB), 1.5GiB)
- Thread-safe via double-check locking
"""
from __future__ import annotations

import asyncio
import gc
import importlib.util
import logging
import threading
from collections import OrderedDict
from typing import Any

import psutil

logger = logging.getLogger(__name__)

# === Safe MLX detection (no mlx.core at module level) ===
def _detect_mlx_available() -> bool:
    try:
        spec = importlib.util.find_spec("mlx.core")
        return spec is not None
    except (ValueError, ModuleNotFoundError, ImportError):
        return False


MLX_AVAILABLE: bool = _detect_mlx_available()

import sys as _sys


def get_mx():
    """Lazy accessor for mlx.core — never holds module-level reference."""
    if not MLX_AVAILABLE:
        return None
    return _sys.modules.get("mlx.core")


# === LRU cache for MLX LLM models (not embeddings) ===
_MLX_CACHE: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
_MLX_CACHE_MAX = 2

_MLX_CACHE_LOCK: asyncio.Lock | None = None
_MLX_SEMAPHORE: asyncio.Semaphore | None = None
_MLX_EVICT_LOCK = threading.Lock()

_CACHE_HITS = 0
_CACHE_MISSES = 0


def _get_cache_lock() -> asyncio.Lock:
    global _MLX_CACHE_LOCK
    if _MLX_CACHE_LOCK is None:
        _MLX_CACHE_LOCK = asyncio.Lock()
    return _MLX_CACHE_LOCK


def get_mlx_semaphore() -> asyncio.Semaphore:
    """Semaphore limiting concurrent MLX inference to 1 (M1 8GB)."""
    global _MLX_SEMAPHORE
    if _MLX_SEMAPHORE is None:
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        _MLX_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.MLX_INFERENCE)
    return _MLX_SEMAPHORE


async def get_mlx_model(model_name: str) -> tuple[Any, Any]:
    """Get MLX LLM model and tokenizer from cache or load."""
    async with _get_cache_lock():
        if model_name in _MLX_CACHE:
            _MLX_CACHE.move_to_end(model_name)
            global _CACHE_HITS
            _CACHE_HITS += 1
            logger.debug(f"MLX cache hit: {model_name}")
            return _MLX_CACHE[model_name]

        global _CACHE_MISSES
        _CACHE_MISSES += 1

        try:
            from mlx_lm import load as mlx_load
            logger.info(f"Loading MLX model: {model_name}")
            model, tokenizer, *_ = await asyncio.to_thread(
                mlx_load,
                model_name,
            )
            _MLX_CACHE[model_name] = (model, tokenizer)
            if len(_MLX_CACHE) > _MLX_CACHE_MAX:
                evicted_name, _ = _MLX_CACHE.popitem(last=False)
                logger.info(f"MLX cache evicted: {evicted_name}")

            logger.info(f"MLX model loaded and cached: {model_name}")
            return model, tokenizer
        except Exception as e:
            logger.warning(f"Failed to load MLX model {model_name}: {e}")
            return None, None


def clear_mlx_cache() -> None:
    _MLX_CACHE.clear()
    logger.info("MLX cache cleared")


def evict_all() -> None:
    global _MLX_CACHE
    with _MLX_EVICT_LOCK:
        _MLX_CACHE.clear()
        logger.info("MLX cache evicted via evict_all()")


def get_cache_stats() -> dict:
    total = _CACHE_HITS + _CACHE_MISSES
    hit_rate = _CACHE_HITS / total if total > 0 else 0.0
    return {
        "size": len(_MLX_CACHE),
        "max": _MLX_CACHE_MAX,
        "models": list(_MLX_CACHE.keys()),
        "hits": _CACHE_HITS,
        "misses": _CACHE_MISSES,
        "total": total,
        "hit_rate": hit_rate,
    }


def reset_cache_stats() -> None:
    global _CACHE_HITS, _CACHE_MISSES
    _CACHE_HITS = 0
    _CACHE_MISSES = 0


# === Metal memory limits (Sprint 8T / MEM-2) ===
_METAL_CACHE_LIMIT_BYTES = int(1.5 * 1024 ** 3)
_METAL_WIRED_LIMIT_BYTES = int(768 * 1024 ** 2)
_METAL_CACHE_EMERGENCY_FLOOR_BYTES: int = 256 * 1024 * 1024

_MLX_CACHE_LIMIT = _METAL_CACHE_LIMIT_BYTES
_MLX_WIRED_LIMIT = _METAL_WIRED_LIMIT_BYTES

_MLX_METAL_LIMITS_CONFIGURED = False
_MLX_METAL_LIMITS_LOCK = threading.Lock()
_MLX_INITIALIZED = False

_last_setter_error: str | None = None
_cache_limit_actual: int | None = None
_wired_limit_actual: int | None = None


def _format_limit_mib(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value // 1024 ** 2} MiB"


def get_dynamic_metal_cache_limit(uma_state: str | None = None) -> int:
    """
    Compute Metal cache limit dynamically based on available system memory.

    Formula (normal): min(max(available * 0.2, 512 MiB), 1.5 GiB)
    Formula (EMERGENCY): min(max(available * 0.2, 256 MiB), 1.5 GiB)

    M1 8GB budget:
        model(2GB) + KV(0.75GB) + cache(1.1GB) = ~3.85GB MLX footprint
        leaving ~4.15GB for macOS baseline
    """
    emergency_floor = _METAL_CACHE_EMERGENCY_FLOOR_BYTES if uma_state == "emergency" else 512 * 1024 * 1024
    dynamic_ceiling = 1_610_612_736
    try:
        available = psutil.virtual_memory().available
        limit = available * 0.2
        limit = max(limit, emergency_floor)
        limit = min(limit, dynamic_ceiling)
        return int(limit)
    except Exception:
        return dynamic_ceiling


def _ensure_metal_memory_limits() -> bool:
    """Set Metal memory limits exactly once per process (thread-safe double-check)."""
    global _MLX_METAL_LIMITS_CONFIGURED, _last_setter_error, _cache_limit_actual, _wired_limit_actual

    if _MLX_METAL_LIMITS_CONFIGURED:
        return True

    with _MLX_METAL_LIMITS_LOCK:
        if _MLX_METAL_LIMITS_CONFIGURED:
            return True

        try:
            mx = get_mx()
        except Exception as e:
            _last_setter_error = f"mlx.core import failed: {e}"
            logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
            _MLX_METAL_LIMITS_CONFIGURED = True
            return False

        if not hasattr(mx, 'metal'):
            _last_setter_error = "mx.metal namespace missing"
            logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
            _MLX_METAL_LIMITS_CONFIGURED = True
            return False

        errors = []
        dynamic_cache_limit = get_dynamic_metal_cache_limit()

        setter_found = False
        if hasattr(mx, 'set_cache_limit'):
            try:
                mx.set_cache_limit(dynamic_cache_limit)
                _cache_limit_actual = dynamic_cache_limit
                setter_found = True
            except Exception as e:
                _last_setter_error = f"set_cache_limit failed: {e}"
                errors.append(_last_setter_error)
        elif hasattr(mx.metal, 'set_cache_limit'):
            try:
                mx.metal.set_cache_limit(dynamic_cache_limit)
                _cache_limit_actual = dynamic_cache_limit
                setter_found = True
            except Exception as e:
                _last_setter_error = f"set_cache_limit failed: {e}"
                errors.append(_last_setter_error)

        if hasattr(mx, 'set_wired_limit'):
            try:
                mx.set_wired_limit(_METAL_WIRED_LIMIT_BYTES)
                _wired_limit_actual = _METAL_WIRED_LIMIT_BYTES
            except Exception as e:
                err = f"set_wired_limit failed: {e}"
                errors.append(err)
        elif hasattr(mx.metal, 'set_wired_limit'):
            try:
                mx.metal.set_wired_limit(_METAL_WIRED_LIMIT_BYTES)
                _wired_limit_actual = _METAL_WIRED_LIMIT_BYTES
            except Exception as e:
                err = f"set_wired_limit failed: {e}"
                errors.append(err)

        if errors and not setter_found:
            _MLX_METAL_LIMITS_CONFIGURED = True
            return False

        _MLX_METAL_LIMITS_CONFIGURED = True
        _last_setter_error = None
        logger.info(
            f"[Sprint 8T] Metal limits configured: "
            f"cache={dynamic_cache_limit // 1024**2} MiB, "
            f"wired={_METAL_WIRED_LIMIT_BYTES // 1024**2} MiB"
        )
        return True


def get_metal_limits_status() -> dict:
    return {
        "mlx_available": MLX_AVAILABLE,
        "configured": _MLX_METAL_LIMITS_CONFIGURED,
        "cache_limit_bytes": _cache_limit_actual,
        "wired_limit_bytes": _wired_limit_actual,
        "last_error": _last_setter_error,
    }


def reconfigure_metal_cache_limit(uma_state: str | None = None) -> bool:
    """Runtime reconfigure of Metal cache limit on UMA state transitions."""
    global _cache_limit_actual, _last_setter_error

    if not MLX_AVAILABLE:
        return False

    try:
        mx = get_mx()
    except Exception as e:
        _last_setter_error = f"mlx.core import failed: {e}"
        return False

    if not hasattr(mx, 'metal') and not hasattr(mx, 'set_cache_limit'):
        _last_setter_error = "mx.metal namespace or set_cache_limit missing"
        return False

    new_limit = get_dynamic_metal_cache_limit(uma_state)

    try:
        if hasattr(mx, 'set_cache_limit'):
            mx.set_cache_limit(new_limit)
        elif hasattr(mx.metal, 'set_cache_limit'):
            mx.metal.set_cache_limit(new_limit)
        _cache_limit_actual = new_limit
        _last_setter_error = None
        logger.info(f"[F265H] Metal cache reconfigured: {new_limit // 1024**2} MiB (state={uma_state})")
        return True
    except Exception as e:
        _last_setter_error = f"set_cache_limit failed: {e}"
        return False


def init_mlx_buffers() -> bool:
    """
    Initialize MLX buffer limits for M1 8GB.

    Sets:
    - cache_limit: dynamic (20% of available, ceiling 1.5 GiB)
    - wired_limit: fixed 768 MiB

    Thread-safe double-checked locking.
    Returns False when MLX unavailable (never raises).
    """
    global _MLX_INITIALIZED
    if not MLX_AVAILABLE:
        return False
    if _MLX_INITIALIZED:
        return True

    _ensure_metal_memory_limits()
    _MLX_INITIALIZED = True
    status = get_metal_limits_status()
    logger.info(
        f"MLX buffers initialized: cache={_format_limit_mib(status['cache_limit_bytes'])}, "
        f"wired={_format_limit_mib(status['wired_limit_bytes'])}, "
        f"configured={status['configured']}, error={status['last_error']}"
    )
    return True


# === Cleanup canonical ===

def mlx_cleanup_sync() -> None:
    """
    Sync canonical cleanup — always call in thread executor.

    Order:
    1. gc.collect() — release Python refs to MLX objects FIRST
    2. mx.eval([]) — barrier: drain GPU queue BEFORE clear_cache
    3. clear_cache() — release Metal cache

    Previous order (clear_cache → gc.collect) was wrong: Python objects
    still held MLX tensors during clear_cache, causing brief over-budget on M1 8GB.
    """
    if not MLX_AVAILABLE:
        return
    try:
        gc.collect()
        mx = get_mx()
        if mx is None:
            return
        try:
            mx.eval([])
        except Exception as _e:
            logger.warning(f"[CRITICAL] mx.eval([]) barrier failed: {_e}")
        if hasattr(mx, 'clear_cache'):
            mx.clear_cache()
        elif hasattr(mx.metal, 'clear_cache'):
            mx.metal.clear_cache()

        if _release_slab_pool is not None:
            _release_slab_pool()
    except Exception as e:
        logger.debug(f"MLX cleanup non-critical: {e}")


def mlx_cleanup_aggressive() -> None:
    """Aggressive cleanup — temporarily lower cache limit to release fragmentation."""
    if not MLX_AVAILABLE:
        return
    try:
        mx = get_mx()
        if mx is None:
            return

        old_limit = None
        if hasattr(mx, 'get_cache_limit'):
            old_limit = mx.get_cache_limit()
        elif hasattr(mx.metal, 'get_cache_limit'):
            old_limit = mx.metal.get_cache_limit()

        if hasattr(mx, 'set_cache_limit'):
            mx.set_cache_limit(64 * 1024 * 1024)
        elif hasattr(mx.metal, 'set_cache_limit'):
            mx.metal.set_cache_limit(64 * 1024 * 1024)

        gc.collect()
        try:
            mx.eval([])
        except Exception as e:
            logger.debug(f"[MLX] mx.eval([]) barrier skipped: {e}")
        if hasattr(mx, 'clear_cache'):
            mx.clear_cache()
        elif hasattr(mx.metal, 'clear_cache'):
            mx.metal.clear_cache()

        if old_limit is not None:
            if hasattr(mx, 'set_cache_limit'):
                mx.set_cache_limit(old_limit)
            elif hasattr(mx.metal, 'set_cache_limit'):
                mx.metal.set_cache_limit(old_limit)
    except Exception:
        mlx_cleanup_sync()


# F269: Metal slab pool
try:
    from hledac.universal.utils.metal_slab_pool import release_slab_pool as _release_slab_pool
except ImportError:
    _release_slab_pool: None = None  # type: ignore


def mlx_cleanup_decorator(aggressive: bool = False):
    """Decorator for async/sync functions — adds cleanup after completion."""
    import functools
    import inspect

    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            finally:
                if aggressive:
                    await asyncio.to_thread(mlx_cleanup_aggressive)
                else:
                    await asyncio.to_thread(mlx_cleanup_sync)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            finally:
                if aggressive:
                    mlx_cleanup_aggressive()
                else:
                    mlx_cleanup_sync()

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator
