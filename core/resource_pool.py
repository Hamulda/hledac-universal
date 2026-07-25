"""
core/resource_pool.py — Centralized Resource Pool for Hledac Universal

R-1 Solution: Unified resource pool addressing:
- 8 DuckDB instances without coordination
- 110+ run_in_executor submissions
- ANE/MLX/CoreML resources without coordination

Provides explicit named pools with context manager interface:
- duckdb_pool (4 instances, round-robin)
- mlx_pool (1 stream)
- ane_pool (1 stream)
- coreml_pool (1 stream)
- cpu_io_pool (8 workers) — asyncio.to_thread bounded
- cpu_blocking_pool (4 workers) — ThreadPoolExecutor for sync I/O

M1 8GB UMA constraints:
- DuckDB: 4 connections × ~50MB = ~200MB (vs 8× idle)
- Thread pools: bounded by ConcurrencyPreset from resource_governor
- MLX/ANE/CoreML: lazy init, single stream each

Sprint R-1 (2026-07-18)
"""
from __future__ import annotations
import asyncio
import atexit
import contextlib
import functools
import threading
import time
import weakref
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Generator
from core.env_config import ENV
if TYPE_CHECKING:
    pass

class PoolKind(Enum):
    """Canonical pool identifiers."""
    DUCKDB_RO = auto()
    DUCKDB_RW = auto()
    MLX = auto()
    ANE = auto()
    COREML = auto()
    CPU_IO = auto()
    CPU_BLOCKING = auto()
_DUCKDB_POOL_SIZE = 4
_DUCKDB_POOL_MAX = 8
_MLX_POOL_SIZE = 1
_ANE_POOL_SIZE = 1
_COREML_POOL_SIZE = 1
_CPU_IO_WORKERS_DEFAULT = 8
_CPU_BLOCKING_WORKERS_DEFAULT = 4
_thread_local = threading.local()
_current_duckdb_conn: ContextVar[Any | None] = ContextVar('current_duckdb_conn', default=None)

def _health_check_duckdb(conn: Any) -> bool:
    """Verify DuckDB connection is still alive."""
    try:
        conn.execute('SELECT 1')
        return True
    except Exception:
        return False

def _get_max_workers_from_governor() -> int:
    """
    Get current max_workers from M1ResourceGovernor state.

    Falls back to default if governor unavailable.
    """
    try:
        from core.resource_governor import evaluate_uma_state, ConcurrencyPreset
        import psutil
        mem = psutil.virtual_memory()
        system_used_gib = mem.used / 1024 ** 3
        state = evaluate_uma_state(system_used_gib)
        preset = ConcurrencyPreset.from_state(state)
        return preset.max_workers
    except Exception:
        return _CPU_IO_WORKERS_DEFAULT

@dataclass(frozen=True, slots=True)
class _DuckDBPoolStats:
    """DuckDB pool statistics."""
    acquire_count: int = 0
    release_count: int = 0
    health_check_failures: int = 0
    new_connections: int = 0
    pool_hits: int = 0
    pool_misses: int = 0

