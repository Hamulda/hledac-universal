"""
DEPRECATED (ISSUE-010): Unified pool API — redirect to utils.pools

This module is DEPRECATED. Use the new unified pools module instead.

Migration:
    # BEFORE (deprecated)
    from utils.rayon_pool import run_in_cpu_pool_async

    # AFTER (unified)
    from utils.pools import run_in_cpu_pool_async
    # or
    from utils.pools import run_in_cpu_pool, run_in_io_pool, run_in_mixed_pool

The new utils.pools module provides:
    - PoolProtocol ABC for type-safe pool abstraction
    - PoolType enum for explicit pool selection
    - Unified rayon pools (cpu, io, mixed)
    - Thread pools with adaptive sizing
    - Subinterpreter pools (Python 3.14.6+)

See: utils/pools/__init__.py for the unified API.
"""

import warnings

warnings.warn(
    "DEPRECATED (ISSUE-010): utils.rayon_pool is deprecated. "
    "Use utils.pools instead: "
    "from utils.pools import run_in_cpu_pool_async, run_in_io_pool_async",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new unified location
from hledac.universal.utils.pools.rayon import (
    RayonPoolsAvailable,
    run_in_cpu_pool,
    run_in_io_pool,
    run_in_mixed_pool,
    run_in_cpu_pool_async,
    run_in_io_pool_async,
    run_in_mixed_pool_async,
)

__all__ = [
    "RayonPoolsAvailable",
    "run_in_cpu_pool",
    "run_in_io_pool",
    "run_in_mixed_pool",
    "run_in_cpu_pool_async",
    "run_in_io_pool_async",
    "run_in_mixed_pool_async",
]
