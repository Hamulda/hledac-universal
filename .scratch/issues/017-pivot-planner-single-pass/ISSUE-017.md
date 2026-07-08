# Issue #17: Pivot Planner Single-Pass Analysis

## Status: ANALYSIS COMPLETE

## Problem Statement
Pivot planner currently uses multi-pass approach with Hermes3Engine integration
that requires iterating findings twice (once for Hermes path, once for heuristic).

## Current Architecture (pivot_planner.py)

### Pass 1: Hermes Path (plan_pivots → score_with_hermes_output)
```
plan_pivots(findings, hermes_outputs)
  ├─ score_with_hermes_output()
  │    ├─ _generate_pivots_from_hermes() ← per-output iteration
  │    └─ _generate_pivots_from_findings() ← SECOND iteration over findings
  └─ deduplicate → sort → trim
```

### Pass 2: Heuristic Path (direct plan_pivots)
```
plan_pivots(findings)
  └─ _generate_pivots_from_findings()
       └─ _generate_pivots_for_ioc() ← per-finding
```

## Root Cause
`score_with_hermes_output()` calls both `_generate_pivots_from_hermes()` (per Hermes output)
AND `_generate_pivots_from_findings()` (per finding) — two full iterations.

## Solution: Single-Pass Architecture
Merge Hermes and heuristic scoring into ONE pass through findings.

New structure:
```
_generate_pivots_from_findings_and_hermes(findings, hermes_outputs, graph_stats)
  ├─ Build Hermes pivot map: (pivot_type, ioc_type, ioc_value) → hermes_score
  ├─ Single iteration over findings
  │    ├─ Extract IOC from finding
  │    ├─ Generate heuristic pivots
  │    └─ Boost with Hermes score if available
  └─ Return merged + deduped pivots
```

## Implementation Plan
1. Extract `_generate_pivots_for_ioc()` to standalone function
2. Add `_build_hermes_pivot_map(hermes_outputs) → dict`
3. Modify `_generate_pivots_from_findings()` to accept optional hermes_map
4. Create unified `plan_pivots()` that uses single iteration

## Files Affected
- `runtime/pivot_planner.py` — core logic

## Complexity: LOW
