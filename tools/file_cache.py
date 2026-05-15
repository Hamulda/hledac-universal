"""
File caching utilities for large download optimization.

Extracted from coordinators/fetch_coordinator.py.
Provides F_NOCACHE flag application for Darwin kernel to avoid caching
large downloads in memory on memory-constrained systems.
"""

from __future__ import annotations

import fcntl
import platform
from typing import Optional

NOCACHE_THRESHOLD_BYTES = 50 * 1024 * 1024  # 50MB
F_NOCACHE: Optional[int] = 48 if platform.system() == "Darwin" else None


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