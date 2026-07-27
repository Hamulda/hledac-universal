---
confidence: 0.88
sources: [facts/project/_index.md, hledac_universal/_index.md, memory/resource_governor/_index.md, testing/conftest/_index.md]
synthesized_at: '2026-07-18T00:18:19.624Z'
type: synthesis
title: 10 Critical Invariants Govern System Stability
summary: 'Unified rule system enforces safe patterns: asyncio.gather return_exceptions, MLX cache management, DuckDB write paths.'
tags: [invariants, stability, async, m1, ci]
related: [architecture/hledac_universal/critical_invariants.md, architecture/hledac_universal/critical-invariants-as-cross-domain-enforcement-mechanism.md]
keywords: [ghost-invariants, asyncio-gather, return-exceptions, mx-eval, duckdb-write, lmdb-bulk, fail-safe]
createdAt: '2026-07-18T00:18:19.624Z'
updatedAt: '2026-07-18T00:18:19.624Z'
---

# 10 Critical Invariants Govern System Stability

GHOST_INVARIANTS I1-I10 span async patterns, MLX lifecycle, DuckDB/LMDB writes, fail-safe returns, and concurrency rules. I6 (asyncio.gather+return_exceptions) and I4 (mx.eval([]) before clear_cache) are enforced in CI. Violations cause cascade failures on M1 8GB.

## Evidence

- **facts/project**: 10 critical invariants: no bare except, gather return_exceptions, mx.eval before cache clear, DuckDB via async_ingest, LMDB cursor.putmulti
- **hledac_universal**: 10 critical invariants for M1 8GB stability: async patterns, MLX cache, DuckDB/LMDB, bloom filters, fail-safe
- **memory/resource_governor**: GHOST_INVARIANTS I6, I4 explicitly called out in MPSC architecture
- **testing/conftest**: pytest-timeout 30s default, 10% benchmark threshold, asyncio_mode=auto
