---
title: DuckDB Thread Count and Settings
summary: 'DuckDB thread count: settings.py (2) overrides duckdb_store.py (4), DuckDBSettings with in_process/arrow ingest/memory limits, 3-tier facts hierarchy, query cache F320-2'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T19:02:39.623Z'
updatedAt: '2026-07-11T19:02:39.623Z'
---
## Reason
Documenting DuckDB thread count conflict resolution and DuckDBSettings configuration

## Raw Concept
**Task:**
Document DuckDB thread count conflict resolution and DuckDBSettings configuration

**Changes:**
- DuckDB thread count conflict resolved: settings.py (2) overrides duckdb_store.py (4)
- DuckDBSettings with in_process, threads, arrow_ingest, memory limits
- DuckDBShadowStore 3-tier facts hierarchy documented
- DuckDB query cache F320-2 documented

**Files:**
- config/settings.py
- knowledge/duckdb_store.py

**Flow:**
config/settings.py -> DuckDBSettings.from_env() -> orchestrator uses 2 threads

## Narrative
### Structure
DuckDBSettings in config/settings.py with DuckDBShadowStore in knowledge/duckdb_store.py

### Dependencies
HLEDAC_DUCKDB_THREADS, HLEDAC_DUCKDB_INPROCESS, HLEDAC_ARROW_INGEST, HLEDAC_DUCKDB_MEMORY env vars

### Highlights
Thread count conflict: duckdb_store.py default=4 overridden by settings.py value=2. Sprint F275 confirmed 2 is optimal for thread-local connection bottleneck. Arrow ingest zero-copy is 1.5-2× faster on M1 8GB.

### Rules
Rule 1: threads capped at 4 for M1
Rule 2: memory_limit_gib capped at 4.0 for M1
Rule 3: DuckDBShadowStore sprint facts forwarded from EvidenceLog.append()

### Examples
DuckDBSettings.from_env() returns threads=2, in_process=True, arrow_ingest=True, memory_limit_gib=2.0

## Facts
- **duckdb_thread_count_conflict**: DuckDB thread count conflict: duckdb_store.py default=4 vs settings.py threads=2 [project]
- **duckdb_threads_default**: DuckDBSettings.threads default is 2 (optimal for M1 thread-local connection bottleneck) [project]
- **duckdb_in_process_default**: DuckDB in_process defaults to True (saves ~200MB RAM) [project]
- **duckdb_arrow_ingest_default**: DuckDB arrow_ingest defaults to True (zero-copy, 1.5-2× faster than executemany) [project]
- **duckdb_memory_defaults**: DuckDB memory_limit_gib default is 2.0, ceiling is 4.0 (for M1 8GB) [project]
- **duckdb_arrow_min_batch**: DuckDB arrow_ingest break-even vs executemany at N=5-10 rows [project]
- **duckdb_query_cache**: DuckDB query cache is two-tier: L1 LRU (500 entries, 300s) + L2 LMDB (5000 entries, 300s, 16MB) [project]
- **duckdb_facts_hierarchy**: DuckDBShadowStore has 3-tier facts hierarchy: Sprint Facts, Shadow Findings, Cross-Sprint [project]
