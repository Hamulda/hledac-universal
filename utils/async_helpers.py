# hledac/universal/utils/async_helpers.py
# DEPRECATED: Re-exports from utils.asyncx package for backward compatibility.
#
# This module is DEPRECATED. All functionality has been moved to the
# modular `utils/asyncx/` package for better maintainability and testability.
#
# Migration:
#   OLD:                     NEW:
#   from utils.async_helpers import parallel
#   from utils.async_helpers import parallel_ok
#   from utils.async_helpers import silent_except
#   from utils.async_helpers import BoundedPerHostGate
#   from utils.async_helpers import DomainRateLimiter
#
#   Simply change the import path:
#   from utils.asyncx import parallel
#   from utils.asyncx import parallel_ok
#   from utils.asyncx import silent_except
#   from utils.asyncx import BoundedPerHostGate
#   from utils.asyncx import DomainRateLimiter
#
"""
DEPRECATED: Backward compatibility module for utils.asyncx package.

This module re-exports all public API from utils/asyncx/ package.
All functionality has been moved to the modular package structure.

Migration:
    from hledac.universal.utils.async_helpers import parallel
    → from utils.asyncx import parallel

    from hledac.universal.utils.async_helpers import silent_except
    → from utils.asyncx import silent_except

    from hledac.universal.utils.async_helpers import BoundedPerHostGate
    → from utils.asyncx import BoundedPerHostGate
"""

from __future__ import annotations

import warnings

# Emit deprecation warning on import
warnings.warn(
    "utils.async_helpers is DEPRECATED. "
    "Import from utils.asyncx instead. "
    "See migration guide in module docstring.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export everything from the new package for backward compatibility
from hledac.universal.utils.asyncx import (
from core import aclose
    # _monitor.py
    AsyncMonitor,
    get_async_monitor,
    init_async_monitoring,
    # _fault.py
    silent_except,
    get_cascading_failure_id,
    # _parallel.py
    parallel,
    parallel_ok,
    try_group,
    parallel_taskgroup_star,
    safe_create_task,
    safe_gather,
    safe_gather_ok,
    safe_gather_strict,
    safe_gather_fire_and_forget,
    bounded_parallel_map,
    race_first_success,
    chunked_taskgroup,
    _check_gathered,
    ParallelResult,
    SafeGatherResult,
    RaceFirstSuccessResult,
    _BoundedExceptionLog,
    ExceptionPolicy,
    ConcurrencyBudgetResolver,
    current_otel_context,
    # _rate_limit.py
    BoundedPerHostGate,
    DomainRateLimiter,
    _TokenBucketState,
    # _core.py
    async_getaddrinfo,
    safe_wait_for,
    first_completed,
    monotonic_ms,
    stop_task,
    parallel_close,
    parallel_close_async,
    retry_backoff_async,
)

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
