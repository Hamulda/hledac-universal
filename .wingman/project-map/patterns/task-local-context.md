# Task-Local Context

## Metadata

| Field | Value |
| --- | --- |
| Kind | pattern |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `patterns/task-local-context.md` |
| Source Path | `paths.py` |

## Summary

ContextVar-based task-local overrides for paths, enabling per-sprint isolation without thread-local complexity.

## Evidence

- _paths_context_var: ContextVar[_Paths | None]
- set_current_paths() / get_current_paths() / reset_current_paths()
- Caller owns loop lifecycle in composition_root

## Use When

- Per-task path isolation
- Sprint-specific path overrides

## Do Not Use When

- Cross-task shared state (use SharedBlock in memory_layer)
