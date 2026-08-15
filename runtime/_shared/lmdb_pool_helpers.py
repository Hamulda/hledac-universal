"""
Shared LMDB pool helpers — extracted constants and types.

These values are shared between:
- runtime.lmdb_pool (canonical LMDB pool)
- runtime._legacy_role_based_pools (deprecated, delegates to lmdb_pool)

M1 8GB calibrated constants:
  - _LMDB_WORKERS=2: LMDB writer limit (1 writer + 1 reader)
  - ThreadPoolExecutor with thread_name_prefix="hledac-lmdb"
"""

from __future__ import annotations
from core import aclose

# LMDB pool configuration — M1 8GB calibrated
_LMDB_WORKERS: int = 2  # LMDB writer limit (1 writer + 1 reader)

__all__ = [
    "_LMDB_WORKERS",
]
