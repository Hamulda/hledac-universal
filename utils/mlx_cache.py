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
from hledac.universal.utils.lru_cache import LRUCache
from typing import Any

from hledac.universal.core.psutil_shim import psutil

logger = logging.getLogger(__name__)

# ── MLX Availability Detection ──────────────────────────────────────────────
# Safe runtime detection: does NOT import mlx.core, only checks spec existence.
# This ensures importing mlx_cache never crashes when mlx is absent.


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

import sys as _sys  # noqa: E402


def get_mx():
    """
    Lazy accessor for mlx.core module — never holds a module-level reference.
    Returns the mlx.core module object if available, otherwise None.

    Usage pattern:
        mx = get_mx()
        if mx is None:
            return fallback_result
        arr = mx.array([1, 2, 3])
    """
    if not MLX_AVAILABLE:
        return None
    return _sys.modules.get("mlx.core")

# LRU cache for MLX models (max 2 models)
_MLX_CACHE: LRUCache[str, tuple[Any, Any]] = LRUCache(max_size=2)
_MLX_CACHE_MAX = 2

# Lazy locks
_MLX_CACHE_LOCK: asyncio.Lock | None = None
_MLX_SEMAPHORE: asyncio.Semaphore | None = None

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
        from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
        _MLX_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.MLX_INFERENCE)
    return _MLX_SEMAPHORE


