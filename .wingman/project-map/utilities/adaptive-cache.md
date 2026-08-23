# adaptive-cache

**Type:** Utility  
**Path:** `utils/cache/_adaptive.py`  
**Status:** current

## Purpose

Adaptive cache that tunes eviction policy based on access patterns.

## Key Functions

| Function | Purpose |
|----------|---------|
| `AdaptiveCache` | Main class |
| `access(key)` | Record access |
| `evict_lru()` | LRU eviction |
| `evict_lfu()` | LFU eviction |
| `select_policy()` | Auto-select best policy |

## Policies

| Policy | Best For |
|--------|----------|
| LRU | Temporal locality |
| LFU | Popular items |
| ARC | Mixed workloads |
| TinyLFU | Admission control |

## Invariants

- [UADC-1] Default: TinyLFU with LRU victim cache
- [UADC-2] Window size: 10K accesses
- [UADC-3] Adaptation: re-evaluate every 5 minutes
