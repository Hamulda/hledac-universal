# Live KPI Derivation — Responsibility Index

**Sprint**: F228A
**Source**: `benchmarks/live_sprint_measurement.py`
**Purpose**: Read-only extraction map before any high-risk KPI derivation refactor

---

## Extraction Order

| Phase | Module | Rationale |
|-------|--------|-----------|
| 1 | `benchmarks/live_measurement_quality.py` | Quality verdict helpers — isolated, pure transforms, no KPI computation |
| 2 | `benchmarks/live_measurement_terminality.py` | Terminality predicates — pure boolean tests, no side-effects |
| 3 | `benchmarks/live_measurement_next_action.py` | Rule dispatcher, NextActionInput, 8 rule helpers |
| 4 | `benchmarks/live_measurement_kpi.py` | `_derive_live_kpi`, `_stamp_live_kpi`, discovery helpers — **LAST** |

---

## Phase 1 — Quality Verdict (`live_measurement_quality.py`)

| Function | Lines | Args | Called Helpers | Risk | Key Fields |
|----------|-------|------|---------------|------|------------|
| `_derive_run_quality_verdict` | 418–434 | 15 | `_uma_state_is_critical_or_emergency`, `_is_active_domain_query`, `_has_terminal_source_outcomes`, `_has_scheduler_exit_path` | LOW | verdict, hardware_constrained |
| `_stamp_run_quality_verdict` | 587–596 | 2 | `_derive_run_quality_verdict` | LOW | verdict, hardware_constrained |

**Dependency graph**:
```
_derive_run_quality_verdict
└── terminality predicates (Phase 2)
    _uma_state_is_critical_or_emergency
    _is_active_domain_query
    _has_terminal_source_outcomes
    _has_scheduler_exit_path
```

**Note**: `_derive_run_quality_verdict` has 15 explicit args and ~128 lines of dense conditional logic. Extract Phase 2 helpers first, then this function.

---

## Phase 2 — Terminality Predicates (`live_measurement_terminality.py`)

| Function | Lines | Args | Called Helpers | Risk |
|----------|-------|------|---------------|------|
| `_uma_state_is_critical_or_emergency` | 356–359 | 1 | — | LOW |
| `_is_active_domain_query` | 363–371 | 2 | — | LOW |
| `_has_terminal_source_outcomes` | 381–387 | 1 | — | LOW |
| `_has_scheduler_exit_path` | 399–403 | 1 | — | LOW |
| `_was_family_attempted` | 1350–1356 | 2 | — | LOW |

All 5 functions are pure boolean tests with 0–2 args and are used by both `_derive_run_quality_verdict` (Phase 1) and `_derive_live_kpi` (Phase 4) — extract these first.

**Duplicate detected**: `_has_terminal_source_outcomes` and `_has_scheduler_exit_path` also exist in `benchmarks/live_measurement_parser.py` (lines 20–35 and 38–54). The parser copies are used by the parser; these are the measurement versions.

---

## Phase 3 — Next Action (`live_measurement_next_action.py`)

### Dataclass

| Name | Lines | Risk | Notes |
|------|-------|------|-------|
| `NextActionInput` | 1369–1404 | LOW | Frozen dataclass with 35 fields |

### Rule Helpers (dispatch order)

| Rule | Lines | Called Helpers | Risk | Notes |
|------|-------|---------------|------|-------|
| `_rule_wallclock_enforcement` | 1413–1416 | — | MEDIUM | Rule 0 |
| `_rule0g_prewindup_barrier` | 1429–1437 | — | MEDIUM | F207Q |
| `_rule_profile_propagation` | 1460–1478 | — | MEDIUM | F207T/F207V-B |
| `_rule_terminality` | 1515–1519 | — | MEDIUM | F208F/F208M |
| `_rule_provider_surface` | 1534–1542 | — | MEDIUM | F209B |
| `_rule_quality_gate` | 1563–1567 | — | MEDIUM | F207M |
| `_rule_default` | 1575–1583 | `_was_family_attempted` | MEDIUM | Rules 2–7 |
| `_derive_next_action` | 1614–1618 | `NextActionInput` | MEDIUM | Thin priority dispatcher |

**Rule priority order** (as implemented):
1. `_rule_wallclock_enforcement` — wallclock deadline exceeded
2. `_rule0g_prewindup_barrier` — prewindup barrier telemetry (F207Q)
3. `_rule_profile_propagation` — return guard, windup callback, scheduler exit (F207T/F207V-B)
4. `_rule_provider_surface` — acquisition prelude (F209B)
5. `_rule_terminality` — terminality wiring (F208F/F208M)
6. `_rule_quality_gate` — starvation, memory gate
7. `_rule_default` — feed-dominance, public rejection, ct, quality rules

