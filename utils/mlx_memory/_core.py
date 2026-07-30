"""
utils/mlx_memory/_core.py — MLX Runtime delegation (M-11 refactor)

Tento modul nyní DELEGUJE veškerou logiku na utils.mlx_cache.

Přehled rolí po M-11:
  - utils.mlx_cache:     Metal limits, cleanup sequencing, memory poller (CANONICAL)
  - utils.mlx_memory:    Re-export pro zpětnou kompatibilitu + mlx_memory-specific API
  - brain._hermes_cache:  HermesModelCache singleton pro LLM inference (SINGLETON)
  - core.embeddings.pool: Embedding worker thread + Metal limits delegation

Funkce v tomto modulu nyní delegují na mlx_cache kromě:
  - get_mlx_memory_pressure() — mlx_memory-specific API pro mlx_bridge
  - get_metal_stream_context() — thread-local stream context

API (vše deleguje na mlx_cache kromě výše uvedených):
  MLX_AVAILABLE, init_mlx_buffers, configure_mlx_limits,
  clear_mlx_cache, get_mlx_memory_*, mlx_cleanup_sync,
  mlx_cleanup_aggressive, get_dynamic_metal_cache_limit,
  get_metal_limits_status, get_metal_stream_context,
  evict_all, get_cache_stats, get_semaphore

Model caching: Používej brain._hermes_cache.hermes_cache() — jediný canonical entry point.
"""

import asyncio
import functools
import gc
import importlib.util
import logging
import sys
import threading
import time as _time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from hledac.universal.utils.locks import LazyAsyncioLock

from hledac.universal.core.locks import LockCategory, register_lock

if TYPE_CHECKING:
    from collections.abc import Awaitable

logger = logging.getLogger(__name__)

# ── MLX Availability Detection ────────────────────────────────────────────────

def _detect_mlx_available() -> bool:
    """Return True only if mlx package is installed (no module import — ISSUE 3.2 fix).

    Uses importlib.metadata instead of find_spec — find_spec loads mlx.core on macOS
    which violates PLANNER: ZERO MLX when these modules are imported by planners.
    """
    try:
        import importlib.metadata
        importlib.metadata.version("mlx")
        return True
    except Exception:
        return False


MLX_AVAILABLE: bool = _detect_mlx_available()

# P3-03 FIX: Module-level cached reference replaces sys.modules.get() lookup.
# In can_afford_sync, get_mx() is called 6× per check (hasattr×4, eval, metal).
# sys.modules dict lookup is ~100ns but adds up in tight MLX inference loops.
# Pattern matches resource_governor._get_mx() — one import on first use.
_mx_module: Any = None


def get_mx():
    """
    Lazy accessor for mlx.core module — cached after first import.
    Returns the mlx.core module object if available, otherwise None.

    P3-03 FIX: Replaced sys.modules.get() with module-level cached reference.
    Previously did dict lookup on every call — now O(1) with no module traversal.
    """
    global _mx_module
    if _mx_module is None and MLX_AVAILABLE:
        import mlx.core as _mx_module
    return _mx_module


# Sentinel for unset lazy module reference
_MISSING = object()


# ---------------------------------------------------------------------------
# Centralized lazy-accessor for mlx_memory package.
# Replaces per-class _get_mlx_memory() lazy-import patterns (ISSUE-F330-DUP).
# ---------------------------------------------------------------------------

_mlx_memory_module: Any = _MISSING


def get_mlx_memory_module() -> Any:
    """
    Lazy accessor for the mlx_memory package.

    Avoids import at module load time. Returns the mlx_memory module
    or None if unavailable.

    Canonical replacement for per-class lazy-import patterns:
        # BEFORE (duplicated in 3 files):
        def _get_mlx_memory(self):
            if self._mlx_memory is None:
                try:
                    from hledac.universal.utils import mlx_memory
                    self._mlx_memory = mlx_memory
                except ImportError:
                    self._mlx_memory = None
            return self._mlx_memory

        # AFTER (centralized):
        mlx_mem = get_mlx_memory_module()
    """
    global _mlx_memory_module
    if _mlx_memory_module is _MISSING:
        try:
            from hledac.universal.utils import mlx_memory as _mod
            _mlx_memory_module = _mod
        except ImportError:
            _mlx_memory_module = None
    return _mlx_memory_module


# ── UMA Budget Constants ───────────────────────────────────────────────────────

