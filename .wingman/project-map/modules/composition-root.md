# Composition Root

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/composition-root.md` |
| Source Path | `_core/composition_root.py` |

## Summary

Central dependency injection and wiring layer. Owns event loop creation, signal handling, DuckDB bootstrap, MLX prewarm, layer stack assembly, and all shutdown paths.

## Evidence

- `build_runtime()` is synchronous (no event loop yet)
- Initializes: DuckDBShadowStore, SprintLifecycleManager, MLX/Hermes prewarm, ResourceGovernor, EvidenceLog, Layer stack, Health-check runner
- Caller owns loop lifecycle for structured exit codes

## Use When

- Understanding service initialization order
- Adding new service dependencies
- Debugging startup/shutdown issues

## Do Not Use When

- Changing business logic (see pipeline, coordinators)
- Changing CLI parsing (see cli/parser)
