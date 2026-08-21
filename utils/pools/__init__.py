"""
ISSUE-010: Unified Pool Abstraction — Single API for all execution pools

Architecture:
    pools/
        ├── __init__.py        # Unified public API
        ├── protocol.py        # PoolProtocol ABC
        ├── rayon.py           # Rust rayon pools (cpu, io, mixed)
        ├── thread.py          # ThreadPoolExecutor wrapper
        └── subinterpreter.py  # InterpreterPoolExecutor (Python 3.14.6+)

M1 8GB Constraints:
    - Rayon pools: GIL-free CPU parallelism
    - Subinterpreters: Native Python parallelism (future)
    - Thread pools: Fallback for sync I/O
    - Resource governor: Adaptive limits based on memory pressure

Usage:
    from hledac.universal.utils.pools import get_pool, PoolType, PoolProtocol

    pool = get_pool(PoolType.CPU)  # Rayon cpu_pool (4 P-cores)
    result = await pool.run_sync(cpu_bound_func, *args)

    # Or use convenience functions
    from hledac.universal.utils.pools import run_in_cpu_pool_async
    result = await run_in_cpu_pool_async(hash_func, data)

Migration guide:
    # BEFORE (fragmented)
    from utils.rayon_pool import run_in_cpu_pool_async
    from _core.resource_pool import run_in_io_pool
    from utils.subinterpreter_pool import run_in_subinterpreter

    # AFTER (unified)
    from utils.pools import run_in_cpu_pool_async, run_in_io_pool_async
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from hledac.universal.utils.pools.protocol import (
    PoolProtocol,
    PoolType,
    PoolStats,
    SyncPoolProtocol,
    AsyncPoolProtocol,
)

# Re-export rayon pool functions
from hledac.universal.utils.pools.rayon import (
    RayonPoolsAvailable,
    run_in_cpu_pool,
    run_in_io_pool,
    run_in_mixed_pool,
    run_in_cpu_pool_async,
    run_in_io_pool_async,
    run_in_mixed_pool_async,
)

# Re-export thread pool functions
from hledac.universal.utils.pools.thread import (
    get_thread_pool,
    run_in_thread_pool,
    run_in_thread_pool_async,
)

# Re-export subinterpreter pool functions
from hledac.universal.utils.pools.subinterpreter import (
    is_subinterpreter_available,
    run_in_subinterpreter,
    run_batch_in_subinterpreter,
)

if TYPE_CHECKING:

T = TypeVar("T")

__all__ = [
    # Protocol
    "PoolProtocol",
    "PoolType",
    "PoolStats",
    "SyncPoolProtocol",
    "AsyncPoolProtocol",
    # Rayon pools (Rust)
    "RayonPoolsAvailable",
    "run_in_cpu_pool",
    "run_in_io_pool",
    "run_in_mixed_pool",
    "run_in_cpu_pool_async",
    "run_in_io_pool_async",
    "run_in_mixed_pool_async",
    # Thread pools
    "get_thread_pool",
    "run_in_thread_pool",
    "run_in_thread_pool_async",
    # Subinterpreter pools
    "is_subinterpreter_available",
    "run_in_subinterpreter",
    "run_batch_in_subinterpreter",
]
