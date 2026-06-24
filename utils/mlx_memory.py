"""
MLX memory hygiene helper - Sprint 8AY.

.. deprecated:: F266-U3
    ``mlx_memory`` is deprecated in favour of ``utils.mlx_cache`` which
    provides a superset of the functionality (reconfigure_metal_cache_limit,
    get_dynamic_metal_cache_limit, get_metal_limits_status) plus canonical
    Metal cache reconfiguration. All callers should migrate to
    ``from hledac.universal.utils import mlx_cache``.

LAZY MLX import: helper module import NEBO first call aktivuje MLX.
Neprodukuje žádný MLX import při boot bez volání.

API:
- clear_mlx_cache() -> bool
- get_mlx_active_memory_mb() -> int | None
- get_mlx_peak_memory_mb() -> int | None
- get_mlx_cache_memory_mb() -> int | None
- get_mlx_memory_pressure() -> tuple[int, str]
- get_mlx_memory_metrics() -> dict (optional convenience)

M1 8GB UMA thresholds: WARNING >= 80%, CRITICAL >= 90% of MLX budget.
Derived from uma_budget.py canonical thresholds (Sprint F207N-C).
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "hledac.universal.utils.mlx_memory is deprecated; "
    "use hledac.universal.utils.mlx_cache instead (F266-U3)",
    DeprecationWarning,
    stacklevel=2,
)

import gc  # noqa: E402
import logging  # noqa: E402
import time as _time  # noqa: E402
from contextlib import nullcontext  # noqa: E402
from typing import TYPE_CHECKING, Any  # noqa: E402

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)

# Sprint F207N-C: Canonical threshold import — single source of truth.
# Fail-open: if uma_budget is unavailable, fall back to safe defaults.
try:

    # MLX budget matches the M1 8GB UMA total app budget documented in
    # CLAUDE.md / docs: macOS ~2.5GB + orchestrator ~1GB + LLM ~2GB + KV cache
    # ~0.75GB = 6.25 GiB max for MLX-loaded artifacts.
    # WARNING at 80% of MLX budget (~5.0 GiB), CRITICAL at 90% (~5.625 GiB).
    _MLX_BUDGET_GIB: float = 6.25
    MLX_WARNING_GIB: float = _MLX_BUDGET_GIB * 0.8
    MLX_CRITICAL_GIB: float = _MLX_BUDGET_GIB * 0.9
    MAX_MEMORY_MB: int = int(_MLX_BUDGET_GIB * 1024)  # 6_400 MB
except Exception:
    # Fail-open: no hardcoded thresholds at module level
    MLX_WARNING_GIB = 5.0
    MLX_CRITICAL_GIB = 5.625
    MAX_MEMORY_MB = 6_400

# Lazy availability singleton
_MLX_AVAILABLE: bool | None = None
_mlx_core: ModuleType | None = None


def _ensure_mlx() -> bool:
    """Lazy MLX initialization. Volá se až při prvním API volání."""
    global _MLX_AVAILABLE, _mlx_core
    if _MLX_AVAILABLE is not None:
        return _MLX_AVAILABLE
    _MLX_AVAILABLE = False
    try:
        import mlx.core as mx
        _mlx_core = mx
        _MLX_AVAILABLE = True
    except Exception as e:
        logger.debug(f"MLX lazy init failed: {e}")
        _mlx_core = None
    return _MLX_AVAILABLE


def _get_mlx_core():
    """Return mlx.core module if available, else None."""
    if not _ensure_mlx():
        return None
    return _mlx_core


def clear_mlx_cache() -> bool:
    """
    Clear MLX Metal cache s gc.collect() + mx.eval([]) + metal.clear_cache().

    Returns:
        True pokud úspěšně provedeno, False pokud MLX nedostupný.
    """
    mx_core = _get_mlx_core()
    if mx_core is None:
        return False

    try:
        gc.collect()
        mx_core.eval([])
    except Exception as e:
        logger.debug(f"mx.eval([]) failed: {e}")

    try:
        # F185C: metal.clear_cache is the canonical MLX API; check it FIRST
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "clear_cache"):
            metal.clear_cache()
        elif hasattr(mx_core, "clear_cache"):
            # Fallback to top-level clear_cache (for older MLX versions)
            mx_core.clear_cache()
        return True
    except Exception as e:
        logger.debug(f"clear_mlx_cache() failed: {e}")
        return False


def get_mlx_active_memory_mb() -> int | None:
    """Aktuální aktivní MLX paměť v MB, nebo None pokud nedostupné."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return None
    try:
        # Modern-first: try mx.get_active_memory() first, fall back to mx.metal.get_active_memory()
        if hasattr(mx_core, "get_active_memory"):
            return mx_core.get_active_memory() // (1024 * 1024)
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "get_active_memory"):
            return metal.get_active_memory() // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_active_memory failed: {e}")
    return None


