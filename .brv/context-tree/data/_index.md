---
children_hash: 67d3485ef42c542e7c4579dcff16b35f8c5ba6ed506b242984888640975f5df3
compression_ratio: 0.5490981963927856
condensation_order: 2
covers: [duckdb_store/_index.md]
covers_token_total: 998
summary_level: d2
token_count: 548
type: summary
---
<think>
The user wants me to create a structural summary of the DuckDB store knowledge entries at the domain level. I've analyzed the child entries and created a comprehensive summary. Now I just need to output it.
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