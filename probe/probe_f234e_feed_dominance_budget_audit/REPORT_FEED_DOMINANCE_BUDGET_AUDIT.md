# FEED DOMINANCE BUDGET REALITY AUDIT — F234E
**Audit Date:** 2026-05-11
**Status:** READ-ONLY — No Production Edits

---

## EXECUTIVE SUMMARY

**Feed dominance budget is PLANNED but NOT ENFORCED in the live feed processing loop.**

The `FeedDominanceBudget.cap_feeding()` policy is fully implemented and correctly wired in `_feed_dominance_should_fetch()`, but the method is called **per-source during source selection**, not during feed result processing. A feed-heavy run can still accept 2000+ findings before nonfeed terminality because:

1. **No hard stop in feed ingestion** — `_feed_dominance_should_fetch()` returns `(False, reason)` which causes the source to be SKIPPED, but the scheduler keeps running other sources
2. **Base budget defaults to zero** — `FeedDominanceBudget()` with all-zero fields means `is_active()` returns `False` unless ENV vars are set
3. **nonfeed_diagnostic cap triggers only when `domain_recon` intent is detected**, which requires `mission_intent` to be set (line 309: `_effective_intent = mission_intent if mission_intent else "unknown"`)

---

## QUESTION-BY-QUESTION AUDIT

### Q1: Where is FeedDominanceBudget constructed?

**Files:** `runtime/acquisition_strategy.py`

| Location | Line | Description |
|----------|------|-------------|
| `_load_feed_budget_from_env()` | 376-404 | Loads from ENV vars: `HLEDAC_FEED_MAX_ACCEPTED_BEFORE_NONFEED`, `HLEDAC_FEED_MAX_PER_SOURCE`, `HLEDAC_FEED_MAX_SHARE_BEFORE_NONFEED` |
| `build_acquisition_plan()` | 2085 | `feed_budget = _load_feed_budget_from_env() if acquisition_profile != "default" else FeedDominanceBudget()` |
| Default constructor | 249 | `FeedDominanceBudget()` — all fields default to 0 (inactive) |

**Finding:** Construction is correct. Default profile gets an empty/inactive budget.

---

### Q2: Where is FeedDominanceBudget.cap_feeding() called?

**File:** `runtime/sprint_scheduler.py`

| Call Site | Line | Context |
|-----------|------|---------|
| `_feed_dominance_should_fetch()` | 8055 | Mission-aware cap check (when `mission_intent is not None`) |
| `_feed_dominance_should_fetch()` | 8082 | nonfeed_diagnostic profile cap check (line 8080-8101) |
| `_feed_dominance_should_fetch()` | 8113 | Base budget fallback check |

**Finding:** 3 call sites in `_feed_dominance_should_fetch()`.

---

### Q3: Is cap_feeding() called inside the actual feed processing loop?

**Answer: NO** — It is called during **source selection** (`_feed_dominance_should_fetch()`), NOT during feed result processing.

The call chain:
```
_run_feed_lanes()
  → _fetch_feed_source(work)
    → _feed_dominance_should_fetch(work, nonfeed_terminal)  ← cap_feeding called HERE
      → if cap_feeding returns (False, reason): source SKIPPED
```

The scheduler keeps fetching other sources. The cap doesn't STOP ingestion — it SKIPS individual sources.

**Finding:** Per-source skip mechanism, not a hard global stop.

---

### Q4: Does nonfeed_diagnostic profile set a non-zero cap for domain_recon?

**Answer: YES** — See `acquisition_strategy.py:240`:
```python
_NONFEED_PROFILE_FEED_CAP_THRESHOLDS: dict[str, int] = {
    "cve_recon": 100,
    "wallet_recon": 15,
    "domain_recon": 20,   # ← Non-zero cap
    "infra_recon": 20,
    "person_recon": 20,
    "unknown": 0,         # ← Falls through if mission_intent not set
    "org_recon": 0,
}
```

**BUT CRITICAL ISSUE:** Line 309 — intent is inferred from `mission_intent`:
```python
_effective_intent = mission_intent if mission_intent else "unknown"
```

If `mission_intent` is `None` (which it is for nonfeed_diagnostic unless mission_runtime is active), `_effective_intent` becomes `"unknown"` → cap = 0 → **no cap enforced**.

**Finding:** Cap exists but requires `mission_intent` to be set. Without it, falls through to `unknown` → 0 cap.

---

### Q5: Does scheduler pause or cap feed ingestion while PUBLIC/CT are unresolved?

**Answer: PARTIAL** — The scheduler does check `nonfeed_terminal` and sets `nonfeed_unresolved = not nonfeed_terminal` (line 8047), but:

1. If budget is inactive (`is_active()` returns False and no mission/profile cap), it returns `(True, "")` — **feed runs normally**
2. If budget is active and cap triggers, it returns `(False, reason)` — **source skipped**, but scheduler continues to next source

**Finding:** Per-source skip, not a global pause. Scheduler continues processing other feed sources.