try:
    _MLX_BUDGET_GIB: float = 6.25
    MLX_WARNING_GIB: float = _MLX_BUDGET_GIB * 0.8
    MLX_CRITICAL_GIB: float = _MLX_BUDGET_GIB * 0.9
    MAX_MEMORY_MB: int = int(_MLX_BUDGET_GIB * 1024)  # 6_400 MB
except Exception:
    MLX_WARNING_GIB = 5.0
    MLX_CRITICAL_GIB = 5.625
    MAX_MEMORY_MB = 6_400

# ── Metal Memory Constants ─────────────────────────────────────────────────────

_METAL_WIRED_LIMIT_BYTES: int = 768 * 1024 * 1024  # 768 MiB, fixed

_METAL_CACHE_LIMIT_BYTES: int = int(1.5 * 1024 * 1024 * 1024)  # 1.5 GiB ceiling

_EMERGENCY_FLOOR_BYTES: int = 256 * 1024 * 1024  # 256 MiB

# For test surface
_METAL_CACHE_LIMIT_BYTES = _METAL_CACHE_LIMIT_BYTES
_METAL_WIRED_LIMIT_BYTES = _METAL_WIRED_LIMIT_BYTES

# ── Internal State ─────────────────────────────────────────────────────────────

_mlx_initialized: bool = False
_mlx_init_lock = threading.Lock()
register_lock(LockCategory.CACHE, _mlx_init_lock, "mlx_memory._mlx_init_lock")
_mlx_metal_limits_lock = threading.Lock()
register_lock(LockCategory.CACHE, _mlx_metal_limits_lock, "mlx_memory._mlx_metal_limits_lock")
_last_setter_error: str | None = None
_cache_limit_actual: int | None = None
_wired_limit_actual: int | None = None


