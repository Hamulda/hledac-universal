"""
core.psutil_shim — centralized psutil import with graceful fallback
===================================================================

Single source of truth for psutil availability. All modules should import
from this shim instead of directly importing psutil.

Rationale (Issue #122 / Issue #17):
  - psutil is a runtime dependency, not guaranteed in all environments
    (e.g., minified Python builds, embedded interpreters)
  - Scattered try/except ImportError blocks duplicate logic across 32+ files
  - Centralizing the availability check makes the fallback behavior explicit
    and ensures consistent graceful degradation
  - Issue #17: 6 modules had their own _get_psutil() lazy-init boilerplate
    that is now replaced by this single centralized shim

Usage:
    from hledac.universal.core.psutil_shim import psutil, PSUTIL_AVAILABLE

    if PSUTIL_AVAILABLE:
        mem = psutil.virtual_memory()
    else:
        mem = None  # caller handles None

Canonical availability flag: PSUTIL_AVAILABLE (bool)
Canonical psutil reference: psutil (module or None)
Canonical RAM query: available_gb() -> float (0.0 if unavailable)
Canonical RSS query: current_rss_gb() -> float (0.0 if unavailable)
Canonical Process singleton: process() -> psutil.Process (lazy, cached)
Canonical psutil module: psutil_module() -> psutil module (lazy, cached; for cpu_count, etc.)
Canonical virtual_memory(): virtual_memory() -> memory tuple (lazy, cached)

Fallback values are conservative (4.0 GB available) to avoid false negatives
in memory pressure detection when psutil is unavailable.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psutil as _psutil_type

try:
    import psutil as _psutil_module

    psutil = _psutil_module  # re-export for backward compatibility
    PSUTIL_AVAILABLE: bool = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    PSUTIL_AVAILABLE = False


# Issue #17: Lazy Process singleton — replaces per-module _get_psutil() boilerplate.
# Cached after first call so repeated callers (e.g., per-request RSS polling)
# pay no additional import cost.
_UNSET = object()
_process: Any = _UNSET  # type: ignore[assignment]
_psutil_module: Any = _UNSET  # type: ignore[assignment]


def process() -> Any:  # type: ignore[type-arg]
    """
    Return a psutil.Process singleton for the current process.

    Lazy: process is created on first call and cached.
    Returns None if psutil is unavailable.

    Use instead of psutil.Process() directly — avoids repeated instantiation.
    """
    global _process
    if _process is _UNSET:
        if PSUTIL_AVAILABLE and psutil is not None:
            try:
                _process = psutil.Process()  # type: ignore[union-attr]
            except Exception:
                _process = None
        else:
            _process = None
    return _process


def psutil_module() -> Any:  # type: ignore[type-arg]
    """
    Return the psutil module singleton (lazy-cached).

    Use when you need psutil module-level functions (cpu_count, virtual_memory,
    swap_memory, etc.). For Process-level calls, use process() instead.

    Returns None if psutil is unavailable.
    """
    global _psutil_module
    if _psutil_module is _UNSET:
        if PSUTIL_AVAILABLE and psutil is not None:
            _psutil_module = psutil
        else:
            _psutil_module = None
    return _psutil_module


def virtual_memory() -> tuple | None:
    """
    Return virtual_memory() tuple, lazy-cached.

    Returns None if psutil is unavailable.
    Cached after first call — system memory doesn't change between calls
    often enough to warrant repeated polling overhead.
    """
    if not PSUTIL_AVAILABLE or psutil is None:
        return None
    try:
        return psutil.virtual_memory()
    except Exception:
        return None


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
        process().memory_info().rss / 1e9 when available
        0.0 when psutil unavailable or process info inaccessible
    """
    if not PSUTIL_AVAILABLE or psutil is None:
        return 0.0
    try:
        p = process()
        if p is None:
            return 0.0
        return p.memory_info().rss / 1e9
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
    except Exception:  # noqa: BLE001
        pass
    try:
        p = process()
        if p is not None:
            result["rss_gb"] = p.memory_info().rss / 1e9
    except Exception:  # noqa: BLE001
        pass
    return result
