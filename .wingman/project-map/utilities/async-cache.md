# async-cache

**Type:** Utility  
**Path:** `utils/cache/_async.py`  
**Status:** current

## Purpose

Async-aware cache with TTL, size limits, and memory pressure integration.

## Key Functions

| Function | Purpose |
|----------|---------|
| `AsyncCache` | Main class |
| `get(key)` | Get cached value |
| `set(key, value, ttl)` | Set with TTL |
| `clear()` | Clear all entries |

## Invariants

- [UAC-1] TTL: configurable per entry
- [UAC-2] Max size: 1000 entries default
- [UAC-3] Pressure-aware eviction
- [UAC-4] Async-first: no blocking I/O

## M1 Memory Notes

Memory bounded by entry count and size limits.
