"""
Role-Based Pool Executor — DEPRECATED (R-18)
============================================

.. deprecated::
    This module is deprecated as of R-18. Use the dedicated modules instead:
    - ``runtime.lmdb_pool`` for LMDB operations (``run_lmdb``)
    - ``runtime.worker_pool`` for generic CPU/IO pool work
    - ``core.isolated_executors`` for MLX/DuckDB isolated executors

This module kept for backward compatibility only. All production call sites
have been migrated to ``runtime.lmdb_pool``.

Original facade for CPU/IO work distribution on M1 8GB.

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
  from hledac.universal.runtime.role_based_pools import RoleBasedPools, get_role_pools

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
from hledac.universal.runtime.lmdb_pool import get_lmdb_pool
from hledac.universal.utils.async_helpers import safe_wait_for

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

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
_EMBED_FALLBACK_WORKERS: int = 2  # Embedding fallback pool (when PEP 734 unavailable)
_DUCKDB_FALLBACK_WORKERS: int = 4  # DuckDB fallback pool (when PEP 734 unavailable)

# LMDB pool configuration — shared with runtime.lmdb_pool
from hledac.universal.runtime._shared.lmdb_pool_helpers import _LMDB_WORKERS


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
        "_embed_fallback_executor",
        "_hash_executor",
        "_regex_executor",
        "_async_io_executor",
        "_embed_semaphore",
        "_db_semaphore",
        "_hash_semaphore",
        "_regex_semaphore",
        "_async_io_semaphore",
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

        # Per-role async locks for submit serialization
        self._async_locks: dict[str, asyncio.Lock] = {}

        # Lazy executors
        self._embed_executor: IsolatedMLXExecutor | None = None
        self._duckdb_executor: IsolatedDuckDBExecutor | None = None
        self._duckdb_fallback_executor: ThreadPoolExecutor | None = None
        self._embed_fallback_executor: ThreadPoolExecutor | None = None
        self._hash_executor: ThreadPoolExecutor | None = None
        self._regex_executor: ThreadPoolExecutor | None = None
        self._async_io_executor: ThreadPoolExecutor | None = None

    def _ensure_initialized(self) -> None:
        """Lazy initialization of all executors (double-checked locking)."""
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            self._initialized = True

            # Initialize semaphores
            self._embed_semaphore = asyncio.Semaphore(_EMBED_WORKERS)
            self._db_semaphore = asyncio.Semaphore(_DB_WORKERS)
            self._hash_semaphore = asyncio.Semaphore(_HASH_WORKERS)
            self._regex_semaphore = asyncio.Semaphore(_REGEX_WORKERS)
            self._async_io_semaphore = asyncio.Semaphore(_ASYNC_IO_WORKERS)

            # Initialize async locks
            self._async_locks = {
                "hash": asyncio.Lock(),
                "embed": asyncio.Lock(),
                "db": asyncio.Lock(),
                "regex": asyncio.Lock(),
                "async_io": asyncio.Lock(),
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
            self._embed_fallback_executor = ThreadPoolExecutor(
                max_workers=_EMBED_FALLBACK_WORKERS,
                thread_name_prefix="hledac-embed-fb",
            )

            # PEP 734 executors (Python 3.14+, memory-isolated)
            if is_pep734_available():
                self._embed_executor = get_mlx_executor()
                self._duckdb_executor = get_duckdb_executor()

    # ------------------------------------------------------------------|
    # Lock accessor (parameterized, replaces 5 duplicated methods)       |
    # ------------------------------------------------------------------|

    async def _get_lock(self, role: str) -> asyncio.Lock:
        """Get async lock for a given role (embed, db, hash, regex, async_io)."""
        self._ensure_initialized()
        assert self._async_locks is not None
        return self._async_locks[role]

    async def _get_embed_lock(self) -> asyncio.Lock:
        return await self._get_lock("embed")

    async def _get_db_lock(self) -> asyncio.Lock:
        return await self._get_lock("db")

    async def _get_hash_lock(self) -> asyncio.Lock:
        return await self._get_lock("hash")

    async def _get_regex_lock(self) -> asyncio.Lock:
        return await self._get_lock("regex")

    async def _get_async_io_lock(self) -> asyncio.Lock:
        return await self._get_lock("async_io")

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

        M1 8GB: DuckDB in-process uses ~100mb per connection.
        We cap at 2 concurrent writers.
        """
        available = _get_available_memory_gib()
        return available > 0.5  # Need 500MB minimum headroom

    # ------------------------------------------------------------------|
    # Shared helpers (parameterized, eliminates Type-2 clone patterns) |
    # ------------------------------------------------------------------|

    async def _run_role_a[T](
        self,
        semaphore: asyncio.Semaphore,
        lock_getter: Callable[[], Coroutine[Any, Any, asyncio.Lock]],
        executor: ThreadPoolExecutor,
        label: str,
        fn: "Callable[..., T]",
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Parameterized async runner for CPU/IO-bound roles (hash, regex, async_io).

        Replaces: run_hash, run_regex, run_async_io (Type-2 clone, 3→1).
        """
        self._ensure_initialized()
        assert semaphore is not None
        assert executor is not None

        async with semaphore:
            async with await lock_getter():
                loop = asyncio.get_running_loop()
                try:
                    if timeout is not None:
                        coro = loop.run_in_executor(executor, lambda: fn(*args, **kwargs))
                        return await safe_wait_for(coro, timeout=timeout, label=f"role_pool:{label}")
                    return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))
                except Exception:
                    return None

    def _run_role_a_sync[T](
        self,
        executor: ThreadPoolExecutor,
        fn: "Callable[..., T]",
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Synchronous runner for CPU-bound roles (hash, regex).

        Replaces: run_hash_sync, run_regex_sync (Type-2 clone, 2→1).
        """
        self._ensure_initialized()
        assert executor is not None
        try:
            return fn(*args, **kwargs)
        except Exception:
            return None

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
        return await self._run_role_a(
            self._hash_semaphore,  # type: ignore[arg-type]
            self._get_hash_lock,
            self._hash_executor,  # type: ignore[arg-type]
            "hash",
            fn, *args, timeout=timeout, **kwargs
        )

    def run_hash_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """Synchronous version of run_hash for non-async contexts."""
        self._ensure_initialized()
        return self._run_role_a_sync(self._hash_executor, fn, *args, **kwargs)

    # ------------------------------------------------------------------|
    # Role: EMBED — MLX embedding generation (memory-heavy)             |
    # ------------------------------------------------------------------|

    async def _run_embed_impl(
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> T | None:
        """
        Shared implementation for run_embed.

        Uses PEP 734 executor if available, otherwise dedicated fallback pool.
        """
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
            # Fallback: use dedicated embed pool (MLX releases GIL)
            assert self._embed_fallback_executor is not None
            loop = asyncio.get_running_loop()
            try:
                if timeout is not None:
                    coro = loop.run_in_executor(
                        self._embed_fallback_executor, lambda: fn(*args, **kwargs)
                    )
                    return await safe_wait_for(coro, timeout=timeout, label="role_pool:embed")
                return await loop.run_in_executor(
                    self._embed_fallback_executor, lambda: fn(*args, **kwargs)
                )
            except Exception:
                return None

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
                return await self._run_embed_impl(fn, *args, timeout=timeout, **kwargs)

    # ------------------------------------------------------------------|
    # Role: DB — DuckDB operations (I/O-bound)                         |
    # ------------------------------------------------------------------|

    async def _run_db_impl(
        self,
        fn: "Callable[..., Any]",
        /,
        *args: Any,
        timeout: float | None = None,
        label: str = "role_pool:db",
        **kwargs: Any,
    ) -> Any:
        """
        Shared implementation for run_db and run_db_write.

        Args:
            fn: Function to execute
            *args: Arguments passed to fn
            timeout: Optional timeout in seconds
            label: safe_wait_for label
            **kwargs: Keyword arguments passed to fn
        """
        if self._duckdb_executor is not None and self._duckdb_executor.is_available:
            # PEP 734 available — use isolated interpreter for memory isolation
            try:
                if timeout is not None:
                    coro = self._duckdb_executor.execute_query_async(fn, *args, **kwargs)
                    return await safe_wait_for(coro, timeout=timeout, label=label)
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
                    return await safe_wait_for(coro, timeout=timeout, label=label)
                return await loop.run_in_executor(
                    self._duckdb_fallback_executor, lambda: fn(*args, **kwargs)
                )
            except Exception:
                return None

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
                return await self._run_db_impl(
                    fn, *args, timeout=timeout, label="role_pool:db", **kwargs
                )

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
                return await self._run_db_impl(
                    fn, *args, timeout=timeout, label="role_pool:db_write", **kwargs
                )

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
        return await self._run_role_a(
            self._regex_semaphore,  # type: ignore[arg-type]
            self._get_regex_lock,
            self._regex_executor,  # type: ignore[arg-type]
            "regex",
            fn, *args, timeout=timeout, **kwargs
        )

    def run_regex_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """Synchronous version of run_regex for non-async contexts."""
        self._ensure_initialized()
        return self._run_role_a_sync(self._regex_executor, fn, *args, **kwargs)  # type: ignore[arg-type]

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
        return await self._run_role_a(
            self._async_io_semaphore,  # type: ignore[arg-type]
            self._get_async_io_lock,
            self._async_io_executor,  # type: ignore[arg-type]
            "async_io",
            fn, *args, timeout=timeout, **kwargs
        )

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

        .. deprecated::
            This method is deprecated as of R-18.
            Use ``runtime.lmdb_pool.get_lmdb_pool().run_lmdb()`` instead.

        Delegates to the canonical ``LmdbPool`` singleton.
        """
        return await get_lmdb_pool().run_lmdb(fn, *args, timeout=timeout, **kwargs)

    def run_lmdb_sync[T](
        self,
        fn: "Callable[..., T]",
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T | None:
        """
        Synchronous version of run_lmdb for non-async contexts.

        .. deprecated::
            This method is deprecated as of R-18.
            Use ``runtime.lmdb_pool.get_lmdb_pool().run_lmdb_sync()`` instead.

        Delegates to the canonical ``LmdbPool`` singleton.
        """
        return get_lmdb_pool().run_lmdb_sync(fn, *args, **kwargs)

    # ------------------------------------------------------------------|
    # Batch processing (parameterized, replaces 2 duplicated methods)      |
    # ------------------------------------------------------------------|

    async def _run_batch[T, R](
        self,
        fn: "Callable[[T], R]",
        items: list[T],
        *,
        role: str,
        concurrency: int,
        timeout: float | None = None,
    ) -> list[R]:
        """
        Process items in parallel on a dedicated pool.

        Args:
            fn: Function to apply to each item
            items: Items to process
            role: Role name (hash, regex) for method dispatch
            concurrency: Max concurrent workers
            timeout: Optional timeout per item

        Returns:
            List of results (same order as items)
        """
        if not items:
            return []

        async def wrap(item: T) -> R | None:
            if role == "hash":
                return await self.run_hash(fn, item, timeout=timeout)
            elif role == "regex":
                return await self.run_regex(fn, item, timeout=timeout)
            return None

        from hledac.universal.utils.async_helpers import parallel

        result = await parallel(
            [wrap(item) for item in items],  # type: ignore[arg-type]
            concurrency=concurrency,
            ctx=f"role_pool:{role}_batch",
        )
        return [r for r in result.ok if r is not None]

    async def run_hash_batch[T, R](
        self,
        fn: "Callable[[T], R]",
        items: list[T],
        *,
        timeout: float | None = None,
    ) -> list[R]:
        """Process hash items in parallel on dedicated pool."""
        return await self._run_batch(fn, items, role="hash", concurrency=_HASH_WORKERS, timeout=timeout)

    async def run_regex_batch[T, R](
        self,
        fn: "Callable[[T], R]",
        items: list[T],
        *,
        timeout: float | None = None,
    ) -> list[R]:
        """Process regex items in parallel on dedicated pool."""
        return await self._run_batch(fn, items, role="regex", concurrency=_REGEX_WORKERS, timeout=timeout)

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
        if self._duckdb_fallback_executor is not None:
            self._duckdb_fallback_executor.shutdown(wait=wait)
            self._duckdb_fallback_executor = None
        if self._embed_fallback_executor is not None:
            self._embed_fallback_executor.shutdown(wait=wait)
            self._embed_fallback_executor = None

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

    .. deprecated::
        This function is deprecated as of R-18.
        Use ``runtime.lmdb_pool.get_lmdb_pool()`` for LMDB operations.
        Use ``runtime.worker_pool.get_shared_pool()`` for generic CPU/IO work.

    Returns:
        RoleBasedPools instance (shared across all callers)
    """
    warnings.warn(
        "runtime.role_based_pools is deprecated (R-18). "
        "Use runtime.lmdb_pool.get_lmdb_pool() for LMDB operations "
        "or runtime.worker_pool.get_shared_pool() for generic work.",
        DeprecationWarning,
        stacklevel=2,
    )
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


async def _deprecated_pool_wrapper[T](
    shim_name: str,
    canonical_name: str,
    pools_method: Callable[..., Coroutine[Any, Any, T]],  # type: ignore[misc]
    fn: "Callable[..., T]",
    /,
    *args: Any,
    **kwargs: Any,
) -> T | None:
    """
    Shared deprecation wrapper for run_in_*_pool legacy shims.

    Type-1 exact clone elimination: 4 identical fragments (hash/regex/embed/db)
    replaced with single parameterized helper.
    """
    warnings.warn(
        f"{shim_name} is deprecated. Use RoleBasedPools.{canonical_name}() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    pools = get_role_pools()
    return await pools_method(pools, fn, *args, **kwargs)


async def run_in_hash_pool[T](fn: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T | None:
    """
    Backward-compat shim for run_in_cpu_pool (hash role).

    DEPRECATED: Use RoleBasedPools.run_hash() instead.
    """
    return await _deprecated_pool_wrapper(
        "run_in_hash_pool", "run_hash", RoleBasedPools.run_hash, fn, *args, **kwargs
    )


async def run_in_regex_pool[T](fn: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T | None:
    """
    Backward-compat shim for run_in_cpu_pool (regex role).

    DEPRECATED: Use RoleBasedPools.run_regex() instead.
    """
    return await _deprecated_pool_wrapper(
        "run_in_regex_pool", "run_regex", RoleBasedPools.run_regex, fn, *args, **kwargs
    )


async def run_in_embed_pool[T](fn: "Callable[..., T]", /, *args: Any, **kwargs: Any) -> T | None:
    """
    Backward-compat shim for embedding role.

    DEPRECATED: Use RoleBasedPools.run_embed() instead.
    """
    return await _deprecated_pool_wrapper(
        "run_in_embed_pool", "run_embed", RoleBasedPools.run_embed, fn, *args, **kwargs
    )


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
    return await _deprecated_pool_wrapper(
        "run_in_db_pool", "run_db", RoleBasedPools.run_db, fn, *args, **kwargs
    )