async def get_mlx_model(model_name: str) -> tuple[Any, Any]:
    """
    DEPRECATED — M-11: Use brain._hermes_cache.hermes_cache() instead.

    This function is kept for backward compatibility only.
    Delegates to the HermesModelCache singleton.

    Uses LRU eviction when cache exceeds max 2 models.

    Args:
        model_name: The model identifier (e.g., 'mlx-community/mamba2-370m-4bit')

    Returns:
        Tuple of (model, tokenizer) or (None, None) on failure
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

        logger.info(f"Loading MLX model: {model_name}")
        model, tokenizer, *_ = await asyncio.to_thread(mlx_load, model_name)
        cache.put_model(model_name, model, tokenizer)
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

import gc  # noqa: E402

_mx = None  # lazy singleton


def _get_mx():
    """Lazily import mlx.core on first use."""
    global _mx
    if _mx is None:
        import mlx.core as mx_module
        _mx = mx_module
    return _mx


# MLX_AVAILABLE is set at line 37 from _detect_mlx_available()
# Do NOT reassign here — _detect_mlx_available() is the single source of truth

# Sprint 8T: MLX Metal memory limits for M1 8GB — ONE authoritative module
#
# Memory budget on M1 8GB Unified Memory Architecture (8 192 MiB total):
#   macOS baseline               ~2.5 GiB
#   Python + packages           ~1.0 GiB
#   DuckDB (in-process)         ~0.5 GiB
#   LMDB + graph structures     ~0.5 GiB
#   ─────────────────────────────────────────
#   Available for MLX            ~3.7 GiB
#   Model (Hermes-3-3B-4bit)   ~2.0 GiB
#   KV cache                    ~0.75 GiB
#   Metal cache (dynamic)        ~0.5–1.1 GiB  ← LRU workspace on GPU (dynamic ceiling 1.5 GiB)
#   Metal wired (fixed)         768 MiB        ← pinned Metal memory (cannot be swapped)
#
# Both limits use bytes as the native API unit (verified via inspect.signature
# on darwin with mlx.core.metal.set_cache_limit / set_wired_limit).
# GHOST_INVARIANT (CLAUDE.md): M1 Metal cache limit 1.5 GiB ceiling on 8GB machines.
#
# F266: 2.5 GiB wired was too aggressive — reduced to 768 MiB for M1 8GB headroom.
# F267: cache ceiling raised from 1 GiB to 1.5 GiB — dynamic cache uses 20% of available.
#
# Dynamic cache formula: min(max(available*0.2, 512MiB), 1.5GiB)
# At boot (~5.5 GiB available): cache ≈ 1.1 GiB → MLX footprint ≈ 3.85 GiB,
# leaving ~4.15 GiB for macOS → stays in warn zone, not critical.
_METAL_CACHE_LIMIT_BYTES = int(1.5 * 1024 ** 3)   # 1.5 GiB — ceiling for dynamic cache
_METAL_WIRED_LIMIT_BYTES = int(768 * 1024 ** 2)  # 768 MiB — fixed pinned Metal memory

# F265H: EMERGENCY floor — 256 MiB (half of normal 512 MiB floor)
# Gives draft model more Metal memory headroom during EMERGENCY state.
_METAL_CACHE_EMERGENCY_FLOOR_BYTES: int = 256 * 1024 * 1024  # 256 MiB

# Public aliases for test surface (Sprint 7B / 6B probes)
_MLX_CACHE_LIMIT = _METAL_CACHE_LIMIT_BYTES
_MLX_WIRED_LIMIT = _METAL_WIRED_LIMIT_BYTES

# Thread-safe one-time init infrastructure
_MLX_METAL_LIMITS_CONFIGURED = False
_MLX_METAL_LIMITS_LOCK = threading.Lock()
_MLX_INITIALIZED = False

# Diagnostic surface for setter failures
_last_setter_error: str | None = None
_cache_limit_actual: int | None = None
_wired_limit_actual: int | None = None


def _format_limit_mib(value: int | None) -> str:
    """Format a memory limit in MiB for safe logging."""
    if value is None:
        return "unavailable"
    return f"{value // 1024 ** 2} MiB"


# MEM-2: Dynamic Metal cache sizing for M1 8GB stability
# F267: Ceiling raised from 1 GiB to 1.5 GiB — M1 8GB budget allows it:
#   model(2GB) + KV(0.75GB) + cache(1.5GB) = 4.25GB MLX footprint
#   leaving ~3.75GB for macOS baseline (~2.5GB) + apps + GPUComputationSlice
#   At 5.5 GiB available: cache_limit = min(1.1, 1.5) = 1.1 GiB (not capped at 1 GiB)
def get_dynamic_metal_cache_limit(
    uma_state: str | None = None,
    thermal_headroom: float = 1.0,
) -> int:
    """
    Compute Metal cache limit dynamically based on available system memory.

    Formula (normal): min(max(available * 0.2, 512 MiB), 1.5 GiB)
    Formula (EMERGENCY): min(max(available * 0.2, 256 MiB), 1.5 GiB)
    - 20% of available memory (adaptive to workload)
    - Floor: 256 MiB EMERGENCY / 512 MiB normal (ensures minimum caching)
    - Ceiling: 1.5 GiB (M1 8GB safe upper bound, raised from 1 GiB in F267)

    F265H: EMERGENCY floor is 256 MiB — half of normal floor. This gives
    the draft model more Metal memory headroom during EMERGENCY state, trading
    cache for model workspace.

    HW-01 / ISSUE-013: Under thermal pressure, Metal cache is reduced to free
    up memory bandwidth. On M1 MacBook Air (fanless), Metal and CPU share the
    same heatsink — sustained inference at >70°C throttles both. Thermal headroom
    scales the cache ceiling:
      - thermal_headroom >= 0.5: no reduction (nominal operation)
      - 0.3 <= thermal_headroom < 0.5: cache *= 0.5 (mild throttle)
      - thermal_headroom < 0.3: cache *= 0.25 (severe throttle)
    Floor: 256 MiB (never drop below this even in emergency+thermal).

    Args:
        uma_state: Optional UMA state string ("ok"|"soft_warn"|"warn"|"critical"|"emergency").
                   When "emergency", uses 256 MiB floor instead of 512 MiB.
        thermal_headroom: Float 0.0-1.0, where 1.0 = no throttling.
                          On M1 MacBook Air fanless: >0.5 nominal, 0.3-0.5 mild,
                          <0.3 severe.

    Called inside _ensure_metal_memory_limits() so it reflects memory state
    at init time (~5.5 GiB available on 8GB M1 at boot), not at module import.
    Also called by reconfigure_metal_cache_limit() for runtime re-adjustment.
    At 5.5 GiB available: cache_limit = min(1.1, 1.5) = 1.1 GiB
    → model(2GB) + cache(1.1GB) + KV(0.75GB) = ~3.85GB total MLX footprint,
      leaving ~4.15GB for macOS → stays in warn zone, not critical.
    """
    emergency_floor = _METAL_CACHE_EMERGENCY_FLOOR_BYTES if uma_state == "emergency" else 512 * 1024 * 1024
    # F267: 1.5 GiB ceiling (matches _METAL_CACHE_LIMIT_BYTES), not 1 GiB
    dynamic_ceiling = 1_610_612_736  # 1.5 GiB exactly (not 1_073_741_824)
    try:
        available = psutil.virtual_memory().available
        limit = available * 0.2
        limit = max(limit, emergency_floor)  # floor: 256 MiB EMERGENCY / 512 MiB normal
        limit = min(limit, dynamic_ceiling)  # ceiling: 1.5 GiB (M1 8GB safe)

        # HW-01 / ISSUE-013: Thermal headroom feedback — MacBook Air M1 fanless
        # Metal and CPU share heatsink; under throttling, reduce cache to free
        # memory bandwidth for CPU compute rather than GPU caching.
        if thermal_headroom < 0.3:  # Severe throttle (>85°C or worse)
            limit *= 0.25
        elif thermal_headroom < 0.5:  # Mild throttle (>70°C)
            limit *= 0.5

        return int(max(limit, _METAL_CACHE_EMERGENCY_FLOOR_BYTES))  # never below 256 MiB
    except Exception:
        return dynamic_ceiling  # fallback: 1.5 GiB


def _ensure_metal_memory_limits() -> bool:
    """
    Ensure Metal memory limits are set exactly once per process (thread-safe).

    Uses double-checked locking:
      1. Fast path: check _MLX_METAL_LIMITS_CONFIGURED without lock
      2. Slow path: acquire lock, re-check, then call set_cache_limit + set_wired_limit

    Cache limit is DYNAMIC (MEM-2): min(max(available*0.2, 512MiB), 1.5GiB).
    Wired limit stays fixed at 768 MiB (pinned Metal memory, non-swappable).

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
        # MEM-2: dynamic cache limit computed at call time, not at module load
        dynamic_cache_limit = get_dynamic_metal_cache_limit()

        # ── set_cache_limit ───────────────────────────────────────────────────
        if hasattr(mx, 'set_cache_limit'):
            try:
                mx.set_cache_limit(dynamic_cache_limit)
                _cache_limit_actual = dynamic_cache_limit
            except Exception as e:
                _last_setter_error = f"set_cache_limit failed: {e}"
                errors.append(_last_setter_error)
                logger.warning(f"[Sprint 8T] _ensure_metal_memory_limits: {_last_setter_error}")
        elif hasattr(mx.metal, 'set_cache_limit'):
            try:
                mx.metal.set_cache_limit(dynamic_cache_limit)
                _cache_limit_actual = dynamic_cache_limit
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
            f"cache={dynamic_cache_limit // 1024**2} MiB (of {_METAL_CACHE_LIMIT_BYTES // 1024**2} MiB max), "
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


