# DuckDB API Surface

## Metadata

| Field | Value | <!---->
| --- | --- |
| Kind | surface |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `surfaces/duckdb-api.md` |
| Source Path | `knowledge/duckdb_store.py`, `knowledge/db.py` |

## Summary

Canonical persistent store for sprint facts and derived analytics. Two tiers: DuckDBShadowStore (findings/facts) and UnifiedDatabaseFacade (connection facade).

## Key APIs

- `DuckDBShadowStore.async_ingest_findings_batch()` — canonical write (hot IOC path)
- `DuckDBShadowStore.submit_findings()` — store stage integration
- `DuckDBShadowStore._assess_finding_quality()` — semantic dedup trigger
- `UnifiedDatabaseFacade` — singleton connection facade (duckdb + lmdb)

## Evidence

- M1 8GB: WAL mode, 2 threads, 1GB limit per connection
- ReadCoordinator with asyncio.Semaphore(2) prevents deadlock
- Arrow IPC for zero-copy bulk insert
- LMDB for cache/dedup/KV (256MB default, max 512MB)

## Use When

- Writing sprint findings
- Reading sprint analytics
- Understanding the two-tier storage architecture

## Do Not Use When

- Direct duckdb.connect() calls (CI will catch it — use duckdb_pool)
