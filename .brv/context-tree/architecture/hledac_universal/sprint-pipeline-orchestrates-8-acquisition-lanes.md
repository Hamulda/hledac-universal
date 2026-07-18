---
confidence: 0.9
sources: [facts/project/_index.md, hledac_universal/_index.md, duckdb_store/_index.md, testing/conftest/_index.md]
synthesized_at: '2026-07-18T00:18:19.625Z'
type: synthesis
title: Sprint Pipeline Orchestrates 8 Acquisition Lanes
summary: 12-stage sprint lifecycle processes queries through tiered acquisition lanes to DuckDB write.
tags: [sprint, pipeline, orchestration, lanes]
related: []
keywords: [sprint-scheduler, acquisition-lanes, advisory-log, tier-priority, lifecycle-stages]
createdAt: '2026-07-18T00:18:19.625Z'
updatedAt: '2026-07-18T00:18:19.625Z'
---

# Sprint Pipeline Orchestrates 8 Acquisition Lanes

SprintScheduler.run() traverses: run_prelude → run_acquisition_lanes (8 lanes: surface/structured_ti/deep/archive/other/nonfeed/CT/WAYBACK/PASSIVE_DNS/PIVOT_EXECUTOR/DOH) → advisory runner → graph accumulation → DuckDB async_ingest_findings_batch. Advisory Log LRU(16) with FIFO eviction.

## Evidence

- **facts/project**: SprintScheduler.run flow documented with full pipeline stages
- **hledac_universal**: 8 acquisition lanes, tier priority High→Low: surface → structured_ti → deep → archive → other
- **duckdb_store**: 3-tier facts hierarchy maps to sprint_delta, sprint_scorecard, temporal_events
- **testing/conftest**: test_sprint_scheduler.py: ~89 tests as canonical validation