def reconfigure_metal_cache_limit(
    uma_state: str | None = None,
    thermal_headroom: float = 1.0,
) -> bool:
    """
    F265H: Runtime reconfigure of Metal cache limit — called on UMA state transitions.

    This function re-applies the dynamic cache limit formula with the current
    UMA state, allowing the cache to shrink at EMERGENCY (256 MiB floor) and
    restore to normal floors when pressure subsides.

    HW-01 / ISSUE-013: Also applies thermal_headroom feedback to reduce the Metal
    cache ceiling under thermal throttling (MacBook Air M1 fanless).

    Called by the EMERGENCY/CRITICAL callbacks in __main__.py and by the
    resource governor's thermal feedback loop to dynamically adjust the Metal
    cache ceiling based on memory and/or thermal pressure.

    Args:
        uma_state: Current UMA state string ("ok"|"soft_warn"|"warn"|"critical"|"emergency").
                   When None, uses normal 512 MiB floor.
        thermal_headroom: Float 0.0-1.0, where 1.0 = no throttling.
                          On M1 MacBook Air fanless: >0.5 nominal, 0.3-0.5 mild,
                          <0.3 severe.

    Returns:
        True if reconfiguration succeeded, False otherwise.
    """
    global _cache_limit_actual, _last_setter_error

    if not MLX_AVAILABLE:
        return False

    try:
        mx = _get_mx()
    except Exception as e:
        _last_setter_error = f"mlx.core import failed: {e}"
        logger.warning(f"[F265H] reconfigure_metal_cache_limit: {_last_setter_error}")
        return False

    # Compute new limit with current UMA state + thermal headroom
    new_limit = get_dynamic_metal_cache_limit(uma_state, thermal_headroom)

    try:
        # Try mx.set_cache_limit first; fall back to mx.metal.set_cache_limit.
        # The hasattr guards are safety nets — the try/except below is the
        # canonical error handler. Guard uses `or` semantics: fail only if
        # NEITHER mx.set_cache_limit NOR mx.metal.set_cache_limit exists.
        if not hasattr(mx, 'set_cache_limit') and not (
            hasattr(mx, 'metal') and hasattr(mx.metal, 'set_cache_limit')
        ):
            _last_setter_error = "neither mx.set_cache_limit nor mx.metal.set_cache_limit available"
            logger.warning(f"[F265H] reconfigure_metal_cache_limit: {_last_setter_error}")
            return False
        if hasattr(mx, 'set_cache_limit'):
            mx.set_cache_limit(new_limit)
        elif hasattr(mx.metal, 'set_cache_limit'):
            mx.metal.set_cache_limit(new_limit)
        _cache_limit_actual = new_limit
        _last_setter_error = None
        logger.info(
            f"[F265H] Metal cache reconfigured: {new_limit // 1024**2} MiB "
            f"(state={uma_state}, thermal_headroom={thermal_headroom:.2f}, "
            f"floor={_METAL_CACHE_EMERGENCY_FLOOR_BYTES // 1024**2} MiB)"
        )
        return True
    except Exception as e:
        _last_setter_error = f"set_cache_limit failed: {e}"
        logger.warning(f"[F265H] reconfigure_metal_cache_limit: {_last_setter_error}")
        return False


