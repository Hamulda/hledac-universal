"""
MLX Cache - Shared LRU cache for MLX models and semaphore for inference.

Provides:
- LRU cache with max 2 models (Mamba2 + Qwen)
- Shared semaphore for limiting concurrent MLX inference to 1
- Thread-safe async access with lazy initialization
"""

import asyncio
import importlib.util
import logging
import threading
from collections import OrderedDict
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)

# ── MLX Availability Detection ──────────────────────────────────────────────
# Safe runtime detection: does NOT import mlx.core, only checks spec existence.
# This ensures importing mlx_cache never crashes when mlx is absent.


def _detect_mlx_available() -> bool:
    """Return True only if mlx.core is importable (spec found, not None)."""
    try:
        spec = importlib.util.find_spec("mlx.core")
        return spec is not None
    except (ValueError, ModuleNotFoundError, ImportError):
        # ModuleNotFoundError: parent 'mlx' package doesn't exist
        # ImportError: mlx exists but mlx.core submodule not found
        return False


MLX_AVAILABLE: bool = _detect_mlx_available()

# LRU cache for MLX models (max 2 models)
_MLX_CACHE: OrderedDict[str, Tuple[Any, Any]] = OrderedDict()
_MLX_CACHE_MAX = 2

# Lazy locks
_MLX_CACHE_LOCK: Optional[asyncio.Lock] = None
_MLX_SEMAPHORE: Optional[asyncio.Semaphore] = None

# Synchronní lock pro evict_all (nezávislý na asyncio lock)
_MLX_EVICT_LOCK = threading.Lock()

# Sprint F206J: P1-14 - Cache hit/miss metrics
_CACHE_HITS = 0
_CACHE_MISSES = 0


def _get_cache_lock() -> asyncio.Lock:
    """Get or create the cache lock (lazy initialization)."""
    global _MLX_CACHE_LOCK
    if _MLX_CACHE_LOCK is None:
        _MLX_CACHE_LOCK = asyncio.Lock()
    return _MLX_CACHE_LOCK


def get_mlx_semaphore() -> asyncio.Semaphore:
    """
    Get or create the shared semaphore for MLX inference.

    Limits concurrent MLX inference to 1 to prevent memory overflow on M1 8GB.
    """
    global _MLX_SEMAPHORE
    if _MLX_SEMAPHORE is None:
        _MLX_SEMAPHORE = asyncio.Semaphore(1)
    return _MLX_SEMAPHORE


async def get_mlx_model(model_name: str) -> Tuple[Any, Any]:
    """
    Get MLX model and tokenizer from cache or load from disk.

    Uses LRU eviction when cache exceeds max 2 models.

    Args:
        model_name: The model identifier (e.g., 'mlx-community/mamba2-370m-4bit')

    Returns:
        Tuple of (model, tokenizer) or (None, None) on failure
    """
    async with _get_cache_lock():
        # Check cache first
        if model_name in _MLX_CACHE:
            _MLX_CACHE.move_to_end(model_name)
            _CACHE_HITS += 1
            logger.debug(f"MLX cache hit: {model_name}")
            return _MLX_CACHE[model_name]
        _CACHE_MISSES += 1

        # Try to load model
        try:
            from mlx_lm import load as mlx_load
            loop = asyncio.get_running_loop()

            logger.info(f"Loading MLX model: {model_name}")
            model, tokenizer = await loop.run_in_executor(
                None,
                mlx_load,
                model_name
            )

            # Add to cache with LRU eviction
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
    """Clear the MLX model cache."""
    global _MLX_CACHE
    _MLX_CACHE.clear()
    logger.info("MLX cache cleared")


def evict_all() -> None:
    """Synchronní vyčištění celé cache (bezpečné z jakéhokoli vlákna)."""
    global _MLX_CACHE
    with _MLX_EVICT_LOCK:
        _MLX_CACHE.clear()
        logger.info("MLX cache evicted via evict_all()")


