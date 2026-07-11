---
title: Query Cache & IOC Extraction
summary: 'DuckDB: L1/L2 query cache (500/5000 entries, 300s TTL), Parquet row-group reader (100GB+ safe), IOC 3-tier fallback.'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:54:06.244Z'
updatedAt: '2026-07-11T14:54:06.244Z'
---
## Reason
Document DuckDB query cache tiers, Parquet history reader, and IOC extraction fallback

## Raw Concept
**Task:**
Document DuckDB query cache, Parquet history reader, and IOC extraction fallback system

**Changes:**
- Documented two-tier query cache with L1/L2 bounds
- Added Parquet history reader spec
- Documented IOC extraction 3-tier fallback

**Files:**
- knowledge/duckdb_store.py

**Flow:**
Query → L1 cache (500 entries) → L2 LMDB cache (5000 entries) → DuckDB execute → Parquet write/read

## Narrative
### Structure
DuckDB shadow store provides query caching (L1/L2), Parquet history reader for 100GB+ IOC history, and 3-tier IOC extraction fallback.

### Dependencies
Opt-in via HLEDAC_DUCKDB_QUERY_CACHE=1 (default OFF). Arrow zero-copy ingest via HLEDAC_ARROW_INGEST (default ON).

### Highlights
L1 in-memory LRU sub-millisecond hits. L2 LMDB 16MB persistent map. Parquet reader: zero-copy Arrow IPC→PyArrow→Polars, max 100k rows/batch. IOC types: ipv4/ipv6/domain/md5/sha1/sha256/email/cve.

### Rules
Rule 1: Query cache invalidation on schema migration via _invalidate_on_migration()
Rule 2: Parquet reader columns: id, query, source_type, confidence, ts, provenance_json
Rule 3: IOC tier 1: batch_ioc_extract_unified_python (zero-copy), tier 2: batch_ioc_extract_unified (Rayon/PyO3), tier 3: pure Python fallback

## Facts
- **duckdb_query_cache_tiers**: DuckDB query cache: L1 in-memory 500 entries, L2 LMDB 5000 entries, TTL 300s [project]
- **duckdb_memory_config**: DuckDB memory: 600MB default, 4 threads, 1GB temp storage for M1 8GB [project]
- **ioc_extraction_tiers**: IOC extraction 3-tier fallback: Python zero-copy → Rayon PyO3 → Pure Python [project]
- **duckdb_chunk_config**: MAX_CHUNK_SIZE 500 with MAX_CHUNK_CONCURRENCY 2 for M1 8GB safe parallelism [project]
