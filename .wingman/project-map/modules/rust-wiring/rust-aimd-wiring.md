# rust-aimd-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/aimd_wiring.py`  
**Status:** current

## Purpose

AIMD (Additive Increase Multiplicative Decrease) rate controller for parallel fetch orchestration. Dynamic concurrency control.

## Key Functions

| Function | Purpose |
|----------|---------|
| `AIMDRateController` | Class wrapper |
| `increase()` | Additive increase on success |
| `decrease(factor)` | Multiplicative decrease on failure |
| `get_rate()` | Current rate |
| `reset()` | Reset to initial state |

## Invariants

- [RAIMD-1] AI step: +1 concurrent request
- [RAIMD-2] MD factor: ×0.5 on failure (or custom)
- [RAIMD-3] Min rate: 1, Max rate: configured ceiling

## M1 Memory Notes

Stateless, minimal memory. ~1KB per controller instance.
