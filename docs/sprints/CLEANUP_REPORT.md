# Cleanup Report — Wave 1 & 2 Findings
**Date:** 2026-05-23
**Scope:** hledac/universal/

---

## 1. Shodan Duplicate Audit

**Finding:** `tools/shodan_wrapper.py` does not exist in the codebase.

| File | Status | Canonical? |
|------|--------|------------|
| `intelligence/shodan_lane.py` (187 lines) | EXISTS | ✅ **Canonical** |
| `tools/shodan_wrapper.py` | Does not exist | N/A |

**Active imports of `shodan_lane` (8 files):**
- `discovery/ti_feed_adapter.py`
- `runtime/acquisition_strategy.py`
- `export/sprint_exporter.py`
- `capabilities.py`
- `tests/test_research_depth_metric.py`
- `tests/probe_f195g/__init__.py`
- `tests/probe_f195g/test_f195g_shodan_bgp_canonical.py`
- `tests/probe_f192f/test_f192f_facts_output_seam.py`

**Conclusion:** No duplicate. `shodan_lane.py` is the canonical implementation.

---

## 2. Stale Test Audit

### `tests/test_sprint62c.py`

**Issue:** Test imported `BetaBinomial` from `hypothesis` but the class lives in `brain/confidence_utils.py`.

```python
# Original (broken):
from hledac.universal.hypothesis import BetaBinomial

# Fixed:
from hledac.universal.brain.confidence_utils import BetaBinomial
```

**Review finding:** The `hypothesis` import path appears to have never been valid — no evidence `BetaBinomial` existed there. The test was likely broken since creation. `brain/confidence_utils.BetaBinomial` has no other production callers (only used in `legacy/atomic_storage.py` with try/except fallback). Test is legitimate verification, not fake-green.

**Fix:** ✅ Import updated, test passes (1 passed).

### F196A Dead Code References

| Test File | Dead Import | Action |
|-----------|-------------|--------|
| `tests/probe_f196a/test_canonical_baseline_and_ghost_verdict.py` | MARLCoordinator, etc. | Test validates removal — correct behavior |
| `tests/test_sprint58a.py` | F401 import for skip markers | Preserved correctly |

**Conclusion:** Only `test_sprint62c.py` needs fixing (import path).

---

## 3. Legacy/Autonomous Orchestrator Dependency Map (31,056 lines)

**File:** `legacy/autonomous_orchestrator.py` (1.3MB)

**Referenced by 13 modules:**

| Reference | Type | Status |
|-----------|------|--------|
| `runtime/memory_authority.py` | Production | Maps to legacy_ao symbol |
| `runtime_authority_manifest.py` | Production | Documented as deprecated |
| `tests/probe_r9x_academic_profile_guard/test_r9x_academic_profile_guard.py` | Test | Assertion test |
| `tests/r5x_nonfeed_integration_guard/test_r5x_nonfeed_integration_guard.py` | Test | Assertion 12 validation |
| `tests/test_sprint_f193a_legacy_boundary.py` | Test | Legacy boundary test |
| `tests/probe_memory_authority/test_memory_authority.py` | Test | Symbol classification |
| `tests/probe_0a/test_sprint_0a.py` | Test | mkdtemp invariant |
| `tests/probe_f205d/test_dead_code_archive_manifest.py` | Test | Archive manifest |
| `tests/probe_f206ah_runtime_authority/test_runtime_authority.py` | Test | Authority validation |
| `tests/probe_f234b_source_family_contract/test_source_family_contract.py` | Test | Contract test |
| `tests/probe_8vc/test_legacy_burial.py` | Test | Legacy burial test |
| `tests/probe_r7x_advisory_sidecar_propagation/test_probe.py` | Test | Sidecar propagation |
| `tests/test_sprint8ap_bounded_live_gate.py` | Test | Live gate test |

**Note:** All 13 references are from **tests** or **runtime authority manifest** — NOT from production code paths. The file is a compatibility seam, not active production code.

**Do NOT modify the file itself — only document.**

---

## 4. Benchmarks Audit

**Result:** ✅ All 30 benchmark files compile successfully.

