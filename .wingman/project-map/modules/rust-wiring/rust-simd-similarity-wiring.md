# rust-simd-similarity-wiring

**Type:** Rust FFI Wiring  
**Path:** `rust_extensions/wiring/simd_similarity_wiring.py`  
**Status:** current

## Purpose

SIMD-accelerated text similarity using AVX2/SSE4 on x86, NEON on ARM64. Used for semantic deduplication and near-duplicate detection.

## Key Functions

| Function | Purpose |
|----------|---------|
| `compute_simhash(text)` | Compute 64-bit SimHash |
| `hamming_distance(h1, h2)` | Hamming distance between hashes |
| `find_near_duplicates(texts)` | Batch near-duplicate detection |

## Invariants

- [RSS-1] SimHash computed via Rust SIMD, never pure Python for batch ops
- [RSS-2] Hamming distance threshold: 3 for near-duplicate认定
- [RSS-3] Batch size recommended: ≤10K texts per call

## M1 Memory Notes

NEON SIMD used on M1. ~10MB pre-allocated lookup tables.
