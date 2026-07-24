---
confidence: 0.88
sources: [facts/project/_index.md, data/duckdb_store/_index.md, architecture/hledac_universal/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-24T21:05:20.861Z'
type: synthesis
title: M1 8GB Memory Budget Fragmentation
summary: M1 8GB unified memory budget is hardcoded differently across 4 domains without a canonical budget allocation document.
tags: [memory-budget, m1-8gb, fragmentation, duckdb, metal-cache]
related: []
keywords: [M1-8GB, memory-budget, DuckDB-limit, Metal-cache, unified-memory, budget-allocation, settings-conflict]
createdAt: '2026-07-24T21:05:20.861Z'
updatedAt: '2026-07-24T21:05:20.861Z'
---

# M1 8GB Memory Budget Fragmentation

DuckDB claims 600MB in facts/project, but duckdb_store settings.py overrides to 2 threads; architecture references both 600MB and 4 threads; memory/resource_governor defines separate Metal cache formula. No single source of truth for M1 8GB memory split.

## Evidence

- **facts/project**: GHOST_DUCKDB_MEMORY=600MB, optimal_threads=2 documented in configuration_constants.md
- **data/duckdb_store**: duckdb_store.py default THREAD_COUNT=4, but settings.py overrides to 2 threads for M1
- **architecture/hledac_universal**: critical_invariants.md references DuckDB 600MB limit and 4 threads in sprint_lifecycle_pipeline
- **memory/resource_governor**: m1_8gb-unified-memory-as-system-wide-constraint.md defines separate Metal cache formula: min(max(available*0.2, 512MiB), 1GiB)