def _format_limit_mib(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value // 1024 ** 2} MiB"


# ── Memory Metrics ──────────────────────────────────────────────────────────────

def _ensure_mlx() -> bool:
    """Ensure MLX core is available."""
    return MLX_AVAILABLE


def get_mlx_active_memory_mb() -> int | None:
    """Get active MLX memory in MB."""
    if not _ensure_mlx():
        return None
    mx = get_mx()
    if mx is None:
        return None
    try:
        if hasattr(mx, "get_active_memory"):
            return int(mx.get_active_memory()) // (1024 * 1024)
        if hasattr(mx, "metal") and hasattr(mx.metal, "get_active_memory"):
            return int(mx.metal.get_active_memory()) // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_mlx_active_memory_mb failed: {e}")
    return None


def get_mlx_peak_memory_mb() -> int | None:
    """Get peak MLX memory in MB."""
    if not _ensure_mlx():
        return None
    mx = get_mx()
    if mx is None:
        return None
    try:
        if hasattr(mx, "get_peak_memory"):
            return int(mx.get_peak_memory()) // (1024 * 1024)
        if hasattr(mx, "metal") and hasattr(mx.metal, "get_peak_memory"):
            return int(mx.metal.get_peak_memory()) // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_mlx_peak_memory_mb failed: {e}")
    return None


def get_mlx_cache_memory_mb() -> int | None:
    """Get MLX cache memory in MB."""
    if not _ensure_mlx():
        return None
    mx = get_mx()
    if mx is None:
        return None
    try:
        if hasattr(mx, "get_cache_memory"):
            return int(mx.get_cache_memory()) // (1024 * 1024)
        if hasattr(mx, "metal") and hasattr(mx.metal, "get_cache_memory"):
            return int(mx.metal.get_cache_memory()) // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_mlx_cache_memory_mb failed: {e}")
    return None


def get_mlx_memory_pressure() -> tuple[int, str]:
    """Return (usage_pct, level) where level is NORMAL|WARNING|CRITICAL."""
    if not _ensure_mlx():
        return 0, "UNKNOWN"
    try:
        active = get_mlx_active_memory_mb() or 0
        usage_pct = int((active / MAX_MEMORY_MB) * 100)
        if usage_pct >= 90:
            return usage_pct, "CRITICAL"
        elif usage_pct >= 80:
            return usage_pct, "WARNING"
        return usage_pct, "NORMAL"
    except Exception as e:
        logger.debug(f"get_mlx_memory_pressure failed: {e}")
        return 0, "UNKNOWN"


def get_mlx_memory_metrics() -> dict[str, Any]:
    """Convenience reporter for all MLX memory metrics."""
    if not _ensure_mlx():
        return {
            "available": False,
            "active_mb": None,
            "peak_mb": None,
            "cache_mb": None,
            "pressure_pct": 0,
            "pressure_level": "UNKNOWN",
        }
    active = get_mlx_active_memory_mb()
    peak = get_mlx_peak_memory_mb()
    cache = get_mlx_cache_memory_mb()
    pressure_pct, pressure_level = get_mlx_memory_pressure()
    return {
        "available": True,
        "active_mb": active,
        "peak_mb": peak,
        "cache_mb": cache,
        "pressure_pct": pressure_pct,
        "pressure_level": pressure_level,
    }


def format_mlx_memory_snapshot() -> dict[str, Any]:
    """Get a complete MLX memory snapshot."""
    if not _ensure_mlx():
        return {"available": False, "active_mb": None, "peak_mb": None, "cache_mb": None, "pressure_pct": 0, "pressure_level": "UNKNOWN"}
    active = get_mlx_active_memory_mb()
    peak = get_mlx_peak_memory_mb()
    cache = get_mlx_cache_memory_mb()
    pressure_pct, pressure_level = get_mlx_memory_pressure()
    return {"available": True, "active_mb": active, "peak_mb": peak, "cache_mb": cache, "pressure_pct": pressure_pct, "pressure_level": pressure_level}


# ── Metal Limits ────────────────────────────────────────────────────────────────

def _has_metal_api() -> bool:
    mx = get_mx()
    return mx is not None and hasattr(mx, "metal")


def get_dynamic_metal_cache_limit() -> int:
    """
    Dynamic Metal cache limit: 20% of available UMA, clamp [256MiB, 1.5GiB].
    Called by init_mlx_buffers; not for direct use by callers.
    """
    try:
        from hledac.universal.utils.uma_budget import get_uma_usage_mb

        total_mb = get_uma_usage_mb()
        if total_mb is None:
            return int(1.0 * 1024 * 1024 * 1024)
        available_gb = (total_mb) / 1024.0
        raw = available_gb * 0.20
        clamped = max(min(raw, 1.5), 0.25)
        return int(clamped * 1024 * 1024 * 1024)
    except Exception:
        return int(1.0 * 1024 * 1024 * 1024)  # 1 GiB fallback


def get_metal_limits_status() -> dict[str, Any]:
    """Diagnostic surface for metal limit configuration status."""
    return {
        "cache_limit_actual_mib": _cache_limit_actual // (1024 * 1024) if _cache_limit_actual else None,
        "wired_limit_actual_mib": _wired_limit_actual // (1024 * 1024) if _wired_limit_actual else None,
        "last_setter_error": _last_setter_error,
        "dynamic_cache_limit_mib": get_dynamic_metal_cache_limit() // (1024 * 1024),
        "wired_limit_mib": _METAL_WIRED_LIMIT_BYTES // (1024 * 1024),
    }


def _apply_metal_limits_impl(cache_limit_bytes: int, wired_limit_bytes: int) -> dict[str, Any]:
    """Apply Metal limits. Called only from init_mlx_buffers under lock."""
    global _last_setter_error, _cache_limit_actual, _wired_limit_actual
    mx = get_mx()
    if mx is None:
        return {"success": False, "error": "MLX not available"}

    result: dict[str, Any] = {
        "success": True,
        "cache_limit_bytes": cache_limit_bytes,
        "wired_limit_bytes": wired_limit_bytes,
        "cache_configured": False,
        "wired_configured": False,
    }

    # Cache limit
    try:
        if hasattr(mx, "set_cache_limit"):
            mx.set_cache_limit(cache_limit_bytes)
        elif hasattr(mx.metal, "set_cache_limit"):
            mx.metal.set_cache_limit(cache_limit_bytes)
        result["cache_configured"] = True
        _cache_limit_actual = cache_limit_bytes
    except Exception as e:
        _last_setter_error = str(e)
        result["cache_error"] = str(e)

    # Wired limit
    try:
        if hasattr(mx, "set_wired_limit"):
            mx.set_wired_limit(wired_limit_bytes)
        elif hasattr(mx.metal, "set_wired_limit"):
            mx.metal.set_wired_limit(wired_limit_bytes)
        result["wired_configured"] = True
        _wired_limit_actual = wired_limit_bytes
    except Exception as e:
        _last_setter_error = str(e)
        result["wired_error"] = str(e)

    return result


def configure_mlx_limits(cache_limit_mb: int = 1536, memory_limit_mb: int | None = None) -> dict[str, Any]:
    """
    Configure MLX cache and memory limits for M1 8GB.
    Returns dict with success status and any errors.
    """
    mx = get_mx()
    if mx is None:
        return {"success": False, "error": "MLX not available"}

    result: dict[str, Any] = {
        "success": True,
        "cache_limit_mb": cache_limit_mb,
        "memory_limit_mb": memory_limit_mb,
    }

    cache_bytes = cache_limit_mb * 1024 * 1024
    with _mx_metal_limits_lock:
        impl = _apply_metal_limits_impl(cache_bytes, _METAL_WIRED_LIMIT_BYTES)
        result.update(impl)

    if memory_limit_mb is not None:
        try:
            if hasattr(mx, "set_memory_limit"):
                mx.set_memory_limit(memory_limit_mb * 1024 * 1024)
                result["memory_configured"] = True
        except Exception as e:
            result["memory_error"] = str(e)

    return result


# ── Initialization ─────────────────────────────────────────────────────────────

def init_mlx_buffers() -> dict[str, Any]:
    """
    Initialize MLX Metal memory limits.

    DO NOT call at module import time — importing utils.mlx_memory must not
    import mlx.core or configure Metal limits. Call explicitly when MLX is
    about to be used.

    M1 8GB: dynamic cache ceiling 1.5 GiB, wired 768 MiB fixed.
    """
    global _mlx_initialized

    if not MLX_AVAILABLE:
        return {"success": False, "error": "MLX not available"}

    mx = get_mx()
    if mx is None:
        return {"success": False, "error": "MLX core unavailable"}

    with _mx_metal_limits_lock:
        if _mlx_initialized:
            return {"success": True, "initialized": True}

        dynamic_limit = get_dynamic_metal_cache_limit()
        impl = _apply_metal_limits_impl(dynamic_limit, _METAL_WIRED_LIMIT_BYTES)
        _mlx_initialized = True
        return impl


# ── Cleanup ────────────────────────────────────────────────────────────────────

_debounce_last_clear: float = 0.0
_DEBOUNCE_SECONDS: float = 0.5


def clear_mlx_cache() -> bool:
    """
    Canonical Metal cache clear — delegates to mlx_cleanup_sync().

    Sequence (per GHOST_INVARIANTS.md:80): gc.collect() → mx.eval([]) →
    mx.clear_cache() → gc.collect()

    F330-DUP: this was the legacy duplicate implementation. Now delegates
    to mlx_cleanup_sync() which is the single canonical source of truth.
    """
    if not MLX_AVAILABLE:
        return False
    try:
        mlx_cleanup_sync()
        return True
    except Exception as e:
        logger.debug(f"clear_mlx_cache: mlx_cleanup_sync failed: {e}")
        return False


def clear_mlx_cache_debounced(min_interval_seconds: float = _DEBOUNCE_SECONDS) -> bool:
    """Clear MLX cache with debounce to prevent rapid repeated clears."""
    global _debounce_last_clear
    now = _time.monotonic()
    if now - _debounce_last_clear < min_interval_seconds:
        return False
    _debounce_last_clear = now
    return clear_mlx_cache()


def set_cache_limit_with_debounce(limit_mb: int, min_interval_seconds: float = 1.0) -> dict[str, Any]:
    """Set MLX cache limit with debounce protection."""
    global _debounce_last_clear
    now = _time.monotonic()
    if now - _debounce_last_clear < min_interval_seconds:
        return {"success": False, "error": "debounced", "cache_limit_mb": limit_mb}
    _debounce_last_clear = now
    return configure_mlx_limits(cache_limit_mb=limit_mb)


def safe_clear_metal_cache() -> bool:
    """
    Alias for clear_mlx_cache() for backward compatibility.

    U2-04 FIX: This function is a public API entry point. Some callers
    invoke it directly without knowing it delegates to mlx_cleanup_sync().
    We add the mx.eval([]) barrier here directly so that even if the
    delegation chain changes, this remains correct.

    The barrier ensures GPU queue is flushed before clear_cache() is called,
    otherwise Metal cache is NOT actually released (MLX lazy eval).
    """
    if not MLX_AVAILABLE:
        return False
    mx = get_mx()
    if mx is None:
        return False
    try:
        mx.eval([])  # U2-04: barrier — flush GPU queue before clear_cache
    except Exception as _e:
        logger.warning(f"[CRITICAL] safe_clear_metal_cache mx.eval([]) barrier failed: {_e}")
    return clear_mlx_cache()


def safe_set_cache_limit(bytes_limit: int) -> bool:
    """Set Metal cache limit. Returns True on success."""
    if not MLX_AVAILABLE:
        return False
    mx = get_mx()
    if mx is None:
        return False
    try:
        if hasattr(mx, "set_cache_limit"):
            mx.set_cache_limit(bytes_limit)
            return True
        if hasattr(mx, "metal") and hasattr(mx.metal, "set_cache_limit"):
            mx.metal.set_cache_limit(bytes_limit)
            return True
    except Exception as e:
        logger.debug(f"safe_set_cache_limit({bytes_limit}) failed: {e}")
    return False


def safe_get_cache_limit() -> int | None:
    """Get current Metal cache limit. Returns None on failure."""
    if not MLX_AVAILABLE:
        return None
    mx = get_mx()
    if mx is None:
        return None
    try:
        if hasattr(mx, "get_cache_limit"):
            return int(mx.get_cache_limit())
        if hasattr(mx, "metal") and hasattr(mx.metal, "get_cache_limit"):
            return int(mx.metal.get_cache_limit())
    except Exception:
        pass
    return None


# ── Slab Pool Cleanup Integration ─────────────────────────────────────────────

def _release_slab_pool() -> None:
    """Called by mlx_cleanup_sync to release slab pool memory."""
    try:
        from ..mlx_memory import _slab as _slab_mod
        _slab_mod.release_slab_pool()
    except Exception:
        pass


def metal_reclaim() -> None:
    """
    M5: Canonical defensive Metal hygiene — single entry point for all gc+eval+clear calls.

    Call ONLY at:
      1. Model swap (deephermes3_engine swap path)
      2. Sprint winddown (sprint_scheduler run_winddown)
      3. RSS > soft ceiling (memory_coordinator check)

    NEVER call ad-hoc in recon/retry loops — MEM-2 pattern.

    Sequence (GHOST_INVARIANT: F183C):
      1. gc.collect()          — release Python refs to MLX objects FIRST
      2. mx.eval([])           — barrier: flush GPU queue BEFORE clear_cache
      3. clear_cache()         — release Metal cache
      4. get_dynamic_metal_cache_limit() — recompute ceiling from current UMA state
      5. safe_set_cache_limit() — apply dynamic ceiling
      6. gc.collect()          — second pass for circular refs created during Metal free
    """
    if not MLX_AVAILABLE:
        return
    try:
        gc.collect()
        try:
            get_mx().eval([])
        except Exception as _e:
            logger.warning(f"[CRITICAL] mx.eval([]) barrier failed: {_e}")
        mx = get_mx()
        if mx is None:
            return
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
        # MEM-2: dynamic cache ceiling after reclaim
        new_limit = get_dynamic_metal_cache_limit()
        safe_set_cache_limit(new_limit)
        gc.collect()
    except Exception as e:
        logger.debug(f"metal_reclaim non-critical: {e}")


def mlx_cleanup_sync() -> None:
    """
    Sync cleanup – always call in thread executor (never asyncio.run).

    F183C canonical cleanup order:
      1. gc.collect() — release Python refs to MLX objects FIRST
      2. mx.eval([])  — barrier: flush GPU queue BEFORE clear_cache
      3. clear_cache() — release Metal cache
      4. gc.collect()  — second pass for circular refs created during Metal free
      5. slab pool release
    """
    if not MLX_AVAILABLE:
        return
    try:
        gc.collect()
        try:
            get_mx().eval([])
        except Exception as _e:
            logger.warning(f"[CRITICAL] mx.eval([]) barrier failed: {_e}")
        mx = get_mx()
        if mx is None:
            return
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()
        gc.collect()
        _release_slab_pool()
    except Exception as e:
        logger.debug(f"MLX cleanup non-critical: {e}")


def mlx_cleanup_aggressive() -> None:
    """
    Aggressive cleanup — sets cache to 64MB floor then restores limits.
    Use during EMERGENCY memory pressure.
    """
    if not MLX_AVAILABLE:
        return
    mx = get_mx()
    if mx is None:
        return
    old_limit: int | None = None
    try:
        if hasattr(mx, "get_cache_limit"):
            old_limit = int(mx.get_cache_limit())
        elif hasattr(mx, "metal") and hasattr(mx.metal, "get_cache_limit"):
            old_limit = int(mx.metal.get_cache_limit())
    except Exception:
        pass

    try:
        floor = _EMERGENCY_FLOOR_BYTES
        if hasattr(mx, "set_cache_limit"):
            mx.set_cache_limit(floor)
        elif hasattr(mx, "metal") and hasattr(mx.metal, "set_cache_limit"):
            mx.metal.set_cache_limit(floor)

        gc.collect()
        try:
            mx.eval([])
        except Exception as e:
            logger.debug(f"[MLX] mx.eval([]) barrier skipped: {e}")
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "clear_cache"):
            mx.metal.clear_cache()

        if old_limit is not None:
            if hasattr(mx, "set_cache_limit"):
                mx.set_cache_limit(old_limit)
            elif hasattr(mx, "metal") and hasattr(mx.metal, "set_cache_limit"):
                mx.metal.set_cache_limit(old_limit)
    except Exception:
        pass


