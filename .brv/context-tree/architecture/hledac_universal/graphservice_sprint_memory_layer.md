---
title: GraphService Sprint Memory Layer
summary: GraphService is sprint memory layer facade backed by DuckPGQGraph with instance-isolated idempotency state
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:18:46.781Z'
updatedAt: '2026-07-26T11:18:46.781Z'
---
## Reason
Document GraphService facade for cross-sprint entity memory

## Raw Concept
**Task:**
Document GraphService as cross-sprint entity memory facade

**Changes:**
- F226: GraphService instances own only instance-isolated state (_seen_iocs, _seen_rels)
- DuckPGQGraph backend is module-level lazy singleton via _get_graph()
- Issue-5.1: shutdown_graph() properly closes DuckDB connections
- F272: DuckPGQGraph handles IOC buffer operations and STIX export

**Flow:**
upsert_ioc/rel -> idempotency check -> DuckPGQGraph backend

## Narrative
### Structure
GraphService class owns instance-isolated state (_seen_iocs, _seen_rels, _relationship_callbacks). DuckPGQGraph backend is module-level singleton. Module-level facade functions delegate to _DEFAULT_GRAPH_SERVICE.

### Dependencies
DuckPGQGraph singleton, LanceDB for entity storage, LMDB hot-edges cache

### Highlights
MAX_GRAPH_ANALYTICS_NODES=500, MAX_GRAPH_ANALYTICS_TOP_K=10. Unknown IOC types set to pending not rejected (F320). BUG-5: use asyncio.get_running_loop() not run_until_complete() for async fire-and-forget.