def get_cache_stats() -> dict:
    """Get cache statistics including hit/miss metrics."""
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
    """Reset cache hit/miss statistics."""
    global _CACHE_HITS, _CACHE_MISSES
    _CACHE_HITS = 0
    _CACHE_MISSES = 0


# =============================================================================
# MLX Cleanup Functions (Sprint 72)
# =============================================================================

import gc

_mx = None  # lazy singleton


def _get_mx():
    """Lazily import mlx.core on first use."""
    global _mx
    if _mx is None:
        import mlx.core as mx_module
        _mx = mx_module
    return _mx


MLX_AVAILABLE = True  # assume available until proven otherwise at runtime

# Sprint 8T: MLX Metal memory limits for M1 8GB — ONE authoritative module
#
# Memory budget on M1 8GB Unified Memory Architecture:
#   OS + kernel reserve        ~2.0 GiB
#   Python + packages          ~1.0 GiB
#   DuckDB (RAM disk)          ~0.5 GiB
#   LMDB + graph structures    ~0.75 GiB
#   Metal cache limit          ~2.0 GiB  ← kv_cache workspace on GPU (fits 3B-4bit)
#   Metal wired limit          ~0.5 GiB  ← pinned Metal memory (cannot be swapped)
#   Headroom for model weights  ~1.25 GiB (mlx-lm lazy-loads into GPU)
#
# Both limits use bytes as the native API unit (verified via inspect.signature
# on darwin with mlx.core.metal.set_cache_limit / set_wired_limit).
_METAL_CACHE_LIMIT_BYTES = int(2.0 * 1024 ** 3)   # 2.0 GiB — fits 3B-4bit model
_METAL_WIRED_LIMIT_BYTES = int(0.5 * 1024 ** 3)   # 0.5 GiB — pinned Metal memory

# Public aliases for test surface (Sprint 7B / 6B probes)
_MLX_CACHE_LIMIT = _METAL_CACHE_LIMIT_BYTES
_MLX_WIRED_LIMIT = _METAL_WIRED_LIMIT_BYTES

# Thread-safe one-time init infrastructure
_MLX_METAL_LIMITS_CONFIGURED = False
_MLX_METAL_LIMITS_LOCK = threading.Lock()
_MLX_INITIALIZED = False

# Diagnostic surface for setter failures
_last_setter_error: Optional[str] = None
_cache_limit_actual: Optional[int] = None
_wired_limit_actual: Optional[int] = None


def _format_limit_mib(value: Optional[int]) -> str:
    """Format a memory limit in MiB for safe logging."""
    if value is None:
        return "unavailable"
    return f"{value // 1024 ** 2} MiB"


