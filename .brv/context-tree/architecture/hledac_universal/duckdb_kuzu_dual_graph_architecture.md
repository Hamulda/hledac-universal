---
title: DuckDB Kuzu Dual-Graph Architecture
summary: IOCGraph (KuzuDB) is truth store, DuckPGQGraph is analytics donor, GraphService is the seam
tags: []
related: [architecture/hledac_universal/sidecar_protocol_registry.md, data/duckdb_store/duckpgqgraph_api.md]
keywords: []
createdAt: '2026-07-26T11:18:46.777Z'
updatedAt: '2026-07-26T11:18:46.777Z'
---
## Reason
Document dual-graph architecture with truth store and analytics donor

## Raw Concept
**Task:**
Document dual-graph architecture separating truth storage from analytics

**Changes:**
- F272: DuckDB ioc_graph table removed, IOC storage via DuckPGQGraph
- Unified 3 independent locking strategies into one
- DuckPGQGraph now handles buffer_ioc(), flush_buffers(), export_stix_bundle()

**Flow:**
Truth store (Kuzu) -> GraphService seam -> Analytics donor (DuckPGQGraph)

## Narrative
### Structure
Dual-graph model: IOCGraph (KuzuDB) as authoritative truth store, DuckPGQGraph (DuckDB) as analytics donor. GraphService acts as sprint memory layer between them.

### Dependencies
KuzuDB for truth, DuckDB for analytics, LanceDB for vector search, LMDB for hot-edges cache

### Highlights
DuckPGQGraph capabilities since F272: buffer_ioc(), flush_buffers(), export_stix_bundle(), graph_supports_buffered_writes=True
