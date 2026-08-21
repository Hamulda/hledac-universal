"""
DEPRECATED (F270): Shared executors — replaced by domain_executors.

This module is DEPRECATED and will be removed in a future sprint.
Use domain_executors directly:
  - get_legacy_cpu_executor() → from utils.domain_executors
  - get_legacy_io_executor()  → from utils.domain_executors

Migration:
  BEFORE:  from utils.executors import CPU_EXECUTOR
  AFTER:   from utils.domain_executors import get_legacy_cpu_executor
           CPU_EXECUTOR = get_legacy_cpu_executor()
"""

import warnings

warnings.warn(
    "DEPRECATED (F270): utils.executors is deprecated. "
    "Use domain_executors.get_legacy_cpu_executor() / get_legacy_io_executor() "
    "or utils.rayon_pool (run_in_cpu_pool, run_in_io_pool) instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.utils.domain_executors import get_legacy_cpu_executor, get_legacy_io_executor, shutdown_all

__all__ = ["get_legacy_cpu_executor", "get_legacy_io_executor", "shutdown_all"]
