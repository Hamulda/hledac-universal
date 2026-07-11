---
children_hash: 26b41ccaa37bc6180ddc3951eb36bdf19030bae4c69e1f6b15e27bc71538fcad
compression_ratio: 0.4148314606741573
condensation_order: 1
covers: [context.md, duckdb_shadow_store.md, duckdb_shadow_store_extended.md, duckdb_thread_count_and_settings.md, query_cache_ioc_extraction.md]
covers_token_total: 2225
summary_level: d1
token_count: 923
type: summary
---
<think>The user wants me to create a structural summary at level d1 from the provided knowledge entries about duckdb_store. I need to:

1. Preserve key facts, architectural decisions, and relationships
2. Reference entry names for drill-down
3. Target ~20-30% compression
4. Output clean structured markdown without code blocks or XML tags
5. NOT use any tools

Let me synthesize the information from these entries:

**Key architectural elements:**
- 3-tier facts hierarchy: Sprint Facts → Shadow Findings → Cross-Sprint
- IOC extraction with 3-tier fallback chain
- Two-tier L1/L2 query cache with LMDB persistence
- DuckPGQGraph for IOC storage (F272)
- ParquetHistoryReader for 100GB+ lazy reads
- Arrow ingest for performance
- DuckDBSettings configuration with env vars

**Key facts to preserve:**
- DuckDB memory: 600MB default, 4 threads, 1GB temp for M1 8GB
- Thread count conflict: settings.py (2) overrides duckdb_store.py (4)
- Query cache: L1=500 entries, L2=5000 entries, 300s TTL
- Arrow ingest 1.5-2x faster than executemany
- IOC extraction 3 tiers: zero-copy Python → Rayon PyO3 → Pure Python
- Parquet reader: 100k rows/batch max

Let me create a concise summary.
</think>



# duckdb_store

DuckDB shadow analytics store with tiered facts hierarchy and IOC extraction pipeline.

## Architecture

**3-Tier Facts Hierarchy:**
- TIER1: Sprint Facts (sprint_delta, sprint_scorecard, source_hit_log)
- TIER2: Shadow Findings (canonical_findings, shadow_runs)
- TIER3: Cross-Sprint (temporal_events)

**Query Cache (F320-2):** Two-tier L1/L2 with LMDB persistence
- L1: 500-entry in-memory LRU, sub-millisecond hits
- L2: 5000-entry LMDB (16MB map), 300s TTL
- Invalidation on schema migration via `_invalidate_on_migration()`
- Opt-in via `HLEDAC_DUCKDB_QUERY_CACHE=1` (default OFF)

**IOC Extraction (F266-2.3):** 3-tier fallback chain
1. Zero-copy Python via `PyList::append` → `batch_ioc_extract_unified_python`
2. Rayon `Vec` return with PyO3 auto-convert → `batch_ioc_extract_unified`
3. Pure Python fallback → `ioc_qs.extract_iocs_from_text`

**IOC Storage (F272):** DuckDB `ioc_graph` table removed; storage now via DuckPGQGraph (`graph/quantum_pathfinder.py`)

**Ingest:** Arrow zero-copy (default ON) is 1.5-2× faster than executemany on M1 8GB; break-even at N=5-10 rows

**History Reads:** ParquetHistoryReader enables 100GB+ IOC history without OOM (100k rows/batch, zero-copy Arrow IPC→PyArrow→Polars)

## Configuration

| Setting | Default | Ceiling | Notes |
|---------|---------|---------|-------|
| threads | 2 | 4 | settings.py (2) overrides duckdb_store.py (4) |
| memory_limit_gib | 2.0 | 4.0 | M1 8GB optimal |
| in_process | True | — | Saves ~200MB RAM |
| arrow_ingest | True | — | Zero-copy, 1.5-2× faster |
| MAX_CHUNK_SIZE | 500 | — | With MAX_CHUNK_CONCURRENCY=2 |

## Data Flow

```
EvidenceLog.append() → sprint_delta/scorecard/hit_log → duckdb_store
                                          ↓
                                  canonical_findings → shadow_runs → temporal_events
                                          ↓
                              ioc_extract (3-tier fallback) → DuckPGQGraph
                                          ↓
                              query_cache (L1 → L2 → execute → Parquet)
```

## Drill-Down References

- **duckdb_shadow_store_extended.md** — Full facts hierarchy, IOC extraction tiers, query cache details
- **duckdb_thread_count_and_settings.md** — Thread count conflict resolution, DuckDBSettings env vars
- **query_cache_ioc_extraction.md** — Parquet row-group reader spec, IOC types (ipv4/ipv6/domain/md5/sha1/sha256/email/cve)
- **duckdb_shadow_store.md** — Core architecture, DuckPGQGraph IOC storage