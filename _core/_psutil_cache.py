"""
core._psutil_cache — Centralized cached psutil reads for M1 8GB UMA.

ROLE: Canonical cached-psutil surface. Eliminates blocking psutil.virtual_memory()
syscalls from hot paths by centralizing reads behind a 1-second TTL cache.

Authority boundary (per resource_governor.py):
  - SAMPLER  (utils/uma_budget.py): raw memory sampling, no policy
  - GOVERNOR (core/resource_governor.py): policy / hysteresis / runtime governance
  - ALLOCATOR (resource_allocator.py): request-level budgeting / concurrency
  - CACHE    (core/_psutil_cache.py): cached psutil reads for callers
             outside the Governor's own call graph

Why separate from resource_governor.py:
  - resource_governor is the UMA POLICY authority — it defines thresholds,
    hysteresis, and runtime governance decisions.
  - This module is a low-level CACHE primitive — it simply caches the
    raw psutil reads so that callers who need memory snapshots WITHOUT
    going through the Governor's policy layer can do so without blocking.
  - Both modules use the same _psutil_cache dict and TTL (1.0 s), so
    reads stay coherent across the two surfaces.

Usage:
    from hledac.universal._core._psutil_cache import get_virtual_memory, get_swap_memory

    vm = get_virtual_memory()   # returns psutil.Process().virtual_memory(), cached 1s
    sm = get_swap_memory()      # returns psutil.Process().swap_memory(), cached 1s

Invariant:
  - Always-on, no feature flags.
  - Thread-safe: dict operations guarded by _psutil_meta_lock.
  - Fail-safe: if psutil is unavailable, returns None (not a blocking call).
  - 1-second TTL per entry; stale entries purged on next access.
"""

import threading
import time as _time_module
from collections.abc import Callable
from typing import Any
from _core._util import aclose

# Deferred import — psutil is a hard dependency of the M1 8GB stack but we
# fail-safe rather than crash if it's somehow absent.
_psutil: Any | None = None
try:
    import psutil

    _psutil = psutil
except ImportError:  # noqa: BLE001
    pass  # _psutil stays None, fail-safe


# ----------------------------------------------------------------------------------------------------------------------
# 1-second TTL cache — shared with resource_governor._psutil_cache
# ----------------------------------------------------------------------------------------------------------------------
_psutil_cache: dict[str, tuple[Any, float]] = {}  # key → (result, timestamp)
_psutil_meta_lock = threading.Lock()  # guards dict operations only
_CACHE_TTL_S: float = 1.0


# ----------------------------------------------------------------------------------------------------------------------
# Readers — MUST run in a thread (blocking syscalls)
# ----------------------------------------------------------------------------------------------------------------------


def _read_virtual_memory_sync() -> Any:
    """Blocking psutil.virtual_memory(). Must run in a thread, not the event loop."""
    if _psutil is None:
        return None
    return _psutil.virtual_memory()


def _read_swap_memory_sync() -> Any:
    """Blocking psutil.swap_memory(). Must run in a thread, not the event loop."""
    if _psutil is None:
        return None
    return _psutil.swap_memory()


# ----------------------------------------------------------------------------------------------------------------------
# Public API — cached reads
# ----------------------------------------------------------------------------------------------------------------------


def get_virtual_memory() -> Any:
    """
    Return cached psutil.virtual_memory() result.

    Thread-safe, 1-second TTL. On psutil error or import failure returns None.
    """
    return _get_cached("virtual_memory", _read_virtual_memory_sync)


def get_swap_memory() -> Any:
    """
    Return cached psutil.swap_memory() result.

    Thread-safe, 1-second TTL. On psutil error or import failure returns None.
    """
    return _get_cached("swap_memory", _read_swap_memory_sync)


def _get_cached(key: str, reader_fn: Callable[[], Any]) -> Any:
    """
    Thread-safe cached read with 1-second TTL.

    Fast path: TTL cache hit → return cached, no syscall, no thread.
    Slow path: TTL expired → call reader_fn() directly (caller already
    runs this via asyncio.to_thread or a dedicated monitor thread).
    Fail-safe: purge entry on error so next call retries cleanly.

    Invariant: always-on, bounded, fail-safe. No thread spawn overhead
    on cache miss — the monitor thread or to_thread pool owns the
    blocking call; this is a cache layer, not an executor.
    """
    now = _time_module.monotonic()

    # Fast path — no lock needed for TTL check (read is atomic in Python)
    entry = _psutil_cache.get(key)
    if entry is not None:
        result, timestamp = entry
        if (now - timestamp) < _CACHE_TTL_S:
            return result

    # Slow path — dict write needs the lock
    with _psutil_meta_lock:
        # Re-check after acquiring lock (another thread may have populated it)
        entry = _psutil_cache.get(key)
        if entry is not None:
            result, timestamp = entry
            if (now - timestamp) < _CACHE_TTL_S:
                return result

        # Call reader directly — caller runs this in a thread or monitor loop.
        # TTL cache amortises the cost: one call per 1-second window regardless
        # of how many callers hit the same key.
        try:
            result = reader_fn()
        except Exception:
            # Purge stale entry so next caller retries
            _psutil_cache.pop(key, None)
            return None

        if result is not None:
            _psutil_cache[key] = (result, now)
        return result


def reset() -> None:
    """Clear the entire cache. Use when entering a new execution phase."""
    with _psutil_meta_lock:
        _psutil_cache.clear()


__all__ = [
    "get_virtual_memory",
    "get_swap_memory",
    "reset",
]