class _DuckDBPool:
    """
    Bounded DuckDB connection pool with round-robin and health checks.

    Design decisions:
    - Round-robin across connections to distribute query load
    - Health check on acquire to detect stale connections
    - Per-db_path pools to avoid cross-database pollution
    - Thread-safe with minimal lock contention
    """
    __slots__ = tuple(('_health_check', '_lock', '_max_absolute', '_max_size', '_pools', '_round_robin', '_stats', '_total_connections'))

    def __init__(self, max_size: int=_DUCKDB_POOL_SIZE, max_absolute: int=_DUCKDB_POOL_MAX, health_check: bool=True) -> None:
        self._max_size = max_size
        self._max_absolute = max_absolute
        self._health_check = health_check
        self._lock = threading.Lock()
        self._pools: dict[str, deque[Any]] = {}
        self._round_robin: dict[str, int] = {}
        self._total_connections: int = 0
        self._stats = _DuckDBPoolStats()

    def _get_pool(self, db_path: str) -> deque[Any]:
        """Get or create pool for db_path."""
        with self._lock:
            if db_path not in self._pools:
                self._pools[db_path] = deque(maxlen=self._max_size)
                self._round_robin[db_path] = 0
            return self._pools[db_path]

    def _create_connection(self, db_path: str, read_only: bool=True) -> Any:
        """Create new DuckDB connection with lazy import."""
        try:
            import duckdb
            conn = duckdb.connect(db_path, read_only=read_only)
            return conn
        except ImportError:
            return None

    def acquire(self, db_path: str, read_only: bool=True) -> tuple[Any, str] | tuple[None, None]:
        """
        Acquire connection from pool.

        Returns:
            (connection, db_path) on success
            (None, None) on failure
        """
        self._stats.acquire_count += 1
        pool = self._get_pool(db_path)
        with self._lock:
            pool_len = len(pool)
            if pool_len > 0:
                idx = self._round_robin.get(db_path, 0) % pool_len
                try:
                    conn = pool[idx]
                    if self._health_check and (not _health_check_duckdb(conn)):
                        del pool[idx]
                        self._total_connections -= 1
                        try:
                            conn.close()
                        except Exception:
                            pass
                        self._stats.health_check_failures += 1
                    else:
                        self._round_robin[db_path] = (idx + 1) % pool_len
                        self._stats.pool_hits += 1
                        del pool[idx]
                        return (conn, db_path)
                except (IndexError, TypeError):
                    pass
        self._stats.pool_misses += 1
        with self._lock:
            if self._total_connections < self._max_absolute:
                conn = self._create_connection(db_path, read_only)
                if conn is not None:
                    self._total_connections += 1
                    self._stats.new_connections += 1
                    return (conn, db_path)
        with self._lock:
            if len(pool) > 0:
                conn = pool.popleft()
                self._total_connections -= 1
                if self._health_check and (not _health_check_duckdb(conn)):
                    try:
                        conn.close()
                    except Exception:
                        pass
                    self._stats.health_check_failures += 1
                    self._stats.pool_misses += 1
                    return (None, None)
                return (conn, db_path)
        return (None, None)

    def release(self, conn: Any, db_path: str | None) -> None:
        """
        Release connection back to pool.
        """
        if conn is None or db_path is None:
            return
        self._stats.release_count += 1
        pool = self._get_pool(db_path)
        with self._lock:
            if self._health_check and (not _health_check_duckdb(conn)):
                try:
                    conn.close()
                except Exception:
                    pass
                self._stats.health_check_failures += 1
                self._total_connections -= 1
                return
            max_pool_size = pool.maxlen if pool.maxlen is not None else self._max_size
            if len(pool) < max_pool_size:
                pool.append(conn)
            else:
                try:
                    conn.close()
                except Exception:
                    pass
                self._total_connections -= 1

    @property
    def stats(self) -> _DuckDBPoolStats:
        """Return pool statistics."""
        return self._stats

    def close_all(self) -> None:
        """Close all pooled connections."""
        with self._lock:
            for pool in self._pools.values():
                for conn in pool:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._pools.clear()
            self._round_robin.clear()
            self._total_connections = 0
_duckdb_ro_pool = _DuckDBPool(max_size=_DUCKDB_POOL_SIZE)
_duckdb_rw_pool = _DuckDBPool(max_size=2, max_absolute=4)

