---
confidence: 0.9
sources: [duckdb_store/_index.md, facts/project/_index.md, hledac_universal/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-18T00:18:19.620Z'
type: synthesis
title: DuckDB as Canonical Storage with Tiered Memory Budget
summary: DuckDB serves as primary analytics store, pinned to 600MB-2GB with chunk tuning and Arrow ingest optimization.
tags: [storage, duckdb, analytics, ioc-extraction]
related: []
keywords: [duckdb, shadow-store, chunk-size, arrow-ingest, ioc-extraction, facts-hierarchy, async-ingest]
createdAt: '2026-07-18T00:18:19.620Z'
updatedAt: '2026-07-18T00:18:19.620Z'
---

# DuckDB as Canonical Storage with Tiered Memory Budget

DuckDB is the single write authority for sprint facts (tiered: sprint facts → shadow findings → cross-sprint). All write paths funnel through async_ingest_findings_batch(). P3 priority for chunk tuning (MAX_CHUNK_SIZE=500, MAX_CHUNK_CONCURRENCY=2).

## Evidence

- **duckdb_store**: 3-tier facts hierarchy: Sprint Facts, Shadow Findings, Cross-Sprint. Chunk size 500, concurrency 2.
- **facts/project**: DuckDB ~600MB limit, 4 threads default, Arrow ingest 1.5-2× faster
- **hledac_universal**: DuckDB shadow store is canonical facts authority, storage trinity: DuckDB→LMDB→LanceDB
- **memory/resource_governor**: DuckDB chunk tuning as P3 priority (low effort, low leverage)