# ── Metal Stream Context ───────────────────────────────────────────────────────

# ISSUE-026: threading.local is CORRECT here — not contextvars.
# Thread-bound resources (MX stream, event loop) use threading.local.
# Task-bound state in async code uses contextvars.ContextVar (see resource_governor.py).
# Reason: this function runs on a dedicated single-task thread (_compile_executor),
# never handling multiple asyncio Tasks concurrently.
_tls = threading.local()


def get_metal_stream_context():
    """
    Return a thread-local mx.stream(gpu) context manager.
    M1 8GB: cached per-thread, prevents "Stream(gpu,1) not in current thread" errors
    when MLX is called from worker threads (MLXWorkerThread, asyncio.to_thread).
    NOTE: threading.local is intentional — dedicated thread, not shared async pool.
    """
    mx = get_mx()
    if mx is None:
        return __import__("contextlib").nullcontext()
    if not hasattr(mx, "stream"):
        return __import__("contextlib").nullcontext()
    stream = getattr(_tls, "stream", None)
    if stream is None:
        try:
            gpu = mx.Stream(mx.gpu)
            _tls.stream = gpu
            stream = gpu
        except Exception:
            return __import__("contextlib").nullcontext()
    try:
        return mx.stream(mx.gpu)
    except Exception:
        return __import__("contextlib").nullcontext()


