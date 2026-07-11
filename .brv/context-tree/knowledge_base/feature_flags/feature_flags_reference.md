---
title: Feature Flags Reference
summary: 'Complete feature flag reference: 50+ flags with defaults, DuckDB in-process mode, LanceDB IVF-PQ quantization, HTTP/3 dual strategy'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T15:07:29.396Z'
updatedAt: '2026-07-11T15:07:29.396Z'
---
## Reason
Document all feature flags with their defaults and purposes

## Raw Concept
**Task:**
Document all feature flags

**Timestamp:** 2026-07-11

## Narrative
### Structure
Feature flags organized by category: network protocols, intelligence sources, ML/LLM, storage, RAG, stealth

### Highlights
All feature flags default to 0 except HLEDAC_DUCKDB_INPROCESS=1, HLEDAC_DUCKDB_THREADS=2, HLEDAC_ARROW_INGEST=1. Optional extras: mlx-embed, http3

## Facts
- **duckdb_mode**: HLEDAC_DUCKDB_INPROCESS default ON - saves ~200MB RAM [project]
- **duckdb_threads**: HLEDAC_DUCKDB_THREADS default 2 - optimal for thread-local conn bottleneck [project]
- **lancedb_partitions**: HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS default 64 [project]
- **lancedb_subvectors**: HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS default 12 [project]
- **arrow_ingest**: HLEDAC_ARROW_INGEST default 1 - Arrow zero-copy ingest for DuckDB [project]
- **http3**: HLEDAC_ENABLE_HTTPX_H3 uses curl_cffi HttpVersion.v3 + aioquic for real QUIC [project]
