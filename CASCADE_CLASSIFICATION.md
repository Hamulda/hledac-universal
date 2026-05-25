# Cascade Classification — Sprint F3FORENSICS_ACTIVATE

## Finding: PENDING (conditionally active)

## What It Does

`discovery/cascade.py` (319 lines) implements a **providerless discovery cascade** — sequential first-hit-wins fallback chain: DDG → Historical Frontier → Wayback CDX.

Key exports:
- `async_search_providerless(query, max_results, timeout_s)` — main entry point
- `_async_search_sequential(query, max_results, timeout_s)` — sequential cascade implementation
- `_search_all_providers(query, max_results, timeout_s)` — parallel multi-provider search

## Wiring Status

**Conditionally wired** via `pipeline/live_public_pipeline.py`:
- Gate: `HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1` (default OFF)
- When enabled, replaces direct DDG with the cascade in `_ASYNC_DISCOVERY_SEARCH`
- Also referenced by `tests/probe_providerless_discovery/test_providerless_discovery_mesh.py` (probe tests)

Not imported by `discovery_planner.py` or `sprint_scheduler.py` directly.

## Classification Rationale

PENDING, not ACTIVE: The code exists, is designed for connection, and has a working integration path in `live_public_pipeline.py`, but the integration is opt-in (env-gated). Default behavior uses direct DDG. The "pending" designation reflects that it was intentionally deferred via the env gate rather than being fully integrated by default.

## Integration Path

When `HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY=1`:
```
live_public_pipeline.py (_ensure_discovery_patched)
  → imports async_search_providerless from discovery.cascade
  → _ASYNC_DISCOVERY_SEARCH = async_search_providerless
  → called by live_public_pipeline during discovery phase
```

## No Orphan Cleanup Required

Cascade.py is properly maintained and has probe test coverage. No archival or deletion needed.