# ── Model Cache (LRU, max 2 models) ──────────────────────────────────────────

_MLX_CACHE: dict[str, tuple[Any, Any]] = {}
_MLX_CACHE_MAX = 2
_MLX_CACHE_LIMIT = _METAL_CACHE_LIMIT_BYTES
_MLX_WIRED_LIMIT = _METAL_WIRED_LIMIT_BYTES

_mlx_cache_lock = LazyAsyncioLock()
_mlx_evict_lock = threading.Lock()
register_lock(LockCategory.CACHE, _mlx_evict_lock, "mlx_memory._mlx_evict_lock")

# Concurrency control
_MLX_SEMAPHORE: asyncio.Semaphore | None = None
_MLX_SEMAPHORE_INIT = threading.Lock()
register_lock(LockCategory.CACHE, _MLX_SEMAPHORE_INIT, "mlx_memory._MLX_SEMAPHORE_INIT")


def _get_cache_lock() -> LazyAsyncioLock:
    """Get or create the cache async lock."""
    global _mlx_cache_lock
    return _mlx_cache_lock


class ConcurrencyCategory:
    MLX_INFERENCE = "mlx_inference"


def get_semaphore() -> asyncio.Semaphore:
    """Get the shared MLX inference semaphore (max 1 concurrent inference)."""
    global _MLX_SEMAPHORE
    if _MLX_SEMAPHORE is None:
        with _MLX_SEMAPHORE_INIT:
            if _MLX_SEMAPHORE is None:
                _MLX_SEMAPHORE = asyncio.Semaphore(1)
    return _MLX_SEMAPHORE


