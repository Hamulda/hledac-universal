---
confidence: 0.92
sources: [facts/project/_index.md, duckdb_store/_index.md, hledac_universal/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-18T00:18:19.622Z'
type: synthesis
title: Rust/PyO3 Unifies All Hot Paths
summary: 30+ Rust extensions via PyO3/Maturin handle MPSC batching, IOC extraction, hashing, and graph analytics.
tags: [rust, pyo3, performance, hot-path, mpsc]
related: []
keywords: [pyo3, maturin, rust-extensions, mpsc-pool, batch-ops, neon-simd, xxhash, zero-copy]
createdAt: '2026-07-18T00:18:19.622Z'
updatedAt: '2026-07-18T00:18:19.622Z'
---

# Rust/PyO3 Unifies All Hot Paths

All performance-critical paths use Rust through PyO3: MPSC batch sends (~1µs/event vs 5µs N×calls), batch_ioc_extract_unified, xxh3_64 hashing (10× faster via NEON SIMD), and DuckPGQGraph for 10-100× graph analytics speedup. msgspec.msgpack for zero-copy IPC.

## Evidence

- **facts/project**: 30+ modules in rust_extensions/src/, MPSC pool 2048×512B≈1MiB, ARM LSE atomics ~2-5ns/send
- **duckdb_store**: batch_ioc_extract_unified Rust call (Tier 2), Arrow batch builder, msgspec.Struct hot-path DTOs
- **hledac_universal**: Rust extensions code review: OnceLock fix, SQL injection patch, GIL safety confirmed
- **memory/resource_governor**: Rust graph analytics 10-100× speedup over Python igraph (P1 priority)
