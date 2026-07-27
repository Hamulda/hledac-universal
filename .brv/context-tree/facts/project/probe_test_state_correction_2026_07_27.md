---
title: Probe Test State Correction 2026-07-27
summary: 'Probe test state as of 2026-07-27: probe_p_e2_feed_pipeline exists; probe_p12/p14 were archived in F350M-R cleanup'
tags: []
related: [facts/project/known_issues_and_todos.md]
keywords: []
createdAt: '2026-07-27T13:24:49.662Z'
updatedAt: '2026-07-27T13:24:49.663Z'
---
## Reason
Correcting stale KB audit finding about probe test directories

## Raw Concept
**Task:**
Document actual probe test directory state

**Changes:**
- probe_p_e2_feed_pipeline: EXISTS - active probe test directory
- probe_p12_http3_lane: NOT PRESENT - was archived in F350M-R cleanup
- probe_p14_prewarm_conditional: NOT PRESENT - was archived in F350M-R cleanup

**Files:**
- tests/probe_p_e2_feed_pipeline/
- tests/test_deep_probe_runner.py
- tests/archive/

**Timestamp:** 2026-07-27

## Narrative
### Structure
Probe tests organized under tests/ directory with deep probe runner infrastructure

### Highlights
KB audit from 2026-07-11 incorrectly claimed probe_p12 and probe_p14 existed - these were archived/removed during F350M-R cleanup

### Rules
Rule 1: KB audit from 2026-07-11 is STALE for probe_p12/p14
Rule 2: Only probe_p_e2_feed_pipeline is currently active

## Facts
- **active_probe_test**: tests/probe_p_e2_feed_pipeline/ is the only active probe test directory [project]
- **archived_probe_test**: tests/probe_p12_http3_lane/ does not exist - was archived in F350M-R cleanup [project]
- **archived_probe_test**: tests/probe_p14_prewarm_conditional/ does not exist - was archived in F350M-R cleanup [project]
- **stale_kb_audit**: KB audit from 2026-07-11 is stale for probe_p12/p14 directories [project]
