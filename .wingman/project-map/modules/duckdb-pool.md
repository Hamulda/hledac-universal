# DuckDB Pool

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/duckdb-pool.md` |
| Source Path | `_core/duckdb_pool.py` |

## Summary

Canonical DuckDB connection pool for Hledac. MUST use DuckDBShadowStore.async_ingest_findings_batch() for all writes. Bounded RO pool + single RW pool with M1 8GB safe defaults. ReadCoordinator prevents deadlock.

## Evidence

- RO pool: io_threads (2) from ConcurrencyPreset, 1GB limit per connection
- RW pool: 1 connection with serial write lock
- ReadCoordinator: asyncio.Semaphore(2) limits concurrent reads
- ISSUE-17 fix: write barrier pattern prevents read saturation deadlock
- CI guard: grep for duckdb.connect( outside this module

## Use When

- Writing to DuckDB (use async_ingest_findings_batch)
- Reading from DuckDB (use pool acquire)
- Understanding read/write coordination

## Do Not Use When

- Direct duckdb.connect() calls (CI will catch it)
