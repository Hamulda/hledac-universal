---
title: Coding Conventions Status
summary: Coding conventions documented in .claude/CLAUDE.md, not in dedicated docs/conventions/ directory
tags: []
related: [facts/project/exit_code_convention.md, facts/project/hledac_universal_claude_md.md]
keywords: []
createdAt: '2026-07-26T12:10:53.721Z'
updatedAt: '2026-07-26T12:10:53.721Z'
---
## Reason
KB audit gap fix - document that coding conventions exist in CLAUDE.md, not a dedicated directory

## Raw Concept
**Task:**
Document coding conventions status from KB audit

**Changes:**
- Conventions EXIST in CLAUDE.md and .claude/CLAUDE.md
- NO dedicated docs/conventions/ directory (gap identified)
- Coding invariants documented: snake_case, no bare except, asyncio patterns

**Files:**
- CLAUDE.md
- .claude/CLAUDE.md

**Flow:**
Conventions defined in CLAUDE.md -> enforced by code review

**Timestamp:** 2026-07-26

**Author:** KB Audit 2026-07-26

## Narrative
### Structure
Conventions are documented in project root CLAUDE.md and .claude/CLAUDE.md, not in a dedicated docs/conventions/ directory

### Dependencies
Depends on CLAUDE.md for actual convention rules

### Highlights
Coding invariants: snake_case naming, no bare except, asyncio.gather with return_exceptions=True, mx.eval before clear_cache, no time.sleep in async, no asyncio.run in ThreadPoolExecutor, DuckDB writes via async_ingest_findings_batch, LMDB bulk via cursor.putmulti, RotatingBloomFilter for URL dedup, sidecars return [] on errors

## Facts
- **conventions_location**: Conventions are documented in CLAUDE.md and .claude/CLAUDE.md [convention]
- **conventions_directory**: No dedicated docs/conventions/ directory exists [project]
- **async_pattern**: asyncio.gather requires return_exceptions=True [convention]
- **duckdb_write_pattern**: DuckDB writes must use async_ingest_findings_batch [convention]
- **lmdb_bulk_pattern**: LMDB bulk operations use cursor.putmulti [convention]
