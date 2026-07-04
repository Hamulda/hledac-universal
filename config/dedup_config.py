"""
Dedup configuration — single source of truth.

Migrated from:
  - knowledge/dedup.py: _DEDUP_HOT_CACHE_MAX, _DEDUP_LMDB_MAP_SIZE
  - knowledge/quality_assessment.py: _DEDUP_HOT_CACHE_MAX, _DEDUP_LMDB_MAP_SIZE

All dedup-related config MUST be imported from here. No lazy loading,
no circular imports, no module-level override patterns.
"""
from __future__ import annotations

import os

# Sprint 8AG §6.17: Default dedup LMDB map size (Phase4: 256MB — int8 embeddings 4× compression frees headroom)
DEDUP_LMDB_MAP_SIZE: int = int(os.environ.get(
    "HLEDAC_DEDUP_LMDB_MAP_SIZE",
    str(256 * 1024 * 1024),  # 256 MB
))

# Sprint F216G: Hard cap on in-memory dedup hot-cache entries.
# Bounded to prevent unbounded memory growth on M1 8GB.
DEDUP_HOT_CACHE_MAX: int = int(os.environ.get(
    "HLEDAC_DEDUP_HOT_CACHE_MAX",
    "10000",
))
