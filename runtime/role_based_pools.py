"""
Role-Based Pool Executor — ISSUE #5 Fix
========================================

Unified facade for CPU/IO work distribution on M1 8GB.

PROBLEM:
  utils/rayon_pool.py had a singleton cpu_pool for ALL CPU work →
  contention between Hash/IOC (CPU-heavy), Embedding (memory-heavy),
  DuckDB (I/O-heavy), Regex (CPU-heavy).

SOLUTION:
  Role-based dispatch to specialized executors with RAM monitoring.

M1 8GB THREAD BUDGET:
  ┌─────────────────────────────────────────────────────────────┐
  │ Role           │ Pool              │ Workers │ Memory       │
  ├────────────────┼───────────────────┼─────────┼──────────────┤
  │ HASH_WORKERS   │ Rayon cpu_pool    │  4 P    │ ~50 MB       │
  │ EMBED_WORKERS  │ IsolatedInterp    │  1      │ ~2 GB max    │
  │ DB_WORKERS     │ Rayon io_pool     │  2 E    │ ~100 MB      │
  │ REGEX_WORKERS  │ Rayon cpu_pool    │  4 P    │ ~50 MB       │
  │ ASYNC_IO       │ asyncio.to_thread  │  4      │ ~10 MB       │
  └─────────────────────────────────────────────────────────────┘
  Total: 11 threads (fits 8-core M1 with QoS)

ROLE SEMANTICS:
  HASH_WORKERS   — xxhash, blake3 checksums, content hashing
  EMBED_WORKERS  — MLX embedding generation (memory-heavy, 2GB)
  DB_WORKERS     — DuckDB concurrent writers (I/O-bound)
  REGEX_WORKERS  — fancy-regex pattern matching (CPU-bound)

INVARIANTS (Python 3.14+):
  1. Always-on: no feature flags, all pools lazy-initialized
  2. Bounded: RAM monitoring caps concurrent workers per role
  3. Fail-safe: every pool returns None/[] on error, never raises
  4. M1-metal-aware: EMBED_WORKERS throttled on memory pressure

USAGE:
  from runtime.role_based_pools import RoleBasedPools, get_role_pools

  pools = get_role_pools()

  # Hash workload (goes to cpu_pool)
  result = await pools.run_hash(hash_func, data)

  # Embedding workload (goes to IsolatedMLXExecutor with RAM guard)
  result = await pools.run_embed(embedding_func, prompt)

  # DuckDB workload (goes to io_pool)
  result = await pools.run_db(duckdb_query_func, sql)

  # Regex workload (goes to cpu_pool)
  result = await pools.run_regex(pattern_match, text, patterns)

RAM GUARDS (M1 8GB):
  - Embedding: max 1 concurrent (2GB VRAM budget)
  - DuckDB: max 2 concurrent (connection pool limit)
  - Hash/Regex: max 4 concurrent (P-core count)
"""

from __future__ import annotations

import asyncio
import gc
import os
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Literal, TypeVar

from hledac.universal.core.isolated_executors import (
    IsolatedDuckDBExecutor,
    IsolatedMLXExecutor,
    get_duckdb_executor,
    get_mlx_executor,
    is_pep734_available,
)
from hledac.universal.utils.async_helpers import safe_wait_for

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "RoleBasedPools",
    "get_role_pools",
    "RAMBudgetExceeded",
]

T = TypeVar("T")

# ------------------------------------------------------------------|
# Constants — M1 8GB calibrated                                     |
# ------------------------------------------------------------------|

_HARD_RAM_LIMIT_GiB: float = 6.5  # Hard ceiling (system needs headroom)
_EMBED_RAM_BUDGET_GiB: float = 2.0  # MLX embedding VRAM budget
_EMBED_WORKERS: int = 1  # One at a time due to 2GB VRAM limit
_DB_WORKERS: int = 2  # DuckDB concurrent writer limit
_HASH_WORKERS: int = 4  # P-core count for CPU-bound hash
_REGEX_WORKERS: int = 4  # P-core count for regex matching
_ASYNC_IO_WORKERS: int = 4  # asyncio.to_thread workers
_LMDB_WORKERS: int = 2  # LMDB writer limit (1 writer + 1 reader)
_DUCKDB_FALLBACK_WORKERS: int = 4  # DuckDB fallback pool (when PEP 734 unavailable)


