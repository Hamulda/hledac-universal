# Performance Coordinator

## Metadata

- **Entry Path:** modules/performance-coordinator
- **Status:** current
- **Source:** coordinators/performance_coordinator.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

M1-optimized performance coordinator managing Metal cache, memory pressure, and resource allocation.

## Source Paths

- `coordinators/performance_coordinator.py`
- `_core/mlx_inference_lock.py`

## Use When

- Dynamic Metal cache sizing
- Memory pressure monitoring
- GPU/CPU resource management
- Model swap orchestration

## M1 8GB Constraints

| Resource | Budget |
|----------|--------|
| macOS | ~2.5GB |
| Orchestrator | ~1GB |
| LLM (Hermes-3) | ~2GB |
| KV Cache | ~0.75GB |
| **Total Max** | **6.25GB** |

## Metal Cache Strategy

- Dynamic ceiling: `min(max(available*0.2, 512MiB), 1.5GiB)`
- `mx.eval([])` before `mx.metal.clear_cache()`
- Relaxed mode disabled (feature, not bug)

## Related Entries

- modules/mlx-inference
- domains/m1-memory-management
