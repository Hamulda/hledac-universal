"""
compat/locks_legacy — Legacy shims for lock registry exports.

Deprecated: import directly from ``core.locks`` instead.
This module exists only for backward compatibility during migration.
"""

import warnings

__all__ = [
    "LockCategory",
    "LockInfo",
    "register_lock",
    "acquire_in_order",
    "get_registered_locks",
    "get_locks_by_category",
    "AsyncLockDCLP",
    "make_counter",
]

warnings.warn(
    "compat.locks_legacy is deprecated. Import from core.locks instead.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal._core.locks import (
    AsyncLockDCLP,
    LockCategory,
    LockInfo,
    acquire_in_order,
    get_locks_by_category,
    get_registered_locks,
    make_counter,
    register_lock,
)