class _CPUPool:
    """
    Bounded CPU thread pool with adaptive sizing based on M1ResourceGovernor.
    """
    __slots__ = tuple(('_adaptive_max', '_executor', '_kind', '_lock', '_max_workers', '_name', '_semaphore'))

    def __init__(self, name: str, max_workers: int, kind: PoolKind) -> None:
        self._name = name
        self._max_workers = max_workers
        self._kind = kind
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._semaphore: asyncio.Semaphore | None = None
        self._adaptive_max: int = max_workers

    def _get_adaptive_max(self) -> int:
        """Get current max workers from resource governor if available."""
        return _get_max_workers_from_governor()

    def get_executor(self) -> ThreadPoolExecutor:
        """Get or create ThreadPoolExecutor."""
        with self._lock:
            if self._executor is None:
                max_w = self._get_adaptive_max()
                self._adaptive_max = max_w
                self._executor = ThreadPoolExecutor(max_workers=max_w, thread_name_prefix=f'hledac_{self._name}')
            return self._executor

    def get_semaphore(self) -> asyncio.Semaphore:
        """Get asyncio semaphore for bounded concurrency."""
        if self._semaphore is None:
            max_w = self._get_adaptive_max()
            self._semaphore = asyncio.Semaphore(max_w)
        return self._semaphore

    def resize(self, max_workers: int) -> None:
        """Resize pool (graceful, existing work completes)."""
        with self._lock:
            if self._executor is not None and max_workers != self._adaptive_max:
                old = self._executor
                self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f'hledac_{self._name}')
                self._adaptive_max = max_workers
                old.shutdown(wait=False)
                self._semaphore = asyncio.Semaphore(max_workers)

    def shutdown(self, wait: bool=True) -> None:
        """Shutdown the pool."""
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=wait)
                self._executor = None
            self._semaphore = None
_cpu_io_pool = _CPUPool(name='cpu_io', max_workers=_CPU_IO_WORKERS_DEFAULT, kind=PoolKind.CPU_IO)
_cpu_blocking_pool = _CPUPool(name='cpu_blocking', max_workers=_CPU_BLOCKING_WORKERS_DEFAULT, kind=PoolKind.CPU_BLOCKING)

class _MLXPool:
    """
    MLX compute stream pool (single stream, lazy initialization).
    """
    __slots__ = tuple(('_loaded', '_lock', '_stream'))

    def __init__(self) -> None:
        self._stream: Any | None = None
        self._lock = threading.Lock()
        self._loaded = False

    def acquire(self) -> Any | None:
        """Acquire MLX stream (lazy init)."""
        with self._lock:
            if not self._loaded:
                try:
                    import mlx.core as mx
                    self._stream = mx
                    self._loaded = True
                except ImportError:
                    return None
            return self._stream

    def release(self, _stream: Any) -> None:
        """MLX has single stream, release is no-op."""
        pass

    @property
    def is_available(self) -> bool:
        """Check if MLX is available."""
        try:
            import mlx.core
            return True
        except ImportError:
            return False

class _ANEPool:
    """
    Apple Neural Engine pool (single stream, lazy initialization).
    """
    __slots__ = tuple(('_loaded', '_lock', '_model'))

    def __init__(self) -> None:
        self._model: Any | None = None
        self._lock = threading.Lock()
        self._loaded = False

    def acquire(self) -> Any | None:
        """Acquire ANE context (lazy init)."""
        with self._lock:
            if not self._loaded:
                try:
                    import coremltools as ct
                    self._model = ct
                    self._loaded = True
                except ImportError:
                    return None
            return self._model

    def release(self, _model: Any) -> None:
        """ANE has single model, release is no-op."""
        pass

    @property
    def is_available(self) -> bool:
        """Check if CoreML/ANE is available."""
        try:
            import coremltools
            return True
        except ImportError:
            return False

class _CoreMLPool:
    """
    CoreML compute pool (single stream, lazy initialization).
    """
    __slots__ = tuple(('_loaded', '_lock', '_runtime'))

    def __init__(self) -> None:
        self._runtime: Any | None = None
        self._lock = threading.Lock()
        self._loaded = False

    def acquire(self) -> Any | None:
        """Acquire CoreML runtime (lazy init)."""
        with self._lock:
            if not self._loaded:
                try:
                    import coremltools as ct
                    self._runtime = ct
                    self._loaded = True
                except ImportError:
                    return None
            return self._runtime

    def release(self, _runtime: Any) -> None:
        """CoreML has single runtime, release is no-op."""
        pass

    @property
    def is_available(self) -> bool:
        """Check if CoreML is available."""
        try:
            import coremltools
            return True
        except ImportError:
            return False