**Call chain**: `_derive_next_action` → builds `NextActionInput` → iterates through rule helpers in priority order → first non-None result wins

**Note**: `_rule0b_memory_or_swap_gate` (lines 1422–1425) is defined but NOT in the dispatch list — dead code path. Should be removed or reconnected.

---

## Phase 4 — KPI Derivation (`live_measurement_kpi.py`) — LAST

| Function | Lines | Explicit Args | Called Helpers | Risk | Key Fields |
|----------|-------|--------------|---------------|------|------------|
| `_derive_discovery_provider_status_debug` | 1297–1304 | 1 | — | LOW | discovery_* |
| `_derive_discovery_selected_providers` | 1326–1329 | 1 | `_derive_discovery_provider_status_debug` | LOW | discovery_* |
| `_derive_discovery_skipped_providers` | 1332–1335 | 1 | `_derive_discovery_provider_status_debug` | LOW | discovery_* |
| `_derive_discovery_stub_providers` | 1338–1341 | 1 | `_derive_discovery_provider_status_debug` | LOW | discovery_* |
| `_derive_discovery_not_wired_providers` | 1344–1347 | 1 | `_derive_discovery_provider_status_debug` | LOW | discovery_* |
| `_derive_live_kpi` | 618–704 | 32 | `_derive_next_action`, `_is_active_domain_query`, `_has_terminal_source_outcomes`, `_has_scheduler_exit_path`, all discovery helpers | HIGH | total_findings, accepted_findings, wallclock_budget_exceeded, feed_dominance_score, nonfeed_starvation_suspected, next_action, terminality_quality_verdict, ct_loss_stage, claims_extracted_count, discovery_* (60+ fields total) |
| `_stamp_live_kpi` | 1720–1729 | 1 | `_derive_live_kpi`, `score_research_quality` | HIGH | live_kpi, research_quality |

**Critical observations**:

1. **`_derive_live_kpi` is the dominant hotspot** (F226D finding confirmed):
   - 32 explicit args
   - ~700 lines of dense conditional logic
   - Depends on: terminality predicates (Phase 2), next_action (Phase 3), 5 discovery helpers
   - Returns 60+ KPI fields

2. **`_stamp_live_kpi` calls `score_research_quality`** from `tools/research_quality_score.py` — this is the only cross-module import dependency. When extracting Phase 4, this import must be replicated or forwarded.

3. **PUBLIC synthesis** (lines ~905): `_has_public_signal` synthesis for lane_execution_counts["PUBLIC"] — this logic should probably live in a terminality helper.

4. **`_derive_next_action` is called as a helper inside `_derive_live_kpi`** (not the other way around) — confirmed by call chain analysis.

---

## Known Issues Found During Scan

| Issue | Location | Severity | Notes |
|-------|----------|----------|-------|
| `_rule0b_memory_or_swap_gate` dead code | line 1422, not in dispatch | MEDIUM | Defined but never called by `_derive_next_action` |
| Duplicate terminality helpers | parser.py has copies | LOW | Parser versions are separate; measurement versions are canonical |
| `_derive_next_action` end-line misreported | line 1618 vs actual ~1717 | LOW | AST `endlineno` issue — body spans ~100 more lines |

---

## Risk Summary

| Risk | Count | Functions |
|------|-------|-----------|
| HIGH | 2 | `_derive_live_kpi`, `_stamp_live_kpi` |
| MEDIUM | 9 | 8 rule helpers + `_derive_next_action` |
| LOW | 13 | terminality predicates, quality verdict helpers, discovery helpers, NextActionInput |

---

## Recommended First Extraction (Phase 1)

**Do this first** to validate the extraction pattern without touching high-risk code:

1. Extract `_uma_state_is_critical_or_emergency` (5 lines) → `benchmarks/live_measurement_terminality.py`
2. Extract `_is_active_domain_query` (9 lines) → same file
3. Extract `_has_terminal_source_outcomes` (7 lines) → same file
4. Extract `_has_scheduler_exit_path` (8 lines) → same file
5. Extract `_was_family_attempted` (7 lines) → same file
6. Extract `_derive_run_quality_verdict` (Phase 1) with terminality imports updated

This validates: import wiring, no regression in `_stamp_run_quality_verdict`, and confirms AST line ranges are accurate before tackling Phase 4.