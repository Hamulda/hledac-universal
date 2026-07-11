---
title: Critical Invariants
summary: '10 critical invariants for M1 8GB stability: async patterns, MLX cache, DuckDB/LMDB write paths, bloom filters, fail-safe'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T15:07:16.919Z'
updatedAt: '2026-07-11T15:07:16.919Z'
---
## Reason
Document critical M1 stability invariants

## Raw Concept
**Task:**
Document critical invariants for M1 stability

**Files:**
- runtime/sprint_scheduler.py
- knowledge/duckdb_store.py
- brain/inference_engine.py

**Flow:**
async gather -> _check_gathered() -> check for exceptions

**Timestamp:** 2026-07-11

## Narrative
### Structure
10 invariants organized by category: async patterns, MLX patterns, storage patterns, fail-safe patterns

### Highlights
Invariant 8: M1 Metal cache limit dynamic = min(max(available*0.2, 512MiB), 1GiB) ceiling on M1 8GB; wired limit 1.5 GiB

### Rules
INV-1: asyncio.gather ALWAYS with return_exceptions=True
INV-2: mx.eval([]) before mx.metal.clear_cache()
INV-3: No time.sleep() in async - use asyncio.sleep() or asyncio.to_thread()
INV-4: No asyncio.run() in ThreadPoolExecutor - use loop.run_until_complete()
INV-5: DuckDB ONLY via async_ingest_findings_batch()
INV-6: LMDB bulk ONLY via cursor.putmulti()
INV-7: URL dedup ONLY via RotatingBloomFilter
INV-8: M1 Metal cache: dynamic formula with 1GiB ceiling on 8GB M1
INV-9: Fail-safe: sidecary return [] on errors
INV-10: No bare except - always except Exception:
