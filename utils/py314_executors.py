"""
py314_executors — DEPRECATED (2026-07-12)
============================================





This module is DEPRECATED. All functionality has been consolidated into:

  runtime/worker_pool.py — SharedWorkerPool singleton (M1 8GB-safe)

MIGRATION:
  OLD:
    from hledac.universal.utils.py314_executors import ChunkedExecutor, smart_executor

  NEW:
    from hledac.universal.runtime.worker_pool import get_shared_pool, io_bound
    # For CPU-bound work:
    await get_shared_pool().run(cpu_intensive_fn, *args)
    # For I/O-bound work:
    await io_bound(io_heavy_fn, *args)

For Rust rayon pools (NEON SIMD on M1):
    from hledac.universal.core.rust_backend import rust
    # cpu_pool_run (4 P-cores), io_pool_run (2 threads)
    rust.pool_run.cpu_pool_run(func, args)

RATIONALE:
  - ProcessPoolExecutor: ~50MB RSS per worker, never actually used (dead code)
  - InterpreterPoolExecutor: PEP 756 unstable, MLX incompatible (single interpreter)
  - Rust rayon: GIL-free via PyO3 allow_threads, NEON SIMD on M1

Issue 9 fix: InterpreterPoolExecutor now gated behind runtime probe
(HLEDAC_ENABLE_SUBINTERPRETERS=1 + CPython --with-experimental-isolated-subinterpreters).
Import alone is insufficient for Python 3.14.6.
"""
import logging
import math
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass
import msgspec
from typing import Any, TypeVar
from core import aclose

logger = logging.getLogger(__name__)

__all__ = ['ChunkedExecutor', 'smart_executor', 'ExecutorType', 'get_optimal_chunksize']
T = TypeVar('T')
R = TypeVar('R')

class ExecutorType:
    THREAD = 'thread'
    PROCESS = 'process'
    INTERPRETER = 'interpreter'
_MAX_THREAD_WORKERS = 25
_MAX_PROCESS_WORKERS = 4
_MAX_INTERPRETER_WORKERS = 2
_MIN_CHUNKSIZE = 100

class ExecutorConfig(msgspec.Struct, frozen=True, gc=False):
    """Immutable executor configuration."""
    executor_type: str
    max_workers: int
    chunksize: int
    use_process_pool: bool
    reason: str

def get_optimal_chunksize(n_items: int, n_workers: int, executor_type: str=ExecutorType.THREAD) -> int:
    """
    Calculate optimal chunksize for parallel execution.

    InterpreterPoolExecutor: aim for n_workers * 4 chunks (amortizes overhead)
    ThreadPoolExecutor: aim for n_workers * 2 chunks (balances load)
    ProcessPoolExecutor: aim for n_workers * 2 chunks (IPC overhead)

    Rules:
      - Never below _MIN_CHUNKSIZE (unless items < _MIN_CHUNKSIZE)
      - Round up to nearest power of 2 for cache alignment
      - Clamp to reasonable bounds

    >>> get_optimal_chunksize(10000, 4, ExecutorType.INTERPRETER)
    625
    >>> get_optimal_chunksize(100, 4, ExecutorType.THREAD)
    100
    >>> get_optimal_chunksize(50, 4, ExecutorType.INTERPRETER)
    50
    """
    if n_items == 0:
        return 1
    if n_items <= _MIN_CHUNKSIZE:
        return max(1, n_items)
    target_chunks = {ExecutorType.INTERPRETER: n_workers * 4, ExecutorType.THREAD: n_workers * 2, ExecutorType.PROCESS: n_workers * 2}.get(executor_type, n_workers * 2)
    raw_chunksize = math.ceil(n_items / target_chunks)
    chunksize = max(raw_chunksize, _MIN_CHUNKSIZE)
    max_chunk = n_items
    chunksize = min(chunksize, max_chunk)
    return chunksize

