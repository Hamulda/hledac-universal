---
title: DuckDB Shadow Store
summary: DuckDB shadow store with 3-tier facts hierarchy, IOC extraction fallback chain, and query cache (L1/L2)
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:50:41.388Z'
updatedAt: '2026-07-11T14:50:41.388Z'
---
## Reason
Documenting DuckDB shadow analytics, tiered facts, and IOC extraction pipeline

## Raw Concept
**Task:**
Document DuckDB shadow analytics architecture

**Changes:**
- F320-2: Added DuckDB query cache (L1 in-memory, L2 LMDB)
- F266-2.3: IOC extraction with zero-copy path
- Established tiered facts hierarchy

**Flow:**
ingest -> tier_classify -> duckdb_store -> ioc_extract -> query_cache

## Narrative
### Structure
3-tier architecture: TIER1 Sprint Facts (sprint_delta, sprint_scorecard, source_hit_log), TIER2 Shadow Findings (canonical_findings, shadow_runs), TIER3 Cross-Sprint (temporal_events)

### Dependencies
DuckDB memory limit 600MB, max temp 1GB, optimal 2 threads

### Highlights
IOC extraction fallback chain: batch_ioc_extract_unified_python -> batch_ioc_extract_unified -> ioc_qs.extract_iocs_from_text. Query cache: L1 500 entries/300s, L2 5000 entries/16MB LMDB.
