# M1 8GB Memory Management

## Metadata

| Field | Value |
| --- | --- |
| Kind | domain |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `domains/m1-memory-management.md` |

## Summary

Hardware-specific domain: M1 8GB UMA optimizations. RAM budget ~6.25GB max, never swap silently.

## RAM Budget

| Component | Budget |
|---|---|
| macOS | ~2.5GB |
| Orchestrator | ~1GB |
| LLM (Hermes-3) | ~2GB |
| KV cache | ~0.75GB |
| **Total** | **~6.25GB** |

## Key Invariants

- BoundedList for all unbounded-grow collections
- MX.eval([]) before mx.metal.clear_cache()
- kv_bits=4, max_kv_size=8192 in mlx_lm.generate()
- NO --disable-gpu in nodriver args (GPU=CPU on UMA)
- AIMD ceiling=16 for parallel enrichment
- Batch size=100 for pipeline safety

## Evidence

- _core/bounded_collections.py
- resource_allocator.py (MLX prediction)
- coordinators/memory_coordinator.py (pressure detection)
- layers/memory_layer.py (cross-session state)

## Use When

- M1 8GB memory-sensitive changes
- Adding new bounded/unbounded collections

## Do Not Use When

- General Python (these are M1-specific optimizations)
