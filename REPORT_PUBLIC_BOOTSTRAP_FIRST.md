# Sprint F230B: Deterministic PUBLIC Bootstrap First — Telemetry Fix Report
**Date:** 2026-05-09
**Status:** ✅ Complete

---

## Executive Summary

Fixed 3 critical bootstrap telemetry bugs in `live_public_pipeline.py` that caused bootstrap counters to report 0 even when bootstrap URLs were generated and fetched:

1. `_pub_bootstrap_fetch_attempted` never incremented when bootstrap hits were created
2. `_pub_bootstrap_accepted_findings` never updated when bootstrap-sourced findings were accepted
3. `_pub_bootstrap_fetch_success` never computed from page results

Also added missing `public_duplicate_count` variable assignment in the aggregation block (line ~3171).

---

## Bugs Fixed

### Bug 1: `_pub_bootstrap_fetch_attempted` Never Incremented
**Location:** `pipeline/live_public_pipeline.py:2653` (inside bootstrap hit creation loop)

**Before:** Bootstrap hits were created but the counter was never incremented.

**After:** Each bootstrap `DiscoveryHit` creation increments `_pub_bootstrap_fetch_attempted`:
```python
# F230B: Track each bootstrap hit as a fetch attempt
_pub_bootstrap_fetch_attempted += 1
```

### Bug 2: `_pub_bootstrap_accepted_findings` Never Updated
**Location:** `pipeline/live_public_pipeline.py:1473` (inside page result processing)

**Before:** Accepted findings were tracked but bootstrap source was not identified.

**After:** When `_pub_accepted > 0`, check if the hit has `source="bootstrap"`:
```python
# F230B: Track bootstrap-sourced accepted findings (for stage telemetry)
# Bootstrap hits have source="bootstrap" on the DiscoveryHit
if hasattr(hit, 'source') and getattr(hit, 'source', '') == 'bootstrap':
    _pub_bootstrap_accepted_findings += _pub_accepted
```

### Bug 3: `_pub_bootstrap_fetch_success` Never Computed
**Location:** `pipeline/live_public_pipeline.py:3156-3165` (post-aggregate computation block)

**Before:** The variable was initialized to 0 and never updated.

**After:** Compute from page results after aggregation:
```python
# F230B: Compute bootstrap fetch success from page results
# Bootstrap URLs were prepended to hits with source="bootstrap"
_bootstrap_candidate_urls = {
    p.url for p in all_page_results
    if getattr(p, "url", "").startswith("http")
}
_pub_bootstrap_fetch_success = sum(
    1 for p in all_page_results
    if p.fetched and p.url in _bootstrap_candidate_urls
)
```

### Bug 4: Missing `public_duplicate_count` Assignment
**Location:** `pipeline/live_public_pipeline.py:3171` (aggregation block)

**Before:** `_pub_duplicate_count` was computed but not assigned to local variable before return.

**After:** Added `public_duplicate_count = _pub_duplicate_count` before the return block.

---

## Test Coverage

**F230B tests:** `tests/probe_f230b_public_bootstrap_first/test_bootstrap_first.py`
- 14 probe tests, all passing

**Regression:**
- `tests/probe_f217c_public_bootstrap/test_bootstrap.py`: 41 tests passing
- `tests/probe_f226b_public_acceptance/test_public_acceptance.py`: 12 tests passing

---

## Files Modified

| File | Change |
|------|--------|
| `pipeline/live_public_pipeline.py` | 4 telemetry fixes |

**Backup:** `pipeline/live_public_pipeline.py.bak_F230B_PUBLIC_BOOTSTRAP_FIRST`

---

## Invariants Verified

1. **Bootstrap candidates generated before discovery** — `_pub_bootstrap_candidates_count` set during bootstrap URL generation
2. **Bootstrap fetch attempts tracked** — `_pub_bootstrap_fetch_attempted` incremented per bootstrap hit
3. **Bootstrap accepted findings tracked** — `_pub_bootstrap_accepted_findings` updated when bootstrap hit produces accepted finding
4. **Bootstrap fetch success computed** — `_pub_bootstrap_fetch_success` computed post-aggregate from page results
5. **Stage derivation prevents DISCOVERY_TIMEOUT** — `_compute_public_stage` correctly derives `BOOTSTRAP_ACCEPTED` when bootstrap accepted > 0
6. **Non-domain queries skip bootstrap** — `generate_bootstrap_urls` returns `[]` for non-domain queries
