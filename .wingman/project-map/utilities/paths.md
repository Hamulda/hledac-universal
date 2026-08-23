# Paths Utility

## Metadata

| Field | Value |
| --- | --- |
| Kind | utility |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `utilities/paths.md` |
| Source Path | `paths.py` |

## Summary

Centralized path management with task-local overrides via contextvars. RAM disk support, per-sprint isolation, LMDB bounds.

## Key Paths

- `RAMDISK_ROOT` / `FALLBACK_ROOT` — scratch storage
- `DB_ROOT` — DuckDB database root
- `LMDB_ROOT` / `SPRINT_LMDB_ROOT` — LMDB storage
- `EVIDENCE_ROOT` — evidence files
- `SPRINT_STORE_ROOT` — per-sprint store (shared with DuckDB)
- `IOC_DB_PATH` — IOC database

## Evidence

- ContextVar-based task-local overrides: set_current_paths() / get_current_paths()
- RAM disk auto-detection: is_auto_ramdisk()
- LMDB map_size management: lmdb_map_size, get_lmdb_max_size_mb()
- Stale lock cleanup: cleanup_stale_lmdb_locks()

## Use When

- Any path access in the codebase
- Setting up per-sprint isolation

## Do Not Use When

- Hardcoding paths (always use PATHS or context)
