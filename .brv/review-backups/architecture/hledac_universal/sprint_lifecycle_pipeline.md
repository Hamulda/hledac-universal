---
title: Sprint Lifecycle & Pipeline
summary: 'Sprint pipeline: CLI→8 lanes→advisory runners→graph→DuckDB. Lifecycle: bootstrap→discovery→fetch→quality→accept with 12 defined stages.'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:54:06.241Z'
updatedAt: '2026-07-11T14:54:06.241Z'
---
## Reason
Document sprint lifecycle stages, pipeline flow, and advisory dedup from architecture context

## Raw Concept
**Task:**
Document Hledac Universal sprint lifecycle, pipeline, and advisory systems

**Changes:**
- Added sprint lifecycle stages
- Documented advisory log LRU dedup
- Noted deprecated entry point

**Files:**
- runtime/sprint_scheduler.py
- runtime/sprint_entrypoint.py
- core/__main__.py

**Flow:**
CLI → run_sprint → SprintScheduler.run → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB canonical write

## Narrative
### Structure
Sprint pipeline flows from CLI through 8 acquisition lanes, advisory runners, graph accumulation, to DuckDB canonical write. Advisory Log uses LRU dedup with max 16 keys.

### Highlights
12 sprint lifecycle stages from NOT_SCHEDULED through ACCEPTED. Advisory Log LRU: HIT=O(1) membership+counter increment, MISS=O(1) dict setitem+deque.append+popleft.

### Rules
Rule 1: SprintScheduler tier priority High→Low: surface → structured_ti → deep → archive → other
Rule 2: Advisory Log LRU _ADVISORY_LOG_LRU_MAX = 16, FIFO eviction, no promotion on hit
Rule 3: Deprecated entry point: python -m hledac.universal --sprint (use runtime/sprint_entrypoint.py)

## Facts
- **sprint_pipeline_flow**: Sprint pipeline: CLI → run_sprint → SprintScheduler.run → 8 acquisition lanes → advisory runners → graph accumulation → DuckDB canonical write [project]
- **advisory_log_lru_max**: Advisory Log LRU has max 16 unique keys with FIFO eviction [project]
- **deprecated_entry_point**: Deprecated entry point: python -m hledac.universal replaced by runtime/sprint_entrypoint.py [project]
