# madvise.py — Madvise / Memory Advice domain
"""
macOS memory advice operations via madvise(2).
Used for hinting to the kernel about memory access patterns.

"""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

# Platform-specific setup
_LIB = None
if sys.platform == "darwin":
    try:
        _LIB = ctypes.CDLL(ctypes.util.find_library("c"))
    except Exception:
        pass

_MADV_FREE_REUSABLE = 7  # macOS MADV_FREE_REUSABLE


# =============================================================================
# Madvise Domain
# =============================================================================


class _RustMadvisDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def madvise_on_mmap_region(self, addr: int, length: int, advice: int = 7) -> bool:
        """Apply madvise advice to memory region."""
        return self._ext.madvise_on_mmap_region(addr, length, advice)

    def madvise_hugepage(self, addr: int, length: int) -> bool:
        """Hint that memory region will be accessed heavily (MADV_HUGEPAGE)."""
        return self._ext.madvise_hugepage(addr, length)

    def mmap_alloc_with_hugepage(self, size: int, read_write: bool = True) -> tuple[int, int]:
        """Allocate memory with huge pages."""
        return self._ext.mmap_alloc_with_hugepage(size, read_write)

    def mmap_free_hugepage(self, addr: int, size: int) -> bool:
        """Free huge page memory."""
        return self._ext.mmap_free_hugepage(addr, size)

    def mmap_hugepage(self, path: str, read_only: bool = False) -> tuple[int, int]:
        """Memory-map a file with huge pages."""
        return self._ext.mmap_hugepage(path, read_only)

    def munmap_hugepage(self, addr: int, size: int) -> bool:
        """Unmap huge page memory."""
        return self._ext.munmap_hugepage(addr, size)

    def get_hugepage_size(self) -> int:
        """Get system huge page size in bytes."""
        return self._ext.get_hugepage_size()


class _PythonMadvisDomain:
    __slots__ = ()

    def madvise_on_mmap_region(self, addr: int, length: int, advice: int = 7) -> bool:
        """Python fallback: apply madvise to memory region."""
        return _python_madvise_free_reusable(addr, length)

    def madvise_hugepage(self, addr: int, length: int) -> bool:
        """Python fallback: hint for huge page usage."""
        return False

    def mmap_alloc_with_hugepage(self, size: int, read_write: bool = True) -> tuple[int, int]:
        """Python fallback: return (0, 0) - not supported."""
        return (0, 0)

    def mmap_free_hugepage(self, addr: int, size: int) -> bool:
        """Python fallback: return False - not supported."""
        return False

    def mmap_hugepage(self, path: str, read_only: bool = False) -> tuple[int, int]:
        """Python fallback: return (0, 0) - not supported."""
        return (0, 0)

    def munmap_hugepage(self, addr: int, size: int) -> bool:
        """Python fallback: return False - not supported."""
        return False

    def get_hugepage_size(self) -> int:
        """Python fallback: return 0 - not supported."""
        return 0


def _python_madvise_free_reusable(addr: int, length: int) -> bool:
    """Python fallback: call madvise with MADV_FREE_REUSABLE on macOS."""
    global _LIB, _MADV_FREE_REUSABLE
    if _LIB is None:
        return False
    try:
        result = _LIB.madvise(addr, length, _MADV_FREE_REUSABLE)
        return result == 0
    except Exception:
        return False


def get_madvise_domain(ext: object | None) -> _RustMadvisDomain | _PythonMadvisDomain:
    """Factory: return Rust or Python MadvisDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustMadvisDomain(ext)
        except Exception:
            pass
    return _PythonMadvisDomain()
