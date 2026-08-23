# Resource Allocator

## Metadata

| Field | Value |
| --- | --- |
| Kind | utility |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `utilities/resource-allocator.md` |
| Source Path | `resource_allocator.py` |

## Summary

Canonical request-level RAM budgeting and concurrency control. MLX linear regression for prediction, adaptive semaphore based on memory pressure, emergency brake for lowest-priority tasks.

## Authority Boundary

```
SAM (utils/uma_budget.py)     → raw memory sampling, no policy
GOVERNOR (core/resource_governor.py) → policy/hysteresis/runtime
ALLOCATOR (resource_allocator.py)    → request-level budgeting/concurrency
```

## Evidence

- Request-level RAM budgeting with MLX prediction
- Adaptive concurrency semaphore
- Emergency brake: cancel lowest priority task
- Concurrency limits adapt to system memory pressure

## Use When

- Request-level resource budgeting
- Memory-aware concurrency control

## Do Not Use When

- Raw memory sampling (use uma_budget.py)
- Runtime policy/hysteresis (use resource_governor.py)
