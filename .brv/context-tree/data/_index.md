---
children_hash: aebbb508a3116e301879261491cd1eeb639b74ff852f530a1aace1deb3fd06d2
compression_ratio: 0.9254267744833783
condensation_order: 2
covers: [duckdb_store/_index.md]
covers_token_total: 1113
summary_level: d2
token_count: 1030
type: summary
---
<think>
The user wants me to create a structural summary of the DuckDB store knowledge entries based on what's in the context tree. I need to consolidate the key facts, architectural decisions, and relationships while referencing entry names for drill-down.

Let me identify the key components from the context tree entries:

1. **3-tier facts hierarchy**: Sprint Facts, Shadow Findings, Cross-Sprint
2. **IOC extraction**: 3-tier fallback chain with zero-copy Python path
3. **Query cache**: L1/L2 two-tier system (500/5000 entries, 300s TTL)
4. **DuckPGQGraph**: Graph analytics layer for IOC storage
5. **WALManager**: LMDB-based write-ahead logging
6. **DuckDB settings**: Thread count (2 optimal), memory (600MB-2GB), Arrow ingest
7. **ParquetHistoryReader**: For 100GB+ lazy reads
8. **DuckDB memory config for M1 8GB**: 600MB limit, 4 threads, 1GB temp

Key relationships:
- duckdb_shadow_store -> ioc_extract -> query_cache
- DuckPGQGraph uses DuckDB for storage, LanceDB for reranking
- WALManager for crash recovery

Let me create a concise structural summary.
</think>

# duckdb_store

DuckDB shadow analytics store with tiered facts hierarchy, IOC extraction pipeline, and query caching.

## Architecture

**3-Tier Facts Hierarchy** (see `duckdb_shadow_store_extended.md`):
- **TIER1**: Sprint Facts — `sprint_delta`, `sprint_scorecard`, `source_hit_log`
- **TIER2**: Shadow Findings — `canonical_findings`, `shadow_runs`
- **TIER3**: Cross-Sprint — `temporal_events` (append-only)

**Ingest Flow**: `EvidenceLog.append()` → `canonical_findings` → `shadow_runs` → `temporal_events`

## IOC Extraction

3-tier fallback chain (see `duckdb_shadow_store_extended.md`, `query_cache_ioc_extraction.md`):
1. **Tier 1**: `batch_ioc_extract_unified_python` — zero-copy via `PyList::append` (fastest)
2. **Tier 2**: `batch_ioc_extract_unified` — Rayon Vec return with PyO3 auto-convert
3. **Tier 3**: `ioc_qs.extract_iocs_from_text` — pure Python fallback

Valid IOC types: `ipv4|ipv6|domain|md5|sha1|sha256|email|cve|url|filename|registry_key|pending` (F320 unknown→pending)

## Query Cache

Two-tier L1/L2 system (see `duckdb_thread_count_and_settings.md`, `query_cache_ioc_extraction.md`):
- **L1**: In-memory LRU, 500 entries, 300s TTL (sub-millisecond hits)
- **L2**: LMDB persistent, 5000 entries, 300s TTL, 16MB map
- Opt-in via `HLEDAC_DUCKDB_QUERY_CACHE=1` (default OFF)

## DuckPGQGraph API

Graph analytics donor layer (F226) wrapping DuckDB (see `duckpgqgraph_api.md`):
- **upsert_ioc/upsert_relation**: Idempotent inserts with optional LanceDB embedding
- **find_connected_batch**: DuckPGQ recursive CTE + LanceDB reranking
- **Limits**: MAX_GRAPH_ANALYTICS_NODES=500, MAX_GRAPH_ANALYTICS_TOP_K=10
- **Hot edges cache**: LMDB O(1) read path (F265-U6)

## WALManager

LMDB-based write-ahead logging (see `walmanager.md`):
- **Namespaces**: `finding` (truth), `pending_duckdb_sync` (recovery), `deadletter_ingest` (failed writes)
- **Bounds**: MAX_PENDING_SYNC_MARKERS=10000 (auto-eviction)
- **Compaction**: 1-hour interval OR 5000 writes
- Env: `HLEDAC_WAL_UNIFIED=1` enables `wal:finding:` prefix (F272)

## DuckDB Configuration (M1 8GB)

| Setting | Value | Env Var |
|---------|-------|---------|
| Threads | 2 (optimal) | `HLEDAC_DUCKDB_THREADS` |
| Memory | 600MB limit, 2GB default | `HLEDAC_DUCKDB_MEMORY` |
| Temp | 1GB | — |
| In-process | True (saves ~200MB) | `HLEDAC_DUCKDB_INPROCESS` |
| Arrow ingest | True (1.5-2× faster) | `HLEDAC_ARROW_INGEST` |
| Chunk size | 500 | — |
| Chunk concurrency | 2 | — |

**Note**: Thread count conflict resolved — `settings.py` (2) overrides `duckdb_store.py` (4). Arrow ingest break-even at N=5-10 rows.

## Large History Reads

`ParquetHistoryReader` (see `duckdb_shadow_store_extended.md`):
- 100GB+ IOC history without OOM
- Zero-copy Arrow IPC → PyArrow → Polars
- 100k rows/batch max
- Columns: `id, query, source_type, confidence, ts, provenance_json`

## Key Files

- `knowledge/duckdb_store.py` — DuckDBShadowStore
- `knowledge/graph_service.py` — GraphService + DuckPGQGraph
- `knowledge/wal.py` — WALManager
- `config/settings.py` — DuckDBSettings