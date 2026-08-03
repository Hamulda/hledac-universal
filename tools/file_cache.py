"""
File caching utilities for large download optimization.

Extracted from coordinators/fetch_coordinator.py.
Provides F_NOCACHE flag application for Darwin kernel to avoid caching
large downloads in memory on memory-constrained systems.

F273F: MADV_FREE_REUSABLE (value=7) for LMDB/DuckDB mmap regions —
tells Darwin kernel that pages are reusable (not modified), allowing
immediate reclaim without writing to disk. Critical for M1 8GB UMA
where page cache pressure directly impacts Metal/MLX memory budget.

R-03: madv_free_reusable and madv_free_reusable_on_path removed —
they called madvise(NULL, 0, advice) which always returns EINVAL.
Use madvise_lmdb_mmap(path, advice=1) for MAP_NOCACHE on LMDB/DuckDB.
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


def madvise_lmdb_mmap(path: str | os.PathLike, advice: int = 1) -> bool:
    """
    R-03: Apply madvise to a file via Rust madvise_lmdb_mmap().

    This is the correct implementation: opens the file, mmaps it with
    MAP_NOCACHE (Darwin), then applies MADV_NOCACHE (advice=1) or
    MADV_FREE_REUSABLE (advice=0) to the mapped region, then unmaps.

    Replaces the broken madv_free_reusable_on_path() which called
    madvise(NULL, 0, advice) — always returned EINVAL.

    Args:
        path: Path to the file-backed artifact (LMDB .mdb, DuckDB .duckdb).
        advice: 0=MADV_FREE_REUSABLE, 1=MADV_NOCACHE (default, recommended).

    Returns:
        True if madvise succeeded, False otherwise.
    """
    if platform.system() != "Darwin":
        return False
    path_str = str(path)
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    _rust_madvise = rust.raw.madvise_lmdb_mmap
    if _rust_madvise is None:
        return False
    try:
        result = _rust_madvise(path_str, advice)
        return result == 0
    except Exception:  # noqa: BLE001
        return False


def madv_nocache_on_path(path: str | os.PathLike) -> bool:
    """
    F273F + P3-2 + R-03: Apply MADV_NOCACHE to file pages on Darwin.

    Tells the kernel not to cache the pages in the page cache — critical
    for M1 8GB UMA where page cache competes directly with Metal memory.

    Uses Rust madvise_lmdb_mmap() (has MAP_NOCACHE support with proper
    page-aligned mmap), falls back to ctypes madvise(MADV_NOCACHE=11).
    Always-on, fail-safe (returns False on any error, never raises).

    Args:
        path: Path to the file-backed artifact (LMDB .mdb, DuckDB .duckdb).

    Returns:
        True if MADV_NOCACHE was applied successfully, False otherwise.
    """
    return madvise_lmdb_mmap(path, advice=1)  # MADV_NOCACHE


# QoS class constants for apply_thread_qos
QOS_CLASS_BACKGROUND: int = 0x1
QOS_CLASS_UTILITY: int = 0x2
QOS_CLASS_DEFAULT: int = 0x3
QOS_CLASS_INTERACTIVE: int = 0x6  # Fixed: was 0x5 (B-5)
QOS_CLASS_USER_INITIATED: int = 0x9


def apply_thread_qos(qos_class: int) -> bool:
    """
    F350M-R 5.5: Set QoS class for the current thread (B-5 fix).

    QoS classes on macOS:
        0x1 = QOS_CLASS_BACKGROUND — lowest priority (vacuum/close threads)
        0x2 = QOS_CLASS_UTILITY
        0x3 = QOS_CLASS_DEFAULT
        0x6 = QOS_CLASS_INTERACTIVE  # Fixed: was 0x5 (B-5)
        0x9 = QOS_CLASS_USER_INITIATED — highest priority (inference threads)

    Uses Rust apply_current_thread_qos(qos_class) internally.
    Falls back silently on non-macOS or if unavailable.

    B-5 fix: pthread_id parameter removed from Rust API. The old
    apply_thread_qos(pthread_id=0, ...) always set the CALLING thread
    regardless of pthread_id value — now fixed with apply_current_thread_qos.

    Args:
        qos_class: QoS class constant (e.g. QOS_CLASS_BACKGROUND for vacuum).

    Returns:
        True if QoS was set successfully, False otherwise.
    """
    try:
        from hledac.universal.core.rust_backend import rust
        if rust.is_available:
            # B-5: new API — no pthread_id needed (always sets calling thread)
            return rust.apply_current_thread_qos(qos_class) == 0
    except Exception:
        pass
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
    "madvise_lmdb_mmap",
    "madv_nocache_on_path",
]
