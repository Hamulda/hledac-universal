---
confidence: 0.85
sources: [data/duckdb_store/_index.md, memory/resource_governor/_index.md, memory/resource_governor/_index.md, facts/project/_index.md]
synthesized_at: '2026-07-26T11:44:30.878Z'
type: synthesis
title: Zero-Copy and Batching are Universal Optimizations
summary: Zero-copy via Arrow, msgspec, and batch operations appear across storage (DuckDB), IPC (MPSC), and caching layers
tags: [zero-copy, arrow, batch, performance, memory-efficiency]
related: []
keywords: [zero-copy, arrow, msgspec, batch, neon-simd, executemany, optimization]
createdAt: '2026-07-26T11:44:30.878Z'
updatedAt: '2026-07-26T11:44:30.878Z'
---

# Zero-Copy and Batching are Universal Optimizations

The project systematically applies zero-copy patterns: Arrow ingest for DuckDB, msgspec.msgpack for MPSC IPC, NEON SIMD for hashing, batch operations over single calls. Memory efficiency is a first-class concern.

## Evidence

- **data/duckdb_store**: Arrow Ingest: Zero-copy, 1.5-2x faster than executemany
- **memory/resource_governor**: Zero-copy via msgspec.msgpack.encode() for MPSC batch events
- **memory/resource_governor**: msgspec.Struct with gc=False saves ~200 bytes per instance
- **facts/project**: batch_xxh3_64_hex 10x via NEON SIMD
