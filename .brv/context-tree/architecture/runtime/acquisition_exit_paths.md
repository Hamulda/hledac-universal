---
title: Acquisition Exit Paths
summary: '5 exit paths: hard_deadline, stop_requested, lifecycle_abort, max_cycles_reached, terminal'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:38.198Z'
updatedAt: '2026-07-26T11:19:38.198Z'
---
## Reason
Documenting acquisition orchestrator exit conditions

## Raw Concept
**Task:**
Document acquisition orchestrator exit paths

**Files:**
- runtime/scheduler_v2/acquisition.py

**Timestamp:** 2026-07-26

## Narrative
### Structure
Exit paths: hard_deadline (_check_hard_deadline() returns False), stop_requested (ctx._stop_requested set), lifecycle_abort (_runner.abort_requested), max_cycles_reached (cycles_started >= effective_max_cycles), terminal (normal completion)

### Highlights
effective_max_cycles = max(50, min(300, int((sprint_duration - windup_lead) / cycle_time_ema)))
