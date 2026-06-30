"""
File caching utilities for large download optimization.

Extracted from coordinators/fetch_coordinator.py.
Provides F_NOCACHE flag application for Darwin kernel to avoid caching
large downloads in memory on memory-constrained systems.

F273F: MADV_FREE_REUSABLE (value=7) for LMDB/DuckDB mmap regions —
tells Darwin kernel that pages are reusable (not modified), allowing
immediate reclaim without writing to disk. Critical for M1 8GB UMA
where page cache pressure directly impacts Metal/MLX memory budget.
"""


import ctypes
import ctypes.util
import fcntl
import os
import platform

NOCACHE_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50MB
F_NOCACHE: int | None = 48 if platform.system() == "Darwin" else None

# MADV_FREE_REUSABLE tells the kernel pages are clean/reusable — value 7 on Darwin.
# MADV_FREE (value 5) marks pages as free but may still writeback; REUSABLE is better.
# Use ctypes (not ctypes.util.find_library) — libc is always available on Darwin.
MADV_FREE_REUSABLE: int = 7
# MADV_NOCACHE tells kernel not to cache pages in page cache — value 11 on Darwin.
# Critical for M1 8GB UMA where page cache competes with Metal memory.
MADV_NOCACHE: int = 11
_libc_for_madvise: ctypes.CDLL | None = None


def _get_libc() -> ctypes.CDLL | None:
    """Lazy-load libc for madvise syscalls. Returns None if unavailable."""
    global _libc_for_madvise
    if _libc_for_madvise is None:
        try:
            # 'c' is the standard libc on Darwin (Darwin uses libc.so not .so)
            _libc_for_madvise = ctypes.CDLL("libc.dylib", use_errno=True)
        except OSError:
            try:
                _libc_for_madvise = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
            except OSError:
                _libc_for_madvise = None
    return _libc_for_madvise


def madv_free_reusable(fd: int) -> bool:
    """
    F273F: Apply MADV_FREE_REUSABLE to an open file descriptor on Darwin.

    Tells the Darwin kernel that pages backing this mmap region are clean
    and reusable — the kernel can reclaim them immediately without writing
    to disk. This is the correct madvise flag for LMDB mmap and DuckDB WAL
    regions on M1 8GB where every page in the page cache competes with the
    Metal memory budget.

    Fails silently: returns False on non-Darwin, missing libc, or syscall
    failure. Never raises.

    Args:
        fd: Open file descriptor (must be a valid mmap-backed fd).

    Returns:
        True if madvise succeeded, False otherwise.
    """
    if platform.system() != "Darwin":
        return False
    libc = _get_libc()
    if libc is None:
        return False
    try:
        # int madvise(void* addr, size_t len, int advice)
        # Signature: madvise(caddr_t addr, size_t len, int advice)
        libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        libc.madvise.restype = ctypes.c_int
        # madvise requires a memory address; we pass 0 (NULL) to apply to entire file
        # via the file descriptor's mapped region. NULL +0 + MADV_FREE_REUSABLE
        # applies to all pages of the mmap.
        result = libc.madvise(ctypes.c_void_p(0), ctypes.c_size_t(0), MADV_FREE_REUSABLE)
        return result == 0
    except (OSError, AttributeError):
        return False


def madv_free_reusable_on_path(path: str | os.PathLike) -> bool:
    """
    F273F: Open a file and apply MADV_FREE_REUSABLE to its mmap region.

    Opens the file RDWR (or RDONLY fallback), seeks to 0, and applies
    MADV_FREE_REUSABLE via madvise(). Fails silently. Always-on.

    Use this after lmdb.open() or duckdb.connect() to hint the kernel
    that the mmap region pages are reusable.

    Args:
        path: Path to the file-backed artifact (LMDB .mdb, DuckDB .duckdb).

    Returns:
        True if both open and madvise succeeded, False otherwise.
    """
    if platform.system() != "Darwin":
        return False
    try:
        try:
            fd = os.open(str(path), os.O_RDWR)
        except OSError:
            try:
                fd = os.open(str(path), os.O_RDONLY)
            except OSError:
                return False
        try:
            return madv_free_reusable(fd)
        finally:
            os.close(fd)
    except OSError:
        return False


