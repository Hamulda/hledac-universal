"""
core.psutil_shim — centralized psutil import with graceful fallback
===================================================================

Single source of truth for psutil availability. All modules should import
from this shim instead of directly importing psutil.

Rationale (Issue #122):
  - psutil is a runtime dependency, not guaranteed in all environments
    (e.g., minified Python builds, embedded interpreters)
  - Scattered try/except ImportError blocks duplicate logic across 32+ files
  - Centralizing the availability check makes the fallback behavior explicit
    and ensures consistent graceful degradation

Usage:
    from core.psutil_shim import psutil, PSUTIL_AVAILABLE

    if PSUTIL_AVAILABLE:
        mem = psutil.virtual_memory()
    else:
        mem = None  # caller handles None

Canonical availability flag: PSUTIL_AVAILABLE (bool)
Canonical psutil reference: psutil (module or None)
Canonical RAM query: available_gb() -> float (0.0 if unavailable)
Canonical RSS query: current_rss_gb() -> float (0.0 if unavailable)

Fallback values are conservative (4.0 GB available) to avoid false negatives
in memory pressure detection when psutil is unavailable.
"""
from __future__ import annotations

try:
    import psutil as _psutil_module

    psutil = _psutil_module  # re-export for backward compatibility
    PSUTIL_AVAILABLE: bool = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    PSUTIL_AVAILABLE = False


def available_gb() -> float:
    """
    Return available system memory in GB.

    Returns:
        psutil.virtual_memory().available / (1024**3) when available
        4.0 (conservative fallback) when psutil unavailable

    The 4.0 GB fallback represents a safe baseline for M1 8GB systems
    where the OS reserves ~4 GB for itself, leaving ~4 GB for apps.
    """
    if not PSUTIL_AVAILABLE or psutil is None:
        return 4.0
    try:
        return psutil.virtual_memory().available / (1024**3)
    except Exception:
        return 4.0


def current_rss_gb() -> float:
    """
    Return current process RSS in GB.

    Returns:
        psutil.Process().memory_info().rss / 1e9 when available
        0.0 when psutil unavailable or process info inaccessible
    """
    if not PSUTIL_AVAILABLE or psutil is None:
        return 0.0
    try:
        return psutil.Process().memory_info().rss / 1e9
    except Exception:
        return 0.0


def memory_info() -> dict[str, float]:
    """
    Return a JSON-safe memory info dict.

    Returns:
        {
            "available_gb": float,
            "total_gb": float,
            "used_gb": float,
            "percent": float,
            "rss_gb": float,
        }
        All values are 0.0 when psutil unavailable.
    """
    result = {
        "available_gb": 0.0,
        "total_gb": 0.0,
        "used_gb": 0.0,
        "percent": 0.0,
        "rss_gb": 0.0,
    }
    if not PSUTIL_AVAILABLE or psutil is None:
        return result
    try:
        vm = psutil.virtual_memory()  # type: ignore[union-attr]
        result["available_gb"] = vm.available / (1024**3)
        result["total_gb"] = vm.total / (1024**3)
        result["used_gb"] = vm.used / (1024**3)
        result["percent"] = vm.percent
    except Exception:
        pass
    try:
        result["rss_gb"] = psutil.Process().memory_info().rss / 1e9  # type: ignore[union-attr]
    except Exception:
        pass
    return result
