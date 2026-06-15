# Architectural Gap Analysis: Query→Seed→Lane Flow

**Date:** 2026-06-14
**Status:** COMPLETE ANALYSIS
**Priority:** P0 (blocks CT lane for conceptual queries)

---

## Executive Summary

Current flow for query "ALPHV BlackCat ransomware":
```
Query → build_acquisition_plan() [has_domain=False] → CT DISABLED → 0 findings
```

Expected flow:
```
Query → Seed Extraction → Domain Seeds → CT Lane → Infrastructure → Findings
```

**Root Cause:** `build_acquisition_plan()` runs at L6694 BEFORE `run_mandatory_acquisition_prelude()` at L6865, where domain seeds are extracted at L12863. The acquisition plan decision (`has_domain = _has_domain_or_ip(query)`) uses only regex, missing semantic entity extraction.

---

## Timeline in `_run_internal()`

| Line | Operation | Issue |
|------|-----------|-------|
| L6694 | `build_acquisition_plan()` | **NO seed context** - uses regex only |
| L6865 | `run_mandatory_acquisition_prelude()` | prelude phase starts |
| L12863 | `extract_domain_candidates_from_text(query)` | Domain seeds extracted **AFTER** plan built |
| L12922 | `run_nonfeed_prelude_gather(seed_ctx)` | CT runs with seeds, but **lane not enabled in plan** |

---

## Problem Breakdown

### Problem 1: CT Lane Enablement Decision Without Seeds

**Location:** `runtime/acquisition_strategy.py:3057`

```python
has_domain = _has_domain_or_ip(query)  # Pure regex
```

**In LANE_RULES:** CT enabled when `has_domain=True OR aggressive_mode=True`

**For "ALPHV BlackCat ransomware":**
- `_has_domain_or_ip()` returns `False` (no domain pattern)
- CT lane **NOT enabled** in acquisition plan
- Even though `build_lane_query()` has wildcard expansion for entity names

### Problem 2: NonfeedSeedContext Built After Plan Decision

**Location:** `runtime/sprint_scheduler.py:12908`

```python
# INSIDE run_nonfeed_prelude_gather - runs AFTER build_acquisition_plan
_seed_ctx = NonfeedSeedContext(
    domains=tuple(d.domain for d in _candidates if d.domain),
    ips=...,
    urls=...,
)
```

The seed context that `build_lane_query()` needs is built **after** the plan is already decided.

### Problem 3: Hermes3 Not Connected to Seed Flow

**Location:** `brain/research_hypothesis_engine.py:2952`

`HypothesisEngine.generate_dark_surface_queries()` runs **after** findings are collected. It's isolated from the initial seed extraction phase.

### Problem 4: Existing Fallback is Misplaced

**Location:** `runtime/sprint_scheduler.py` (pivot seed fallback around L12850)

There's already a fallback that extracts "significant terms" as pseudo-seeds for non-domain queries:
```python
# Extract significant terms from query as pseudo-domain seeds
_tokens = _re.findall(r'\b[a-zA-Z]{3,}\b', query.lower())
_significant = [t for t in _tokens if t not in _noise]
if _significant:
    _pseudo_seeds = tuple(_significant[:5])
```

But this runs **inside** the prelude, after `build_acquisition_plan()` has already disabled CT.

---

## Components Analysis

### `extract_domain_candidates_from_text()` ✅ WORKS

**Location:** `runtime/nonfeed_candidate_ledger.py:814`