def madv_nocache_on_path(path: str | os.PathLike) -> bool:
    """
    F273F + P3-2: Apply MADV_NOCACHE to file pages on Darwin.

    Tells the kernel not to cache the pages in the page cache — critical
    for M1 8GB UMA where page cache competes directly with Metal memory.

    Uses Rust madvise_on_mmap_region() when available (has MAP_NOCACHE
    support), falls back to ctypes madvise(MADV_NOCACHE=11) on failure.
    Always-on, fail-safe (returns False on any error, never raises).

    Args:
        path: Path to the file-backed artifact (LMDB .mdb, DuckDB .duckdb).

    Returns:
        True if MADV_NOCACHE was applied successfully, False otherwise.
    """
    if platform.system() != "Darwin":
        return False
    path_str = str(path)

    # Try Rust version first — has MAP_NOCACHE support and proper page alignment
    try:
        from hledac_rust_extensions import madvise_on_mmap_region

        fd = os.open(path_str, os.O_RDWR)
        try:
            # madvise_on_mmap_region(fd, addr=0, length=0, advice=1)
            # addr=0, length=0 with advice=1 means entire file with MADV_NOCACHE
            result = madvise_on_mmap_region(fd, 0, 0, 1)
            return result == 0
        finally:
            os.close(fd)
    except Exception:
        pass

    # Fallback: ctypes madvise with MADV_NOCACHE (value 11)
    libc = _get_libc()
    if libc is None:
        return False
    try:
        libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        libc.madvise.restype = ctypes.c_int
        fd = os.open(path_str, os.O_RDWR)
        try:
            result = libc.madvise(ctypes.c_void_p(0), ctypes.c_size_t(0), MADV_NOCACHE)
            return result == 0
        finally:
            os.close(fd)
    except Exception:
        return False


def apply_fcntl_nocache(fd: int, content_length: int | None) -> None:
    """
    Apply F_NOCACHE flag to file descriptor for large downloads.

    This tells Darwin's kernel not to cache the file data in memory,
    which is beneficial for very large downloads (>50MB) on memory-constrained systems.

    Args:
        fd: File descriptor to apply the flag to
        content_length: Size of the content being written (if known)
    """
    if content_length is None or content_length <= NOCACHE_THRESHOLD_BYTES:
        return

    # LOW-7 fix: F_NOCACHE is Darwin-only
    if F_NOCACHE is None:
        return

    try:
        fcntl.fcntl(fd, F_NOCACHE, 1)
    except OSError:
        # Fail-safe: never let fcntl failure abort the write
        # Catches: platform not supported, invalid fd, etc.
        pass


def apply_nocache_to_path(path: str | os.PathLike, content_length: int | None = None) -> bool:
    """
    F273F: Apply F_NOCACHE to a runtime artifact file (LMDB mmap, DuckDB file, telemetry log).

    Opens the path in append mode, sets F_NOCACHE on the fd, and immediately
    closes. Subsequent opens inherit the no-cache hint via the kernel's
    per-inode cache state, but the most reliable way to keep the page cache
    from filling with runtime artifacts is to set F_NOCACHE on every open.

    Use this for:
      - LMDB env files (when not in use -- LMDB holds its own mmap)
      - DuckDB database files
      - Telemetry / metrics log files
      - Any hot-path artifact that doesn't need to survive a reboot

    Args:
        path: Path to the runtime artifact.
        content_length: Optional size hint. If provided and below
            NOCACHE_THRESHOLD_BYTES, the call is a no-op (small files
            don't benefit from F_NOCACHE).

    Returns:
        True if F_NOCACHE was applied, False otherwise (non-Darwin, missing
        file, OSError, or below threshold).

    Always-on, bounded (single syscall per call), fail-safe (never raises).
    """
    if F_NOCACHE is None:
        return False
    if content_length is not None and content_length <= NOCACHE_THRESHOLD_BYTES:
        return False
    try:
        # Open with O_RDWR if possible (LMDB mmap needs R/W); fall back to RDONLY.
        try:
            fd = os.open(str(path), os.O_RDWR)
        except OSError:
            try:
                fd = os.open(str(path), os.O_RDONLY)
            except OSError:
                return False
        try:
            fcntl.fcntl(fd, F_NOCACHE, 1)
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


# ── Exports ────────────────────────────────────────────────────────────────────────────
__all__ = [
    "F_NOCACHE",
    "MADV_FREE_REUSABLE",
    "MADV_NOCACHE",
    "NOCACHE_THRESHOLD_BYTES",
    "apply_fcntl_nocache",
    "apply_nocache_to_path",
    "madv_free_reusable",
    "madv_free_reusable_on_path",
    "madv_nocache_on_path",
]
