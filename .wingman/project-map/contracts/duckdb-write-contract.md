# DuckDB Write Contract

## Metadata

| Field | Value |
| --- | --- |
| Kind | contract |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `contracts/duckdb-write-contract.md` |

## Summary

All DuckDB writes MUST route through DuckDBShadowStore.async_ingest_findings_batch(). All other DuckDB operations (reads, scripts) MUST use the connection pool. No direct duckdb.connect() outside duckdb_pool.py.

## Contract

```
Canonical write: DuckDBShadowStore.async_ingest_findings_batch()
Canonical read:  DuckDBShadowStore query methods
Connection pool:  _core/duckdb_pool.py (RO pool + RW pool)
FORBIDDEN:       duckdb.connect() outside duckdb_pool.py
```

## Evidence

- CI guard: grep for duckdb.connect( outside duckdb_pool.py
- ISSUE-17 fix: ReadCoordinator prevents read saturation deadlock
- RW pool: 1 connection, serial write lock

## Use When

- Writing to DuckDB
- Reading from DuckDB

## Do Not Use When

- Direct duckdb.connect() calls (CI will catch it)