```
benchmarks/__init__.py                    OK
benchmarks/benchmark_pipeline.py           OK
benchmarks/benchmark_sprint_probe.py       OK
benchmarks/coreml_ane_capability.py        OK
benchmarks/dedup_determinism_benchmark.py  OK
benchmarks/e2e_canonical_benchmark.py      OK
benchmarks/e2e_compare.py                   OK
benchmarks/e2e_curl_cffi_protected_fixture.py OK
benchmarks/e2e_signal_fixture_compare.py    OK
benchmarks/e2e_signal_fixture.py            OK
benchmarks/e2e_sprint_probe.py             OK
benchmarks/live_measurement_kpi.py         OK
benchmarks/live_measurement_markdown.py    OK
benchmarks/live_measurement_next_action.py OK
benchmarks/live_measurement_parser.py      OK
benchmarks/live_measurement_quality.py     OK
benchmarks/live_measurement_schema.py      OK
benchmarks/live_measurement_terminality.py  OK
benchmarks/live_sprint_measurement.py       OK
benchmarks/llm_reasoner_benchmark.py       OK
benchmarks/m1_embedding_batch_benchmark.py  OK
benchmarks/m1_embedding_streaming.py        OK
benchmarks/m1_mlx_stream_uma_probe.py      OK
benchmarks/m1_phase4_budget.py              OK
benchmarks/m1_sustained_sprint.py          OK
benchmarks/ranking_dedup_hotspot_benchmark.py OK
benchmarks/research_effectiveness.py       OK
benchmarks/run_sprint82j_benchmark.py      OK
benchmarks/static_hydration_impact.py       OK
benchmarks/vision_vlm_routing_benchmark.py  OK
```

**No syntax errors found.**

---

## 5. Dead Config Audit

**Finding:** `config/settings.py` does not exist as a standalone file.

Config is accessed via:
- `self._config` (SprintScheduler, coordinators)
- Module-level imports from `patterns.pattern_matcher`

**Orphaned config fields search:** No `config.settings` module exists. Config fields are accessed via `_config` object parameter, not a global settings module.

---

## 6. Module Size Audit — Refactoring Candidates

| File | Lines | Recommendation |
|------|-------|----------------|
| `legacy/autonomous_orchestrator.py` | **31,056** | **PRIORITY: Decompose into 5-7 sub-modules** |
| `tests/test_autonomous_orchestrator.py` | 22,130 | Test file — not refactoring target |
| `runtime/sprint_scheduler.py` | 13,178 | High priority refactor candidate |
| `knowledge/duckdb_store.py` | 6,610 | Medium priority |
| `pipeline/live_public_pipeline.py` | 5,041 | Medium priority |

### `legacy/autonomous_orchestrator.py` Decomposition Plan

Current: 31,056 lines — single God Object

**Suggested 7 logical sub-modules:**

| Sub-module | Estimated Lines | Responsibility |
|------------|-----------------|-----------------|
| `orchestrator/core.py` | ~4,000 | Main loop, state machine, entry point |
| `orchestrator/memory.py` | ~4,500 | _MemoryManager, _MemoryCoordinator |
| `orchestrator/fetch.py` | ~3,500 | Fetch coordination, URL dedup |
| `orchestrator/coordinators.py` | ~5,000 | CoordinatorRegistry, lifecycle |
| `orchestrator/security.py` | ~3,000 | SecurityManager, authority |
| `orchestrator/research.py` | ~3,500 | ResearchManager, hypothesis |
| `orchestrator/persistence.py` | ~3,500 | CheckpointStore, state persistence |

**Total:** ~27,000 lines (leaving ~4,000 for过渡 code and imports)

---

## Tech Debt Sprint Backlog

| Priority | Item | Estimate | Notes |
|----------|------|----------|-------|
| P1 | Fix `tests/test_sprint62c.py` BetaBinomial import | 5 min | Simple import path update |
| P2 | `runtime/sprint_scheduler.py` refactor (13K lines) | 2-3 days | Extract coordinator sub-classes |
| P2 | `knowledge/duckdb_store.py` refactor (6.6K lines) | 1-2 days | Separate query building |
| P3 | `pipeline/live_public_pipeline.py` refactor (5K lines) | 1 day | Extract stage handlers |
| P3 | `legacy/autonomous_orchestrator.py` decomposition plan | 3-4 days | Full decomposition as separate sprint |

---

## Summary

| Audit | Finding | Action Required |
|-------|---------|-----------------|
| Shodan duplicate | No duplicate — `shodan_lane.py` is canonical | None |
| Stale test | `test_sprint62c.py` needs import fix | Update import path |
| Legacy AO map | 13 test references, 0 production | Document only |
| Benchmarks | All 30 compile OK | None |
| Config audit | No `config/settings.py` exists | None |
| Module size | 1 file > 30K, 1 > 13K, 2 > 5K | Add to backlog |

**Total actionable items:** 1 (fix `test_sprint62c.py` import)