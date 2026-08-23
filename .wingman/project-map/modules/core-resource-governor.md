# core-resource-governor

**Type:** Core Infrastructure  
**Path:** `_core/resource_governor.py`  
**Status:** current

## Purpose

System resource governor for M1 8GB UMA constraint enforcement.

## Key Functions

| Function | Purpose |
|----------|---------|
| `ResourceGovernor` | Main class |
| `check_budget(operation)` | Check resource availability |
| `acquire(resource)` | Acquire resource allocation |
| `release(resource)` | Release resource |
| `pressure_level()` | Current pressure (0-3) |

## Pressure Levels

| Level | Memory | Action |
|-------|--------|--------|
| 0 | < 70% | Normal |
| 1 | 70-80% | Warn |
| 2 | 80-90% | Slow, defer |
| 3 | > 90% | Abort non-critical |

## Invariants

- [CRG-1] Budget enforcement: hard limit at 6.25GB
- [CRG-2] GC trigger: pressure >= 2
- [CRG-3] SWAP warning: log at pressure 3

## Dependencies

- `psutil` for memory metrics