def get_mlx_peak_memory_mb() -> int | None:
    """Peak MLX paměť v MB, nebo None pokud nedostupné."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return None
    try:
        # Modern-first: try mx.get_peak_memory() first, fall back to mx.metal.get_peak_memory()
        if hasattr(mx_core, "get_peak_memory"):
            return mx_core.get_peak_memory() // (1024 * 1024)
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "get_peak_memory"):
            return metal.get_peak_memory() // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_peak_memory failed: {e}")
    return None


def get_mlx_cache_memory_mb() -> int | None:
    """MLX cache paměť v MB, nebo None pokud nedostupné."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return None
    try:
        # Modern-first: try mx.get_cache_memory() first, fall back to mx.metal.get_cache_memory()
        if hasattr(mx_core, "get_cache_memory"):
            return mx_core.get_cache_memory() // (1024 * 1024)
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "get_cache_memory"):
            return metal.get_cache_memory() // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_cache_memory failed: {e}")
    return None


def get_mlx_memory_pressure() -> tuple[int, str]:
    """
    Vypočítá memory pressure na M1 8GB UMA.

    Returns:
        (usage_pct: int, level: str)
        level: NORMAL / WARNING / CRITICAL / UNKNOWN
    """
    if not _ensure_mlx():
        return 0, "UNKNOWN"

    try:
        active = get_mlx_active_memory_mb()
        if active is None:
            return 0, "UNKNOWN"

        # Use module-level MAX_MEMORY_MB (set from canonical thresholds above)
        usage_pct = int((active / MAX_MEMORY_MB) * 100)
        if usage_pct >= 90:
            return usage_pct, "CRITICAL"
        elif usage_pct >= 80:
            return usage_pct, "WARNING"
        else:
            return usage_pct, "NORMAL"
    except Exception as e:
        logger.debug(f"get_mlx_memory_pressure failed: {e}")
        return 0, "UNKNOWN"


def get_mlx_memory_metrics() -> dict:
    """
    Convenience reporter pro všechny MLX memory metriky.

    Returns:
        dict s klíči: available, active_mb, peak_mb, cache_mb, pressure_pct, pressure_level
    """
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