def get_semaphore_for_testing(category: str) -> asyncio.Semaphore:
    """Test hook for semaphore creation."""
    return asyncio.Semaphore(1)


# Cache hit/miss metrics
_CACHE_HITS: int = 0
_CACHE_MISSES: int = 0


async def get_mlx_model(model_name: str) -> tuple[Any, Any]:
    """
    DEPRECATED — M-11: Use brain._hermes_cache.hermes_cache() instead.

    This function is kept for backward compatibility only.
    Delegates to the HermesModelCache singleton.

    LRU eviction when cache exceeds max 2 models.
    """
    import warnings

    warnings.warn(
        "get_mlx_model() is deprecated — use brain._hermes_cache.hermes_cache() instead. "
        "This function will be removed in a future release.",
        DeprecationWarning,
        stacklevel=2,
    )
    # Lazy import to avoid circular dependency
    from hledac.universal.brain._hermes_cache import hermes_cache

    cache = hermes_cache()
    result = cache.get_model(model_name)
    if result is not None:
        return result

    # Not cached — load and put
    try:
        from mlx_lm import load as mlx_load

        model, tokenizer, *_ = await asyncio.to_thread(mlx_load, model_name)
        cache.put_model(model_name, model, tokenizer)
        return model, tokenizer
    except Exception as e:
        logger.warning(f"Failed to load MLX model {model_name}: {e}")
        return None, None