async def async_reconfigure_metal_cache_limit(
    uma_state: str | None = None,
    thermal_headroom: float = 1.0,
) -> bool:
    """
    U2-07 FIX: Async wrapper for reconfigure_metal_cache_limit.

    Offloads the synchronous Metal cache reconfiguration to a thread pool
    to avoid blocking the event loop. Call this from async contexts instead
    of the sync version when adjusting cache limits during runtime.

    HW-01 / ISSUE-013: Applies thermal_headroom feedback in addition to uma_state.

    Args:
        uma_state: Current UMA state string ("ok"|"soft_warn"|"warn"|"critical"|"emergency").
                   When None, uses normal 512 MiB floor.
        thermal_headroom: Float 0.0-1.0, where 1.0 = no throttling.
                          On M1 MacBook Air fanless: >0.5 nominal, 0.3-0.5 mild,
                          <0.3 severe.

    Returns:
        True if reconfiguration succeeded, False otherwise.
    """
    try:
        return await asyncio.to_thread(
            reconfigure_metal_cache_limit, uma_state, thermal_headroom
        )
    except Exception:
        return False


def init_mlx_buffers() -> bool:
    """
    Initialize MLX buffer limits for M1 8GB.

    Sprint 8T: Delegates to _ensure_metal_memory_limits() which sets:
    - cache_limit: dynamic (20% of available, ceiling 1.5 GiB)
    - wired_limit: fixed 768 MiB (pinned Metal memory)
    Uses thread-safe double-checked locking. Must be called before MLX
    inference to ensure proper memory budget.

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
        # F290-FIX: WARNING not DEBUG — clear_cache is no-op without this barrier
        try:
            _get_mx().eval([])
        except Exception as _e:
            logger.warning(f"[CRITICAL] mx.eval([]) barrier failed: {_e} — clear_cache may be no-op on M1")

        # Krok 3: clear_cache — uvolní Metal cache
        # F185C: metal.clear_cache is canonical MLX API; check it FIRST, reuse mx ref
        mx = _get_mx()
        if hasattr(mx, 'clear_cache'):
            mx.clear_cache()

        # F269: Release slab pool memory back to system
        if _release_slab_pool is not None:
            _release_slab_pool()

        # Krok 5: macOS malloc zone pressure relief — uvolní fragmented malloc zones
        # MEM-SYS-001: mx.metal.clear_cache() uvolní Metal cache, ale ne system malloc zone.
        # Dlouhodobé používání může akumulovat fragmentaci v malloc zone, což postupně
        # zvyšuje RAM pressure. malloc_zone_pressure_relief(NULL) releasuje všechny zóny.
        try:
            import ctypes
            libc = ctypes.CDLL(None)
            libc.malloc_zone_pressure_relief(None)
        except Exception as _e:
            logger.debug(f"[MLX] malloc_zone_pressure_relief not available: {_e}")
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

        # F269: Release slab pool memory back to system
        if _release_slab_pool is not None:
            _release_slab_pool()

        # MEM-SYS-001: macOS malloc zone pressure relief — uvolní fragmented malloc zones
        # (synchronizováno s mlx_cleanup_sync pro konzistentní chování)
        try:
            import ctypes
            libc = ctypes.CDLL(None)
            libc.malloc_zone_pressure_relief(None)
        except Exception as _e:
            logger.debug(f"[MLX] malloc_zone_pressure_relief not available: {_e}")

        # Obnovit starý limit
        if old_limit is not None:
            if hasattr(mx, 'set_cache_limit'):
                mx.set_cache_limit(old_limit)
            elif hasattr(mx.metal, 'set_cache_limit'):
                mx.metal.set_cache_limit(old_limit)
    except Exception:
        mlx_cleanup_sync()  # fallback


# F269: Metal slab pool — bounded reuse pool for Metal buffers
try:
    from hledac.universal.utils.metal_slab_pool import release_slab_pool as _release_slab_pool
except ImportError:
    _release_slab_pool: None = None  # type: ignore[assignment]


def mlx_cleanup_decorator(aggressive: bool = False):
    """Dekorátor pro async i sync funkce – přidá cleanup po dokončení."""
    import asyncio
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


# =============================================================================
# ISSUE-7.2: Memory status poller — asyncio task for dynamic Metal cache reconfiguration
# =============================================================================

import asyncio
import os
import sys
import time as _time_module
from collections.abc import Callable

# Rust atomic UMA state — 0=ok, 1=soft_warn, 2=warn, 3=critical, 4=emergency
_rust_uma_state: Callable | None = None
_set_uma_state_u8: Callable | None = None

_RUST_MEMORY_AVAILABLE: bool = False
try:
    from hledac.universal.hledac_rust_extensions import (  # type: ignore[unresolved-import]
        get_uma_state_u8 as _get_uma_state_u8_rust,
        set_uma_state_u8 as _set_uma_state_u8_rust,
    )
    _rust_uma_state = _get_uma_state_u8_rust
    _set_uma_state_u8 = _set_uma_state_u8_rust
    _RUST_MEMORY_AVAILABLE = True
except ImportError:
    _RUST_MEMORY_AVAILABLE = False


# Poller state — written by background task, read by get_dynamic_metal_cache_limit fast path
_current_uma_state_u8: int = 0  # 0=ok initially
_last_reconfigure_time: float = 0.0
_RECONFIGURE_COOLDOWN_S: float = 2.0  # Don't reconfigure more than every 2s


def _read_available_memory() -> int:
    """
    Read available memory in bytes using the most accurate source for M1.

    Priority:
    1. os.proc_available_memory() — macOS 13+ accurate probe (no psutil overhead)
    2. psutil.virtual_memory().available — fallback for older macOS / other platforms
    """
    if sys.platform == "darwin" and hasattr(os, "proc_available_memory"):
        try:
            return os.proc_available_memory()
        except Exception:
            pass
    try:
        return int(psutil.virtual_memory().available)
    except Exception:
        return 0


def _available_to_uma_state(available_bytes: int) -> int:
    """
    Map available memory (bytes) to UMA state u8.

    Thresholds in bytes (exact integers to avoid float precision):
      - >= 1_492_538_553 bytes (~1.39 GiB) → 0=ok
      - >= 1_073_741_824 bytes (1.0 GiB)  → 1=soft_warn
      - >= 751_619_276 bytes (~0.7 GiB)    → 2=warn
      - >= 429_496_729 bytes (~0.4 GiB)    → 3=critical
      - < 429_496_729 bytes                 → 4=emergency
    """
    if available_bytes >= 1_492_538_553:  # ~1.39 GiB
        return 0  # ok
    elif available_bytes >= 1_073_741_824:  # 1.0 GiB
        return 1  # soft_warn
    elif available_bytes >= 751_619_276:  # ~0.7 GiB
        return 2  # warn
    elif available_bytes >= 429_496_729:  # ~0.4 GiB
        return 3  # critical
    else:
        return 4  # emergency


# Cached uma_state string mapping for reconfigure calls
_UMA_STATE_NAMES: tuple[str, ...] = ("ok", "soft_warn", "warn", "critical", "emergency")


async def _memory_status_poller_task(interval_s: float = 0.5) -> None:
    """
    ISSUE-7.2: Background asyncio task that monitors UMA memory state every 500ms.

    Responsibilities:
    1. Poll os.proc_available_memory() (or psutil fallback) every 500ms
    2. Map available → uma_state_u8
    3. Write to Rust AtomicU8 for fast non-blocking reads (get_uma_state_u8)
    4. When state changes, trigger reconfigure_metal_cache_limit() (with 2s cooldown)

    This replaces mx.metal.get_active_memory() as the primary signal for Metal cache
    management. On M1 8GB UMA, psutil.available is the accurate indicator of actual
    memory pressure — mx.metal.get_active_memory() can report 0 bytes while the
    unified memory bus is saturated.

    Never raises — fail-safe task that logs errors and continues polling.
    """
    global _current_uma_state_u8, _last_reconfigure_time

    logger.info("[ISSUE-7.2] memory_status_poller_task started (500ms interval)")

    while True:
        try:
            await asyncio.sleep(interval_s)
        except asyncio.CancelledError:
            logger.info("[ISSUE-7.2] memory_status_poller_task cancelled")
            break

        try:
            available = await asyncio.to_thread(_read_available_memory)
        except Exception as e:
            logger.debug(f"[ISSUE-7.2] read available memory failed: {e}")
            continue

        if available <= 0:
            continue

        new_state_u8 = _available_to_uma_state(available)

        # Update Rust AtomicU8 — lock-free, ~10ns
        if _set_uma_state_u8 is not None:
            try:
                _set_uma_state_u8(new_state_u8)
            except Exception as e:
                logger.debug(f"[ISSUE-7.2] set_uma_state_u8 failed: {e}")

        # Trigger reconfigure if state changed and cooldown elapsed
        if new_state_u8 != _current_uma_state_u8:
            now = _time_module.monotonic()
            if now - _last_reconfigure_time >= _RECONFIGURE_COOLDOWN_S:
                state_name = _UMA_STATE_NAMES[new_state_u8] if new_state_u8 < len(_UMA_STATE_NAMES) else "unknown"
                try:
                    await asyncio.to_thread(reconfigure_metal_cache_limit, state_name)
                    _last_reconfigure_time = now
                    logger.info(
                        f"[ISSUE-7.2] Metal cache reconfigured: state={state_name}, "
                        f"available={available / 1024**2:.0f} MiB"
                    )
                except Exception as e:
                    logger.debug(f"[ISSUE-7.2] reconfigure_metal_cache_limit failed: {e}")
                _current_uma_state_u8 = new_state_u8


_memory_poller_task: asyncio.Task | None = None


async def start_memory_status_poller(interval_s: float = 0.5) -> None:
    """
    Start the background memory status poller task.

    Call once from the main async entry point (e.g., __main__.py run_sprint).
    The task runs for the lifetime of the process and is cancelled on shutdown.
    """
    global _memory_poller_task
    if _memory_poller_task is not None and not _memory_poller_task.done():
        return  # Already running

    loop = asyncio.get_running_loop()
    _memory_poller_task = loop.create_task(_memory_status_poller_task(interval_s))


async def stop_memory_status_poller() -> None:
    """Stop the background memory status poller task gracefully."""
    global _memory_poller_task
    if _memory_poller_task is not None:
        _memory_poller_task.cancel()
        try:
            await _memory_poller_task
        except asyncio.CancelledError:
            pass
        _memory_poller_task = None


def get_current_uma_state_u8() -> int:
    """
    Get current UMA state u8 from Rust AtomicU8 (fast path, ~10ns, no GIL).

    Returns 0-4: 0=ok, 1=soft_warn, 2=warn, 3=critical, 4=emergency.
    Falls back to Python-side tracking if Rust functions unavailable.
    """
    if _rust_uma_state is not None:
        try:
            return _rust_uma_state()
        except Exception:
            pass
    return _current_uma_state_u8


def get_current_uma_state_name() -> str:
    """Get current UMA state as string name."""
    state_u8 = get_current_uma_state_u8()
    return _UMA_STATE_NAMES[state_u8] if state_u8 < len(_UMA_STATE_NAMES) else "unknown"