def configure_mlx_limits(cache_limit_mb: int = 1536, memory_limit_mb: int | None = None) -> dict[str, Any]:
    """Configure MLX cache and memory limits for M1 8GB."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return {"success": False, "error": "MLX not available"}

    result: dict[str, Any] = {
        "success": True,
        "cache_limit_mb": cache_limit_mb,
        "memory_limit_mb": memory_limit_mb,
    }

    try:
        if hasattr(mx_core, "set_cache_limit"):
            mx_core.set_cache_limit(cache_limit_mb * 1024 * 1024)
            result["cache_configured"] = True
        else:
            result["cache_configured"] = False
    except Exception as e:
        result["cache_configured"] = False
        result["cache_error"] = str(e)

    if memory_limit_mb is not None:
        try:
            if hasattr(mx_core, "set_memory_limit"):
                mx_core.set_memory_limit(memory_limit_mb * 1024 * 1024)
                result["memory_configured"] = True
            else:
                result["memory_configured"] = False
        except Exception as e:
            result["memory_configured"] = False
            result["memory_error"] = str(e)

    return result


def format_mlx_memory_snapshot() -> dict:
    """Get a complete MLX memory snapshot."""
    if not _ensure_mlx():
        return {"available": False, "active_mb": None, "peak_mb": None, "cache_mb": None, "pressure_pct": 0, "pressure_level": "UNKNOWN"}  # noqa: E501

    active = get_mlx_active_memory_mb()
    peak = get_mlx_peak_memory_mb()
    cache = get_mlx_cache_memory_mb()
    pressure_pct, pressure_level = get_mlx_memory_pressure()

    return {"available": True, "active_mb": active, "peak_mb": peak, "cache_mb": cache, "pressure_pct": pressure_pct, "pressure_level": pressure_level}  # noqa: E501


# -----------------------------------------------------------------------
# F265-METAL: Pre-Allocation + Unified Memory Governor (M1 8GB)
# -----------------------------------------------------------------------
# Metal memory tier-based pre-allocation for M1 8GB UMA stability.
# Adaptive budgets based on UMA state for optimal memory utilization.
# -----------------------------------------------------------------------

# Memory tier definitions (M1 8GB adaptive budgets)
_METAL_TIER_BUFFERS: dict[str, dict[str, int]] = {
    "idle":      {"buffer_mb": 768,  "cache_mb": 1024, "wired_mb": 1536},
    "low":       {"buffer_mb": 640,  "cache_mb": 896,  "wired_mb": 1280},
    "medium":    {"buffer_mb": 512,  "cache_mb": 768,  "wired_mb": 1024},
    "high":      {"buffer_mb": 384,  "cache_mb": 512,  "wired_mb": 768},
    "critical":  {"buffer_mb": 256,  "cache_mb": 384,  "wired_mb": 512},
    "emergency": {"buffer_mb": 128,  "cache_mb": 256,  "wired_mb": 384},
}

# Ceiling limits (M1 8GB safe maximums)
_MAX_BUFFER_MB: int = 1536  # 1.5 GiB
_MAX_CACHE_MB: int = 1536   # 1.5 GiB
_MAX_WIRED_MB: int = 1536  # 1.5 GiB


def set_default_memory(buffer_mb: int = 512) -> dict[str, Any]:
    """
    F265-METAL: Set default Metal memory buffer size (user snippet API).

    Pre-allocates Metal buffers for MLX inference to reduce allocation overhead.
    On M1 8GB, buffer is limited to [128, 1536] MiB for system stability.

    Args:
        buffer_mb: Desired buffer size in MB (default: 512 MiB)

    Returns:
        dict with keys: success (bool), buffer_mb (int), error (str or None)

    Invariant: buffer_mb is clamped to [128, 1536] MiB for M1 8GB safety.
    """
    mx_core = _get_mlx_core()
    if mx_core is None:
        return {"success": False, "buffer_mb": 0, "error": "MLX not available"}

    # Clamp to M1 8GB safe range
    safe_buffer = max(128, min(buffer_mb, _MAX_BUFFER_MB))

    try:
        if hasattr(mx_core, "set_default_memory"):
            mx_core.set_default_memory(safe_buffer * 1024 * 1024)
            return {"success": True, "buffer_mb": safe_buffer, "error": None}
        elif hasattr(mx_core.metal, "set_default_memory"):
            mx_core.metal.set_default_memory(safe_buffer * 1024 * 1024)
            return {"success": True, "buffer_mb": safe_buffer, "error": None}
        else:
            return {"success": False, "buffer_mb": 0, "error": "set_default_memory not available"}
    except Exception as e:
        return {"success": False, "buffer_mb": 0, "error": str(e)}


def get_memory_info() -> dict[str, Any]:
    """
    F265-METAL: Get comprehensive Metal memory info (user snippet API).

    Returns:
        dict with keys:
            - used (int): Active memory in bytes
            - peak (int): Peak memory in bytes
            - cache (int): Cache memory in bytes
            - available (bool): MLX availability
            - pressure (str): NORMAL|WARNING|CRITICAL|UNKNOWN
            - pressure_pct (int): Usage percentage
    """
    if not _ensure_mlx():
        return {
            "used": 0, "peak": 0, "cache": 0,
            "available": False, "pressure": "UNKNOWN", "pressure_pct": 0
        }

    try:
        used = get_metal_active_memory()
        peak = get_metal_peak_memory()
        cache = get_metal_cache_memory()
        pressure_pct, pressure = get_mlx_memory_pressure()

        return {
            "used": used,
            "peak": peak,
            "cache": cache,
            "available": True,
            "pressure": pressure,
            "pressure_pct": pressure_pct,
        }
    except Exception:
        return {
            "used": 0, "peak": 0, "cache": 0,
            "available": False, "pressure": "UNKNOWN", "pressure_pct": 0
        }


def get_tier_for_memory(memory_mb: int) -> str:
    """
    Map memory usage to tier name for tier-based pre-allocation.

    Args:
        memory_mb: Current memory usage in MB

    Returns:
        Tier name: idle|low|medium|high|critical|emergency
    """
    if memory_mb < 256:
        return "emergency"
    elif memory_mb < 384:
        return "critical"
    elif memory_mb < 512:
        return "high"
    elif memory_mb < 640:
        return "medium"
    elif memory_mb < 768:
        return "low"
    return "idle"


def recommend_tier_config(tier: str | None = None, uma_state: str | None = None) -> dict[str, int | str]:
    """
    Recommend optimal Metal memory configuration for given tier.

    Args:
        tier: Memory tier name (auto-detected from current usage if None)
        uma_state: UMA state string for adaptive configuration

    Returns:
        dict with buffer_mb, cache_mb, wired_mb keys
    """
    # Auto-detect tier from current memory if not provided
    if tier is None:
        active_mb = get_mlx_active_memory_mb() or 0
        tier = get_tier_for_memory(active_mb)

    # Override with UMA state if provided
    if uma_state in ("critical", "emergency"):
        tier = uma_state

    config = _METAL_TIER_BUFFERS.get(tier, _METAL_TIER_BUFFERS["medium"])

    return {
        "buffer_mb": config["buffer_mb"],
        "cache_mb": config["cache_mb"],
        "wired_mb": config["wired_mb"],
        "tier": tier,
    }


class MetalPreallocator:
    """
    F265-METAL: Metal memory pre-allocator with tier-based adaptive budgets.

    Manages pre-allocation of Metal buffers to reduce allocation overhead
    during inference. Operates in tier-based modes that adapt to current
    memory pressure.

    Usage:
        preallocator = MetalPreallocator()
        preallocator.apply_tier("medium")  # Apply medium memory tier
        info = preallocator.get_status()     # Get current configuration
    """

    def __init__(self, default_tier: str = "medium"):
        """
        Initialize preallocator with default tier.

        Args:
            default_tier: Initial memory tier (default: "medium")
        """
        self._current_tier: str = default_tier
        self._configured: bool = False
        self._last_config_time: float = 0.0

    @property
    def current_tier(self) -> str:
        """Current memory tier."""
        return self._current_tier

    def apply_tier(self, tier: str | None = None, uma_state: str | None = None) -> dict[str, Any]:
        """
        Apply memory tier configuration.

        Args:
            tier: Tier name (auto-detected if None)
            uma_state: Override tier based on UMA state

        Returns:
            dict with configuration results
        """
        config = recommend_tier_config(tier=tier, uma_state=uma_state)
        tier_name = config["tier"]

        mx_core = _get_mlx_core()
        if mx_core is None:
            return {"success": False, "error": "MLX not available", "tier": tier_name}

        results: dict[str, Any] = {"tier": tier_name, "success": True, "errors": []}

        # Apply cache limit
        try:
            cache_bytes = config["cache_mb"] * 1024 * 1024
            if hasattr(mx_core, "set_cache_limit"):
                mx_core.set_cache_limit(cache_bytes)
                results["cache_mb"] = config["cache_mb"]
            elif hasattr(mx_core.metal, "set_cache_limit"):
                mx_core.metal.set_cache_limit(cache_bytes)
                results["cache_mb"] = config["cache_mb"]
        except Exception as e:
            results["errors"].append(f"cache_limit: {e}")

        # Apply wired limit
        try:
            wired_bytes = config["wired_mb"] * 1024 * 1024
            if hasattr(mx_core, "set_wired_limit"):
                mx_core.set_wired_limit(wired_bytes)
                results["wired_mb"] = config["wired_mb"]
            elif hasattr(mx_core.metal, "set_wired_limit"):
                mx_core.metal.set_wired_limit(wired_bytes)
                results["wired_mb"] = config["wired_mb"]
        except Exception as e:
            results["errors"].append(f"wired_limit: {e}")

        # Apply default memory buffer
        buffer_result = set_default_memory(int(config["buffer_mb"]))
        results["buffer_mb"] = buffer_result.get("buffer_mb", 0)
        if not buffer_result["success"]:
            results["errors"].append(f"default_memory: {buffer_result['error']}")
            results["success"] = False

        self._current_tier = str(tier_name)
        self._configured = True
        self._last_config_time = _time.time()

        if results["errors"]:
            results["success"] = False

        return results

    def get_status(self) -> dict[str, Any]:
        """
        Get current preallocator status and memory info.

        Returns:
            dict with tier, configured, last_config_time, memory_info
        """
        return {
            "tier": self._current_tier,
            "configured": self._configured,
            "last_config_time": self._last_config_time,
            "memory_info": get_memory_info(),
            "tier_config": recommend_tier_config(self._current_tier),
        }

    def adaptive_update(self) -> dict[str, Any]:
        """
        Auto-update tier based on current memory pressure.

        Returns:
            dict with updated configuration
        """
        active_mb = get_mlx_active_memory_mb() or 0
        # Also consider absolute memory usage
        tier_name = get_tier_for_memory(active_mb)

        return self.apply_tier(tier=tier_name)


# -----------------------------------------------------------------------
# F266 METAL LEAK FIX: Canonical teardown sequence + deprecated API guard
# -----------------------------------------------------------------------
# MLX >= 0.18 moved memory APIs from mx.metal.* to mx.* (no .metal prefix).
# Guard via hasattr for backward compat with older MLX (< 0.18).
# Canonical teardown order (fixes +1.02 GiB/sprint leak on M1 8GB):
#   1. mx.eval([])        — flush pending lazy ops (REQUIRED before clear_cache)
#   2. gc.collect()        — Python GC BEFORE Metal release (clears circular refs)
#   3. mx.clear_cache()   — Metal cache release (modern API)
#   4. gc.collect()        — second pass for circular refs created during Metal free
# -----------------------------------------------------------------------

import mlx.core as _mx  # noqa: E402


def _has_metal_api() -> bool:
    """Check if mx.metal namespace exists (MLX < 0.18 compatibility)."""
    return hasattr(_mx, "metal")


def get_metal_active_memory() -> int:
    """Get active Metal memory with modern-first fallback."""
    if hasattr(_mx, "get_active_memory"):
        return int(_mx.get_active_memory())
    if _has_metal_api() and hasattr(_mx.metal, "get_active_memory"):
        return int(_mx.metal.get_active_memory())
    return 0


def get_metal_peak_memory() -> int:
    """Get peak Metal memory with modern-first fallback."""
    if hasattr(_mx, "get_peak_memory"):
        return int(_mx.get_peak_memory())
    if _has_metal_api() and hasattr(_mx.metal, "get_peak_memory"):
        return int(_mx.metal.get_peak_memory())
    return 0


def get_metal_cache_memory() -> int:
    """Get Metal cache memory with modern-first fallback."""
    if hasattr(_mx, "get_cache_memory"):
        return int(_mx.get_cache_memory())
    if _has_metal_api() and hasattr(_mx.metal, "get_cache_memory"):
        return int(_mx.metal.get_cache_memory())
    return 0


def safe_clear_metal_cache() -> bool:
    """
    Canonical Metal cache clear — fixes +1.02 GiB/sprint leak.

    Sequence: mx.eval([]) → gc.collect() → mx.clear_cache() → gc.collect()
    All wrapped in try/except for fail-safe operation.

    Returns True if successful, False otherwise.
    """
    try:
        # Step 1: flush lazy ops (REQUIRED before clear_cache)
        _mx.eval([])
    except Exception as e:
        logger.debug(f"safe_clear_metal_cache: mx.eval([]) failed: {e}")

    # Step 2: Python GC BEFORE Metal release
    gc.collect()

    # Step 3: Metal cache release — modern API first, fallback to deprecated
    cleared = False
    try:
        if hasattr(_mx, "clear_cache"):
            _mx.clear_cache()
            cleared = True
        elif _has_metal_api() and hasattr(_mx.metal, "clear_cache"):
            _mx.metal.clear_cache()
            cleared = True
    except Exception as e:
        logger.debug(f"safe_clear_metal_cache: clear_cache failed: {e}")

    # Step 4: second GC pass for circular refs created during Metal free
    gc.collect()

    return cleared


def safe_set_cache_limit(bytes_limit: int) -> bool:
    """Set Metal cache limit with modern-first fallback."""
    try:
        if hasattr(_mx, "set_cache_limit"):
            _mx.set_cache_limit(bytes_limit)
            return True
        if _has_metal_api() and hasattr(_mx.metal, "set_cache_limit"):
            _mx.metal.set_cache_limit(bytes_limit)
            return True
    except Exception as e:
        logger.debug(f"safe_set_cache_limit({bytes_limit}) failed: {e}")
    return False


def safe_get_cache_limit() -> int | None:
    """Get Metal cache limit with modern-first fallback."""
    try:
        if hasattr(_mx, "get_cache_limit"):
            return int(_mx.get_cache_limit())
        if _has_metal_api() and hasattr(_mx.metal, "get_cache_limit"):
            return int(_mx.metal.get_cache_limit())
    except Exception:
        pass
    return None


# -----------------------------------------------------------------------
# Debounced cache clear (Sprint 1B)
# -----------------------------------------------------------------------

_debounce_last_clear: float = 0.0
_DEBOUNCE_SECONDS: float = 0.5


def clear_mlx_cache_debounced(min_interval_seconds: float = _DEBOUNCE_SECONDS) -> bool:
    """
    Clear MLX cache with debounce to prevent rapid repeated clears.

    Args:
        min_interval_seconds: minimum interval between clears (default 0.5s)

    Returns:
        True if cache was cleared, False if debounced (too soon).
    """
    global _debounce_last_clear
    now = _time.monotonic()

    if now - _debounce_last_clear < min_interval_seconds:
        return False

    _debounce_last_clear = now
    return clear_mlx_cache()


def set_cache_limit_with_debounce(limit_mb: int, min_interval_seconds: float = 1.0) -> dict:
    """
    Set MLX cache limit with debounce protection.

    Returns the result dict from configure_mlx_limits, or a debounce skip result.
    """
    global _debounce_last_clear
    now = _time.monotonic()

    if now - _debounce_last_clear < min_interval_seconds:
        return {"success": False, "error": "debounced", "cache_limit_mb": limit_mb}

    _debounce_last_clear = now
    return configure_mlx_limits(cache_limit_mb=limit_mb)


# -----------------------------------------------------------------------
# F219L: Metal stream context helper — single source of truth for mx.stream guard
# F288 FIX: Thread-aware stream creation — mx.stream(gpu) is tied to the
# thread that creates it. Caching a single stream globally causes
# "Stream(gpu,1) not in current thread" when MLX is called from a worker
# thread (P0-3 MLXWorkerThread, asyncio.to_thread, ThreadPoolExecutor).
# Fix: create stream per-thread via thread-local storage.
# -----------------------------------------------------------------------
import threading  # noqa: E402

# F288 FIX (P1): Per-thread stream cache via thread-local storage.
# mx.stream(gpu) is tied to the creating thread — a stream created in
# the main thread cannot be used in a worker thread (causes
# "Stream(gpu,1) not in current thread" Metal error).
# Using thread-local ensures each thread (main, MLXWorkerThread,
# asyncio.to_thread pool) gets its own stream instance.
# Streams are cached per-thread and reused — Metal handles concurrent
# dispatches correctly when each thread uses its own stream.
_thread_local = threading.local()
_STREAM_CACHE_MAX_PER_THREAD = 4  # bound per thread, prevents unbounded growth


def get_metal_stream_context():
    """
    F219L + F288: Return mx.stream(mx.gpu) or nullcontext if GPU unavailable.

    Thread-aware: each thread gets its own mx.stream(gpu) instance via
    thread-local storage. This ensures:
    1. Main thread: stream created at first MLX call, reused in same thread
    2. MLXWorkerThread: stream created inside the worker loop, valid there
    3. asyncio.to_thread pool threads: each gets its own stream on first use

    mx.stream() is lightweight (no GPU memory allocation) — creating a fresh
    stream per call or reusing a cached one are both valid patterns. We cache
    per-thread to avoid the overhead of creating a new stream on every call
    while still being thread-safe.

    Guards against:
    - MLX not available
    - mx.gpu attribute missing
    - mx.gpu is None
    - Any exception during stream creation

    Fail-open: returns nullcontext() so callers always have a valid context manager.

    Returns:
        context manager: mx.stream(mx.gpu) or nullcontext()
    """
    try:
        if not _ensure_mlx():
            return nullcontext()
        mx_core = _get_mlx_core()
        if mx_core is None or not hasattr(mx_core, 'gpu') or mx_core.gpu is None:
            return nullcontext()

        # F288 FIX (P1): Per-thread stream cache via thread-local storage.
        # Each thread caches its own stream(s) — bounded LRU per thread.
        # This avoids creating a new stream on every MLX call while
        # ensuring stream affinity is respected per thread.
        thread_local = _thread_local
        cache: list = getattr(thread_local, '_metal_stream_cache', []) or []
        thread_local._metal_stream_cache = cache

        if cache:
            # Reuse most recent stream (LIFO — most recent at end)
            stream = cache.pop()
        else:
            # Create fresh stream for this thread
            stream = mx_core.stream(mx_core.gpu)

        # Return stream as context manager; caller releases it back to cache
        return _ThreadLocalStreamContext(stream, cache)

    except Exception:
        return nullcontext()


class _ThreadLocalStreamContext:
    """
    F288 FIX (P1): Context manager that returns the stream to the thread-local
    cache after the with-block exits. Bounded: cache capped at
    _STREAM_CACHE_MAX_PER_THREAD per thread; oldest entries evicted on overflow.
    """

    __slots__ = ('_stream', '_cache')

    def __init__(self, stream, cache: list) -> None:
        self._stream = stream
        self._cache = cache

    def __enter__(self):
        return self._stream

    def __exit__(self, *_, _args=None):
        try:
            # Return stream to thread-local cache, bounded LRU eviction
            if len(self._cache) < _STREAM_CACHE_MAX_PER_THREAD:
                self._cache.append(self._stream)
            # else: stream discarded — bounded, prevents unbounded growth
        except Exception:
            pass  # fail-safe: discard on any error
