---
children_hash: 63e31a5506470e37349bcab94ca1ca6b5fa8f687b6ffb3a15201567ae9268afb
compression_ratio: 0.20791494388659185
condensation_order: 2
covers: [context.md, duckdb_store/_index.md, testing/_index.md]
covers_token_total: 1693
summary_level: d2
token_count: 352
type: summary
---
<think>
The user wants me to output only the summary text for the DuckDB store domain. They've indicated this is a final iteration and I should review what I've learned and provide my answer now.

Looking at the context, this appears to be a system generating or processing summaries of DuckDB store documentation. The final output should be a condensed summary of DuckDB as the canonical analytics store.

Let me provide the condensed summary as requested.
</think>

# data/duckdb_store

DuckDB serves as the canonical analytics store with a shadow architecture supporting tiered facts, IOC extraction, and graph analytics.

**Storage Trinity:** DuckDB (SQL/facts) + LMDB (WAL/cache) + LanceDB (vector embeddings) + DuckPGQGraph (graph analytics)

**Key Capabilities:**

- 3-tier facts hierarchy: TIER1 (sprint facts), TIER2 (shadow findings), TIER3 (temporal events)
- DuckPGQGraph replaces ioc_graph table with graph algorithms (pagerank, shortest_path, community_detection)
- IOC extraction via 3-tier fallback: Rust zero-copy → Rayon/PyO3 → Pure Python
- Query cache L1/L2: 500-entry LRU + 5000-entry LMDB (16MB)
- Remote Parquet: S3, HTTPS, Azure, GCS, Postgres via DuckDB 1.5+ ATTACH
- WALManager: LMDB-based with 3 namespaces and compaction triggers
- Write serialization: Shared executor pool (max_workers=4)

**Configuration:** 1GB memory default, 2 threads, 1024 chunk size, Arrow ingest enabled