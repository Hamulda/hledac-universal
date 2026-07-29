"""
hledac.universal.compat — Deprecated shim package (F350M-R A-01)

This package contains deprecation stubs that re-export from canonical modules.
All logic has been migrated — these stubs exist only for backward compatibility
during the transition period.

**All modules deprecated**: migrate to canonical paths listed below.

| Deprecated stub              | Canonical module                        |
|-----------------------------|----------------------------------------|
| ``compat.core_watchdog``     | ``hledac.core.watchdog.Watchdog``       |
| ``compat.core_mlx_embeddings`` | ``hledac.core.mlx_embeddings``         |
| ``compat.core_http``         | ``hledac.universal.fetching.public_fetcher`` |
| ``compat.core_resilience``   | **REMOVED** (F350M-R A-01)             |
| ``compat.core_unified_ai_orchestrator`` | **REMOVED** (never functional)    |
| ``compat.cortex_director``  | **REMOVED** (stub was never functional)   |
| ``compat.security_*``       | ``hledac.security.<SymbolName>``        |

**Migration complete (F350M-R A-01):**
- All import sites migrated to canonical paths
- compat/ stubs now emit DeprecationWarning on import
- ``ls compat/`` shows only ``__init__.py`` with this doc

DO NOT add new imports here — use canonical module paths directly.
"""


__all__ = [
    # Core compat — deprecated, re-export from canonical
    # NOTE: core_resilience, core_unified_ai_orchestrator, cortex_director removed (F350M-R A-01)
    "core_watchdog",
    "core_http",
    "core_mlx_embeddings",
]