def _ensure_metal_memory_limits() -> bool:
    """
    Ensure Metal memory limits are set exactly once per process (thread-safe).

    Uses double-checked locking:
      1. Fast path: check _MLX_METAL_LIMITS_CONFIGURED without lock
      2. Slow path: acquire lock, re-check, then call set_cache_limit + set_wired_limit

    Limiting to 2.5 GiB each prevents Metal from hogging the unified-memory bus
    and leaves headroom for mlx-lm to lazy-load model weights on demand.

    Returns:
        True if limits are now configured (or were already configured), False on failure.
    """
    global _MLX_METAL_LIMITS_CONFIGURED, _last_setter_error, _cache_limit_actual, _wired_limit_actual

    # ── Fast path ────────────────────────────────────────────────────────────
    if _MLX_METAL_LIMITS_CONFIGURED:
        return True

    # ── Slow path: thread-safe one-time init ─────────────────────────────────
    with _MLX_METAL_LIMITS_LOCK:
        # Re-check after acquiring lock (another thread may have set it)
        if _MLX_METAL_LIMITS_CONFIGURED:
            return True

        try:
            mx = _get_mx()
        except Exception as e:
            _last_setter_error = f"mlx.core import failed: {e}"
            logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
            # Fail-open: MLX may not be available on non-Apple platforms
            _MLX_METAL_LIMITS_CONFIGURED = True   # mark configured to skip retries
            return False

        if not hasattr(mx, 'metal'):
            _last_setter_error = "mx.metal namespace missing"
            logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
            _MLX_METAL_LIMITS_CONFIGURED = True
            return False

        errors = []

        # ── set_cache_limit ───────────────────────────────────────────────────
        if hasattr(mx, 'set_cache_limit'):
            try:
                mx.set_cache_limit(_METAL_CACHE_LIMIT_BYTES)
                _cache_limit_actual = _METAL_CACHE_LIMIT_BYTES
            except Exception as e:
                _last_setter_error = f"set_cache_limit failed: {e}"
                errors.append(_last_setter_error)
                logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
        elif hasattr(mx.metal, 'set_cache_limit'):
            try:
                mx.metal.set_cache_limit(_METAL_CACHE_LIMIT_BYTES)
                _cache_limit_actual = _METAL_CACHE_LIMIT_BYTES
            except Exception as e:
                _last_setter_error = f"set_cache_limit failed: {e}"
                errors.append(_last_setter_error)
                logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
        else:
            _last_setter_error = "mx.set_cache_limit not available"
            errors.append(_last_setter_error)

        # ── set_wired_limit ────────────────────────────────────────────────────
        if hasattr(mx, 'set_wired_limit'):
            try:
                mx.set_wired_limit(_METAL_WIRED_LIMIT_BYTES)
                _wired_limit_actual = _METAL_WIRED_LIMIT_BYTES
            except Exception as e:
                err = f"set_wired_limit failed: {e}"
                errors.append(err)
                if not _last_setter_error:
                    _last_setter_error = err
                logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {err}")
        elif hasattr(mx.metal, 'set_wired_limit'):
            try:
                mx.metal.set_wired_limit(_METAL_WIRED_LIMIT_BYTES)
                _wired_limit_actual = _METAL_WIRED_LIMIT_BYTES
            except Exception as e:
                err = f"set_wired_limit failed: {e}"
                errors.append(err)
                if not _last_setter_error:
                    _last_setter_error = err
                logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {err}")
        else:
            _last_setter_error = "mx.set_wired_limit not available"
            errors.append(_last_setter_error)

        if errors:
            # At least one setter was unavailable or failed
            _MLX_METAL_LIMITS_CONFIGURED = True   # mark to prevent retry loops
            return False

        _MLX_METAL_LIMITS_CONFIGURED = True
        _last_setter_error = None
        logger.info(
            f"[Sprint 8T] Metal limits configured: "
            f"cache={_METAL_CACHE_LIMIT_BYTES // 1024**2} MiB, "
            f"wired={_METAL_WIRED_LIMIT_BYTES // 1024**2} MiB"
        )
        return True


def get_metal_limits_status() -> dict:
    """
    Observability surface for Metal memory limit configuration.

    Returns:
        dict with keys:
          - mlx_available: bool — whether mlx.core spec was found at import time
          - configured: bool — whether limits have been initialized
          - cache_limit_bytes: int or None
          - wired_limit_bytes: int or None
          - last_error: str or None
    """
    return {
        "mlx_available": MLX_AVAILABLE,
        "configured": _MLX_METAL_LIMITS_CONFIGURED,
        "cache_limit_bytes": _cache_limit_actual,
        "wired_limit_bytes": _wired_limit_actual,
        "last_error": _last_setter_error,
    }


def init_mlx_buffers() -> bool:
    """
    Initialize MLX buffer limits for M1 8GB.

    Sprint 8T: Delegates to _ensure_metal_memory_limits() which sets
    cache_limit and wired_limit to 2.5 GiB each using thread-safe
    double-checked locking.  Must be called before MLX inference to
    ensure proper memory budget.

    Returns:
        True if initialization successful, False otherwise.
        Returns False (no crash) when MLX is unavailable.
    """
    global _MLX_INITIALIZED
    if not MLX_AVAILABLE:
        return False
    if _MLX_INITIALIZED:
        return True

    # Sprint 8T: Metal limit init FIRST, before any buffer/array allocation
    _ensure_metal_memory_limits()

    _MLX_INITIALIZED = True
    status = get_metal_limits_status()
    logger.info(
        f"MLX buffers initialized: cache={_format_limit_mib(status['cache_limit_bytes'])}, "
        f"wired={_format_limit_mib(status['wired_limit_bytes'])}, "
        f"configured={status['configured']}, error={status['last_error']}"
    )
    return True


