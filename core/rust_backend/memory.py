# memory.py — Memory Info domain
"""
Memory availability and total system memory queries.
Used for M1 resource governance and memory pressure monitoring.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Memory Domain
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
    except ImportError:
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
    except ImportError:
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
    except Exception:
        pass
    return 0


def get_memory_domain(ext: object | None) -> _RustMemoryDomain | _PythonMemoryDomain:
    """Factory: return Rust or Python MemoryDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustMemoryDomain(ext)
        except Exception:
            pass
    return _PythonMemoryDomain()
