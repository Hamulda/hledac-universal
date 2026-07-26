---
title: KB Audit Gap Status 2026-07-26
summary: '4 KB audit gaps addressed: conventions exist in CLAUDE.md, exit codes documented, integrations remain scattered, empty probe_tests directory flagged.'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T12:09:46.633Z'
updatedAt: '2026-07-26T12:09:46.633Z'
---
## Reason
Update status of 4 KB audit gap items from audit findings

## Raw Concept
**Task:**
Update KB audit gap status for 4 open items

**Changes:**
- Naming/Error-handling conventions: EXISTS in CLAUDE.md - snake_case, no bare except, asyncio.gather return_exceptions=True, mx.eval before clear_cache
- Integrations: NO dedicated docs/integrations/ directory - scattered across DuckDB/LMDB/LanceDB, MLX/Hermes3, curl_cffi/httpx/aioquic, Tor/I2P/SOCKS
- probe_tests: EMPTY directory exists at .brv/context-tree/testing/probe_tests/ - tests/probe_p_e2_feed_pipeline/ not documented
- Exit codes: ALREADY HAS CONTENT - tests/test_exit_codes.py (6 tests) + smoke_runner.py

**Timestamp:** 2026-07-26

## Narrative
### Structure
KB audit gap tracker with 4 items. Conventions documented in CLAUDE.md. Exit codes have 6 regression tests.

### Dependencies
Requires periodic review to consolidate integrations documentation.

### Highlights
Conventions: snake_case, no bare except, asyncio.gather with return_exceptions=True, no time.sleep in async, no asyncio.run in ThreadPoolExecutor, DuckDB writes via async_ingest_findings_batch, LMDB bulk via cursor.putmulti, RotatingBloomFilter for URL dedup, sidecars return [] on errors
