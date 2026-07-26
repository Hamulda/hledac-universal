---
title: Integration Landscape Overview
summary: 'Integrations scattered across codebase: DuckDB/LMDB/LanceDB (storage), MLX/Hermes3 (brain), curl_cffi/httpx/aioquic (HTTP), proxies, Rust PyO3'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T12:10:53.724Z'
updatedAt: '2026-07-26T12:10:53.724Z'
---
## Reason
KB audit gap fix - document scattered integrations across codebase

## Raw Concept
**Task:**
Document integration landscape - integrations are scattered across codebase, no dedicated docs/integrations/

**Changes:**
- NO dedicated docs/integrations/ directory (gap identified)
- Integrations are scattered across codebase

**Flow:**
Integration -> Implementation scattered across core/, runtime/, storage/, brain/

**Timestamp:** 2026-07-26

**Author:** KB Audit 2026-07-26

## Narrative
### Structure
Integrations exist in: DuckDB/LMDB/LanceDB (storage trinity), MLX/Hermes3 (brain), curl_cffi/httpx/aioquic (HTTP), Tor/I2P/SOCKS proxies (transport), Rust PyO3 extensions

### Dependencies
NO dedicated docs/integrations/ directory - consolidation needed

### Highlights
Storage trinity: DuckDB (analytics/queries), LMDB (in-memory KV), LanceDB (vector/RAG). HTTP stack: curl_cffi (TLS), httpx (sync), aioquic (HTTP/3). MLX/Hermes3 for LLM brain. Rust PyO3 for hot-path extensions.

## Facts
- **integrations_directory**: No dedicated docs/integrations/ directory exists [project]
- **duckdb_role**: DuckDB used for analytics and query operations [project]
- **lmdb_role**: LMDB used for in-memory KV store [project]
- **lancedb_role**: LanceDB used for vector/RAG storage [project]
- **mlx_role**: MLX used for local LLM inference [project]
- **curl_cffi_role**: curl_cffi used for TLS-capable HTTP requests [project]
- **aioquic_role**: aioquic used for HTTP/3 protocol [project]
