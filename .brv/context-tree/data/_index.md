---
children_hash: c00a280c3a150678cdf8d6aa5eda953137a71e69113fbe52aa979641b6c86c69
compression_ratio: 0.8908098271155596
condensation_order: 2
covers: [duckdb_store/_index.md]
covers_token_total: 1099
summary_level: d2
token_count: 979
type: summary
---
<think>
The user wants me to create a structural summary at level d1 (domain level) from the duckdb_store topic. I need to distill the key facts, architectural decisions, and relationships while referencing entry names for drill-down.

Let me analyze the key points from all the duckdb_store entries:

1. **Architecture Overview**: DuckDB shadow analytics store with tiered facts hierarchy and IOC extraction pipeline

2. **3-Tier Facts Hierarchy**:
   - TIER1: Sprint Facts (sprint_delta, sprint_scorecard, source_hit_log)
   - TIER2: Shadow Findings (canonical_findings, shadow_runs)
   - TIER3: Cross-Sprint (temporal_events)

3. **IOC Extraction**: 3-tier fallback chain (Python zero-copy → Rayon/PyO3 → Pure Python), DuckPGQGraph for IOC storage (F272 replaced ioc_graph table)

4. **Query Cache**: L1 in-memory LRU (500 entries, 300s TTL) + L2 LMDB (5000 entries, 300s TTL, 16MB)

5. **Memory/Performance Settings**: M1 8GB optimized (600MB limit, 4 threads, Arrow ingest 1.5-2x faster)

6. **Write Serialization**: ISSUE-032 fix via shared executor pool (max_workers=4)

7. **WALManager**: LMDB-based WAL with 3 namespaces, compaction triggers

8. **Key Files**: duckdb_store.py, wal.py, graph_service.py, config/settings.py

Let me structure this into a clean summary.
</think>

# duckdb_store

## Overview
DuckDB shadow analytics store with tiered facts hierarchy and IOC extraction pipeline. Serves as the canonical facts authority in the storage trinity (DuckDB → LMDB → LanceDB).

## Architecture

### 3-Tier Facts Hierarchy
- **TIER1 - Sprint Facts**: sprint_delta, sprint_scorecard, source_hit_log
- **TIER2 - Shadow Findings**: canonical_findings, shadow_runs
- **TIER3 - Cross-Sprint**: temporal_events

### Write Pipeline
EvidenceLog.append() → canonical_findings → shadow_runs → temporal_events
All write paths funnel through async_ingest_findings_batch(). Write serialization via shared executor pool (max_workers=4) from ISSUE-032.

### IOC Extraction
3-tier fallback chain (duckdb_shadow_store_extended.md, query_cache_ioc_extraction.md):
1. Zero-copy Python via PyList::append
2. Rayon Vec return with PyO3 auto-convert
3. Pure Python fallback

IOC storage via DuckPGQGraph (F272 replaced DuckDB ioc_graph table). Valid IOC types: ipv4|ipv6|domain|md5|sha1|sha256|email|cve|url|filename|registry_key|pending.

### Query Cache (F320-2)
Two-tier L1/L2 cache (duckdb_thread_count_and_settings.md, query_cache_ioc_extraction.md):
- **L1**: In-memory LRU, 500 entries, 300s TTL
- **L2**: LMDB persistent, 5000 entries, 300s TTL, 16MB map

Default OFF (opt-in via HLEDAC_DUCKDB_QUERY_CACHE=1).

### WALManager (walmanager.md)
LMDB-based write-ahead logging with 3 namespaces: finding (truth records), pending_duckdb_sync (recovery markers), deadletter_ingest (failed writes). Compaction triggers: 1-hour interval OR 5000 writes.

### DuckPGQGraph API (duckpgqgraph_api.md)
Graph analytics layer for IOC storage. Owns path queries, graph analytics, and LanceDB reranking. MAX_GRAPH_ANALYTICS_NODES=500, MAX_GRAPH_ANALYTICS_TOP_K=10. Hot-edges LMDB cache for O(1) read path.

## Performance & Configuration

### M1 8GB Optimized Settings
- Memory limit: 600MB (down from 2GB)
- Threads: 4 (duckdb_store.py default), overridden to 2 by settings.py
- Arrow ingest: ON by default, 1.5-2× faster than executemany
- Chunk tuning: MAX_CHUNK_SIZE=500, MAX_CHUNK_CONCURRENCY=2

### DuckDBSettings
Threads capped at 4, memory_limit_gib capped at 4.0 for M1. Arrow ingest break-even at N=5-10 rows.

### ParquetHistoryReader
100GB+ IOC history reads without OOM, 100k rows per batch, zero-copy Arrow IPC→PyArrow→Polars.

## Related Entries
- duckpgqgraph_api.md - Graph analytics layer
- walmanager.md - WAL lifecycle management
- issue_032_write_serialization_fix.md - Write serialization fix
- query_cache_ioc_extraction.md - Cache and IOC tiers
- duckdb_thread_count_and_settings.md - Thread count and settings