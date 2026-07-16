---
title: WALManager
summary: WALManager owns LMDB WAL lifecycle with 3 namespaces (finding, pending_duckdb_sync, deadletter_ingest), bounded pending markers at 10000, and compaction triggers at 1-hour or 5000 writes
tags: []
related: [data/duckdb_store/duckdb_shadow_store.md]
keywords: []
createdAt: '2026-07-16T11:07:11.646Z'
updatedAt: '2026-07-16T11:07:11.646Z'
---
## Reason
Document WALManager write-ahead logging for DuckDB crash recovery

## Raw Concept
**Task:**
Document WALManager write-ahead logging module for DuckDBShadowStore crash recovery

**Changes:**
- Added WALManager class for LMDB-based WAL lifecycle management

**Files:**
- knowledge/wal.py

**Flow:**
finding write -> LMDB WAL -> DuckDB insert -> pending marker on failure -> recovery on restart

**Timestamp:** 2026-07-16

**Patterns:**
- `^finding:` - WAL truth record key prefix
- `^pending_duckdb_sync:` - Pending sync recovery marker prefix
- `^deadletter_ingest:` - Dead letter namespace prefix
- `^wal:finding:` - Unified store WAL namespace prefix (F272)

## Narrative
### Structure
WALManager in knowledge/wal.py manages LMDB-based write-ahead logging for DuckDBShadowStore

### Dependencies
Requires LMDBKVStore for LMDB operations, UnifiedLMDBStore for unified store mode

### Highlights
Three namespaces: finding (truth records), pending_duckdb_sync (recovery markers), deadletter_ingest (failed writes). MAX_PENDING_SYNC_MARKERS=10000 with automatic eviction. Compaction triggers: 1-hour interval OR 5000 writes. E4 cleanup: weakref.finalize + atexit fallback.

### Rules
Rule 1: pending_duckdb_sync marker written ONLY when LMDB succeeded but DuckDB failed
Rule 2: Oldest pending markers evicted when at or above MAX_PENDING_SYNC_MARKERS bound
Rule 3: wal:finding namespace prefix used when HLEDAC_WAL_UNIFIED=1

### Examples
Env vars: HLEDAC_WAL_UNIFIED=1, HLEDAC_WAL_COMPACT_INTERVAL_S=3600, HLEDAC_WAL_COMPACT_WRITE_THRESHOLD=5000
