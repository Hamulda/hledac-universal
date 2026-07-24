---
children_hash: 95763771535711d39e11d55cc0e794841af3436c960a06df7b71ca13964cb14d
compression_ratio: 0.8525852585258525
condensation_order: 2
covers: [duckdb_store/_index.md]
covers_token_total: 909
summary_level: d2
token_count: 775
type: summary
---
<think>
The user wants me to create a structural summary of the duckdb_store topic from the provided knowledge entries. I need to condense the information while preserving key facts, relationships, and architectural decisions, referencing entry names for drill-down.

Let me organize this:

1. **Overview** - DuckDB as canonical storage, shadow analytics store
2. **Core Architecture** - 3-tier facts hierarchy, IOC extraction, query cache
3. **Key Components** - DuckPGQGraph, WALManager, DuckDBSettings
4. **Performance Optimizations** - Arrow ingest, chunk tuning, thread settings
5. **Integration Points** - Storage trinity, resource governor
6. **Issues Fixed** - ISSUE-032, write serialization

Let me create a clean, structured summary.
</think>

# duckdb_store

DuckDB serves as the canonical analytics store with shadow architecture for sprint facts and IOC extraction.

## Architecture

- **3-Tier Facts Hierarchy**: Sprint Facts (sprint_delta, sprint_scorecard, source_hit_log) → Shadow Findings (canonical_findings, shadow_runs) → Cross-Sprint (temporal_events)
- **Storage Trinity**: DuckDB → LMDB → LanceDB (see duckpgqgraph-integration-gap.md for gap in documentation)
- **Write Path**: EvidenceLog.append() → canonical_findings → shadow_runs → temporal_events

## Core Components

- **duckdb_shadow_store**: Main DuckDB shadow store with IOC extraction fallback chain and query cache
- **duckdb_shadow_store_extended**: Extended documentation with Arrow ingest, ParquetHistoryReader, and zero-copy Python path
- **duckpgqgraph_api**: Graph analytics layer (F272 replacement for ioc_graph) - owns path queries, graph analytics, LanceDB reranking
- **walmanager**: LMDB-based write-ahead logging with 3 namespaces (finding, pending_duckdb_sync, deadletter_ingest), bounded pending markers at 10000, compaction triggers at 1-hour or 5000 writes
- **duckdb_thread_count_and_settings**: Thread count conflict resolution (settings.py 2 overrides duckdb_store.py 4), DuckDBSettings with env var configuration

## Query Cache (F320-2)

Two-tier L1/L2 cache: L1 in-memory LRU (500 entries, 300s TTL) + L2 LMDB (5000 entries, 300s TTL, 16MB map). Opt-in via HLEDAC_DUCKDB_QUERY_CACHE=1 (default OFF).

## IOC Extraction

3-tier fallback chain: batch_ioc_extract_unified_python (zero-copy via PyList::append) → batch_ioc_extract_unified (Rayon/PyO3) → pure Python fallback. IOC types: ipv4/ipv6/domain/md5/sha1/sha256/email/cve/url/filename/registry_key/pending. F272 removed DuckDB ioc_graph table; IOC storage now via DuckPGQGraph.

## Performance Optimizations

- **Arrow Ingest**: Zero-copy, 1.5-2× faster than executemany, break-even at N=5-10 rows. Default: ON
- **M1 8GB Settings**: DuckDB memory 600MB (Phase4 reduction from 2GB), 4 threads, temp storage 1GB
- **Chunk Tuning**: MAX_CHUNK_SIZE=500, MAX_CHUNK_CONCURRENCY=2 (P3 priority)

## Issues Fixed

- **issue_032_write_serialization_fix**: DuckDB write serialization via shared executor pool (max_workers=4), WAL parallel writes, QualityAssessmentState lazy loader fix, prepared _DuckDBAsyncWriter for future dedicated writer thread