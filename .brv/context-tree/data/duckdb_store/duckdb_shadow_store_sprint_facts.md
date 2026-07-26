---
title: DuckDB Shadow Store Sprint Facts
summary: '3-tier facts hierarchy: sprint facts, shadow findings, cross-sprint events. 3 independent graph attachment slots.'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:18:46.783Z'
updatedAt: '2026-07-26T11:18:46.783Z'
---
## Reason
Document 3-tier facts hierarchy and graph attachment slots

## Raw Concept
**Task:**
Document DuckDB Shadow Store facts hierarchy and graph attachments

**Changes:**
- F272: DuckDB ioc_graph table removed, IOC storage via DuckPGQGraph
- Truth write graph slot added for ACTIVE-phase buffered writes

**Flow:**
async_ingest_findings_batch -> graph attachments -> DuckPGQGraph or IOCGraph

## Narrative
### Structure
3-tier facts: (1) Sprint facts: sprint_delta, sprint_scorecard, source_hit_log. (2) Shadow findings: canonical_findings, shadow_runs. (3) Cross-sprint: temporal_events (append-only). 3 graph slots: _ioc_graph (analytics), _stix_graph (STIX synthesis), _truth_write_graph (ACTIVE buffered writes).

### Dependencies
DuckPGQGraph for analytics, IOCGraph for truth, LanceDB for entity storage

## Facts
- **facts_tiers**: DuckDB Shadow Store has 3-tier facts hierarchy [project]
- **graph_slots**: 3 independent graph attachment slots in ShadowStore [project]