class ChunkedExecutor:
    """
    ThreadPoolExecutor wrapper with automatic chunking for CPU-bound work.

    Design rationale:
      Standard `executor.map(fn, items)` submits one item at a time.
      For InterpreterPoolExecutor this means N subinterpreter calls.
      ChunkedExecutor batches items into chunks, reducing calls by ~workers×.

    M1 8GB note:
      Uses ThreadPoolExecutor (not InterpreterPoolExecutor) because:
        1. Most CPU work in hledac releases GIL (C extensions: ahocorasick,
           orjson, msgspec, pyahocorasick, lz4, zstd)
        2. Subinterpreters add ~15-30MB RSS overhead × workers = OOM risk
        3. ThreadPoolExecutor is ~few MB overhead total
        4. For truly heavy pure-Python, ProcessPoolExecutor is better

    Chunking is still valuable for ThreadPoolExecutor because:
        - Reduces Python object threading overhead (GIL held briefly)
        - Allows C extension work to run uninterrupted
        - Batch scheduling reduces context switches

    Usage:
        with ChunkedExecutor(max_workers=4, chunksize=2500) as ex:
            results = list(ex.map(fn, items))
    """
    __slots__ = tuple(('_chunksize_override', '_executor', 'executor_type', 'max_workers'))

    def __init__(self, max_workers: int | None=None, chunksize: int | None=None, executor_type: str=ExecutorType.THREAD) -> None:
        if max_workers is None:
            max_workers = min(os.cpu_count() or 4, _MAX_THREAD_WORKERS)
        self.max_workers = max_workers
        self.executor_type = executor_type
        self._executor: ThreadPoolExecutor | ProcessPoolExecutor | None = None
        self._chunksize_override = chunksize

    def _get_chunksize(self, n_items: int) -> int:
        if self._chunksize_override is not None:
            return self._chunksize_override
        return get_optimal_chunksize(n_items, self.max_workers, self.executor_type)

    def __enter__(self) -> ChunkedExecutor:
        if self.executor_type == ExecutorType.PROCESS:
            self._executor = ProcessPoolExecutor(max_workers=self.max_workers)
        elif self.executor_type == ExecutorType.INTERPRETER:
            # Issue 9 fix: Runtime gate — InterpreterPoolExecutor requires
            # HLEDAC_ENABLE_SUBINTERPRETERS=1 + CPython --with-experimental-isolated-subinterpreters.
            # Import alone is NOT sufficient — the subinterpreter API is experimental
            # in Python 3.14.6 and may fail at runtime without the build flag.
            from hledac.universal.runtime.execution_gateway import subinterpreter_available

            if not subinterpreter_available():
                logger.debug(
                    'InterpreterPoolExecutor requested but subinterpreter support '
                    'not available — falling back to ThreadPoolExecutor'
                )
                self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
            else:
                try:
                    from concurrent.futures import InterpreterPoolExecutor
                    self._executor = InterpreterPoolExecutor(max_workers=self.max_workers)
                except ImportError:
                    logger.debug('InterpreterPoolExecutor import failed — falling back to ThreadPoolExecutor')
                    self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
                except Exception as exc:
                    logger.warning('InterpreterPoolExecutor init failed: %s — falling back to ThreadPoolExecutor', exc)
                    self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        else:
            self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._executor is not None:
            self._executor.__exit__(*args)
            self._executor = None

    def map(self, fn: Callable[[T], R], items: list[T], chunksize: int | None=None) -> Iterator[R]:
        """
        Map fn over items with automatic chunking.

        Unlike executor.map which submits 1 item per call, this batches
        items into chunks to amortize scheduling overhead.

        Args:
            fn: Callable to apply to each item
            items: List of items to process
            chunksize: Override automatic chunksize (optional)

        Yields:
            Results in order (same as executor.map)

        >>> with ChunkedExecutor(max_workers=2) as ex:
        ...     results = list(ex.map(lambda x: x*2, [1, 2, 3, 4]))
        [2, 4, 6, 8]
        """
        if not items:
            return
        if self._executor is None:
            raise RuntimeError('ChunkedExecutor must be used as context manager')
        chunk_size = chunksize if chunksize is not None else self._get_chunksize(len(items))
        yield from self._executor.map(fn, items, chunksize=chunk_size)