---

### Q6: Is feed cap reason written to NonfeedPlanDebug.feed_cap_reason?

**Answer: YES** — See `sprint_scheduler.py:8099`:
```python
if profile_cap_reason[0]:
    self._result.feed_budget_reason = profile_cap_reason[1]
    if self._acquisition_plan is not None and self._acquisition_plan.nonfeed_plan_debug is not None:
        nd = self._acquisition_plan.nonfeed_plan_debug
        nd.feed_cap_reason = profile_cap_reason[1]
        nd.feed_cap_applied_by_mission = True
    return False, profile_cap_reason[1]
```

**Finding:** Correctly written to `NonfeedPlanDebug.feed_cap_reason`.

---

### Q7: Is feed_cap_reason included in acquisition_report?

**Answer: YES** — See `sprint_scheduler.py:9355`:
```python
"feed_cap_reason": getattr(nd, "feed_cap_reason", None),
```

Also `mission_feed_cap_reason` at line 9376 and `feed_cap_applied_by_mission` at line 9377.

**Finding:** Correctly included in acquisition_report under `nonfeed_plan_debug` section.

---

### Q8: Did F234 live indicate feed cap active or inactive?

**Context states:** F234 live accepted 2421 feed findings and 0 nonfeed findings.

**Likely Finding:** Given that nonfeed was 0, `nonfeed_terminal` was likely False throughout, meaning `nonfeed_unresolved = True`. However, without `mission_intent` set, the nonfeed_diagnostic cap would fall through to `"unknown"` → cap = 0. The base budget is also inactive by default.

**Result:** Feed cap was INACTIVE throughout F234 live run.

---

### Q9: Is the feed cap per-source or global?

**Answer: BOTH:**

1. **Per-source cap:** `cap_feeding()` checks `max_feed_per_source` and iterates `feed_per_source.items()` — line 340-346 in acquisition_strategy.py
2. **Global cap:** `cap_feeding()` checks `max_feed_accepted_before_nonfeed_terminal` against total `feed_accepted_so_far` — line 328-337

**Finding:** Both per-source and global caps exist in the policy.

---

### Q10: Can a feed-heavy run accept 2000+ findings before nonfeed terminality?

**Answer: YES** — Because:

1. Default `FeedDominanceBudget()` has all zeros → `is_active()` returns False
2. nonfeed_diagnostic cap only triggers if `mission_intent` is set and resolves to a known intent
3. Even if cap triggers, it skips individual sources but doesn't stop the scheduler
4. **A feed-heavy run can accept unlimited findings if the budget is inactive or mission_intent is None**

---

### Q11: What is the minimum code change needed later to enforce feed cap?

**Current state:** Cap is planned, wired, and returns telemetry — but is effectively inactive without ENV vars.

**Minimum changes needed:**
1. Set `HLEDAC_FEED_MAX_ACCEPTED_BEFORE_NONFEED` ENV var to a non-zero value, OR
2. Ensure `mission_intent` is populated for nonfeed_diagnostic profile runs, OR
3. Modify `cap_feeding()` to use a default non-zero threshold when `_effective_intent == "unknown"` for nonfeed_diagnostic

**Code location for option 3:** `acquisition_strategy.py:309`:
```python
# Current:
_effective_intent = mission_intent if mission_intent else "unknown"

# Suggested fix:
if acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC and not mission_intent:
    _effective_intent = "domain_recon"  # Use default cap of 20
```

---

## ASSERTION VERIFICATION

| Assertion | Status | Evidence |
|-----------|--------|----------|
| FeedDominanceBudget exists | ✅ PASS | `acquisition_strategy.py:249` |
| `_NONFEED_PROFILE_FEED_CAP_THRESHOLDS` contains `domain_recon` | ✅ PASS | Line 240: `"domain_recon": 20` |
| `cap_feeding` method exists | ✅ PASS | `acquisition_strategy.py:279` |
| `acquisition_report` exposes `feed_cap_reason` | ✅ PASS | `sprint_scheduler.py:9355` |
| Scheduler has direct calls to `cap_feeding` | ✅ PASS | Lines 8055, 8082, 8113 |
| No production file modified | ✅ PASS | READ-ONLY audit |

---

## CONCLUSION

**Feed dominance budget is REAL but INACTIVE by default.**

The mechanism is fully implemented and correctly wired. However:
- Default construction (`FeedDominanceBudget()`) sets all caps to 0
- nonfeed_diagnostic cap requires `mission_intent` to be set
- Without ENV vars or `mission_intent`, feed cap is a no-op

**To make it enforceable in production:**
1. Set `HLEDAC_FEED_MAX_ACCEPTED_BEFORE_NONFEED=20` (or similar), OR
2. Ensure mission_intent is propagated through the nonfeed_diagnostic flow, OR
3. Add a default threshold for `unknown` intent in nonfeed_diagnostic profile

**The policy is not merely planned — it is implemented and reporting-ready. The gap is activation, not implementation.**
