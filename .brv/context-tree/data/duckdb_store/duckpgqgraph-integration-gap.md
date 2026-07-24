---
confidence: 0.76
sources: [data/duckdb_store/_index.md, architecture/hledac_universal/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-24T21:05:20.865Z'
type: synthesis
title: DuckPGQGraph Integration Gap
summary: DuckPGQGraph API is documented in data/duckdb_store but not referenced by architecture's storage trinity or resource_governor's graph analytics optimization.
tags: [duckpgqgraph, graph-analytics, ioc-graph, documentation-gap]
related: []
keywords: [DuckPGQGraph, F272, graph-analytics, ioc_graph, Rust, LanceDB, documentation-gap]
createdAt: '2026-07-24T21:05:20.865Z'
updatedAt: '2026-07-24T21:05:20.865Z'
---

# DuckPGQGraph Integration Gap

duckpgqgraph_api.md in data/duckdb_store defines the graph analytics layer (F272 replacement for ioc_graph), but architecture/hledac_universal storage trinity description omits it, and memory/resource_governor P1 optimizations reference 'Rust graph analytics' without linking to the DuckPGQGraph API.

## Evidence

- **data/duckdb_store**: duckpgqgraph_api.md defines DuckPGQGraph as F272 replacement, owns path queries, graph analytics, LanceDB reranking
- **architecture/hledac_universal**: storage trinity lists DuckDB/LMDB/LanceDB but omits DuckPGQGraph as the graph analytics layer
- **memory/resource_governor**: P1 optimization references 'Rust graph analytics (10-100× speedup over Python igraph)' without cross-reference to DuckPGQGraph
