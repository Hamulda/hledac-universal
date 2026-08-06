---
title: DuckDB Shadow Store Extended
summary: 'DuckDB Shadow Store: 3-tier facts hierarchy, 3-tier IOC extraction with zero-copy Python path, two-tier L1/L2 query cache with LMDB persistence, UMA-aware runtime settings, Arrow ingest, ParquetHistoryReader for 100GB+ lazy reads'
tags: []
related: [data/duckdb_store/duckdb_shadow_store_extended_architecture.md]
keywords: []
createdAt: '2026-07-11T19:02:58.783Z'
updatedAt: '2026-07-11T19:02:58.783Z'
---
## Reason
Curate DuckDB Shadow Store detailed facts, 3-tier hierarchy, IOC extraction tiers, query cache, and configuration

## Raw Concept
**Task:**
Document DuckDB Shadow Store architecture with facts hierarchy, IOC extraction, query cache, and configuration

**Changes:**
- F272: DuckDB ioc_graph table removed; IOC storage via DuckPGQGraph (graph/quantum_pathfinder.py)
- Added ParquetHistoryReader for 100GB+ lazy paginated reads with zero-copy Arrow
- Added two-tier L1/L2 query cache with LMDB persistence

**Flow:**
EvidenceLog.append() -> canonical_findings -> shadow_runs -> temporal_events

## Narrative
### Structure
3-tier facts hierarchy: TIER1=Sprint Facts (sprint_delta, sprint_scorecard, source_hit_log), TIER2=Shadow Findings (canonical_findings, shadow_runs), TIER3=Cross-Sprint (temporal_events)

### Dependencies
Requires rust_extensions for IOC extraction tiers, DuckPGQGraph for IOC storage (F272)

### Highlights
IOC extraction 3 tiers: (1) Zero-copy Python via PyList::append, (2) rayon Vec return with PyO3 auto-convert, (3) pure Python fallback. Query cache: L1=500-entry LRU/300s TTL, L2=5000-entry LMDB/300s TTL/16MB map. Arrow ingest 1.5-2x faster than executemany on M1 8GB.

## Facts
- **duckdb_facts_hierarchy**: DuckDB facts use 3-tier hierarchy: Sprint Facts (durable), Shadow Findings (durable), Cross-Sprint (append-only) [project]
- **ioc_extraction_zero_copy**: IOC extraction tier 1 uses zero-copy Python path via PyList::append/PyTuple::new [project]
- **duckdb_query_cache**: DuckDB query cache is two-tier: L1 in-memory LRU (500 entries, 300s TTL) + L2 LMDB (5000 entries, 300s TTL, 16MB map) [project]
- **query_cache_default_off**: HLEDAC_DUCKDB_QUERY_CACHE defaults to OFF (opt-in) [project]
- **ioc_graph_removed_f272**: F272 removed DuckDB ioc_graph table; IOC storage now via DuckPGQGraph [project]
- **parquet_history_reader**: ParquetHistoryReader enables 100GB+ IOC history reads without OOM (100k rows per batch) [project]
- **duckdb_memory_m1_8gb**: DuckDB memory limit: 600MB for M1 Air 8GB (Phase4 reduction from 2GB) [project]
- **arrow_ingest_performance**: Arrow ingest is 1.5-2x faster than executemany on M1 8GB, break-even at N=5-10 [project]
- **arrow_ingest_default_on**: HLEDAC_ARROW_INGEST defaults to ON [project]
- **duckdb_threads_m1_8gb**: DuckDB threads: 4 for M1 8GB (4P cores optimal) [project]
