"""
Sprint F201B: Truth Docs Drift Repair — Probe Tests
===================================================

Tests verify that documentation files are consistent with actual code:
1. GHOST_INVARIANTS.md: _check_gathered authority is network.session_runtime
2. STORAGE_LAYER_DOCUMENTATION.md: canonical write path uses async_ingest_findings_batch
3. REAL_ARCHITECTURE.md: active/dormant verdicts match F195C-F200D wiring

Invariant table:
  F201B-1 | GHOST_INVARIANTS.md links _check_gathered to network.session_runtime
  F201B-2 | GHOST_INVARIANTS.md documents tuple return (ok, errors) contract
  F201B-3 | STORAGE_LAYER_DOCUMENTATION.md lists async_ingest_findings_batch in API
  F201B-4 | STORAGE_LAYER_DOCUMENTATION.md example uses async_ingest_findings_batch
  F201B-5 | STORAGE_LAYER_DOCUMENTATION.md has NO banned single-finding write example
  F201B-6 | REAL_ARCHITECTURE.md marks stealth/ as ACTIVE (was incorrectly dormant)
  F201B-7 | REAL_ARCHITECTURE.md marks prefetch/ as partially active (was dormant)
"""
from __future__ import annotations
