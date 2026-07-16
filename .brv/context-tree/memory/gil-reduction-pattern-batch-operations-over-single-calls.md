---
confidence: 0.82
sources: [memory/resource_governor/_index.md, memory/resource_governor/_index.md, facts/project/_index.md, facts/project/_index.md]
synthesized_at: '2026-07-16T11:30:38.727Z'
type: synthesis
title: 'GIL Reduction Pattern: Batch Operations Over Single Calls'
summary: Both MPSC send_batch and DuckDB async_ingest_findings_batch reduce GIL overhead via N→1 call bundling.
tags: [batch, gil, performance, rust, python]
related: []
keywords: [batch, send_batch, async_ingest, gil, overhead, reduce, bundle, mpsc, duckdb]
createdAt: '2026-07-16T11:30:38.727Z'
updatedAt: '2026-07-16T11:30:38.727Z'
---

# GIL Reduction Pattern: Batch Operations Over Single Calls

Batch-to-Rust pattern (single call for N items) is a deliberate architecture to minimize Python↔Rust context switches — appears in both storage and concurrency.

## Evidence

- **memory/resource_governor**: Rust send_batch reduces GIL acquisition from N× to 1×
- **memory/resource_governor**: Memory budget: 2048 × 512B ≈ 1 MiB for MPSC pool headroom
- **facts/project**: DuckDB writes via async_ingest_findings_batch()
- **facts/project**: batch_xxh3_64_hex function for bulk hashing
