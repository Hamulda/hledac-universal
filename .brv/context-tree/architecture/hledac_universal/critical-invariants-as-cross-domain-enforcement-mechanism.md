---
confidence: 0.91
sources: [architecture/hledac_universal/_index.md, data/duckdb_store/_index.md, memory/resource_governor/_index.md, facts/project/_index.md]
synthesized_at: '2026-07-24T21:05:20.864Z'
type: synthesis
title: Critical Invariants as Cross-Domain Enforcement Mechanism
summary: 10 critical invariants are defined in architecture but enforced by duckdb_store, resource_governor, and facts/project CI.
tags: [invariants, ci-enforcement, cross-domain, stability]
related: [architecture/hledac_universal/10-critical-invariants-govern-system-stability.md, architecture/hledac_universal/critical_invariants.md]
keywords: [critical-invariants, GHOST_INVARIANTS, CI, stability-rules, asyncio, DuckDB, enforcement]
createdAt: '2026-07-24T21:05:20.864Z'
updatedAt: '2026-07-24T21:05:20.864Z'
---

# Critical Invariants as Cross-Domain Enforcement Mechanism

The 10 critical invariants (M1 stability rules) are authored in architecture/hledac_universal but their enforcement is distributed: duckdb_store enforces I5 (async_ingest), resource_governor enforces I7 (no time.sleep), facts/project enforces I1-I10 via GHOST_INVARIANTS CI.

## Evidence

- **architecture/hledac_universal**: critical_invariants.md defines all 10 invariants with CI enforcement rules
- **data/duckdb_store**: Enforces I5: DuckDB ONLY via async_ingest_findings_batch(), documented in issue_032_write_serialization_fix
- **memory/resource_governor**: Enforces I7 via asyncio.sleep() in hysteresis state machine; MPSC uses crossbeam not asyncio.Queue
- **facts/project**: GHOST_INVARIANTS CI enforcement documented in parallel_async_helper and technology_stack
