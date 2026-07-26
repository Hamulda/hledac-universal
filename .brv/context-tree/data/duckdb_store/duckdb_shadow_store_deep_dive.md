---
title: DuckDB Shadow Store Deep Dive
summary: DuckDB Shadow Store with 3-tier facts hierarchy, chunk config (1024/4), IOC 3-tier fallback chain, RemoteParquetSource, and M1 8GB safe settings
tags: []
related: [data/duckdb_store/duckdb_shadow_store.md, data/duckdb_store/duckdb_remote_parquet_support.md, data/duckdb_store/duckpgqgraph_api.md]
keywords: []
createdAt: '2026-07-26T11:56:18.147Z'
updatedAt: '2026-07-26T11:56:18.147Z'
---
## Reason
Curate DuckDB Shadow Store configuration, facts hierarchy, and IOC extraction patterns from working notes

## Raw Concept
**Task:**
Document DuckDB Shadow Store architecture, configuration, and patterns

**Flow:**
IOC extraction -> buffering (_IOC_CHUNK=128) -> DuckDB insert -> Parquet export

**Timestamp:** 2026-07-26

**Patterns:**
- `PRAGMA threads = [0-9]+` - DuckDB thread configuration
- `SET memory_limit = .*GB` - DuckDB memory limit setting
- `_IOC_CHUNK = [0-9]+` - IOC per-chunk sizing

## Narrative
### Structure
DuckDB Shadow Store handles sprint facts, shadow findings, and cross-sprint temporal events. Uses DuckPGQGraph for IOC storage (F272).

### Dependencies
DuckDB >= 1.5 for ATTACH remote Parquet support, DuckPGQGraph extension, Rust batch functions (ISSUE-024)

### Highlights
Three-tier IOC fallback: (1) batch_ioc_extract_unified_python zero-copy, (2) batch_ioc_extract_unified rayon, (3) pure Python. RemoteParquetSource supports S3/HTTPS/Azure/GCS/Postgres.

### Rules
M1 8GB: gc=False for TargetProfileSummary. Async duckdb.connect() via asyncio.to_thread(). preserve_insertion_order=false on writes.

### Examples
RemoteParquetSource ATTACH: CREATE SECRET (TYPE S3, ...) -> ATTACH s3://bucket/file.parquet AS remote (TYPE PARQUET)

## Facts
- **duckdb_chunk_config**: DuckDB chunk_size=1024, pipeline_maxsize=4 (updated from 500/2) [project]
- **duckdb_pressure_states**: DuckDB pressure states: WARN=768/3, CRITICAL=512/2, EMERGENCY=256/2 [project]
- **ioc_chunk_size**: IOC buffering chunk size: _IOC_CHUNK = 128 [project]
- **m1_8gb_duckdb_read**: M1 8GB DuckDB reads: PRAGMA threads=2, memory_limit=1GB, hard_memory_limit=1GB [project]
- **m1_8gb_duckdb_write**: M1 8GB DuckDB writes: PRAGMA threads=2, memory_limit=2GB, hard_memory_limit=1GB [project]
- **ioc_graph_removed**: F272: DuckDB ioc_graph table removed; IOC storage via DuckPGQGraph [project]
