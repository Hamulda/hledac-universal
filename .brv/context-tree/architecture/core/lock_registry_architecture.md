---
title: Lock Registry Architecture
summary: Lock registry with 8-category ascending priority ordering for deadlock prevention
tags: []
related: []
keywords: []
createdAt: '2026-07-26T11:19:38.188Z'
updatedAt: '2026-07-26T11:19:38.188Z'
---
## Reason
Documenting core/locks.py lock registry with category ordering

## Raw Concept
**Task:**
Document lock registry architecture in core/locks.py

**Files:**
- core/locks.py

**Flow:**
register_lock() -> acquire_in_order() -> deadlock-free parallel acquisition

**Timestamp:** 2026-07-26

## Narrative
### Structure
LockCategory enum with 8 levels (METRICS through MPC). Ascending order prevents cyclic dependencies and ensures constant amortized acquisition time.

### Dependencies
Requires all locks within a group to share same category for correct ordering

### Highlights
AsyncLockDCLP uses lazy init - asyncio.Lock() only after threading.Lock check. Prevents asyncio.Lock() at module import (ISSUE-014 CRITICAL).

### Rules
Rule 1: Always acquire locks in ascending category order
Rule 2: Never acquire asyncio.Lock() at module import time
Rule 3: Use Rust AtomicCounter (issue #5) for high-frequency lock-free counters

## Facts
- **lock_category_order**: Lock category priority order: METRICS(1) -> CACHE(2) -> CONFIG(3) -> NETWORK(4) -> CURSOR(5) -> GRAPH(6) -> WAL(7) -> MPC(8) [project]
