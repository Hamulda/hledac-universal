---
confidence: 0.95
sources: [architecture/hledac_universal/_index.md, data/duckdb_store/_index.md]
synthesized_at: '2026-07-24T21:05:20.859Z'
type: synthesis
title: Sprint Lifecycle Cross-Domain Contract
summary: Sprint pipeline architecture spans architecture + data domains with 12-stage lifecycle, 8 acquisition lanes, and async ingest as the canonical write endpoint.
tags: [sprint, async-ingest, acquisition-lanes, write-serialization]
related: [architecture/hledac_universal/sprint_lifecycle_pipeline.md, data/duckdb_store/context.md]
keywords: [sprint-lifecycle, async_ingest_findings_batch, acquisition-lanes, 12-stages, canonical-write, write-path, advisory-runners]
createdAt: '2026-07-24T21:05:20.859Z'
updatedAt: '2026-07-24T21:05:20.859Z'
---

# Sprint Lifecycle Cross-Domain Contract

The 12-stage sprint lifecycle (NOT_SCHEDULED→ACCEPTED) in architecture/hledac_universal establishes the process contract that data/duckdb_store enforces via async_ingest_findings_batch as the mandatory write endpoint.

## Evidence

- **architecture/hledac_universal**: sprint_lifecycle_pipeline.md defines 12 stages with 8 acquisition lanes feeding into run_acquisition_lanes → advisory runners → graph accumulation → async_ingest_findings_batch
- **data/duckdb_store**: Invariant I5 (critical_invariants) mandates DuckDB writes ONLY through async_ingest_findings_batch(), enforced by duckdb_store.write_serialization_fix
