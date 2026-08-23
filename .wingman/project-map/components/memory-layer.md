# Memory Layer

## Metadata

| Field | Value |
| --- | --- |
| Kind | component |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `components/memory-layer.md` |
| Source Path | `layers/memory_layer.py` |

## Summary

Cross-session shared data and entropy masking. SharedBlock for cross-session state, EntropyMaskingManager for privacy noise injection with O(|fifo|) eviction.

## Evidence

- SharedBlock: cross-session research context, evidence carriers
- EntropyMaskingManager: privacy noise injection, FIFO eviction
- Wraps MemoryManager (Layer 1 per-session) with Layer 2 cross-session

## Use When

- Cross-session state sharing
- Privacy-preserving memory operations

## Do Not Use When

- Per-session ephemeral storage (use MemoryManager directly)