class WorkloadProfile(msgspec.Struct, frozen=True, gc=False):
    """Describes a workload's characteristics for executor selection."""
    name: str
    estimated_cpu_ms_per_item: float
    item_size_bytes: int
    releases_gil: bool
    needs_isolation: bool
    n_items: int

def smart_executor(profile: WorkloadProfile | None=None, *, n_items: int=0, cpu_ms_per_item: float=1.0, item_size_bytes: int=100, releases_gil: bool=True, needs_isolation: bool=False, executor_type_hint: str | None=None) -> ChunkedExecutor:
    """
    Create an optimally configured ChunkedExecutor for the given workload.

    Selection logic (M1 8GB-aware):

      1. If executor_type_hint provided, use it (allows override)
      2. If needs_isolation=True → ProcessPoolExecutor
      3. If not releases_gil AND cpu_ms_per_item > 50ms → ProcessPoolExecutor
      4. If cpu_ms_per_item > 500ms AND item_size_bytes > 100KB → ProcessPoolExecutor
      5. Otherwise → ThreadPoolExecutor (InterpreterPoolExecutor showed no
         benefit for our C-extension-heavy workloads in benchmarking)

    M1 8GB constraints:
      - ProcessPoolExecutor: 4 workers max, ~50MB RSS overhead each
      - ThreadPoolExecutor: 25 workers, ~few MB overhead total
      - InterpreterPoolExecutor: 4 workers, ~15-30MB RSS each, no benefit for GIL-releasing C ext

    Args:
        profile: WorkloadProfile dataclass (alternative to kwargs)
        n_items: Number of items to process
        cpu_ms_per_item: Estimated CPU time per item in milliseconds
        item_size_bytes: Average serialized size per item
        releases_gil: Does the workload release the GIL? (C ext / I/O = True)
        needs_isolation: Requires process-level isolation?
        executor_type_hint: Force a specific executor type

    Returns:
        Configured ChunkedExecutor instance (must be used as context manager)

    Example:
        with smart_executor(n_items=10000, cpu_ms_per_item=0.5, releases_gil=True) as ex:
            results = list(ex.map(fn, items))
    """
    if profile is not None:
        n_items = profile.n_items
        cpu_ms_per_item = profile.estimated_cpu_ms_per_item
        item_size_bytes = profile.item_size_bytes
        releases_gil = profile.releases_gil
        needs_isolation = profile.needs_isolation
    if executor_type_hint is not None:
        ex_type = executor_type_hint
        reason = f'hint={executor_type_hint}'
    elif needs_isolation:
        ex_type = ExecutorType.PROCESS
        reason = 'needs_isolation=True'
    elif not releases_gil and cpu_ms_per_item > 50:
        ex_type = ExecutorType.PROCESS
        reason = f'pure-Python slow ({cpu_ms_per_item:.0f}ms > 50ms)'
    elif cpu_ms_per_item > 500 and item_size_bytes > 100000:
        ex_type = ExecutorType.PROCESS
        reason = f'heavy workload ({cpu_ms_per_item:.0f}ms, {item_size_bytes}B)'
    else:
        if n_items >= 5000 and cpu_ms_per_item >= 1.0 and (not releases_gil):
            ex_type = ExecutorType.INTERPRETER
            reason = f'pure-Python CPU ({cpu_ms_per_item:.0f}ms/item), IPP chunking beneficial'
        else:
            ex_type = ExecutorType.THREAD
            reason = 'lightweight/IO/GIL-releasing workload, ThreadPoolExecutor optimal'
        if not releases_gil:
            reason = f'pure-Python fast ({cpu_ms_per_item:.0f}ms < 50ms), ThreadPoolExecutor sufficient'
        else:
            reason = 'GIL-releasing C-extension/I/O workload, ThreadPoolExecutor optimal'
    max_workers = {ExecutorType.PROCESS: _MAX_PROCESS_WORKERS, ExecutorType.INTERPRETER: _MAX_INTERPRETER_WORKERS}.get(ex_type, _MAX_THREAD_WORKERS)
    chunksize = get_optimal_chunksize(n_items, max_workers, ex_type)
    return ChunkedExecutor(max_workers=max_workers, chunksize=chunksize, executor_type=ex_type)

