---
title: UMA Memory Management
summary: UMA memory management with ratio-based thresholds, concurrency presets, hysteresis state machine, and swap tiered policy
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:50:41.383Z'
updatedAt: '2026-07-11T14:50:41.383Z'
---
## Reason
Documenting ResourceGovernor memory management decisions

## Raw Concept
**Task:**
Document ResourceGovernor UMA memory management for M1 8GB

**Changes:**
- B1-FIX: Ratio-based adaptive thresholds (2026-07-03)
- Added dual-channel TTL cache (Issue #8)
- Implemented memory pressure hysteresis state machine (P2-23)
- Calibrated swap tiered policy for M1 8GB

**Flow:**
memory_check -> hysteresis_state -> concurrency_adjust -> action_take

## Narrative
### Structure
ResourceGovernor manages UMA memory with ratio-based thresholds across M1 8GB (6.25GB budget), M2 16GB, M3 24GB

### Dependencies
Requires psutil for memory detection, concurrent.futures for worker pool management

### Highlights
Concurrency presets: emergency=0 workers, critical=1, warn=3, soft_warn=5, ok=5 with varying fetch limits. Hysteresis prevents oscillation between states.
