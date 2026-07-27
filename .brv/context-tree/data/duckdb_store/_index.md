---
children_hash: 8e303730915be1561dae60a83ac687be4347817df24bc4d47fa2f5e1cf87479c
compression_ratio: 0.11822732626619552
condensation_order: 1
covers: [context.md, duckdb-as-canonical-storage-with-tiered-memory-budget.md, duckdb_remote_parquet_support.md, duckdb_shadow_store.md, duckdb_shadow_store_deep_dive.md, duckdb_shadow_store_extended.md, duckdb_shadow_store_sprint_facts.md, duckdb_thread_count_and_settings.md, duckpgqgraph-integration-gap.md, duckpgqgraph_api.md, duckpgqgraph_storage_trinity_gap_fill.md, fallback-chains-for-graceful-degradation.md, issue_032_write_serialization_fix.md, query_cache_ioc_extraction.md, walmanager.md, zero-copy-and-batching-are-universal-optimizations.md]
covers_token_total: 6792
summary_level: d1
token_count: 803
type: summary
---
<think>
The user wants me to create a structural summary of the DuckDB store knowledge entries at the domain level (d1). Let me analyze the child entries and create a coherent summary.

Key themes from the entries:
1. DuckDB serves as canonical storage with tiered memory budget (600MB-2GB)
2. 3-tier facts hierarchy: Sprint Facts → Shadow Findings → Cross-Sprint
3. IOC extraction with 3-tier fallback chain
4. Query cache L1/L2 architecture
5. DuckPGQGraph as analytics donor layer (F272 replacement for ioc_graph)
6. Storage trinity: DuckDB + LMDB + LanceDB + DuckPGQGraph
7. WALManager for crash recovery
8. Remote Parquet support (DuckDB 1.5+)
9. Write serialization via shared executor pool
10. Zero-copy and batching as universal optimizations

Let me create a concise structural summary.
</think>

# duckdb_store

DuckDB serves as the canonical analytics store with tiered facts hierarchy and IOC extraction pipeline.

## Architecture

**3-Tier Facts Hierarchy:**
- TIER1 Sprint Facts: `sprint_delta`, `sprint_scorecard`, `source_hit_log`
- TIER2 Shadow Findings: `canonical_findings`, `shadow_runs`
- TIER3 Cross-Sprint: `temporal_events` (append-only)

**Storage Trinity:** DuckDB + LMDB + LanceDB + DuckPGQGraph

**3 Graph Attachment Slots:** `_ioc_graph` (analytics), `_stix_graph` (STIX synthesis), `_truth_write_graph` (ACTIVE buffered writes)

## Key Components

- **DuckDBShadowStore** (`knowledge/duckdb_store.py`): Primary write authority for sprint facts
- **DuckPGQGraph** (`knowledge/graph_service.py`): Analytics donor layer (F272 replacement for ioc_graph) — owns path queries, PageRank, shortest_path, community_detection; LanceDB reranking
- **WALManager** (`knowledge/wal.py`): LMDB-based write-ahead logging with 3 namespaces (finding, pending_duckdb_sync, deadletter_ingest), bounded at 10000 pending markers
- **ParquetHistoryReader**: Remote Parquet support (DuckDB 1.5+) for 100GB+ lazy reads with zero-copy Arrow batches

## Performance Configuration (M1 8GB)

- Memory limit: 600MB default, 2GB max
- Threads: 2 (optimal for thread-local connection bottleneck)
- Chunk config: 1024/4 (updated from 500/2)
- Arrow ingest: ON by default (1.5-2× faster than executemany)
- IOC chunk size: 128

## IOC Extraction (3-Tier Fallback)

1. `batch_ioc_extract_unified_python` — zero-copy via PyList::append
2. `batch_ioc_extract_unified` — Rayon/PyO3 vector return
3. Pure Python fallback

## Query Cache (F320-2)

- L1: 500-entry in-memory LRU, 300s TTL
- L2: 5000-entry LMDB, 300s TTL, 16MB map
- Default: OFF (opt-in via HLEDAC_DUCKDB_QUERY_CACHE)

## Write Serialization (ISSUE-032)

- Shared executor pool: `max_workers=4`
- WAL parallel writes → DuckDB sequential writes
- QualityAssessmentState: lazy loader fix for TYPE_CHECKING import issue

## Related Entries

- `duckdb_shadow_store.md` — core architecture
- `duckpgqgraph_api.md` — graph analytics layer
- `duckdb_shadow_store_deep_dive.md` — configuration and patterns
- `duckdb_remote_parquet_support.md` — remote parquet capabilities
- `walmanager.md` — crash recovery
- `fallback-chains-for-graceful-degradation.md` — degradation patterns
- `zero-copy-and-batching-are-universal-optimizations.md` — optimization philosophy