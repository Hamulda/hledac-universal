---
children_hash: 1bce745cd6361daa17b6991485afc31d695310e808ee210dd92902a9d1259bb4
compression_ratio: 0.9533527696793003
condensation_order: 2
covers: [duckdb_store/_index.md]
covers_token_total: 1372
summary_level: d2
token_count: 1308
type: summary
---
<think>
The user wants me to create a structural summary of the duckdb_store knowledge entries in the context tree. I need to analyze the entries and create a well-organized summary that captures key facts, architectural decisions, and relationships while referencing entry names for drill-down.

Let me analyze the duckdb_store entries from the context tree structure provided:

1. **DuckDB Shadow Store Architecture** - 3-tier facts hierarchy, IOC extraction, query cache
2. **DuckDB Dual Role** - SQL store + graph analytics donor (DuckPGQGraph)
3. **Storage Trinity** - DuckDB + LMDB + LanceDB + DuckPGQGraph
4. **IOC Extraction** - 3-tier fallback chain
5. **Query Cache** - L1/L2 tiered caching
6. **Remote Parquet Support** - DuckDB 1.5+ capabilities
7. **DuckPGQGraph API** - Graph analytics layer
8. **WALManager** - Write-ahead logging for crash recovery
9. **Write Serialization** - ISSUE-032 fix
10. **Configuration** - Thread count, memory limits, M1 8GB settings

Now I need to write a summary that:
- Is well-organized and coherent
- Captures key facts and architectural decisions
- References entry names for drill-down
- Is condensed but preserves important information

Let me structure this:

1. Opening - DuckDB Shadow Analytics Store
2. Architecture Overview
3. 3-Tier Facts Hierarchy
4. IOC Extraction Pipeline
5. Query Cache
6. DuckPGQGraph
7. Remote Parquet Support
8. WALManager
9. Configuration
10. Write Serialization
11. Key Relationships

Let me write this now.
</think>

# duckdb_store: DuckDB Shadow Analytics Store

## Architecture Overview

DuckDB serves as the canonical storage authority with dual roles: SQL store and graph analytics donor (DuckPGQGraph). The storage trinity comprises **DuckDB + LMDB + LanceDB + DuckPGQGraph** (gap fill from `duckpgqgraph_storage_trinity_gap_fill`).

**Key entry:** `duckdb-as-canonical-storage-with-tiered-memory-budget`

---

## 3-Tier Facts Hierarchy

| Tier | Content | Durability |
|------|---------|------------|
| TIER1 | sprint_delta, sprint_scorecard, source_hit_log | Sprint Facts (durable) |
| TIER2 | canonical_findings, shadow_runs | Shadow Findings (durable) |
| TIER3 | temporal_events | Cross-Sprint (append-only) |

**Entries:** `duckdb_shadow_store`, `duckdb_shadow_store_extended`, `duckdb_shadow_store_sprint_facts`

---

## IOC Extraction Pipeline

3-tier fallback chain (`fallback-chains-for-graceful-degradation`):
1. **Python zero-copy** — `batch_ioc_extract_unified_python` via PyList::append
2. **Rayon/PyO3** — `batch_ioc_extract_unified` with auto-convert
3. **Pure Python** — `ioc_qs.extract_iocs_from_text` fallback

IOC types: `ipv4|ipv6|domain|md5|sha1|sha256|email|cve|url|filename|registry_key|pending`

**Entries:** `duckdb_shadow_store_extended`, `query_cache_ioc_extraction`

---

## Query Cache (F320-2)

Two-tier L1/L2 cache with LMDB persistence:

| Tier | Type | Capacity | TTL |
|------|------|----------|-----|
| L1 | In-memory LRU | 500 entries | 300s |
| L2 | LMDB | 5000 entries / 16MB | 300s |

Opt-in via `HLEDAC_DUCKDB_QUERY_CACHE=1` (default OFF).

**Entry:** `duckdb_thread_count_and_settings`, `query_cache_ioc_extraction`

---

## DuckPGQGraph (F272)

DuckPGQGraph is the **analytics donor** — not a persistence layer. IOC graph table removed from DuckDB; storage now via DuckPGQGraph.

**Graph slots:**
- `_ioc_graph` — IOC analytics
- `_stix_graph` — STIX synthesis
- `_truth_write_graph` — ACTIVE buffered writes

**Analytics methods:** path_queries, PageRank, shortest_path, community_detection

**Entry:** `duckpgqgraph_api`, `duckpgqgraph_storage_trinity_gap_fill`

---

## Remote Parquet Support

DuckDB 1.5+ supports S3/HTTPS/Azure/GCS/Postgres via `ATTACH`. `ParquetHistoryReader` provides:
- Filter pushdown with `to_polars_lazy()`
- Zero-copy Arrow RecordBatches (100k rows/batch)
- Async batch iteration via thread pool

**Entry:** `duckdb_remote_parquet_support`, `duckdb_shadow_store_deep_dive`

---

## WALManager (Crash Recovery)

LMDB-based write-ahead logging with 3 namespaces:
- `finding` — truth records
- `pending_duckdb_sync` — recovery markers (bounded at 10000)
- `deadletter_ingest` — failed writes

Compaction triggers: 1-hour interval OR 5000 writes.

**Entry:** `walmanager`

---

## Configuration (M1 8GB Safe)

| Setting | Read | Write |
|---------|------|-------|
| Threads | 2 | 2 |
| Memory Limit | 1GB | 2GB |
| Hard Memory Limit | 1GB | 1GB |
| Chunk Size | 1024 | 1024 |
| Pipeline Max | 4 | 4 |

**Default memory:** 600MB (Phase4 reduction from 2GB). Arrow ingest 1.5-2× faster than executemany (break-even at N=5-10).

**Entries:** `duckdb_shadow_store_deep_dive`, `duckdb_thread_count_and_settings`, `duckdb_shadow_store_extended`

---

## Write Serialization (ISSUE-032)

Shared executor pool (max_workers=4) for DuckDB write serialization. WAL parallel, DuckDB sequential. `QualityAssessmentState` lazy loader fix for TYPE_CHECKING import issue.

**Entry:** `issue_032_write_serialization_fix`

---

## Key Relationships

- IOC extraction → DuckDB insert → Parquet export
- DuckDB canonical data → DuckPGQGraph PGQL analytics → results
- WALManager → DuckDB crash recovery
- GraphService singleton wraps DuckPGQGraph; module-level `_get_graph()` patchable for tests