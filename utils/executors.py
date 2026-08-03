"""
DEPRECATED (F270): Shared executors — replaced by domain_executors R5 registry.

This module is DEPRECATED and will be removed in a future sprint.
Use domain_executors.get_or_create() instead:
  - CPU_EXECUTOR  → get_legacy_cpu_executor()
  - IO_EXECUTOR   → get_legacy_io_executor()

R5 FIX (2026-07-19): Module-level ThreadPoolExecutor instantiation replaced
with lazy get_or_create() calls via PEP 562 __getattr__. No threads are
created at import time — pools are only created on first attribute access.

Reasons for replacement:
  - Hard cap enforcement (24 threads on M1 8GB)
  - Multi-layer shutdown guarantee (signal + atexit + weakref)
  - Memory-pressure-aware emergency cap (12 threads)
  - cancel_futures=True for zero-stall shutdown (Python 3.14)

Migration:
  BEFORE:  await loop.run_in_executor(CPU_EXECUTOR, fn)
  AFTER:   await asyncio.to_thread(run_in_cpu_pool, fn, queue_depth_hint=N)
"""

import atexit
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import Any

warnings.warn(
    "DEPRECATED (F270): utils.executors is deprecated. "
    "Use domain_executors.get_legacy_cpu_executor() / get_legacy_io_executor() "
    "or utils.rayon_pool (run_in_cpu_pool, run_in_io_pool) instead. "
    "See domain_executors.py for executor lifecycle management.",
    DeprecationWarning,
    stacklevel=2,
)

# R5 FIX: No executors at import time.
# PEP 562 __getattr__ provides lazy resolution of CPU_EXECUTOR / IO_EXECUTOR.
# Threads are created on FIRST ACCESS, not at module level.

_CPU_EXECUTOR: ThreadPoolExecutor | None = None
_IO_EXECUTOR: ThreadPoolExecutor | None = None
_lazy_lock = __import__('threading').Lock()


def _get_cpu_executor() -> ThreadPoolExecutor:
    """Lazily create or retrieve the CPU executor from domain_executors."""
    global _CPU_EXECUTOR
    if _CPU_EXECUTOR is not None:
        return _CPU_EXECUTOR
    with _lazy_lock:
        if _CPU_EXECUTOR is not None:
            return _CPU_EXECUTOR
        from hledac.universal.utils.domain_executors import get_legacy_cpu_executor
        _CPU_EXECUTOR = get_legacy_cpu_executor()
        return _CPU_EXECUTOR


def _get_io_executor() -> ThreadPoolExecutor:
    """Lazily create or retrieve the IO executor from domain_executors."""
    global _IO_EXECUTOR
    if _IO_EXECUTOR is not None:
        return _IO_EXECUTOR
    with _lazy_lock:
        if _IO_EXECUTOR is not None:
            return _IO_EXECUTOR
        from hledac.universal.utils.domain_executors import get_legacy_io_executor
        _IO_EXECUTOR = get_legacy_io_executor()
        return _IO_EXECUTOR


def __getattr__(name: str) -> Any:
    """PEP 562: Lazy module-level attribute resolution.

    ThreadPoolExecutor instances are created on first access, not at import time.
    This addresses Issue 4 — no threads allocated until actually used.

    Supported attributes:
        CPU_EXECUTOR → 2 workers, M1 performance cores
        IO_EXECUTOR  → 4 workers, M1 efficiency cores
    """
    if name == "CPU_EXECUTOR":
        return _get_cpu_executor()
    if name == "IO_EXECUTOR":
        return _get_io_executor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def shutdown_all_executors(wait: bool = True) -> None:
    """Shutdown both shared executors. Called automatically via atexit.

    R5: Delegates to domain_executors.shutdown_all() which handles
    all registered executors with cancel_futures=True.
    """
    from hledac.universal.utils.domain_executors import shutdown_all
    shutdown_all()


# R5: atexit replaced by domain_executors' multi-layer shutdown.
# Old: atexit.register(shutdown_all_executors, wait=False)
# New: domain_executors handles signal + atexit + weakref shutdown.

__all__ = ["CPU_EXECUTOR", "IO_EXECUTOR", "shutdown_all_executors"]
