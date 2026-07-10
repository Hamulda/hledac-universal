"""
DEPRECATED (F270): Sdílené executory — nahrazeno Rust rayon pools.

Tento modul je ZASTARALÝ a bude odstraněn v budoucím sprintu.
Používejte místo něj utils.rayon_pool:
  - run_in_cpu_pool() — CPU-bound workloads (4 P-cores)
  - run_in_io_pool()  — I/O-bound workloads (2 threads, DuckDB ceiling)
  - run_in_mixed_pool(n) — adaptive mixed (1-2 threads)

Důvody pro nahrazení:
  - Rust rayon je GIL-free (skutečný paralelismus)
  - Work-stealing scheduler (lepší load balancing)
  - M1 cache-friendly (sdílený adresní prostor)
  - macOS QoS integrace (E/P cores)

Migrace:
  PŘED:  await loop.run_in_executor(CPU_EXECUTOR, fn)
  PO:    await asyncio.to_thread(run_in_cpu_pool, fn, queue_depth_hint=N)
"""


import atexit
import warnings
from concurrent.futures import ThreadPoolExecutor

__all__ = ["CPU_EXECUTOR", "IO_EXECUTOR", "shutdown_all_executors"]

warnings.warn(
    "DEPRECATED (F270): utils.executors is deprecated. "
    "Use utils.rayon_pool (run_in_cpu_pool, run_in_io_pool) instead. "
    "See rust_extensions/src/lib.rs:51-164 for pool documentation.",
    DeprecationWarning,
    stacklevel=2,
)

# M1: 4E+4P cores — CPU-bound dostane 2 performance cores, IO dostane 4 pro síťové čekání
# These are kept for backward compatibility during migration period.
CPU_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="hledac_cpu"
)
IO_EXECUTOR: ThreadPoolExecutor = ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="hledac_io"
)


def shutdown_all_executors(wait: bool = True) -> None:
    """Shutdown both shared executors. Called automatically via atexit."""
    CPU_EXECUTOR.shutdown(wait=wait)
    IO_EXECUTOR.shutdown(wait=wait)


atexit.register(shutdown_all_executors, wait=False)
