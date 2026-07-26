---
title: DuckPGQGraph Storage Trinity Gap Fill
summary: 'DuckPGQGraph = analytics donor (PGQL path queries, PageRank, shortest_path), NOT separate persistence. Operates on DuckDB/Kuzu data. Storage Trinity: DuckDB + LMDB + LanceDB + DuckPGQGraph.'
tags: []
related: [data/duckdb_store/duckpgqgraph_api.md, data/duckdb_store/duckdb_shadow_store.md]
keywords: []
createdAt: '2026-07-26T11:44:25.258Z'
updatedAt: '2026-07-26T11:44:25.258Z'
---
## Reason
Gap fill document clarifying DuckPGQGraph role in storage trinity and GraphService API

## Raw Concept
**Task:**
Clarify DuckPGQGraph position in storage trinity and document GraphService API

**Changes:**
- Clarified DuckPGQGraph is analytics donor, not separate persistence
- Added 4-component storage trinity (DuckDB + LMDB + LanceDB + DuckPGQGraph)
- Documented GraphService singleton pattern and module-level facade
- Added analytics methods: pagerank, shortest_path, community_detection
- Documented async pattern BUG-5 fix using get_running_loop + create_task

**Files:**
- knowledge/graph_service.py

**Flow:**
DuckDB canonical data -> DuckPGQGraph PGQL analytics -> results back to DuckDB

**Timestamp:** 2026-07-26

**Author:** context-engine

## Narrative
### Structure
Gap fill document updating existing duckpgqgraph_api.md with storage trinity context

### Dependencies
Depends on DuckDB canonical data, Kuzu truth store, LanceDB embeddings

### Highlights
MAX_GRAPH_ANALYTICS_NODES=500, MAX_GRAPH_ANALYTICS_TOP_K=10. DuckPGQGraph uses module-level _DUCKPGQ_GRAPH singleton accessed via _get_graph(). GraphService has __slots__ = ("_seen_iocs", "_seen_rels", "_relationship_callbacks"). _RUST_IOC_DEDUP_AVAILABLE gates IocSet usage.

### Rules
Rule 1: DuckPGQGraph is NOT a persistence layer — operates on DuckDB/Kuzu via PGQL
Rule 2: DuckDB shutdown must precede graph shutdown (ISSUE-5.1)
Rule 3: Tests patch graph_service._get_graph for uniform mocking

### Examples
Sprint features: F214Q (IOC type validation), F320 (pending type routing), F202B (identity edge), F206G (bounded analytics), ISSUE #14 (PageRank/shortest_path/community_detection), BUG-5 FIX (async pattern)
