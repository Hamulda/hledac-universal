---
confidence: 0.95
sources: [facts/project/_index.md, duckdb_store/_index.md, memory/resource_governor/_index.md, testing/conftest/_index.md, hledac_universal/_index.md]
synthesized_at: '2026-07-18T00:18:19.611Z'
type: synthesis
title: M1 8GB Unified Memory as System-Wide Constraint
summary: Hardware limit of 8GB unified memory shapes architecture across storage, concurrency, ML inference, and testing.
tags: [hardware, memory, performance, m1, optimization]
related: []
keywords: [m1-8gb, uma, unified-memory, oom, ram-budget, metal-cache, swap, concurrency-presets]
createdAt: '2026-07-18T00:18:19.611Z'
updatedAt: '2026-07-18T00:18:19.611Z'
---

# M1 8GB Unified Memory as System-Wide Constraint

Every performance-sensitive subsystem has explicit M1 8GB tuning: DuckDB 600MB cap, 2 threads, session-scoped loops, MLX Metal cache ceiling, and concurrency presets. This isn't optional—OOM kills inference pipelines.

## Evidence

- **facts/project**: M1 8GB Apple Silicon with ~6.25GB usable budget, swap_detected at 3.8 GiB
- **duckdb_store**: DuckDB 600MB limit, 2 threads (M1 optimal), 1GB temp, chunk size 500
- **memory/resource_governor**: 5-state hysteresis: NORMAL→ELEVATED→CRITICAL→EMERGENCY→CIRED with concurrency presets (0-5 workers)
- **testing/conftest**: Session-scoped event loop eliminates loop recreation overhead on M1 8GB
- **hledac_universal**: M1 Metal cache hard limit 1.5 GiB, kv_bits=4, max_kv_size=8192 for MLX
