---
title: M1ResourceGovernor Implementation
summary: M1 8GB memory governor with state machine, hysteresis, swap tiers, ConcurrencyPreset, and per-key ContextVar locks
tags: []
related: []
keywords: []
createdAt: '2026-07-16T11:06:41.910Z'
updatedAt: '2026-07-16T11:06:41.910Z'
---
## Reason
Document M1 8GB resource governor implementation from core/resource_governor.py

## Raw Concept
**Task:**
Document M1ResourceGovernor implementation for M1 8GB memory management

**Files:**
- core/resource_governor.py
- utils/uma_budget.py

**Flow:**
SAMPLER (raw sampling) -> GOVERNOR (policy/hysteresis) -> ALLOCATOR (request-level budgeting)

**Timestamp:** 2026-07-16

## Narrative
### Structure
Authority boundary: SAMPLER (utils/uma_budget.py) = raw sampling only, GOVERNOR (core/resource_governor.py) = policy/hysteresis/runtime, ALLOCATOR (resource_allocator.py) = request-level budgeting

### Dependencies
Depends on utils/uma_budget.py for raw samples, uses psutil for system memory metrics

### Highlights
State machine: NORMAL -> ELEVATED -> CRITICAL -> EMERGENCY -> CIRED (Circular RE Dormancy). ConcurrencyPreset calibrated for M1 8GB. Swap policy tiers: clean (<3GiB), diagnostic (3-5GiB), hard_block (>6GiB). Lock ordering prevents deadlocks (MPC=1, IO_LATCH=2, TELEMETRY=3, DECISION=4). Thread-safe via ContextVar per-key locks.

## Facts
- **m1_memory_budget**: M1 8GB memory budget is 5632 MB (soft ceiling) [project]
- **thermal_threshold**: Thermal threshold is 82.0°C [project]
- **gc_threshold**: GC threshold triggers at RSS ratio > 0.75 [project]
- **swap_detected_threshold**: swap_detected threshold is > 3.8 GiB [project]
- **clean_swap_max**: CLEAN_SWAP_MAX_GIB = 3.0 GiB [project]
- **diagnostic_swap_max**: DIAGNOSTIC_SWAP_MAX_GIB = 5.0 GiB [project]
- **hard_block_swap**: HARD_BLOCK_SWAP_GIB = 6.0 GiB [project]
- **lock_ordering**: Lock ordering: MPC=1, IO_LATCH=2, TELEMETRY=3, DECISION=4 [project]
- **io_only_ttl**: Governor io_only TTL = 0.5s [project]
- **fetch_limit_ttl**: Governor fetch_limit TTL = 5.0s [project]
- **block_model_load_ttl**: Governor block_model_load TTL = 30s [project]
