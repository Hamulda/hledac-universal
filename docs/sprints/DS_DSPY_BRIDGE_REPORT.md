# DS ↔ DSPy Bridge Report (Sprint F260)

## Overview

Bridge Dempster-Shafer evidence fusion with DSPy optimization for OSINT reasoning.

**Status**: Implemented

## Changes

### Part A: DSPy OSINT Metric with DS Penalty (`brain/dspy_programs.py`)

Updated `osint_metric(gold, pred, trace=None)`:

| Component | Behavior |
|-----------|----------|
| Base score | Semantic similarity (cosine) via JSON field count |
| DS penalty | `conflict_mass > 0.4` → multiply by `(1 - conflict * 0.5)` |
| EIG bonus | `+min(0.1, eig)` if action reduces entropy |

New helpers:
- `_compute_conflict_from_evidence(evidence_list)` → float
- `_compute_eig_bonus(hypothesis_set, action)` → float

### Part B: EpistemicGapDetector DSPy Signature (`brain/dspy_signatures.py`)

```python
class EpistemicGapDetector(dspy.Signature):
    findings: list[str] = dspy.InputField(desc="Current sprint findings as text")
    known_gaps: list[str] = dspy.InputField(desc="Previously identified knowledge gaps")
    query: str = dspy.InputField(desc="Research query")
    gaps: list[str] = dspy.OutputField(desc="Prioritized list of unanswered questions")
    evidence_needed: list[str] = dspy.OutputField(desc="Specific evidence types needed to fill gaps")
    confidence: float = dspy.OutputField(desc="Confidence that these gaps are real (0-1)")
```

### Part C: New DSPy Programs (`brain/dspy_programs.py`)

| Program | Bounds | Wire |
|--------|--------|------|
| `EpistemicGapProgram` | MAX_EPISTEMIC_FINDINGS=30 | Called after WINDUP synthesis |
| `ContradictionResolverProgram` | MAX_CONTRADICTIONS=5 | Triggered when DS conflict > 0.3 |

### Wire Point (`runtime/sprint_scheduler.py`)

Location: `_run_epistemic_gap_advisory()` — called after `_run_synthesis_sidecar()` in WINDUP phase.

```
WINDUP entry:
 → _run_synthesis_sidecar()  [F259]
  → _run_epistemic_gap_advisory()  [F260] ← NEW
  → _enrichment_services.flush()  [F195C]
```

**Gates**:
- `HLEDAC_ENABLE_LLM=1` (same as synthesis)
- RAM< 5.0GB (tighter than synthesis's 5.5GB)

**Flow**:
1. EpistemicGapProgram → findings + known_gaps → gaps → ResearchSessionMemory
2. If DS conflict_mass > 0.3 → ContradictionResolverProgram → resolution

## M1 Constraints

| Constraint | Value | Reason |
|------------|-------|--------|
| EpistemicGapProgram input | max 30 findings | RAM budget |
| ContradictionResolver input | max 5 contradictions | M1 constraint |
| RAM guard |< 5.0GB | Tighter than synthesis (5.5GB) |

## Fail-Soft Design

All components fail gracefully:
- DSPy unavailable → skip silently
- ImportError → log debug, return
- Exception → log debug, continue
- No findings → skip silently

## Files Modified

| File | Changes |
|------|---------|
| `brain/dspy_programs.py` | DS penalty + EIG bonus in metric, 2 new programs |
| `brain/dspy_signatures.py` | EpistemicGapDetector signature |
| `runtime/sprint_scheduler.py` | Wire point + `_run_epistemic_gap_advisory()` |

## Invariants

| Test | Invariant |
|------|-----------|
| DS penalty applied when conflict > 0.4 | `conflict_mass() > 0.4 → score *= (1 - conflict * 0.5)` |
| EIG bonus capped at 0.1 | `eig_bonus = min(0.1, eig)` |
| RAM guard at 5.0GB | `uma.rss_gib >= 5.0 → skip` |
| Findings capped at 30 | `findings[:MAX_EPISTEMIC_FINDINGS]` |
| Contradictions capped at 5 | `contradictions[:MAX_CONTRADICTIONS]` |
| DS threshold for contradiction | `conflict_mass > 0.3` |
