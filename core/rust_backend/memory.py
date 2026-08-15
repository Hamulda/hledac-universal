# memory.py — Memory Info domain (A5-04: deprecated aliases)
"""
Memory availability and total system memory queries.
Used for M1 resource governance and memory pressure monitoring.


A5-04 CONSOLIDATION (2026-07-30)
=================================
Tento modul obsahuje DOMAIN FACTORY pattern pro DuckDB bridge.
NEMÁ overlp s core.memory — každý modul má jinou roli:

| Modul                        | Zodpovědnost                        |
|------------------------------|--------------------------------------|
| core.memory                  | System-wide metrics (Rust SSOT)       |
| core.rust_backend.memory     | DuckDB bridge: domain factory         |
| utils.mlx_memory._core       | MLX-specific: Metal API               |

DEPRECATED ALIASES (A5-04):
    get_process_rss_gib() → core.memory.get_process_rss_gib()
    get_available_memory_gib() → core.memory.get_available_memory_gib()
    get_metal_active_memory_bytes() → core.memory.get_metal_active_memory_bytes()

Pro nový kód používej přímo core.memory.
"""

from __future__ import annotations

import sys
import warnings
from typing import TYPE_CHECKING
from core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Deprecated aliases — point to core.memory (A5-04)
# =============================================================================

def get_process_rss_gib() -> float:
    """
    DEPRECATED (A5-04): Use core.memory.get_process_rss_gib() instead.
    Kept for backward compatibility with callers of this module.
    """
    warnings.warn(
        "core.rust_backend.memory.get_process_rss_gib() is deprecated. "
        "Use core.memory.get_process_rss_gib() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from hledac.universal.core.memory import get_process_rss_gib as _fn
    return _fn()


def get_available_memory_gib() -> float:
    """
    DEPRECATED (A5-04): Use core.memory.get_available_memory_gib() instead.
    Kept for backward compatibility with callers of this module.
    """
    warnings.warn(
        "core.rust_backend.memory.get_available_memory_gib() is deprecated. "
        "Use core.memory.get_available_memory_gib() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from hledac.universal.core.memory import get_available_memory_gib as _fn
    return _fn()


def get_metal_active_memory_bytes() -> int:
    """
    DEPRECATED (A5-04): Use core.memory.get_metal_active_memory_bytes() instead.
    Kept for backward compatibility with callers of this module.
    """
    warnings.warn(
        "core.rust_backend.memory.get_metal_active_memory_bytes() is deprecated. "
        "Use core.memory.get_metal_active_memory_bytes() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    from hledac.universal.core.memory import get_metal_active_memory_bytes as _fn
    return _fn()


# =============================================================================
# Memory Domain (original implementation)
# =============================================================================


class _RustMemoryDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def available_memory(self) -> int:
        """Get available memory in bytes."""
        return self._ext.memory_available()

    def total_memory(self) -> int:
        """Get total physical memory in bytes."""
        return self._ext.memory_total()


class _PythonMemoryDomain:
    __slots__ = ()

    def available_memory(self) -> int:
        """Python fallback: get available memory via psutil or ctypes."""
        return _python_get_available_memory()

    def total_memory(self) -> int:
        """Python fallback: get total memory via psutil or ctypes."""
        return _python_get_total_memory()

    def advise_free(self, ptr: int, len: int) -> bool:
        """Python fallback: MADV_FREE_REUSABLE not available on non-macOS."""
        import sys
        if sys.platform != "darwin":
            return False
        # Fallback for non-macOS Unix — MADV_FREE_REUSABLE is macOS-specific
        return False


def _python_get_available_memory() -> int:
    """Python fallback: get available system memory."""
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except ImportError:  # noqa: BLE001
        pass
    # Ultimate fallback: ctypes
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("dwTotalPhys", ctypes.c_uint64),
                ("dwAvailPhys", ctypes.c_uint64),
                ("dwTotalPageFile", ctypes.c_uint64),
                ("dwAvailPageFile", ctypes.c_uint64),
                ("dwTotalVirtual", ctypes.c_uint64),
                ("dwAvailVirtual", ctypes.c_uint64),
            ]

        stat = MemoryStatus()
        stat.dwLength = ctypes.sizeof(stat)
        if sys.platform == "win32":
            ctypes.windll.kernel32.GlobalMemoryStatus(ctypes.byref(stat))
            return int(stat.dwAvailPhys)
        else:
            # Unix - return 0 as fallback
            return 0
    except Exception:
        return 0


def _python_get_total_memory() -> int:
    """Python fallback: get total system memory."""
    try:
        import psutil

        return int(psutil.virtual_memory().total)
    except ImportError:  # noqa: BLE001
        pass
    try:
        import ctypes

        if sys.platform == "darwin":
            # macOS: use sysctl
            import subprocess

            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        elif sys.platform == "win32":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_uint32),
                    ("dwTotalPhys", ctypes.c_uint64),
                ]

            stat = MemoryStatus()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatus(ctypes.byref(stat))
            return int(stat.dwTotalPhys)
    except Exception:  # noqa: BLE001
        pass
    return 0


def get_memory_domain(ext: object | None) -> _RustMemoryDomain | _PythonMemoryDomain:
    """Factory: return Rust or Python MemoryDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustMemoryDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonMemoryDomain()