# ── MLX Utilities (from mlx_utils.py) ─────────────────────────────────────────

import functools
import inspect
from collections.abc import Callable
from typing import TypeVar

_MIN_EVAL_INTERVAL: float = 0.05  # 50ms throttle
_last_eval_time: float = 0.0


def _get_mlx_safe():
    """Safe lazy accessor for mlx.core (fallback None)."""
    if not MLX_AVAILABLE:
        return None
    return sys.modules.get("mlx.core")


async def _maybe_eval_async() -> None:
    """Throttled mx.eval([]) to prevent excessive GPU sync."""
    global _last_eval_time
    mx = _get_mlx_safe()
    if mx is None:
        return
    now = _time.monotonic()
    if now - _last_eval_time > _MIN_EVAL_INTERVAL:
        try:
            await asyncio.to_thread(mx.eval, [])
            _last_eval_time = now
        except Exception as e:
            logger.debug(f"_maybe_eval_async: mx.eval([]) failed: {e}")


def _maybe_eval_sync() -> None:
    """Synchronous throttled mx.eval([])."""
    global _last_eval_time
    mx = _get_mlx_safe()
    if mx is None:
        return
    now = _time.monotonic()
    if now - _last_eval_time > _MIN_EVAL_INTERVAL:
        try:
            mx.eval([])
            _last_eval_time = now
        except Exception as e:
            logger.debug(f"_maybe_eval_sync: mx.eval([]) failed: {e}")


async def _clear_metal_cache_async() -> None:
    """Async wrapper around safe_clear_metal_cache()."""
    if not MLX_AVAILABLE:
        return
    mx = _get_mlx_safe()
    if mx is None:
        return
    try:
        await asyncio.to_thread(safe_clear_metal_cache)
    except Exception as e:
        logger.debug(f"_clear_metal_cache_async: failed: {e}")


def _clear_metal_cache_sync() -> None:
    """Sync wrapper around safe_clear_metal_cache()."""
    if not MLX_AVAILABLE:
        return
    safe_clear_metal_cache()


Fn = TypeVar("Fn", bound=Callable[..., Any])


def mlx_managed(func: Fn) -> Fn:
    """
    Decorator: auto mx.eval([]) + clear_cache() after MLX operation.

    Sync function → _maybe_eval_sync() + _clear_metal_cache_sync()
    Async function → await _maybe_eval_async() + await _clear_metal_cache_async()
    """
    if not inspect.iscoroutinefunction(func):
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
                # U2-01 FIX: offload sync MLX ops to thread — prevents event-loop stall.
                # asyncio.to_thread() is non-blocking; the thread runs in background.
                try:
                    _loop = asyncio.get_running_loop()
                except RuntimeError:
                    # No event loop (e.g. bare thread) — fall back to direct call.
                    # This is safe: we're already in a sync function, no event loop to block.
                    _maybe_eval_sync()
                    _clear_metal_cache_sync()
                else:
                    # Schedule cleanup in background thread; don't await it here.
                    _loop.run_in_executor(None, _sync_cleanup_sequence)
                return result
            except Exception:
                # U2-01 FIX: cleanup on exception path too — no await, same offload.
                try:
                    _loop = asyncio.get_running_loop()
                except RuntimeError:
                    _clear_metal_cache_sync()
                else:
                    _loop.run_in_executor(None, _clear_metal_cache_sync)
                raise

        return sync_wrapper  # type: ignore[return-value]
    else:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await func(*args, **kwargs)
                await _maybe_eval_async()
                await _clear_metal_cache_async()
                return result
            except Exception:
                await _clear_metal_cache_async()
                raise

        return async_wrapper  # type: ignore[return-value]


