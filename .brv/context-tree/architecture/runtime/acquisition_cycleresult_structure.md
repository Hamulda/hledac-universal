---
title: Acquisition CycleResult Structure
summary: 'CycleResult frozen msgspec.Struct with 10 fields: ok, empty_work_items, aggressive_mode, feed/public/ct results, aimd metrics'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:38.197Z'
updatedAt: '2026-07-26T11:19:38.197Z'
---
## Reason
Documenting CycleResult msgspec.Struct for cycle telemetry

## Raw Concept
**Task:**
Document CycleResult structure for acquisition cycle telemetry

**Files:**
- runtime/scheduler_v2/acquisition.py

**Timestamp:** 2026-07-26

## Narrative
### Structure
CycleResult(msgspec.Struct, frozen=True): cycle_ok, empty_work_items, aggressive_mode, feed_results (ok,count), public_results (ok,count,timeout), ct_results (ok,count), aimd_window, aimd_successes, aimd_failures, error

### Examples
feed_results: (ok: bool, count: int)
public_results: (ok: bool, count: int, timeout: bool)
ct_results: (ok: bool, count: int)
