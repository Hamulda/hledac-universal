---
title: KB Audit Gap Fix 2026-07-26
summary: 'KB Audit gap fix: naming conventions exist in CLAUDE.md, integrations scattered, probe_tests empty, exit codes 2 files not 3'
tags: []
related: [knowledge_base/audit/kb_audit_2026_07_11.md]
keywords: []
createdAt: '2026-07-26T12:08:12.413Z'
updatedAt: '2026-07-26T12:08:12.413Z'
---
## Reason
Update KB audit status with 4 gap fixes resolved

## Raw Concept
**Task:**
KB Audit Gap Fix - Status update on 4 open items from previous audits

**Changes:**
- Naming/Error-handling conventions: RESOLVED - exists in CLAUDE.md and .claude/CLAUDE.md
- Integrations: OPEN - scattered across multiple locations, no dedicated docs/integrations/ directory
- probe_tests: OPEN - .brv/context-tree/testing/probe_tests/ is EMPTY, recommend delete or populate
- Exit codes: RESOLVED - tests/test_exit_codes.py (242 lines) + smoke_runner.py exist; kb_audit_2026_07_11 outdated saying "100 tests in 3 files"

**Files:**
- tests/test_exit_codes.py
- .claude/CLAUDE.md
- tests/probe_p_e2_feed_pipeline/

**Flow:**
Audit -> Identify gaps -> Update status -> Document findings

**Timestamp:** 2026-07-26

## Narrative
### Structure
KB audit gap fix addressing 4 items from previous audits

### Dependencies
Depends on CLAUDE.md for naming conventions; depends on tests/test_exit_codes.py for exit code tests

### Highlights
Naming conventions documented in CLAUDE.md with coding invariants: snake_case, no bare except, asyncio.gather with return_exceptions=True, mx.eval before clear_cache, no time.sleep in async, no asyncio.run in ThreadPoolExecutor, DuckDB writes via async_ingest_findings_batch, LMDB bulk via cursor.putmulti, RotatingBloomFilter for URL dedup, sidecars return [] on errors
