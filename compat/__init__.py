"""
hledac.universal.compat — Shim package for internal compatibility

This package contains proxy modules that re-export from sibling packages
(hledac.core, hledac.security, hledac.cortex) to avoid circular import
chains in the hledac namespace.

NOTE: Security modules have been migrated directly to security/ (F330-COMPAT-LEAK-009).
Only core/cortex compat shims remain here.

**Naming:** `compat/` (not `_shims/`) — clear intent, public-friendly prefix.

DO NOT add new logic here — prefer direct imports when circular issues are resolved.
"""
from __future__ import annotations


__all__ = [
    # Core compat
    "core_resilience",
    "core_watchdog",
    "core_http",
    "core_mlx_embeddings",
    "core_unified_ai_orchestrator",
    "cortex_director",
    # Security compat — REMOVED (F330-COMPAT-LEAK-009)
    # All security modules migrated to security/
]
