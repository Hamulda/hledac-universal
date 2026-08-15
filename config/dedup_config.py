"""
Dedup configuration — single source of truth.

Migrated from:
  - knowledge/dedup.py: _DEDUP_HOT_CACHE_MAX, _DEDUP_LMDB_MAP_SIZE
  - knowledge/quality_assessment.py: _DEDUP_HOT_CACHE_MAX, _DEDUP_LMDB_MAP_SIZE

All dedup-related config MUST be imported from here. No eager env read,
no circular imports, no module-level override patterns.

Canonical source: DedupSettings in config/settings.py
These module-level vars exist for backward compat only — prefer DedupSettings.
"""

from hledac.universal._core.env_config import ENV
from _core import aclose

# Sprint 8AG §6.17: Default dedup LMDB map size (Phase4: 256MB — int8 embeddings 4× compression frees headroom)
DEDUP_LMDB_MAP_SIZE: int = ENV.get_int("HLEDAC_DEDUP_LMDB_MAP_SIZE", 256 * 1024 * 1024)

# Sprint F216G: Hard cap on in-memory dedup hot-cache entries.
# Bounded to prevent unbounded memory growth on M1 8GB.
DEDUP_HOT_CACHE_MAX: int = ENV.get_int("HLEDAC_DEDUP_HOT_CACHE_MAX", 10_000)
