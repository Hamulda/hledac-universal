---
confidence: 0.85
sources: [facts/project/_index.md, facts/project/_index.md, testing/_index.md, testing/_index.md, memory/resource_governor/_index.md]
synthesized_at: '2026-07-16T11:30:38.726Z'
type: synthesis
title: Feature Flags Gate Memory Management Across Stack
summary: HLEDAC_DUCKDB_QUERY_CACHE, f221_abort_on_windup, mlx_memory_pressure all control memory pressure paths.
tags: [feature-flags, memory, duckdb, mlx, resource-governor]
related: []
keywords: [hledac_duckdb, f221, mlx_memory, feature-gate, query-cache, abort, windup, memory-pressure]
createdAt: '2026-07-16T11:30:38.726Z'
updatedAt: '2026-07-16T11:30:38.726Z'
---

# Feature Flags Gate Memory Management Across Stack

Memory pressure management is feature-gated at storage, ML, and Rust layers — coordinated guard system, not isolated configs.

## Evidence

- **facts/project**: Feature gate HLEDAC_DUCKDB_QUERY_CACHE controls DuckDB query cache
- **facts/project**: Feature gate HLEDAC_ARROW_INGEST and HLEDAC_ARROW_MIN_BATCH=5
- **testing**: Feature flag f221_abort_on_windup guard exists
- **testing**: Feature flag mlx_memory_pressure controls MLX memory pressure handling
- **memory/resource_governor**: ResourceGovernor manages memory with hysteresis state machine across pressure states
