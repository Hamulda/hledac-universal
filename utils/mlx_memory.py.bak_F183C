"""
MLX memory hygiene helper - Sprint 8AY.

LAZY MLX import: helper module import NEBO first call aktivuje MLX.
Neprodukuje žádný MLX import při boot bez volání.

API:
- clear_mlx_cache() -> bool
- get_mlx_active_memory_mb() -> int | None
- get_mlx_peak_memory_mb() -> int | None
- get_mlx_cache_memory_mb() -> int | None
- get_mlx_memory_pressure() -> tuple[int, str]
- get_mlx_memory_metrics() -> dict (optional convenience)

M1 8GB UMA thresholds:
- WARNING >= 80%
- CRITICAL >= 90%
"""

from __future__ import annotations

import gc
import logging
import time as _time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from types import ModuleType

logger = logging.getLogger(__name__)

# Lazy availability singleton
_MLX_AVAILABLE: Optional[bool] = None
_mlx_core: Optional["ModuleType"] = None


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
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "clear_cache"):
            metal.clear_cache()
        elif hasattr(mx_core, "clear_cache"):
            mx_core.clear_cache()
        return True
    except Exception as e:
        logger.debug(f"clear_mlx_cache() failed: {e}")
        return False


def get_mlx_active_memory_mb() -> Optional[int]:
    """Aktuální aktivní MLX paměť v MB, nebo None pokud nedostupné."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return None
    try:
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "get_active_memory"):
            return metal.get_active_memory() // (1024 * 1024)
        if hasattr(mx_core, "get_active_memory"):
            return mx_core.get_active_memory() // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_active_memory failed: {e}")
    return None


def get_mlx_peak_memory_mb() -> Optional[int]:
    """Peak MLX paměť v MB, nebo None pokud nedostupné."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return None
    try:
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "get_peak_memory"):
            return metal.get_peak_memory() // (1024 * 1024)
        if hasattr(mx_core, "get_peak_memory"):
            return mx_core.get_peak_memory() // (1024 * 1024)
    except Exception as e:
        logger.debug(f"get_peak_memory failed: {e}")
    return None


def get_mlx_cache_memory_mb() -> Optional[int]:
    """MLX cache paměť v MB, nebo None pokud nedostupné."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return None
    try:
        metal = getattr(mx_core, "metal", None)
        if metal is not None and hasattr(metal, "get_cache_memory"):
            return metal.get_cache_memory() // (1024 * 1024)
        if hasattr(mx_core, "get_cache_memory"):
            return mx_core.get_cache_memory() // (1024 * 1024)
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

        # M1 8GB UMA budget: ~6.25GB max pro LLM + KV cache
        # Warning threshold: 80% → ~5GB
        # Critical threshold: 90% → ~5.6GB
        MAX_MEMORY_MB = 6_250  # 6.25GB unified budget

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


def configure_mlx_limits(cache_limit_mb: int = 1536, memory_limit_mb: int | None = None) -> dict:
    """Configure MLX cache and memory limits for M1 8GB."""
    mx_core = _get_mlx_core()
    if mx_core is None:
        return {"success": False, "error": "MLX not available"}

    result = {"success": True, "cache_limit_mb": cache_limit_mb, "memory_limit_mb": memory_limit_mb}

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
        return {"available": False, "active_mb": None, "peak_mb": None, "cache_mb": None, "pressure_pct": 0, "pressure_level": "UNKNOWN"}

    active = get_mlx_active_memory_mb()
    peak = get_mlx_peak_memory_mb()
    cache = get_mlx_cache_memory_mb()
    pressure_pct, pressure_level = get_mlx_memory_pressure()

    return {"available": True, "active_mb": active, "peak_mb": peak, "cache_mb": cache, "pressure_pct": pressure_pct, "pressure_level": pressure_level}


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