# DO NOT call init_mlx_buffers() at module import time.
# Importing utils.mlx_cache must not import mlx.core or configure Metal limits.
# Call init_mlx_buffers() explicitly when MLX is about to be used.


def mlx_cleanup_sync() -> None:
    """
    Sync cleanup – vždy v thread executoru.

    F183C: Canonical cleanup order (srovnáno s model_manager + model_lifecycle):
      1. gc.collect() — uvolní Python refs na MLX objekty PRVNÍ
      2. mx.eval([])  — barrier: vyprázdní GPU queue PŘED clear_cache
      3. clear_cache() — uvolní Metal cache

    Dřívější pořadí (clear_cache → gc.collect) bylo špatně: Python objekty držely
    MLX tensory ještě při clear_cache, což mohlo na M1 8GB způsobit brief over-budget.
    """
    if not MLX_AVAILABLE:
        return
    try:
        # Krok 1: Python GC PRVNÍ — uvolní refs na MLX objekty
        gc.collect()

        # Krok 2: mx.eval([]) barrier — vyprázdní GPU queue
        _get_mx().eval([])

        # Krok 3: clear_cache — uvolní Metal cache
        # F185C: metal.clear_cache is canonical MLX API; check it FIRST, reuse mx ref
        mx = _get_mx()
        if hasattr(mx.metal, 'clear_cache'):
            mx.metal.clear_cache()
        elif hasattr(mx, 'clear_cache'):
            mx.clear_cache()
    except Exception as e:
        logger.debug(f"MLX cleanup non-critical: {e}")


def mlx_cleanup_aggressive() -> None:
    """Agresivní cleanup – dočasně sníží cache limit pro uvolnění fragmentace."""
    if not MLX_AVAILABLE:
        return
    try:
        # F185C: cache mx ref once, check mx.* API first (mx.metal.* is deprecated)
        mx = _get_mx()

        # Uložit starý limit — prefer mx.get_cache_limit (canonical) over mx.metal.* (deprecated)
        if hasattr(mx, 'get_cache_limit'):
            old_limit = mx.get_cache_limit()
        elif hasattr(mx.metal, 'get_cache_limit'):
            old_limit = mx.metal.get_cache_limit()
        else:
            old_limit = None

        # Nastavit nízký limit — prefer mx.set_cache_limit over mx.metal.* (deprecated)
        if hasattr(mx, 'set_cache_limit'):
            mx.set_cache_limit(64 * 1024 * 1024)  # 64MB
        elif hasattr(mx.metal, 'set_cache_limit'):
            mx.metal.set_cache_limit(64 * 1024 * 1024)  # 64MB

        # F183C canonical cleanup order: gc.collect() → mx.eval([]) → clear_cache()
        gc.collect()
        try:
            mx.eval([])
        except Exception as e:
            logger.debug(f"[MLX] mx.eval([]) barrier skipped: {e}")
        if hasattr(mx, 'clear_cache'):
            mx.clear_cache()
        elif hasattr(mx.metal, 'clear_cache'):
            mx.metal.clear_cache()

        # Obnovit starý limit
        if old_limit is not None:
            if hasattr(mx, 'set_cache_limit'):
                mx.set_cache_limit(old_limit)
            elif hasattr(mx.metal, 'set_cache_limit'):
                mx.metal.set_cache_limit(old_limit)
    except Exception:
        mlx_cleanup_sync()  # fallback


def mlx_cleanup_decorator(aggressive: bool = False):
    """Dekorátor pro async i sync funkce – přidá cleanup po dokončení."""
    import functools
    import asyncio
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
