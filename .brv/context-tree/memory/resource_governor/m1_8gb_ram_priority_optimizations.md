---
title: M1 8GB RAM Priority Optimizations
summary: 'P0-P3 priority matrix for M1 8GB RAM: msgspec gc=False (L4), GHOST_INVARIANTS CI (L5), per-lane memory cost (L6), Rust graph analytics (L10)'
tags: []
related: [memory/resource_governor/m1resourcegovernor_implementation.md]
keywords: []
createdAt: '2026-07-16T11:14:05.322Z'
updatedAt: '2026-07-16T11:14:05.322Z'
---
## Reason
Documenting systematic memory optimization priorities ranked by Meadows leverage hierarchy

## Raw Concept
**Task:**
Document highest leverage optimizations for M1 8GB RAM UMA system

**Changes:**
- Added P0 optimization: msgspec.Struct gc=False on hot-path DTOs (~200 bytes/future × N futures)
- Added P0 optimization: Enforce GHOST_INVARIANTS in CI via ruff/mypy plugins
- Added P1 optimization: Surface per-lane RSS delta to ResourceGovernor
- Added P1 optimization: Wire Rust shortest_path + pagerank from graph_cache.rs
- Added P2 optimization: evidence_quality telemetry (findings_with_citation / total_findings)
- Added P2 optimization: Adaptive ResourceGovernor thresholds based on swap history

**Files:**
- evidence_log.py
- CLAUDE.md

**Flow:**
Analysis -> Priority Matrix -> P0/P1/P2/P3 Recommendations

**Timestamp:** 2026-07-16

**Patterns:**
- `asyncio\.gather.*return_exceptions=True` - GHOST_INVARIANT I6 pattern
- `mx\.eval\(\[\]\).*mx\.metal\.clear_cache\(\)` - GHOST_INVARIANT I4 pattern

## Narrative
### Structure
Priority matrix with 9 optimizations across 6 Meadows leverage levels (L4-L12). P0 items are low effort, high impact.

### Dependencies
ResourceGovernor requires per-lane visibility to throttle correctly. SidecarContext.memory_pressure currently only exposes aggregate 0.0-1.0 float.

### Highlights
P0: msgspec gc=False (evidence_log.py, finding.py, pipeline/*.py) | P0: CI enforcement of 10 GHOST_INVARIANTS | P1: Rust graph analytics 10-100× speedup | P1: Per-lane RSS delta telemetry

### Rules
Rule 1: P0 items (low effort, high impact) should be started first
Rule 2: GHOST_INVARIANTS I6: asyncio.gather must have return_exceptions=True
Rule 3: GHOST_INVARIANTS I4: mx.eval([]) must precede mx.metal.clear_cache()

### Examples
Example P0 fix: Add gc=False to msgspec.Struct classes in evidence_log.py, finding.py
Example P1 fix: Emit per-lane RSS delta after each lane completes, visible to ResourceGovernor

## Facts
- **hardware_config**: M1 8GB RAM uses Unified Memory Architecture (UMA) [project]
- **memory_per_future**: msgspec.Struct gc=False saves ~200 bytes per future instance [project]
- **sprint_ram_savings**: 10,000 findings per sprint × 200B = 2MB RAM saved [project]
- **invariant_enforcement**: GHOST_INVARIANTS are documented in CLAUDE.md but not automatically enforced [convention]
- **rg_thresholds**: ResourceGovernor thresholds are fixed at 0.5, 0.7, 0.85 [project]
- **duckdb_config**: MAX_CHUNK_SIZE=500, MAX_CHUNK_CONCURRENCY=2 for DuckDB [project]
- **graph_speedup**: Rust graph analytics could provide 10-100× speedup over Python igraph [project]
