---
children_hash: 181821e194a509a3d9f9688e6a4bb9fffff8cfc59719e7774cd5c98119a367a2
compression_ratio: 0.10776218001651527
condensation_order: 1
covers: [context.md, duckdb-as-canonical-storage-with-tiered-memory-budget.md, duckdb-dual-role-sql-store-graph-analytics-donor.md, duckdb_remote_parquet_support.md, duckdb_shadow_store.md, duckdb_shadow_store_architecture.md, duckdb_shadow_store_deep_dive.md, duckdb_shadow_store_extended.md, duckdb_shadow_store_extended_architecture.md, duckdb_shadow_store_sprint_facts.md, duckdb_thread_count_and_settings.md, duckpgqgraph-integration-gap.md, duckpgqgraph_api.md, duckpgqgraph_storage_trinity_gap_fill.md, fallback-chains-for-graceful-degradation.md, issue_032_write_serialization_fix.md, query_cache_ioc_extraction.md, walmanager.md, zero-copy-and-batching-are-universal-optimizations.md]
covers_token_total: 9688
summary_level: d1
token_count: 1044
type: summary
---
<think>
The user wants me to create a structural summary at level d1 from the provided knowledge entries about DuckDB store. Let me analyze the key themes:

1. **DuckDB as canonical storage** - 3-tier facts hierarchy, memory budgets, chunk tuning
2. **DuckPGQGraph** - graph analytics layer (replaces ioc_graph table), analytics donor pattern
3. **IOC extraction** - 3-tier fallback chain (Python zero-copy → Rayon → Pure Python)
4. **Query cache** - L1/L2 tiers with LMDB persistence
5. **Remote Parquet** - DuckDB 1.5+ support for S3/HTTPS/Azure/GCS/Postgres
6. **Security/Async** - SEC-02 permissions, S-07 async via asyncio.to_thread, S1-06 bounded queues
7. **Storage trinity** - DuckDB + LMDB + LanceDB + DuckPGQGraph
8. **WALManager** - LMDB-based WAL with namespaces, compaction triggers
9. **Architecture patterns** - fallback chains for graceful degradation, Arrow zero-copy ingest

Let me create a concise structural summary preserving key facts, relationships, and architectural decisions.
</think>

# DuckDB Store (data/duckdb_store)

DuckDB serves as the canonical analytics store with a shadow architecture supporting tiered facts, IOC extraction, and graph analytics. The storage trinity comprises DuckDB (SQL/facts), LMDB (WAL/cache), LanceDB (vector embeddings), and DuckPGQGraph (graph analytics donor).

## 3-Tier Facts Hierarchy

- **TIER1 Sprint Facts**: sprint_delta, sprint_scorecard, source_hit_log (durable)
- **TIER2 Shadow Findings**: canonical_findings, shadow_runs
- **TIER3 Cross-Sprint**: temporal_events (append-only)

Reference: `duckdb_shadow_store.md`, `duckdb_shadow_store_extended.md`

## DuckPGQGraph Analytics Layer

F272 replaced the DuckDB ioc_graph table with DuckPGQGraph as the analytics donor. Key methods: `upsert_ioc()`, `upsert_relation()`, `find_connected()`, `pagerank()`, `shortest_path()`, `community_detection()`. Max 500 analytics nodes, top_k=10. Hot Edges LMDB cache provides O(1) lookup for high-degree nodes.

Reference: `duckpgqgraph_api.md`, `duckpgqgraph_storage_trinity_gap_fill.md`

## IOC Extraction (3-Tier Fallback)

1. **Rust zero-copy** via PyList::append (fastest)
2. **Rayon/PyO3** via batch_ioc_extract_unified
3. **Pure Python** fallback (ioc_qs.extract_iocs_from_text)

IOC types: ipv4, ipv6, domain, md5, sha1, sha256, email, cve, url. Unknown types route to "pending".

Reference: `duckdb_shadow_store_extended.md`, `duckdb_shadow_store_sprint_facts.md`

## Query Cache (L1/L2)

- **L1**: 500-entry in-memory LRU, 300s TTL
- **L2**: 5000-entry LMDB (16MB map), 300s TTL
- Opt-in via `HLEDAC_DUCKDB_QUERY_CACHE`

Reference: `query_cache_ioc_extraction.md`, `duckdb_shadow_store_extended.md`

## Remote Parquet Support

DuckDB 1.5+ ATTACH for S3, HTTPS, Azure, GCS, Postgres. `RemoteParquetSource` provides zero-copy Arrow batches via `iter_batches()`. `ParquetHistoryReader` handles 100GB+ with 100k rows/batch.

Reference: `duckdb_remote_parquet_support.md`

## Configuration & Performance

- **Memory**: 1GB default (2GB write, 1GB hard limit)
- **Threads**: 2 (optimal for M1 thread-local connection bottleneck)
- **Chunk**: 1024 size, concurrency 4 (updated from 500/2)
- **Arrow ingest**: ON by default, 1.5-2× faster than executemany

Reference: `duckdb_thread_count_and_settings.md`, `duckdb_shadow_store_sprint_facts.md`

## Security & Async Patterns

- **SEC-02**: DuckDB files 0o600 permissions
- **S-07**: All DuckDB ops via `asyncio.to_thread()`
- **S1-06**: Bounded queue (16 items, 5s timeout)
- **E-33**: URI whitelist: s3, https, az, gs, postgres only
- **SQL injection prevention**: Forbidden patterns in sql_where

Reference: `duckdb_shadow_store_architecture.md`, `duckdb_shadow_store_extended_architecture.md`

## WALManager

LMDB-based WAL with 3 namespaces: finding, pending_duckdb_sync, deadletter_ingest. Compaction at 1-hour interval or 5000 writes. MAX_PENDING_SYNC_MARKERS=10000.

Reference: `walmanager.md`

## Write Serialization (ISSUE-032)

Shared executor pool (max_workers=4) serializes DuckDB writes. WAL parallel, DuckDB sequential within pool. `_DuckDBAsyncWriter` class prepared for future dedicated writer thread.

Reference: `issue_032_write_serialization_fix.md`