def batch_map[T, R](fn: Callable[[T], R], items: list[T], *, max_workers: int | None=None, chunksize: int | None=None, executor_type: str=ExecutorType.THREAD) -> list[R]:
    """
    Process items in parallel using ChunkedExecutor.

    Convenience function wrapping ChunkedExecutor for simple usage.

    Args:
        fn: Function to apply to each item
        items: Items to process
        max_workers: Max thread/process workers (default: auto)
        chunksize: Items per chunk (default: auto-calculated)
        executor_type: THREAD | PROCESS | INTERPRETER

    Returns:
        List of results in same order as items

    Example:
        results = batch_map(lambda x: x.upper(), ["a", "b", "c"])
    """
    with ChunkedExecutor(max_workers=max_workers, chunksize=chunksize, executor_type=executor_type) as ex:
        return list(ex.map(fn, items))

def interpreter_pool_available() -> bool:
    """Check if InterpreterPoolExecutor is available (Python 3.14+)."""
    try:
        from concurrent.futures import InterpreterPoolExecutor
        return True
    except ImportError:
        return False

class BenchmarkResult(msgspec.Struct, gc=False):
    """Result of a parallel execution benchmark."""
    name: str
    serial_ms: float
    thread_ms: float
    chunked_ms: float
    process_ms: float
    thread_speedup: float
    chunked_speedup: float
    process_speedup: float
    optimal_chunksize: int
    recommended_executor: str

def benchmark_parallel[T, R](fn: Callable[[T], R], items: list[T], *, name: str='unnamed', max_workers: int=4) -> BenchmarkResult:
    """
    Benchmark serial vs ThreadPoolExecutor vs ChunkedExecutor vs ProcessPoolExecutor.

    Useful for determining the optimal executor configuration for a workload.

    Args:
        fn: Function to benchmark
        items: Workload items
        name: Human-readable name for the benchmark
        max_workers: Number of workers to test with

    Returns:
        BenchmarkResult with timing and speedup data

    Example:
        result = benchmark_parallel(str.upper, ["a", "b", "c"] * 1000, name="str.upper")
        print(f"Best: {result.recommended_executor} at {min(result.thread_ms, result.chunked_ms, result.process_ms):.2f}ms")
    """
    import gc
    import time
    for _ in range(2):
        [fn(item) for item in items[:min(100, len(items))]]
    gc.collect()
    t0 = time.perf_counter()
    for _ in range(3):
        serial_result = [fn(item) for item in items]
    serial_ms = (time.perf_counter() - t0) / 3 * 1000
    gc.collect()
    t0 = time.perf_counter()
    for _ in range(3):
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            thread_result = list(ex.map(fn, items))
    thread_ms = (time.perf_counter() - t0) / 3 * 1000
    gc.collect()
    chunksize = get_optimal_chunksize(len(items), max_workers, ExecutorType.THREAD)
    t0 = time.perf_counter()
    for _ in range(3):
        with ChunkedExecutor(max_workers=max_workers, chunksize=chunksize) as ex:
            chunked_result = list(ex.map(fn, items))
    chunked_ms = (time.perf_counter() - t0) / 3 * 1000
    gc.collect()
    t0 = time.perf_counter()
    for _ in range(3):
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            process_result = list(ex.map(fn, items))
    process_ms = (time.perf_counter() - t0) / 3 * 1000
    times = {'thread': thread_ms, 'chunked': chunked_ms, 'process': process_ms}
    best = min(times, key=times.__getitem__)
    return BenchmarkResult(name=name, serial_ms=serial_ms, thread_ms=thread_ms, chunked_ms=chunked_ms, process_ms=process_ms, thread_speedup=serial_ms / thread_ms if thread_ms > 0 else 0, chunked_speedup=serial_ms / chunked_ms if chunked_ms > 0 else 0, process_speedup=serial_ms / process_ms if process_ms > 0 else 0, optimal_chunksize=chunksize, recommended_executor=best)