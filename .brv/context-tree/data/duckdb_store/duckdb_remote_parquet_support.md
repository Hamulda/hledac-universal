---
title: DuckDB Remote Parquet Support
summary: DuckDB 1.5+ supports S3/HTTPS/Azure/GCS/Postgres via ATTACH. ParquetHistoryReader provides filter pushdown and zero-copy Arrow batches.
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:18:46.788Z'
updatedAt: '2026-07-26T11:18:46.788Z'
---
## Reason
Document DuckDB 1.5+ remote parquet ATTACH capabilities

## Raw Concept
**Task:**
Document DuckDB remote parquet support and ParquetHistoryReader

**Flow:**
RemoteParquetSource -> iter_batches -> Arrow RecordBatch -> Polars

## Narrative
### Structure
RemoteParquetSource(uri, source_type, credentials, alias, columns, batch_size, sql_where). iter_batches() yields zero-copy Arrow RecordBatches. to_polars_lazy() enables filter pushdown. iter_batches_async() uses thread pool.

### Dependencies
DuckDB 1.5+, pyarrow, polars

### Highlights
Supported sources: S3 (CREATE SECRET), HTTPS (LOAD httpfs), Azure, GCS, Postgres. ParquetHistoryReader wraps RemoteParquetSource with time range filtering.
