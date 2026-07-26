---
title: Acquisition Orchestrator Lifecycle
summary: 'Acquisition orchestrator: stable (2-branch) vs aggressive (3-branch) cycles, adaptive max cycles, 5 exit paths'
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:38.194Z'
updatedAt: '2026-07-26T11:19:38.194Z'
---
## Reason
Documenting acquisition orchestrator lifecycle from scheduler_v2/acquisition.py

## Raw Concept
**Task:**
Document acquisition orchestrator lifecycle in runtime/scheduler_v2/acquisition.py

**Files:**
- runtime/scheduler_v2/acquisition.py

**Flow:**
run() -> while not terminal: check_deadline -> pre_windup -> drain_patterns -> pressure_relief -> windup_guard -> run_one_cycle

**Timestamp:** 2026-07-26

## Narrative
### Structure
Stable mode: FEED || PUBLIC. Aggressive mode: FEED + PUBLIC + CT in 3-branch TaskGroup. _branch_timeout = max((remaining - _safety_floor) / 3, 5.0).

### Dependencies
AIMD telemetry from ctx._cycle._aimd_telemetry or FetchCoordinator

### Highlights
Adaptive max cycles: max(50, min(300, int((sprint_duration - windup_lead) / cycle_time_ema))). Synthesis sidecar runs in WINDUP phase (F259, HLEDAC_ENABLE_HERMES_SYNTHESIS env gate).
