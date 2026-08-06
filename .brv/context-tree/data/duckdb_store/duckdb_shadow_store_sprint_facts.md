---
title: DuckDB Shadow Store Sprint Facts
summary: 'DuckDBShadowStore: 1GB memory default, 3-tier IOC fallback, Arrow zero-copy, RemoteParquetSource with 5 URI schemes, SEC-02 permissions, S-07 async ops, S1-06 bounded queue'
tags: []
related: [data/duckdb_store/duckdb_shadow_store_extended_architecture.md]
keywords: []
createdAt: '2026-07-26T11:18:46.783Z'
updatedAt: '2026-08-05T15:12:23.363Z'
---
## Reason
Document DuckDBShadowStore architecture with all technical decisions

## Raw Concept
**Task:**
Document DuckDBShadowStore architecture and sprint facts

**Changes:**
- F272: DuckDB ioc_graph table removed, IOC storage via DuckPGQGraph
- Truth write graph slot added for ACTIVE-phase buffered writes
- F350M-R extraction: DuckDBQueryExecutor moved to knowledge/query_executor.py
- S1-06 FIX: Bounded queue with backpressure (_QUEUE_MAXSIZE=16)
- SEC-02: DuckDB file permission hardening to 0o600
- E-33: URI scheme whitelist and SQL injection prevention
- ISSUE-024: Rust batch functions wired (IOC extraction, Arrow builders)
- P4-8: RemoteParquetSource with DuckDB 1.5+ native Parquet ATTACH
- DuckDB memory corrected from 600MB to 1GB
- IOC extraction 3-tier fallback: Python zero-copy -> Rayon PyO3 -> Pure Python
- Arrow zero-copy ingest (HLEDAC_ARROW_INGEST default ON)
- RemoteParquetSource with DuckDB 1.5+ ATTACH pattern
- SEC-02: File permission hardening to 0o600
- S-07: Async DuckDB via asyncio.to_thread
- S1-06: Bounded queue backpressure (size 16, timeout 5.0s)

**Files:**
- rust_extensions/src/parquet_reader.rs
- knowledge/query_executor.py

**Flow:**
Data ingestion -> Arrow zero-copy -> DuckDB -> Query cache L1/L2 -> SQL analysis

**Timestamp:** 2026-08-05

## Narrative
### Structure
DuckDBShadowStore handles SQL storage with DuckPGQGraph for IOC graph storage. RemoteParquetSource reads remote parquet files via DuckDB native ATTACH.

### Dependencies
Requires duckdb, polars, pyarrow. Rust backend optional for IOC extraction.

### Highlights
1GB memory default. Insert config: chunk=500, concurrency=2. Pragmas: threads=2, memory_limit=2GB, hard_memory_limit=1GB. Capability: rss=200MB, peak=512MB.

### Rules
SEC-02: DuckDB files must have 0o600 permissions
E-33: Only whitelisted URI schemes allowed: s3, https, az, gs, postgres
S-07: All DuckDB operations must be async to prevent event loop blocking
S1-06: Queue size 16 with 5.0s put timeout for backpressure

### Examples
ATTACH pattern: CREATE SECRET (TYPE S3, KEY_ID "...") -> ATTACH "s3://bucket/file.parquet" AS remote (TYPE PARQUET)

## Facts
- **duckdb_memory_limit**: DuckDB memory default is 1GB (GHOST_DUCKDB_MEMORY) [project]
- **duckdb_memory_fix**: Previously documented as 600MB but code shows 1GB [project]
- **ioc_extraction_fallback**: IOC extraction 3-tier fallback: Python zero-copy -> Rayon PyO3 -> Pure Python [project]
- **arrow_ingest**: Arrow zero-copy ingest via HLEDAC_ARROW_INGEST (default ON) [project]
- **insert_config**: Insert chunk size: 500, concurrency: 2 [project]
- **duckdb_pragmas**: DuckDB pragmas: threads=2, memory_limit='2GB', hard_memory_limit='1GB' [project]
- **capability_cost**: Capability cost: rss_mb=200, peak_mb=512, tier=heavy [project]
- **duckpgqgraph**: DuckPGQGraph for IOC graph storage (F272 replacement) [project]
- **query_cache**: Query cache L1/L2 with TTL [project]
- **remote_sources**: RemoteParquetSource supports s3, https, az, gs, postgres URI schemes [project]
- **file_permissions**: SEC-02: DuckDB file permission hardening to 0o600 [project]
- **async_duckdb**: S-07: Async DuckDB operations to prevent event loop blocking [project]
- **bounded_queue**: S1-06: Bounded queue backpressure (queue size 16, timeout 5.0s) [project]
- **uri_whitelist**: E-33: URI schemes whitelist enforced (s3, https, az, gs, postgres) [project]
- **gc_setting**: gc=False on TargetProfileSummary for M1 8GB compatibility [project]
