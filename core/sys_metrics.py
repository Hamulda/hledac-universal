"""
System Metrics — Unified psutil wrappers for M1 8GB UMA.

Invariant: All memory functions are fail-safe — return 0/fallback on any error.
"""

import logging

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)


def get_memory_usage_mb() -> float:
    """
    Get current process memory usage in MB (RSS).

    Returns:
        RSS in MB, or 0.0 if psutil unavailable or on error.
    """
    if PSUTIL_AVAILABLE and psutil:
        try:
            proc = psutil.Process()
            return proc.memory_info().rss / (1024 * 1024)
        except Exception:
            return 0.0
    return 0.0


def get_system_memory() -> dict:
    """
    Get system memory info.

    Deprecated: Use uma_budget.get_system_memory_mb() instead.
    This function is kept for backward compatibility only.

    Returns:
        Dict with total_gb, available_gb, percent keys.
    """
    # Delegate to uma_budget for consistent caching/calculation
    try:
        from utils.uma_budget import get_system_memory_mb

        total_mb, used_mb, available_mb = get_system_memory_mb()
        return {
            "total_gb": total_mb / 1024,
            "available_gb": available_mb / 1024,
            "percent": (used_mb / total_mb * 100) if total_mb > 0 else 0.0,
        }
    except Exception:
        return {"total_gb": 8.0, "available_gb": 4.0, "percent": 50.0}