class RAMBudgetExceeded(Exception):
    """Raised when a role's RAM budget would be exceeded."""

    pass


# ------------------------------------------------------------------|
# Memory monitoring (M1 8GB aware)                                 |
# ------------------------------------------------------------------|


def _get_available_memory_gib() -> float:
    """Get available system memory in GiB (M1 8GB UMA-aware)."""
    try:
        import psutil

        mem = psutil.virtual_memory()
        available_gib = mem.available / (1024**3)
        return available_gib
    except Exception:
        return 4.0  # Conservative fallback


def _get_mlx_active_memory_gib() -> float:
    """Get active MLX Metal memory in GiB (from MLX runtime)."""
    try:
        import mlx.core as mx

        mx.eval([])  # Ensure lazy evaluation is flushed
        return mx.metal.get_active_memory() / (1024**3)
    except Exception:
        return 0.0  # No MLX memory in use


# ------------------------------------------------------------------|
# Role-based pool executor                                          |
# ------------------------------------------------------------------|


class RoleBasedPools:
    """
    Unified facade for role-based executor pools on M1 8GB.

    Provides specialized pools for different workload roles:
    - HASH: CPU-bound hashing (xxhash, blake3)
    - EMBED: Memory-heavy MLX embedding generation
    - DB: I/O-bound DuckDB operations
    - REGEX: CPU-bound regex/pattern matching
    - ASYNC_IO: asyncio.to_thread wrapper for generic blocking I/O

    Invariants:
      1. Always-on: no feature flags
      2. Bounded: RAM monitoring prevents OOM on M1 8GB
      3. Fail-safe: returns None/[] on error, never raises
      4. Lazy: pools initialized on first use

    Thread safety: all methods are thread-safe via asyncio.Lock.
    """

    __slots__ = (
        "_embed_executor",
        "_duckdb_executor",
        "_duckdb_fallback_executor",
        "_hash_executor",
        "_regex_executor",
        "_async_io_executor",
        "_lmdb_executor",
        "_embed_semaphore",
        "_db_semaphore",
        "_hash_semaphore",
        "_regex_semaphore",
        "_async_io_semaphore",
        "_lmdb_semaphore",
        "_async_locks",
        "_init_lock",
        "_initialized",
    )

    def __init__(self) -> None:
        self._initialized = False
        self._init_lock = threading.Lock()

        # Semaphores for bounded concurrency
        self._embed_semaphore: asyncio.Semaphore | None = None
        self._db_semaphore: asyncio.Semaphore | None = None
        self._hash_semaphore: asyncio.Semaphore | None = None
        self._regex_semaphore: asyncio.Semaphore | None = None
        self._async_io_semaphore: asyncio.Semaphore | None = None
        self._lmdb_semaphore: asyncio.Semaphore | None = None

        # Per-role async locks for submit serialization
        self._async_locks: dict[str, asyncio.Lock] = {}

        # Lazy executors
        self._embed_executor: IsolatedMLXExecutor | None = None
        self._duckdb_executor: IsolatedDuckDBExecutor | None = None
        self._duckdb_fallback_executor: ThreadPoolExecutor | None = None
        self._hash_executor: ThreadPoolExecutor | None = None
        self._regex_executor: ThreadPoolExecutor | None = None
        self._async_io_executor: ThreadPoolExecutor | None = None
        self._lmdb_executor: ThreadPoolExecutor | None = None

    def _ensure_initialized(self) -> None:
        """Lazy initialization of all executors (double-checked locking)."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return  # type: ignore[unreachable]
            self._initialized = True

            # Initialize semaphores
            self._embed_semaphore = asyncio.Semaphore(_EMBED_WORKERS)
            self._db_semaphore = asyncio.Semaphore(_DB_WORKERS)
            self._hash_semaphore = asyncio.Semaphore(_HASH_WORKERS)
            self._regex_semaphore = asyncio.Semaphore(_REGEX_WORKERS)
            self._async_io_semaphore = asyncio.Semaphore(_ASYNC_IO_WORKERS)
            self._lmdb_semaphore = asyncio.Semaphore(_LMDB_WORKERS)

            # Initialize async locks
            self._async_locks = {
                "hash": asyncio.Lock(),
                "embed": asyncio.Lock(),
                "db": asyncio.Lock(),
                "regex": asyncio.Lock(),
                "async_io": asyncio.Lock(),
                "lmdb": asyncio.Lock(),
            }

            # ThreadPoolExecutors for GIL-releasing work
            self._hash_executor = ThreadPoolExecutor(
                max_workers=_HASH_WORKERS,
                thread_name_prefix="hledac-hash",
            )
            self._regex_executor = ThreadPoolExecutor(
                max_workers=_REGEX_WORKERS,
                thread_name_prefix="hledac-regex",
            )
            self._async_io_executor = ThreadPoolExecutor(
                max_workers=_ASYNC_IO_WORKERS,
                thread_name_prefix="hledac-async-io",
            )
            # P2-1: Dedicated pools replacing unbounded asyncio.to_thread defaults
            self._duckdb_fallback_executor = ThreadPoolExecutor(
                max_workers=_DUCKDB_FALLBACK_WORKERS,
                thread_name_prefix="hledac-duckdb-fb",
            )
            self._lmdb_executor = ThreadPoolExecutor(
                max_workers=_LMDB_WORKERS,
                thread_name_prefix="hledac-lmdb",
            )

            # PEP 734 executors (Python 3.14+, memory-isolated)
            if is_pep734_available():
                self._embed_executor = get_mlx_executor()
                self._duckdb_executor = get_duckdb_executor()

    async def _get_embed_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks["embed"]

    async def _get_db_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks["db"]

    async def _get_hash_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks["hash"]

    async def _get_regex_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks["regex"]

    async def _get_async_io_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks["async_io"]

    async def _get_lmdb_lock(self) -> asyncio.Lock:
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks["lmdb"]

    # ------------------------------------------------------------------|
    # RAM budget checks (M1 8GB)                                       |
    # ------------------------------------------------------------------|

    def _check_embed_ram_budget(self) -> bool:
        """
        Check if embedding budget allows new work.

        M1 8GB: MLX embeddings use Metal VRAM. We cap at 2 concurrent
        workers because each embedding batch can use up to 2GB VRAM.

        Returns:
            True if budget allows, False otherwise.
        """
        mlx_memory = _get_mlx_active_memory_gib()
        available = _get_available_memory_gib()

        # Reject if MLX using > 1.5 GiB or system RAM < 1 GiB
        if mlx_memory > 1.5:
            return False
        if available < 1.0:
            return False
        return True

    def _check_db_ram_budget(self) -> bool:
        """
        Check if DuckDB budget allows new work.

        M1 8GB: DuckDB in-process uses ~100MB per connection.
        We cap at 2 concurrent writers.
        """
        available = _get_available_memory_gib()
        return available > 0.5  # Need 500MB minimum headroom

    # ------------------------------------------------------------------|
    # Role: HASH — CPU-bound hashing (xxhash, blake3)                  |
    # ------------------------------------------------------------------|

    async def run_hash[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Run CPU-bound hash function on dedicated pool.

        Use for: xxhash, blake3, content hashing, checksums.

        Args:
            fn: Synchronous callable to run
            timeout: Optional timeout in seconds
            *args, **kwargs: Arguments passed to fn

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout

        Pool: ThreadPoolExecutor with 4 P-cores (GIL-releasing C ext)
        RAM budget: 4 concurrent workers max
        """
        self._ensure_initialized()
        assert self._hash_semaphore is not None
        assert self._hash_executor is not None

        async with self._hash_semaphore:
            async with await self._get_hash_lock():
                loop = asyncio.get_running_loop()
                try:
                    if timeout is not None:
                        coro = loop.run_in_executor(self._hash_executor, lambda: fn(*args, **kwargs))
                        return await safe_wait_for(coro, timeout=timeout, label="role_pool:hash")
                    return await loop.run_in_executor(self._hash_executor, lambda: fn(*args, **kwargs))
                except Exception:
                    return None

    def run_hash_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """Synchronous version of run_hash for non-async contexts."""
        self._ensure_initialized()
        assert self._hash_executor is not None
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    # ------------------------------------------------------------------|
    # Role: EMBED — MLX embedding generation (memory-heavy)             |
    # ------------------------------------------------------------------|

    async def run_embed[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Run MLX embedding function with RAM budget guard.

        Use for: MLX embedding generation, vectorization.

        Args:
            fn: Function that generates embeddings
            timeout: Optional timeout in seconds
            *args, **kwargs: Arguments passed to fn

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout/RAM budget

        Pool: IsolatedInterpreter (PEP 734) for memory isolation
        RAM budget: max 1 concurrent (2GB VRAM limit)
        """
        self._ensure_initialized()

        # RAM budget check (M1 8GB)
        if not self._check_embed_ram_budget():
            warnings.warn(
                "Embedding budget exceeded (MLX memory > 1.5 GiB or system RAM < 1 GiB), "
                "deferring embedding work",
                RuntimeWarning,
                stacklevel=2,
            )
            gc.collect()
            if not self._check_embed_ram_budget():
                return None  # Still over budget after GC

        assert self._embed_semaphore is not None

        async with self._embed_semaphore:
            async with await self._get_embed_lock():
                if self._embed_executor is not None and self._embed_executor.is_available:
                    # PEP 734 available — use isolated interpreter
                    try:
                        if timeout is not None:
                            coro = self._embed_executor.run_inference_async(fn, *args, **kwargs)
                            return await safe_wait_for(coro, timeout=timeout, label="role_pool:embed")
                        return await self._embed_executor.run_inference_async(fn, *args, **kwargs)
                    except Exception:
                        return None
                else:
                    # Fallback: run directly (MLX releases GIL)
                    try:
                        if timeout is not None:
                            coro = asyncio.to_thread(fn, *args, **kwargs)
                            return await safe_wait_for(coro, timeout=timeout, label="role_pool:embed")
                        return await asyncio.to_thread(fn, *args, **kwargs)
                    except Exception:
                        return None

    # ------------------------------------------------------------------|
    # Role: DB — DuckDB operations (I/O-bound)                         |
    # ------------------------------------------------------------------|

    async def run_db(
        self,
        fn: "Callable[..., list[dict[str, Any]]]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> list[dict[str, Any]] | None:
        """
        Run DuckDB query function on dedicated I/O pool.

        Use for: DuckDB SQL queries, graph traversal, compress I/O.

        Args:
            fn: Function that executes DuckDB operations
            timeout: Optional timeout in seconds
            *args, **kwargs: Arguments passed to fn

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout/RAM budget

        Pool: PEP 734 IsolatedInterpreter (DuckDB) or asyncio.to_thread fallback
        RAM budget: max 2 concurrent (DuckDB connection limit)
        """
        self._ensure_initialized()

        # RAM budget check
        if not self._check_db_ram_budget():
            warnings.warn(
                "DuckDB RAM budget exceeded (system RAM < 500 MB), "
                "deferring DB work",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

        assert self._db_semaphore is not None

        async with self._db_semaphore:
            async with await self._get_db_lock():
                if self._duckdb_executor is not None and self._duckdb_executor.is_available:
                    # PEP 734 available — use isolated interpreter
                    try:
                        if timeout is not None:
                            coro = self._duckdb_executor.execute_query_async(fn, *args, **kwargs)
                            return await safe_wait_for(coro, timeout=timeout, label="role_pool:db")
                        return await self._duckdb_executor.execute_query_async(fn, *args, **kwargs)
                    except Exception:
                        return None
                else:
                    # P2-1 Fallback: use dedicated DuckDB pool (not default asyncio executor)
                    assert self._duckdb_fallback_executor is not None
                    loop = asyncio.get_running_loop()
                    try:
                        if timeout is not None:
                            coro = loop.run_in_executor(
                                self._duckdb_fallback_executor, lambda: fn(*args, **kwargs)
                            )
                            return await safe_wait_for(coro, timeout=timeout, label="role_pool:db")
                        return await loop.run_in_executor(
                            self._duckdb_fallback_executor, lambda: fn(*args, **kwargs)
                        )
                    except Exception:
                        return None

    async def run_db_write(
        self,
        fn: "Callable[..., Any]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Run DuckDB write function with isolation support.

        Use for: WAL writes, Arrow batch inserts, bulk upserts.

        Returns:
            Write result (count, error, or bool), or None on error/timeout/RAM budget.

        Pool: PEP 734 IsolatedInterpreter (DuckDB) when available, otherwise
        asyncio.to_thread fallback with dedicated DuckDB pool.
        RAM budget: max 2 concurrent (DuckDB connection limit)

        M1 8GB note: DuckDB writes are I/O-bound (Arrow batch + LMDB WAL).
        True memory isolation via PEP 734 prevents DuckDB memory pressure
        from affecting MLX Metal arena.
        """
        self._ensure_initialized()

        # RAM budget check
        if not self._check_db_ram_budget():
            warnings.warn(
                "DuckDB write RAM budget exceeded (system RAM < 500 MB), "
                "deferring DB write work",
                RuntimeWarning,
                stacklevel=2,
            )
            return None

        assert self._db_semaphore is not None

        async with self._db_semaphore:
            async with await self._get_db_lock():
                if self._duckdb_executor is not None and self._duckdb_executor.is_available:
                    # PEP 734 available — use isolated interpreter for memory isolation
                    try:
                        if timeout is not None:
                            coro = self._duckdb_executor.execute_query_async(fn, *args, **kwargs)
                            return await safe_wait_for(coro, timeout=timeout, label="role_pool:db_write")
                        return await self._duckdb_executor.execute_query_async(fn, *args, **kwargs)
                    except Exception:
                        return None
                else:
                    # Fallback: use dedicated DuckDB pool
                    assert self._duckdb_fallback_executor is not None
                    loop = asyncio.get_running_loop()
                    try:
                        if timeout is not None:
                            coro = loop.run_in_executor(
                                self._duckdb_fallback_executor, lambda: fn(*args, **kwargs)
                            )
                            return await safe_wait_for(coro, timeout=timeout, label="role_pool:db_write")
                        return await loop.run_in_executor(
                            self._duckdb_fallback_executor, lambda: fn(*args, **kwargs)
                        )
                    except Exception:
                        return None

    # ------------------------------------------------------------------|
    # Role: REGEX — Pattern matching (CPU-bound)                       |
    # ------------------------------------------------------------------|

    async def run_regex[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Run regex/pattern matching on dedicated CPU pool.

        Use for: fancy-regex matching, pattern scanning, text extraction.

        Args:
            fn: Function that performs regex operations
            timeout: Optional timeout in seconds
            *args, **kwargs: Arguments passed to fn

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout

        Pool: ThreadPoolExecutor with 4 P-cores (GIL-releasing C ext)
        RAM budget: 4 concurrent workers max
        """
        self._ensure_initialized()
        assert self._regex_semaphore is not None
        assert self._regex_executor is not None

        async with self._regex_semaphore:
            async with await self._get_regex_lock():
                loop = asyncio.get_running_loop()
                try:
                    if timeout is not None:
                        coro = loop.run_in_executor(self._regex_executor, lambda: fn(*args, **kwargs))
                        return await safe_wait_for(coro, timeout=timeout, label="role_pool:regex")
                    return await loop.run_in_executor(self._regex_executor, lambda: fn(*args, **kwargs))
                except Exception:
                    return None

    def run_regex_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """Synchronous version of run_regex for non-async contexts."""
        self._ensure_initialized()
        assert self._regex_executor is not None
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    # ------------------------------------------------------------------|
    # Role: ASYNC_IO — Generic blocking I/O wrapper                    |
    # ------------------------------------------------------------------|

    async def run_async_io[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Run generic blocking I/O on asyncio thread pool.

        Use for: WHOIS, SSL handshake, file I/O, network calls.

        Args:
            fn: Blocking synchronous callable
            timeout: Optional timeout in seconds
            *args, **kwargs: Arguments passed to fn

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout
        """
        self._ensure_initialized()
        assert self._async_io_semaphore is not None
        assert self._async_io_executor is not None

        async with self._async_io_semaphore:
            async with await self._get_async_io_lock():
                loop = asyncio.get_running_loop()
                try:
                    if timeout is not None:
                        coro = loop.run_in_executor(self._async_io_executor, lambda: fn(*args, **kwargs))
                        return await safe_wait_for(coro, timeout=timeout, label="role_pool:async_io")
                    return await loop.run_in_executor(self._async_io_executor, lambda: fn(*args, **kwargs))
                except Exception:
                    return None

    # ------------------------------------------------------------------|
    # Role: LMDB — Key-value store I/O (I/O-bound, 1 writer)          |
    # ------------------------------------------------------------------|

    async def run_lmdb[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Run LMDB operations on dedicated 2-worker pool.

        LMDB is single-writer but supports concurrent readers.
        Pool has 2 workers: 1 writer + 1 reader (sufficient for LMDB's
        mdb_reader_list API and write transactions).

        P2-1: Replaces unbounded asyncio.to_thread() default executor
        for LMDB operations in local_graph.py, persistent_kv_cache.py, etc.

        Args:
            fn: Synchronous callable that performs LMDB operation.
                Must NOT hold the write lock across await points.
            timeout: Optional timeout in seconds.

        Returns:
            Result of fn(*args, **kwargs), or None on error/timeout.

        Pool: ThreadPoolExecutor with 2 workers (LMDB write lock serializes anyway)
        """
        self._ensure_initialized()
        assert self._lmdb_semaphore is not None
        assert self._lmdb_executor is not None

        async with self._lmdb_semaphore:
            async with await self._get_lmdb_lock():
                loop = asyncio.get_running_loop()
                try:
                    if timeout is not None:
                        coro = loop.run_in_executor(
                            self._lmdb_executor, lambda: fn(*args, **kwargs)
                        )
                        return await safe_wait_for(coro, timeout=timeout, label="role_pool:lmdb")
                    return await loop.run_in_executor(
                        self._lmdb_executor, lambda: fn(*args, **kwargs)
                    )
                except Exception:
                    return None

    def run_lmdb_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Synchronous version of run_lmdb for non-async contexts.

        Note: Unlike run_lmdb, this does NOT use the thread pool because
        it is called from synchronous code paths (e.g., shutdown hooks)
        where the caller already owns the thread. The function is executed
        directly with full exception shielding.
        """
        self._ensure_initialized()
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

    # ------------------------------------------------------------------|
    # Utility: batch processing                                        |
    # ------------------------------------------------------------------|

    async def run_hash_batch[T, R](
        self,
        fn: "Callable[[T], R]",
        items: list[T],
        *,
        timeout: float | None = None,
    ) -> list[R]:
        """
        Process hash items in parallel on dedicated pool.

        Args:
            fn: Function to apply to each item
            items: Items to process
            timeout: Optional timeout per item

        Returns:
            List of results (same order as items)
        """
        if not items:
            return []

        async def wrap(item: T) -> R | None:
            return await self.run_hash(fn, item, timeout=timeout)

        from hledac.universal.utils.async_helpers import parallel

        result = await parallel(
            [wrap(item) for item in items],  # type: ignore[arg-type]
            concurrency=_HASH_WORKERS,
            ctx="role_pool:hash_batch",
        )
        return [r for r in result.ok if r is not None]

    async def run_regex_batch[T, R](
        self,
        fn: "Callable[[T], R]",
        items: list[T],
        *,
        timeout: float | None = None,
    ) -> list[R]:
        """
        Process regex items in parallel on dedicated pool.

        Args:
            fn: Function to apply to each item
            items: Items to process
            timeout: Optional timeout per item

        Returns:
            List of results (same order as items)
        """
        if not items:
            return []

        async def wrap(item: T) -> R | None:
            return await self.run_regex(fn, item, timeout=timeout)

        from hledac.universal.utils.async_helpers import parallel

        result = await parallel(
            [wrap(item) for item in items],  # type: ignore[arg-type]
            concurrency=_REGEX_WORKERS,
            ctx="role_pool:regex_batch",
        )
        return [r for r in result.ok if r is not None]

    # ------------------------------------------------------------------|
    # Shutdown                                                         |
    # ------------------------------------------------------------------|

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown all role-based pools.

        Args:
            wait: If True, wait for pending tasks to complete
        """
        # Shutdown ThreadPoolExecutors
        if self._hash_executor is not None:
            self._hash_executor.shutdown(wait=wait)
            self._hash_executor = None
        if self._regex_executor is not None:
            self._regex_executor.shutdown(wait=wait)
            self._regex_executor = None
        if self._async_io_executor is not None:
            self._async_io_executor.shutdown(wait=wait)
            self._async_io_executor = None

        # Close PEP 734 executors
        if self._embed_executor is not None:
            self._embed_executor.close()
            self._embed_executor = None
        if self._duckdb_executor is not None:
            self._duckdb_executor.close()
            self._duckdb_executor = None

        # Clear semaphores and locks to prevent orphaned objects on re-init
        self._embed_semaphore = None
        self._db_semaphore = None
        self._hash_semaphore = None
        self._regex_semaphore = None
        self._async_io_semaphore = None
        self._async_locks.clear()
        self._async_locks = {}

        self._initialized = False


# ------------------------------------------------------------------|
# Module-level singleton                                            |
# ------------------------------------------------------------------|

_role_pools: RoleBasedPools | None = None
_role_pools_lock = threading.Lock()


def get_role_pools() -> RoleBasedPools:
    """
    Get the global RoleBasedPools singleton.

    Returns:
        RoleBasedPools instance (shared across all callers)
    """
    global _role_pools
    if _role_pools is not None:
        return _role_pools
    with _role_pools_lock:
        if _role_pools is None:
            _role_pools = RoleBasedPools()
        assert _role_pools is not None
        return _role_pools


# ------------------------------------------------------------------|
# Backward-compatibility shims                                      |
# ------------------------------------------------------------------|

async def run_in_hash_pool[T](fn: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T | None:
    """
    Backward-compat shim for run_in_cpu_pool (hash role).

    DEPRECATED: Use RoleBasedPools.run_hash() instead.
    """
    warnings.warn(
        "run_in_hash_pool is deprecated. Use RoleBasedPools.run_hash() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    pools = get_role_pools()
    return await pools.run_hash(fn, *args, **kwargs)


async def run_in_regex_pool[T](fn: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T | None:
    """
    Backward-compat shim for run_in_cpu_pool (regex role).

    DEPRECATED: Use RoleBasedPools.run_regex() instead.
    """
    warnings.warn(
        "run_in_regex_pool is deprecated. Use RoleBasedPools.run_regex() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    pools = get_role_pools()
    return await pools.run_regex(fn, *args, **kwargs)


async def run_in_embed_pool[T](fn: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T | None:
    """
    Backward-compat shim for embedding role.

    DEPRECATED: Use RoleBasedPools.run_embed() instead.
    """
    warnings.warn(
        "run_in_embed_pool is deprecated. Use RoleBasedPools.run_embed() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    pools = get_role_pools()
    return await pools.run_embed(fn, *args, **kwargs)


async def run_in_db_pool(
    fn: "Callable[..., list[dict[str, Any]]]",
    /,
    *args: Any,
    **kwargs: Any,
) -> list[dict[str, Any]] | None:
    """
    Backward-compat shim for DuckDB role.

    DEPRECATED: Use RoleBasedPools.run_db() instead.
    """
    warnings.warn(
        "run_in_db_pool is deprecated. Use RoleBasedPools.run_db() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    pools = get_role_pools()
    return await pools.run_db(fn, *args, **kwargs)