def _sync_cleanup_sequence() -> None:
    """U2-01 FIX: sequential sync cleanup for executor offload (no async)."""
    _maybe_eval_sync()
    _clear_metal_cache_sync()


def mlx_cleanup_after(func: Fn) -> Fn:
    """
    Decorator: cleanup after function (eval + clear) regardless of outcome.
    U2-01 FIX: sync functions offload cleanup to executor thread.
    """
    if not inspect.iscoroutinefunction(func):
        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = func(*args, **kwargs)
                try:
                    _loop = asyncio.get_running_loop()
                except RuntimeError:
                    _sync_cleanup_sequence()
                else:
                    _loop.run_in_executor(None, _sync_cleanup_sequence)
                return result
            except Exception:
                try:
                    _loop = asyncio.get_running_loop()
                except RuntimeError:
                    _clear_metal_cache_sync()
                else:
                    _loop.run_in_executor(None, _clear_metal_cache_sync)
                raise

        return sync_wrapper  # type: ignore[return-value]
    else:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await func(*args, **kwargs)
                await _maybe_eval_async()
                await _clear_metal_cache_async()
                return result
            except Exception:
                await _clear_metal_cache_async()
                raise

        return async_wrapper  # type: ignore[return-value]


def get_mlx_memory_stats() -> dict[str, Any]:
    """Získat aktuální MLX memory statistiky."""
    mx = _get_mlx_safe()
    if mx is None:
        return {"available": False, "active_mb": None, "peak_mb": None, "cache_mb": None}

    stats: dict[str, Any] = {"available": True}

    try:
        if hasattr(mx, "get_active_memory"):
            stats["active_mb"] = mx.get_active_memory() / (1024 ** 2)
    except Exception:
        stats["active_mb"] = None

    try:
        if hasattr(mx, "get_peak_memory"):
            stats["peak_mb"] = mx.get_peak_memory() / (1024 ** 2)
        elif hasattr(mx, "metal") and hasattr(mx.metal, "get_peak_memory"):
            stats["peak_mb"] = mx.metal.get_peak_memory() / (1024 ** 2)
    except Exception:
        stats["peak_mb"] = None

    try:
        if hasattr(mx, "get_cache_memory"):
            stats["cache_mb"] = mx.get_cache_memory() / (1024 ** 2)
        elif hasattr(mx, "metal") and hasattr(mx.metal, "get_cache_memory"):
            stats["cache_mb"] = mx.metal.get_cache_memory() / (1024 ** 2)
    except Exception:
        stats["cache_mb"] = None

    return stats


def reset_metal_peak() -> None:
    """Reset MLX peak memory counter."""
    mx = _get_mlx_safe()
    if mx is None:
        return
    try:
        if hasattr(mx, "reset_peak_memory"):
            mx.reset_peak_memory()
        elif hasattr(mx, "metal") and hasattr(mx.metal, "reset_peak_memory"):
            mx.metal.reset_peak_memory()
    except Exception as e:
        logger.debug(f"reset_peak_memory() failed: {e}")


def evict_all() -> None:
    """Synchronous eviction of entire MLX model cache (safe from any thread)."""
    global _MLX_CACHE
    with _mlx_evict_lock:
        _MLX_CACHE.clear()
        logger.info("MLX cache evicted via evict_all()")


def get_cache_stats() -> dict[str, Any]:
    """Get model cache statistics including hit/miss metrics."""
    total = _CACHE_HITS + _CACHE_MISSES
    hit_rate = _CACHE_HITS / total if total > 0 else 0.0
    return {
        "size": len(_MLX_CACHE),
        "max": _MLX_CACHE_MAX,
        "models": list(_MLX_CACHE.keys()),
        "cache_hits": _CACHE_HITS,
        "cache_misses": _CACHE_MISSES,
        "hit_rate": hit_rate,
    }