- Normalizes defanged markers ([.], (.), hxxp://)
- Validates FQDN structure
- Rejects .onion, .gov, .edu
- Bounded: max 10 domains/ips/urls
- **Already handles conceptual queries correctly**

### `build_lane_query()` ✅ WORKS

**Location:** `runtime/acquisition_strategy.py:4455`

```python
if lane == AcquisitionLane.CT:
    if seed_context and seed_context.domains:
        return seed_context.domains[0]
    domains = _DOMAIN_OR_IP_RE.findall(base_query)
    if domains:
        return " ".join(unique)
    # F265D-FIX: Wildcard expansion for entity names
    return base_query  # "ALPHV BlackCat" → crtsh wildcard expansion
```

**Correctly** handles:
1. Seed context domains
2. Regex domain extraction
3. Wildcard expansion fallback

### `NonfeedSeedContext` ✅ EXISTS

**Location:** `runtime/acquisition_strategy.py:830`

Bounded dataclass with domains, ips, urls tuples.

### `_run_nonfeed_prelude_gather()` ✅ CORRECT STRUCTURE

**Location:** `runtime/sprint_scheduler.py:12922`

Correctly builds `NonfeedSeedContext` and passes to lanes.

---

## Solution Options

### Option A: Pre-Seed Extraction Before Plan (MINIMAL CHANGE)

**Concept:** Move `extract_domain_candidates_from_text()` before `build_acquisition_plan()`.

**Change in `_run_internal()` around L6690:**
```python
# F270: Extract domain seeds BEFORE build_acquisition_plan
_pre_candidates = extract_domain_candidates_from_text(query)
_pre_seed_ctx = NonfeedSeedContext(
    domains=tuple(c.domain for c in _pre_candidates if c.domain),
    ips=(),
    urls=(),
)

self._acquisition_plan = build_acquisition_plan(
    query=query,
    ...,
    pre_seed_context=_pre_seed_ctx,  # NEW PARAM
)
```

**Change in `build_acquisition_plan()`:**
```python
has_domain = _has_domain_or_ip(query) or (
    pre_seed_context is not None and len(pre_seed_context.domains) > 0
)
```

**Pros:** Minimal change, no MLX dependency
**Cons:** Still regex-based, no semantic understanding

### Option B: Hermes3 Semantic Entity Extraction (CUTTING-EDGE)

**Concept:** Use Hermes3 to analyze query semantically and extract:
- Named entities (actors, malware, infrastructure)
- CT-queriable domain hints
- Relationship mapping

**New function in `nonfeed_candidate_ledger.py`:**
```python
async def hermes_enhanced_seed_extraction(
    query: str,
    hermes_engine: Hermes3Engine,
    seed_ctx: NonfeedSeedContext,
    timeout_s: float = 5.0,
) -> NonfeedSeedContext:
    """
    F270: Use Hermes3 for semantic entity extraction from conceptual queries.
    
    M1 8GB: Lazy MLX load, max 512 tokens, bounded timeout.
    Only runs when regex found no domain seeds.
    """
    if seed_ctx.domains:
        return seed_ctx  # Skip if regex found domains
    
    # Prompt: Analyze query, extract infrastructure IOCs
    # Parse: actor names, malware, infrastructure patterns
    # Map to CT-queriable domains where possible
```

**Wire into `_run_mandatory_acquisition_prelude()` after L12863:**
```python
# Hermes3 enrichment for domain-seed-less queries
if not _seed_ctx.domains and hermes_engine_available:
    _seed_ctx = await hermes_enhanced_seed_extraction(
        query, hermes_engine, _seed_ctx
    )
```

**Pros:** Semantic understanding, entity-aware, cutting-edge
**Cons:** MLX load overhead, complexity

### Option C: Hybrid Approach (RECOMMENDED)

Combine Option A (fast path) + Option B (enrichment path):

1. **Fast path:** Regex extraction before plan (Option A)
2. **Enrichment path:** Hermes3 for complex queries without domains (Option B)
3. **Fallback:** `build_lane_query()` wildcard expansion already works

**M1 8GB constraints:**
- Hermes3 lazy load only when needed
- Max 512 tokens for analysis prompt
- 5s timeout with fail-soft fallback
- No MLX in hot path (prelude is cold path)

---

## Files to Modify

| File | Lines | Change |
|------|-------|--------|
| `runtime/sprint_scheduler.py` | ~L6690-6700 | Pre-seed extraction before plan |
| `runtime/sprint_scheduler.py` | ~L12863 | Hermes3 enrichment call |
| `runtime/acquisition_strategy.py` | ~L830-860 | Extend NonfeedSeedContext |
| `runtime/acquisition_strategy.py` | ~L2940 | Add pre_seed_context param |
| `runtime/acquisition_strategy.py` | ~L3057 | has_domain uses pre_seed_context |
| `runtime/nonfeed_candidate_ledger.py` | ~L814 | Add hermes_enhanced_seed_extraction() |
| `brain/inference_engine.py` | TBD | New method: analyze_query_entities() |

---

## Test Plan

### Unit Tests
1. `extract_domain_candidates_from_text("ALPHV BlackCat")` → empty list (no domains)
2. `build_acquisition_plan(query, pre_seed_context=seed)` → CT enabled
3. `hermes_enhanced_seed_extraction()` → entities extracted

### Integration Tests
1. Sprint with "ALPHV BlackCat ransomware" → CT lane runs
2. Sprint with "lockbit3.tw" → CT lane runs with domain
3. Sprint with conceptual query → Hermes3 enrichment triggers

### Probe Tests
- `tests/probe_f270_query_seed_flow/` - 20+ tests for new flow

---

## Invariants

| # | Invariant | Test |
|---|-----------|------|
| 1 | `build_acquisition_plan()` called AFTER seed extraction | `test_f270_plan_after_seeds` |
| 2 | CT lane enabled for queries with extracted domain seeds | `test_f270_ct_enabled_with_seeds` |
| 3 | Hermes3 enrichment bounded: 512 tokens, 5s timeout | `test_f270_hermes_bounded` |
| 4 | No MLX load in hot path | `test_f270_no_mlx_hot_path` |
| 5 | Fail-soft: MLX unavailable → fallback to regex-only | `test_f270_fail_soft` |

---

## References

- Sprint 300s analysis: `~/.hledac/reports/sprint_1780830658_ANALYSIS.md`
- F265D-FIX: Wildcard expansion in `build_lane_query()`
- NonfeedSeedContext: `acquisition_strategy.py:830`
- `extract_domain_candidates_from_text()`: `nonfeed_candidate_ledger.py:814`
- `plan_lanes_for_pivot_seeds()`: `pipeline/pivot_lane_planner.py:36`