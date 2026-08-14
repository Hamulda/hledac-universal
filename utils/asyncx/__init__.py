# hledac/universal/utils/async/__init__.py
# Modular async utilities package
#
# Structure:
# - _monitor.py: AsyncMonitor, sys.monitoring integration (Python 3.14+)
# - _fault.py: Failure tracking, silent_except decorator
# - _parallel.py: Parallel execution primitives
# - _rate_limit.py: Rate limiting (gates, token buckets)
# - _core.py: DNS, timing, lifecycle helpers
#
# Backward compatibility: all public API is re-exported from this package.
"""
Modular async utilities package

Structure:
- _monitor.py: AsyncMonitor, sys.monitoring integration (Python 3.14+)
- _fault.py: Failure tracking, silent_except decorator
- _parallel.py: Parallel execution primitives
- _rate_limit.py: Rate limiting (gates, token buckets)
- _core.py: DNS, timing, lifecycle helpers

Backward compatibility: all public API is re-exported from this package.

Preferred imports:
    from hledac.universal.utils.asyncx import parallel, parallel_ok, silent_except
    from hledac.universal.utils.asyncx import bounded_parallel_map, chunked_taskgroup
    from hledac.universal.utils.asyncx import BoundedPerHostGate, DomainRateLimiter
    from hledac.universal.utils.asyncx import safe_wait_for, retry_backoff_async
"""

from __future__ import annotations

from typing import TYPE_CHECKING

# Import from submodules
from hledac.universal.utils.asyncx._core import (
    async_getaddrinfo,
    first_completed,
    monotonic_ms,
    parallel_close,
    parallel_close_async,
    retry_backoff_async,
    safe_wait_for,
    stop_task,
)

from hledac.universal.utils.asyncx._fault import (
    get_cascading_failure_id,
    silent_except,
)

from hledac.universal.utils.asyncx._monitor import (
    AsyncMonitor,
    get_async_monitor,
    init_async_monitoring,
)

from hledac.universal.utils.asyncx._parallel import (
    ExceptionPolicy,
    ParallelResult,
    RaceFirstSuccessResult,
    SafeGatherResult,
    _BoundedExceptionLog,
    bounded_parallel_map,
    chunked_taskgroup,
    parallel,
    parallel_ok,
    parallel_taskgroup_star,
    race_first_success,
    safe_create_task,
    safe_gather,
    safe_gather_fire_and_forget,
    safe_gather_ok,
    safe_gather_strict,
    try_group,
    _check_gathered,
    ConcurrencyBudgetResolver,
)

from hledac.universal.utils.asyncx._rate_limit import (
    BoundedPerHostGate,
    DomainRateLimiter,
    _TokenBucketState,
)

# Re-export for backward compatibility
from hledac.universal.utils.asyncx._parallel import (
    current_otel_context,
)

if TYPE_CHECKING:
    pass


__all__ = [
    # _monitor.py
    "AsyncMonitor",
    "get_async_monitor",
    "init_async_monitoring",
    # _fault.py
    "silent_except",
    "get_cascading_failure_id",
    # _parallel.py
    "parallel",
    "parallel_ok",
    "try_group",
    "parallel_taskgroup_star",
    "race_first_success",
    "safe_create_task",
    "safe_gather",
    "safe_gather_ok",
    "safe_gather_strict",
    "safe_gather_fire_and_forget",
    "bounded_parallel_map",
    "race_first_success",
    "chunked_taskgroup",
    "_check_gathered",
    "ParallelResult",
    "SafeGatherResult",
    "RaceFirstSuccessResult",
    "_BoundedExceptionLog",
    "ExceptionPolicy",
    "ConcurrencyBudgetResolver",
    "current_otel_context",
    # _rate_limit.py
    "BoundedPerHostGate",
    "DomainRateLimiter",
    "_TokenBucketState",
    # _core.py
    "async_getaddrinfo",
    "safe_wait_for",
    "first_completed",
    "monotonic_ms",
    "stop_task",
    "parallel_close",
    "parallel_close_async",
    "retry_backoff_async",
]
