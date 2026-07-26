---
confidence: 0.9
sources: [facts/project/_index.md, memory/resource_governor/_index.md, facts/project/_index.md, data/duckdb_store/_index.md]
synthesized_at: '2026-07-26T11:44:30.876Z'
type: synthesis
title: Rust/PyO3 FFI Dominates All Hot Paths
summary: '30+ Rust modules via PyO3 handle critical hot paths: MPSC batch (crossbeam), hashing (xxh3 NEON SIMD), graph analytics (DuckPGQGraph)'
tags: [rust, pyo3, ffi, performance, hot-path]
related: []
keywords: [rust, pyo3, crossbeam, neon-simd, xxh3, ffi, duckpgqgraph, hot-path]
createdAt: '2026-07-26T11:44:30.876Z'
updatedAt: '2026-07-26T11:44:30.876Z'
---

# Rust/PyO3 FFI Dominates All Hot Paths

Rust/PyO3 is the consistent pattern for performance-critical paths. The project uses Rust for MPSC IPC, SIMD hashing, DuckDB graph queries, and lock-free data structures — all bridging to Python via PyO3.

## Evidence

- **facts/project**: Rust/PyO3: 30+ modules in rust_extensions/src/
- **memory/resource_governor**: MPSC Rust: crossbeam-channel bounded (2048), ~2-5ns/send via ARM LSE atomics
- **facts/project**: batch_xxh3_64_hex via NEON SIMD, 10x speedup
- **data/duckdb_store**: DuckPGQGraph Rust implementation (F272) replacing Python igraph