_mlx_pool = _MLXPool()
_ane_pool = _ANEPool()
_coreml_pool = _CoreMLPool()

@contextlib.contextmanager
def with_resource(kind: PoolKind, db_path: str | None=None, read_only: bool=True) -> Generator[Any, None, None]:
    """
    Context manager for acquiring/releasing pooled resources.

    Usage:
        # DuckDB read-only connection
        with with_resource(PoolKind.DUCKDB_RO, "/path/to/db.db") as conn:
            result = conn.execute("SELECT * FROM table").fetchall()

        # DuckDB read-write connection
        with with_resource(PoolKind.DUCKDB_RW, "/path/to/db.db", read_only=False) as conn:
            conn.execute("INSERT INTO table VALUES (1, 2)")

        # MLX stream
        with with_resource(PoolKind.MLX) as mx:
            arr = mx.array([1, 2, 3])

    Args:
        kind: Pool identifier
        db_path: For DuckDB pools, the database path
        read_only: For DuckDB pools, whether connection is read-only

    Yields:
        Pooled resource

    Raises:
        RuntimeError: If pool is unavailable or exhausted
    """
    resource: Any = None
    acquired_db_path: str | None = None
    try:
        match kind:
            case PoolKind.DUCKDB_RO:
                if db_path is None:
                    raise ValueError('db_path required for DUCKDB_RO pool')
                resource, acquired_db_path = _duckdb_ro_pool.acquire(db_path, read_only=True)
                if resource is None:
                    raise RuntimeError(f'DuckDB pool exhausted for {db_path}')
            case PoolKind.DUCKDB_RW:
                if db_path is None:
                    raise ValueError('db_path required for DUCKDB_RW pool')
                resource, acquired_db_path = _duckdb_rw_pool.acquire(db_path, read_only=False)
                if resource is None:
                    raise RuntimeError(f'DuckDB pool exhausted for {db_path}')
            case PoolKind.MLX:
                resource = _mlx_pool.acquire()
                if resource is None and (not _mlx_pool.is_available):
                    raise RuntimeError('MLX not available')
            case PoolKind.ANE:
                resource = _ane_pool.acquire()
                if resource is None and (not _ane_pool.is_available):
                    raise RuntimeError('ANE not available')
            case PoolKind.COREML:
                resource = _coreml_pool.acquire()
                if resource is None and (not _coreml_pool.is_available):
                    raise RuntimeError('CoreML not available')
            case PoolKind.CPU_IO | PoolKind.CPU_BLOCKING:
                pool = _cpu_io_pool if kind == PoolKind.CPU_IO else _cpu_blocking_pool
                resource = pool.get_executor()
            case _:
                raise ValueError(f'Unknown pool kind: {kind}')
        yield resource
    finally:
        match kind:
            case PoolKind.DUCKDB_RO:
                if resource is not None:
                    _duckdb_ro_pool.release(resource, acquired_db_path)
            case PoolKind.DUCKDB_RW:
                if resource is not None:
                    _duckdb_rw_pool.release(resource, acquired_db_path)
            case PoolKind.MLX:
                if resource is not None:
                    _mlx_pool.release(resource)
            case PoolKind.ANE:
                if resource is not None:
                    _ane_pool.release(resource)
            case PoolKind.COREML:
                if resource is not None:
                    _coreml_pool.release(resource)

class _AsyncPoolContextManager:
    """Async context manager for CPU pools with semaphore."""
    __slots__ = ('_pool', '_semaphore', '_executor')

    def __init__(self, pool: _CPUPool) -> None:
        self._pool = pool
        self._semaphore: asyncio.Semaphore | None = None
        self._executor: ThreadPoolExecutor | None = None

    async def __aenter__(self) -> ThreadPoolExecutor:
        self._semaphore = self._pool.get_semaphore()
        self._executor = self._pool.get_executor()
        await self._semaphore.acquire()
        return self._executor

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._semaphore is not None:
            self._semaphore.release()

