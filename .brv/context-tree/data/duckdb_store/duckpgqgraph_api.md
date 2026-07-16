---
title: DuckPGQGraph API
summary: 'DuckPGQGraph IOC Storage API: graph analytics layer using DuckDB, supports IOC/relation upsert, path queries, LanceDB reranking, and hot-edges LMDB cache'
tags: []
related: [data/duckdb_store/duckdb_shadow_store.md]
keywords: []
createdAt: '2026-07-16T11:05:22.278Z'
updatedAt: '2026-07-16T11:05:22.278Z'
---
## Reason
Documenting DuckPGQGraph IOC Storage API architecture and methods

## Raw Concept
**Task:**
Document DuckPGQGraph API - IOC Storage and Graph Analytics

**Files:**
- knowledge/graph_service.py

**Flow:**
upsert_ioc -> DuckDB insert -> (optional) LanceDB embedding upsert; find_connected -> DuckPGQ recursive CTE -> LanceDB rerank

**Timestamp:** 2026-07-16

**Patterns:**
- `^(ipv4|ipv6|domain|md5|sha1|sha256|email|cve|url|filename|registry_key|pending)$` - Valid IOC type values

## Narrative
### Structure
DuckPGQGraph is the analytics donor layer (F226). IOCGraph (Kuzu) is truth store. DuckPGQGraph owns path queries, graph analytics, and LanceDB reranking. GraphService wraps module-level DuckPGQGraph singleton.

### Dependencies
DuckDB for graph storage, LanceDB for vector embeddings, MLXEmbeddingManager for M1 8GB-safe embeddings, LMDB hot_edges_cache for O(1) read path

### Highlights
MAX_GRAPH_ANALYTICS_NODES=500, MAX_GRAPH_ANALYTICS_TOP_K=10; BUG-5 fix: get_running_loop() + create_task() for async; F265-U6 hot-edges cache on upsert_relation; unknown IOC types become "pending" (F320)

### Rules
Rule 1: upsert_ioc is idempotent - skip if seen in sprint session
Rule 2: Returns True only if newly upserted, False if existed or error
Rule 3: Unknown IOC types map to "pending" for pattern discovery
Rule 4: GraphService instances own only instance state (_seen_iocs, _seen_rels)
Rule 5: Module-level _get_graph() is patchable for tests

### Examples
upsert_ioc("1.2.3.4", "ipv4", 0.8, "source"); upsert_relation("ip1", "domain1", "resolves_to", 0.9, "dns_log"); find_connected_batch(["ip1", "ip2"])
