# Sprint F235D — Source Family Canonicalization and Dedup

## Problem
Live canonical reports had `acquisition_report_fallback_used=false` but contradictory CT identities:
- `family="CT", attempted=false, error="no_candidates", terminal_state="SKIPPED"`
- `family="ct", attempted=true, error="http_502", terminal_state="ATTEMPTED_ERROR"`

This broke delta memory, research quality, CT loss stage, terminality, validator, and triage.

## Solution
Two new helpers in `runtime/acquisition_strategy.py` (F235D section):

### `normalize_source_family_name(value: str) -> str`
Maps mixed-case family names to canonical lowercase:
- "CT", "ct", "Ct" → "ct"
- "PUBLIC", "public", "Public" → "public"
- "PASSIVE_DNS", "passivedns" → "passive_dns"
- etc.

### `canonicalize_source_family_outcomes(outcomes: list[dict]) -> list[dict]`
Dedup + merge outcomes that normalize to the same family:
- `attempted = any(attempted=True)`
- `skipped = all(skipped) and not attempted`
- `timeout = any(timeout=True)`
- `error = first real provider error over synthetic (http_502 > no_candidates)`
- `terminal_state = highest-priority` (ATTEMPTED_ACCEPTED > ATTEMPTED_TIMEOUT > ATTEMPTED_ERROR > ...)
- `raw_count/built_count/accepted_count = max`
- `duration_s = max non-null`

## Changes

| File | Change |
|------|--------|
| `runtime/acquisition_strategy.py` | Added F235D section with `normalize_source_family_name`, `_TERMINAL_PRIORITY`, `_pick_best_terminal`, `canonicalize_source_family_outcomes`. Updated `normalize_source_family_outcome` to set `family=_canonical_family`. Added to `__all__`. |
| `core/__main__.py` | Imported `canonicalize_source_family_outcomes`. Call after `_sfo_list` assembly, before `build_acquisition_report`. |
| `runtime/sprint_scheduler.py` | Imported `canonicalize_source_family_outcomes`. Canonicalize at `_final_source_family_outcomes_for_terminality` return (terminality SSOT). |
| `tools/live_artifact_triage.py` | `_has_ct` uses `.get("family")` + `.lower()` instead of `.get("source_family")`. |
| `tools/evidence_delta_memory.py` | `_get_ct_public_info` uses `.lower()` on family field for case-insensitive comparison. |

## CT Duplicate Required Result

Given:
- CT: attempted=False, error=no_candidates, terminal_state=SKIPPED
- ct: attempted=True, error=http_502, terminal_state=ATTEMPTED_ERROR

Output:
```
family=ct, attempted=True, skipped=False, raw_count=0,
accepted_count=0, error=http_502, terminal_state=ATTEMPTED_ERROR
```

## Assertions Verified

1. `normalize_source_family_name("CT") == "ct"`
2. `normalize_source_family_name("ct") == "ct"`
3. `normalize_source_family_name("PUBLIC") == "public"`
4. `normalize_source_family_name("PASSIVE_DNS") == "passive_dns"`
5. Canonicalize removes duplicate normalized family names
6. CT/ct duplicate merges to one ct
7. Provider error http_502 wins over synthetic no_candidates
8. attempted=True wins over attempted=False
9. timeout=True wins over generic error
10. public DISCOVERY_ERROR outcome preserved
11. feed accepted outcome preserved
12. Live report with CT+ct normalizes to exactly one ct
13. normalize_source_family_outcome produces "family" key (not "source_family")
14. evidence_delta_memory sees ct attempted=True (lowercase check)
15. live_artifact_triage reads canonical family field case-insensitively

## Safety
- No live network calls
- No MLX/model load
- No browser/stealth
- No legacy/deep_probe/DHT imports
- `canonicalize` is fail-soft: empty list → empty list, single entry → unchanged

## Files Modified
- `runtime/acquisition_strategy.py` (backup: `.bak_F235D_SOURCE_FAMILY_CANONICALIZATION`)
- `core/__main__.py` (backup: `.bak_F235D_SOURCE_FAMILY_CANONICALIZATION`)
- `benchmarks/live_measurement_kpi.py` (backup: `.bak_F235D_SOURCE_FAMILY_CANONICALIZATION`)
- `tools/evidence_delta_memory.py` (backup: `.bak_F235D_SOURCE_FAMILY_CANONICALIZATION`)
- `tools/live_artifact_triage.py` (backup: `.bak_F235D_SOURCE_FAMILY_CANONICALIZATION`)
- `tools/research_quality_score.py` (backup: `.bak_F235D_SOURCE_FAMILY_CANONICALIZATION`)

## Test
- 16 assertions in `tests/test_sprint66/test_source_family_canonicalization.py`
- Regression: `tests/probe_f234b_source_family_contract`, `tests/probe_f235_ct_loss_stage_end_to_end`, `tests/probe_f234c_parallel_public_provider`, `tests/probe_f234d_parallel_ct_provider_resilience`