def with_resource_async(kind: PoolKind, db_path: str | None=None, read_only: bool=True) -> Any:
    """
    Async context manager for acquiring/releasing pooled resources.

    For CPU pools, uses semaphore for bounded concurrency control.
    For other pools, falls back to sync context manager.
    """
    if kind in (PoolKind.CPU_IO, PoolKind.CPU_BLOCKING):
        pool = _cpu_io_pool if kind == PoolKind.CPU_IO else _cpu_blocking_pool
        return _AsyncPoolContextManager(pool)
    else:
        return _SyncPoolContextManagerWrapper(kind, db_path, read_only)

class _SyncPoolContextManagerWrapper:
    """Sync context manager wrapper for async context."""
    __slots__ = ('_kind', '_db_path', '_read_only', '_ctx')

    def __init__(self, kind: PoolKind, db_path: str | None, read_only: bool) -> None:
        self._kind = kind
        self._db_path = db_path
        self._read_only = read_only
        self._ctx: Any = None

    async def __aenter__(self) -> Any:
        self._ctx = with_resource(self._kind, self._db_path, self._read_only)
        return self._ctx.__enter__()

    async def __aexit__(self, *exc_info: Any) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(*exc_info)

async def run_in_io_pool(func: Any, *args: Any, **kwargs: Any) -> Any:
    """
    Run function in bounded CPU I/O pool.

    Respects M1ResourceGovernor adaptive limits.
    """
    loop = asyncio.get_running_loop()
    executor = _cpu_io_pool.get_executor()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

async def run_in_blocking_pool(func: Any, *args: Any, **kwargs: Any) -> Any:
    """
    Run blocking function in bounded CPU blocking pool.

    Use for truly blocking I/O (file ops, sync DB calls).
    """
    loop = asyncio.get_running_loop()
    executor = _cpu_blocking_pool.get_executor()
    return await loop.run_in_executor(executor, lambda: func(*args, **kwargs))

@dataclass(frozen=True, slots=True)
class PoolStats:
    """Aggregated pool statistics."""
    duckdb_ro: _DuckDBPoolStats = field(default_factory=_DuckDBPoolStats)
    duckdb_rw: _DuckDBPoolStats = field(default_factory=_DuckDBPoolStats)
    cpu_io_max: int = 0
    cpu_blocking_max: int = 0
    mlx_available: bool = False
    ane_available: bool = False
    coreml_available: bool = False

def get_pool_stats() -> PoolStats:
    """Get snapshot of all pool statistics."""
    return PoolStats(duckdb_ro=_duckdb_ro_pool.stats, duckdb_rw=_duckdb_rw_pool.stats, cpu_io_max=_cpu_io_pool._adaptive_max, cpu_blocking_max=_cpu_blocking_pool._adaptive_max, mlx_available=_mlx_pool.is_available, ane_available=_ane_pool.is_available, coreml_available=_coreml_pool.is_available)

def _cleanup_pools() -> None:
    """Cleanup all pools at interpreter exit."""
    _duckdb_ro_pool.close_all()
    _duckdb_rw_pool.close_all()
    _cpu_io_pool.shutdown(wait=False)
    _cpu_blocking_pool.shutdown(wait=False)
atexit.register(_cleanup_pools)

def resize_cpu_pools(preset: Any) -> None:
    """
    Resize CPU pools based on memory pressure preset.

    Called by M1ResourceGovernor when memory state changes.
    """
    try:
        new_max = max(1, getattr(preset, 'max_workers', _CPU_IO_WORKERS_DEFAULT))
    except Exception:
        new_max = _CPU_IO_WORKERS_DEFAULT
    _cpu_io_pool.resize(new_max)
    _cpu_blocking_pool.resize(max(1, new_max // 2))
__all__ = ['PoolKind', 'PoolStats', 'with_resource', 'with_resource_async', 'run_in_io_pool', 'run_in_blocking_pool', 'get_pool_stats', 'resize_cpu_pools']