# F233F Gate No False-Green — Nonfeed Capability vs Feed Baseline Split

**Sprint:** F233F
**Date:** 2026-05-11
**Verdict:** Split gate verdicts between nonfeed capability and feed baseline

---

## Problem

F232 readiness emitted `READY_TO_RUN_NOW` for a run that produced:
- 4464 findings
- 100% feed
- `QUALITY_FAIL_FEED_ONLY`
- `nonfeed_evidence_missing=true`

This was acceptable as a **feed baseline** but was incorrectly labeled as full capability readiness.

---

## Solution: F233F Gate Split

### New Verdicts

| Verdict | Meaning |
|---------|---------|
| `READY_FOR_NONFEED_CAPABILITY_RUN` | All capability checks pass. Nonfeed evidence surface confirmed. |
| `READY_FOR_FEED_BASELINE_ONLY` | Feed baseline runnable. Nonfeed capability blocked (reason in `why_nonfeed_capability_blocked`). |
| `RUN_NOW` | Legacy — retained for backward compatibility (all clear + no split needed) |
| `RESTART_THEN_RUN` | Swap elevated, restart recommended |
| `DO_NOT_RUN_*` | Blocked for specific reasons |

### Output Contract Fields (JSON + Markdown)

```json
{
  "verdict": "READY_FOR_NONFEED_CAPABILITY_RUN | READY_FOR_FEED_BASELINE_ONLY | ...",
  "live_allowed": true | false,
  "capability_live_allowed": true | false,   // nonfeed capability run allowed
  "feed_baseline_allowed": true | false,    // feed baseline run allowed
  "why_nonfeed_capability_blocked": "provider_surface_degraded; canonical_fallback_detected; f232g_research_quality_missing",
  "degraded_but_allowed": true | false,       // provider degraded but explicitly allowed
  "canonical_fallback_detected": true | false,
  "f232g_research_quality_present": true | false,
  "f233d_nonfeed_prelude_coverage": true | false
}
```

---

## Gate Rules for Nonfeed Diagnostic Profile

### Nonfeed Capability Requires ALL of:
1. `provider_surface_ok = True` (explicit, not degraded)
2. `canonical_fallback_detected = False` (no acquisition fallback)
3. `f232g_research_quality_present = True` (F231D `research_quality: true`)
4. `f233d_nonfeed_prelude_coverage = True` OR F233D not yet written (soft-fail for future)

### Feed Baseline Allows When:
- Swap ≤ 4.0 GiB (not hard block)
- No `DO_NOT_RUN_*` blocks active
- Basic artifacts (F221, F223) present

---

## Verdict Decision Tree

```
Missing F221/F223 artifacts → DO_NOT_RUN_FIX_ARTIFACTS
Fallback schema detected    → DO_NOT_RUN_CONTRACT (blocks both)
Provider surface broken     → DO_NOT_RUN_PROVIDER_SURFACE (blocks both)
UMA emergency/critical      → DO_NOT_RUN_UNKNOWN (blocks both)
Swap > 4.0 GiB             → DO_NOT_RUN_MEMORY_HARD_BLOCK (blocks both)

# Feed baseline allowed (no capability)
Swap in (2.0, 4.0] GiB    → RESTART_THEN_RUN (capability blocked)

# Capability path
All 4 capability conditions met
  AND swap ≤ 2.0 GiB      → READY_FOR_NONFEED_CAPABILITY_RUN

# Capability blocked, feed baseline allowed
Capability conditions NOT met
  AND swap ≤ 2.0 GiB      → READY_FOR_FEED_BASELINE_ONLY
```

---

## Context: F232 Run Output vs F233F Gate

| Field | F232 Run Result | F233F Gate Verdict |
|-------|----------------|-------------------|
| `nonfeed_evidence_missing` | `true` | Capability blocked |
| `quality_verdict` | `QUALITY_FAIL_FEED_ONLY` | Feed baseline |
| `feed_percentage` | `100%` | Feed only |
| F232G research_quality | Not present | Required for capability |
| `provider_surface` | OK | Required for capability |

---

## Implementation

**File:** `tools/prelive_one_button_gate.py`

- `OneButtonVerdict` enum extended with `READY_FOR_NONFEED_CAPABILITY_RUN`, `READY_FOR_FEED_BASELINE_ONLY`
- `OneButtonResult` dataclass extended with 7 new fields for split output contract
- `run_one_button_gate()` refactored with F233F decision tree
- `_render_markdown()` extended with capability vs feed split section
- Gate remains pure: no scheduler/runtime/network imports

**Tests:** `tests/probe_f233f_gate_no_false_green/test_f233f_gate_no_false_green.py`

---

## Abort Conditions Enforced

- ❌ Do not remove feed baseline option
- ❌ Do not greenlight nonfeed capability when provider surface is false
- ❌ Do not import runtime/scheduler in gate
- ❌ Do not run live sprint

---

## Success Definition

> Gate no longer labels feed-only readiness as nonfeed capability readiness.

F233F achieves this by:
1. Splitting `live_allowed` into `capability_live_allowed` and `feed_baseline_allowed`
2. Making `READY_FOR_NONFEED_CAPABILITY_RUN` require all 4 conditions explicitly
3. Emitting `READY_FOR_FEED_BASELINE_ONLY` when capability blocked but feed baseline OK