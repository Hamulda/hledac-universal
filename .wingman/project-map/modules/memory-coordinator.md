# Memory Coordinator

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/memory-coordinator.md` |
| Source Path | `coordinators/memory_coordinator.py` |

## Summary

Universal Memory Coordinator with priority-based zones, MLX cache management, and thread-safe operations. Uses ctypes, gc, and asyncio for memory pressure management.

## Evidence

- Priority-based memory zones
- MLX cache management (metal.clear_cache integration)
- Thread-safe operations
- Enum-based memory state tracking

## Use When

- Understanding memory pressure management
- Adding MLX memory management
- Debugging memory coordinator issues

## Do Not Use When

- Low-level LMDB access (see memory_manager.py)
